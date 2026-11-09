import tempfile
from pathlib import Path
from core import db


def test_sqlite_chat_persistence(monkeypatch):
    # Use temporary database for testing
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        test_db = Path(tmpdir) / "test_chat.db"
        monkeypatch.setattr(db, "SQLITE_DB_PATH", test_db)
        db.init_sqlite_db()

        # Initially empty
        history = db.get_conversation_history("test-ws", "conv-1")
        assert history == []

        # Save user & assistant turn
        db.save_message("test-ws", "conv-1", "user", "What is the status?")
        db.save_message("test-ws", "conv-1", "assistant", "All systems operational.", sources=["status.txt"])

        history = db.get_conversation_history("test-ws", "conv-1")
        assert len(history) == 2
        assert history[0]["role"] == "user"
        assert history[0]["content"] == "What is the status?"
        assert history[1]["role"] == "assistant"
        assert history[1]["content"] == "All systems operational."
        assert history[1]["sources"] == ["status.txt"]

        # Different conversation is isolated
        other_history = db.get_conversation_history("test-ws", "conv-2")
        assert other_history == []

        # Clear conversation
        db.clear_conversation_history("test-ws", "conv-1")
        assert db.get_conversation_history("test-ws", "conv-1") == []


def test_list_conversations(monkeypatch):
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        test_db = Path(tmpdir) / "test_chat.db"
        monkeypatch.setattr(db, "SQLITE_DB_PATH", test_db)
        db.init_sqlite_db()

        # Save turns in two conversations
        db.save_message("test-ws", "conv-a", "user", "What is the budget?")
        db.save_message("test-ws", "conv-a", "assistant", "The budget is 100k.")
        db.save_message("test-ws", "conv-b", "user", "Explain quantum computing")

        convs = db.list_conversations("test-ws")
        assert len(convs) == 2
        ids = [c["conversation_id"] for c in convs]
        assert "conv-a" in ids
        assert "conv-b" in ids

        conv_a = next(c for c in convs if c["conversation_id"] == "conv-a")
        assert conv_a["title"] == "What is the budget?"
        assert conv_a["message_count"] == 2


def test_workspace_isolation_and_deletion(monkeypatch):
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        test_db = Path(tmpdir) / "test_chat.db"
        monkeypatch.setattr(db, "SQLITE_DB_PATH", test_db)
        db.init_sqlite_db()

        # Workspace 1
        db.save_message("ws-finance", "conv-1", "user", "Q1 report")
        db.save_message("ws-finance", "conv-2", "user", "Q2 forecast")

        # Workspace 2
        db.save_message("ws-hr", "conv-3", "user", "Onboarding checklist")

        counts = db.get_workspace_conversation_counts()
        assert counts["ws-finance"] == 2
        assert counts["ws-hr"] == 1

        # Delete workspace 1
        deleted_count = db.delete_workspace_messages("ws-finance")
        assert deleted_count == 2

        # Workspace 1 is now empty
        assert db.get_conversation_history("ws-finance", "conv-1") == []
        assert db.list_conversations("ws-finance") == []

        # Workspace 2 is preserved
        hr_history = db.get_conversation_history("ws-hr", "conv-3")
        assert len(hr_history) == 1
        assert hr_history[0]["content"] == "Onboarding checklist"

