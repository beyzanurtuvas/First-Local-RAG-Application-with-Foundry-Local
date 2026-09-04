from __future__ import annotations

import json
from dataclasses import replace

import numpy as np
import pytest

from local_learning_coach.database import Database
from local_learning_coach.indexing import IndexError, IndexManager
from local_learning_coach.retrieval import HybridRetriever


def built_index(settings):
    db = Database(settings.db_path)
    db.migrate()
    manager = IndexManager(settings, db)
    manifest = manager.build(force=True)
    return manager, manifest


def test_manifest_contains_hashes_and_settings(test_settings):
    manager, manifest = built_index(test_settings)
    assert manifest["document_count"] == 3
    assert manifest["embedding_model"] == "hashing-test-v1"
    assert len(manifest["documents"]) == 3


def test_document_hash_change_marks_index_stale(test_settings):
    manager, _ = built_index(test_settings)
    path = test_settings.source_paths[0]
    path.write_bytes(path.read_bytes() + b"changed")
    assert manager.status()["stale"] is True
    assert "documents" in manager.status()["reason"]


def test_embedding_model_change_marks_index_stale(test_settings):
    manager, _ = built_index(test_settings)
    other = IndexManager(replace(test_settings, embedding_model="hashing-test-v2"))
    status = other.status()
    assert status["stale"] is True
    assert "embedding_model" in status["reason"]


def test_vector_chunk_alignment_and_normalization(test_settings):
    manager, manifest = built_index(test_settings)
    chunks, vectors, _ = manager.load()
    assert vectors.shape == (len(chunks), manifest["vector_dimension"])
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=1e-5)


def test_empty_index_is_clear_error(test_settings):
    test_settings.index_dir.mkdir(parents=True)
    with pytest.raises(IndexError, match="İndeks bulunamadı"):
        IndexManager(test_settings).load()


def test_failed_rebuild_preserves_old_manifest(test_settings, monkeypatch):
    manager, manifest = built_index(test_settings)
    original = manager.manifest_path.read_text(encoding="utf-8")
    with manager.database.connection() as conn:
        conn.execute("DELETE FROM embedding_cache")
    monkeypatch.setattr("local_learning_coach.indexing.indexer.EmbeddingProvider.embed_passages", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(IndexError):
        manager.build(force=True)
    assert manager.manifest_path.read_text(encoding="utf-8") == original


@pytest.fixture
def retriever(test_settings):
    built_index(test_settings)
    return HybridRetriever(test_settings)


def test_dense_search_runs(retriever):
    results = retriever.search("Q# simulator", top_k=3)
    assert results and all(np.isfinite(item.dense_score) for item in results)


def test_bm25_search_runs(retriever):
    results = retriever.search("Sliding window stock", top_k=3)
    assert results[0].bm25_score > 0


def test_rrf_combines_scores(retriever):
    result = retriever.search("Pandas DataFrame", top_k=1)[0]
    assert result.rrf_score > 0


def test_document_and_route_filters(retriever):
    route_results = retriever.search("simulator", route="quantum")
    assert route_results and all(item.chunk["route"] == "quantum" for item in route_results)
    name = route_results[0].chunk["source"]
    assert all(item.chunk["source"] == name for item in retriever.search("simulator", document=name))


def test_day_and_week_filters(retriever):
    assert all(item.chunk["day"] == "12" for item in retriever.search("Travel family", route="data-analysis", day="12"))
    assert all(str(item.chunk["week"]) == "5" for item in retriever.search("testing", route="quantum", week="5"))


def test_exact_term_bonus(retriever):
    result = retriever.search("Matplotlib Seaborn", route="data-analysis", top_k=1)[0]
    assert result.exact_bonus > 0


def test_context_limit(retriever):
    results = retriever.search("data", top_k=20, max_context_chars=900)
    assert sum(item.chunk["n_chars"] for item in results) <= 900 or len(results) == 1


def test_negative_query_does_not_get_lexical_score(retriever):
    results = retriever.search("Kubernetes autoscaling React deployment", top_k=1)
    assert results[0].bm25_score == 0
