from fastapi.testclient import TestClient
from app import app

client = TestClient(app)


def test_get_workspaces():
    response = client.get("/api/workspaces")
    assert response.status_code == 200
    data = response.json()
    assert "workspaces" in data
    names = [w["name"] for w in data["workspaces"]]
    assert "default" in names


def test_create_and_delete_workspace():
    ws_name = "test-automated-workspace"

    # Create
    res_create = client.post("/api/workspaces", json={"name": ws_name})
    assert res_create.status_code == 200
    data_create = res_create.json()
    assert data_create["status"] == "created"
    assert data_create["workspace"] == ws_name

    # Verify present in list
    res_list = client.get("/api/workspaces")
    names = [w["name"] for w in res_list.json()["workspaces"]]
    assert ws_name in names

    # Delete
    res_del = client.request("DELETE", "/api/workspaces", json={"workspace": ws_name})
    assert res_del.status_code == 200
    assert res_del.json()["status"] == "deleted"

    # Verify removed from list
    res_list_after = client.get("/api/workspaces")
    names_after = [w["name"] for w in res_list_after.json()["workspaces"]]
    assert ws_name not in names_after


def test_cannot_delete_default_workspace():
    res = client.request("DELETE", "/api/workspaces", json={"workspace": "default"})
    assert res.status_code == 400
    assert "default" in res.json()["detail"].lower()


def test_create_workspace_invalid_name():
    res = client.post("/api/workspaces", json={"name": "   "})
    assert res.status_code == 400
