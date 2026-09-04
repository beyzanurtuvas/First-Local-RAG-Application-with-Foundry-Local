from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from local_learning_coach.config import DOCUMENT_DEFINITIONS, Settings
from local_learning_coach.database import Database, DatabaseError
from local_learning_coach.documents import DocumentParser
from local_learning_coach.documents.parser import extract_source_tasks
from local_learning_coach.foundry import FoundryAdapter
from local_learning_coach.indexing import IndexManager
from local_learning_coach.interview import InterviewCoach
from local_learning_coach.learning.assessment import AssessmentEngine
from local_learning_coach.learning.advanced import (
    AdaptivePlanner, SourceBoundQuizEngine, SufficiencyAnalyzer, goal_countdown,
)
from local_learning_coach.learning.custom_routes import CustomRouteBuilder, UploadedDocumentManager
from local_learning_coach.learning.planner import PersonalPlanner
from local_learning_coach.rag import RAGAnswerer
from local_learning_coach.retrieval import HybridRetriever
from local_learning_coach.search import OpenSearchBackend, SearchBackendRouter
from local_learning_coach.web import configured_web_adapter


class LearningCoachService:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings.load()
        self.settings.ensure_runtime_dirs()
        self.database = Database(self.settings.db_path)
        self._restore_runtime_settings()
        self.assessment = AssessmentEngine(self.database)
        self.planner = PersonalPlanner()
        self.uploads = UploadedDocumentManager(self.settings, self.database)
        self.custom_routes = CustomRouteBuilder(self.settings, self.database, self.planner)
        self.sufficiency = SufficiencyAnalyzer(self.database)
        self.custom_quiz_engine = SourceBoundQuizEngine(self.database)
        self.adaptive = AdaptivePlanner(self.database)
        self.web_sources = configured_web_adapter(self.settings, self.database)
        self.interview = InterviewCoach(self.database, self.settings)

    def _restore_runtime_settings(self) -> None:
        if "application_settings" not in self.database.table_names():
            return
        persisted_backend = self.database.get_setting("search_backend")
        persisted_foundry_endpoint = self.database.get_setting("foundry_endpoint")
        persisted_foundry_model = self.database.get_setting("foundry_model")
        if persisted_backend in {"local", "opensearch"}:
            self.settings = replace(self.settings, search_backend=persisted_backend)
        if persisted_foundry_endpoint:
            self.settings = replace(self.settings, foundry_endpoint=persisted_foundry_endpoint.rstrip("/"))
        if persisted_foundry_model is not None:
            self.settings = replace(self.settings, foundry_model=persisted_foundry_model.strip())

    def setup(self, *, build_index: bool = True, force_index: bool = False) -> dict[str, Any]:
        self.settings.validate_sources()
        migrations = self.database.migrate()
        self._restore_runtime_settings()
        self.assessment.seed_questions()
        interview_seed = self.interview.seed()
        documents = self._documents()
        route_counts: dict[str, int] = {}
        with self.database.connection() as conn:
            for document in documents:
                count = len(extract_source_tasks(document))
                route_counts[document.route] = count
                conn.execute(
                    """INSERT INTO learning_routes(id, title, source_document, description, task_count, updated_at)
                       VALUES (?, ?, ?, ?, ?, datetime('now'))
                       ON CONFLICT(id) DO UPDATE SET title=excluded.title, source_document=excluded.source_document,
                           description=excluded.description, task_count=excluded.task_count, updated_at=excluded.updated_at""",
                    (
                        document.route,
                        document.route_title,
                        document.filename,
                        f"{document.title} belgesinden çıkarılan yerel öğrenme rotası.",
                        count,
                    ),
                )
        manifest = None
        if build_index:
            manifest = IndexManager(self.settings, self.database).build(force=force_index)
        return {
            "migrations": migrations,
            "routes": route_counts,
            "index": manifest,
            "foundry": FoundryAdapter(self.settings).health(),
            "foundry_cli": FoundryAdapter.cli_status(),
            "interview": interview_seed,
        }

    def _documents(self):
        parser = DocumentParser(
            self.settings.chunk_max_chars,
            self.settings.chunk_overlap_paragraphs,
        )
        return parser.parse_all(self.settings.source_paths)

    def recommend_route(self, goal: str, skills: dict[str, str]) -> dict[str, str]:
        text = (goal + " " + " ".join(skills)).casefold()
        scores = {"data-analysis": 0, "machine-learning": 0, "quantum": 0}
        keyword_map = {
            "data-analysis": ("pandas", "titanic", "veri analizi", "eda", "jupyter", "görselleştirme"),
            "machine-learning": ("machine learning", "makine öğren", "pytorch", "lstm", "gru", "tahmin"),
            "quantum": ("kuantum", "quantum", "q#", "qubit", "grover"),
        }
        for route, keywords in keyword_map.items():
            scores[route] = sum(text.count(keyword) for keyword in keywords)
        route = max(scores, key=scores.get)
        if scores[route] == 0:
            route = "data-analysis"
            reason = "Hedefte belirgin bir rota anahtar sözcüğü bulunmadığı için temel Python/veri analizi rotası önerildi."
        else:
            reason = "Öneri yalnızca yazdığınız hedef ve beyan ettiğiniz beceri alanlarındaki eşleşmelere dayanır."
        title = next(title for slug, title, _ in DOCUMENT_DEFINITIONS if slug == route)
        return {"route": route, "title": title, "reason": reason}

    def create_profile(self, data: dict[str, Any], skills: dict[str, str]) -> int:
        valid_routes = {item[0] for item in DOCUMENT_DEFINITIONS} | {"unassigned"}
        data = {**data, "preferred_route": data.get("preferred_route") or "unassigned"}
        if data["preferred_route"] not in valid_routes:
            raise ValueError("Geçersiz rota seçimi.")
        if date.fromisoformat(data["target_end_date"]) < date.fromisoformat(data["start_date"]):
            raise ValueError("Hedef bitiş tarihi başlangıç tarihinden önce olamaz.")
        return self.database.create_profile(data, skills)

    def profile(self, profile_id: int | None = None) -> dict[str, Any]:
        profile = self.database.get_profile(profile_id)
        if not profile:
            raise DatabaseError("Henüz profil yok. Önce 'python cli.py profile create' komutunu çalıştırın.")
        return profile

    def generate_plan(self, profile_id: int | None = None) -> dict[str, Any]:
        profile = self.profile(profile_id)
        custom = self.database.custom_route(profile["preferred_route"])
        if custom:
            return self.custom_routes.install_route(custom["id"], profile["id"])
        document = next((doc for doc in self._documents() if doc.route == profile["preferred_route"]), None)
        if document is None:
            raise ValueError(f"Rota belgesi bulunamadı: {profile['preferred_route']}")
        source_tasks = extract_source_tasks(document)
        result = self.planner.generate(profile, source_tasks)
        count = self.database.replace_profile_plan(profile["id"], result.tasks)
        return {
            "task_count": count,
            "source_task_count": len(source_tasks),
            "warnings": result.warnings,
            "projected_end_date": result.projected_end_date,
            "accelerated_suggestion": result.accelerated_suggestion,
        }

    def upload_document(self, filename: str, data: bytes) -> dict[str, Any]:
        self.database.migrate()
        return self.uploads.upload(filename, data)

    def uploaded_documents(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        return self.database.list_source_documents(origin="uploaded", include_archived=include_archived)

    def archive_uploaded_document(self, document_id: str, confirmation: str) -> dict[str, Any]:
        return self.uploads.archive(document_id, confirmation)

    def create_custom_route_draft(
        self,
        document_id: str,
        profile_id: int,
        configuration: dict[str, Any],
    ) -> dict[str, Any]:
        return self.custom_routes.create_draft(document_id, profile_id, configuration)

    def analyze_sources(
        self, profile_id: int, source_ids: list[str], configuration: dict[str, Any]
    ) -> dict[str, Any]:
        route_id = "custom-" + __import__("hashlib").sha256(
            ("|".join(source_ids) + "|" + str(configuration["goal"]) + "|" + str(profile_id)).encode("utf-8")
        ).hexdigest()[:20]
        return self.sufficiency.analyze(
            route_id=route_id,
            profile_id=profile_id,
            goal=str(configuration["goal"]),
            source_ids=source_ids,
            declared_level=str(configuration["declared_level"]),
            daily_minutes=int(configuration["daily_minutes"]),
            start_date=str(configuration["start_date"]),
            target_end_date=str(configuration["target_end_date"]),
        )

    def approve_sufficiency(self, report_id: str) -> None:
        self.database.approve_sufficiency_report(report_id)

    def create_multi_source_route_draft(
        self,
        source_ids: list[str],
        profile_id: int,
        configuration: dict[str, Any],
        sufficiency_report_id: str,
    ) -> dict[str, Any]:
        report = self.database.sufficiency_report(sufficiency_report_id)
        if not report:
            raise ValueError("Kaynak yeterlilik raporu bulunamadı.")
        return self.custom_routes.create_draft_multi(
            source_ids,
            profile_id,
            configuration,
            sufficiency_report_id=sufficiency_report_id,
            route_id=report["route_id"],
        )

    def update_custom_route_tasks(self, route_id: str, edits: list[dict[str, Any]]) -> int:
        return self.database.update_custom_route_tasks(route_id, edits)

    def install_custom_route(self, route_id: str, profile_id: int) -> dict[str, Any]:
        return self.custom_routes.install_route(route_id, profile_id)

    def list_custom_routes(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        return self.database.list_custom_routes(include_archived=include_archived)

    def custom_route_tasks(self, route_id: str) -> list[dict[str, Any]]:
        return self.database.custom_route_tasks(route_id)

    def duplicate_custom_route_task(
        self,
        route_id: str,
        task_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        return self.database.duplicate_custom_route_task(
            route_id, task_id, title=title, description=description,
        )

    def create_route_version(self, route_id: str) -> str:
        return self.database.create_custom_route_version(route_id)

    def route_sources(self, route_id: str) -> list[dict[str, Any]]:
        return self.database.route_sources(route_id)

    def all_sources(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        return self.database.list_source_documents(include_archived=include_archived)

    def add_web_url(self, url: str, *, title: str = "", description: str = "") -> dict[str, Any]:
        return self.web_sources.add_url(url, title=title, description=description)

    def discover_web_sources(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        return self.web_sources.search(query, limit)

    def approve_web_source(self, source_id: str) -> dict[str, Any]:
        return self.web_sources.approve(source_id)

    def list_web_sources(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        return self.database.list_web_sources(include_archived=include_archived)

    def create_custom_quiz(self, route_id: str, source_ids: list[str]) -> list[dict[str, Any]]:
        return self.custom_quiz_engine.generate(route_id, source_ids)

    def improve_custom_route_with_foundry(self, route_id: str) -> list[dict[str, Any]]:
        route = self.database.custom_route(route_id)
        if not route or route["status"] != "draft":
            raise ValueError("Foundry Local yalnızca onaylanmamış rota taslağını iyileştirebilir.")
        tasks = self.database.custom_route_tasks(route_id)
        if not tasks:
            raise ValueError("İyileştirilecek rota görevi bulunamadı.")
        source_ids = [item["source_id"] for item in self.database.route_sources(route_id)]
        chunks_by_id = {item["id"]: item for item in self.database.source_chunks(source_ids)}
        task_payload: list[dict[str, Any]] = []
        context_chunks: dict[str, dict[str, Any]] = {}
        for task in tasks:
            references = self.database.task_sources(task["id"], "draft")
            chunk_ids = [item["chunk_id"] for item in references if item["chunk_id"] in chunks_by_id]
            if not chunk_ids:
                raise ValueError(f"Kaynak chunk bağı olmayan görev Foundry'ye gönderilmedi: {task['title']}")
            for chunk_id in chunk_ids:
                chunk = chunks_by_id[chunk_id]
                context_chunks[chunk_id] = {
                    "chunk_id": chunk_id,
                    "source_id": chunk["document_id"],
                    "section": chunk["section_path"],
                    "page": chunk.get("page"),
                    "url": chunk.get("source_url"),
                    "text": f"{chunk['heading']}\n{chunk['body']}"[:1200],
                }
            task_payload.append({
                "task_id": task["id"],
                "title": task["title"],
                "description": task["description"],
                "task_type": task["task_type"],
                "module": task.get("module"),
                "estimated_minutes": task["estimated_minutes"],
                "position": task["position"],
                "prerequisite_task_ids": task.get("prerequisite_task_ids", []),
                "source_chunk_ids": chunk_ids,
            })
        source_json = json.dumps(list(context_chunks.values()), ensure_ascii=False)
        source_json = source_json[: self.settings.max_context_chars]
        response = FoundryAdapter(self.settings).structured_json(
            [
                {
                    "role": "system",
                    "content": (
                        "Yalnız verilen kaynakları kullan. JSON dışında yanıt verme. Görev kimliklerini ve "
                        "source_chunk_ids alanlarını değiştirme; kaynakta olmayan bilgiyi ekleme."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Bu rota taslağının anlatımını, sırasını ve ön koşullarını kaynaklara bağlı kalarak "
                        "iyileştir. Şema: {\"tasks\":[{\"task_id\":str,\"title\":str,\"description\":str,"
                        "\"task_type\":str,\"module\":str|null,\"estimated_minutes\":int,\"position\":int,"
                        "\"prerequisite_task_ids\":[str],\"source_chunk_ids\":[str]}]}.\n"
                        f"MEVCUT_GÖREVLER={json.dumps(task_payload, ensure_ascii=False)}\nKAYNAKLAR={source_json}"
                    ),
                },
            ],
            required_keys={"tasks"},
            max_tokens=2200,
        )
        proposed = response.get("tasks")
        if not isinstance(proposed, list) or len(proposed) != len(tasks):
            raise ValueError("Foundry Local rota çıktısı tüm görevleri tam olarak içermiyor; taslak değiştirilmedi.")
        existing = {item["id"]: item for item in tasks}
        validated: list[dict[str, Any]] = []
        seen: set[str] = set()
        allowed_types = {"teori", "uygulama", "tekrar", "değerlendirme"}
        for item in proposed:
            if not isinstance(item, dict):
                raise ValueError("Foundry Local görev şeması geçersiz; taslak değiştirilmedi.")
            task_id = str(item.get("task_id", ""))
            if task_id not in existing or task_id in seen:
                raise ValueError("Foundry Local görev kimliklerini değiştirdi; taslak değiştirilmedi.")
            seen.add(task_id)
            current = existing[task_id]
            allowed_chunks = {ref["chunk_id"] for ref in self.database.task_sources(task_id, "draft")}
            returned_chunks = item.get("source_chunk_ids")
            if not isinstance(returned_chunks, list) or set(map(str, returned_chunks)) != allowed_chunks:
                raise ValueError("Foundry Local kaynak chunk bağını değiştirdi; taslak değiştirilmedi.")
            title = str(item.get("title", "")).strip()
            description = str(item.get("description", "")).strip()
            task_type = str(item.get("task_type", ""))
            minutes = int(item.get("estimated_minutes", 0))
            position = int(item.get("position", 0))
            prerequisites = item.get("prerequisite_task_ids")
            if (
                not title or not description or task_type not in allowed_types
                or not 10 <= minutes <= 720 or position < 1 or not isinstance(prerequisites, list)
            ):
                raise ValueError("Foundry Local görev alanları şemaya uymuyor; taslak değiştirilmedi.")
            validated.append({
                **current,
                "title": title,
                "description": description,
                "task_type": task_type,
                "module": str(item.get("module") or "").strip() or None,
                "estimated_minutes": minutes,
                "position": position,
                "prerequisite_task_ids": [str(value) for value in prerequisites],
            })
        self.database.update_custom_route_tasks(route_id, validated)
        return self.database.custom_route_tasks(route_id)

    def create_custom_quiz_with_foundry(
        self, route_id: str, source_ids: list[str], limit: int = 8,
    ) -> list[dict[str, Any]]:
        chunks = [item for item in self.database.source_chunks(source_ids) if len(item["body"]) >= 20][:limit]
        if not chunks:
            raise ValueError("Foundry Local sınavı için kaynak chunk bulunamadı.")
        context = [
            {
                "chunk_id": item["id"], "source_id": item["document_id"],
                "section": item["section_path"], "page": item.get("page"),
                "url": item.get("source_url"), "text": f"{item['heading']}\n{item['body']}"[:1400],
            }
            for item in chunks
        ]
        response = FoundryAdapter(self.settings).structured_json(
            [
                {
                    "role": "system",
                    "content": (
                        "Yalnız verilen kaynak metninden, cevabı metinde kelimesi kelimesine bulunan sorular üret. "
                        "JSON dışında yanıt verme; genel bilgi kullanma."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Şema: {\"questions\":[{\"question\":str,\"options\":[str],\"correct_answer\":str,"
                        "\"explanation\":str,\"difficulty\":str,\"topic\":str,\"source_id\":str,"
                        "\"chunk_id\":str}]}. Her soruda 3-4 benzersiz seçenek kullan.\nKAYNAKLAR="
                        + json.dumps(context, ensure_ascii=False)[: self.settings.max_context_chars]
                    ),
                },
            ],
            required_keys={"questions"},
            max_tokens=1800,
        )
        proposed = response.get("questions")
        if not isinstance(proposed, list) or not proposed or len(proposed) > limit:
            raise ValueError("Foundry Local sınav çıktısı geçersiz; mevcut sınav değiştirilmedi.")
        chunks_by_id = {item["id"]: item for item in chunks}
        questions: list[dict[str, Any]] = []
        import hashlib
        for index, item in enumerate(proposed):
            if not isinstance(item, dict):
                raise ValueError("Foundry Local soru şeması geçersiz; mevcut sınav değiştirilmedi.")
            chunk_id = str(item.get("chunk_id", ""))
            chunk = chunks_by_id.get(chunk_id)
            source_id = str(item.get("source_id", ""))
            options = item.get("options")
            correct = str(item.get("correct_answer", "")).strip()
            question_text = str(item.get("question", "")).strip()
            if (
                not chunk or source_id != chunk["document_id"] or not question_text
                or not isinstance(options, list) or not 3 <= len(options) <= 4
                or len({str(value).strip() for value in options}) != len(options)
                or correct not in [str(value).strip() for value in options]
                or correct.casefold() not in f"{chunk['heading']}\n{chunk['body']}".casefold()
            ):
                raise ValueError("Foundry Local sorusu kaynak kanıtı doğrulamasını geçemedi; sınav değiştirilmedi.")
            question_id = "quiz-fl-" + hashlib.sha256(
                f"{route_id}|{chunk_id}|{index}|{question_text}".encode("utf-8")
            ).hexdigest()[:20]
            questions.append({
                "id": question_id,
                "question": question_text,
                "options": [str(value).strip() for value in options],
                "correct_answer": correct,
                "explanation": str(item.get("explanation", "")).strip() or "Doğru cevap kaynak chunk içinde açıkça yer alır.",
                "difficulty": str(item.get("difficulty", "temel")).strip(),
                "topic": str(item.get("topic", correct)).strip(),
                "source_id": source_id,
                "chunk_id": chunk_id,
                "source_locator": chunk.get("source_url") or chunk["section_path"],
                "generation_method": "foundry-local-kaynak-bağlı",
            })
        self.database.save_custom_quiz(route_id, questions)
        return questions

    def custom_quiz(self, route_id: str) -> list[dict[str, Any]]:
        return self.database.custom_quiz(route_id)

    def grade_custom_quiz(self, profile_id: int, route_id: str, answers: dict[str, str]) -> dict[str, Any]:
        return self.custom_quiz_engine.grade(profile_id, route_id, answers)

    def skip_custom_quiz(self, profile_id: int, route_id: str) -> dict[str, Any]:
        return self.custom_quiz_engine.skip(profile_id, route_id)

    def today(self, profile_id: int | None = None, value: date | None = None) -> list[dict[str, Any]]:
        profile = self.profile(profile_id)
        return self.database.tasks(profile["id"], scheduled_date=(value or date.today()).isoformat())

    def weekly_plan(self, profile_id: int | None = None, start: date | None = None) -> list[dict[str, Any]]:
        profile = self.profile(profile_id)
        start_date = start or (date.today() - timedelta(days=date.today().weekday()))
        end_date = start_date + timedelta(days=6)
        return [
            task
            for task in self.database.tasks(profile["id"])
            if task.get("scheduled_date") and start_date <= date.fromisoformat(task["scheduled_date"]) <= end_date
        ]

    def start_task(self, task_id: str) -> dict[str, Any]:
        return self.database.transition_task(task_id, "in_progress")

    def complete_task(
        self,
        task_id: str,
        minutes: int,
        difficulty: int,
        *,
        quiz_score: float | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        task = self.database.transition_task(
            task_id,
            "completed",
            actual_minutes=minutes,
            difficulty_rating=difficulty,
            quiz_score=quiz_score,
            note=note,
        )
        review = None
        if (quiz_score is not None and quiz_score < 60) or difficulty >= 4:
            profile = self.profile(task["profile_id"])
            next_day = self.planner._next_study_day(
                date.today() + timedelta(days=1),
                self.planner._weekdays(profile["preferred_days"], profile["weekly_days"]),
            )
            review = self.database.insert_review_task(task, next_day.isoformat())
        task["adaptive_decisions"] = self.adaptive.record_after_completion(task)
        task["review_task"] = review
        return task

    def postpone_task(self, task_id: str) -> dict[str, Any]:
        task = self.database.transition_task(task_id, "postponed")
        profile = self.profile(task["profile_id"])
        all_tasks = self.database.tasks(profile["id"])
        schedule = self.planner.reschedule_open_tasks(profile, all_tasks, date.today() + timedelta(days=1))
        for open_task_id, new_date in schedule.items():
            self.database.reschedule(open_task_id, new_date)
        updated = self.database.get_task(task_id)
        assert updated is not None
        return updated

    def review_task(self, task_id: str) -> dict[str, Any]:
        task = self.database.get_task(task_id)
        if not task:
            raise DatabaseError(f"Görev bulunamadı: {task_id}")
        if task["status"] == "completed":
            profile = self.profile(task["profile_id"])
            next_day = self.planner._next_study_day(
                date.today() + timedelta(days=1),
                self.planner._weekdays(profile["preferred_days"], profile["weekly_days"]),
            )
            return self.database.insert_review_task(task, next_day.isoformat())
        return self.database.transition_task(task_id, "needs_review")

    def progress(self, profile_id: int | None = None) -> dict[str, Any]:
        profile = self.profile(profile_id)
        return self.database.progress_summary(profile["id"])

    def weekly_report(self, profile_id: int | None = None) -> dict[str, Any]:
        profile = self.profile(profile_id)
        week_start = date.today() - timedelta(days=date.today().weekday())
        tasks = self.weekly_plan(profile["id"], week_start)
        completed = [task for task in tasks if task["status"] == "completed"]
        minutes = sum(task.get("actual_minutes") or 0 for task in completed)
        quiz_scores = [task["quiz_score"] for task in completed if task.get("quiz_score") is not None]
        avg_quiz = round(sum(quiz_scores) / len(quiz_scores), 1) if quiz_scores else None
        recommendations: list[str] = []
        if any(task.get("needs_review") for task in tasks):
            recommendations.append("Tekrar işaretli konuları sonraki uygun çalışma gününde ele alın.")
        if avg_quiz is not None and avg_quiz < 60:
            recommendations.append("Mini sınav ortalaması düşük olduğu için kaynak görevlerden tekrar oturumu önerildi.")
        if avg_quiz is not None and avg_quiz >= 85 and len(completed) >= 2:
            recommendations.append("Yüksek başarı kanıtı var; kullanıcı onayıyla hızlandırılmış tempo değerlendirilebilir.")
        report = {
            "week_start": week_start.isoformat(),
            "planned_tasks": len(tasks),
            "completed_tasks": len(completed),
            "completion_percentage": round(len(completed) / len(tasks) * 100, 1) if tasks else 0.0,
            "actual_minutes": minutes,
            "average_quiz_score": avg_quiz,
            "recommendations": recommendations,
        }
        with self.database.connection() as conn:
            conn.execute(
                """INSERT INTO weekly_reviews(profile_id, week_start, summary_json, created_at)
                   VALUES (?, ?, ?, datetime('now'))
                   ON CONFLICT(profile_id, week_start) DO UPDATE SET summary_json=excluded.summary_json,
                       created_at=excluded.created_at""",
                (profile["id"], week_start.isoformat(), json.dumps(report, ensure_ascii=False)),
            )
        return report

    def route_comparison(self) -> list[dict[str, Any]]:
        documents = self._documents()
        return [
            {
                "route": doc.route,
                "title": doc.route_title,
                "source_document": doc.filename,
                "source_task_count": len(extract_source_tasks(doc)),
                "chunk_count": len(doc.chunks),
                "topics": list(dict.fromkeys(chunk.heading for chunk in doc.chunks))[:6],
                "phases": sorted({chunk.phase for chunk in doc.chunks if chunk.phase}, key=int),
                "weeks": sorted({chunk.week for chunk in doc.chunks if chunk.week}, key=int),
                "days": sorted({chunk.day for chunk in doc.chunks if chunk.day}, key=lambda value: int(value.split("-")[0])),
            }
            for doc in documents
        ]

    def dashboard(self, profile_id: int | None = None) -> dict[str, Any]:
        profile = self.profile(profile_id)
        tasks = self.database.tasks(profile["id"])
        sources = self.database.route_sources(profile["preferred_route"])
        if not sources and profile["preferred_route"] in {item[0] for item in DOCUMENT_DEFINITIONS}:
            document = next((item for item in self.database.list_source_documents() if item["route"] == profile["preferred_route"]), None)
            sources = [document] if document else []
        today_value = date.today().isoformat()
        today_tasks = [item for item in tasks if item.get("scheduled_date") == today_value]
        upcoming = [
            item for item in tasks
            if item.get("scheduled_date") and item["scheduled_date"] > today_value and item["status"] != "completed"
        ][:5]
        source_counts = {
            "local": sum((item.get("source_kind") or "document") != "web" for item in sources),
            "web": sum(item.get("source_kind") == "web" for item in sources),
        }
        return {
            "profile": profile,
            "progress": self.database.progress_summary(profile["id"]),
            "countdown": goal_countdown(profile, tasks),
            "sources": sources,
            "source_counts": source_counts,
            "today": today_tasks,
            "upcoming": upcoming,
            "review_topics": [item for item in tasks if item.get("needs_review")][:8],
            "adaptive_decisions": self.database.adaptive_decisions(profile["id"])[:5],
        }

    def analytics(self, profile_id: int | None = None) -> dict[str, list[dict[str, Any]]]:
        profile = self.profile(profile_id)
        return self.database.analytics(profile["id"])

    def focus_action(self, action: str, profile_id: int | None = None, task_id: str | None = None) -> dict[str, Any] | None:
        profile = self.profile(profile_id)
        return self.database.focus_action(profile["id"], action, task_id)

    def focus_session(self, profile_id: int | None = None) -> dict[str, Any] | None:
        profile = self.profile(profile_id)
        return self.database.focus_session(profile["id"])

    def apply_focus_duration(self, task_id: str, seconds: int) -> dict[str, Any]:
        return self.database.apply_focus_duration(task_id, seconds)

    def select_ready_route(self, profile_id: int, route_id: str) -> dict[str, Any]:
        valid = {item[0] for item in DOCUMENT_DEFINITIONS}
        if route_id not in valid:
            raise ValueError("Hazır rota bulunamadı.")
        with self.database.connection() as conn:
            conn.execute(
                """UPDATE profiles SET preferred_route=?, source_method='Hazır rotalardan seç',
                       measured_level=NULL, assessment_status='user_declared', updated_at=datetime('now')
                   WHERE id=?""",
                (route_id, profile_id),
            )
        return self.generate_plan(profile_id)

    def retrieve(self, query: str, **filters: Any):
        route = filters.get("route")
        if route:
            sources = self.database.route_sources(str(route))
            if sources:
                filters = {**filters, "route": None, "document_ids": [item["source_id"] for item in sources]}
        return SearchBackendRouter(self.settings).search(query, **filters)

    def ask(self, question: str, profile_id: int | None = None, **filters: Any):
        profile = self.database.get_profile(profile_id)
        today_tasks = self.today(profile["id"]) if profile else []
        self.database.save_chat(profile["id"] if profile else None, "user", question, [])
        results = self.retrieve(question, **filters)
        stream, results = RAGAnswerer(self.settings).stream_answer(
            question, profile=profile, today_tasks=today_tasks, retrieval_results=results, **filters
        )

        def recording_stream():
            parts: list[str] = []
            for text in stream:
                parts.append(text)
                yield text
            citations = [
                {"source": result.chunk["source"], "section_path": result.chunk["section_path"], "chunk_id": result.chunk["id"]}
                for result in results
            ]
            self.database.save_chat(profile["id"] if profile else None, "assistant", "".join(parts), citations)

        return recording_stream(), results

    def index_status(self) -> dict[str, Any]:
        return IndexManager(self.settings).status()

    def build_index(self, force: bool = False) -> dict[str, Any]:
        self.database.migrate()
        return IndexManager(self.settings, self.database).build(force=force)

    def search_backend_status(self) -> dict[str, Any]:
        return SearchBackendRouter(self.settings).status()

    def select_search_backend(
        self,
        backend: str,
        *,
        confirmed_fallback: bool = False,
        approved_local_indexing: bool = False,
    ) -> dict[str, Any]:
        if backend not in {"local", "opensearch"}:
            raise ValueError("Arama backend'i local veya opensearch olmalıdır.")
        if self.settings.search_backend == "opensearch" and backend == "local" and not confirmed_fallback:
            raise ValueError("OpenSearch'ten yerel backende dönüş kullanıcı onayı gerektirir.")
        candidate = replace(self.settings, search_backend=backend)
        if backend == "opensearch":
            if not approved_local_indexing:
                raise ValueError("Onaylanmış yerel kaynakların localhost OpenSearch'e indekslenmesi açık onay gerektirir.")
            adapter = OpenSearchBackend(candidate)
            status = adapter.status()
            if not status.get("available"):
                raise ValueError(status.get("message", "Yerel OpenSearch kullanılamıyor."))
            chunks, _, _ = IndexManager(self.settings, self.database).load()
            adapter.index_chunks(chunks)
        self.database.set_setting("search_backend", backend)
        self.settings = candidate
        self.interview = InterviewCoach(self.database, self.settings)
        return self.search_backend_status()

    def sync_opensearch(self, *, approved_local_indexing: bool = False) -> dict[str, Any]:
        if self.settings.search_backend != "opensearch":
            raise ValueError("Önce OpenSearch backend'ini seçin.")
        if not approved_local_indexing:
            raise ValueError("OpenSearch indeks eşitlemesi açık kullanıcı onayı gerektirir.")
        adapter = OpenSearchBackend(self.settings)
        chunks, _, _ = IndexManager(self.settings, self.database).load()
        return {"indexed_chunks": adapter.index_chunks(chunks), **adapter.status()}

    def configure_foundry(self, endpoint: str, model: str) -> dict[str, Any]:
        candidate = replace(self.settings, foundry_endpoint=endpoint.rstrip("/"), foundry_model=model.strip())
        adapter = FoundryAdapter(candidate)
        status = adapter.health()
        self.database.set_setting("foundry_endpoint", candidate.foundry_endpoint)
        self.database.set_setting("foundry_model", candidate.foundry_model)
        self.settings = candidate
        self.interview = InterviewCoach(self.database, self.settings)
        return status

    def foundry_status(self) -> dict[str, Any]:
        return FoundryAdapter(self.settings).health()
