"""
tests/test_rbac.py - Comprehensive Unit Tests for RBAC and ACL Engine
"""

import tempfile
from pathlib import Path
import pytest

from core import db, auth, rbac, audit


@pytest.fixture
def temp_rbac_db(monkeypatch):
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        test_db = Path(tmpdir) / "test_rbac.db"
        monkeypatch.setattr(db, "SQLITE_DB_PATH", test_db)
        monkeypatch.setattr(auth, "SQLITE_DB_PATH", test_db)
        monkeypatch.setattr(rbac, "SQLITE_DB_PATH", test_db)
        monkeypatch.setattr(audit, "SQLITE_DB_PATH", test_db)
        db.init_sqlite_db()
        yield test_db


def test_default_roles_and_permissions_seeded(temp_rbac_db):
    roles = rbac.list_roles()
    role_names = [r["name"] for r in roles]
    assert "Super Admin" in role_names
    assert "Admin" in role_names
    assert "Manager" in role_names
    assert "User" in role_names
    assert "Viewer" in role_names

    perms = rbac.list_permissions()
    assert len(perms) >= 25
    perm_names = [p["name"] for p in perms]
    assert "users:view" in perm_names
    assert "users:create" in perm_names
    assert "roles:manage" in perm_names
    assert "audit_logs:view" in perm_names
    assert "settings:edit" in perm_names


def test_first_user_receives_super_admin(temp_rbac_db):
    user = auth.register_user("superboss", "secret123")
    user_id = user["user_id"]

    roles = rbac.get_user_role_names(user_id)
    assert "Super Admin" in roles
    assert rbac.is_user_super_admin(user_id) is True
    assert rbac.is_user_admin(user_id) is True

    # Super Admin has all permissions
    assert rbac.has_permission(user_id, "users", "delete") is True
    assert rbac.has_permission(user_id, "settings", "manage") is True
    assert rbac.has_permission(user_id, "audit_logs", "export") is True

    # Second user receives default 'User' role
    user2 = auth.register_user("regular_joe", "secret123")
    user2_id = user2["user_id"]
    roles2 = rbac.get_user_role_names(user2_id)
    assert "User" in roles2
    assert "Super Admin" not in roles2
    assert rbac.is_user_super_admin(user2_id) is False

    # Regular user can view documents but cannot delete users or edit settings
    assert rbac.has_permission(user2_id, "documents", "view") is True
    assert rbac.has_permission(user2_id, "users", "delete") is False
    assert rbac.has_permission(user2_id, "settings", "edit") is False


def test_granular_acl_permission_overrides(temp_rbac_db):
    admin_user = auth.register_user("admin1", "pass1234")
    user2 = auth.register_user("worker", "pass1234")
    worker_id = user2["user_id"]

    # worker initially has User role: has documents:view, but does not have users:view
    assert rbac.has_permission(worker_id, "users", "view") is False
    assert rbac.has_permission(worker_id, "documents", "delete") is True

    # Find permission ID for users:view and documents:delete
    perms = {p["name"]: p["id"] for p in rbac.list_permissions()}
    users_view_id = perms["users:view"]
    docs_del_id = perms["documents:delete"]

    # 1. Grant override: worker gets users:view
    rbac.set_user_permission_override(worker_id, users_view_id, granted=1, actor_id=admin_user["user_id"])
    assert rbac.has_permission(worker_id, "users", "view") is True

    # 2. Deny override: revoke documents:delete even though Worker's role includes it
    rbac.set_user_permission_override(worker_id, docs_del_id, granted=0, actor_id=admin_user["user_id"])
    assert rbac.has_permission(worker_id, "documents", "delete") is False

    # 3. Clear deny override: reverts to role permission (documents:delete granted again)
    rbac.clear_user_permission_override(worker_id, docs_del_id, actor_id=admin_user["user_id"])
    assert rbac.has_permission(worker_id, "documents", "delete") is True


def test_user_activation_and_deactivation(temp_rbac_db):
    admin = auth.register_user("mainadmin", "pass1234")
    user = auth.register_user("charlie", "pass1234")
    user_id = user["user_id"]
    token = user["token"]

    # Active user can be looked up by token
    assert auth.get_user_by_token(token) is not None

    # Deactivate user
    rbac.set_user_active_status(user_id, is_active=False, actor_id=admin["user_id"])

    # Deactivated user's token is immediately rejected/cleared
    assert auth.get_user_by_token(token) is None

    # Deactivated user cannot log in
    with pytest.raises(ValueError, match="deactivated"):
        auth.login_user("charlie", "pass1234")

    # Reactivate user
    rbac.set_user_active_status(user_id, is_active=True, actor_id=admin["user_id"])
    login_info = auth.login_user("charlie", "pass1234")
    assert login_info is not None
    assert login_info["username"] == "charlie"


def test_self_protection_rules(temp_rbac_db):
    admin = auth.register_user("admin_self", "pass1234")
    admin_id = admin["user_id"]

    # Admin cannot deactivate self
    with pytest.raises(ValueError, match="cannot deactivate their own account"):
        rbac.set_user_active_status(admin_id, is_active=False, actor_id=admin_id)

    # Admin cannot delete self
    with pytest.raises(ValueError, match="cannot delete their own account"):
        rbac.delete_user_admin(admin_id, actor_id=admin_id)

    # Admin cannot be deleted if they are the only Super Admin
    user2 = auth.register_user("other", "pass1234")
    with pytest.raises(ValueError, match="only Super Admin account"):
        rbac.delete_user_admin(admin_id, actor_id=user2["user_id"])


def test_custom_role_management(temp_rbac_db):
    admin = auth.register_user("root", "pass1234")
    perms = {p["name"]: p["id"] for p in rbac.list_permissions()}

    # Create custom role "Auditor" with audit_logs:view and reports:view
    auditor_perm_ids = [perms["audit_logs:view"], perms["reports:view"]]
    role = rbac.create_role(
        name="Auditor",
        description="Compliance auditor",
        permission_ids=auditor_perm_ids,
        actor_id=admin["user_id"],
    )
    assert role["name"] == "Auditor"
    assert role["is_system"] is False
    assert len(role["permissions"]) == 2

    # Protected system roles cannot be deleted
    roles_by_name = {r["name"]: r["id"] for r in rbac.list_roles()}
    super_admin_id = roles_by_name["Super Admin"]
    with pytest.raises(ValueError, match="Protected system role"):
        rbac.delete_role(super_admin_id, actor_id=admin["user_id"])

    # Delete custom role
    assert rbac.delete_role(role["id"], actor_id=admin["user_id"]) is True


def test_audit_logging_and_export(temp_rbac_db):
    user = auth.register_user("auditee", "pass1234")
    uid = user["user_id"]

    # Verify audit event for registration was captured
    logs = audit.list_audit_logs(user_id=uid)
    assert logs["total"] >= 1
    actions = [l["action"] for l in logs["logs"]]
    assert "user.register" in actions

    # Export logs to JSON and CSV
    json_export, jtype = audit.export_audit_logs_data(format_type="json")
    assert jtype == "application/json"
    assert "user.register" in json_export

    csv_export, ctype = audit.export_audit_logs_data(format_type="csv")
    assert ctype == "text/csv"
    assert "auditee" in csv_export
