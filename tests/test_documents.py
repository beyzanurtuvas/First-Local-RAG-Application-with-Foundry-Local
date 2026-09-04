from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from local_learning_coach.config import DOCUMENT_DEFINITIONS, ConfigurationError, Settings
from local_learning_coach.documents import DocumentParser
from local_learning_coach.documents.parser import extract_source_tasks


@pytest.mark.source_docs
def test_three_real_docx_exist_and_are_unchanged():
    settings = Settings.load()
    expected = {
        "One-Month Machine Learning Plan.docx": "5d693e546e26cdcfedf10eafd4620945c6f1e5523ef48f2fe4e6f67bb84a3106",
        "Python Pandas Titanic Project Plan.docx": "019e4f5cbd1de86dc841ffa3004c17b20a4fb3e3be83193e4ec727bfc84f5f47",
        "Quantum Programming Month Plan.docx": "62d20c6ed1196a4bc0c21f7d30685ee7398dbf51f95b586ef165aa6734e355b1",
    }
    assert len(settings.source_paths) == 3
    for path in settings.source_paths:
        assert path.is_file()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected[path.name]


def test_only_allowed_documents_accepted(test_settings, tmp_path):
    test_settings.validate_sources()
    extra = tmp_path / "other.docx"
    extra.write_bytes(b"x")
    altered = test_settings.__class__(**{**test_settings.__dict__, "source_paths": (*test_settings.source_paths[:2], extra)})
    with pytest.raises(ConfigurationError):
        altered.validate_sources()


def test_heading_phase_week_day_detection(test_settings):
    docs = DocumentParser(max_chars=600).parse_all(test_settings.source_paths)
    ml = next(doc for doc in docs if doc.route == "machine-learning")
    assert any(chunk.phase == "2" for chunk in ml.chunks)
    assert any(chunk.week == "3" for chunk in ml.chunks)
    assert any(chunk.day == "11-12" for chunk in ml.chunks)


def test_bold_heading_detection(test_settings):
    data_path = next(path for path in test_settings.source_paths if "Titanic" in path.name)
    blocks = DocumentParser(max_chars=600).read_blocks(data_path)
    assert any(block.bold_heading and "Deliverable" in block.text for block in blocks)


def test_table_headers_preserved(test_settings):
    ml_path = next(path for path in test_settings.source_paths if "Machine Learning" in path.name)
    blocks = DocumentParser(max_chars=600).read_blocks(ml_path)
    table = next(block for block in blocks if block.kind == "table")
    assert "Week: 3" in table.text
    assert "Outcome: PyTorch begins" in table.text


def test_hyperlink_extraction(test_settings):
    data_path = next(path for path in test_settings.source_paths if "Titanic" in path.name)
    blocks = DocumentParser(max_chars=600).read_blocks(data_path)
    assert any("https://example.com/titanic" in block.urls for block in blocks)


def test_long_section_splits_on_boundaries(test_settings):
    parser = DocumentParser(max_chars=500, overlap_paragraphs=1)
    parts = parser._split_section(["A sentence. " * 100, "Son paragraf Türkçe."])
    assert len(parts) > 1
    assert all(sum(len(item) for item in part) <= 650 for part in parts)


def test_chunk_order_is_monotonic(test_settings):
    doc = DocumentParser(max_chars=600).parse(test_settings.source_paths[0])
    assert [chunk.order for chunk in doc.chunks] == list(range(len(doc.chunks)))


def test_chunk_id_is_stable(test_settings):
    parser = DocumentParser(max_chars=600)
    first = parser.parse(test_settings.source_paths[1])
    second = parser.parse(test_settings.source_paths[1])
    assert [chunk.id for chunk in first.chunks] == [chunk.id for chunk in second.chunks]


def test_unicode_survives(test_settings):
    doc = DocumentParser(max_chars=600).parse(test_settings.source_paths[2])
    assert any("Q#" in chunk.text for chunk in doc.chunks)


@pytest.mark.source_docs
def test_real_route_task_counts():
    settings = Settings.load()
    docs = DocumentParser(settings.chunk_max_chars, settings.chunk_overlap_paragraphs).parse_all(settings.source_paths)
    assert {doc.route: len(extract_source_tasks(doc)) for doc in docs} == {
        "machine-learning": 18,
        "data-analysis": 20,
        "quantum": 6,
    }
