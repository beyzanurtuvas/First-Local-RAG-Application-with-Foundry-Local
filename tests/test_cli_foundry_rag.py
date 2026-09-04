from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import cli
from local_learning_coach.foundry import FoundryAdapter
from local_learning_coach.rag.answering import SYSTEM_PROMPT


def test_cli_help_exits_successfully():
    result = subprocess.run([sys.executable, "cli.py", "--help"], capture_output=True, text=True, check=False)
    assert result.returncode == 0
    assert "Local Learning Coach" in result.stdout
    assert "weekly-report" in result.stdout


def test_cli_invalid_arguments_return_nonzero():
    result = subprocess.run([sys.executable, "cli.py", "task", "complete"], capture_output=True, text=True, check=False)
    assert result.returncode != 0


def test_cli_profile_create_and_show(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "cli.db"
    responses = iter([
        "Başlangıç", "Yok", "Yok", "Yok", "Yok", "Yok", "Yok",
        "Dengeli", "", "", "Haftalık",
    ])
    monkeypatch.setattr("builtins.input", lambda _: next(responses))
    result = cli.main([
        "--db", str(db_path), "profile", "create",
        "--name", "CLI Test", "--goal", "Pandas öğren", "--route", "data-analysis",
        "--weekly-days", "5", "--daily-minutes", "45", "--days", "Pazartesi,Salı,Çarşamba,Perşembe,Cuma",
        "--start-date", "2026-09-02", "--target-end-date", "2026-12-01", "--declared-level", "Başlangıç",
    ])
    assert result == 0
    assert "Profil oluşturuldu" in capsys.readouterr().out
    assert cli.main(["--db", str(db_path), "profile", "show"]) == 0
    assert "CLI Test" in capsys.readouterr().out


def test_cli_today_progress_and_retrieval_only(test_settings, profile_id, database, profile_data, monkeypatch, capsys):
    from local_learning_coach.learning import PersonalPlanner
    from tests.test_database_planner import source_tasks

    plan = PersonalPlanner().generate({**profile_data, "id": profile_id}, source_tasks(count=1, minutes=45))
    database.replace_profile_plan(profile_id, plan.tasks)
    monkeypatch.setenv("LLC_SOURCE_DIR", str(test_settings.source_paths[0].parent))
    monkeypatch.setenv("LLC_INDEX_DIR", str(test_settings.index_dir))
    monkeypatch.setenv("LLC_EMBEDDING_BACKEND", "hashing")
    monkeypatch.setenv("LLC_EMBEDDING_MODEL", "hashing-test-v1")
    monkeypatch.setenv("LLC_CHUNK_MAX_CHARS", "600")
    monkeypatch.setenv("LLC_CHUNK_OVERLAP_PARAGRAPHS", "1")
    from local_learning_coach.indexing import IndexManager

    IndexManager(test_settings, database).build(force=True)
    assert cli.main(["--db", str(test_settings.db_path), "today"]) == 0
    assert "task-" in capsys.readouterr().out
    assert cli.main(["--db", str(test_settings.db_path), "progress"]) == 0
    assert '"total": 1' in capsys.readouterr().out
    assert cli.main(["--db", str(test_settings.db_path), "retrieve", "Q# simulator", "--route", "quantum"]) == 0
    assert "Quantum" in capsys.readouterr().out


def test_foundry_endpoint_rejects_cloud(test_settings):
    from dataclasses import replace

    with pytest.raises(ValueError, match="localhost"):
        FoundryAdapter(replace(test_settings, foundry_endpoint="https://api.openai.com/v1"))


def test_foundry_unavailable_has_actionable_message(test_settings):
    status = FoundryAdapter(test_settings).health()
    assert status["available"] is False
    assert "Foundry Local" in status["message"]


def test_rag_prompt_contains_grounding_rules():
    assert "Yalnızca verilen CONTEXT" in SYSTEM_PROMPT
    assert "[1], [2]" in SYSTEM_PROMPT
    assert "uydurma" in SYSTEM_PROMPT
    assert "Planlama motorunun görev durumlarını değiştirme" in SYSTEM_PROMPT


@pytest.mark.integration
def test_real_foundry_model_if_available():
    from local_learning_coach.config import Settings

    adapter = FoundryAdapter(Settings.load())
    health = adapter.health()
    if not health["available"]:
        pytest.skip("Foundry Local servisi/modeli çalışmıyor")
    output = "".join(adapter.stream_chat([{"role": "user", "content": "Yalnızca 'tamam' yaz."}], max_tokens=10))
    assert output.strip()
