"""
tests/test_admin_api.py - Complete API Integration Tests for Admin Dashboard
"""

import tempfile
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from app import app
from core import db, auth, rbac, audit


@pytest.fixture
def client_with_rbac(monkeypatch):
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        test_db = Path(tmpdir) / "test_admin_api.db"
        monkeypatch.setattr(db, "SQLITE_DB_PATH", test_db)
        monkeypatch.setattr(auth, "SQLITE_DB_PATH", test_db)
        monkeypatch.setattr(rbac, "SQLITE_DB_PATH", test_db)
        monkeypatch.setattr(audit, "SQLITE_DB_PATH", test_db)
        db.init_sqlite_db()
        client = TestClient(app)
        yield client


def test_admin_api_authorization_gates(client_with_rbac):
    client = client_with_rbac

    # 1. Unauthenticated request -> 401
    res = client.get("/api/admin/overview")
    assert res.status_code == 401

    # 2. Register first user (Super Admin)
    sa = client.post("/api/auth/register", json={"username": "superadmin", "password": "password123"}).json()
    sa_token = sa["token"]

    # 3. Register second user (Regular User)
    reg_user = client.post("/api/auth/register", json={"username": "norman", "password": "password123"}).json()
    reg_token = reg_user["token"]

    # 4. Super Admin can access overview -> 200
    sa_res = client.get("/api/admin/overview", headers={"Authorization": f"Bearer {sa_token}"})
    assert sa_res.status_code == 200
    ov = sa_res.json()["overview"]
    assert ov["users"]["total"] == 2
    assert ov["users"]["active"] == 2
    assert ov["roles"]["total"] >= 5

    # 5. Regular User without admin permissions gets 403 Forbidden
    reg_res = client.get("/api/admin/overview", headers={"Authorization": f"Bearer {reg_token}"})
    assert reg_res.status_code == 403
    assert "Access denied" in reg_res.json()["detail"]


def test_admin_user_crud_and_status(client_with_rbac):
    client = client_with_rbac

    # First user: Super Admin
    sa = client.post("/api/auth/register", json={"username": "admin1", "password": "password123"}).json()
    sa_token = sa["token"]
    headers = {"Authorization": f"Bearer {sa_token}"}

    # 1. Create a new user via Admin API
    roles_res = client.get("/api/admin/roles", headers=headers).json()
    user_role_id = next(r["id"] for r in roles_res["roles"] if r["name"] == "User")

    create_res = client.post(
        "/api/admin/users",
        json={"username": "danielle", "password": "securepass123", "role_ids": [user_role_id], "is_active": True},
        headers=headers,
    )
    assert create_res.status_code == 200
    danielle_id = create_res.json()["user"]["id"]

    # 2. List users with pagination and search
    users_list = client.get("/api/admin/users?search=danielle", headers=headers).json()
    assert users_list["total"] == 1
    assert users_list["users"][0]["username"] == "danielle"

    # 3. Get single user details
    user_det = client.get(f"/api/admin/users/{danielle_id}", headers=headers).json()
    assert user_det["user"]["username"] == "danielle"

    # 4. Reset user password
    pw_res = client.post(
        f"/api/admin/users/{danielle_id}/reset-password",
        json={"new_password": "brandnewpassword"},
        headers=headers,
    )
    assert pw_res.status_code == 200

    # 5. Toggle user active status to inactive
    status_res = client.post(
        f"/api/admin/users/{danielle_id}/status",
        json={"is_active": False},
        headers=headers,
    )
    assert status_res.status_code == 200
    assert status_res.json()["is_active"] is False

    # Inactive user cannot log in
    login_attempt = client.post("/api/auth/login", json={"username": "danielle", "password": "brandnewpassword"})
    assert login_attempt.status_code == 403

    # Reactivate
    client.post(f"/api/admin/users/{danielle_id}/status", json={"is_active": True}, headers=headers)
    login_attempt2 = client.post("/api/auth/login", json={"username": "danielle", "password": "brandnewpassword"})
    assert login_attempt2.status_code == 200

    # 6. Admin cannot deactivate self
    self_status = client.post(
        f"/api/admin/users/{sa['user_id']}/status",
        json={"is_active": False},
        headers=headers,
    )
    assert self_status.status_code == 400
    assert "cannot deactivate their own account" in self_status.json()["detail"]

    # 7. Admin cannot delete self
    self_del = client.delete(f"/api/admin/users/{sa['user_id']}", headers=headers)
    assert self_del.status_code == 400
    assert "cannot delete their own account" in self_del.json()["detail"]

    # 8. Delete user Danielle
    del_res = client.delete(f"/api/admin/users/{danielle_id}", headers=headers)
    assert del_res.status_code == 200
    assert del_res.json()["status"] == "deleted"


def test_admin_roles_and_permissions(client_with_rbac):
    client = client_with_rbac
    sa = client.post("/api/auth/register", json={"username": "superadmin", "password": "password123"}).json()
    headers = {"Authorization": f"Bearer {sa['token']}"}

    # 1. Fetch permissions catalog
    perms_res = client.get("/api/admin/permissions", headers=headers)
    assert perms_res.status_code == 200
    data = perms_res.json()
    assert "dashboard" in data["grouped"]
    assert "users" in data["grouped"]

    # 2. Create custom role
    perm_ids = [data["permissions"][0]["id"], data["permissions"][1]["id"]]
    create_role_res = client.post(
        "/api/admin/roles",
        json={"name": "SupportAgent", "description": "Support helpdesk", "permission_ids": perm_ids},
        headers=headers,
    )
    assert create_role_res.status_code == 200
    role_id = create_role_res.json()["role"]["id"]

    # 3. Update custom role
    update_role_res = client.put(
        f"/api/admin/roles/{role_id}",
        json={"name": "SupportAgentSenior", "description": "Updated description", "permission_ids": perm_ids},
        headers=headers,
    )
    assert update_role_res.status_code == 200
    assert update_role_res.json()["role"]["name"] == "SupportAgentSenior"

    # 4. System role cannot be deleted
    roles_list = client.get("/api/admin/roles", headers=headers).json()["roles"]
    viewer_role = next(r for r in roles_list if r["name"] == "Viewer")
    del_sys = client.delete(f"/api/admin/roles/{viewer_role['id']}", headers=headers)
    assert del_sys.status_code == 400
    assert "Protected system role" in del_sys.json()["detail"]

    # 5. Delete custom role
    del_custom = client.delete(f"/api/admin/roles/{role_id}", headers=headers)
    assert del_custom.status_code == 200


def test_audit_logs_and_settings_api(client_with_rbac):
    client = client_with_rbac
    sa = client.post("/api/auth/register", json={"username": "sysadmin", "password": "password123"}).json()
    headers = {"Authorization": f"Bearer {sa['token']}"}

    # 1. Get audit logs
    logs_res = client.get("/api/admin/audit-logs", headers=headers)
    assert logs_res.status_code == 200
    logs = logs_res.json()
    assert logs["total"] >= 1

    # 2. Export audit logs
    export_json = client.get("/api/admin/audit-logs/export?format=json", headers=headers)
    assert export_json.status_code == 200
    assert "sysadmin" in export_json.text

    export_csv = client.get("/api/admin/audit-logs/export?format=csv", headers=headers)
    assert export_csv.status_code == 200
    assert "Timestamp" in export_csv.text

    # 3. System settings get & update
    settings_res = client.get("/api/admin/settings", headers=headers)
    assert settings_res.status_code == 200
    assert "app_name" in settings_res.json()["settings"]

    update_sett = client.put(
        "/api/admin/settings",
        json={"settings": {"app_name": "Antigravity Secure Cloud", "allow_registration": "true"}},
        headers=headers,
    )
    assert update_sett.status_code == 200
    assert update_sett.json()["settings"]["app_name"]["value"] == "Antigravity Secure Cloud"
