"""
routers/auth.py - User Authentication & Session Endpoints
---------------------------------------------------------
Endpoints for user registration, login, logout, and checking session status.
"""

from pydantic import BaseModel, Field
from fastapi import APIRouter, HTTPException, Request, Response

from core.auth import (
    register_user,
    login_user,
    logout_user,
    get_optional_current_user,
    get_session_token_from_request,
    get_user_count,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class AuthRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=30)
    password: str = Field(..., min_length=4, max_length=128)


@router.post("/register")
def register(auth_req: AuthRequest, response: Response):
    """Register a new user account with bcrypt password protection."""
    try:
        user_info = register_user(auth_req.username, auth_req.password)
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
    }


@router.post("/login")
def login(auth_req: AuthRequest, response: Response):
    """Authenticate with username and password, returning a session token."""
    user_info = login_user(auth_req.username, auth_req.password)
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
    }


@router.post("/logout")
def logout(request: Request, response: Response):
    """Terminate the active user session and clear authentication cookies."""
    token = get_session_token_from_request(request)
    if token:
        logout_user(token)

    response.delete_cookie(
        key="session_token",
        httponly=True,
        samesite="lax",
    )
    return {"status": "logged_out"}


@router.get("/me")
def get_current_user_status(request: Request):
    """Check current authentication status and user identity."""
    user = get_optional_current_user(request)
    if not user:
        return {
            "authenticated": False,
            "total_users": get_user_count(),
        }

    return {
        "authenticated": True,
        "username": user["username"],
        "user_id": user["id"],
        "total_users": get_user_count(),
    }
