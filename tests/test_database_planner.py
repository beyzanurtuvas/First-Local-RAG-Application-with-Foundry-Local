from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import date, timedelta

import pytest

from local_learning_coach.database import DatabaseError
from local_learning_coach.documents import SourceTask
from local_learning_coach.learning import AssessmentEngine, PersonalPlanner


def source_tasks(count=3, minutes=90):
    return [
        SourceTask(
            source_document="Plan.docx", source_section=f"Phase 1 > Day {i}", source_chunk_id=f"chunk-{i}",
            route="data-analysis", phase="1", week=None, day=str(i), title=f"Day {i}: Task",
            description="Practice Pandas and document results.", task_type="uygulama",
            estimated_minutes=minutes, estimated_is_system=True, position=i, is_critical=i == 1,
        )
        for i in range(1, count + 1)
    ]


def test_migration_is_idempotent(database):
    assert database.migrate() == []
    assert "profiles" in database.table_names()
    assert len(database.table_names()) == 44
    assert {"route_sources", "web_sources", "sufficiency_reports", "focus_sessions"}.issubset(database.table_names())
    assert {"career_profiles", "interview_plans", "mock_interviews", "embedding_cache"}.issubset(database.table_names())


def test_profile_crud(database, profile_id):
    profile = database.get_profile(profile_id)
    assert profile["name"] == "Test Öğrencisi"
    assert profile["skills"]["python"] == "Başlangıç"
    assert len(database.list_profiles()) == 1


def test_profile_delete_requires_exact_confirmation(database, profile_id):
    with pytest.raises(DatabaseError):
        database.delete_profile(profile_id, "evet")
    database.delete_profile(profile_id, f"PROFİLİ SİL {profile_id}")
    assert database.get_profile(profile_id) is None


def test_foreign_keys_enabled(database):
    with pytest.raises(DatabaseError):
        with database.connection() as conn:
            conn.execute("INSERT INTO profile_skills(profile_id,skill_name,declared_level) VALUES (999,'x','y')")


def test_planner_daily_capacity_and_split(profile_data):
    profile = {**profile_data, "id": 1}
    result = PersonalPlanner().generate(profile, source_tasks(count=1, minutes=150))
    assert len(result.tasks) == 3
    assert all(task["estimated_minutes"] <= 60 for task in result.tasks)


def test_planner_prerequisites_are_sequential(profile_data):
    result = PersonalPlanner().generate({**profile_data, "id": 1}, source_tasks(count=2, minutes=45))
    assert result.tasks[0]["prerequisite_task_ids"] == []
    assert result.tasks[1]["prerequisite_task_ids"] == [result.tasks[0]["id"]]


def test_unrealistic_target_warns(profile_data):
    profile = {**profile_data, "id": 1, "target_end_date": date.today().isoformat(), "daily_minutes": 30}
    result = PersonalPlanner().generate(profile, source_tasks(count=5, minutes=120))
    assert result.warnings
    assert result.projected_end_date > profile["target_end_date"]


def test_measured_high_level_accelerates_theory(profile_data):
    tasks = source_tasks(count=4, minutes=100)
    tasks[0] = replace(tasks[0], task_type="teori")
    profile = {**profile_data, "id": 1, "measured_level": "İleri", "assessment_status": "measured"}
    result = PersonalPlanner().generate(profile, tasks)
    assert result.accelerated_suggestion
    assert result.tasks[0]["estimated_minutes"] <= profile["daily_minutes"]


def install_plan(database, profile_id, profile_data):
    result = PersonalPlanner().generate({**profile_data, "id": profile_id}, source_tasks(count=2, minutes=45))
    database.replace_profile_plan(profile_id, result.tasks)
    return result.tasks


def test_task_creation_and_valid_transitions(database, profile_id, profile_data):
    tasks = install_plan(database, profile_id, profile_data)
    started = database.transition_task(tasks[0]["id"], "in_progress")
    assert started["started_at"]
    completed = database.transition_task(tasks[0]["id"], "completed", actual_minutes=40, difficulty_rating=3)
    assert completed["completed_at"] and completed["actual_minutes"] == 40


def test_invalid_transition_and_completion_fields(database, profile_id, profile_data):
    tasks = install_plan(database, profile_id, profile_data)
    with pytest.raises(DatabaseError, match="gerçek çalışma süresi"):
        database.transition_task(tasks[0]["id"], "completed")
    database.transition_task(tasks[0]["id"], "completed", actual_minutes=30)
    with pytest.raises(DatabaseError, match="Geçersiz"):
        database.transition_task(tasks[0]["id"], "completed", actual_minutes=30)


def test_dependency_blocks_start(database, profile_id, profile_data):
    tasks = install_plan(database, profile_id, profile_data)
    with pytest.raises(DatabaseError, match="Ön koşullar"):
        database.transition_task(tasks[1]["id"], "in_progress")


def test_postpone_and_reschedule(database, profile_id, profile_data):
    tasks = install_plan(database, profile_id, profile_data)
    database.transition_task(tasks[0]["id"], "postponed")
    new_date = (date.today() + timedelta(days=5)).isoformat()
    database.reschedule(tasks[0]["id"], new_date)
    assert database.get_task(tasks[0]["id"])["scheduled_date"] == new_date


def test_completed_task_cannot_be_rescheduled(database, profile_id, profile_data):
    tasks = install_plan(database, profile_id, profile_data)
    database.transition_task(tasks[0]["id"], "completed", actual_minutes=30)
    with pytest.raises(DatabaseError, match="Tamamlanmış"):
        database.reschedule(tasks[0]["id"], date.today().isoformat())


def test_progress_calculation(database, profile_id, profile_data):
    tasks = install_plan(database, profile_id, profile_data)
    database.transition_task(tasks[0]["id"], "completed", actual_minutes=35)
    summary = database.progress_summary(profile_id)
    assert summary["completed"] == 1 and summary["percentage"] == 50.0
    assert summary["actual_minutes"] == 35


def test_review_task_derived_from_source(database, profile_id, profile_data):
    tasks = install_plan(database, profile_id, profile_data)
    original = database.transition_task(tasks[0]["id"], "completed", actual_minutes=35, quiz_score=40)
    review = database.insert_review_task(original, (date.today() + timedelta(days=1)).isoformat())
    assert review["parent_task_id"] == original["id"]
    assert review["status"] == "needs_review"


def test_critical_task_skip_requires_confirmation(database, profile_id, profile_data):
    tasks = install_plan(database, profile_id, profile_data)
    with pytest.raises(DatabaseError, match="Kritik"):
        database.transition_task(tasks[0]["id"], "skipped")


def test_assessment_is_deterministic(database, profile_id):
    engine = AssessmentEngine(database)
    answers = {question.id: question.correct_answer for question in engine.questions("data-analysis")}
    result = engine.grade(profile_id, "data-analysis", answers)
    assert result["score"] == 100 and result["level"] == "İleri"


def test_assessment_skip_keeps_declared_separate(database, profile_id):
    result = AssessmentEngine(database).skip(profile_id, "data-analysis", "Temel")
    profile = database.get_profile(profile_id)
    assert result["skipped"] and profile["measured_level"] is None
    assert profile["assessment_status"] == "user_declared"
