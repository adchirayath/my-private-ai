import pytest
import shutil
from pathlib import Path


@pytest.fixture(autouse=True)
def isolate_test_environment(monkeypatch, tmp_path):
    """Ensure all tests run in an isolated SQLite database and temporary upload directory,
    preventing any test runs from polluting the development or production database.
    """
    test_db = tmp_path / "test_isolated.db"
    test_uploads = tmp_path / "test_uploads"
    test_uploads.mkdir(parents=True, exist_ok=True)

    import core.config as config
    import core.db as db
    import core.auth as auth
    import core.rbac as rbac
    import core.audit as audit

    monkeypatch.setattr(config, "SQLITE_DB_PATH", test_db)
    monkeypatch.setattr(config, "UPLOAD_DIR", test_uploads)
    monkeypatch.setattr(db, "SQLITE_DB_PATH", test_db)
    monkeypatch.setattr(auth, "SQLITE_DB_PATH", test_db)
    monkeypatch.setattr(rbac, "SQLITE_DB_PATH", test_db)
    monkeypatch.setattr(audit, "SQLITE_DB_PATH", test_db)

    db.init_sqlite_db()
    yield
