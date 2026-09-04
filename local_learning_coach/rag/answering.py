from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from local_learning_coach.config import Settings
from local_learning_coach.foundry import FoundryAdapter
from local_learning_coach.retrieval import HybridRetriever, RetrievalResult


SYSTEM_PROMPT = """Sen Local Learning Coach'sun. Aşağıdaki kuralları eksiksiz uygula:
- Yalnızca verilen CONTEXT ve kayıtlı kullanıcı profilini kullan.
- Belgelerde olmayan içeriği belge bilgisi gibi sunma.
- Her belge iddiasını [1], [2] biçiminde kaynaklandır.
- Kullanıcının dilinde cevap ver.
- Bilgi bulunmuyorsa bunu açıkça belirt.
- Faz, hafta, gün, teknoloji, URL veya teslimat uydurma.
- Kullanıcı profilinde bulunmayan özelliği tahmin etme.
- Görev süresi tahminse “tahmini” olarak belirt.
- Belge bilgisini, planlama kararını ve LLM çıkarımını açıkça ayır.
- Planlama motorunun görev durumlarını değiştirme.
- Kullanıcıyı küçümseyen veya yargılayan dil kullanma.
- Tamamlanmamış görevi tamamlanmış gösterme.

Yanıt biçimi:
1. Kısa cevap
2. Bugünkü görev/öneri
3. Bu görevin seçilme nedeni
4. Kaynaklar
5. Sonraki adım
"""


class RAGAnswerer:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.retriever = HybridRetriever(settings)
        self.foundry = FoundryAdapter(settings)

    def retrieve(self, query: str, **filters: Any) -> list[RetrievalResult]:
        return self.retriever.search(query, **filters)

    def stream_answer(
        self,
        question: str,
        *,
        profile: dict[str, Any] | None = None,
        today_tasks: list[dict[str, Any]] | None = None,
        retrieval_results: list[RetrievalResult] | None = None,
        **filters: Any,
    ) -> tuple[Iterator[str], list[RetrievalResult]]:
        results = retrieval_results if retrieval_results is not None else self.retrieve(question, **filters)
        if not results:
            raise ValueError("Bu soru için seçili yerel belgelerde yeterli bilgi bulunamadı.")
        context_parts: list[str] = []
        for index, result in enumerate(results, start=1):
            chunk = result.chunk
            context_parts.append(
                f"[{index}] Belge: {chunk['source']}\nBölüm: {chunk['section_path']}\nMetin: {chunk['body']}"
            )
        safe_profile = self._safe_profile(profile)
        task_text = self._task_context(today_tasks or [])
        user_prompt = (
            "CONTEXT\n" + "\n\n".join(context_parts)
            + "\n\nKAYITLI PROFİL\n" + safe_profile
            + "\n\nBUGÜNÜN PLANLANMIŞ GÖREVLERİ\n" + task_text
            + "\n\nKULLANICI SORUSU\n" + question
        )
        return self.foundry.stream_chat(
            [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_prompt}]
        ), results

    @staticmethod
    def _safe_profile(profile: dict[str, Any] | None) -> str:
        if not profile:
            return "Kayıtlı profil yok."
        allowed = (
            "name", "goal", "preferred_route", "declared_level", "measured_level",
            "daily_minutes", "preferred_days", "theory_practice_preference",
            "completed_topics", "difficult_topics", "review_preference",
        )
        return "\n".join(f"{key}: {profile.get(key)}" for key in allowed)

    @staticmethod
    def _task_context(tasks: list[dict[str, Any]]) -> str:
        if not tasks:
            return "Bugüne planlanmış görev yok."
        return "\n".join(
            f"- {task['title']} | durum={task['status']} | süre={task['estimated_minutes']} dakika (tahmini)"
            for task in tasks
        )
