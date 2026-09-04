from __future__ import annotations

import hashlib
import re
import sys
import types
import unicodedata
import warnings
from typing import Iterable

import numpy as np


class EmbeddingError(RuntimeError):
    """Embedding hazırlama veya model yükleme hatası."""


TOKEN_RE = re.compile(r"[^\W_]+(?:[#.+-][^\W_]*)?", re.UNICODE)


def l2_normalize(matrix: np.ndarray) -> np.ndarray:
    values = np.asarray(matrix, dtype=np.float32)
    if values.ndim == 1:
        values = values.reshape(1, -1)
    norms = np.linalg.norm(values, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return values / norms


class EmbeddingProvider:
    """FastEmbed üretim backend'i ve çevrimdışı testler için deterministik backend."""

    def __init__(self, backend: str, model_name: str, *, dimension: int = 384):
        self.backend = backend
        self.model_name = model_name
        self.dimension = dimension
        self._model = None

    def _load_fastembed(self):
        if self._model is not None:
            return self._model
        self._ensure_optional_sparse_hash_module()
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:
            raise EmbeddingError(
                "FastEmbed kurulu değil. '.venv\\Scripts\\python -m pip install -r requirements.txt' komutunu çalıştırın."
            ) from exc
        supported = {item["model"]: item for item in TextEmbedding.list_supported_models()}
        if self.model_name not in supported:
            available = [name for name in supported if "multilingual" in name.casefold()]
            raise EmbeddingError(
                f"FastEmbed modeli desteklenmiyor: {self.model_name}. Desteklenen çok dilli modeller: "
                + ", ".join(available[:8])
            )
        try:
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", message=r"The model .* now uses mean pooling.*")
                self._model = TextEmbedding(model_name=self.model_name)
            self.dimension = int(supported[self.model_name]["dim"])
            return self._model
        except Exception as exc:
            raise EmbeddingError(
                f"Yerel embedding modeli yüklenemedi ({self.model_name}). İlk kurulumda model indirmesi için internet gerekebilir: {exc}"
            ) from exc

    @staticmethod
    def _ensure_optional_sparse_hash_module() -> None:
        """Windows ilkesi mmh3 DLL'ini engellerse dense FastEmbed'i sparse eklentiden ayırır.

        Uygulama kendi BM25 uygulamasını kullandığı için FastEmbed'in sparse BM25 modülü
        çalıştırılmaz. Bu uyumluluk modülü yalnızca fastembed paketinin üst-seviye importunu
        tamamlar; dense ONNX embedding hesaplamasını değiştirmez.
        """
        try:
            import mmh3  # noqa: F401
            return
        except ImportError:
            sys.modules.pop("mmh3", None)
        module = types.ModuleType("mmh3")

        def digest(value, seed=0, *args, **kwargs):
            data = value.encode("utf-8") if isinstance(value, str) else bytes(value)
            return hashlib.blake2b(seed.to_bytes(4, "little", signed=False) + data, digest_size=16).digest()

        def hash_value(value, seed=0, signed=True, *args, **kwargs):
            raw = int.from_bytes(digest(value, seed)[:4], "little", signed=False)
            return raw - 2**32 if signed and raw >= 2**31 else raw

        module.hash = hash_value
        module.hash_bytes = digest
        module.mmh3_x64_128_digest = digest
        module.hash128 = lambda value, seed=0, signed=False, **kwargs: int.from_bytes(digest(value, seed), "little", signed=signed)
        module.hash64 = lambda value, seed=0, signed=True, **kwargs: tuple(
            int.from_bytes(digest(value, seed)[offset:offset + 8], "little", signed=signed) for offset in (0, 8)
        )
        sys.modules["mmh3"] = module

    def validate(self) -> None:
        if self.backend == "fastembed":
            self._load_fastembed()
        elif self.backend != "hashing":
            raise EmbeddingError(f"Desteklenmeyen embedding backend'i: {self.backend}")

    def embed_passages(self, texts: Iterable[str]) -> np.ndarray:
        values = list(texts)
        if not values:
            return np.empty((0, self.dimension), dtype=np.float32)
        if self.backend == "fastembed":
            model = self._load_fastembed()
            method = getattr(model, "passage_embed", None)
            vectors = list(method(values) if method else model.embed([f"passage: {text}" for text in values]))
            return l2_normalize(np.asarray(vectors, dtype=np.float32))
        return self._hash_vectors(values, "passage")

    def embed_queries(self, texts: Iterable[str]) -> np.ndarray:
        values = list(texts)
        if not values:
            return np.empty((0, self.dimension), dtype=np.float32)
        if self.backend == "fastembed":
            model = self._load_fastembed()
            method = getattr(model, "query_embed", None)
            vectors = list(method(values) if method else model.embed([f"query: {text}" for text in values]))
            return l2_normalize(np.asarray(vectors, dtype=np.float32))
        return self._hash_vectors(values, "query")

    def _hash_vectors(self, texts: list[str], mode: str) -> np.ndarray:
        matrix = np.zeros((len(texts), self.dimension), dtype=np.float32)
        for row, text in enumerate(texts):
            normalized = unicodedata.normalize("NFKC", text).casefold()
            tokens = TOKEN_RE.findall(normalized)
            features = tokens + [f"{tokens[i]}_{tokens[i + 1]}" for i in range(max(0, len(tokens) - 1))]
            for token in features:
                digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
                index = int.from_bytes(digest[:4], "little") % self.dimension
                sign = 1.0 if digest[4] & 1 else -1.0
                matrix[row, index] += sign
        return l2_normalize(matrix)
