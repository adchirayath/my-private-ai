import pytest
import uuid
import sqlite3
import shutil
from fastapi.testclient import TestClient
from app import app
from core.config import SQLITE_DB_PATH, UPLOAD_DIR

client = TestClient(app)


@pytest.fixture
def auth_client():
    u_test = f"wsuser_{uuid.uuid4().hex[:6]}"
    client.cookies.clear()
    reg = client.post("/api/auth/register", json={"username": u_test, "password": "password123"})
    token = reg.json()["token"]
    user_id = reg.json()["user_id"]
    headers = {"Authorization": f"Bearer {token}"}
    yield client, headers, user_id
    with sqlite3.connect(SQLITE_DB_PATH) as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.execute("DELETE FROM workspaces WHERE user_id = ?", (user_id,))
        conn.commit()
    shutil.rmtree(UPLOAD_DIR / f"user_{user_id}", ignore_errors=True)


def test_unauthenticated_workspaces_blocked():
    client.cookies.clear()
    res_get = client.get("/api/workspaces")
    assert res_get.status_code == 401
    assert "Authentication required" in res_get.json()["detail"]

    res_post = client.post("/api/workspaces", json={"name": "test-ws"})
    assert res_post.status_code == 401

    res_del = client.request("DELETE", "/api/workspaces", json={"workspace": "test-ws"})
    assert res_del.status_code == 401


def test_get_workspaces(auth_client):
    _, headers, _ = auth_client
    response = client.get("/api/workspaces", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert "workspaces" in data
    names = [w["name"] for w in data["workspaces"]]
    assert "default" in names


def test_create_and_delete_workspace(auth_client):
    _, headers, _ = auth_client
    ws_name = "test-automated-workspace"

    # Create
    res_create = client.post("/api/workspaces", json={"name": ws_name}, headers=headers)
    assert res_create.status_code == 200
    data_create = res_create.json()
    assert data_create["status"] == "created"
    assert data_create["workspace"] == ws_name

    # Verify present in list
    res_list = client.get("/api/workspaces", headers=headers)
    names = [w["name"] for w in res_list.json()["workspaces"]]
    assert ws_name in names

    # Delete
    res_del = client.request("DELETE", "/api/workspaces", json={"workspace": ws_name}, headers=headers)
    assert res_del.status_code == 200
    assert res_del.json()["status"] == "deleted"

    # Verify removed from list
    res_list_after = client.get("/api/workspaces", headers=headers)
    names_after = [w["name"] for w in res_list_after.json()["workspaces"]]
    assert ws_name not in names_after


def test_cannot_delete_default_workspace(auth_client):
    _, headers, _ = auth_client
    res = client.request("DELETE", "/api/workspaces", json={"workspace": "default"}, headers=headers)
    assert res.status_code == 400
    assert "default" in res.json()["detail"].lower()


def test_create_workspace_invalid_name(auth_client):
    _, headers, _ = auth_client
    res = client.post("/api/workspaces", json={"name": "   "}, headers=headers)
    assert res.status_code == 400


def test_user_workspace_isolation():
    import uuid
    import sqlite3
    import shutil
    from core.config import SQLITE_DB_PATH, UPLOAD_DIR

    u_alice = f"alice_{uuid.uuid4().hex[:6]}"
    u_bob = f"bob_{uuid.uuid4().hex[:6]}"
    created_uids = []

    try:
        # 1. Register Alice
        client.cookies.clear()
        res_a = client.post("/api/auth/register", json={"username": u_alice, "password": "password123"})
        assert res_a.status_code == 200
        data_a = res_a.json()
        token_a = data_a["token"]
        created_uids.append(data_a.get("user_id"))
        headers_a = {"Authorization": f"Bearer {token_a}"}

        # 2. Register Bob
        client.cookies.clear()
        res_b = client.post("/api/auth/register", json={"username": u_bob, "password": "password123"})
        assert res_b.status_code == 200
        data_b = res_b.json()
        token_b = data_b["token"]
        created_uids.append(data_b.get("user_id"))
        headers_b = {"Authorization": f"Bearer {token_b}"}

        # 3. Alice creates workspace 'alice-finance'
        res_ws_a = client.post("/api/workspaces", json={"name": "alice-finance"}, headers=headers_a)
        assert res_ws_a.status_code == 200
        assert res_ws_a.json()["workspace"] == "alice-finance"

        # 4. Alice's workspace list contains 'alice-finance'
        list_a = client.get("/api/workspaces", headers=headers_a)
        assert list_a.status_code == 200
        names_a = [w["name"] for w in list_a.json()["workspaces"]]
        assert "alice-finance" in names_a

        # 5. Bob's workspace list does NOT contain 'alice-finance'
        list_b = client.get("/api/workspaces", headers=headers_b)
        assert list_b.status_code == 200
        names_b = [w["name"] for w in list_b.json()["workspaces"]]
        assert "alice-finance" not in names_b

        # 6. Unauthenticated list is blocked -> 401
        client.cookies.clear()
        list_anon = client.get("/api/workspaces")
        assert list_anon.status_code == 401

        # 7. Bob cannot delete Alice's workspace -> 404 Not Found
        del_forbidden = client.request(
            "DELETE",
            "/api/workspaces",
            json={"workspace": "alice-finance"},
            headers=headers_b,
        )
        assert del_forbidden.status_code == 404

        # 8. Bob can create his own workspace 'bob-rnd'
        res_ws_b = client.post("/api/workspaces", json={"name": "bob-rnd"}, headers=headers_b)
        assert res_ws_b.status_code == 200
        assert res_ws_b.json()["workspace"] == "bob-rnd"

        # 9. Alice does NOT see Bob's workspace
        list_a_2 = client.get("/api/workspaces", headers=headers_a)
        names_a_2 = [w["name"] for w in list_a_2.json()["workspaces"]]
        assert "bob-rnd" not in names_a_2

        # 10. Alice can delete her own workspace
        del_ok = client.request(
            "DELETE",
            "/api/workspaces",
            json={"workspace": "alice-finance"},
            headers=headers_a,
        )
        assert del_ok.status_code == 200
        assert del_ok.json()["status"] == "deleted"

        # 11. Bob's workspace is still intact
        list_b_2 = client.get("/api/workspaces", headers=headers_b)
        names_b_2 = [w["name"] for w in list_b_2.json()["workspaces"]]
        assert "bob-rnd" in names_b_2
    finally:
        with sqlite3.connect(SQLITE_DB_PATH) as conn:
            for uid in created_uids:
                if uid:
                    conn.execute("DELETE FROM users WHERE id = ?", (uid,))
                    conn.execute("DELETE FROM workspaces WHERE user_id = ?", (uid,))
            conn.commit()
        for uid in created_uids:
            if uid:
                shutil.rmtree(UPLOAD_DIR / f"user_{uid}", ignore_errors=True)


def test_same_workspace_name_different_users():
    import uuid
    import sqlite3
    import shutil
    from core.config import SQLITE_DB_PATH, UPLOAD_DIR

    u_charlie = f"charlie_{uuid.uuid4().hex[:6]}"
    u_dave = f"dave_{uuid.uuid4().hex[:6]}"
    created_uids = []

    try:
        # 1. Register User Charlie and User Dave
        client.cookies.clear()
        res_c = client.post("/api/auth/register", json={"username": u_charlie, "password": "password123"})
        assert res_c.status_code == 200
        data_c = res_c.json()
        token_c = data_c["token"]
        created_uids.append(data_c.get("user_id"))
        headers_c = {"Authorization": f"Bearer {token_c}"}

        client.cookies.clear()
        res_d = client.post("/api/auth/register", json={"username": u_dave, "password": "password123"})
        assert res_d.status_code == 200
        data_d = res_d.json()
        token_d = data_d["token"]
        created_uids.append(data_d.get("user_id"))
        headers_d = {"Authorization": f"Bearer {token_d}"}

        # 2. Charlie creates workspace 'shared-topic'
        res_c_ws = client.post("/api/workspaces", json={"name": "shared-topic"}, headers=headers_c)
        assert res_c_ws.status_code == 200

        # 3. Dave also creates workspace 'shared-topic'
        res_d_ws = client.post("/api/workspaces", json={"name": "shared-topic"}, headers=headers_d)
        assert res_d_ws.status_code == 200

        # 4. Both see 'shared-topic' in their respective workspace lists
        names_c = [w["name"] for w in client.get("/api/workspaces", headers=headers_c).json()["workspaces"]]
        names_d = [w["name"] for w in client.get("/api/workspaces", headers=headers_d).json()["workspaces"]]
        assert "shared-topic" in names_c
        assert "shared-topic" in names_d

        # 5. Charlie deletes his 'shared-topic'
        del_c = client.request(
            "DELETE",
            "/api/workspaces",
            json={"workspace": "shared-topic"},
            headers=headers_c,
        )
        assert del_c.status_code == 200

        # 6. Charlie's list does not have 'shared-topic'
        names_c_after = [w["name"] for w in client.get("/api/workspaces", headers=headers_c).json()["workspaces"]]
        assert "shared-topic" not in names_c_after

        # 7. Dave's 'shared-topic' is still intact!
        names_d_after = [w["name"] for w in client.get("/api/workspaces", headers=headers_d).json()["workspaces"]]
        assert "shared-topic" in names_d_after
    finally:
        with sqlite3.connect(SQLITE_DB_PATH) as conn:
            for uid in created_uids:
                if uid:
                    conn.execute("DELETE FROM users WHERE id = ?", (uid,))
                    conn.execute("DELETE FROM workspaces WHERE user_id = ?", (uid,))
            conn.commit()
        for uid in created_uids:
            if uid:
                shutil.rmtree(UPLOAD_DIR / f"user_{uid}", ignore_errors=True)
