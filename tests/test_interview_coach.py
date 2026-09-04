from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest

from local_learning_coach.foundry import FoundryAdapter
from local_learning_coach.indexing import IndexManager
from local_learning_coach.interview import DisabledCodeRunner, InterviewCoach, LocalPythonRunner
from local_learning_coach.interview.runner import CodeRunnerError
from local_learning_coach.interview.taxonomy import competencies_for_track, validate_competency_graph


def career_data(track: str = "backend-python") -> dict:
    return {
        "department": "Yazılım Mühendisliği",
        "education_type": "Lisans",
        "education_status": "Öğrenci",
        "experience": "0-1 yıl",
        "target_track": track,
        "target_role": "Junior Backend Engineer",
        "target_level": "junior",
        "preferred_language": "Python",
        "other_technologies": ["FastAPI", "PostgreSQL"],
        "company_type": "Ürün şirketi",
        "interview_language": "Türkçe",
        "target_interview_date": (date.today() + timedelta(days=45)).isoformat(),
        "weekly_days": 5,
        "daily_minutes": 60,
        "preferred_days": ["Pazartesi", "Çarşamba", "Cuma"],
        "declared_strengths": ["Python"],
        "declared_weaknesses": ["Sistem tasarımı"],
        "job_url": "",
        "job_text": "",
    }


@pytest.fixture
def interview(database, test_settings, profile_id) -> InterviewCoach:
    coach = InterviewCoach(database, test_settings)
    seeded = coach.seed()
    assert seeded["tracks"] == 9
    coach.save_career_profile(profile_id, career_data())
    return coach


def finish_assessment(coach: InterviewCoach, profile_id: int) -> tuple[dict, dict]:
    session = coach.start_assessment(profile_id, seed=42)
    while True:
        question = coach.current_assessment_question(session["id"])
        if not question:
            break
        if question["question_type"] == "technical":
            coach.submit_assessment_response(session["id"], question["id"], question["correct_answer"])
        else:
            coach.submit_assessment_response(session["id"], question["id"], "", skipped=True)
    return coach.assessment_session(session["id"]), coach.latest_gap_report(profile_id)


def test_taxonomy_is_acyclic_and_tracks_are_versioned(interview):
    tracks = interview.tracks()
    assert [row["support_level"] for row in tracks[:2]] == ["full", "full"]
    assert {row["id"] for row in tracks if row["support_level"] == "full"} == {
        "software-engineering-general", "backend-python",
    }
    competencies = competencies_for_track("backend-python")
    validate_competency_graph(competencies)
    assert len(competencies) >= 25
    broken = [dict(item) for item in competencies]
    broken[0]["prerequisites"] = [broken[1]["id"]]
    broken[1]["prerequisites"] = [broken[0]["id"]]
    with pytest.raises(ValueError, match="döngü"):
        validate_competency_graph(broken)


def test_career_profile_is_additive_to_general_profile(interview, database, profile_id):
    general = database.get_profile(profile_id)
    career = interview.career_profile(profile_id)
    assert general["preferred_route"] == "data-analysis"
    assert career["target_track"] == "backend-python"
    assert career["declared_strengths"] == ["Python"]


def test_assessment_has_required_distribution_and_persists_pause(interview, profile_id):
    session = interview.start_assessment(profile_id, seed=7)
    questions = interview._questions(session["track_id"], session["question_ids"])
    counts = {kind: sum(q["question_type"] == kind for q in questions) for kind in {
        "technical", "algorithm", "coding", "debugging", "sql", "system-design", "behavioral",
    }}
    assert counts == {"technical": 10, "algorithm": 2, "coding": 1, "debugging": 1,
                      "sql": 1, "system-design": 1, "behavioral": 2}
    paused = interview.assessment_action(session["id"], "pause")
    assert paused["status"] == "paused"
    assert interview.assessment_action(session["id"], "resume")["status"] == "active"


def test_gap_report_uses_measured_evidence_and_confidence(interview, profile_id):
    session, report = finish_assessment(interview, profile_id)
    assert session["status"] == "completed"
    assert len(session["responses"]) == 18
    assert report and report["overall_score"] > 0
    measured = [item for item in report["items"] if item["confidence_score"] > 0]
    unmeasured = [item for item in report["items"] if item["status"] == "ölçülmedi"]
    assert measured and unmeasured
    assert all(item["evidence"] for item in measured)
    assert all(item["confidence_score"] == 0 for item in unmeasured)
    assert "işe alınma" in report["disclaimer"]


def test_plan_is_editable_confirmable_and_tracks_progress(interview, profile_id):
    _, report = finish_assessment(interview, profile_id)
    plan = interview.create_plan(profile_id, report["id"])
    assert plan["status"] == "draft" and plan["tasks"]
    assert all(task["source_id"] and task["source_locator"] and task["reason"] for task in plan["tasks"])
    added = interview.add_plan_task(plan["id"], plan["tasks"][0]["competency_id"], "Kullanıcı ek görevi", "tekrar", 25)
    plan = interview.plan(profile_id)
    assert added["generation_method"] == "kullanıcı tarafından plana eklendi"
    edits = [{"id": task["id"], "position": len(plan["tasks"]) - task["position"] + 1,
              "included": task["included"], "title": task["title"] + "!",
              "estimated_minutes": task["estimated_minutes"]} for task in plan["tasks"]]
    assert interview.update_plan_tasks(plan["id"], edits) == len(edits)
    confirmed = interview.confirm_plan(plan["id"], profile_id)
    assert confirmed["status"] == "confirmed"
    task = next(item for item in confirmed["tasks"] if item["included"])
    assert interview.update_plan_task_status(task["id"], "in_progress")["status"] == "in_progress"
    assert interview.update_plan_task_status(task["id"], "completed")["completed_at"]


def test_plan_analysis_cache_is_profile_scoped(interview, profile_id):
    _, report = finish_assessment(interview, profile_id)
    first = interview.create_plan(profile_id, report["id"])
    second = interview.create_plan(profile_id, report["id"])
    assert first["cache_hit"] is False
    assert second["cache_hit"] is True
    assert interview.cache_status()["analysis_entries"] >= 2


def test_local_code_runner_requires_two_explicit_gates(test_settings):
    with pytest.raises(CodeRunnerError):
        DisabledCodeRunner().run("def add(a,b): return a+b", "assert add(1,2)==3", approved=True)
    enabled = replace(test_settings, code_runner_enabled=True, code_runner_timeout_seconds=3)
    runner = LocalPythonRunner(enabled)
    with pytest.raises(CodeRunnerError, match="onay"):
        runner.run("def add(a,b): return a+b", "assert add(1,2)==3", approved=False)
    result = runner.run("def add(a,b):\n    return a+b", "assert add(1,2)==3", approved=True)
    assert result.status == "passed" and result.passed_tests == 1
    assert "ağ izolasyonu garanti edilmez" in result.isolation_notice


def test_local_code_runner_reports_errors_limits_and_blocks_io(test_settings, tmp_path):
    settings = replace(test_settings, code_runner_enabled=True, code_runner_timeout_seconds=1,
                       code_runner_max_output_bytes=64)
    runner = LocalPythonRunner(settings)
    assert runner.run("def add(a,b)\n return a+b", "assert True", approved=True).status == "syntax_error"
    assert runner.run("raise ValueError('x')", "assert True", approved=True).status == "runtime_error"
    output = runner.run("print('x'*500)\ndef add(a,b): return a+b", "assert add(1,2)==3", approved=True)
    assert len(output.output.encode("utf-8")) <= 64
    with pytest.raises(CodeRunnerError):
        runner.run("import os\ndef add(a,b): return a+b", "assert add(1,2)==3", approved=True)
    with pytest.raises(CodeRunnerError):
        runner.run("open('forbidden.txt','w')", "assert True", approved=True)
    sentinel = tmp_path / "sentinel.txt"
    sentinel.write_text("koru", encoding="utf-8")
    with pytest.raises(CodeRunnerError):
        runner.run(f"open({str(sentinel)!r},'w').write('boz')", "assert True", approved=True)
    assert sentinel.read_text(encoding="utf-8") == "koru"
    with pytest.raises(CodeRunnerError, match="sınırını aştı"):
        runner.run("while True: pass", "assert True", approved=True)


def test_system_design_and_star_rubrics_are_explainable():
    design = InterviewCoach.system_design_rubric(
        "Gereksinim ve trafik kısıtlarını belirlerim. API endpoint, veri tablosu, cache TTL, hata timeout, "
        "güvenlik auth rate limit, log metrik trace ve load balancer kullanırım; çünkü bu tercih trade-off sağlar."
    )
    assert set(design) >= {"gereksinimler", "veri_modeli", "api", "güvenlik", "gözlemlenebilirlik"}
    assert all(0 <= value <= 4 for value in design.values())
    star = InterviewCoach.star_analysis("Durum şuydu. Görev benimdi. Yaptım ve sonunda sonuç %20 iyileşti.")
    assert star["completion_percentage"] == 100
    assert "kişilik" in star["notice"]


def test_mock_interview_is_persistent_and_logs_adaptation(interview, profile_id, database):
    mock = interview.start_mock_interview(profile_id, "Davranışsal mülakat")
    assert interview.mock_action(mock["id"], "pause")["status"] == "paused"
    interview.mock_action(mock["id"], "resume")
    while True:
        current = interview.mock_interview(mock["id"])["current_question"]
        if not current:
            break
        result = interview.submit_mock_response(mock["id"], "Kısa yanıt")
        assert result["follow_up"]
    completed = interview.mock_action(mock["id"], "finish")
    assert completed["status"] == "completed"
    with database.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM interview_adaptive_decisions WHERE profile_id=?", (profile_id,)).fetchone()[0] == 4


def test_mixed_mock_contains_multiple_question_types(interview, profile_id):
    mock = interview.start_mock_interview(profile_id, "Tam karma mülakat")
    questions = interview._questions(mock["track_id"], mock["question_ids"])
    assert {item["question_type"] for item in questions} == {
        "technical", "algorithm", "coding", "sql", "system-design", "behavioral",
    }


def test_mock_updates_competency_gap_plan_and_dashboard(interview, profile_id):
    _, report = finish_assessment(interview, profile_id)
    draft = interview.create_plan(profile_id, report["id"])
    before = interview.confirm_plan(draft["id"], profile_id)
    mock = interview.start_mock_interview(profile_id, "Sistem tasarımı mülakatı")
    question = interview.mock_interview(mock["id"])["current_question"]
    interview.submit_mock_response(mock["id"], "API")
    interview.mock_action(mock["id"], "finish")
    latest = interview.latest_gap_report(profile_id)
    after = interview.plan(profile_id)
    dashboard = interview.dashboard(profile_id)
    assert latest["mock_id"] == mock["id"]
    assert len(after["tasks"]) == len(before["tasks"]) + 1
    assert dashboard["target_role"] == "Junior Backend Engineer"
    assert dashboard["system_design_score"] is not None


def test_job_requirements_only_use_approved_web_snapshots(interview, database):
    database.upsert_source_data(
        [{"id": "job-doc", "filename": "job.html", "path": "snapshot", "title": "Backend ilanı",
          "route": "web-job", "sha256": "a" * 64}],
        [{"id": "job-chunk", "document_id": "job-doc", "section_path": "İlan > Gereksinimler",
          "heading": "Gereksinimler", "phase": None, "week": None, "day": None, "page": None,
          "body": "Python FastAPI SQL PostgreSQL Docker testing", "content_hash": "b" * 64,
          "order": 1, "extraction_method": "html", "text": "Python FastAPI SQL PostgreSQL Docker testing"}],
    )
    with pytest.raises(ValueError, match="onaylanmış"):
        interview.extract_job_requirements(["job-doc"])
    with database.connection() as conn:
        conn.execute("UPDATE source_documents SET source_kind='web',source_url='https://example.com/job' WHERE id='job-doc'")
    result = interview.extract_job_requirements(["job-doc"])
    assert {item["skill"] for item in result["skills"]} >= {"Python", "FastAPI", "SQL", "Docker", "Testing"}
    assert result["skills"][0]["evidence"][0]["chunk_id"] == "job-chunk"


def test_foundry_rubric_is_validated_and_cached(interview, profile_id, monkeypatch):
    question = next(item for item in interview._questions("backend-python") if item["question_type"] == "behavioral")
    calls = {"count": 0}

    def fake_structured(self, messages, required_keys, max_tokens):
        calls["count"] += 1
        return {"scores": {"communication": 70}, "explanation": "STAR kısmen var.",
                "evidence": ["örnek yanıt"],
                "question_id": question["id"], "source_id": question["source_id"]}

    monkeypatch.setattr(FoundryAdapter, "structured_json", fake_structured)
    first = interview.foundry_rubric_review(question, "Bir örnek yanıt", {"correctness_score": 50}, profile_id=profile_id)
    second = interview.foundry_rubric_review(question, "Bir örnek yanıt", {"correctness_score": 50}, profile_id=profile_id)
    assert first["cache_hit"] is False and second["cache_hit"] is True
    assert calls["count"] == 1


def test_invalid_foundry_identity_is_not_cached(interview, profile_id, monkeypatch):
    question = next(item for item in interview._questions("backend-python") if item["question_type"] == "behavioral")
    monkeypatch.setattr(FoundryAdapter, "structured_json", lambda *args, **kwargs: {
        "scores": {"communication": 70}, "explanation": "x", "evidence": ["x"],
        "question_id": "wrong", "source_id": question["source_id"],
    })
    before = interview.cache_status()["analysis_entries"]
    with pytest.raises(ValueError, match="kimliği"):
        interview.foundry_rubric_review(question, "yanıt", {"correctness_score": 50}, profile_id=profile_id)
    assert interview.cache_status()["analysis_entries"] == before


def test_reschedule_preserves_content_and_completed_tasks(interview, profile_id):
    _, report = finish_assessment(interview, profile_id)
    draft = interview.create_plan(profile_id, report["id"])
    plan = interview.confirm_plan(draft["id"], profile_id)
    first = next(item for item in plan["tasks"] if item["included"])
    interview.update_plan_task_status(first["id"], "in_progress")
    completed = interview.update_plan_task_status(first["id"], "completed")
    title = completed["title"]
    scheduled = completed["scheduled_date"]
    result = interview.reschedule_plan(plan["id"], profile_id, (date.today() + timedelta(days=90)).isoformat())
    kept = next(item for item in result["tasks"] if item["id"] == first["id"])
    assert kept["title"] == title and kept["scheduled_date"] == scheduled and kept["status"] == "completed"


def test_technical_mode_is_wired_without_replacing_general_mode():
    app_text = __import__("pathlib").Path("app.py").read_text(encoding="utf-8")
    ui_text = __import__("pathlib").Path("local_learning_coach/interview/ui.py").read_text(encoding="utf-8")
    assert '("Genel Öğrenme Koçu", "Teknik Mülakat Koçu")' in app_text
    assert "PAGES = (" in app_text
    assert "Yetkinlik ve eksiklik haritası" in ui_text


def test_streamlit_can_switch_to_technical_mode(test_settings, monkeypatch):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("LLC_DATA_DIR", str(test_settings.data_dir))
    monkeypatch.setenv("LLC_DB_PATH", str(test_settings.db_path))
    monkeypatch.setenv("LLC_INDEX_DIR", str(test_settings.index_dir))
    monkeypatch.setenv("LLC_SOURCE_DOCS", ";".join(str(path) for path in test_settings.source_paths))
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py").run(timeout=30)
    assert not app.exception
    mode = next(item for item in app.selectbox if item.label == "Çalışma modu")
    mode.set_value("Teknik Mülakat Koçu")
    app.run(timeout=30)
    assert not app.exception
    assert any(item.label == "Teknik mülakat ekranı" for item in app.radio)


def test_embedding_cache_reuses_vectors_and_invalidates_by_model(database, test_settings):
    first = IndexManager(test_settings, database).build(force=True)
    with database.connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM embedding_cache").fetchone()[0]
    second = IndexManager(test_settings, database).build(force=True)
    with database.connection() as conn:
        hits = conn.execute("SELECT SUM(hit_count) FROM embedding_cache").fetchone()[0]
    assert first["chunk_count"] == second["chunk_count"]
    assert count == first["chunk_count"] and hits >= count
    changed = replace(test_settings, embedding_model="hashing-test-v2")
    IndexManager(changed, database).build(force=True)
    with database.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM embedding_cache").fetchone()[0] == count * 2


def test_profile_export_contains_interview_data(interview, database, profile_id, tmp_path):
    finish_assessment(interview, profile_id)
    output = database.export_profile(profile_id, tmp_path / "export.json")
    text = output.read_text(encoding="utf-8")
    assert '"career_profile"' in text
    assert '"interview_gap_reports"' in text
