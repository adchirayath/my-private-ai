import tempfile
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from app import app
from core import db, auth


@pytest.fixture
def client_with_db(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        test_db = Path(tmpdir) / "test_api_auth.db"
        monkeypatch.setattr(db, "SQLITE_DB_PATH", test_db)
        monkeypatch.setattr(auth, "SQLITE_DB_PATH", test_db)
        db.init_sqlite_db()
        client = TestClient(app)
        yield client


def test_auth_api_flow(client_with_db):
    client = client_with_db

    # 1. Initially unauthenticated
    me_res = client.get("/api/auth/me")
    assert me_res.status_code == 200
    assert me_res.json()["authenticated"] is False

    # 2. Accessing chat history without login returns 401
    hist_res = client.get("/api/chat/history?workspace=default&conversation_id=default")
    assert hist_res.status_code == 401

    conv_res = client.get("/api/chat/conversations?workspace=default")
    assert conv_res.status_code == 401

    # 3. Register user
    reg_res = client.post("/api/auth/register", json={"username": "sarah", "password": "mypassword123"})
    assert reg_res.status_code == 200
    data = reg_res.json()
    assert data["status"] == "registered"
    assert data["username"] == "sarah"
    token = data["token"]
    assert token

    # 4. Check me with token header
    me_res2 = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me_res2.status_code == 200
    assert me_res2.json()["authenticated"] is True
    assert me_res2.json()["username"] == "sarah"

    # 5. Fetch conversations with token -> should now succeed and be empty
    conv_auth = client.get("/api/chat/conversations?workspace=default", headers={"Authorization": f"Bearer {token}"})
    assert conv_auth.status_code == 200
    assert conv_auth.json()["conversations"] == []

    # 6. Logout
    logout_res = client.post("/api/auth/logout", headers={"Authorization": f"Bearer {token}"})
    assert logout_res.status_code == 200

    # 7. Old token no longer valid
    me_res3 = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me_res3.json()["authenticated"] is False

    # 8. Login again
    login_res = client.post("/api/auth/login", json={"username": "sarah", "password": "mypassword123"})
    assert login_res.status_code == 200
    new_token = login_res.json()["token"]
    assert new_token

    # 9. Login with invalid password
    bad_login = client.post("/api/auth/login", json={"username": "sarah", "password": "wrong"})
    assert bad_login.status_code == 401
