from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

from local_learning_coach.config import Settings
from local_learning_coach.indexing import EmbeddingProvider, IndexManager


TOKEN_RE = re.compile(r"[^\W_]+(?:[#.+-][^\W_]*)?", re.UNICODE)

TRANSLATIONS = {
    "gün": "day", "günde": "day", "gündür": "day", "gündedir": "day", "günler": "days day",
    "günlerde": "days day", "günlerdedir": "days day", "günlerindedir": "days day",
    "hangi": "which", "ne": "what", "zaman": "when",
    "hafta": "week", "haftada": "week", "haftadadır": "week", "haftadır": "week",
    "başlıyor": "starts begin introduction", "başlar": "starts begin introduction",
    "aşama": "phase", "faz": "phase", "veri": "data", "görselleştirme": "visualization plotting",
    "araçları": "tools", "kuantum": "quantum", "programlama": "programming", "öğreniliyor": "learn basics",
    "gerçek": "real", "donanım": "hardware", "çalışıyor": "runs", "test": "testing validation",
    "karşılaştırılıyor": "compare comparison", "karşılaştırma": "compare comparison", "kullanılıyor": "used use",
    "nerede": "where", "pandas": "pandas", "pytorch": "pytorch", "kaydırmalı": "sliding",
    "pencere": "window", "hata": "error", "teslimat": "deliverable", "sunum": "presentation",
    "sunumları": "presentations presentation", "sunumlar": "presentations presentation",
    "teslimatlar": "deliverables checklist", "kontrol": "review checklist", "final": "final",
    "eksik": "missing", "veriler": "data", "gruplama": "grouping groupby", "değer": "value",
    "hayatta": "survival", "kalma": "survival", "demografi": "demographics passenger profile",
    "demografisi": "demographics passenger profile", "yolcu": "passenger", "yolcularının": "passenger demographics",
    "hikâyesi": "story storytelling", "hikayesi": "story storytelling",
    "yeniden": "reproducibility reproducible", "üretilebilirlik": "reproducibility reproducible",
    "tanıtımı": "introduction intro", "filtreleme": "filtering selection",
    "dokümantasyon": "documentation", "dokümantasyonu": "documentation",
    "eğitimi": "training", "eğitim": "training", "yapılır": "scheduled done", "yapılıyor": "scheduled done",
    "modelin": "models", "modellerin": "models", "hiperparametre": "hyperparameter tuning",
}


def normalize(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def tokenize(value: str, *, expand: bool = False) -> list[str]:
    tokens = TOKEN_RE.findall(normalize(value))
    if expand:
        additions: list[str] = []
        for token in tokens:
            additions.extend(TRANSLATIONS.get(token, "").split())
        tokens.extend(additions)
    return tokens


class BM25:
    def __init__(self, documents: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.documents = documents
        self.k1 = k1
        self.b = b
        self.lengths = np.asarray([len(doc) for doc in documents], dtype=np.float32)
        self.avgdl = float(self.lengths.mean()) if len(self.lengths) else 1.0
        self.term_frequencies = [Counter(doc) for doc in documents]
        document_frequencies: Counter[str] = Counter()
        for doc in documents:
            document_frequencies.update(set(doc))
        count = max(len(documents), 1)
        self.idf = {
            term: math.log(1 + (count - frequency + 0.5) / (frequency + 0.5))
            for term, frequency in document_frequencies.items()
        }

    def scores(self, query_tokens: list[str]) -> np.ndarray:
        scores = np.zeros(len(self.documents), dtype=np.float32)
        for index, frequencies in enumerate(self.term_frequencies):
            length_norm = self.k1 * (1 - self.b + self.b * self.lengths[index] / max(self.avgdl, 1.0))
            for term in query_tokens:
                frequency = frequencies.get(term, 0)
                if frequency:
                    scores[index] += self.idf.get(term, 0.0) * frequency * (self.k1 + 1) / (frequency + length_norm)
        return scores


@dataclass(frozen=True)
class RetrievalResult:
    rank: int
    chunk: dict[str, Any]
    rrf_score: float
    dense_score: float
    bm25_score: float
    exact_bonus: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class HybridRetriever:
    def __init__(self, settings: Settings):
        self.settings = settings
        chunks, vectors, manifest = IndexManager(settings).load()
        self.chunks = chunks
        self.vectors = vectors
        self.manifest = manifest
        self.provider = EmbeddingProvider(manifest["embedding_backend"], manifest["embedding_model"])
        self.bm25 = BM25([tokenize(chunk["text"]) for chunk in chunks])

    def search(
        self,
        query: str,
        *,
        top_k: int = 6,
        candidate_k: int = 20,
        document: str | None = None,
        document_id: str | None = None,
        route: str | None = None,
        phase: str | None = None,
        week: str | None = None,
        day: str | None = None,
        max_context_chars: int | None = None,
    ) -> list[RetrievalResult]:
        if not query.strip():
            return []
        normalized_query = normalize(query)
        if route is None:
            route = self._infer_route(normalized_query)
        if day is None:
            day_match = re.search(
                r"(?:\b(?:day|gün)\s*(\d+)\b|\b(\d+)\s*\.?(?:\s*)(?:day|gün)\b)",
                normalized_query,
            )
            if day_match:
                day = day_match.group(1) or day_match.group(2)
        allowed = np.asarray(
            [
                (document is None or chunk["source"] == document)
                and (document_id is None or chunk["document_id"] == document_id)
                and (route is None or chunk["route"] == route)
                and (phase is None or str(chunk.get("phase")) == str(phase))
                and (week is None or str(chunk.get("week")) == str(week))
                and (day is None or self._day_matches(str(chunk.get("day")), str(day)))
                for chunk in self.chunks
            ],
            dtype=bool,
        )
        if not allowed.any():
            return []
        expanded_query = " ".join(tokenize(query, expand=True))
        query_vector = self.provider.embed_queries([expanded_query])[0]
        dense_scores = self.vectors @ query_vector
        bm25_scores = self.bm25.scores(tokenize(query, expand=True))
        dense_scores = np.where(allowed, dense_scores, -np.inf)
        bm25_scores = np.where(allowed, bm25_scores, -np.inf)
        dense_rank = [int(i) for i in np.argsort(-dense_scores)[:candidate_k] if np.isfinite(dense_scores[i])]
        bm25_rank = [int(i) for i in np.argsort(-bm25_scores)[:candidate_k] if np.isfinite(bm25_scores[i])]

        fused: defaultdict[int, float] = defaultdict(float)
        for rank_index, item_index in enumerate(dense_rank, start=1):
            fused[item_index] += 1.0 / (60 + rank_index)
        for rank_index, item_index in enumerate(bm25_rank, start=1):
            fused[item_index] += 1.0 / (60 + rank_index)

        original_terms = set(tokenize(query))
        exact_bonus: dict[int, float] = {}
        for item_index in fused:
            chunk_terms = set(tokenize(self.chunks[item_index]["text"]))
            overlap = len(original_terms & chunk_terms)
            bonus = min(0.008, overlap * 0.0015)
            if normalize(query) in normalize(self.chunks[item_index]["text"]):
                bonus += 0.004
            chunk_text = normalize(self.chunks[item_index]["heading"] + " " + self.chunks[item_index]["body"])
            if any(term in normalized_query for term in ("başlıyor", "başlar", "starts", "begin")):
                if any(term in chunk_text for term in ("intro", "fundamental", "quickstart", "get started", "basics")):
                    bonus += 0.004
                heading_text = normalize(self.chunks[item_index]["heading"])
                named_entities = {term for term in original_terms if term in {"pandas", "pytorch", "q#", "qsharp"}}
                if named_entities and any(entity in heading_text for entity in named_entities):
                    bonus += 0.008
                if named_entities and any(term in heading_text for term in ("concept introduction", "introduction", "intro", "quickstart")):
                    if any(entity in chunk_text for entity in named_entities):
                        bonus += 0.012
            if any(term in normalized_query for term in ("görselleştirme", "visualization")) and any(
                term in normalized_query for term in ("araçları", "tools")
            ):
                if "matplotlib" in chunk_text and "seaborn" in chunk_text:
                    bonus += 0.012
            if any(term in normalized_query for term in ("karşılaştır", "compare", "comparison")):
                if any(term in chunk_text for term in ("compar", "karşılaştır")):
                    bonus += 0.006
            exact_bonus[item_index] = bonus
            fused[item_index] += bonus

        ordered = sorted(fused, key=lambda index: fused[index], reverse=True)
        limit_chars = max_context_chars or self.settings.max_context_chars
        selected: list[int] = []
        section_counts: Counter[str] = Counter()
        used_chars = 0
        for item_index in ordered:
            chunk = self.chunks[item_index]
            section = chunk["section_path"]
            if section_counts[section] >= 2:
                continue
            if selected and used_chars + chunk["n_chars"] > limit_chars:
                continue
            selected.append(item_index)
            section_counts[section] += 1
            used_chars += chunk["n_chars"]
            if len(selected) >= top_k:
                break

        results = []
        for rank, item_index in enumerate(selected, start=1):
            results.append(
                RetrievalResult(
                    rank=rank,
                    chunk=self.chunks[item_index],
                    rrf_score=float(fused[item_index]),
                    dense_score=float(dense_scores[item_index]),
                    bm25_score=float(bm25_scores[item_index]),
                    exact_bonus=float(exact_bonus[item_index]),
                )
            )
        return results

    @staticmethod
    def _infer_route(query: str) -> str | None:
        if "ml plan" in query or "machine learning plan" in query or "makine öğren" in query:
            return "machine-learning"
        if "titanic" in query:
            return "data-analysis"
        if any(term in query for term in ("q#", "qsharp", "quantum", "kuantum", "qubit", "grover")):
            return "quantum"
        route_terms = {
            "data-analysis": (
                "titanic", "pandas", "seaborn", "matplotlib", "passenger", "demografi",
                "yolcu", "jupyter", "eksik veri", "gruplama", "filtreleme", "eda",
            ),
            "machine-learning": (
                "pytorch", "lstm", "gru", "rmse", "mse", "sliding window", "stock price",
                "ml plan", "rnn", "hiperparametre", "makine öğren",
            ),
            "quantum": ("q#", "qsharp", "quantum", "kuantum", "qubit", "grover"),
        }
        scores = {route: sum(query.count(term) for term in terms) for route, terms in route_terms.items()}
        selected = max(scores, key=scores.get)
        return selected if scores[selected] else None

    @staticmethod
    def _day_matches(chunk_day: str, requested_day: str) -> bool:
        if chunk_day == requested_day:
            return True
        if "-" in chunk_day:
            try:
                start, end = (int(value) for value in chunk_day.split("-", 1))
                return start <= int(requested_day) <= end
            except ValueError:
                return False
        return False
