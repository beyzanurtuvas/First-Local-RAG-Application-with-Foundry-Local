from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from local_learning_coach.documents import SourceTask


DAY_NAME_TO_WEEKDAY = {
    "pazartesi": 0, "monday": 0,
    "salı": 1, "sali": 1, "tuesday": 1,
    "çarşamba": 2, "carsamba": 2, "wednesday": 2,
    "perşembe": 3, "persembe": 3, "thursday": 3,
    "cuma": 4, "friday": 4,
    "cumartesi": 5, "saturday": 5,
    "pazar": 6, "sunday": 6,
}


@dataclass(frozen=True)
class PlanResult:
    tasks: list[dict[str, Any]]
    warnings: list[str]
    projected_end_date: str | None
    accelerated_suggestion: str | None


def _task_id(profile_id: int, source_chunk_id: str, part: int) -> str:
    raw = f"{profile_id}|{source_chunk_id}|{part}".encode("utf-8")
    return "task-" + hashlib.sha256(raw).hexdigest()[:18]


class PersonalPlanner:
    def generate(self, profile: dict[str, Any], source_tasks: Iterable[SourceTask]) -> PlanResult:
        daily_minutes = int(profile["daily_minutes"])
        weekdays = self._weekdays(profile["preferred_days"], int(profile["weekly_days"]))
        start = max(date.fromisoformat(profile["start_date"]), date.today())
        target = date.fromisoformat(profile["target_end_date"])
        if target < start:
            raise ValueError("Hedef bitiş tarihi plan başlangıcından önce olamaz.")

        measured = profile.get("measured_level") if profile.get("assessment_status") == "measured" else None
        review_factor = {"Orta": 0.75, "İleri": 0.6}.get(measured, 1.0)
        accelerated = None
        if measured in {"Orta", "İleri"}:
            accelerated = (
                f"Ölçülen {measured} düzeyi nedeniyle erken teori görevleri daha kısa tekrar oturumlarına çevrildi; "
                "kritik görevler atlanmadı."
            )

        sessions: list[dict[str, Any]] = []
        position = 0
        previous_id: str | None = None
        current_date = self._next_study_day(start, weekdays)
        used_by_date: defaultdict[date, int] = defaultdict(int)
        sources = list(source_tasks)
        source_last_ids: dict[str, str] = {}
        for source_index, source in enumerate(sources):
            planning_key = source.planning_key or source.source_chunk_id
            minutes = source.estimated_minutes
            if review_factor < 1.0 and source_index < max(1, len(sources) // 4) and source.task_type == "teori":
                minutes = max(30, int(math.ceil(minutes * review_factor / 5) * 5))
            part_count = max(1, math.ceil(minutes / daily_minutes))
            remaining = minutes
            parent_id = _task_id(profile["id"], planning_key, 0) if part_count > 1 else None
            for part in range(1, part_count + 1):
                allocation = min(daily_minutes, remaining)
                while used_by_date[current_date] + allocation > daily_minutes:
                    current_date = self._next_study_day(current_date + timedelta(days=1), weekdays)
                task_id = _task_id(profile["id"], planning_key, part)
                position += 1
                title = source.title
                if part_count > 1:
                    title = f"{title} — Oturum {part}/{part_count}"
                explicit_dependencies = [
                    source_last_ids[key] for key in source.prerequisite_planning_keys if key in source_last_ids
                ]
                dependencies = [previous_id] if part > 1 and previous_id else (
                    explicit_dependencies or ([previous_id] if previous_id else [])
                )
                sessions.append(
                    {
                        "id": task_id,
                        "source_document": source.source_document,
                        "source_section": source.source_section,
                        "source_chunk_id": source.source_chunk_id,
                        "route": source.route,
                        "phase": source.phase,
                        "week": source.week,
                        "day": source.day,
                        "source_page": source.source_page,
                        "provenance": source.provenance,
                        "generation_type": source.generation_type or source.provenance,
                        "module": source.module,
                        "confidence_explanation": source.confidence_explanation,
                        "title": title,
                        "description": source.description,
                        "task_type": source.task_type,
                        "estimated_minutes": allocation,
                        "estimated_is_system": source.estimated_is_system,
                        "position": position,
                        "prerequisite_task_ids": dependencies,
                        "parent_task_id": parent_id,
                        "is_critical": source.is_critical,
                        "scheduled_date": current_date.isoformat(),
                    }
                )
                used_by_date[current_date] += allocation
                remaining -= allocation
                previous_id = task_id
                if used_by_date[current_date] >= daily_minutes:
                    current_date = self._next_study_day(current_date + timedelta(days=1), weekdays)
            source_last_ids[planning_key] = previous_id

        projected = date.fromisoformat(sessions[-1]["scheduled_date"]) if sessions else None
        warnings: list[str] = []
        if projected and projected > target:
            required_days = len({item["scheduled_date"] for item in sessions})
            warnings.append(
                f"Hedef tarih mevcut kapasiteyle gerçekçi değil. {required_days} çalışma günü gerekiyor; "
                f"kapasiteyi aşmayan alternatif bitiş tarihi {projected.isoformat()}."
            )
        return PlanResult(
            tasks=sessions,
            warnings=warnings,
            projected_end_date=projected.isoformat() if projected else None,
            accelerated_suggestion=accelerated,
        )

    @staticmethod
    def _weekdays(preferred_days: list[str], weekly_days: int) -> set[int]:
        resolved = {DAY_NAME_TO_WEEKDAY[item.strip().casefold()] for item in preferred_days if item.strip().casefold() in DAY_NAME_TO_WEEKDAY}
        if not resolved:
            resolved = set(range(min(max(weekly_days, 1), 7)))
        if len(resolved) > weekly_days:
            resolved = set(sorted(resolved)[:weekly_days])
        return resolved

    @staticmethod
    def _next_study_day(value: date, weekdays: set[int]) -> date:
        candidate = value
        for _ in range(8):
            if candidate.weekday() in weekdays:
                return candidate
            candidate += timedelta(days=1)
        raise ValueError("Uygun çalışma günü bulunamadı.")

    def reschedule_open_tasks(self, profile: dict[str, Any], tasks: list[dict[str, Any]], from_date: date) -> dict[str, str]:
        weekdays = self._weekdays(profile["preferred_days"], int(profile["weekly_days"]))
        capacity = int(profile["daily_minutes"])
        used: defaultdict[date, int] = defaultdict(int)
        for task in tasks:
            if task["status"] == "completed" and task.get("scheduled_date"):
                used[date.fromisoformat(task["scheduled_date"])] += task["estimated_minutes"]
        result: dict[str, str] = {}
        current = self._next_study_day(from_date, weekdays)
        for task in sorted((item for item in tasks if item["status"] != "completed"), key=lambda item: item["position"]):
            minutes = task["estimated_minutes"]
            while used[current] + minutes > capacity:
                current = self._next_study_day(current + timedelta(days=1), weekdays)
            result[task["id"]] = current.isoformat()
            used[current] += minutes
            if used[current] >= capacity:
                current = self._next_study_day(current + timedelta(days=1), weekdays)
        return result
