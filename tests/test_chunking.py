"""Pure RAG chunking tests, with no external services required."""

import pytest

from app.rag import chunk_text


def test_empty_document_produces_no_chunks():
    assert chunk_text(" \n \t ") == []


def test_short_document_preserves_text():
    assert chunk_text("First paragraph.\r\n\r\nSecond paragraph.", size=100, overlap=10) == [
        "First paragraph.\nSecond paragraph."
    ]


def test_invalid_overlap_is_rejected():
    with pytest.raises(ValueError, match="overlap"):
        chunk_text("Some text", size=16, overlap=16)
