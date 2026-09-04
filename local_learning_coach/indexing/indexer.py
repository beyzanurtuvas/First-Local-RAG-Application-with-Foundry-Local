from __future__ import annotations

import json
import hashlib
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from local_learning_coach.config import Settings
from local_learning_coach.database import Database, DatabaseError
from local_learning_coach.documents import DocumentParser, TesseractOCRAdapter
from local_learning_coach.indexing.embeddings import EmbeddingProvider


class IndexError(RuntimeError):
    """Kullanıcıya gösterilebilir indeks hatası."""


INDEX_VERSION = 4


class IndexManager:
    def __init__(self, settings: Settings, database: Database | None = None):
        self.settings = settings
        self.database = database

    @property
    def manifest_path(self) -> Path:
        return self.settings.index_dir / "manifest.json"

    @property
    def chunks_path(self) -> Path:
        return self.settings.index_dir / "chunks.jsonl"

    @property
    def vectors_path(self) -> Path:
        return self.settings.index_dir / "vectors.npz"

    def current_signature(self) -> dict[str, Any]:
        import hashlib

        self.settings.validate_sources()
        uploaded = self._uploaded_documents()
        return {
            "index_version": INDEX_VERSION,
            "documents": {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in self.settings.source_paths
            },
            "uploaded_documents": {
                item["id"]: (
                    hashlib.sha256(Path(item["path"]).read_bytes()).hexdigest()
                    if Path(item["path"]).is_file()
                    else "missing"
                )
                for item in uploaded
            },
            "embedding_backend": self.settings.embedding_backend,
            "embedding_model": self.settings.embedding_model,
            "chunk_max_chars": self.settings.chunk_max_chars,
            "chunk_overlap_paragraphs": self.settings.chunk_overlap_paragraphs,
        }

    def _uploaded_documents(self) -> list[dict[str, Any]]:
        database = self.database or Database(self.settings.db_path)
        try:
            return database.list_source_documents(origin="uploaded", include_archived=False)
        except DatabaseError:
            return []

    def status(self) -> dict[str, Any]:
        if not self.manifest_path.exists() or not self.chunks_path.exists() or not self.vectors_path.exists():
            return {"exists": False, "stale": True, "reason": "İndeks dosyaları eksik."}
        try:
            manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            current = self.current_signature()
            stale_fields = [key for key, value in current.items() if manifest.get(key) != value]
            return {
                "exists": True,
                "stale": bool(stale_fields),
                "reason": ", ".join(stale_fields) if stale_fields else "Güncel",
                "manifest": manifest,
            }
        except Exception as exc:
            return {"exists": True, "stale": True, "reason": f"Manifest okunamadı: {exc}"}

    def _embed_passages(self, provider: EmbeddingProvider, texts: list[str]) -> np.ndarray:
        """İçerik ve model sürümüne bağlı, yerel ve güvenli embedding önbelleği."""
        if not self.database or "embedding_cache" not in self.database.table_names():
            return provider.embed_passages(texts)
        backend = self.settings.embedding_backend
        model = self.settings.embedding_model
        parser_version = f"index-v{INDEX_VERSION}:chunk-{self.settings.chunk_max_chars}:{self.settings.chunk_overlap_paragraphs}"
        normalization_version = "l2-v1"
        keys = [hashlib.sha256(
            f"{backend}|{model}|{parser_version}|{normalization_version}|{hashlib.sha256(text.encode('utf-8')).hexdigest()}".encode("utf-8")
        ).hexdigest() for text in texts]
        cached: dict[str, np.ndarray] = {}
        now = datetime.now(timezone.utc).isoformat()
        with self.database.connection() as conn:
            for key in keys:
                row = conn.execute(
                    "SELECT vector_blob,dimension FROM embedding_cache WHERE cache_key=?", (key,),
                ).fetchone()
                if row:
                    vector = np.frombuffer(row["vector_blob"], dtype=np.float32).copy()
                    if vector.size == int(row["dimension"]):
                        cached[key] = vector
                        conn.execute(
                            "UPDATE embedding_cache SET hit_count=hit_count+1,last_accessed_at=? WHERE cache_key=?",
                            (now, key),
                        )
        missing_by_key: dict[str, int] = {}
        for index, key in enumerate(keys):
            if key not in cached:
                missing_by_key.setdefault(key, index)
        missing_indices = list(missing_by_key.values())
        if missing_indices:
            generated = provider.embed_passages([texts[index] for index in missing_indices]).astype(np.float32)
            if generated.shape[0] != len(missing_indices):
                raise IndexError("Embedding sağlayıcısı beklenen vektör sayısını döndürmedi.")
            with self.database.connection() as conn:
                for row_index, text_index in enumerate(missing_indices):
                    vector = generated[row_index]
                    key = keys[text_index]
                    cached[key] = vector
                    conn.execute(
                        """INSERT INTO embedding_cache(cache_key,content_sha256,backend,model,parser_version,
                               normalization_version,vector_blob,dimension,created_at,last_accessed_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(cache_key) DO UPDATE SET
                               vector_blob=excluded.vector_blob,dimension=excluded.dimension,last_accessed_at=excluded.last_accessed_at""",
                        (key, hashlib.sha256(texts[text_index].encode("utf-8")).hexdigest(), backend, model,
                         parser_version, normalization_version, vector.astype(np.float32).tobytes(),
                         int(vector.size), now, now),
                    )
                excess = conn.execute("SELECT MAX(0,COUNT(*)-?) FROM embedding_cache", (self.settings.cache_max_entries,)).fetchone()[0]
                if excess:
                    conn.execute(
                        "DELETE FROM embedding_cache WHERE cache_key IN (SELECT cache_key FROM embedding_cache ORDER BY last_accessed_at LIMIT ?)",
                        (int(excess),),
                    )
        vectors = [cached[key] for key in keys]
        dimensions = {vector.size for vector in vectors}
        if len(dimensions) != 1:
            raise IndexError("Embedding önbelleğinde vektör boyutu uyuşmazlığı bulundu.")
        return np.vstack(vectors).astype(np.float32)

    def build(self, *, force: bool = False) -> dict[str, Any]:
        self.settings.ensure_runtime_dirs()
        signature = self.current_signature()
        status = self.status()
        if status.get("exists") and not status.get("stale") and not force:
            return status["manifest"]

        parser = DocumentParser(
            max_chars=self.settings.chunk_max_chars,
            overlap_paragraphs=self.settings.chunk_overlap_paragraphs,
            ocr_adapter=TesseractOCRAdapter(self.settings.ocr_command) if self.settings.ocr_enabled else None,
        )
        documents = parser.parse_all(self.settings.source_paths)
        for item in self._uploaded_documents():
            path = Path(item["path"])
            if not path.is_file():
                raise IndexError(f"Yüklenen belge dosyası bulunamadı: {item.get('original_filename') or path.name}")
            documents.append(
                parser.parse(
                    path,
                    route=item["route"],
                    route_title=item["title"],
                    document_id=item["id"],
                    source_name=item.get("original_filename") or item["filename"],
                )
            )
        chunks = [chunk.to_dict() for document in documents for chunk in document.chunks]
        if not chunks:
            raise IndexError("Belge ayrıştırma sonucunda hiç chunk oluşmadı; indeks yazılmadı.")
        provider = EmbeddingProvider(self.settings.embedding_backend, self.settings.embedding_model)

        temp_dir = Path(tempfile.mkdtemp(prefix="llc-index-", dir=self.settings.index_dir.parent))
        backup_dir = temp_dir / "backup"
        try:
            vectors = self._embed_passages(provider, [chunk["text"] for chunk in chunks])
            if vectors.shape[0] != len(chunks):
                raise IndexError("Embedding vektör sayısı ile chunk sayısı uyuşmuyor.")
            norms = np.linalg.norm(vectors, axis=1)
            if not np.allclose(norms, 1.0, atol=1e-4):
                raise IndexError("Embedding vektörleri L2-normalize değil.")

            temp_chunks = temp_dir / "chunks.jsonl"
            with temp_chunks.open("w", encoding="utf-8", newline="\n") as handle:
                for chunk in chunks:
                    handle.write(json.dumps(chunk, ensure_ascii=False) + "\n")
            np.savez_compressed(temp_dir / "vectors.npz", vectors=vectors)
            manifest = {
                **signature,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "document_count": len(documents),
                "chunk_count": len(chunks),
                "vector_dimension": int(vectors.shape[1]),
                "normalized": True,
            }
            (temp_dir / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            self._validate_files(temp_dir)

            backup_dir.mkdir()
            targets = ("chunks.jsonl", "vectors.npz", "manifest.json")
            for name in targets:
                existing = self.settings.index_dir / name
                if existing.exists():
                    try:
                        shutil.copy2(existing, backup_dir / name)
                    except PermissionError:
                        # Okunamayan eski Windows ACL'li dosya zaten çalışan yedek olarak kullanılamaz.
                        pass
            try:
                # Manifest en son değiştirilir; okuyucular yalnızca tamamlanmış üretimi görür.
                for name in ("chunks.jsonl", "vectors.npz", "manifest.json"):
                    self._replace_with_inherited_permissions(temp_dir / name, self.settings.index_dir / name)
            except Exception:
                for name in targets:
                    backup = backup_dir / name
                    if backup.exists():
                        self._replace_with_inherited_permissions(backup, self.settings.index_dir / name)
                raise

            if self.database:
                self.database.upsert_source_data(
                    [document.metadata() for document in documents],
                    chunks,
                )
                self._record_document_ranges(chunks)
            return manifest
        except Exception as exc:
            if isinstance(exc, IndexError):
                raise
            raise IndexError(f"İndeks oluşturulamadı; önceki çalışan indeks korundu: {exc}") from exc
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def update_document(self, document_id: str) -> dict[str, Any]:
        """Yalnızca tek belgenin chunk ve vektörlerini atomik olarak ekler/değiştirir."""
        self.settings.ensure_runtime_dirs()
        document_row = None
        if self.database:
            document_row = self.database.source_document(document_id)
        if not document_row or document_row.get("status") != "active":
            raise IndexError("Artımlı indekslenecek etkin belge bulunamadı.")
        if not (self.manifest_path.exists() and self.chunks_path.exists() and self.vectors_path.exists()):
            return self.build(force=True)
        try:
            old_manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            configuration_keys = (
                "index_version", "embedding_backend", "embedding_model", "chunk_max_chars",
                "chunk_overlap_paragraphs",
            )
            signature = self.current_signature()
            if any(old_manifest.get(key) != signature.get(key) for key in configuration_keys):
                return self.build(force=True)
            old_chunks = [
                json.loads(line) for line in self.chunks_path.read_text(encoding="utf-8").splitlines() if line.strip()
            ]
            with np.load(self.vectors_path) as data:
                old_vectors = np.asarray(data["vectors"], dtype=np.float32)
            if old_vectors.shape[0] != len(old_chunks):
                raise IndexError("Mevcut indeks bozuk: chunk/vektör hizalaması geçersiz.")
            parser = DocumentParser(
                max_chars=self.settings.chunk_max_chars,
                overlap_paragraphs=self.settings.chunk_overlap_paragraphs,
                ocr_adapter=TesseractOCRAdapter(self.settings.ocr_command) if self.settings.ocr_enabled else None,
            )
            parsed = parser.parse(
                Path(document_row["path"]),
                route=document_row["route"],
                route_title=document_row["title"],
                document_id=document_id,
                source_name=document_row.get("original_filename") or document_row["filename"],
            )
            new_chunks = [chunk.to_dict() for chunk in parsed.chunks]
            if not new_chunks:
                raise IndexError("Belgeden artımlı indeks için chunk üretilemedi.")
            keep_indices = [index for index, item in enumerate(old_chunks) if item["document_id"] != document_id]
            kept_chunks = [old_chunks[index] for index in keep_indices]
            kept_vectors = old_vectors[keep_indices] if keep_indices else np.empty((0, old_vectors.shape[1]), dtype=np.float32)
            provider = EmbeddingProvider(self.settings.embedding_backend, self.settings.embedding_model)
            new_vectors = self._embed_passages(provider, [item["text"] for item in new_chunks])
            if new_vectors.shape[0] != len(new_chunks):
                raise IndexError("Yeni chunk/vektör sayıları uyuşmuyor.")
            chunks = kept_chunks + new_chunks
            vectors = np.vstack([kept_vectors, new_vectors]).astype(np.float32)
            manifest = self._commit_index(chunks, vectors, signature, operation="incremental-update")
            self.database.upsert_source_data([parsed.metadata()], new_chunks)
            self._record_document_ranges(chunks)
            return manifest
        except IndexError:
            raise
        except Exception as exc:
            raise IndexError(f"Artımlı indeks güncellenemedi; önceki çalışan indeks korundu: {exc}") from exc

    def archive_document(self, document_id: str) -> dict[str, Any]:
        """Arşivlenen belgeyi etkin indeksten embedding üretmeden çıkarır."""
        if not (self.manifest_path.exists() and self.chunks_path.exists() and self.vectors_path.exists()):
            return self.build(force=True)
        try:
            chunks = [
                json.loads(line) for line in self.chunks_path.read_text(encoding="utf-8").splitlines() if line.strip()
            ]
            with np.load(self.vectors_path) as data:
                vectors = np.asarray(data["vectors"], dtype=np.float32)
            keep = [index for index, item in enumerate(chunks) if item["document_id"] != document_id]
            if not keep:
                raise IndexError("Arşivleme etkin indeksi boş bırakacağı için işlem uygulanmadı.")
            remaining_chunks = [chunks[index] for index in keep]
            remaining_vectors = vectors[keep]
            manifest = self._commit_index(
                remaining_chunks, remaining_vectors, self.current_signature(), operation="incremental-archive"
            )
            self._record_document_ranges(remaining_chunks)
            return manifest
        except IndexError:
            raise
        except Exception as exc:
            raise IndexError(f"Belge indeksten çıkarılamadı; önceki çalışan indeks korundu: {exc}") from exc

    def _commit_index(
        self,
        chunks: list[dict[str, Any]],
        vectors: np.ndarray,
        signature: dict[str, Any],
        *,
        operation: str,
    ) -> dict[str, Any]:
        if not chunks or vectors.shape[0] != len(chunks):
            raise IndexError("İndeks commit öncesi chunk/vektör hizalaması başarısız.")
        if not np.allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=1e-4):
            raise IndexError("İndeks commit öncesi vektör normalizasyonu başarısız.")
        temp_dir = Path(tempfile.mkdtemp(prefix="llc-index-update-", dir=self.settings.index_dir.parent))
        backup_dir = temp_dir / "backup"
        try:
            with (temp_dir / "chunks.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
                for chunk in chunks:
                    handle.write(json.dumps(chunk, ensure_ascii=False) + "\n")
            np.savez_compressed(temp_dir / "vectors.npz", vectors=vectors)
            manifest = {
                **signature,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "document_count": len({item["document_id"] for item in chunks}),
                "chunk_count": len(chunks),
                "vector_dimension": int(vectors.shape[1]),
                "normalized": True,
                "last_operation": operation,
            }
            (temp_dir / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            self._validate_files(temp_dir)
            backup_dir.mkdir()
            targets = ("chunks.jsonl", "vectors.npz", "manifest.json")
            for name in targets:
                existing = self.settings.index_dir / name
                if existing.exists():
                    try:
                        shutil.copy2(existing, backup_dir / name)
                    except PermissionError:
                        pass
            try:
                for name in targets:
                    self._replace_with_inherited_permissions(temp_dir / name, self.settings.index_dir / name)
            except Exception:
                for name in targets:
                    backup = backup_dir / name
                    if backup.exists():
                        self._replace_with_inherited_permissions(backup, self.settings.index_dir / name)
                raise
            return manifest
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    @staticmethod
    def _replace_with_inherited_permissions(source: Path, target: Path) -> None:
        """Windows'ta geçici klasör ACL'sini hedefe taşımadan atomik dosya değişimi yapar."""
        stage = target.parent / f".{target.name}.next"
        try:
            with source.open("rb") as reader, stage.open("wb") as writer:
                shutil.copyfileobj(reader, writer)
                writer.flush()
                os.fsync(writer.fileno())
            os.replace(stage, target)
        finally:
            if stage.exists():
                stage.unlink()

    def _record_document_ranges(self, chunks: list[dict[str, Any]]) -> None:
        if not self.database:
            return
        ranges: dict[str, list[int]] = {}
        for index, chunk in enumerate(chunks):
            ranges.setdefault(chunk["document_id"], []).append(index)
        with self.database.connection() as conn:
            conn.execute("DELETE FROM document_index_status")
            for document_id, positions in ranges.items():
                document = self.database.source_document(document_id)
                if not document:
                    continue
                conn.execute(
                    """INSERT INTO document_index_status(document_id, content_sha256, chunk_count,
                           vector_start, vector_end, backend, status, indexed_at)
                       VALUES (?, ?, ?, ?, ?, 'local', 'active', ?)""",
                    (
                        document_id, document["sha256"], len(positions), min(positions), max(positions) + 1,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )

    @staticmethod
    def _validate_files(directory: Path) -> None:
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        chunks = [line for line in (directory / "chunks.jsonl").read_text(encoding="utf-8").splitlines() if line]
        with np.load(directory / "vectors.npz") as data:
            vectors = data["vectors"]
        if len(chunks) != manifest["chunk_count"] or vectors.shape[0] != len(chunks):
            raise IndexError("Geçici indeks doğrulaması başarısız: chunk/vektör boyutları uyuşmuyor.")

    def load(self) -> tuple[list[dict[str, Any]], np.ndarray, dict[str, Any]]:
        status = self.status()
        if not status.get("exists"):
            raise IndexError("İndeks bulunamadı. 'python cli.py index build' komutunu çalıştırın.")
        if status.get("stale"):
            raise IndexError(f"İndeks güncel değil ({status.get('reason')}). Yeniden oluşturun.")
        try:
            chunks = [
                json.loads(line)
                for line in self.chunks_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            with np.load(self.vectors_path) as data:
                vectors = np.asarray(data["vectors"], dtype=np.float32)
            manifest = status["manifest"]
            if not chunks:
                raise IndexError("İndeks boş.")
            if vectors.shape[0] != len(chunks):
                raise IndexError("İndeks bozuk: chunk/vektör sayıları uyuşmuyor.")
            return chunks, vectors, manifest
        except IndexError:
            raise
        except Exception as exc:
            raise IndexError(f"İndeks dosyaları okunamadı: {exc}") from exc
