"""
core/audit.py - Centralized Audit Logging System
------------------------------------------------
Provides immutable audit trail tracking for security events, user administration,
role/permission changes, authentication events, and administrative actions.
Audit records are strictly append-only and read-only.
"""

import csv
import io
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import Request

import core.config as config_mod
import core.db as db_mod

SQLITE_DB_PATH = config_mod.SQLITE_DB_PATH


def get_db_path():
    """Retrieve dynamic database path, supporting test monkeypatching."""
    if hasattr(db_mod, "SQLITE_DB_PATH") and db_mod.SQLITE_DB_PATH != config_mod.SQLITE_DB_PATH:
        return db_mod.SQLITE_DB_PATH
    return SQLITE_DB_PATH


def extract_client_ip(request: Optional[Request]) -> str:
    """Extract client IP address from Request headers or direct host."""
    if not request:
        return ""
    x_forwarded_for = request.headers.get("X-Forwarded-For")
    if x_forwarded_for:
        # First IP in the comma-separated chain is the client
        return x_forwarded_for.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return ""


def log_audit_event(
    action: str,
    resource: str,
    user_id: Optional[int] = None,
    username: Optional[str] = None,
    details: Optional[dict[str, Any]] = None,
    ip_address: Optional[str] = None,
) -> int:
    """
    Persist an audit log entry to SQLite.
    Returns the created record ID, or -1 on failure.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    details_str = json.dumps(details or {}, ensure_ascii=False)
    db_path = get_db_path()

    try:
        with sqlite3.connect(db_path, timeout=10.0) as conn:
            conn.execute("PRAGMA foreign_keys = ON;")
            cursor = conn.execute(
                """
                INSERT INTO audit_logs (user_id, username, action, resource, details, ip_address, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    username or "",
                    action.strip(),
                    resource.strip(),
                    details_str,
                    (ip_address or "").strip(),
                    now_iso,
                ),
            )
            conn.commit()
            return cursor.lastrowid
    except Exception:
        # Failsafe: audit logger should never break caller execution
        return -1


def list_audit_logs(
    search: Optional[str] = None,
    user_id: Optional[int] = None,
    action: Optional[str] = None,
    resource: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    page: int = 1,
    limit: int = 20,
) -> dict[str, Any]:
    """
    Query paginated audit logs with dynamic filtering.
    Logs are strictly read-only and ordered newest first.
    """
    page = max(1, page)
    limit = max(1, min(100, limit))
    offset = (page - 1) * limit
    db_path = get_db_path()

    where_clauses: list[str] = []
    params: list[Any] = []

    if user_id is not None:
        where_clauses.append("user_id = ?")
        params.append(user_id)

    if action:
        where_clauses.append("action LIKE ?")
        params.append(f"%{action.strip()}%")

    if resource:
        where_clauses.append("resource = ?")
        params.append(resource.strip().lower())

    if start_date:
        where_clauses.append("created_at >= ?")
        params.append(start_date.strip())

    if end_date:
        where_clauses.append("created_at <= ?")
        params.append(end_date.strip())

    if search:
        s = f"%{search.strip()}%"
        where_clauses.append("(username LIKE ? OR action LIKE ? OR resource LIKE ? OR ip_address LIKE ? OR details LIKE ?)")
        params.extend([s, s, s, s, s])

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    with sqlite3.connect(db_path, timeout=10.0) as conn:
        # Total count
        count_cursor = conn.execute(f"SELECT COUNT(*) FROM audit_logs {where_sql}", params)
        total = count_cursor.fetchone()[0]

        # Paginated rows
        query = f"""
            SELECT id, user_id, username, action, resource, details, ip_address, created_at
            FROM audit_logs
            {where_sql}
            ORDER BY id DESC
            LIMIT ? OFFSET ?
        """
        rows_cursor = conn.execute(query, params + [limit, offset])

        logs = []
        for row in rows_cursor.fetchall():
            try:
                parsed_details = json.loads(row[5]) if row[5] else {}
            except Exception:
                parsed_details = {"raw": row[5]}

            logs.append({
                "id": row[0],
                "user_id": row[1],
                "username": row[2] or "Anonymous / System",
                "action": row[3],
                "resource": row[4],
                "details": parsed_details,
                "ip_address": row[6] or "",
                "created_at": row[7],
            })

    total_pages = (total + limit - 1) // limit if total > 0 else 1
    return {
        "logs": logs,
        "total": total,
        "page": page,
        "limit": limit,
        "total_pages": total_pages,
    }


def get_recent_activities(limit: int = 10) -> list[dict[str, Any]]:
    """Retrieve the latest administrative/user actions for the overview feed."""
    db_path = get_db_path()
    with sqlite3.connect(db_path, timeout=10.0) as conn:
        cursor = conn.execute(
            """
            SELECT id, user_id, username, action, resource, details, ip_address, created_at
            FROM audit_logs
            ORDER BY id DESC
            LIMIT ?
            """,
            (max(1, min(50, limit)),),
        )
        activities = []
        for row in cursor.fetchall():
            try:
                details = json.loads(row[5]) if row[5] else {}
            except Exception:
                details = {}
            activities.append({
                "id": row[0],
                "user_id": row[1],
                "username": row[2] or "Anonymous / System",
                "action": row[3],
                "resource": row[4],
                "details": details,
                "ip_address": row[6] or "",
                "created_at": row[7],
            })
        return activities


def get_recent_logins(limit: int = 10) -> list[dict[str, Any]]:
    """Retrieve recent authentication events (successful logins and failed attempts)."""
    db_path = get_db_path()
    with sqlite3.connect(db_path, timeout=10.0) as conn:
        cursor = conn.execute(
            """
            SELECT id, user_id, username, action, details, ip_address, created_at
            FROM audit_logs
            WHERE action IN ('auth.login', 'auth.login_failed', 'auth.logout', 'user.register')
            ORDER BY id DESC
            LIMIT ?
            """,
            (max(1, min(50, limit)),),
        )
        logins = []
        for row in cursor.fetchall():
            try:
                details = json.loads(row[4]) if row[4] else {}
            except Exception:
                details = {}
            logins.append({
                "id": row[0],
                "user_id": row[1],
                "username": row[2] or "Anonymous",
                "action": row[3],
                "status": "success" if row[3] in ("auth.login", "user.register") else ("failed" if row[3] == "auth.login_failed" else "logout"),
                "details": details,
                "ip_address": row[5] or "",
                "created_at": row[6],
            })
        return logins


def export_audit_logs_data(format_type: str = "json") -> tuple[str, str]:
    """
    Export all audit logs formatted as JSON or CSV.
    Returns (content_string, media_type).
    """
    db_path = get_db_path()
    with sqlite3.connect(db_path, timeout=10.0) as conn:
        cursor = conn.execute(
            """
            SELECT id, user_id, username, action, resource, details, ip_address, created_at
            FROM audit_logs
            ORDER BY id DESC
            LIMIT 5000
            """
        )
        rows = cursor.fetchall()

    if format_type.lower() == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["ID", "User ID", "Username", "Action", "Resource", "Details", "IP Address", "Timestamp"])
        for r in rows:
            writer.writerow([r[0], r[1] or "", r[2] or "", r[3], r[4], r[5] or "", r[6] or "", r[7]])
        return output.getvalue(), "text/csv"

    # Default: JSON
    data = []
    for r in rows:
        try:
            details = json.loads(r[5]) if r[5] else {}
        except Exception:
            details = {"raw": r[5]}
        data.append({
            "id": r[0],
            "user_id": r[1],
            "username": r[2] or "",
            "action": r[3],
            "resource": r[4],
            "details": details,
            "ip_address": r[6] or "",
            "created_at": r[7],
        })
    return json.dumps(data, indent=2, ensure_ascii=False), "application/json"
