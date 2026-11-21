"""
core/rbac.py - Role-Based Access Control (RBAC) & Access Control List (ACL) Layer
--------------------------------------------------------------------------------
Provides dynamic role management, centralized resource-based permissions,
granular user permission overrides, privilege escalation prevention,
and reusable FastAPI authorization dependencies.
"""

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import Depends, HTTPException, Request

import core.config as config_mod
import core.db as db_mod
from core.audit import log_audit_event

SQLITE_DB_PATH = config_mod.SQLITE_DB_PATH


def get_db_path():
    """Retrieve dynamic database path, supporting test monkeypatching."""
    if hasattr(db_mod, "SQLITE_DB_PATH") and db_mod.SQLITE_DB_PATH != config_mod.SQLITE_DB_PATH:
        return db_mod.SQLITE_DB_PATH
    return SQLITE_DB_PATH


# ==============================================================================
# Centralized Permission Registry & Default System Configuration
# ==============================================================================

SYSTEM_MODULES = [
    "dashboard",
    "users",
    "roles",
    "permissions",
    "documents",
    "reports",
    "settings",
    "audit_logs",
]

DEFAULT_PERMISSIONS: list[dict[str, str]] = [
    # Dashboard
    {"name": "dashboard:view", "resource": "dashboard", "action": "view", "description": "View admin overview & operational metrics"},
    # Users
    {"name": "users:view", "resource": "users", "action": "view", "description": "View user accounts and profiles"},
    {"name": "users:create", "resource": "users", "action": "create", "description": "Create new user accounts"},
    {"name": "users:edit", "resource": "users", "action": "edit", "description": "Edit existing user accounts and roles"},
    {"name": "users:delete", "resource": "users", "action": "delete", "description": "Delete user accounts"},
    {"name": "users:manage", "resource": "users", "action": "manage", "description": "Manage user status and reset passwords"},
    # Roles
    {"name": "roles:view", "resource": "roles", "action": "view", "description": "View roles and assigned permissions"},
    {"name": "roles:create", "resource": "roles", "action": "create", "description": "Create new custom roles"},
    {"name": "roles:edit", "resource": "roles", "action": "edit", "description": "Modify role definitions"},
    {"name": "roles:delete", "resource": "roles", "action": "delete", "description": "Delete custom roles"},
    {"name": "roles:manage", "resource": "roles", "action": "manage", "description": "Assign and configure role permissions"},
    # Permissions
    {"name": "permissions:view", "resource": "permissions", "action": "view", "description": "View centralized permission registry"},
    {"name": "permissions:create", "resource": "permissions", "action": "create", "description": "Define new resource permissions"},
    {"name": "permissions:edit", "resource": "permissions", "action": "edit", "description": "Edit permission metadata"},
    {"name": "permissions:delete", "resource": "permissions", "action": "delete", "description": "Remove custom permissions"},
    {"name": "permissions:manage", "resource": "permissions", "action": "manage", "description": "Configure user permission overrides (ACL)"},
    # Documents
    {"name": "documents:view", "resource": "documents", "action": "view", "description": "View indexed workspace documents"},
    {"name": "documents:upload", "resource": "documents", "action": "upload", "description": "Upload and index new files"},
    {"name": "documents:edit", "resource": "documents", "action": "edit", "description": "Edit document metadata and tags"},
    {"name": "documents:delete", "resource": "documents", "action": "delete", "description": "Delete indexed documents and chunks"},
    {"name": "documents:download", "resource": "documents", "action": "download", "description": "Download stored document files"},
    # Reports
    {"name": "reports:view", "resource": "reports", "action": "view", "description": "View system activity and analytics reports"},
    {"name": "reports:export", "resource": "reports", "action": "export", "description": "Export reports and analytics data"},
    # Settings
    {"name": "settings:view", "resource": "settings", "action": "view", "description": "View system configuration settings"},
    {"name": "settings:edit", "resource": "settings", "action": "edit", "description": "Modify system configuration"},
    {"name": "settings:manage", "resource": "settings", "action": "manage", "description": "Manage advanced security and storage policies"},
    # Audit Logs
    {"name": "audit_logs:view", "resource": "audit_logs", "action": "view", "description": "View security and administrative audit logs"},
    {"name": "audit_logs:export", "resource": "audit_logs", "action": "export", "description": "Export audit trail to CSV or JSON"},
]

DEFAULT_ROLES: dict[str, dict[str, Any]] = {
    "Super Admin": {
        "description": "Full system control with root privileges and all permissions",
        "is_system": 1,
        "permissions": [p["name"] for p in DEFAULT_PERMISSIONS],
    },
    "Admin": {
        "description": "Comprehensive administrative control over users, roles, settings, and logs",
        "is_system": 1,
        "permissions": [
            "dashboard:view",
            "users:view", "users:create", "users:edit", "users:delete", "users:manage",
            "roles:view", "roles:create", "roles:edit", "roles:delete", "roles:manage",
            "permissions:view", "permissions:manage",
            "documents:view", "documents:upload", "documents:edit", "documents:delete", "documents:download",
            "reports:view", "reports:export",
            "settings:view", "settings:edit", "settings:manage",
            "audit_logs:view", "audit_logs:export",
        ],
    },
    "Manager": {
        "description": "Workspace manager with user oversight, document management, and analytics",
        "is_system": 1,
        "permissions": [
            "dashboard:view",
            "users:view", "users:create", "users:edit",
            "roles:view",
            "documents:view", "documents:upload", "documents:edit", "documents:download",
            "reports:view", "reports:export",
            "audit_logs:view",
        ],
    },
    "User": {
        "description": "Standard user with private document upload, chat, and download access",
        "is_system": 1,
        "permissions": [
            "documents:view", "documents:upload", "documents:edit", "documents:delete", "documents:download",
        ],
    },
    "Viewer": {
        "description": "Read-only access to view documents and system reports",
        "is_system": 1,
        "permissions": [
            "documents:view", "documents:download",
            "reports:view",
        ],
    },
}

DEFAULT_SYSTEM_SETTINGS: dict[str, dict[str, str]] = {
    "app_name": {"value": "My Private AI", "description": "Application display name"},
    "allow_registration": {"value": "true", "description": "Allow public user self-registration"},
    "default_role": {"value": "User", "description": "Default role assigned to newly registered users"},
    "session_timeout_days": {"value": "30", "description": "Default user session duration in days"},
    "password_min_length": {"value": "4", "description": "Minimum required password character length"},
    "max_file_upload_mb": {"value": "25", "description": "Maximum allowed upload size in megabytes"},
    "maintenance_mode": {"value": "false", "description": "System maintenance mode status"},
}


# ==============================================================================
# Effective Permissions & Centralized ACL Engine
# ==============================================================================

def get_user_role_names(user_id: int) -> list[str]:
    """Retrieve assigned role names for a user."""
    db_path = get_db_path()
    with sqlite3.connect(db_path, timeout=10.0) as conn:
        cursor = conn.execute(
            """
            SELECT r.name
            FROM roles r
            JOIN user_roles ur ON r.id = ur.role_id
            WHERE ur.user_id = ?
            ORDER BY r.name ASC
            """,
            (user_id,),
        )
        return [row[0] for row in cursor.fetchall()]


def is_user_super_admin(user_id: int) -> bool:
    """Check if the user has the Super Admin role."""
    roles = get_user_role_names(user_id)
    return "Super Admin" in roles


def is_user_admin(user_id: int) -> bool:
    """Check if user has administrative capabilities."""
    roles = get_user_role_names(user_id)
    if "Super Admin" in roles or "Admin" in roles:
        return True
    perms = get_user_effective_permissions(user_id)
    return any(p in perms for p in ("users:view", "roles:view", "settings:view", "audit_logs:view"))


def get_user_effective_permissions(user_id: int) -> set[str]:
    """
    Compute the effective permissions for a user:
    1. Collect permissions from all assigned roles.
    2. If user has 'Super Admin' role, user possesses all permissions.
    3. Apply user-specific ACL overrides:
       - granted = 1 grants permission override
       - granted = 0 revokes permission override (explicit deny)
    4. Deny access by default if permission not present.
    """
    db_path = get_db_path()
    with sqlite3.connect(db_path, timeout=10.0) as conn:
        # Check Super Admin
        cur = conn.execute(
            """
            SELECT COUNT(*)
            FROM roles r
            JOIN user_roles ur ON r.id = ur.role_id
            WHERE ur.user_id = ? AND r.name = 'Super Admin'
            """,
            (user_id,),
        )
        if cur.fetchone()[0] > 0:
            # Super Admin has all registered permissions
            all_cur = conn.execute("SELECT name FROM permissions")
            perms = {r[0].lower() for r in all_cur.fetchall()}
            # Still respect explicit user overrides if configured (deny overrides)
            overrides_cur = conn.execute(
                """
                SELECT p.name, up.granted
                FROM user_permissions up
                JOIN permissions p ON up.permission_id = p.id
                WHERE up.user_id = ?
                """,
                (user_id,),
            )
            for pname, granted in overrides_cur.fetchall():
                if granted == 0:
                    perms.discard(pname.lower())
            return perms

        # 1. Base permissions from assigned roles
        cur = conn.execute(
            """
            SELECT DISTINCT p.name
            FROM permissions p
            JOIN role_permissions rp ON p.id = rp.permission_id
            JOIN user_roles ur ON rp.role_id = ur.role_id
            WHERE ur.user_id = ?
            """,
            (user_id,),
        )
        effective: set[str] = {row[0].lower() for row in cur.fetchall()}

        # 2. Granular ACL overrides (user_permissions)
        cur = conn.execute(
            """
            SELECT p.name, up.granted
            FROM user_permissions up
            JOIN permissions p ON up.permission_id = p.id
            WHERE up.user_id = ?
            """,
            (user_id,),
        )
        for perm_name, granted in cur.fetchall():
            p_clean = perm_name.lower()
            if granted == 1:
                effective.add(p_clean)
            elif granted == 0:
                effective.discard(p_clean)

        return effective


def has_permission(user_id: int, resource: str, action: str) -> bool:
    """
    Check if a user is authorized for a specific resource:action.
    Follows least-privilege: access is denied by default unless granted.
    """
    perm_target = f"{resource.strip().lower()}:{action.strip().lower()}"
    effective = get_user_effective_permissions(user_id)
    if perm_target in effective:
        return True
    # Resource wildcard support (e.g. users:*)
    if f"{resource.strip().lower()}:*" in effective or "*:*" in effective:
        return True
    return False


def get_user_effective_permissions_detailed(user_id: int) -> list[dict[str, Any]]:
    """
    Detailed audit of a user's permissions, showing which role granted each permission
    or if it originates from a direct override.
    """
    db_path = get_db_path()
    with sqlite3.connect(db_path, timeout=10.0) as conn:
        # All permissions
        all_perms_cur = conn.execute("SELECT id, name, resource, action, description FROM permissions ORDER BY resource, action")
        all_perms = all_perms_cur.fetchall()

        # Roles assigned to user
        roles_cur = conn.execute(
            """
            SELECT r.id, r.name
            FROM roles r
            JOIN user_roles ur ON r.id = ur.role_id
            WHERE ur.user_id = ?
            """,
            (user_id,),
        )
        user_roles = {r[0]: r[1] for r in roles_cur.fetchall()}
        is_super = "Super Admin" in user_roles.values()

        # Role permissions map: perm_id -> list of role names
        role_perms_cur = conn.execute(
            """
            SELECT rp.permission_id, r.name
            FROM role_permissions rp
            JOIN roles r ON rp.role_id = r.id
            JOIN user_roles ur ON r.id = ur.role_id
            WHERE ur.user_id = ?
            """,
            (user_id,),
        )
        perm_granted_by_roles: dict[int, list[str]] = {}
        for pid, rname in role_perms_cur.fetchall():
            perm_granted_by_roles.setdefault(pid, []).append(rname)

        # Direct overrides: perm_id -> granted (1 or 0)
        overrides_cur = conn.execute(
            "SELECT permission_id, granted FROM user_permissions WHERE user_id = ?",
            (user_id,),
        )
        user_overrides = dict(overrides_cur.fetchall())

        result = []
        for p in all_perms:
            pid, pname, resource, action, desc = p
            has_role_grant = is_super or (pid in perm_granted_by_roles)
            override = user_overrides.get(pid)  # None, 1, or 0

            if override == 1:
                effective_status = True
                source = "Direct Grant (Override)"
            elif override == 0:
                effective_status = False
                source = "Direct Deny (Override)"
            elif has_role_grant:
                effective_status = True
                source = f"Role: {', '.join(perm_granted_by_roles.get(pid, ['Super Admin']))}"
            else:
                effective_status = False
                source = "Not Granted"

            result.append({
                "id": pid,
                "name": pname,
                "resource": resource,
                "action": action,
                "description": desc,
                "effective": effective_status,
                "override": override,  # None, 1, or 0
                "source": source,
                "roles": perm_granted_by_roles.get(pid, ["Super Admin"] if is_super else []),
            })
        return result


# ==============================================================================
# Reusable Authorization Dependencies (FastAPI)
# ==============================================================================

def require_permission(resource: str, action: str):
    """
    FastAPI dependency factory enforcing granular permission authorization.
    Rejects unauthorized requests with HTTP 403 Forbidden.
    """
    from core.auth import get_current_user

    def dependency(current_user: dict = Depends(get_current_user)):
        user_id = current_user["id"]
        if not has_permission(user_id, resource, action):
            raise HTTPException(
                status_code=403,
                detail=f"Access denied. Required permission: '{resource}:{action}'.",
            )
        return current_user

    return dependency


def require_admin():
    """
    FastAPI dependency ensuring caller possesses administrative access.
    """
    from core.auth import get_current_user

    def dependency(current_user: dict = Depends(get_current_user)):
        user_id = current_user["id"]
        if not is_user_admin(user_id):
            raise HTTPException(
                status_code=403,
                detail="Access denied. Administrative privileges required.",
            )
        return current_user

    return dependency


# ==============================================================================
# Role Management CRUD
# ==============================================================================

def list_roles(search: Optional[str] = None) -> list[dict[str, Any]]:
    """List all roles with associated user counts and permission counts."""
    db_path = get_db_path()
    with sqlite3.connect(db_path, timeout=10.0) as conn:
        where_sql = ""
        params: list[Any] = []
        if search:
            where_sql = "WHERE r.name LIKE ? OR r.description LIKE ?"
            s = f"%{search.strip()}%"
            params = [s, s]

        query = f"""
            SELECT 
                r.id,
                r.name,
                r.description,
                r.is_system,
                r.created_at,
                r.updated_at,
                (SELECT COUNT(*) FROM user_roles ur WHERE ur.role_id = r.id) as user_count,
                (SELECT COUNT(*) FROM role_permissions rp WHERE rp.role_id = r.id) as perm_count
            FROM roles r
            {where_sql}
            ORDER BY r.is_system DESC, r.name ASC
        """
        cursor = conn.execute(query, params)
        roles = []
        for row in cursor.fetchall():
            roles.append({
                "id": row[0],
                "name": row[1],
                "description": row[2] or "",
                "is_system": bool(row[3]),
                "created_at": row[4],
                "updated_at": row[5],
                "user_count": row[6],
                "permission_count": row[7],
            })
        return roles


def get_role(role_id: int) -> Optional[dict[str, Any]]:
    """Get single role details, including assigned permissions and member users."""
    db_path = get_db_path()
    with sqlite3.connect(db_path, timeout=10.0) as conn:
        cursor = conn.execute(
            """
            SELECT id, name, description, is_system, created_at, updated_at
            FROM roles
            WHERE id = ?
            """,
            (role_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None

        # Permissions assigned to role
        perm_cur = conn.execute(
            """
            SELECT p.id, p.name, p.resource, p.action, p.description
            FROM permissions p
            JOIN role_permissions rp ON p.id = rp.permission_id
            WHERE rp.role_id = ?
            ORDER BY p.resource, p.action
            """,
            (role_id,),
        )
        permissions = [
            {"id": p[0], "name": p[1], "resource": p[2], "action": p[3], "description": p[4]}
            for p in perm_cur.fetchall()
        ]

        # Users assigned to role
        users_cur = conn.execute(
            """
            SELECT u.id, u.username, u.is_active
            FROM users u
            JOIN user_roles ur ON u.id = ur.user_id
            WHERE ur.role_id = ?
            ORDER BY u.username ASC
            """,
            (role_id,),
        )
        users = [{"id": u[0], "username": u[1], "is_active": bool(u[2])} for u in users_cur.fetchall()]

        return {
            "id": row[0],
            "name": row[1],
            "description": row[2] or "",
            "is_system": bool(row[3]),
            "created_at": row[4],
            "updated_at": row[5],
            "permissions": permissions,
            "permission_ids": [p["id"] for p in permissions],
            "users": users,
            "user_count": len(users),
        }


def create_role(
    name: str,
    description: str = "",
    permission_ids: Optional[list[int]] = None,
    is_system: bool = False,
    actor_id: Optional[int] = None,
    actor_username: Optional[str] = None,
    ip_address: Optional[str] = None,
) -> dict[str, Any]:
    """Create a new role with optional permission assignments."""
    clean_name = (name or "").strip()
    if not clean_name or len(clean_name) < 2 or len(clean_name) > 50:
        raise ValueError("Role name must be between 2 and 50 characters long.")

    db_path = get_db_path()
    now_iso = datetime.now(timezone.utc).isoformat()

    with sqlite3.connect(db_path, timeout=10.0) as conn:
        conn.execute("PRAGMA foreign_keys = ON;")
        cur = conn.execute("SELECT id FROM roles WHERE name = ? COLLATE NOCASE", (clean_name,))
        if cur.fetchone() is not None:
            raise ValueError(f"Role '{clean_name}' already exists.")

        cur = conn.execute(
            """
            INSERT INTO roles (name, description, is_system, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (clean_name, description.strip(), 1 if is_system else 0, now_iso, now_iso),
        )
        role_id = cur.lastrowid

        if permission_ids:
            # Validate permissions exist
            valid_pids = set()
            for pid in permission_ids:
                if isinstance(pid, int):
                    valid_pids.add(pid)
            if valid_pids:
                rows = [(role_id, pid) for pid in valid_pids]
                conn.executemany(
                    "INSERT OR IGNORE INTO role_permissions (role_id, permission_id) VALUES (?, ?)",
                    rows,
                )
        conn.commit()

    log_audit_event(
        action="role.create",
        resource="roles",
        user_id=actor_id,
        username=actor_username,
        details={"role_id": role_id, "role_name": clean_name, "permission_count": len(permission_ids or [])},
        ip_address=ip_address,
    )

    return get_role(role_id)  # type: ignore


def update_role(
    role_id: int,
    name: Optional[str] = None,
    description: Optional[str] = None,
    permission_ids: Optional[list[int]] = None,
    actor_id: Optional[int] = None,
    actor_username: Optional[str] = None,
    ip_address: Optional[str] = None,
) -> dict[str, Any]:
    """Update role details and reassign permissions."""
    db_path = get_db_path()
    now_iso = datetime.now(timezone.utc).isoformat()

    with sqlite3.connect(db_path, timeout=10.0) as conn:
        conn.execute("PRAGMA foreign_keys = ON;")
        cur = conn.execute("SELECT id, name, is_system FROM roles WHERE id = ?", (role_id,))
        existing = cur.fetchone()
        if not existing:
            raise ValueError("Role not found.")

        old_name, is_system = existing[1], bool(existing[2])

        # Prevent renaming Super Admin
        if old_name == "Super Admin" and name and name.strip() != "Super Admin":
            raise ValueError("The 'Super Admin' role cannot be renamed.")

        target_name = (name or old_name).strip()
        if not target_name:
            raise ValueError("Role name cannot be empty.")

        # Check duplicate name
        if target_name.lower() != old_name.lower():
            dup_cur = conn.execute("SELECT id FROM roles WHERE name = ? COLLATE NOCASE AND id != ?", (target_name, role_id))
            if dup_cur.fetchone():
                raise ValueError(f"Role '{target_name}' already exists.")

        desc = description if description is not None else ""
        conn.execute(
            "UPDATE roles SET name = ?, description = ?, updated_at = ? WHERE id = ?",
            (target_name, desc, now_iso, role_id),
        )

        if permission_ids is not None:
            # Special protection: Super Admin must always retain all permissions
            if old_name == "Super Admin":
                all_pids_cur = conn.execute("SELECT id FROM permissions")
                permission_ids = [r[0] for r in all_pids_cur.fetchall()]

            conn.execute("DELETE FROM role_permissions WHERE role_id = ?", (role_id,))
            valid_pids = [int(p) for p in permission_ids if isinstance(p, (int, str)) and str(p).isdigit()]
            if valid_pids:
                conn.executemany(
                    "INSERT OR IGNORE INTO role_permissions (role_id, permission_id) VALUES (?, ?)",
                    [(role_id, pid) for pid in valid_pids],
                )

        conn.commit()

    log_audit_event(
        action="role.update",
        resource="roles",
        user_id=actor_id,
        username=actor_username,
        details={
            "role_id": role_id,
            "role_name": target_name,
            "updated_permissions": len(permission_ids) if permission_ids is not None else None,
        },
        ip_address=ip_address,
    )

    return get_role(role_id)  # type: ignore


def delete_role(
    role_id: int,
    actor_id: Optional[int] = None,
    actor_username: Optional[str] = None,
    ip_address: Optional[str] = None,
) -> bool:
    """
    Delete a custom role.
    System-protected roles cannot be deleted.
    Roles with active user assignments cannot be deleted.
    """
    db_path = get_db_path()
    with sqlite3.connect(db_path, timeout=10.0) as conn:
        conn.execute("PRAGMA foreign_keys = ON;")
        cur = conn.execute("SELECT id, name, is_system FROM roles WHERE id = ?", (role_id,))
        row = cur.fetchone()
        if not row:
            raise ValueError("Role not found.")

        role_name, is_system = row[1], bool(row[2])
        if is_system or role_name in DEFAULT_ROLES:
            raise ValueError(f"Protected system role '{role_name}' cannot be deleted.")

        # Check if users are assigned
        user_count_cur = conn.execute("SELECT COUNT(*) FROM user_roles WHERE role_id = ?", (role_id,))
        count = user_count_cur.fetchone()[0]
        if count > 0:
            raise ValueError(f"Cannot delete role '{role_name}' because {count} user(s) are assigned to it.")

        conn.execute("DELETE FROM roles WHERE id = ?", (role_id,))
        conn.commit()

    log_audit_event(
        action="role.delete",
        resource="roles",
        user_id=actor_id,
        username=actor_username,
        details={"role_id": role_id, "role_name": role_name},
        ip_address=ip_address,
    )
    return True


# ==============================================================================
# Centralized Permissions Catalog
# ==============================================================================

def list_permissions(resource: Optional[str] = None) -> list[dict[str, Any]]:
    """List all registered permissions, optionally filtered by module."""
    db_path = get_db_path()
    with sqlite3.connect(db_path, timeout=10.0) as conn:
        if resource:
            cursor = conn.execute(
                """
                SELECT id, name, resource, action, description
                FROM permissions
                WHERE resource = ? COLLATE NOCASE
                ORDER BY action ASC
                """,
                (resource.strip(),),
            )
        else:
            cursor = conn.execute(
                """
                SELECT id, name, resource, action, description
                FROM permissions
                ORDER BY resource ASC, action ASC
                """
            )
        return [
            {"id": r[0], "name": r[1], "resource": r[2], "action": r[3], "description": r[4] or ""}
            for r in cursor.fetchall()
        ]


def get_permissions_grouped_by_resource() -> dict[str, list[dict[str, Any]]]:
    """Retrieve all registered permissions organized by module/resource name."""
    perms = list_permissions()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for p in perms:
        grouped.setdefault(p["resource"], []).append(p)
    return grouped


# ==============================================================================
# User Roles & Granular ACL Overrides
# ==============================================================================

def get_user_roles(user_id: int) -> list[dict[str, Any]]:
    """Get all roles assigned to a user."""
    db_path = get_db_path()
    with sqlite3.connect(db_path, timeout=10.0) as conn:
        cursor = conn.execute(
            """
            SELECT r.id, r.name, r.description, r.is_system
            FROM roles r
            JOIN user_roles ur ON r.id = ur.role_id
            WHERE ur.user_id = ?
            ORDER BY r.name ASC
            """,
            (user_id,),
        )
        return [
            {"id": r[0], "name": r[1], "description": r[2] or "", "is_system": bool(r[3])}
            for r in cursor.fetchall()
        ]


def assign_user_roles(
    user_id: int,
    role_ids: list[int],
    actor_id: Optional[int] = None,
    actor_username: Optional[str] = None,
    ip_address: Optional[str] = None,
) -> None:
    """
    Assign roles to a user with privilege escalation prevention.
    - Only Super Admin can assign or revoke the Super Admin role.
    - A user cannot revoke Super Admin from themselves if they are the only Super Admin.
    """
    db_path = get_db_path()
    with sqlite3.connect(db_path, timeout=10.0) as conn:
        conn.execute("PRAGMA foreign_keys = ON;")

        # Fetch current roles of user
        cur = conn.execute(
            "SELECT r.id, r.name FROM roles r JOIN user_roles ur ON r.id = ur.role_id WHERE ur.user_id = ?",
            (user_id,),
        )
        current_roles = {r[0]: r[1] for r in cur.fetchall()}

        # Fetch role IDs for Super Admin
        sa_cur = conn.execute("SELECT id FROM roles WHERE name = 'Super Admin'")
        sa_row = sa_cur.fetchone()
        sa_role_id = sa_row[0] if sa_row else None

        # Check actor privilege
        actor_is_sa = False
        if actor_id is not None:
            cur_actor_sa = conn.execute(
                """
                SELECT COUNT(*) FROM roles r
                JOIN user_roles ur ON r.id = ur.role_id
                WHERE ur.user_id = ? AND r.name = 'Super Admin'
                """,
                (actor_id,),
            )
            actor_is_sa = cur_actor_sa.fetchone()[0] > 0

        # Privilege escalation check: granting Super Admin
        if sa_role_id in role_ids and sa_role_id not in current_roles:
            if not actor_is_sa:
                raise ValueError("Privilege escalation: Only a Super Admin can assign the Super Admin role.")

        # Self-demotion check: removing last Super Admin
        if sa_role_id in current_roles and sa_role_id not in role_ids:
            if not actor_is_sa:
                raise ValueError("Only a Super Admin can revoke the Super Admin role.")
            # Check count of Super Admins in system
            sa_count_cur = conn.execute(
                """
                SELECT COUNT(DISTINCT ur.user_id)
                FROM user_roles ur
                JOIN users u ON ur.user_id = u.id
                WHERE ur.role_id = ? AND u.is_active = 1
                """,
                (sa_role_id,),
            )
            if sa_count_cur.fetchone()[0] <= 1:
                raise ValueError("Cannot remove Super Admin role from the last active Super Admin.")

        # Apply role changes
        conn.execute("DELETE FROM user_roles WHERE user_id = ?", (user_id,))
        for rid in role_ids:
            conn.execute("INSERT OR IGNORE INTO user_roles (user_id, role_id) VALUES (?, ?)", (user_id, rid))
        conn.commit()

    log_audit_event(
        action="user.roles_update",
        resource="users",
        user_id=actor_id,
        username=actor_username,
        details={"target_user_id": user_id, "assigned_role_ids": role_ids},
        ip_address=ip_address,
    )


def set_user_permission_override(
    user_id: int,
    permission_id: int,
    granted: int,  # 1 for allow, 0 for deny
    actor_id: Optional[int] = None,
    actor_username: Optional[str] = None,
    ip_address: Optional[str] = None,
) -> None:
    """
    Set an individual user permission override (ACL).
    granted=1 explicitly allows; granted=0 explicitly denies.
    """
    if granted not in (0, 1):
        raise ValueError("Granted value must be 1 (allow) or 0 (deny).")

    db_path = get_db_path()
    with sqlite3.connect(db_path, timeout=10.0) as conn:
        conn.execute("PRAGMA foreign_keys = ON;")
        # Verify permission exists
        cur = conn.execute("SELECT name FROM permissions WHERE id = ?", (permission_id,))
        p_row = cur.fetchone()
        if not p_row:
            raise ValueError("Permission not found.")
        perm_name = p_row[0]

        conn.execute(
            """
            INSERT INTO user_permissions (user_id, permission_id, granted)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, permission_id) DO UPDATE SET granted = excluded.granted
            """,
            (user_id, permission_id, granted),
        )
        conn.commit()

    log_audit_event(
        action="acl.override_set",
        resource="permissions",
        user_id=actor_id,
        username=actor_username,
        details={
            "target_user_id": user_id,
            "permission_id": permission_id,
            "permission_name": perm_name,
            "granted": granted,
        },
        ip_address=ip_address,
    )


def clear_user_permission_override(
    user_id: int,
    permission_id: int,
    actor_id: Optional[int] = None,
    actor_username: Optional[str] = None,
    ip_address: Optional[str] = None,
) -> None:
    """Remove a user permission override, reverting to role-based resolution."""
    db_path = get_db_path()
    with sqlite3.connect(db_path, timeout=10.0) as conn:
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute(
            "DELETE FROM user_permissions WHERE user_id = ? AND permission_id = ?",
            (user_id, permission_id),
        )
        conn.commit()

    log_audit_event(
        action="acl.override_cleared",
        resource="permissions",
        user_id=actor_id,
        username=actor_username,
        details={"target_user_id": user_id, "permission_id": permission_id},
        ip_address=ip_address,
    )


# ==============================================================================
# User Administration Operations
# ==============================================================================

def list_users_admin(
    search: Optional[str] = None,
    role: Optional[str] = None,
    status: Optional[str] = None,  # "all", "active", "inactive"
    sort_by: str = "created_at",
    order: str = "desc",
    page: int = 1,
    limit: int = 10,
) -> dict[str, Any]:
    """Query, filter, sort, and paginate user directory for admin dashboard."""
    page = max(1, page)
    limit = max(1, min(100, limit))
    offset = (page - 1) * limit
    db_path = get_db_path()

    where_clauses: list[str] = []
    params: list[Any] = []

    if search:
        s = f"%{search.strip()}%"
        where_clauses.append("u.username LIKE ?")
        params.append(s)

    if status == "active":
        where_clauses.append("u.is_active = 1")
    elif status == "inactive":
        where_clauses.append("u.is_active = 0")

    if role:
        where_clauses.append(
            """
            u.id IN (
                SELECT ur.user_id FROM user_roles ur
                JOIN roles r ON ur.role_id = r.id
                WHERE r.name = ? COLLATE NOCASE
            )
            """
        )
        params.append(role.strip())

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    allowed_sort = {
        "username": "u.username",
        "created_at": "u.created_at",
        "last_login_at": "u.last_login_at",
        "id": "u.id",
    }
    sort_col = allowed_sort.get(sort_by, "u.created_at")
    sort_order = "ASC" if order.lower() == "asc" else "DESC"

    with sqlite3.connect(db_path, timeout=10.0) as conn:
        count_cur = conn.execute(f"SELECT COUNT(*) FROM users u {where_sql}", params)
        total = count_cur.fetchone()[0]

        query = f"""
            SELECT u.id, u.username, u.is_active, u.created_at, u.updated_at, u.last_login_at
            FROM users u
            {where_sql}
            ORDER BY {sort_col} {sort_order}
            LIMIT ? OFFSET ?
        """
        rows = conn.execute(query, params + [limit, offset]).fetchall()

        users = []
        for r in rows:
            uid = r[0]
            # Roles for user
            r_cur = conn.execute(
                "SELECT r.id, r.name, r.is_system FROM roles r JOIN user_roles ur ON r.id = ur.role_id WHERE ur.user_id = ?",
                (uid,),
            )
            user_roles = [{"id": row[0], "name": row[1], "is_system": bool(row[2])} for row in r_cur.fetchall()]

            users.append({
                "id": uid,
                "username": r[1],
                "is_active": bool(r[2]),
                "created_at": r[3],
                "updated_at": r[4],
                "last_login_at": r[5],
                "roles": user_roles,
                "role_names": [ur["name"] for ur in user_roles],
            })

    total_pages = (total + limit - 1) // limit if total > 0 else 1
    return {
        "users": users,
        "total": total,
        "page": page,
        "limit": limit,
        "total_pages": total_pages,
    }


def get_user_admin(user_id: int) -> Optional[dict[str, Any]]:
    """Retrieve complete administrative profile of a user."""
    db_path = get_db_path()
    with sqlite3.connect(db_path, timeout=10.0) as conn:
        cur = conn.execute(
            """
            SELECT id, username, is_active, created_at, updated_at, last_login_at
            FROM users
            WHERE id = ?
            """,
            (user_id,),
        )
        row = cur.fetchone()
        if not row:
            return None

        roles = get_user_roles(user_id)
        effective_perms = get_user_effective_permissions_detailed(user_id)

        # Count active conversations & documents
        doc_count = conn.execute("SELECT COUNT(*) FROM documents WHERE user_id = ?", (user_id,)).fetchone()[0]
        msg_count = conn.execute("SELECT COUNT(*) FROM messages WHERE user_id = ?", (user_id,)).fetchone()[0]

        return {
            "id": row[0],
            "username": row[1],
            "is_active": bool(row[2]),
            "created_at": row[3],
            "updated_at": row[4],
            "last_login_at": row[5],
            "roles": roles,
            "role_names": [r["name"] for r in roles],
            "effective_permissions": effective_perms,
            "document_count": doc_count,
            "message_count": msg_count,
        }


def create_user_admin(
    username: str,
    password: str,
    role_ids: Optional[list[int]] = None,
    is_active: bool = True,
    actor_id: Optional[int] = None,
    actor_username: Optional[str] = None,
    ip_address: Optional[str] = None,
) -> dict[str, Any]:
    """Create a new user with assigned roles and active status."""
    from core.auth import validate_username, validate_password, hash_password

    clean_username = validate_username(username)
    clean_password = validate_password(password)
    pw_hash = hash_password(clean_password)
    now_iso = datetime.now(timezone.utc).isoformat()
    db_path = get_db_path()

    with sqlite3.connect(db_path, timeout=10.0) as conn:
        conn.execute("PRAGMA foreign_keys = ON;")
        dup_cur = conn.execute("SELECT id FROM users WHERE username = ? COLLATE NOCASE", (clean_username,))
        if dup_cur.fetchone() is not None:
            raise ValueError(f"Username '{clean_username}' is already registered.")

        cur = conn.execute(
            """
            INSERT INTO users (username, password_hash, is_active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (clean_username, pw_hash, 1 if is_active else 0, now_iso, now_iso),
        )
        user_id = cur.lastrowid

        # Determine roles to assign
        roles_to_assign = role_ids
        if roles_to_assign is None or len(roles_to_assign) == 0:
            # Default role from settings
            default_role_name = get_setting("default_role", "User")
            r_cur = conn.execute("SELECT id FROM roles WHERE name = ? COLLATE NOCASE", (default_role_name,))
            r_row = r_cur.fetchone()
            if r_row:
                roles_to_assign = [r_row[0]]
            else:
                roles_to_assign = []

        for rid in roles_to_assign:
            conn.execute("INSERT OR IGNORE INTO user_roles (user_id, role_id) VALUES (?, ?)", (user_id, rid))
        conn.commit()

    log_audit_event(
        action="user.create",
        resource="users",
        user_id=actor_id,
        username=actor_username,
        details={
            "created_user_id": user_id,
            "created_username": clean_username,
            "is_active": is_active,
            "role_ids": roles_to_assign,
        },
        ip_address=ip_address,
    )

    return get_user_admin(user_id)  # type: ignore


def update_user_admin(
    user_id: int,
    username: Optional[str] = None,
    role_ids: Optional[list[int]] = None,
    is_active: Optional[bool] = None,
    actor_id: Optional[int] = None,
    actor_username: Optional[str] = None,
    ip_address: Optional[str] = None,
) -> dict[str, Any]:
    """
    Update user profile, status, and assigned roles.
    Includes self-deactivation protection and last Super Admin checks.
    """
    from core.auth import validate_username

    db_path = get_db_path()
    now_iso = datetime.now(timezone.utc).isoformat()

    with sqlite3.connect(db_path, timeout=10.0) as conn:
        conn.execute("PRAGMA foreign_keys = ON;")
        cur = conn.execute("SELECT id, username, is_active FROM users WHERE id = ?", (user_id,))
        user_row = cur.fetchone()
        if not user_row:
            raise ValueError("User not found.")

        old_username, old_active = user_row[1], bool(user_row[2])

        # Self-deactivation prevention
        if actor_id is not None and user_id == actor_id and is_active is False:
            raise ValueError("Administrators cannot deactivate their own account.")

        # Self-deletion/deactivation of the last Super Admin prevention
        if is_active is False:
            if is_user_super_admin(user_id):
                active_sa = conn.execute(
                    """
                    SELECT COUNT(DISTINCT u.id)
                    FROM users u
                    JOIN user_roles ur ON u.id = ur.user_id
                    JOIN roles r ON ur.role_id = r.id
                    WHERE r.name = 'Super Admin' AND u.is_active = 1
                    """
                ).fetchone()[0]
                if active_sa <= 1:
                    raise ValueError("Cannot deactivate the only active Super Admin account.")

        updates = ["updated_at = ?"]
        params: list[Any] = [now_iso]

        if username:
            clean_username = validate_username(username)
            if clean_username.lower() != old_username.lower():
                dup = conn.execute("SELECT id FROM users WHERE username = ? COLLATE NOCASE AND id != ?", (clean_username, user_id)).fetchone()
                if dup:
                    raise ValueError(f"Username '{clean_username}' is already taken.")
            updates.append("username = ?")
            params.append(clean_username)

        if is_active is not None:
            updates.append("is_active = ?")
            params.append(1 if is_active else 0)
            if not is_active:
                # Terminate active sessions immediately
                conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))

        params.append(user_id)
        conn.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = ?", params)
        conn.commit()

    if role_ids is not None:
        assign_user_roles(
            user_id=user_id,
            role_ids=role_ids,
            actor_id=actor_id,
            actor_username=actor_username,
            ip_address=ip_address,
        )

    log_audit_event(
        action="user.update",
        resource="users",
        user_id=actor_id,
        username=actor_username,
        details={
            "target_user_id": user_id,
            "target_username": username or old_username,
            "is_active": is_active,
            "role_ids": role_ids,
        },
        ip_address=ip_address,
    )

    return get_user_admin(user_id)  # type: ignore


def set_user_active_status(
    user_id: int,
    is_active: bool,
    actor_id: Optional[int] = None,
    actor_username: Optional[str] = None,
    ip_address: Optional[str] = None,
) -> None:
    """Activate or deactivate a user account."""
    if actor_id is not None and user_id == actor_id and not is_active:
        raise ValueError("Administrators cannot deactivate their own account.")

    update_user_admin(
        user_id=user_id,
        is_active=is_active,
        actor_id=actor_id,
        actor_username=actor_username,
        ip_address=ip_address,
    )


def reset_user_password_admin(
    user_id: int,
    new_password: str,
    actor_id: Optional[int] = None,
    actor_username: Optional[str] = None,
    ip_address: Optional[str] = None,
) -> None:
    """Reset a user's password from the admin panel."""
    from core.auth import validate_password, hash_password

    clean_pass = validate_password(new_password)
    pw_hash = hash_password(clean_pass)
    db_path = get_db_path()

    with sqlite3.connect(db_path, timeout=10.0) as conn:
        conn.execute("PRAGMA foreign_keys = ON;")
        cur = conn.execute("SELECT username FROM users WHERE id = ?", (user_id,))
        row = cur.fetchone()
        if not row:
            raise ValueError("User not found.")
        username = row[0]

        conn.execute(
            "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
            (pw_hash, datetime.now(timezone.utc).isoformat(), user_id),
        )
        # Clear existing sessions so user is required to sign in with new password
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        conn.commit()

    log_audit_event(
        action="user.password_reset",
        resource="users",
        user_id=actor_id,
        username=actor_username,
        details={"target_user_id": user_id, "target_username": username},
        ip_address=ip_address,
    )


def delete_user_admin(
    user_id: int,
    actor_id: Optional[int] = None,
    actor_username: Optional[str] = None,
    ip_address: Optional[str] = None,
) -> None:
    """
    Delete a user account and purge user sessions, messages, and document records.
    Prevents self-deletion and deleting the last remaining Super Admin.
    """
    if actor_id is not None and user_id == actor_id:
        raise ValueError("Administrators cannot delete their own account.")

    db_path = get_db_path()
    with sqlite3.connect(db_path, timeout=10.0) as conn:
        conn.execute("PRAGMA foreign_keys = ON;")
        cur = conn.execute("SELECT username FROM users WHERE id = ?", (user_id,))
        row = cur.fetchone()
        if not row:
            raise ValueError("User not found.")
        username = row[0]

        # Prevent deleting the last Super Admin
        if is_user_super_admin(user_id):
            sa_count = conn.execute(
                """
                SELECT COUNT(DISTINCT u.id)
                FROM users u
                JOIN user_roles ur ON u.id = ur.user_id
                JOIN roles r ON ur.role_id = r.id
                WHERE r.name = 'Super Admin'
                """
            ).fetchone()[0]
            if sa_count <= 1:
                raise ValueError("Cannot delete the only Super Admin account.")

        # Clean up database records
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM user_roles WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM user_permissions WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM messages WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM documents WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM workspaces WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()

    log_audit_event(
        action="user.delete",
        resource="users",
        user_id=actor_id,
        username=actor_username,
        details={"deleted_user_id": user_id, "deleted_username": username},
        ip_address=ip_address,
    )


# ==============================================================================
# System Settings Management
# ==============================================================================

def get_all_settings() -> dict[str, dict[str, str]]:
    """Retrieve all system settings with metadata."""
    db_path = get_db_path()
    with sqlite3.connect(db_path, timeout=10.0) as conn:
        cursor = conn.execute("SELECT key, value, description, updated_at FROM system_settings ORDER BY key ASC")
        settings: dict[str, dict[str, str]] = {}
        for row in cursor.fetchall():
            settings[row[0]] = {
                "key": row[0],
                "value": row[1],
                "description": row[2] or "",
                "updated_at": row[3],
            }
        # Backfill defaults if any are missing
        for k, v in DEFAULT_SYSTEM_SETTINGS.items():
            if k not in settings:
                settings[k] = {
                    "key": k,
                    "value": v["value"],
                    "description": v["description"],
                    "updated_at": "",
                }
        return settings


def get_setting(key: str, default: str = "") -> str:
    """Retrieve an individual system setting string value."""
    db_path = get_db_path()
    with sqlite3.connect(db_path, timeout=10.0) as conn:
        cursor = conn.execute("SELECT value FROM system_settings WHERE key = ?", (key.strip(),))
        row = cursor.fetchone()
        if row:
            return row[0]
        return DEFAULT_SYSTEM_SETTINGS.get(key, {}).get("value", default)


def update_settings(
    settings_dict: dict[str, str],
    actor_id: Optional[int] = None,
    actor_username: Optional[str] = None,
    ip_address: Optional[str] = None,
) -> dict[str, dict[str, str]]:
    """Batch update system configuration settings."""
    db_path = get_db_path()
    now_iso = datetime.now(timezone.utc).isoformat()

    with sqlite3.connect(db_path, timeout=10.0) as conn:
        for k, v in settings_dict.items():
            clean_k = k.strip()
            clean_v = str(v).strip()
            desc = DEFAULT_SYSTEM_SETTINGS.get(clean_k, {}).get("description", "")
            conn.execute(
                """
                INSERT INTO system_settings (key, value, description, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (clean_k, clean_v, desc, now_iso),
            )
        conn.commit()

    log_audit_event(
        action="settings.update",
        resource="settings",
        user_id=actor_id,
        username=actor_username,
        details={"updated_keys": list(settings_dict.keys())},
        ip_address=ip_address,
    )
    return get_all_settings()


# ==============================================================================
# Overview & Aggregate Dashboard Statistics
# ==============================================================================

def get_dashboard_overview_stats() -> dict[str, Any]:
    """Compute aggregate counts and statistics for the main admin dashboard view."""
    from core.audit import get_recent_activities, get_recent_logins

    db_path = get_db_path()
    with sqlite3.connect(db_path, timeout=10.0) as conn:
        total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        active_users = conn.execute("SELECT COUNT(*) FROM users WHERE is_active = 1").fetchone()[0]
        inactive_users = total_users - active_users

        total_roles = conn.execute("SELECT COUNT(*) FROM roles").fetchone()[0]
        system_roles = conn.execute("SELECT COUNT(*) FROM roles WHERE is_system = 1").fetchone()[0]
        custom_roles = total_roles - system_roles

        total_permissions = conn.execute("SELECT COUNT(*) FROM permissions").fetchone()[0]
        total_documents = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        total_conversations = conn.execute("SELECT COUNT(DISTINCT conversation_id) FROM messages").fetchone()[0]
        total_chunks = conn.execute("SELECT COALESCE(SUM(chunk_count), 0) FROM documents").fetchone()[0]

    return {
        "users": {
            "total": total_users,
            "active": active_users,
            "inactive": inactive_users,
        },
        "roles": {
            "total": total_roles,
            "system": system_roles,
            "custom": custom_roles,
        },
        "permissions": {
            "total": total_permissions,
            "modules": len(SYSTEM_MODULES),
        },
        "content": {
            "total_documents": total_documents,
            "total_conversations": total_conversations,
            "total_chunks": total_chunks,
        },
        "recent_activities": get_recent_activities(limit=8),
        "recent_logins": get_recent_logins(limit=8),
    }
