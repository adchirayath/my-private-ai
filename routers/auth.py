"""
routers/auth.py - User Authentication & Session Endpoints
---------------------------------------------------------
Endpoints for user registration, login, logout, checking session status,
and managing user profile picture / avatar.
"""

import base64
import io
from pathlib import Path
from typing import Optional

from PIL import Image, ImageOps
from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile

from core.audit import extract_client_ip
from core.auth import (
    register_user,
    login_user,
    logout_user,
    get_optional_current_user,
    get_current_user,
    get_session_token_from_request,
    get_user_count,
)
from core.db import update_user_avatar, get_user_avatar
from core.rbac import (
    get_user_role_names,
    get_user_effective_permissions,
    is_user_super_admin,
    is_user_admin,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class AuthRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=30)
    password: str = Field(..., min_length=4, max_length=128)


@router.post("/register")
def register(auth_req: AuthRequest, request: Request, response: Response):
    """Register a new user account with bcrypt password protection."""
    client_ip = extract_client_ip(request)
    try:
        user_info = register_user(auth_req.username, auth_req.password, ip_address=client_ip)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Registration error occurred.") from exc

    response.set_cookie(
        key="session_token",
        value=user_info["token"],
        max_age=30 * 86400,
        httponly=True,
        samesite="lax",
    )
    return {
        "status": "registered",
        "username": user_info["username"],
        "user_id": user_info["user_id"],
        "token": user_info["token"],
        "avatar": user_info.get("avatar"),
    }


@router.post("/login")
def login(auth_req: AuthRequest, request: Request, response: Response):
    """Authenticate with username and password, returning a session token."""
    client_ip = extract_client_ip(request)
    try:
        user_info = login_user(auth_req.username, auth_req.password, ip_address=client_ip)
    except ValueError as exc:
        # e.g. Deactivated account
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    if not user_info:
        raise HTTPException(
            status_code=401,
            detail="Invalid username or password.",
        )

    response.set_cookie(
        key="session_token",
        value=user_info["token"],
        max_age=30 * 86400,
        httponly=True,
        samesite="lax",
    )
    return {
        "status": "logged_in",
        "username": user_info["username"],
        "user_id": user_info["user_id"],
        "token": user_info["token"],
        "avatar": user_info.get("avatar"),
    }


@router.post("/logout")
def logout(request: Request, response: Response):
    """Terminate the active user session and clear authentication cookies."""
    client_ip = extract_client_ip(request)
    token = get_session_token_from_request(request)
    if token:
        logout_user(token, ip_address=client_ip)

    response.delete_cookie(
        key="session_token",
        httponly=True,
        samesite="lax",
    )
    return {"status": "logged_out"}


@router.get("/me")
def get_current_user_status(request: Request):
    """Check current authentication status and user identity with roles and permissions."""
    user = get_optional_current_user(request)
    if not user:
        return {
            "authenticated": False,
            "total_users": get_user_count(),
        }

    uid = user["id"]
    roles = get_user_role_names(uid)
    permissions = sorted(list(get_user_effective_permissions(uid)))
    is_super = is_user_super_admin(uid)
    is_adm = is_user_admin(uid)

    return {
        "authenticated": True,
        "username": user["username"],
        "user_id": uid,
        "avatar": user.get("avatar"),
        "roles": roles,
        "permissions": permissions,
        "is_super_admin": is_super,
        "is_admin": is_adm,
        "total_users": get_user_count(),
    }


@router.post("/avatar")
async def upload_avatar(
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    """Upload and optimize user profile picture."""
    filename = (file.filename or "").lower()
    content_type = (file.content_type or "").lower()
    valid_exts = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
    ext = Path(filename).suffix.lower()

    if not (content_type.startswith("image/") or ext in valid_exts):
        raise HTTPException(
            status_code=400,
            detail="Invalid file format. Please upload an image file (PNG, JPG, WebP, or GIF).",
        )

    # 5MB size limit
    max_bytes = 5 * 1024 * 1024
    content = await file.read()
    if len(content) > max_bytes:
        raise HTTPException(status_code=400, detail="Image size exceeds the 5 MB limit.")
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    try:
        img = Image.open(io.BytesIO(content))
        img = ImageOps.exif_transpose(img)

        # Center-crop to 1:1 square
        width, height = img.size
        min_dim = min(width, height)
        left = (width - min_dim) // 2
        top = (height - min_dim) // 2
        img = img.crop((left, top, left + min_dim, top + min_dim))

        # Resize to max 256x256 thumbnail
        img.thumbnail((256, 256), Image.Resampling.LANCZOS)

        out_buf = io.BytesIO()
        if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
            img.save(out_buf, format="PNG", optimize=True)
            mime = "image/png"
        else:
            if img.mode != "RGB":
                img = img.convert("RGB")
            img.save(out_buf, format="JPEG", quality=88, optimize=True)
            mime = "image/jpeg"

        avatar_base64 = base64.b64encode(out_buf.getvalue()).decode("ascii")
        avatar_data_uri = f"data:{mime};base64,{avatar_base64}"
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Failed to process image: {str(exc)}",
        ) from exc

    update_user_avatar(user["id"], avatar_data_uri)
    return {
        "status": "ok",
        "avatar": avatar_data_uri,
        "message": "Profile picture updated successfully.",
    }


@router.delete("/avatar")
def remove_avatar(user: dict = Depends(get_current_user)):
    """Remove user's profile picture and revert to default avatar icon."""
    update_user_avatar(user["id"], None)
    return {
        "status": "ok",
        "avatar": None,
        "message": "Profile picture removed successfully.",
    }
