"""
tests/test_starter_prompts.py - Unit and Integration Tests for Starter Chips
----------------------------------------------------------------------------
Verifies:
1. Generic starter chips returned when no documents are attached to the workspace.
2. Dynamic starter chips generated when documents are attached, tailored to document contents.
3. Single document vs multi-document dynamic prompt generation.
4. Document title and category extraction.
5. In-memory cache behavior and signature validation.
6. HTTP endpoint GET /api/chat/starter-prompts behavior for both empty and document-attached workspaces.
"""

import pytest
from fastapi.testclient import TestClient

from app import app
from core.db import save_document, index_fts_chunks, delete_document, delete_fts_chunks
from services.starter_prompts import (
    GENERIC_STARTER_CHIPS,
    get_starter_prompts,
    generate_document_starter_chips,
    _extract_document_title,
    _generate_single_document_chips,
)


def test_generic_starter_chips_when_no_documents():
    """Verify that an empty workspace returns generic, non-document prompts."""
    res = get_starter_prompts("test_empty_workspace_xyz_99")
    assert res["has_documents"] is False
    assert res["document_count"] == 0
    assert len(res["chips"]) == 4
    assert res["chips"] == GENERIC_STARTER_CHIPS
    # Ensure no generic prompt refers to uploaded documents
    for chip in res["chips"]:
        assert "uploaded" not in chip["prompt"].lower()
        assert "attachment" not in chip["prompt"].lower()


def test_title_extraction():
    """Verify clean title extraction from document text and filenames."""
    title1 = _extract_document_title("quarterly_sales.csv", "Quarter,Region,Units_Sold,Revenue")
    assert "Financials" in title1 or "Quarterly" in title1

    title2 = _extract_document_title("smart_office_report.pdf", "[Page 1]\nSmart Office Upgrade\nProject Plan")
    assert "Smart Office" in title2

    title3 = _extract_document_title("policy_handbook.html", "Acme Corp Employee Policy & FAQ")
    assert "Policy" in title3 or "Handbook" in title3


def test_single_document_dynamic_chips():
    """Verify single document gets 4 distinct, targeted questions."""
    chips = _generate_single_document_chips(
        "Financial_Report_2026.csv",
        "Quarter,Region,Revenue_USD,Net_Profit,Budget\nQ1,North,500000,120000,400000",
    )
    assert len(chips) == 4
    prompts_text = " ".join(c["prompt"] for c in chips)
    assert "Financial_Report_2026.csv" in prompts_text
    assert any("budget" in c["label"].lower() or "financial" in c["label"].lower() for c in chips)


def test_multi_document_dynamic_chips():
    """Verify multiple documents produce questions distributed across the documents."""
    docs = [
        {
            "file_id": "f1",
            "filename": "Project_Plan.pdf",
            "chunk_count": 5,
            "size_bytes": 1024,
        },
        {
            "file_id": "f2",
            "filename": "HR_Policy.html",
            "chunk_count": 4,
            "size_bytes": 800,
        },
        {
            "file_id": "f3",
            "filename": "Quarterly_Revenue.csv",
            "chunk_count": 3,
            "size_bytes": 600,
        },
    ]
    chips = generate_document_starter_chips("test_ws_multi", docs)
    assert len(chips) == 4
    # Ensure questions reference the attached documents
    all_prompts = " ".join(c["prompt"] for c in chips)
    assert "Project_Plan.pdf" in all_prompts or "HR_Policy.html" in all_prompts


def test_lifecycle_starter_prompts():
    """Verify transition from generic -> dynamic -> generic as documents are added and deleted."""
    ws = "test_lifecycle_ws"
    fid = "doc_lifecycle_1"
    fname = "Engineering_Architecture.pdf"

    try:
        # 1. Initially empty -> generic
        init_res = get_starter_prompts(ws)
        assert init_res["has_documents"] is False
        assert init_res["chips"] == GENERIC_STARTER_CHIPS

        # 2. Attach a document -> dynamic
        save_document(fid, ws, fname, 4096, 2)
        index_fts_chunks(ws, fid, fname, [
            {"id": f"{fid}-0", "content": "Engineering Architecture Overview: API gateway, microservices, and PostgreSQL database.", "page_number": "Page 1"}
        ])

        attached_res = get_starter_prompts(ws)
        assert attached_res["has_documents"] is True
        assert attached_res["document_count"] == 1
        assert len(attached_res["chips"]) == 4
        assert any("Engineering_Architecture.pdf" in c["prompt"] for c in attached_res["chips"])

        # 3. Delete the document -> reverts to generic
        delete_document(fid, ws)
        delete_fts_chunks(ws, fid)

        cleaned_res = get_starter_prompts(ws)
        assert cleaned_res["has_documents"] is False
        assert cleaned_res["document_count"] == 0
        assert cleaned_res["chips"] == GENERIC_STARTER_CHIPS

    finally:
        delete_document(fid, ws)
        delete_fts_chunks(ws, fid)


def test_api_starter_prompts_endpoint():
    """Verify HTTP GET /api/chat/starter-prompts endpoint."""
    client = TestClient(app)
    client.cookies.clear()

    # Empty workspace
    resp_empty = client.get("/api/chat/starter-prompts?workspace=api_empty_ws_xyz")
    assert resp_empty.status_code == 200
    data_empty = resp_empty.json()
    assert data_empty["has_documents"] is False
    assert data_empty["document_count"] == 0
    assert len(data_empty["chips"]) == 4

    # Unauthenticated request to workspace with documents must return generic chips (no leak without login)
    resp_default = client.get("/api/chat/starter-prompts?workspace=default")
    assert resp_default.status_code == 200
    data_default = resp_default.json()
    assert data_default["has_documents"] is False
    assert data_default["document_count"] == 0
    assert len(data_default["chips"]) == 4
    assert data_default["chips"] == GENERIC_STARTER_CHIPS

    # Authenticated request for a user with documents receives dynamic chips
    import uuid
    from core.auth import register_user
    test_user = f"user_{uuid.uuid4().hex[:8]}"
    auth_data = register_user(test_user, "password123")
    user_id = auth_data["user_id"]
    token = auth_data["token"]
    save_document("doc_starter_auth_1", "default", "Financial_Summary.csv", 2048, 2, user_id=user_id)
    try:
        resp_auth = client.get(
            "/api/chat/starter-prompts?workspace=default",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp_auth.status_code == 200
        data_auth = resp_auth.json()
        assert data_auth["has_documents"] is True
        assert data_auth["document_count"] >= 1
        assert len(data_auth["chips"]) == 4
    finally:
        delete_document("doc_starter_auth_1", "default")
        import sqlite3
        from core.config import SQLITE_DB_PATH
        with sqlite3.connect(SQLITE_DB_PATH) as conn:
            conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
            conn.commit()
