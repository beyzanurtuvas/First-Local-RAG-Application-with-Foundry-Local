from __future__ import annotations

import hashlib
import io
import os
import re
import tempfile
import unicodedata
import zipfile
from datetime import date
from pathlib import Path
from typing import Any

from local_learning_coach.config import Settings
from local_learning_coach.database import Database
from local_learning_coach.database.db import utc_now
from local_learning_coach.documents import DocumentParser, SourceTask, TesseractOCRAdapter
from local_learning_coach.documents.parser import ParsedDocument, clean_text, extract_source_tasks, stable_hash
from local_learning_coach.indexing import IndexManager
from local_learning_coach.learning.planner import PersonalPlanner
from local_learning_coach.learning.advanced import validate_prerequisite_graph


ALLOWED_PROVENANCE = {
    "belgeden çıkarıldı",
    "sistem tarafından oluşturulan alıştırma",
    "belge dışı ön koşul önerisi",
}
MIME_TYPES = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pdf": "application/pdf",
}
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_DOCX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_PDF_PAGES = 500


class UploadError(ValueError):
    """Kullanıcıya gösterilebilir güvenli belge yükleme hatası."""


class RouteDraftError(ValueError):
    """Kullanıcıya gösterilebilir özel rota hatası."""


def _safe_original_filename(filename: str) -> str:
    normalized = unicodedata.normalize("NFKC", filename).strip()
    if not normalized or normalized in {".", ".."}:
        raise UploadError("Dosya adı boş veya geçersiz.")
    if any(character in normalized for character in ("/", "\\", ":", "\x00")):
        raise UploadError("Dosya adı klasör yolu veya geçersiz karakter içeremez.")
    if len(normalized) > 180:
        raise UploadError("Dosya adı en fazla 180 karakter olabilir.")
    if any(ord(character) < 32 for character in normalized):
        raise UploadError("Dosya adı denetim karakteri içeremez.")
    suffix = Path(normalized).suffix.casefold()
    if suffix not in MIME_TYPES:
        raise UploadError("Yalnızca DOCX ve PDF belgeleri yüklenebilir.")
    return normalized


def _validate_docx(data: bytes) -> None:
    if not data.startswith(b"PK"):
        raise UploadError("Dosya uzantısı DOCX olsa da içerik geçerli bir DOCX paketi değil.")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            names = {item.filename.replace("\\", "/") for item in infos}
            if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                raise UploadError("DOCX paketinde zorunlu Word içeriği bulunamadı.")
            if any("vbaproject.bin" in name.casefold() for name in names):
                raise UploadError("Makro içeren Word belgeleri kabul edilmez.")
            if len(infos) > 10_000 or sum(item.file_size for item in infos) > MAX_DOCX_UNCOMPRESSED_BYTES:
                raise UploadError("DOCX paketi güvenli açma sınırlarını aşıyor.")
            for name in names:
                parts = Path(name).parts
                if name.startswith(("/", "\\")) or ".." in parts:
                    raise UploadError("DOCX paketi güvensiz bir iç yol içeriyor.")
    except zipfile.BadZipFile as exc:
        raise UploadError("DOCX paketi bozuk veya okunamıyor.") from exc


def _validate_signature(filename: str, data: bytes) -> str:
    if not data:
        raise UploadError("Boş belge yüklenemez.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise UploadError("Belge boyutu 25 MB sınırını aşıyor.")
    suffix = Path(filename).suffix.casefold()
    if suffix == ".docx":
        _validate_docx(data)
    elif suffix == ".pdf":
        if not data.startswith(b"%PDF-"):
            raise UploadError("Dosya uzantısı PDF olsa da içerik geçerli bir PDF değil.")
    else:
        raise UploadError("Yalnızca DOCX ve PDF belgeleri yüklenebilir.")
    return MIME_TYPES[suffix]


class UploadedDocumentManager:
    def __init__(self, settings: Settings, database: Database):
        self.settings = settings
        self.database = database
        ocr = TesseractOCRAdapter(settings.ocr_command) if settings.ocr_enabled else None
        self.parser = DocumentParser(settings.chunk_max_chars, settings.chunk_overlap_paragraphs, ocr_adapter=ocr)

    @property
    def upload_dir(self) -> Path:
        return self.settings.data_dir / "uploads"

    @staticmethod
    def _content_signature(chunks: list[Any]) -> str:
        canonical: list[str] = []
        for chunk in chunks:
            value = chunk.to_dict() if hasattr(chunk, "to_dict") else chunk
            canonical.append("\u241f".join((
                clean_text(str(value.get("section_path") or "")),
                clean_text(str(value.get("heading") or "")),
                clean_text(str(value.get("body") or "")),
                str(value.get("page") or ""),
            )))
        return hashlib.sha256("\u241e".join(canonical).encode("utf-8")).hexdigest()

    def upload(self, filename: str, data: bytes, *, rebuild_index: bool = True) -> dict[str, Any]:
        safe_name = _safe_original_filename(filename)
        mime_type = _validate_signature(safe_name, data)
        sha256 = hashlib.sha256(data).hexdigest()
        duplicate = self.database.source_document_by_sha256(sha256)
        if duplicate:
            return {**duplicate, "duplicate": True}

        document_id = "uploaded-" + sha256[:20]
        route = "custom-" + sha256[:16]
        suffix = Path(safe_name).suffix.casefold()
        stored_filename = document_id + suffix
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        temp_path: Path | None = None
        final_path = (self.upload_dir / stored_filename).resolve()
        if final_path.parent != self.upload_dir.resolve():
            raise UploadError("Belge için güvenli yerel hedef oluşturulamadı.")
        try:
            with tempfile.NamedTemporaryFile(
                prefix="upload-", suffix=suffix, dir=self.upload_dir, delete=False
            ) as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
                temp_path = Path(handle.name)
            parsed = self.parser.parse(
                temp_path,
                route=route,
                route_title=Path(safe_name).stem,
                document_id=document_id,
                source_name=safe_name,
            )
            if not parsed.chunks:
                raise UploadError("Belgeden öğrenme rotasında kullanılabilecek metin çıkarılamadı.")
            if suffix == ".pdf" and max((chunk.page or 0 for chunk in parsed.chunks), default=0) > MAX_PDF_PAGES:
                raise UploadError(f"PDF en fazla {MAX_PDF_PAGES} sayfa olabilir.")
            parsed_signature = self._content_signature(parsed.chunks)
            for existing in self.database.list_source_documents(origin="uploaded", include_archived=True):
                existing_chunks = self.database.source_chunks([existing["id"]])
                if existing_chunks and self._content_signature(existing_chunks) == parsed_signature:
                    return {**existing, "duplicate": True}
            os.replace(temp_path, final_path)
            temp_path = None
            registered = self.database.register_uploaded_document(
                {
                    "id": document_id,
                    "filename": stored_filename,
                    "original_filename": safe_name,
                    "path": str(final_path),
                    "title": parsed.title,
                    "route": route,
                    "sha256": sha256,
                    "mime_type": mime_type,
                }
            )
            if rebuild_index:
                IndexManager(self.settings, self.database).update_document(document_id)
            return {**registered, "duplicate": False, "chunk_count": len(parsed.chunks)}
        except Exception:
            if self.database.source_document(document_id):
                self.database.remove_uploaded_document_record(document_id)
            if final_path.exists():
                final_path.unlink()
            raise
        finally:
            if temp_path and temp_path.exists():
                temp_path.unlink()

    def archive(self, document_id: str, confirmation: str, *, rebuild_index: bool = True) -> dict[str, Any]:
        result = self.database.archive_uploaded_document(document_id, confirmation)
        if rebuild_index:
            IndexManager(self.settings, self.database).archive_document(document_id)
        return result


class CustomRouteBuilder:
    def __init__(self, settings: Settings, database: Database, planner: PersonalPlanner | None = None):
        self.settings = settings
        self.database = database
        self.planner = planner or PersonalPlanner()
        ocr = TesseractOCRAdapter(settings.ocr_command) if settings.ocr_enabled else None
        self.parser = DocumentParser(settings.chunk_max_chars, settings.chunk_overlap_paragraphs, ocr_adapter=ocr)

    def create_draft(
        self,
        document_id: str,
        profile_id: int,
        configuration: dict[str, Any],
    ) -> dict[str, Any]:
        document = self.database.source_document(document_id)
        if not document or document.get("origin") != "uploaded" or document.get("status") != "active":
            raise RouteDraftError("Etkin bir kullanıcı belgesi bulunamadı.")
        return self.create_draft_multi(
            [document_id], profile_id, configuration,
            route_id=str(document["route"]), legacy_approved=True,
        )

    def create_draft_multi(
        self,
        document_ids: list[str],
        profile_id: int,
        configuration: dict[str, Any],
        *,
        sufficiency_report_id: str | None = None,
        route_id: str | None = None,
        legacy_approved: bool = False,
    ) -> dict[str, Any]:
        self._validate_configuration(configuration)
        if not document_ids:
            raise RouteDraftError("Rota için en az bir etkin kaynak seçilmelidir.")
        documents = [self.database.source_document(item) for item in document_ids]
        if any(not item or item.get("status") != "active" for item in documents):
            raise RouteDraftError("Seçilen kaynaklardan biri bulunamadı veya arşivlenmiş.")
        resolved_documents = [item for item in documents if item]
        sufficiency_approved = legacy_approved
        report_payload: dict[str, Any] | None = None
        if sufficiency_report_id:
            report = self.database.sufficiency_report(sufficiency_report_id)
            if not report or not report["approved"] or not report["can_generate"]:
                raise RouteDraftError("Rota için onaylanmış ve üretilebilir bir yeterlilik raporu gerekir.")
            if list(report["report"].get("source_ids", [])) != document_ids:
                raise RouteDraftError("Yeterlilik raporu seçilen kaynak kümesine ait değil.")
            sufficiency_approved = True
            report_payload = report["report"]
        source_set_hash = stable_hash("|".join(document_ids), str(configuration["goal"]), length=20)
        resolved_route_id = route_id or "custom-" + stable_hash(
            source_set_hash, str(configuration["title"]), str(profile_id), length=20
        )
        parsed_documents: list[tuple[dict[str, Any], ParsedDocument]] = []
        for document in resolved_documents:
            parsed_documents.append(
                (
                    document,
                    self.parser.parse(
                        Path(document["path"]),
                        route=resolved_route_id,
                        route_title=str(configuration["title"]),
                        document_id=document["id"],
                        source_name=document.get("original_filename") or document["filename"],
                    ),
                )
            )
        tasks: list[dict[str, Any]] = []
        seen_content: set[str] = set()
        seen_token_sets: list[set[str]] = []
        chunk_lookup: dict[str, tuple[dict[str, Any], Any]] = {}
        heading_sources: dict[str, list[dict[str, Any]]] = {}
        for document, parsed in parsed_documents:
            unique_chunks = []
            for chunk in parsed.chunks:
                chunk_lookup[chunk.id] = (document, chunk)
                heading_sources.setdefault(clean_text(chunk.heading).casefold(), []).append(
                    {
                        "source_id": document["id"], "chunk_id": chunk.id,
                        "section_path": chunk.section_path, "page": chunk.page,
                        "url": document.get("source_url"),
                    }
                )
                if chunk.content_hash in seen_content:
                    continue
                token_set = set(re.findall(r"[a-zçğıöşü0-9]+", clean_text(chunk.body).casefold()))
                if token_set and any(
                    len(token_set & previous) / max(1, len(token_set | previous)) >= 0.9
                    for previous in seen_token_sets
                ):
                    continue
                seen_content.add(chunk.content_hash)
                seen_token_sets.append(token_set)
                unique_chunks.append(chunk)
            unique_document = ParsedDocument(
                id=parsed.id, filename=parsed.filename, path=parsed.path, title=parsed.title,
                route=parsed.route, route_title=parsed.route_title, sha256=parsed.sha256,
                chunks=tuple(unique_chunks), display_name=parsed.display_name,
            )
            extracted = extract_source_tasks(unique_document)
            generated = (
                self._structured_tasks(resolved_route_id, extracted)
                if extracted else self._topic_tasks(resolved_route_id, unique_document, configuration)
            )
            for task in generated:
                is_web = document.get("source_kind") == "web"
                task["generation_type"] = "internet kaynağından çıkarıldı" if (
                    is_web and task["provenance"] == "belgeden çıkarıldı"
                ) else task["provenance"]
                task["module"] = f"Modül {task.get('phase')}" if task.get("phase") else task.get("source_section")
                task["confidence_explanation"] = (
                    "Görev doğrudan onaylanmış kaynak yapısından çıkarıldı."
                    if task["provenance"] == "belgeden çıkarıldı"
                    else "Görev kaynak chunk'a bağlı, sistem tarafından çalışma amacıyla üretildi."
                )
            tasks.extend(generated)
        if not tasks:
            raise RouteDraftError("Seçilen kaynaklardan rota görevi üretilemedi.")
        if report_payload and report_payload.get("prerequisites"):
            anchor = tasks[0]
            prerequisites = []
            for missing in report_payload["prerequisites"]:
                prerequisites.append({
                    **anchor,
                    "id": "draft-" + stable_hash(resolved_route_id, str(missing), "external-prerequisite", length=20),
                    "title": f"Belge dışı ön koşul: {missing}",
                    "description": (
                        f"Seçili kaynaklarda açıkça bulunmayan '{missing}' için önce güvenilir bir temel kaynak ekleyin. "
                        "Bu öneri belgede varmış gibi sunulmaz."
                    ),
                    "task_type": "teori",
                    "estimated_minutes": min(45, int(configuration["daily_minutes"])),
                    "provenance": "belge dışı ön koşul önerisi",
                    "generation_type": "belge dışı ön koşul önerisi",
                    "confidence_explanation": "Eksik temel kapsamı yeterlilik raporunda deterministik olarak tespit edildi.",
                })
            tasks = prerequisites + tasks
        for index, task in enumerate(tasks):
            task["position"] = index + 1
            task["prerequisite_task_ids"] = [tasks[index - 1]["id"]] if index else []
        validate_prerequisite_graph(tasks)
        route = {
            "id": resolved_route_id,
            "document_id": document_ids[0],
            "profile_id": profile_id,
            "sufficiency_approved": sufficiency_approved,
            "source_set_hash": source_set_hash,
            **configuration,
        }
        self.database.save_custom_route(route, tasks)
        self.database.set_route_sources(
            resolved_route_id,
            [
                {
                    "source_id": document["id"],
                    "source_type": document.get("source_kind", "document"),
                    "source_role": "primary" if index == 0 else "auxiliary",
                }
                for index, document in enumerate(resolved_documents)
            ],
        )
        for task in tasks:
            found = chunk_lookup.get(task["source_chunk_id"])
            if not found:
                continue
            _, chunk = found
            references = heading_sources.get(clean_text(chunk.heading).casefold(), [])
            self.database.save_task_sources(task["id"], "draft", references or [{
                "source_id": chunk.document_id, "chunk_id": chunk.id,
                "section_path": chunk.section_path, "page": chunk.page,
            }])
        return {
            "route": self.database.custom_route(resolved_route_id),
            "tasks": self.database.custom_route_tasks(resolved_route_id),
            "sources": self.database.route_sources(resolved_route_id),
        }

    @staticmethod
    def _validate_configuration(configuration: dict[str, Any]) -> None:
        required = {
            "title", "goal", "declared_level", "weekly_days", "daily_minutes", "preferred_days",
            "start_date", "target_end_date", "theory_practice_preference",
        }
        missing = required - configuration.keys()
        if missing:
            raise RouteDraftError("Eksik rota alanları: " + ", ".join(sorted(missing)))
        if not str(configuration["title"]).strip() or not str(configuration["goal"]).strip():
            raise RouteDraftError("Rota adı ve öğrenme hedefi zorunludur.")
        if not 1 <= int(configuration["weekly_days"]) <= 7:
            raise RouteDraftError("Haftalık çalışma günü 1-7 arasında olmalıdır.")
        if not 10 <= int(configuration["daily_minutes"]) <= 720:
            raise RouteDraftError("Günlük çalışma süresi 10-720 dakika arasında olmalıdır.")
        if not configuration["preferred_days"]:
            raise RouteDraftError("En az bir çalışma günü seçilmelidir.")
        if date.fromisoformat(str(configuration["target_end_date"])) < date.fromisoformat(str(configuration["start_date"])):
            raise RouteDraftError("Hedef bitiş tarihi başlangıç tarihinden önce olamaz.")

    @staticmethod
    def _task_dict(route_id: str, source: SourceTask, kind: str, position: int) -> dict[str, Any]:
        task_id = "draft-" + stable_hash(route_id, source.source_chunk_id, kind, str(position), length=20)
        return {
            "id": task_id,
            "source_document": source.source_document,
            "source_section": source.source_section,
            "source_chunk_id": source.source_chunk_id,
            "source_page": source.source_page,
            "phase": source.phase,
            "week": source.week,
            "day": source.day,
            "title": source.title,
            "description": source.description,
            "task_type": source.task_type,
            "estimated_minutes": source.estimated_minutes,
            "provenance": source.provenance,
            "generation_type": source.generation_type or source.provenance,
            "module": source.module,
            "confidence_explanation": source.confidence_explanation,
            "position": position,
            "included": True,
        }

    def _structured_tasks(self, route_id: str, sources: list[SourceTask]) -> list[dict[str, Any]]:
        return [self._task_dict(route_id, source, "source", position) for position, source in enumerate(sources, start=1)]

    def _topic_tasks(
        self,
        route_id: str,
        document: ParsedDocument,
        configuration: dict[str, Any],
    ) -> list[dict[str, Any]]:
        chunks = [chunk for chunk in document.chunks if len(chunk.body.strip()) >= 30]
        if not chunks:
            return []
        preference = str(configuration["theory_practice_preference"])
        module_size = {"Teori ağırlıklı": 4, "Dengeli": 3, "Uygulama ağırlıklı": 2}.get(preference, 3)
        tasks: list[dict[str, Any]] = []
        for index, chunk in enumerate(chunks, start=1):
            module = ((index - 1) // module_size) + 1
            minutes = min(120, max(30, ((len(chunk.body) // 900) + 1) * 30))
            source = SourceTask(
                source_document=chunk.source,
                source_section=chunk.section_path,
                source_chunk_id=chunk.id,
                route=route_id,
                phase=str(module),
                week=None,
                day=None,
                title=f"Modül {module}: Oku — {chunk.heading}",
                description=chunk.body,
                task_type="teori",
                estimated_minutes=minutes,
                estimated_is_system=True,
                position=len(tasks) + 1,
                is_critical=index == 1,
                provenance="belgeden çıkarıldı",
                source_page=chunk.page,
            )
            tasks.append(self._task_dict(route_id, source, "read", len(tasks) + 1))
            if index % module_size == 0 or index == len(chunks):
                practice = SourceTask(
                    source_document=chunk.source,
                    source_section=chunk.section_path,
                    source_chunk_id=chunk.id,
                    route=route_id,
                    phase=str(module),
                    week=None,
                    day=None,
                    title=f"Modül {module}: Kaynak temelli uygulama",
                    description=(
                        "Bu modülde okunan kaynak bölümlerindeki kavramları kullanarak küçük bir uygulama veya "
                        "açıklamalı örnek hazırlayın. Bu görev kaynak belgede yazılı bir görev değildir; sistem "
                        "tarafından çalışma amacıyla oluşturulmuştur."
                    ),
                    task_type="uygulama",
                    estimated_minutes=min(90, int(configuration["daily_minutes"])),
                    estimated_is_system=True,
                    position=len(tasks) + 1,
                    is_critical=False,
                    provenance="sistem tarafından oluşturulan alıştırma",
                    source_page=chunk.page,
                )
                tasks.append(self._task_dict(route_id, practice, "practice", len(tasks) + 1))
                review = SourceTask(
                    source_document=chunk.source,
                    source_section=chunk.section_path,
                    source_chunk_id=chunk.id,
                    route=route_id,
                    phase=str(module),
                    week=None,
                    day=None,
                    title=f"Modül {module}: Tekrar ve öz değerlendirme",
                    description=(
                        "Kaynak bölümlerini kendi cümlelerinizle özetleyin ve anlamadığınız noktaları işaretleyin. "
                        "Bu tekrar görevi sistem tarafından oluşturulmuştur."
                    ),
                    task_type="tekrar",
                    estimated_minutes=min(45, int(configuration["daily_minutes"])),
                    estimated_is_system=True,
                    position=len(tasks) + 1,
                    is_critical=False,
                    provenance="sistem tarafından oluşturulan alıştırma",
                    source_page=chunk.page,
                )
                tasks.append(self._task_dict(route_id, review, "review", len(tasks) + 1))
        if len(chunks) >= 3:
            last = chunks[-1]
            capstone = SourceTask(
                source_document=last.source,
                source_section=last.section_path,
                source_chunk_id=last.id,
                route=route_id,
                phase=str(((len(chunks) - 1) // module_size) + 1),
                week=None,
                day=None,
                title="Bitirme çalışması: Öğrendiklerini birleştir",
                description=(
                    "Belgeden öğrendiğiniz ana kavramları bir araya getiren küçük bir çıktı hazırlayın. "
                    "Bu bitirme çalışması kaynak belgede yazılı değildir; sistem tarafından oluşturulmuştur."
                ),
                task_type="uygulama",
                estimated_minutes=min(120, max(45, int(configuration["daily_minutes"]))),
                estimated_is_system=True,
                position=len(tasks) + 1,
                is_critical=True,
                provenance="sistem tarafından oluşturulan alıştırma",
                source_page=last.page,
            )
            tasks.append(self._task_dict(route_id, capstone, "capstone", len(tasks) + 1))
        return tasks

    def install_route(self, route_id: str, profile_id: int) -> dict[str, Any]:
        route = self.database.custom_route(route_id)
        profile = self.database.get_profile(profile_id)
        if not route or route.get("document_status") != "active" or route.get("status") == "archived":
            raise RouteDraftError("Etkin özel rota bulunamadı.")
        if not route.get("sufficiency_approved"):
            raise RouteDraftError("Rota kişisel plana aktarılmadan önce kaynak yeterlilik raporu onaylanmalıdır.")
        if not profile:
            raise RouteDraftError("Rota için kullanıcı profili bulunamadı.")
        draft_tasks = self.database.custom_route_tasks(route_id, included_only=True)
        if not draft_tasks:
            raise RouteDraftError("Onaylanacak en az bir görev seçilmelidir.")
        sources = [
            SourceTask(
                source_document=item["source_document"],
                source_section=item["source_section"],
                source_chunk_id=item["source_chunk_id"],
                route=route_id,
                phase=item.get("phase"),
                week=item.get("week"),
                day=item.get("day"),
                title=item["title"],
                description=item["description"],
                task_type=item["task_type"],
                estimated_minutes=int(item["estimated_minutes"]),
                estimated_is_system=True,
                position=index,
                is_critical=index == 1,
                provenance=item["provenance"],
                source_page=item.get("source_page"),
                planning_key=item["id"],
                module=item.get("module"),
                confidence_explanation=item.get("confidence_explanation", ""),
                generation_type=item.get("generation_type", item["provenance"]),
                prerequisite_planning_keys=tuple(item.get("prerequisite_task_ids", [])),
            )
            for index, item in enumerate(draft_tasks, start=1)
        ]
        planning_profile = {
            **profile,
            "preferred_route": route_id,
            "declared_level": route["declared_level"],
            "measured_level": None,
            "assessment_status": "user_declared",
            "weekly_days": route["weekly_days"],
            "daily_minutes": route["daily_minutes"],
            "preferred_days": route["preferred_days"],
            "start_date": route["start_date"],
            "target_end_date": route["target_end_date"],
            "theory_practice_preference": route["theory_practice_preference"],
        }
        plan = self.planner.generate(planning_profile, sources)
        count = self.database.replace_profile_plan(profile_id, plan.tasks)
        draft_by_chunk: dict[str, list[dict[str, Any]]] = {}
        for item in draft_tasks:
            draft_by_chunk.setdefault(item["source_chunk_id"], []).append(item)
        for installed in plan.tasks:
            candidates = draft_by_chunk.get(installed["source_chunk_id"], [])
            draft = next((item for item in candidates if installed["title"].startswith(item["title"])), None)
            if draft:
                references = self.database.task_sources(draft["id"], "draft")
                if references:
                    self.database.save_task_sources(installed["id"], "plan", references)
        with self.database.connection() as conn:
            conn.execute(
                """INSERT INTO learning_routes(id, title, source_document, description, task_count, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET title=excluded.title, source_document=excluded.source_document,
                       description=excluded.description, task_count=excluded.task_count,
                       updated_at=excluded.updated_at""",
                (
                    route_id,
                    route["title"],
                    route["original_filename"],
                    f"{route['goal']} hedefi için kullanıcı belgesinden oluşturulan yerel rota.",
                    len(sources),
                    utc_now(),
                ),
            )
        if route["status"] == "draft":
            self.database.confirm_custom_route(route_id, profile_id)
        return {
            "route_id": route_id,
            "task_count": count,
            "source_task_count": len(sources),
            "warnings": plan.warnings,
            "projected_end_date": plan.projected_end_date,
            "accelerated_suggestion": plan.accelerated_suggestion,
        }
