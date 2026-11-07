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
# SQLite Persistent Conversation Store (Phase 2 & Auth Layer)
# ---------------------------------------------------------
def init_sqlite_db() -> None:
    """Initialize SQLite tables for persistent chat history, users, and sessions."""
    SQLITE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(SQLITE_DB_PATH) as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys = ON;")

        # 1. Users table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 2. Sessions table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)

        # 3. Messages table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                sources TEXT,
                user_id INTEGER DEFAULT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)

        # Safe schema migration for existing databases without user_id column
        cursor = conn.execute("PRAGMA table_info(messages)")
        columns = [col[1] for col in cursor.fetchall()]
        if "user_id" not in columns:
            conn.execute("ALTER TABLE messages ADD COLUMN user_id INTEGER DEFAULT NULL")

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_ws_conv
            ON messages(workspace, conversation_id, id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_ws_user_conv
            ON messages(workspace, user_id, conversation_id, id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_sessions_token_expires
            ON sessions(token, expires_at)
        """)

        # 4. Relational Documents Catalog table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY,
                workspace TEXT NOT NULL,
                filename TEXT NOT NULL,
                file_size INTEGER NOT NULL,
                chunk_count INTEGER NOT NULL,
                user_id INTEGER DEFAULT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)

        # Safe schema migration for existing databases without user_id column
        cursor = conn.execute("PRAGMA table_info(documents)")
        doc_cols = [col[1] for col in cursor.fetchall()]
        if "user_id" not in doc_cols:
            conn.execute("ALTER TABLE documents ADD COLUMN user_id INTEGER DEFAULT NULL")

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_documents_ws
            ON documents(workspace, created_at DESC)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_documents_ws_user
            ON documents(workspace, user_id, created_at DESC)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_documents_user
            ON documents(user_id)
        """)

        # If users exist and there are unassigned documents, assign them to the earliest user
        cursor = conn.execute("SELECT MIN(id) FROM users")
        min_user_row = cursor.fetchone()
        first_user_id = min_user_row[0] if min_user_row else None
        if first_user_id is not None:
            conn.execute(
                "UPDATE documents SET user_id = ? WHERE user_id IS NULL",
                (first_user_id,),
            )

        # 5. Persistent SQLite FTS5 Inverted Index for Hybrid BM25 Search
        cursor = conn.execute("PRAGMA table_info(chunk_fts)")
        fts_cols = [col[1] for col in cursor.fetchall()]
        if fts_cols and "user_id" not in fts_cols:
            conn.execute("DROP TABLE chunk_fts")

        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
                chunk_id UNINDEXED,
                workspace UNINDEXED,
                user_id UNINDEXED,
                file_id UNINDEXED,
                filename UNINDEXED,
                page_number UNINDEXED,
                content,
                tokenize='porter unicode61'
            )
        """)

        conn.commit()

        # Seamless backfill for pre-existing ChromaDB chunks if FTS is empty
        _backfill_fts_and_documents_if_needed(conn, default_user_id=first_user_id)

    cleanup_expired_sessions()


def _backfill_fts_and_documents_if_needed(conn: sqlite3.Connection, default_user_id: Optional[int] = None) -> None:
    """Populate documents and chunk_fts from ChromaDB if upgrading from older version."""
    try:
        cursor = conn.execute("SELECT COUNT(*) FROM documents")
        doc_count = cursor.fetchone()[0]

        records = documents_collection.get(include=["documents", "metadatas"])
        ids = records.get("ids", [])
        docs = records.get("documents", [])
        metas = records.get("metadatas", [])

        if doc_count == 0:
            file_summaries = {}
            for chunk_id, content, meta in zip(ids, docs, metas):
                if not meta:
                    continue
                ws = meta.get("workspace", "default")
                fid = meta.get("file_id", "unknown")
                fname = meta.get("filename", "unknown")
                uid = meta.get("user_id", default_user_id)

                key = (fid, ws)
                if key not in file_summaries:
                    file_summaries[key] = {
                        "id": fid,
                        "workspace": ws,
                        "filename": fname,
                        "file_size": 0,
                        "chunk_count": 0,
                        "user_id": uid,
                    }
                file_summaries[key]["chunk_count"] += 1

            for item in file_summaries.values():
                conn.execute(
                    """
                    INSERT OR IGNORE INTO documents (id, workspace, filename, file_size, chunk_count, user_id)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (item["id"], item["workspace"], item["filename"], item["file_size"], item["chunk_count"], item["user_id"]),
                )

        # Backfill chunk_fts if empty
        cursor = conn.execute("SELECT COUNT(*) FROM chunk_fts")
        if cursor.fetchone()[0] == 0 and ids:
            fts_rows = []
            for chunk_id, content, meta in zip(ids, docs, metas):
                if not meta:
                    continue
                ws = meta.get("workspace", "default")
                fid = meta.get("file_id", "unknown")
                fname = meta.get("filename", "unknown")
                page = meta.get("page_number", "")
                uid = meta.get("user_id", default_user_id)
                user_str = str(uid) if uid is not None else ""
                fts_rows.append((chunk_id, ws, user_str, fid, fname, str(page), content))

            if fts_rows:
                conn.executemany(
                    """
                    INSERT INTO chunk_fts (chunk_id, workspace, user_id, file_id, filename, page_number, content)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    fts_rows,
                )

        conn.commit()
    except Exception:
        pass


def cleanup_expired_sessions() -> int:
    """Delete expired user sessions from SQLite."""
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        with sqlite3.connect(SQLITE_DB_PATH) as conn:
            cursor = conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now_iso,))
            conn.commit()
            return cursor.rowcount
    except Exception:
        return 0


# Run database initialization at startup
init_sqlite_db()


# ---------------------------------------------------------
# Document Metadata Management
# ---------------------------------------------------------
def save_document(
    file_id: str,
    workspace: str,
    filename: str,
    file_size: int,
    chunk_count: int,
    user_id: Optional[int] = None,
) -> None:
    """Record an uploaded file in the relational documents table."""
    with sqlite3.connect(SQLITE_DB_PATH) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO documents (id, workspace, filename, file_size, chunk_count, user_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (file_id, workspace, filename, file_size, chunk_count, user_id),
        )
        conn.commit()


def delete_document(file_id: str, workspace: str, user_id: Optional[int] = None) -> int:
    """Remove a document record and its FTS chunks from SQLite."""
    with sqlite3.connect(SQLITE_DB_PATH) as conn:
        if user_id is not None:
            cursor = conn.execute(
                "DELETE FROM documents WHERE id = ? AND workspace = ? AND user_id = ?",
                (file_id, workspace, user_id),
            )
            conn.execute(
                "DELETE FROM chunk_fts WHERE file_id = ? AND workspace = ? AND user_id = ?",
                (file_id, workspace, str(user_id)),
            )
        else:
            cursor = conn.execute(
                "DELETE FROM documents WHERE id = ? AND workspace = ?",
                (file_id, workspace),
            )
            conn.execute(
                "DELETE FROM chunk_fts WHERE file_id = ? AND workspace = ?",
                (file_id, workspace),
            )
        conn.commit()
        return cursor.rowcount


def get_document(
    file_id: str,
    workspace: Optional[str] = None,
    user_id: Optional[int] = None,
) -> Optional[dict]:
    """Retrieve document metadata by ID, optional workspace, and optional user_id."""
    with sqlite3.connect(SQLITE_DB_PATH) as conn:
        query = "SELECT id, workspace, filename, file_size, chunk_count, user_id, created_at FROM documents WHERE id = ?"
        params = [file_id]
        if workspace:
            query += " AND workspace = ?"
            params.append(workspace)
        if user_id is not None:
            query += " AND user_id = ?"
            params.append(user_id)

        cursor = conn.execute(query, params)
        row = cursor.fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "file_id": row[0],
            "workspace": row[1],
            "filename": row[2],
            "file_size": row[3],
            "chunk_count": row[4],
            "user_id": row[5],
            "created_at": row[6],
        }


def list_documents_db(workspace: str, user_id: Optional[int] = None) -> list[dict]:
    """List documents registered in a workspace from SQLite, scoped to user if provided."""
    with sqlite3.connect(SQLITE_DB_PATH) as conn:
        if user_id is not None:
            cursor = conn.execute(
                """
                SELECT id, filename, file_size, chunk_count, created_at, user_id
                FROM documents
                WHERE workspace = ? AND user_id = ?
                ORDER BY created_at DESC
                """,
                (workspace, user_id),
            )
        else:
            cursor = conn.execute(
                """
                SELECT id, filename, file_size, chunk_count, created_at, user_id
                FROM documents
                WHERE workspace = ?
                ORDER BY created_at DESC
                """,
                (workspace,),
            )
        return [
            {
                "file_id": row[0],
                "filename": row[1],
                "size_bytes": row[2],
                "chunk_count": row[3],
                "created_at": row[4],
                "user_id": row[5],
            }
            for row in cursor.fetchall()
        ]


def get_workspace_document_counts(user_id: Optional[int] = None) -> dict[str, int]:
    """Return a mapping of workspace name -> document count, scoped to user if provided."""
    with sqlite3.connect(SQLITE_DB_PATH) as conn:
        if user_id is not None:
            cursor = conn.execute(
                """
                SELECT workspace, COUNT(*)
                FROM documents
                WHERE user_id = ?
                GROUP BY workspace
                """,
                (user_id,),
            )
        else:
            cursor = conn.execute(
                """
                SELECT workspace, COUNT(*)
                FROM documents
                GROUP BY workspace
                """
            )
        return dict(cursor.fetchall())


def get_workspace_chunk_counts(user_id: Optional[int] = None) -> dict[str, int]:
    """Return a mapping of workspace name -> total indexed chunk count, scoped to user if provided."""
    with sqlite3.connect(SQLITE_DB_PATH) as conn:
        if user_id is not None:
            cursor = conn.execute(
                """
                SELECT workspace, COALESCE(SUM(chunk_count), 0)
                FROM documents
                WHERE user_id = ?
                GROUP BY workspace
                """,
                (user_id,),
            )
        else:
            cursor = conn.execute(
                """
                SELECT workspace, COALESCE(SUM(chunk_count), 0)
                FROM documents
                GROUP BY workspace
                """
            )
        return dict(cursor.fetchall())


# ---------------------------------------------------------
# SQLite FTS5 Full-Text Search
# ---------------------------------------------------------
def index_fts_chunks(
    workspace: str,
    file_id: str,
    filename: str,
    chunks: list[dict],
    user_id: Optional[int] = None,
) -> None:
    """
    Insert document chunks into the SQLite FTS5 table for fast BM25 ranking.
    chunks is a list of dicts: [{"id": str, "content": str, "page_number": str}]
    """
    user_str = str(user_id) if user_id is not None else ""
    rows = [
        (
            c["id"],
            workspace,
            user_str,
            file_id,
            filename,
            str(c.get("page_number", "")),
            c["content"],
        )
        for c in chunks
    ]
    with sqlite3.connect(SQLITE_DB_PATH) as conn:
        conn.executemany(
            """
            INSERT INTO chunk_fts (chunk_id, workspace, user_id, file_id, filename, page_number, content)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()


def delete_fts_chunks(workspace: str, file_id: str, user_id: Optional[int] = None) -> None:
    """Remove FTS indexed chunks for a file."""
    with sqlite3.connect(SQLITE_DB_PATH) as conn:
        if user_id is not None:
            conn.execute(
                "DELETE FROM chunk_fts WHERE workspace = ? AND file_id = ? AND user_id = ?",
                (workspace, file_id, str(user_id)),
            )
        else:
            conn.execute(
                "DELETE FROM chunk_fts WHERE workspace = ? AND file_id = ?",
                (workspace, file_id),
            )
        conn.commit()


def search_fts_chunks(
    workspace: str,
    query: str,
    limit: int = 15,
    user_id: Optional[int] = None,
) -> list[dict]:
    """
    Search document chunks using SQLite FTS5 BM25 ranking.
    Returns ranked chunk matches with page numbers and BM25 scores.
    """
    cleaned_query = (query or "").strip()
    if not cleaned_query:
        return []

    # Extract alphanumeric query terms to build safe FTS5 MATCH clause
    terms = re.findall(r"\w+", cleaned_query)
    if not terms:
        return []

    # Filter out very short single-letter terms unless all terms are short
    significant_terms = [t for t in terms if len(t) > 1]
    tokens_to_match = significant_terms if significant_terms else terms

    # Construct OR query with quoted tokens to avoid FTS syntax errors
    match_clause = " OR ".join(f'"{t}"' for t in tokens_to_match[:20])

    with sqlite3.connect(SQLITE_DB_PATH) as conn:
        try:
            if user_id is not None:
                cursor = conn.execute(
                    """
                    SELECT chunk_id, file_id, filename, page_number, content, bm25(chunk_fts) as score
                    FROM chunk_fts
                    WHERE workspace = ? AND user_id = ? AND chunk_fts MATCH ?
                    ORDER BY score ASC
                    LIMIT ?
                    """,
                    (workspace, str(user_id), match_clause, limit),
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT chunk_id, file_id, filename, page_number, content, bm25(chunk_fts) as score
                    FROM chunk_fts
                    WHERE workspace = ? AND chunk_fts MATCH ?
                    ORDER BY score ASC
                    LIMIT ?
                    """,
                    (workspace, match_clause, limit),
                )
            results = []
            for row in cursor.fetchall():
                results.append({
                    "id": row[0],
                    "file_id": row[1],
                    "filename": row[2],
                    "page_number": row[3] if row[3] else None,
                    "text": row[4],
                    "fts_score": row[5],
                })
            return results
        except sqlite3.OperationalError:
            # Fallback for unexpected FTS syntax errors
            return []


def get_conversation_history(
    workspace: str,
    conversation_id: str,
    limit: int = MAX_HISTORY_MESSAGES,
    user_id: Optional[int] = None,
) -> list[dict]:
    """Retrieve the most recent messages for a conversation from SQLite."""
    with sqlite3.connect(SQLITE_DB_PATH) as conn:
        if user_id is not None:
            cursor = conn.execute(
                """
                SELECT role, content, sources
                FROM (
                    SELECT id, role, content, sources
                    FROM messages
                    WHERE workspace = ? AND conversation_id = ? AND user_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                )
                ORDER BY id ASC
                """,
                (workspace, conversation_id, user_id, limit),
            )
        else:
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
    user_id: Optional[int] = None,
) -> None:
    """Persist a single user or assistant message to SQLite."""
    sources_json = json.dumps(sources) if sources else None
    with sqlite3.connect(SQLITE_DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO messages (workspace, conversation_id, role, content, sources, user_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (workspace, conversation_id, role, content, sources_json, user_id),
        )
        conn.commit()


def clear_conversation_history(
    workspace: str,
    conversation_id: str,
    user_id: Optional[int] = None,
) -> None:
    """Delete all messages for a specific conversation in a workspace."""
    with sqlite3.connect(SQLITE_DB_PATH) as conn:
        if user_id is not None:
            conn.execute(
                "DELETE FROM messages WHERE workspace = ? AND conversation_id = ? AND user_id = ?",
                (workspace, conversation_id, user_id),
            )
        else:
            conn.execute(
                "DELETE FROM messages WHERE workspace = ? AND conversation_id = ?",
                (workspace, conversation_id),
            )
        conn.commit()


def delete_workspace_messages(workspace: str, user_id: Optional[int] = None) -> int:
    """Delete all messages, documents, and FTS chunks associated with a workspace (optionally scoped to user)."""
    with sqlite3.connect(SQLITE_DB_PATH) as conn:
        if user_id is not None:
            cursor = conn.execute(
                "DELETE FROM messages WHERE workspace = ? AND user_id = ?",
                (workspace, user_id),
            )
            conn.execute("DELETE FROM documents WHERE workspace = ? AND user_id = ?", (workspace, user_id))
            conn.execute("DELETE FROM chunk_fts WHERE workspace = ? AND user_id = ?", (workspace, str(user_id)))
        else:
            cursor = conn.execute(
                "DELETE FROM messages WHERE workspace = ?",
                (workspace,),
            )
            conn.execute("DELETE FROM documents WHERE workspace = ?", (workspace,))
            conn.execute("DELETE FROM chunk_fts WHERE workspace = ?", (workspace,))
        conn.commit()
        return cursor.rowcount


def get_workspace_conversation_counts(user_id: Optional[int] = None) -> dict[str, int]:
    """Return a mapping of workspace name -> distinct conversation count."""
    with sqlite3.connect(SQLITE_DB_PATH) as conn:
        if user_id is not None:
            cursor = conn.execute(
                """
                SELECT workspace, COUNT(DISTINCT conversation_id)
                FROM messages
                WHERE user_id = ?
                GROUP BY workspace
                """,
                (user_id,),
            )
        else:
            cursor = conn.execute(
                """
                SELECT workspace, COUNT(DISTINCT conversation_id)
                FROM messages
                GROUP BY workspace
                """
            )
        return dict(cursor.fetchall())


def list_conversations(workspace: str, user_id: Optional[int] = None) -> list[dict]:
    """
    List distinct conversations in a workspace with their title,
    message count, and last active timestamp.
    """
    with sqlite3.connect(SQLITE_DB_PATH) as conn:
        if user_id is not None:
            cursor = conn.execute(
                """
                SELECT 
                    conversation_id,
                    MAX(created_at) as last_updated,
                    COUNT(*) as message_count,
                    (SELECT content FROM messages m2 
                     WHERE m2.workspace = m1.workspace AND m2.conversation_id = m1.conversation_id AND m2.user_id = ? AND m2.role = 'user'
                     ORDER BY id ASC LIMIT 1) as user_title,
                    (SELECT content FROM messages m3 
                     WHERE m3.workspace = m1.workspace AND m3.conversation_id = m1.conversation_id AND m3.user_id = ?
                     ORDER BY id ASC LIMIT 1) as fallback_title,
                    (SELECT role FROM messages m4
                     WHERE m4.workspace = m1.workspace AND m4.conversation_id = m1.conversation_id AND m4.user_id = ?
                     ORDER BY id DESC LIMIT 1) as last_role
                FROM messages m1
                WHERE workspace = ? AND user_id = ?
                GROUP BY conversation_id
                ORDER BY last_updated DESC
                """,
                (user_id, user_id, user_id, workspace, user_id),
            )
        else:
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
                     ORDER BY id ASC LIMIT 1) as fallback_title,
                    (SELECT role FROM messages m4
                     WHERE m4.workspace = m1.workspace AND m4.conversation_id = m1.conversation_id 
                     ORDER BY id DESC LIMIT 1) as last_role
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
            last_role = row[5] if len(row) > 5 else None
            is_completed = (last_role == "assistant" and count > 0)
            conversations.append({
                "conversation_id": conv_id,
                "title": title,
                "message_count": count,
                "last_updated": last_updated,
                "last_role": last_role,
                "is_completed": is_completed,
            })
        return conversations

