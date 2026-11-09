import io
import tempfile
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from app import app
from core import db, auth


@pytest.fixture
def test_setup(monkeypatch):
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        test_db = Path(tmpdir) / "test_files_auth.db"
        upload_dir = Path(tmpdir) / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)

        monkeypatch.setattr(db, "SQLITE_DB_PATH", test_db)
        monkeypatch.setattr(auth, "SQLITE_DB_PATH", test_db)
        from core import config
        from routers import files
        monkeypatch.setattr(config, "UPLOAD_DIR", upload_dir)
        monkeypatch.setattr(files, "UPLOAD_DIR", upload_dir)

        db.init_sqlite_db()
        client = TestClient(app)
        yield client, upload_dir


def test_unauthenticated_upload_rejected(test_setup):
    client, _ = test_setup

    file_content = b"This is confidential text."
    files = {"files": ("secret.txt", io.BytesIO(file_content), "text/plain")}

    res = client.post("/api/upload", data={"workspace": "default"}, files=files)
    assert res.status_code == 401
    assert "Authentication required" in res.json()["detail"]


def test_user_document_isolation(test_setup):
    client, _ = test_setup

    # 1. Register User 1 (Alice)
    res_a = client.post("/api/auth/register", json={"username": "alice", "password": "password123"})
    assert res_a.status_code == 200
    token_a = res_a.json()["token"]
    headers_a = {"Authorization": f"Bearer {token_a}"}

    # 2. Register User 2 (Bob)
    res_b = client.post("/api/auth/register", json={"username": "bob", "password": "password123"})
    assert res_b.status_code == 200
    token_b = res_b.json()["token"]
    headers_b = {"Authorization": f"Bearer {token_b}"}

    # 3. Alice uploads a document
    alice_file = b"Alice project notes: Alpha budget is $50,000."
    res_up_a = client.post(
        "/api/upload",
        data={"workspace": "default"},
        files={"files": ("alice_notes.txt", io.BytesIO(alice_file), "text/plain")},
        headers=headers_a,
    )
    assert res_up_a.status_code == 200
    data_up_a = res_up_a.json()
    assert len(data_up_a["uploaded"]) == 1
    file_id_a = data_up_a["uploaded"][0]["file_id"]
    assert data_up_a["uploaded"][0]["owner"] == "alice"

    # 4. Bob uploads a document
    bob_file = b"Bob secret report: Beta budget is $90,000."
    res_up_b = client.post(
        "/api/upload",
        data={"workspace": "default"},
        files={"files": ("bob_report.txt", io.BytesIO(bob_file), "text/plain")},
        headers=headers_b,
    )
    assert res_up_b.status_code == 200
    data_up_b = res_up_b.json()
    assert len(data_up_b["uploaded"]) == 1, f"Bob upload error: {data_up_b}"
    file_id_b = data_up_b["uploaded"][0]["file_id"]
    assert data_up_b["uploaded"][0]["owner"] == "bob"

    # 5. Alice lists files -> only sees alice_notes.txt
    list_a = client.get("/api/files?workspace=default", headers=headers_a)
    assert list_a.status_code == 200
    files_a = list_a.json()["files"]
    assert len(files_a) == 1
    assert files_a[0]["file_id"] == file_id_a
    assert files_a[0]["filename"] == "alice_notes.txt"

    # 6. Bob lists files -> only sees bob_report.txt
    list_b = client.get("/api/files?workspace=default", headers=headers_b)
    assert list_b.status_code == 200
    files_b = list_b.json()["files"]
    assert len(files_b) == 1
    assert files_b[0]["file_id"] == file_id_b
    assert files_b[0]["filename"] == "bob_report.txt"

    # 7. Unauthenticated list files -> empty
    client.cookies.clear()
    list_anon = client.get("/api/files?workspace=default")
    assert list_anon.status_code == 200
    assert list_anon.json()["files"] == []
    assert list_anon.json()["authenticated"] is False

    # 8. Bob tries to delete Alice's file -> 403 Forbidden
    del_forbidden = client.request(
        "DELETE",
        "/api/files",
        json={"file_id": file_id_a, "workspace": "default"},
        headers=headers_b,
    )
    assert del_forbidden.status_code == 403

    # 9. Bob tries to preview Alice's file -> 403 Forbidden
    prev_forbidden = client.get(
        f"/api/files/preview?file_id={file_id_a}&workspace=default",
        headers=headers_b,
    )
    assert prev_forbidden.status_code == 403

    # 10. Alice previews her own file -> succeeds
    prev_ok = client.get(
        f"/api/files/preview?file_id={file_id_a}&workspace=default",
        headers=headers_a,
    )
    assert prev_ok.status_code == 200
    assert "Alpha budget" in prev_ok.json()["text_preview"]

    # 11. Workspaces list reflects user-specific file counts
    ws_a = client.get("/api/workspaces", headers=headers_a)
    assert ws_a.status_code == 200
    default_ws_a = next(w for w in ws_a.json()["workspaces"] if w["name"] == "default")
    assert default_ws_a["file_count"] == 1

    # 12. Alice deletes her file -> succeeds
    del_ok = client.request(
        "DELETE",
        "/api/files",
        json={"file_id": file_id_a, "workspace": "default"},
        headers=headers_a,
    )
    assert del_ok.status_code == 200
    assert del_ok.json()["status"] == "deleted"

    # Alice's file list is now empty
    list_a_after = client.get("/api/files?workspace=default", headers=headers_a)
    assert len(list_a_after.json()["files"]) == 0

    # Bob's file is intact
    list_b_after = client.get("/api/files?workspace=default", headers=headers_b)
    assert len(list_b_after.json()["files"]) == 1
    assert list_b_after.json()["files"][0]["file_id"] == file_id_b


@pytest.mark.anyio
async def test_rag_search_user_isolation(test_setup):
    from services.rag import search_documents_hybrid, index_document

    # Index doc for User 101
    await index_document("default", "doc-101", "user101.txt", "The secret passcode for project 101 is OMEGA-X.", user_id=101)

    # Index doc for User 202
    await index_document("default", "doc-202", "user202.txt", "The secret passcode for project 202 is DELTA-Y.", user_id=202)

    # User 101 queries: should find user101.txt, NOT user202.txt
    matches_101 = await search_documents_hybrid("default", "passcode OMEGA DELTA", user_id=101)
    filenames_101 = [m["filename"] for m in matches_101]
    assert "user101.txt" in filenames_101
    assert "user202.txt" not in filenames_101

    # User 202 queries: should find user202.txt, NOT user101.txt
    matches_202 = await search_documents_hybrid("default", "passcode OMEGA DELTA", user_id=202)
    filenames_202 = [m["filename"] for m in matches_202]
    assert "user202.txt" in filenames_202
    assert "user101.txt" not in filenames_202
