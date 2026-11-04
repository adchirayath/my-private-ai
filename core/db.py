"""
core/db.py - Database Layer (ChromaDB + SQLite)
-----------------------------------------------
1. ChromaDB (Vector Database):
   Stores mathematical embedding vectors for document chunks. When you ask
   a question, ChromaDB computes cosine similarity to find the most relevant chunks.

2. SQLite (Relational Chat History):
   Stores conversation turns (User and Assistant messages) on disk so your
   chat history persists across page reloads and server restarts.
"""

import json
import re
import sqlite3
from typing import Optional

import chromadb
from core.config import CHROMA_DB_PATH, SQLITE_DB_PATH, MAX_HISTORY_MESSAGES

# ---------------------------------------------------------
# ChromaDB Vector Store
# ---------------------------------------------------------
chroma_client = chromadb.PersistentClient(path=CHROMA_DB_PATH)

documents_collection = chroma_client.get_or_create_collection(
    name="coworker_documents",
    metadata={"hnsw:space": "cosine"},
)

# ---------------------------------------------------------
# SQLite Persistent Conversation Store (Phase 2)
# ---------------------------------------------------------
def init_sqlite_db() -> None:
    """Initialize SQLite tables for persistent chat history."""
    SQLITE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(SQLITE_DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                sources TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_ws_conv
            ON messages(workspace, conversation_id, id)
        """)
        conn.commit()

init_sqlite_db()


def get_conversation_history(
    workspace: str,
    conversation_id: str,
    limit: int = MAX_HISTORY_MESSAGES,
) -> list[dict]:
    """Retrieve the most recent messages for a conversation from SQLite."""
    with sqlite3.connect(SQLITE_DB_PATH) as conn:
        cursor = conn.execute(
            """
            SELECT role, content, sources
            FROM (
                SELECT id, role, content, sources
                FROM messages
                WHERE workspace = ? AND conversation_id = ?
                ORDER BY id DESC
                LIMIT ?
            )
            ORDER BY id ASC
            """,
            (workspace, conversation_id, limit),
        )
        history = []
        for row in cursor.fetchall():
            item = {"role": row[0], "content": row[1]}
            if row[2]:
                try:
                    item["sources"] = json.loads(row[2])
                except Exception:
                    pass
            history.append(item)
        return history


def save_message(
    workspace: str,
    conversation_id: str,
    role: str,
    content: str,
    sources: Optional[list[str]] = None,
) -> None:
    """Persist a single user or assistant message to SQLite."""
    sources_json = json.dumps(sources) if sources else None
    with sqlite3.connect(SQLITE_DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO messages (workspace, conversation_id, role, content, sources)
            VALUES (?, ?, ?, ?, ?)
            """,
            (workspace, conversation_id, role, content, sources_json),
        )
        conn.commit()


def clear_conversation_history(workspace: str, conversation_id: str) -> None:
    """Delete all messages for a specific conversation in a workspace."""
    with sqlite3.connect(SQLITE_DB_PATH) as conn:
        conn.execute(
            "DELETE FROM messages WHERE workspace = ? AND conversation_id = ?",
            (workspace, conversation_id),
        )
        conn.commit()


def delete_workspace_messages(workspace: str) -> int:
    """Delete all messages associated with an entire workspace."""
    with sqlite3.connect(SQLITE_DB_PATH) as conn:
        cursor = conn.execute(
            "DELETE FROM messages WHERE workspace = ?",
            (workspace,),
        )
        conn.commit()
        return cursor.rowcount


def get_workspace_conversation_counts() -> dict[str, int]:
    """Return a mapping of workspace name -> distinct conversation count."""
    with sqlite3.connect(SQLITE_DB_PATH) as conn:
        cursor = conn.execute(
            """
            SELECT workspace, COUNT(DISTINCT conversation_id)
            FROM messages
            GROUP BY workspace
            """
        )
        return dict(cursor.fetchall())


def list_conversations(workspace: str) -> list[dict]:
    """
    List distinct conversations in a workspace with their title,
    message count, and last active timestamp.
    """
    with sqlite3.connect(SQLITE_DB_PATH) as conn:
        cursor = conn.execute(
            """
            SELECT 
                conversation_id,
                MAX(created_at) as last_updated,
                COUNT(*) as message_count,
                (SELECT content FROM messages m2 
                 WHERE m2.workspace = m1.workspace AND m2.conversation_id = m1.conversation_id AND m2.role = 'user'
                 ORDER BY id ASC LIMIT 1) as user_title,
                (SELECT content FROM messages m3 
                 WHERE m3.workspace = m1.workspace AND m3.conversation_id = m1.conversation_id 
                 ORDER BY id ASC LIMIT 1) as fallback_title
            FROM messages m1
            WHERE workspace = ?
            GROUP BY conversation_id
            ORDER BY last_updated DESC
            """,
            (workspace,),
        )
        conversations = []
        for row in cursor.fetchall():
            conv_id = row[0]
            last_updated = row[1]
            count = row[2]
            title_raw = row[3] or row[4] or "New conversation"
            clean_title = re.sub(r"\s+", " ", title_raw).strip()
            title = clean_title[:45] + ("..." if len(clean_title) > 45 else "")
            conversations.append({
                "conversation_id": conv_id,
                "title": title,
                "message_count": count,
                "last_updated": last_updated,
            })
        return conversations

