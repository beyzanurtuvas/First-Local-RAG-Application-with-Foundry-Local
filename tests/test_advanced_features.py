from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from local_learning_coach.documents import DocumentParser, OCRPage
from local_learning_coach.indexing import IndexError, IndexManager
from local_learning_coach.indexing.embeddings import EmbeddingProvider
from local_learning_coach.learning import LearningCoachService, validate_prerequisite_graph
from local_learning_coach.learning.advanced import LearningDesignError
from local_learning_coach.search import OpenSearchBackend, SearchBackendError
from local_learning_coach.web import DiscoveredSource, SafeWebFetcher, WebSourceDiscoveryAdapter, normalize_public_url
from tests.test_custom_routes import docx_bytes, route_configuration


def ready_service(test_settings) -> LearningCoachService:
    service = LearningCoachService(test_settings)
    service.setup(build_index=True, force_index=True)
    return service


def test_onboarding_can_start_unassigned(test_settings, profile_data):
    service = LearningCoachService(test_settings)
    service.database.migrate()
    data = {
        **profile_data,
        "preferred_route": "unassigned",
        "goal_description": "Kendi kaynağımla ilerlemek istiyorum.",
        "source_method": "Henüz belgem yok",
    }
    profile_id = service.create_profile(data, {"general": "Başlangıç"})
    profile = service.profile(profile_id)
    assert profile["preferred_route"] == "unassigned"
    assert profile["source_method"] == "Henüz belgem yok"


def test_url_security_and_opensearch_localhost_only(test_settings):
    resolver = lambda *args: [(None, None, None, None, ("93.184.216.34", 443))]
    assert normalize_public_url("https://example.com/a?b=2&a=1", resolver=resolver).endswith("/a?a=1&b=2")
    for unsafe in ("file:///etc/passwd", "ftp://example.com/a", "http://127.0.0.1/a", "http://10.0.0.1/a"):
        with pytest.raises(Exception):
            normalize_public_url(unsafe, resolver=resolver)
    with pytest.raises(SearchBackendError, match="localhost"):
        OpenSearchBackend(replace(test_settings, opensearch_endpoint="https://search.example.com"))


class MockSearchProvider:
    name = "mock"

    def search(self, query: str, limit: int = 8):
        assert query == "python"
        return [DiscoveredSource("https://93.184.216.34/python", "Python Kaynağı", "Açık kaynak")]


class MockFetcher:
    def fetch(self, url: str, *, check_robots: bool = True):
        content = (
            b"<!doctype html><html><head><title>Python Guide</title></head><body>"
            b"<h1>Python Guide</h1><h2>Variables</h2>"
            b"<p>Python variables store values and examples support practice.</p>"
            b"<pre>value = 42</pre></body></html>"
        )
        import hashlib

        return {
            "url": url,
            "content": content,
            "content_type": "text/html",
            "title": "Python Guide",
            "suffix": ".html",
            "sha256": hashlib.sha256(content).hexdigest(),
            "accessed_at": "2026-09-03T00:00:00+00:00",
        }


def test_web_discovery_requires_approval_then_snapshot_and_retrieval(test_settings):
    service = ready_service(test_settings)
    adapter = WebSourceDiscoveryAdapter(test_settings, service.database, MockSearchProvider())
    rows = adapter.search("python")
    assert rows[0]["status"] == "discovered"
    assert len(service.database.list_source_documents()) == 3
    approved = adapter.approve(rows[0]["id"], fetcher=MockFetcher())
    assert approved["status"] == "approved"
    assert Path(approved["snapshot_path"]).is_file()
    document = service.database.source_document(approved["document_id"])
    assert document["source_kind"] == "web" and document["source_url"].startswith("https://")
    results = service.retrieve("Python variables", document_id=document["id"])
    assert results and results[0].chunk["document_id"] == document["id"]


def test_multi_source_sufficiency_route_quiz_and_install(test_settings, profile_id):
    service = ready_service(test_settings)
    documents = service.database.list_source_documents(origin="builtin")
    source_ids = [item["id"] for item in documents if item["route"] in {"data-analysis", "machine-learning"}]
    configuration = {**route_configuration(), "goal": "Pandas DataFrame ve örnek proje", "title": "Çoklu Kaynak"}
    report = service.analyze_sources(profile_id, source_ids, configuration)
    assert report["coverage_score"] >= 35
    assert report["score_method"] and report["can_generate"]
    service.approve_sufficiency(report["id"])
    draft = service.create_multi_source_route_draft(source_ids, profile_id, configuration, report["id"])
    assert len(draft["sources"]) == 2
    assert draft["tasks"]
    assert all(service.database.task_sources(item["id"], "draft") for item in draft["tasks"])
    validate_prerequisite_graph(draft["tasks"])
    questions = service.create_custom_quiz(draft["route"]["id"], source_ids)
    assert questions and all(item["source_id"] and item["chunk_id"] and item["source_locator"] for item in questions)
    installed = service.install_custom_route(draft["route"]["id"], profile_id)
    assert installed["task_count"] > 0
    assert all(item["source_chunk_id"] for item in service.database.tasks(profile_id))


def test_prerequisite_cycle_is_rejected():
    tasks = [
        {"id": "a", "prerequisite_task_ids": ["b"]},
        {"id": "b", "prerequisite_task_ids": ["a"]},
    ]
    with pytest.raises(LearningDesignError, match="döngü"):
        validate_prerequisite_graph(tasks)


def test_focus_timer_persists_and_does_not_complete_task(test_settings, profile_id):
    service = ready_service(test_settings)
    service.generate_plan(profile_id)
    task = service.database.tasks(profile_id)[0]
    started = service.focus_action("start", profile_id, task["id"])
    assert started["status"] == "running"
    second = LearningCoachService(test_settings)
    second.database.migrate()
    assert second.focus_session(profile_id)["id"] == started["id"]
    second.focus_action("pause", profile_id)
    second.focus_action("resume", profile_id)
    finished = second.focus_action("finish", profile_id)
    assert finished["elapsed_seconds"] >= 0
    assert second.database.get_task(task["id"])["status"] == "pending"


class MockOCR:
    name = "mock-ocr"

    def __init__(self):
        self.calls = 0

    def available(self):
        return True

    def extract_pdf(self, path: Path):
        self.calls += 1
        return [OCRPage(1, "Scanned Guide\nOptical recognition extracts this sufficiently long learning paragraph.", 0.72)]


class FakePage:
    def __init__(self, text: str):
        self.text = text

    def extract_text(self, *args, **kwargs):
        return self.text


class FakeReader:
    is_encrypted = False

    def __init__(self, path, text=""):
        self.pages = [FakePage(text)]


def test_ocr_only_runs_without_text_layer(monkeypatch, tmp_path):
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    ocr = MockOCR()
    monkeypatch.setattr("local_learning_coach.documents.parser.PdfReader", lambda path: FakeReader(path, ""))
    document = DocumentParser(600, 1, ocr_adapter=ocr).parse(
        pdf, route="custom", route_title="Scan", document_id="scan", source_name="scan.pdf"
    )
    assert ocr.calls == 1 and document.chunks
    assert all(item.extraction_method == "ocr" for item in document.chunks)
    native_ocr = MockOCR()
    monkeypatch.setattr(
        "local_learning_coach.documents.parser.PdfReader",
        lambda path: FakeReader(path, "Native Guide\nThis native text layer is sufficiently long for parsing without OCR."),
    )
    DocumentParser(600, 1, ocr_adapter=native_ocr).read_blocks(pdf)
    assert native_ocr.calls == 0


def test_incremental_index_embeds_only_new_document(test_settings, monkeypatch):
    service = ready_service(test_settings)
    manager = IndexManager(test_settings, service.database)
    old_chunks, old_vectors, _ = manager.load()
    original = EmbeddingProvider.embed_passages
    calls: list[int] = []

    def tracked(self, texts):
        values = list(texts)
        calls.append(len(values))
        return original(self, values)

    monkeypatch.setattr(EmbeddingProvider, "embed_passages", tracked)
    uploaded = service.upload_document("incremental.docx", docx_bytes())
    new_chunks, new_vectors, manifest = manager.load()
    assert calls == [uploaded["chunk_count"]]
    assert manifest["last_operation"] == "incremental-update"
    assert np.array_equal(old_vectors, new_vectors[: len(old_vectors)])
    assert len(new_chunks) == len(old_chunks) + uploaded["chunk_count"]


def test_failed_incremental_update_keeps_previous_index(test_settings, monkeypatch):
    service = ready_service(test_settings)
    manager = IndexManager(test_settings, service.database)
    uploaded = service.uploads.upload("will-fail.docx", docx_bytes(), rebuild_index=False)
    original_manifest = manager.manifest_path.read_bytes()
    monkeypatch.setattr(
        EmbeddingProvider,
        "embed_passages",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    with pytest.raises(IndexError):
        manager.update_document(uploaded["id"])
    assert manager.manifest_path.read_bytes() == original_manifest


def test_dark_theme_and_source_independent_ui_are_declared(test_settings):
    config = (test_settings.project_root.parent / "does-not-exist")
    project = Path(__file__).resolve().parents[1]
    theme = (project / ".streamlit" / "config.toml").read_text(encoding="utf-8")
    app = (project / "app.py").read_text(encoding="utf-8")
    assert 'base = "dark"' in theme and 'backgroundColor = "#05070A"' in theme
    assert "Ne öğrenmek istiyorsunuz?" in app
    assert "Hazır rotalar kataloğu" in app


class FakeHeaders:
    def __init__(self, content_type="text/html; charset=utf-8"):
        self.value = content_type

    def get_content_type(self):
        return self.value.split(";", 1)[0]

    def get_content_charset(self):
        return "utf-8"

    def get(self, key, default=None):
        return default


class FakeResponse:
    def __init__(self, body: bytes, url="https://93.184.216.34/guide"):
        self.body = body
        self.url = url
        self.headers = FakeHeaders()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit=-1):
        return self.body[:limit] if limit >= 0 else self.body

    def geturl(self):
        return self.url


class FakeOpener:
    def __init__(self, body: bytes):
        self.body = body

    def open(self, request, timeout):
        return FakeResponse(self.body)


def test_html_cleaning_preserves_structure_code_and_link(test_settings):
    body = (
        b"<html><head><title>Guide</title><style>bad</style></head><body>"
        b"<nav>Menu noise</nav><h1>Main Topic</h1><p>Read the <a href='https://example.org/doc'>official docs</a>.</p>"
        b"<ul><li>First step</li></ul><pre>print(42)</pre><footer>Footer noise</footer></body></html>"
    )
    fetched = SafeWebFetcher(test_settings, opener=FakeOpener(body)).fetch(
        "https://93.184.216.34/guide", check_robots=False
    )
    cleaned = fetched["content"].decode("utf-8")
    assert "Main Topic" in cleaned and "First step" in cleaned and "print(42)" in cleaned
    assert "https://example.org/doc" in cleaned
    assert "Menu noise" not in cleaned and "Footer noise" not in cleaned


def test_web_archive_requires_confirmation_and_keeps_snapshot(test_settings):
    service = ready_service(test_settings)
    adapter = WebSourceDiscoveryAdapter(test_settings, service.database)
    discovered = adapter.add_url("https://93.184.216.34/archive", title="Archive")
    approved = adapter.approve(discovered["id"], fetcher=MockFetcher())
    snapshot = Path(approved["snapshot_path"])
    with pytest.raises(Exception):
        service.database.archive_web_source(discovered["id"], "evet")
    archived = service.database.archive_web_source(
        discovered["id"], f"WEB KAYNAĞINI ARŞİVLE {discovered['id']}"
    )
    assert archived["status"] == "archived" and snapshot.is_file()
    assert service.database.source_document(approved["document_id"])["status"] == "archived"


def test_route_editor_duplicate_and_confirmed_version(test_settings, profile_id):
    service = ready_service(test_settings)
    uploaded = service.upload_document("version.docx", docx_bytes())
    draft = service.create_custom_route_draft(uploaded["id"], profile_id, route_configuration())
    original_count = len(draft["tasks"])
    copied = service.duplicate_custom_route_task(draft["route"]["id"], draft["tasks"][0]["id"])
    assert len(service.custom_route_tasks(draft["route"]["id"])) == original_count + 1
    assert copied["generation_type"] == "sistem tarafından oluşturulan alıştırma"
    service.install_custom_route(draft["route"]["id"], profile_id)
    new_route_id = service.create_route_version(draft["route"]["id"])
    assert new_route_id.endswith("-v2")
    assert service.database.custom_route(new_route_id)["status"] == "draft"
    assert service.database.custom_route(draft["route"]["id"])["status"] == "confirmed"


def test_route_editor_adds_named_source_bound_task(test_settings, profile_id):
    service = ready_service(test_settings)
    uploaded = service.upload_document("named-task.docx", docx_bytes())
    draft = service.create_custom_route_draft(uploaded["id"], profile_id, route_configuration())
    source_task = draft["tasks"][0]
    added = service.duplicate_custom_route_task(
        draft["route"]["id"],
        source_task["id"],
        title="Kendi uygulama görevim",
        description="Kaynak bölümünü kullanarak küçük bir uygulama yap.",
    )
    assert added["title"] == "Kendi uygulama görevim"
    assert added["generation_type"] == "sistem tarafından oluşturulan alıştırma"
    source_reference = service.database.task_sources(source_task["id"], "draft")[0]
    added_reference = service.database.task_sources(added["id"], "draft")[0]
    for field in ("source_id", "chunk_id", "section_path", "page", "url"):
        assert added_reference[field] == source_reference[field]


def test_runtime_backend_and_foundry_settings_survive_restart(test_settings, monkeypatch):
    service = ready_service(test_settings)
    monkeypatch.setattr(
        "local_learning_coach.learning.service.OpenSearchBackend.status",
        lambda self: {"backend": "opensearch", "available": True},
    )
    monkeypatch.setattr(
        "local_learning_coach.learning.service.OpenSearchBackend.index_chunks",
        lambda self, chunks: len(chunks),
    )
    service.select_search_backend("opensearch", approved_local_indexing=True)
    service.configure_foundry("http://127.0.0.1:5272/v1", "local-test-model")
    restarted = LearningCoachService(test_settings)
    assert restarted.settings.search_backend == "opensearch"
    assert restarted.settings.foundry_endpoint == "http://127.0.0.1:5272/v1"
    assert restarted.settings.foundry_model == "local-test-model"


def test_foundry_route_and_quiz_outputs_are_source_validated(test_settings, profile_id, monkeypatch):
    service = ready_service(test_settings)
    uploaded = service.upload_document("foundry-source.docx", docx_bytes())
    draft = service.create_custom_route_draft(uploaded["id"], profile_id, route_configuration())
    route_id = draft["route"]["id"]

    def route_response(self, messages, *, required_keys, max_tokens):
        assert required_keys == {"tasks"}
        return {
            "tasks": [
                {
                    "task_id": task["id"],
                    "title": task["title"] + " · yerel model",
                    "description": task["description"],
                    "task_type": task["task_type"],
                    "module": task.get("module"),
                    "estimated_minutes": task["estimated_minutes"],
                    "position": task["position"],
                    "prerequisite_task_ids": task.get("prerequisite_task_ids", []),
                    "source_chunk_ids": [
                        item["chunk_id"] for item in service.database.task_sources(task["id"], "draft")
                    ],
                }
                for task in service.custom_route_tasks(route_id)
            ]
        }

    monkeypatch.setattr("local_learning_coach.learning.service.FoundryAdapter.structured_json", route_response)
    improved = service.improve_custom_route_with_foundry(route_id)
    assert all(item["title"].endswith("· yerel model") for item in improved)

    source_chunk = service.database.source_chunks([uploaded["id"]])[0]
    correct = source_chunk["heading"] or source_chunk["body"].split(".", 1)[0]

    def quiz_response(self, messages, *, required_keys, max_tokens):
        assert required_keys == {"questions"}
        return {
            "questions": [{
                "question": "Kaynak bölümün başlığı hangisidir?",
                "options": [correct, "Yanlış seçenek A", "Yanlış seçenek B"],
                "correct_answer": correct,
                "explanation": "Cevap kaynak metinde açıkça bulunur.",
                "difficulty": "temel",
                "topic": correct,
                "source_id": uploaded["id"],
                "chunk_id": source_chunk["id"],
            }]
        }

    monkeypatch.setattr("local_learning_coach.learning.service.FoundryAdapter.structured_json", quiz_response)
    quiz = service.create_custom_quiz_with_foundry(route_id, [uploaded["id"]])
    assert quiz[0]["correct_answer"] in f"{source_chunk['heading']}\n{source_chunk['body']}"
    assert quiz[0]["generation_method"] == "foundry-local-kaynak-bağlı"


def test_invalid_foundry_quiz_does_not_replace_existing_quiz(test_settings, profile_id, monkeypatch):
    service = ready_service(test_settings)
    uploaded = service.upload_document("foundry-invalid.docx", docx_bytes())
    draft = service.create_custom_route_draft(uploaded["id"], profile_id, route_configuration())
    route_id = draft["route"]["id"]
    service.create_custom_quiz(route_id, [uploaded["id"]])
    existing = service.custom_quiz(route_id)

    monkeypatch.setattr(
        "local_learning_coach.learning.service.FoundryAdapter.structured_json",
        lambda *args, **kwargs: {
            "questions": [{
                "question": "Kaynaksız soru",
                "options": ["Uydurma", "A", "B"],
                "correct_answer": "Uydurma",
                "source_id": uploaded["id"],
                "chunk_id": service.database.source_chunks([uploaded["id"]])[0]["id"],
            }]
        },
    )
    with pytest.raises(ValueError, match="kaynak kanıtı"):
        service.create_custom_quiz_with_foundry(route_id, [uploaded["id"]])
    assert service.custom_quiz(route_id) == existing


def test_adaptive_decisions_explain_review_and_preserve_note(test_settings, profile_id):
    service = ready_service(test_settings)
    service.generate_plan(profile_id)
    task = service.database.tasks(profile_id)[0]
    service.start_task(task["id"])
    completed = service.complete_task(task["id"], task["estimated_minutes"] * 2, 5, quiz_score=30, note="Benim notum")
    assert completed["status"] == "completed" and completed["user_note"] == "Benim notum"
    decision_types = {item["decision_type"] for item in service.database.adaptive_decisions(profile_id)}
    assert {"source_review", "difficulty_review", "duration_adjustment_suggestion"}.issubset(decision_types)
    assert completed["review_task"] and completed["review_task"]["source_chunk_id"] == completed["source_chunk_id"]


def test_search_backend_defaults_local_and_fallback_is_explicit(test_settings, monkeypatch):
    from local_learning_coach.search import SearchBackendRouter

    service = ready_service(test_settings)
    assert SearchBackendRouter(test_settings).status()["backend"] == "local"
    opensearch_settings = replace(test_settings, search_backend="opensearch")
    monkeypatch.setattr(
        "local_learning_coach.search.backends.OpenSearchBackend.search",
        lambda *args, **kwargs: (_ for _ in ()).throw(SearchBackendError("offline")),
    )
    with pytest.raises(SearchBackendError):
        SearchBackendRouter(opensearch_settings).search("Pandas")
    assert SearchBackendRouter(opensearch_settings).search("Pandas", allow_local_fallback=True)
