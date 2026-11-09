import tempfile
from pathlib import Path
from datetime import datetime, timedelta, timezone
import pytest

from core import db, auth


@pytest.fixture
def temp_db(monkeypatch):
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        test_db = Path(tmpdir) / "test_auth_chat.db"
        monkeypatch.setattr(db, "SQLITE_DB_PATH", test_db)
        monkeypatch.setattr(auth, "SQLITE_DB_PATH", test_db)
        db.init_sqlite_db()
        yield test_db


def test_user_registration_and_login(temp_db):
    # Valid registration
    res = auth.register_user("alice", "secret123")
    assert res["username"] == "alice"
    assert res["user_id"] > 0
    assert res["token"] is not None

    # Check user count
    assert auth.get_user_count() == 1

    # Duplicate registration fails
    with pytest.raises(ValueError, match="already registered"):
        auth.register_user("alice", "another_pass")

    # Case-insensitive duplicate check fails
    with pytest.raises(ValueError, match="already registered"):
        auth.register_user("Alice", "another_pass")

    # Invalid username fails
    with pytest.raises(ValueError, match="Username must be"):
        auth.register_user("a!", "secret123")

    # Short password fails
    with pytest.raises(ValueError, match="Password must be at least"):
        auth.register_user("bob", "12")

    # Login with valid credentials
    login_res = auth.login_user("alice", "secret123")
    assert login_res is not None
    assert login_res["username"] == "alice"
    assert login_res["user_id"] == res["user_id"]

    # Login with wrong password
    bad_login = auth.login_user("alice", "wrongpass")
    assert bad_login is None

    # Login with non-existent user
    unknown_login = auth.login_user("charlie", "secret123")
    assert unknown_login is None


def test_session_lifecycle(temp_db):
    reg = auth.register_user("david", "pass1234")
    token = reg["token"]

    # Session lookup
    user = auth.get_user_by_token(token)
    assert user is not None
    assert user["username"] == "david"
    assert user["id"] == reg["user_id"]

    # Logout / token invalidation
    auth.logout_user(token)
    assert auth.get_user_by_token(token) is None


def test_session_expiration(temp_db):
    reg = auth.register_user("eve", "pass1234")
    # Manually create an already expired session
    expired_token = auth.create_session(reg["user_id"], "eve", days_valid=-1)
    
    # Lookup should detect expiration and return None
    assert auth.get_user_by_token(expired_token) is None


def test_chat_history_user_isolation(temp_db):
    # Register two distinct users
    user_a = auth.register_user("user_a", "password_a")
    user_b = auth.register_user("user_b", "password_b")
    id_a = user_a["user_id"]
    id_b = user_b["user_id"]

    # User A sends a message in conv-1
    db.save_message("default", "conv-1", "user", "User A question 1", user_id=id_a)
    db.save_message("default", "conv-1", "assistant", "User A answer 1", user_id=id_a)

    # User B sends a message in conv-1
    db.save_message("default", "conv-1", "user", "User B question 1", user_id=id_b)
    db.save_message("default", "conv-1", "assistant", "User B answer 1", user_id=id_b)

    # User A retrieves history: should ONLY see User A messages
    history_a = db.get_conversation_history("default", "conv-1", user_id=id_a)
    assert len(history_a) == 2
    assert history_a[0]["content"] == "User A question 1"
    assert history_a[1]["content"] == "User A answer 1"

    # User B retrieves history: should ONLY see User B messages
    history_b = db.get_conversation_history("default", "conv-1", user_id=id_b)
    assert len(history_b) == 2
    assert history_b[0]["content"] == "User B question 1"
    assert history_b[1]["content"] == "User B answer 1"

    # Conversation list isolation
    convs_a = db.list_conversations("default", user_id=id_a)
    assert len(convs_a) == 1
    assert convs_a[0]["title"] == "User A question 1"

    convs_b = db.list_conversations("default", user_id=id_b)
    assert len(convs_b) == 1
    assert convs_b[0]["title"] == "User B question 1"

    # User A clears conversation: User B's conversation must remain untouched
    db.clear_conversation_history("default", "conv-1", user_id=id_a)
    assert db.get_conversation_history("default", "conv-1", user_id=id_a) == []
    assert len(db.get_conversation_history("default", "conv-1", user_id=id_b)) == 2


def test_first_user_adopts_legacy_messages(temp_db):
    # Save legacy messages without user_id
    db.save_message("default", "conv-legacy", "user", "Pre-existing question")
    db.save_message("default", "conv-legacy", "assistant", "Pre-existing answer")

    # First user registers
    first_user = auth.register_user("founder", "admin123")
    user_id = first_user["user_id"]

    # History should now be accessible under the first user's ID
    history = db.get_conversation_history("default", "conv-legacy", user_id=user_id)
    assert len(history) == 2
    assert history[0]["content"] == "Pre-existing question"

    # Second user registers; should have empty conversation history
    second_user = auth.register_user("newbie", "admin123")
    history_second = db.get_conversation_history("default", "conv-legacy", user_id=second_user["user_id"])
    assert history_second == []
