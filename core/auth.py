"""
core/auth.py - Authentication & Session Management Layer
--------------------------------------------------------
Provides secure user registration, password verification (bcrypt),
session persistence in SQLite, and FastAPI dependency helpers for
scoping private chat history to individual users.
"""

import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from fastapi import HTTPException, Request

from core.config import SQLITE_DB_PATH


def hash_password(password: str) -> str:
    """Hash a plaintext password using bcrypt."""
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against a bcrypt hash."""
    try:
        return bcrypt.checkpw(
            password.encode("utf-8"),
            hashed_password.encode("utf-8"),
        )
    except Exception:
        return False


def validate_username(username: str) -> str:
    """Validate and normalize a username (3-30 chars, alphanumeric, dash, dot, underscore)."""
    normalized = (username or "").strip()
    if not re.match(r"^[a-zA-Z0-9_\-\.]{3,30}$", normalized):
        raise ValueError(
            "Username must be 3-30 characters long and contain only letters, numbers, hyphens, dots, or underscores."
        )
    return normalized


def validate_password(password: str) -> str:
    """Validate password constraints."""
    if not password or len(password) < 4:
        raise ValueError("Password must be at least 4 characters long.")
    if len(password) > 128:
        raise ValueError("Password is too long (maximum 128 characters).")
    return password


def create_session(user_id: int, username: str, days_valid: int = 30) -> str:
    """Generate and persist a cryptographically secure session token."""
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(days=days_valid)
    expires_str = expires_at.isoformat()

    with sqlite3.connect(SQLITE_DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO sessions (token, user_id, username, expires_at)
            VALUES (?, ?, ?, ?)
            """,
            (token, user_id, username, expires_str),
        )
        conn.commit()
    return token


def register_user(username: str, password: str) -> dict:
    """
    Register a new user account with bcrypt password hashing.
    If this is the first user account in the system, any pre-existing
    unassigned chat history is automatically adopted by this user.
    """
    clean_username = validate_username(username)
    clean_password = validate_password(password)
    pw_hash = hash_password(clean_password)

    with sqlite3.connect(SQLITE_DB_PATH) as conn:
        conn.execute("PRAGMA foreign_keys = ON;")
        # Check if username is already taken
        cursor = conn.execute(
            "SELECT id FROM users WHERE username = ? COLLATE NOCASE",
            (clean_username,),
        )
        if cursor.fetchone() is not None:
            raise ValueError(f"Username '{clean_username}' is already registered.")

        # Insert user
        cursor = conn.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            (clean_username, pw_hash),
        )
        user_id = cursor.lastrowid

        # Check total users count: if this is the very first user, claim legacy messages and documents
        cursor = conn.execute("SELECT COUNT(*) FROM users")
        total_users = cursor.fetchone()[0]
        if total_users == 1:
            conn.execute(
                "UPDATE messages SET user_id = ? WHERE user_id IS NULL",
                (user_id,),
            )
            try:
                conn.execute(
                    "UPDATE documents SET user_id = ? WHERE user_id IS NULL",
                    (user_id,),
                )
            except Exception:
                pass
            try:
                conn.execute(
                    "UPDATE chunk_fts SET user_id = ? WHERE user_id = '' OR user_id IS NULL",
                    (str(user_id),),
                )
            except Exception:
                pass
            try:
                conn.execute(
                    "UPDATE workspaces SET user_id = ? WHERE user_id IS NULL",
                    (user_id,),
                )
            except Exception:
                pass

        conn.commit()

    token = create_session(user_id, clean_username)
    return {
        "user_id": user_id,
        "username": clean_username,
        "token": token,
    }


def login_user(username: str, password: str) -> Optional[dict]:
    """Verify credentials and return user info + new session token if successful."""
    clean_username = (username or "").strip()
    if not clean_username or not password:
        return None

    with sqlite3.connect(SQLITE_DB_PATH) as conn:
        cursor = conn.execute(
            "SELECT id, username, password_hash FROM users WHERE username = ? COLLATE NOCASE",
            (clean_username,),
        )
        row = cursor.fetchone()
        if not row:
            return None

        user_id, db_username, db_hash = row
        if not verify_password(password, db_hash):
            return None

    token = create_session(user_id, db_username)
    return {
        "user_id": user_id,
        "username": db_username,
        "token": token,
    }


def get_user_by_token(token: str) -> Optional[dict]:
    """Look up a user by session token, verifying that the session has not expired."""
    if not token or not isinstance(token, str):
        return None

    now_iso = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(SQLITE_DB_PATH) as conn:
        cursor = conn.execute(
            """
            SELECT s.user_id, s.username, s.expires_at
            FROM sessions s
            JOIN users u ON s.user_id = u.id
            WHERE s.token = ?
            """,
            (token.strip(),),
        )
        row = cursor.fetchone()
        if not row:
            return None

        user_id, username, expires_str = row
        if expires_str < now_iso:
            # Expired session cleanup
            conn.execute("DELETE FROM sessions WHERE token = ?", (token.strip(),))
            conn.commit()
            return None

        return {"id": user_id, "username": username}


def logout_user(token: str) -> None:
    """Invalidate an active session token."""
    if not token:
        return
    with sqlite3.connect(SQLITE_DB_PATH) as conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token.strip(),))
        conn.commit()


def get_user_count() -> int:
    """Return total number of registered users."""
    with sqlite3.connect(SQLITE_DB_PATH) as conn:
        cursor = conn.execute("SELECT COUNT(*) FROM users")
        return cursor.fetchone()[0]


# ---------------------------------------------------------
# FastAPI Authentication Dependencies
# ---------------------------------------------------------
def get_session_token_from_request(request: Request) -> Optional[str]:
    """
    Extract session token from:
    1. Authorization Bearer header
    2. X-Session-Token header
    3. Cookie: session_token
    """
    # 1. Bearer token
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
        if token:
            return token

    # 2. X-Session-Token header
    if "X-Session-Token" in request.headers:
        token = request.headers["X-Session-Token"].strip()
        if token:
            return token

    # 3. Cookie
    cookie_token = request.cookies.get("session_token")
    if cookie_token:
        token = cookie_token.strip()
        if token:
            return token

    return None


def get_optional_current_user(request: Request) -> Optional[dict]:
    """FastAPI dependency: returns the authenticated user dict or None."""
    token = get_session_token_from_request(request)
    if not token:
        return None
    return get_user_by_token(token)


def get_current_user(request: Request) -> dict:
    """
    FastAPI dependency: requires user authentication.
    Raises 401 Unauthorized if token is missing or invalid.
    """
    user = get_optional_current_user(request)
    if not user:
        raise HTTPException(
            status_code=401,
            detail="Authentication required.",
        )
    return user
