"""
routers/admin.py - Production-Ready Administrative Management API
------------------------------------------------------------------
Implements complete RESTful APIs for the Admin Dashboard:
- Overview metrics and system activity
- User CRUD, activation, password reset, and role assignment
- Dynamic Role management and permission configuration
- Centralized ACL and granular user permission overrides
- Tamper-proof audit logging and export (CSV / JSON)
- System configuration settings
Protected by granular, resource-based permissions (RBAC & ACL).
"""

from typing import Any, Optional
from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import PlainTextResponse

from core.audit import (
    extract_client_ip,
    list_audit_logs,
    export_audit_logs_data,
)
from core.auth import get_current_user
from core.rbac import (
    # Authorization dependencies
    require_permission,
    require_admin,
    # Stats
    get_dashboard_overview_stats,
    # Users
    list_users_admin,
    get_user_admin,
    create_user_admin,
    update_user_admin,
    set_user_active_status,
    reset_user_password_admin,
    delete_user_admin,
    # Roles
    list_roles,
    get_role,
    create_role,
    update_role,
    delete_role,
    # Permissions & ACL
    list_permissions,
    get_permissions_grouped_by_resource,
    get_user_effective_permissions_detailed,
    set_user_permission_override,
    clear_user_permission_override,
    # Settings
    get_all_settings,
    update_settings,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ==============================================================================
# Pydantic Schemas
# ==============================================================================

class CreateUserSchema(BaseModel):
    username: str = Field(..., min_length=3, max_length=30)
    password: str = Field(..., min_length=4, max_length=128)
    role_ids: Optional[list[int]] = None
    is_active: bool = True


class UpdateUserSchema(BaseModel):
    username: Optional[str] = Field(None, min_length=3, max_length=30)
    role_ids: Optional[list[int]] = None
    is_active: Optional[bool] = None


class UserStatusSchema(BaseModel):
    is_active: bool


class ResetPasswordSchema(BaseModel):
    new_password: str = Field(..., min_length=4, max_length=128)


class CreateRoleSchema(BaseModel):
    name: str = Field(..., min_length=2, max_length=50)
    description: str = Field("", max_length=255)
    permission_ids: Optional[list[int]] = None


class UpdateRoleSchema(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=50)
    description: Optional[str] = Field(None, max_length=255)
    permission_ids: Optional[list[int]] = None


class PermissionOverrideItem(BaseModel):
    permission_id: int
    granted: int = Field(..., ge=0, le=1)  # 1 = allow, 0 = deny


class PermissionOverridesSchema(BaseModel):
    overrides: list[PermissionOverrideItem]


class UpdateSettingsSchema(BaseModel):
    settings: dict[str, str]


# ==============================================================================
# 1. Admin Dashboard Overview
# ==============================================================================

@router.get("/overview")
def get_overview(
    current_user: dict = Depends(require_permission("dashboard", "view")),
):
    """
    Retrieve comprehensive administrative dashboard metrics:
    - User totals (active vs inactive)
    - Roles breakdown (system vs custom)
    - Registered permissions count
    - Document, conversation, and vector chunk counts
    - Recent user activities stream
    - Recent authentication events
    """
    stats = get_dashboard_overview_stats()
    return {
        "status": "ok",
        "overview": stats,
    }


# ==============================================================================
# 2. User Management Endpoints
# ==============================================================================

@router.get("/users")
def get_users_list(
    search: Optional[str] = Query(None),
    role: Optional[str] = Query(None),
    status: Optional[str] = Query(None),  # "all", "active", "inactive"
    sort_by: str = Query("created_at"),
    order: str = Query("desc"),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    current_user: dict = Depends(require_permission("users", "view")),
):
    """Search, filter, sort, and paginate user directory."""
    result = list_users_admin(
        search=search,
        role=role,
        status=status,
        sort_by=sort_by,
        order=order,
        page=page,
        limit=limit,
    )
    return result


@router.post("/users")
def create_new_user(
    body: CreateUserSchema,
    request: Request,
    current_user: dict = Depends(require_permission("users", "create")),
):
    """Create a new user with specified roles and status."""
    client_ip = extract_client_ip(request)
    try:
        new_user = create_user_admin(
            username=body.username,
            password=body.password,
            role_ids=body.role_ids,
            is_active=body.is_active,
            actor_id=current_user["id"],
            actor_username=current_user["username"],
            ip_address=client_ip,
        )
        return {
            "status": "created",
            "user": new_user,
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/users/{user_id}")
def get_user_details(
    user_id: int,
    current_user: dict = Depends(require_permission("users", "view")),
):
    """Retrieve detailed user profile, assigned roles, and document/chat counts."""
    user = get_user_admin(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    return {"user": user}


@router.put("/users/{user_id}")
def update_user_details(
    user_id: int,
    body: UpdateUserSchema,
    request: Request,
    current_user: dict = Depends(require_permission("users", "edit")),
):
    """Update user username, role assignments, or active status."""
    client_ip = extract_client_ip(request)
    try:
        updated = update_user_admin(
            user_id=user_id,
            username=body.username,
            role_ids=body.role_ids,
            is_active=body.is_active,
            actor_id=current_user["id"],
            actor_username=current_user["username"],
            ip_address=client_ip,
        )
        return {
            "status": "updated",
            "user": updated,
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/users/{user_id}/status")
def toggle_user_status(
    user_id: int,
    body: UserStatusSchema,
    request: Request,
    current_user: dict = Depends(require_permission("users", "manage")),
):
    """
    Activate or deactivate a user account.
    Prevents administrators from deactivating themselves.
    """
    client_ip = extract_client_ip(request)
    try:
        set_user_active_status(
            user_id=user_id,
            is_active=body.is_active,
            actor_id=current_user["id"],
            actor_username=current_user["username"],
            ip_address=client_ip,
        )
        return {
            "status": "updated",
            "user_id": user_id,
            "is_active": body.is_active,
            "message": f"User account has been {'activated' if body.is_active else 'deactivated'}.",
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/users/{user_id}/reset-password")
def reset_password(
    user_id: int,
    body: ResetPasswordSchema,
    request: Request,
    current_user: dict = Depends(require_permission("users", "manage")),
):
    """Reset user password and invalidate their active sessions."""
    client_ip = extract_client_ip(request)
    try:
        reset_user_password_admin(
            user_id=user_id,
            new_password=body.new_password,
            actor_id=current_user["id"],
            actor_username=current_user["username"],
            ip_address=client_ip,
        )
        return {
            "status": "success",
            "message": "User password has been successfully reset.",
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/users/{user_id}")
def delete_user(
    user_id: int,
    request: Request,
    current_user: dict = Depends(require_permission("users", "delete")),
):
    """
    Completely remove a user account.
    Prevents self-deletion and deletion of the last remaining Super Admin.
    """
    client_ip = extract_client_ip(request)
    try:
        delete_user_admin(
            user_id=user_id,
            actor_id=current_user["id"],
            actor_username=current_user["username"],
            ip_address=client_ip,
        )
        return {
            "status": "deleted",
            "user_id": user_id,
            "message": "User account successfully deleted.",
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ==============================================================================
# 3. Role Management Endpoints
# ==============================================================================

@router.get("/roles")
def get_roles_list(
    search: Optional[str] = Query(None),
    current_user: dict = Depends(require_permission("roles", "view")),
):
    """List all system and custom roles with user & permission counts."""
    roles = list_roles(search=search)
    return {"roles": roles}


@router.post("/roles")
def create_new_role(
    body: CreateRoleSchema,
    request: Request,
    current_user: dict = Depends(require_permission("roles", "create")),
):
    """Create a new custom role with optional permissions."""
    client_ip = extract_client_ip(request)
    try:
        role = create_role(
            name=body.name,
            description=body.description,
            permission_ids=body.permission_ids,
            is_system=False,
            actor_id=current_user["id"],
            actor_username=current_user["username"],
            ip_address=client_ip,
        )
        return {
            "status": "created",
            "role": role,
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/roles/{role_id}")
def get_role_details(
    role_id: int,
    current_user: dict = Depends(require_permission("roles", "view")),
):
    """Get role details, associated permissions, and assigned users."""
    role = get_role(role_id)
    if not role:
        raise HTTPException(status_code=404, detail="Role not found.")
    return {"role": role}


@router.put("/roles/{role_id}")
def update_role_details(
    role_id: int,
    body: UpdateRoleSchema,
    request: Request,
    current_user: dict = Depends(require_permission("roles", "edit")),
):
    """Update role name, description, and assign/unassign permissions."""
    client_ip = extract_client_ip(request)
    try:
        role = update_role(
            role_id=role_id,
            name=body.name,
            description=body.description,
            permission_ids=body.permission_ids,
            actor_id=current_user["id"],
            actor_username=current_user["username"],
            ip_address=client_ip,
        )
        return {
            "status": "updated",
            "role": role,
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/roles/{role_id}")
def delete_role_endpoint(
    role_id: int,
    request: Request,
    current_user: dict = Depends(require_permission("roles", "delete")),
):
    """
    Delete custom role.
    Protected system roles cannot be deleted.
    Roles with assigned users cannot be deleted.
    """
    client_ip = extract_client_ip(request)
    try:
        delete_role(
            role_id=role_id,
            actor_id=current_user["id"],
            actor_username=current_user["username"],
            ip_address=client_ip,
        )
        return {
            "status": "deleted",
            "role_id": role_id,
            "message": "Role successfully deleted.",
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ==============================================================================
# 4. Centralized Permissions & Granular ACL Overrides
# ==============================================================================

@router.get("/permissions")
def get_permissions_list(
    current_user: dict = Depends(require_permission("permissions", "view")),
):
    """Retrieve all system permissions flat and grouped by module."""
    flat = list_permissions()
    grouped = get_permissions_grouped_by_resource()
    return {
        "permissions": flat,
        "grouped": grouped,
    }


@router.get("/users/{user_id}/permissions")
def get_user_permissions_breakdown(
    user_id: int,
    current_user: dict = Depends(require_permission("permissions", "view")),
):
    """
    Inspect a user's effective permissions, showing whether each permission
    originates from a role, a direct allow override, or a direct deny override.
    """
    perms = get_user_effective_permissions_detailed(user_id)
    return {
        "user_id": user_id,
        "permissions": perms,
    }


@router.put("/users/{user_id}/permissions")
def set_user_permission_overrides(
    user_id: int,
    body: PermissionOverridesSchema,
    request: Request,
    current_user: dict = Depends(require_permission("permissions", "manage")),
):
    """Configure individual ACL overrides (grant or revoke) for a specific user."""
    client_ip = extract_client_ip(request)
    try:
        for item in body.overrides:
            set_user_permission_override(
                user_id=user_id,
                permission_id=item.permission_id,
                granted=item.granted,
                actor_id=current_user["id"],
                actor_username=current_user["username"],
                ip_address=client_ip,
            )
        updated_perms = get_user_effective_permissions_detailed(user_id)
        return {
            "status": "updated",
            "user_id": user_id,
            "permissions": updated_perms,
            "message": f"Applied {len(body.overrides)} permission override(s).",
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/users/{user_id}/permissions/{permission_id}")
def clear_single_override(
    user_id: int,
    permission_id: int,
    request: Request,
    current_user: dict = Depends(require_permission("permissions", "manage")),
):
    """Remove a user override, reverting back to role-assigned permission status."""
    client_ip = extract_client_ip(request)
    clear_user_permission_override(
        user_id=user_id,
        permission_id=permission_id,
        actor_id=current_user["id"],
        actor_username=current_user["username"],
        ip_address=client_ip,
    )
    return {
        "status": "cleared",
        "user_id": user_id,
        "permission_id": permission_id,
        "message": "Permission override cleared.",
    }


# ==============================================================================
# 5. Audit Logs Endpoints (Read-Only)
# ==============================================================================

@router.get("/audit-logs")
def get_audit_logs(
    search: Optional[str] = Query(None),
    user_id: Optional[int] = Query(None),
    action: Optional[str] = Query(None),
    resource: Optional[str] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    current_user: dict = Depends(require_permission("audit_logs", "view")),
):
    """
    Query audit trail entries with filtering.
    Audit records are completely immutable and read-only.
    """
    result = list_audit_logs(
        search=search,
        user_id=user_id,
        action=action,
        resource=resource,
        start_date=start_date,
        end_date=end_date,
        page=page,
        limit=limit,
    )
    return result


@router.get("/audit-logs/export")
def export_audit_logs(
    format: str = Query("json"),  # "json" or "csv"
    current_user: dict = Depends(require_permission("audit_logs", "export")),
):
    """Export the audit trail as structured JSON or CSV format."""
    content, media_type = export_audit_logs_data(format_type=format)
    filename = f"audit_logs_export.{format.lower()}"
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ==============================================================================
# 6. System Settings Endpoints
# ==============================================================================

@router.get("/settings")
def get_settings(
    current_user: dict = Depends(require_permission("settings", "view")),
):
    """Retrieve current system configuration settings."""
    settings = get_all_settings()
    return {"settings": settings}


@router.put("/settings")
def save_settings(
    body: UpdateSettingsSchema,
    request: Request,
    current_user: dict = Depends(require_permission("settings", "edit")),
):
    """Update system settings with audit tracking."""
    client_ip = extract_client_ip(request)
    updated = update_settings(
        settings_dict=body.settings,
        actor_id=current_user["id"],
        actor_username=current_user["username"],
        ip_address=client_ip,
    )
    return {
        "status": "updated",
        "settings": updated,
        "message": "System settings updated successfully.",
    }
