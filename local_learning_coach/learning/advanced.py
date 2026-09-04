from __future__ import annotations

import re
from collections import Counter
from datetime import date
from typing import Any

from local_learning_coach.database import Database
from local_learning_coach.documents.parser import clean_text, stable_hash


class LearningDesignError(ValueError):
    """Kullanıcıya gösterilebilir rota/sınav tasarım hatası."""


STOPWORDS = {
    "ve", "veya", "ile", "için", "bir", "bu", "şu", "the", "and", "for", "from", "how", "nasıl",
    "öğrenmek", "öğrenme", "konusunda", "hakkında", "istiyorum", "etmek", "olan", "olarak",
}


def meaningful_terms(value: str) -> list[str]:
    words = re.findall(r"[a-zçğıöşü0-9#+.-]{3,}", clean_text(value).casefold())
    return [word for word in words if word not in STOPWORDS and not word.isdigit()]


def validate_prerequisite_graph(tasks: list[dict[str, Any]]) -> None:
    ids = {str(item["id"]) for item in tasks}
    graph: dict[str, list[str]] = {}
    for item in tasks:
        task_id = str(item["id"])
        prerequisites = [str(value) for value in item.get("prerequisite_task_ids", [])]
        if task_id in prerequisites:
            raise LearningDesignError("Bir görev kendisinin ön koşulu olamaz.")
        if any(value not in ids for value in prerequisites):
            raise LearningDesignError("Ön koşul rota dışındaki bir göreve bağlanamaz.")
        graph[task_id] = prerequisites
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            raise LearningDesignError("Ön koşul grafiğinde döngü tespit edildi.")
        if node in visited:
            return
        visiting.add(node)
        for dependency in graph.get(node, []):
            visit(dependency)
        visiting.remove(node)
        visited.add(node)

    for task_id in graph:
        visit(task_id)


class SufficiencyAnalyzer:
    def __init__(self, database: Database):
        self.database = database

    def analyze(
        self,
        *,
        route_id: str,
        profile_id: int | None,
        goal: str,
        source_ids: list[str],
        declared_level: str,
        daily_minutes: int,
        start_date: str,
        target_end_date: str,
    ) -> dict[str, Any]:
        chunks = self.database.source_chunks(source_ids)
        documents = [self.database.source_document(source_id) for source_id in source_ids]
        documents = [item for item in documents if item]
        if not chunks:
            raise LearningDesignError("Seçilen kaynaklarda analiz edilebilir etkin içerik bulunamadı.")
        corpus = " ".join(f"{item['heading']} {item['body']}" for item in chunks).casefold()
        goal_terms = list(dict.fromkeys(meaningful_terms(goal)))
        found_terms = [term for term in goal_terms if term in corpus]
        missing = [term for term in goal_terms if term not in corpus]
        topic_coverage = len(found_terms) / max(1, len(goal_terms))
        headings = [clean_text(item["heading"]) for item in chunks if clean_text(item["heading"])]
        topic_counts = Counter(headings)
        repeated = [topic for topic, count in topic_counts.items() if count > 1]
        by_heading: dict[str, list[dict[str, Any]]] = {}
        for chunk in chunks:
            by_heading.setdefault(clean_text(chunk["heading"]).casefold(), []).append(chunk)
        conflicts: list[dict[str, Any]] = []
        negative_markers = (" değildir", " yapılmaz", " kullanılmaz", " not ", " does not ", " never ")
        for heading, group in by_heading.items():
            source_group = {item["document_id"] for item in group}
            signs = {any(marker in f" {item['body'].casefold()} " for marker in negative_markers) for item in group}
            if len(source_group) > 1 and len(signs) > 1:
                conflicts.append({
                    "id": "conflict-" + stable_hash(route_id, heading, length=20),
                    "topic": clean_text(group[0]["heading"]),
                    "source_ids": sorted(source_group),
                    "explanation": "Aynı başlık altında olumlu ve olumsuz ifade örüntüleri bulundu; kaynaklar sessizce üstün sıralanmadı.",
                })
        practical_markers = ("uygula", "alıştır", "örnek", "proje", "kod", "exercise", "practice", "project", "example")
        assessment_markers = ("sınav", "quiz", "test", "değerlendir", "assessment", "question")
        practice_ratio = sum(marker in corpus for marker in practical_markers) / len(practical_markers)
        assessment_ratio = sum(marker in corpus for marker in assessment_markers) / len(assessment_markers)
        example_ratio = min(1.0, (corpus.count("örnek") + corpus.count("example")) / 4)
        structured = any(item.get("week") or item.get("day") or item.get("phase") for item in chunks)
        kinds = Counter(str(item.get("source_kind") or "document") for item in documents)
        web_reliability: list[str] = []
        for source in self.database.list_web_sources(include_archived=False):
            if source.get("document_id") in source_ids:
                web_reliability.append(source["reliability_class"])
        high_reliability = sum(value.startswith("yüksek") for value in web_reliability)
        reliability_score = 1.0 if not web_reliability else (high_reliability + 0.5 * (len(web_reliability) - high_reliability)) / len(web_reliability)
        diversity_score = min(1.0, len(documents) / 3 + (0.2 if len(kinds) > 1 else 0))
        coverage_score = round(
            35 * topic_coverage
            + 15 * (1.0 if structured else min(1.0, len(headings) / 6))
            + 15 * min(1.0, practice_ratio * 2)
            + 10 * example_ratio
            + 10 * min(1.0, assessment_ratio * 2)
            + 10 * reliability_score
            + 5 * diversity_score,
            1,
        )
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(target_end_date)
        available_minutes = max(1, (end - start).days + 1) * max(10, daily_minutes)
        estimated_minutes = max(30, len(chunks) * 35)
        duration_fit = "uygun" if estimated_minutes <= available_minutes else "riskli"
        high_risk = any(term in corpus or term in goal.casefold() for term in ("sağlık", "hukuk", "finans", "medical", "legal"))
        has_primary = any(value.startswith("yüksek") for value in web_reliability)
        can_generate = coverage_score >= 35 and (not high_risk or has_primary)
        prerequisites: list[str] = []
        if declared_level.casefold() in {"başlangıç", "yok"}:
            foundation_markers = ("temel", "giriş", "başlangıç", "foundation", "introduction")
            if not any(marker in corpus for marker in foundation_markers):
                prerequisites.append("Başlangıç düzeyi temel kavram kaynağı")
        report_id = "suff-" + stable_hash(route_id, goal, "|".join(source_ids), length=20)
        report = {
            "id": report_id,
            "route_id": route_id,
            "profile_id": profile_id,
            "goal": goal,
            "source_ids": source_ids,
            "found_topics": headings[:30],
            "found_goal_terms": found_terms,
            "missing_topics": missing,
            "prerequisites": prerequisites,
            "practice_coverage": round(min(1.0, practice_ratio * 2) * 100, 1),
            "example_coverage": round(example_ratio * 100, 1),
            "assessment_coverage": round(min(1.0, assessment_ratio * 2) * 100, 1),
            "beginner_fit": "uygun" if not prerequisites else "ön koşul kaynağı önerilir",
            "duration_fit": duration_fit,
            "currency": "tarih bilgisi yoksa kullanıcı doğrulaması gerekir",
            "source_diversity": dict(kinds),
            "reliability_distribution": dict(Counter(web_reliability)) or {"yerel kullanıcı/hazır belgesi": len(documents)},
            "repeated_topics": repeated[:20],
            "conflicts": conflicts,
            "coverage_score": coverage_score,
            "score_method": "Hedef terimleri %35, yapı %15, uygulama %15, örnek %10, değerlendirme %10, güvenilirlik %10, çeşitlilik %5.",
            "confidence_explanation": (
                f"{len(chunks)} chunk ve {len(documents)} kaynak deterministik kurallarla incelendi; "
                "kaynağın doğruluğu garanti edilmez."
            ),
            "can_generate": can_generate,
            "high_risk_topic": high_risk,
            "warning": (
                "Yüksek riskli konuda resmî/birincil kaynak yetersiz; profesyonel danışmanlık yerine geçmez."
                if high_risk and not has_primary else None
            ),
            "approved": False,
        }
        self.database.save_sufficiency_report(report)
        self.database.replace_source_conflicts(route_id, conflicts)
        return report


class SourceBoundQuizEngine:
    def __init__(self, database: Database):
        self.database = database

    def generate(self, route_id: str, source_ids: list[str], limit: int = 8) -> list[dict[str, Any]]:
        chunks = [item for item in self.database.source_chunks(source_ids) if len(item["body"]) >= 20]
        questions: list[dict[str, Any]] = []
        headings = list(dict.fromkeys(clean_text(item["heading"]) for item in chunks if clean_text(item["heading"])))
        excerpts = list(dict.fromkeys(clean_text(item["body"]).split(". ")[0][:180] for item in chunks))
        if len(chunks) < 2:
            return []
        for index, chunk in enumerate(chunks[:limit]):
            answer = clean_text(chunk["heading"])
            alternatives = [value for value in headings if value != answer][:3]
            question_text = "Aşağıdaki başlıklardan hangisi belirtilen kaynak bölümünün başlığıdır?"
            if not answer or not alternatives:
                answer = clean_text(chunk["body"]).split(". ")[0][:180]
                alternatives = [value for value in excerpts if value != answer][:3]
                question_text = "Aşağıdaki ifadelerden hangisi belirtilen kaynak bölümünde açıkça yer alır?"
            if not answer or not alternatives:
                continue
            options = [answer, *alternatives]
            shift = index % len(options)
            options = options[shift:] + options[:shift]
            source_locator = chunk.get("source_url") or chunk["section_path"]
            question = {
                "id": "quiz-" + stable_hash(route_id, chunk["id"], length=20),
                "question": question_text,
                "options": options,
                "correct_answer": answer,
                "explanation": f"Doğru başlık kaynakta aynen yer alır: {answer}",
                "difficulty": "temel" if index < 3 else "orta",
                "topic": answer,
                "source_id": chunk["document_id"],
                "chunk_id": chunk["id"],
                "source_locator": source_locator,
                "generation_method": "deterministik-kaynak-bağlı",
            }
            questions.append(question)
        self.database.save_custom_quiz(route_id, questions)
        return questions

    def grade(self, profile_id: int, route_id: str, answers: dict[str, str]) -> dict[str, Any]:
        questions = self.database.custom_quiz(route_id)
        if not questions or any(item["id"] not in answers for item in questions):
            raise LearningDesignError("Sınav bulunamadı veya tüm sorular yanıtlanmadı.")
        correct = sum(answers[item["id"]] == item["correct_answer"] for item in questions)
        score = round(correct / len(questions) * 100, 1)
        self.database.save_custom_quiz_attempt(profile_id, route_id, answers, score, False)
        return {"score": score, "correct": correct, "total": len(questions)}

    def skip(self, profile_id: int, route_id: str) -> dict[str, Any]:
        attempt_id = self.database.save_custom_quiz_attempt(profile_id, route_id, {}, None, True)
        return {"attempt_id": attempt_id, "skipped": True, "measured_level": None}


class AdaptivePlanner:
    def __init__(self, database: Database):
        self.database = database

    def record_after_completion(self, task: dict[str, Any]) -> list[dict[str, Any]]:
        decisions: list[dict[str, Any]] = []
        quiz_score = task.get("quiz_score")
        difficulty = task.get("difficulty_rating")
        actual = int(task.get("actual_minutes") or 0)
        estimated = max(1, int(task.get("estimated_minutes") or 1))
        if quiz_score is not None and quiz_score < 60:
            decisions.append({"type": "source_review", "reason": "Mini sınav puanı 60'ın altında.", "applied": True})
        if difficulty is not None and difficulty >= 4:
            decisions.append({"type": "difficulty_review", "reason": "Kullanıcı zorluğu 4/5 veya 5/5 bildirdi.", "applied": True})
        if actual > estimated * 1.5:
            decisions.append({"type": "duration_adjustment_suggestion", "reason": "Gerçek süre tahminin %50 üzerinde; yalnızca açık görevler için süre artışı önerilir.", "applied": False})
        if quiz_score is not None and quiz_score >= 85:
            decisions.append({"type": "acceleration_suggestion", "reason": "Yüksek kaynak-bağlı başarı; kullanıcı onayıyla yalnızca açık görevler hızlandırılabilir.", "applied": False})
        for item in decisions:
            self.database.save_adaptive_decision(
                task["profile_id"], task["id"], item["type"], item["reason"],
                {"estimated_minutes": estimated, "actual_minutes": actual, "quiz_score": quiz_score, "difficulty": difficulty},
                applied=item["applied"],
            )
        return decisions


def goal_countdown(profile: dict[str, Any], tasks: list[dict[str, Any]], today: date | None = None) -> dict[str, Any]:
    current = today or date.today()
    target = date.fromisoformat(profile["target_end_date"])
    remaining_days = (target - current).days
    open_tasks = [item for item in tasks if item["status"] not in {"completed", "skipped"}]
    remaining_minutes = sum(int(item.get("estimated_minutes") or 0) for item in open_tasks)
    daily_capacity = max(1, int(profile["daily_minutes"]))
    needed_days = (remaining_minutes + daily_capacity - 1) // daily_capacity
    preferred = max(1, int(profile["weekly_days"]))
    calendar_days_needed = (needed_days * 7 + preferred - 1) // preferred
    projected = date.fromordinal(current.toordinal() + calendar_days_needed)
    if remaining_days < 0:
        status = "Hedef tarih geçti"
    elif projected < date.fromordinal(target.toordinal() - 3):
        status = "Programın ilerisinde"
    elif projected <= target:
        status = "Programla uyumlu"
    else:
        status = "Risk altında"
    return {
        "target_date": target.isoformat(),
        "remaining_days": remaining_days,
        "remaining_study_days": max(0, (max(0, remaining_days) * preferred) // 7),
        "estimated_completion_date": projected.isoformat(),
        "status": status,
        "remaining_minutes": remaining_minutes,
    }
