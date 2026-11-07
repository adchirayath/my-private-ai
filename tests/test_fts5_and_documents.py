import pytest
from core.db import (
    save_document,
    get_document,
    list_documents_db,
    delete_document,
    index_fts_chunks,
    search_fts_chunks,
    delete_fts_chunks,
    cleanup_expired_sessions,
)
from services.chunker import split_text_with_metadata


def test_documents_crud():
    ws = "test-ws-doc"
    fid = "doc123"
    fname = "report.pdf"
    
    # Save
    save_document(fid, ws, fname, 2048, 5)
    doc = get_document(fid, ws)
    assert doc is not None
    assert doc["filename"] == fname
    assert doc["file_size"] == 2048
    assert doc["chunk_count"] == 5

    # List
    docs = list_documents_db(ws)
    assert len(docs) >= 1
    assert any(d["file_id"] == fid for d in docs)

    # Delete
    delete_document(fid, ws)
    assert get_document(fid, ws) is None


def test_fts5_search_and_ranking():
    ws = "test-ws-fts"
    fid = "doc999"
    fname = "financial_statement.pdf"
    chunks = [
        {"id": f"{fid}-0", "content": "Net profit margin exceeded thirty percent in Q4.", "page_number": "Page 1"},
        {"id": f"{fid}-1", "content": "The marketing expenses were kept strictly under budget.", "page_number": "Page 2"},
    ]

    index_fts_chunks(ws, fid, fname, chunks)

    # Search for term in chunk 0
    results = search_fts_chunks(ws, "profit margin")
    assert len(results) >= 1
    assert results[0]["id"] == f"{fid}-0"
    assert results[0]["page_number"] == "Page 1"

    # Search with punctuation and special chars (should be sanitized and succeed)
    punct_res = search_fts_chunks(ws, "profit: Q4? (margin) + 30%")
    assert len(punct_res) >= 1
    assert punct_res[0]["id"] == f"{fid}-0"

    # Delete
    delete_fts_chunks(ws, fid)
    empty_res = search_fts_chunks(ws, "profit")
    assert len(empty_res) == 0


def test_split_text_with_metadata():
    text = (
        "[Page 1]\n"
        "Executive Summary: Company revenue grew by twenty percent across all global sectors.\n\n"
        "[Page 2]\n"
        "Financial Details: Operating costs were reduced by ten percent through vendor consolidation."
    )
    chunks = split_text_with_metadata(text, chunk_size=90, overlap=15)
    assert len(chunks) >= 2
    assert "Page 1" in (chunks[0]["page_number"] or "")
    assert "Page 2" in (chunks[-1]["page_number"] or "")


def test_cleanup_expired_sessions():
    count = cleanup_expired_sessions()
    assert isinstance(count, int)


def test_export_conversation_api():
    from fastapi.testclient import TestClient
    from app import app
    from core.db import save_message, clear_conversation_history

    clear_conversation_history("export-ws", "conv-export")
    save_message("export-ws", "conv-export", "user", "What is the revenue?", None, None)
    save_message("export-ws", "conv-export", "assistant", "Revenue is $50M.", ["finance.csv"], None)

    client = TestClient(app)
    res_md = client.get("/api/chat/export?workspace=export-ws&conversation_id=conv-export&format=markdown")
    assert res_md.status_code == 200
    data_md = res_md.json()
    assert data_md["format"] == "markdown"
    assert "What is the revenue?" in data_md["content"]
    assert "Revenue is $50M." in data_md["content"]

    res_json = client.get("/api/chat/export?workspace=export-ws&conversation_id=conv-export&format=json")
    assert res_json.status_code == 200
    data_json = res_json.json()
    assert len(data_json["messages"]) == 2

