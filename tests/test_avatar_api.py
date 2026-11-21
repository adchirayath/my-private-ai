import io
import tempfile
from pathlib import Path
from PIL import Image
import pytest
from fastapi.testclient import TestClient

from app import app
from core import db, auth


@pytest.fixture
def client_with_db(monkeypatch):
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        test_db = Path(tmpdir) / "test_avatar.db"
        monkeypatch.setattr(db, "SQLITE_DB_PATH", test_db)
        monkeypatch.setattr(auth, "SQLITE_DB_PATH", test_db)
        db.init_sqlite_db()
        client = TestClient(app)
        yield client


def create_test_image(format="PNG", size=(120, 80), color="blue") -> bytes:
    img = Image.new("RGBA" if format == "PNG" else "RGB", size, color=color)
    buf = io.BytesIO()
    img.save(buf, format=format)
    return buf.getvalue()


def test_avatar_upload_and_delete_flow(client_with_db):
    client = client_with_db

    # 1. Register and get token
    reg = client.post("/api/auth/register", json={"username": "avatar_user", "password": "password123"})
    assert reg.status_code == 200
    token = reg.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 2. Initially avatar is None
    me = client.get("/api/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["avatar"] is None

    # 3. Upload without auth -> 401
    img_bytes = create_test_image("PNG")
    unauth_client = TestClient(app)
    unauth_upload = unauth_client.post("/api/auth/avatar", files={"file": ("test.png", img_bytes, "image/png")})
    assert unauth_upload.status_code == 401

    # 4. Upload valid PNG image
    upload_res = client.post(
        "/api/auth/avatar",
        headers=headers,
        files={"file": ("test.png", img_bytes, "image/png")},
    )
    assert upload_res.status_code == 200
    data = upload_res.json()
    assert data["status"] == "ok"
    assert data["avatar"].startswith("data:image/png;base64,")

    # 5. Check /api/auth/me reflects the new avatar
    me_after = client.get("/api/auth/me", headers=headers)
    assert me_after.status_code == 200
    assert me_after.json()["avatar"] == data["avatar"]

    # 6. Upload JPEG image
    jpeg_bytes = create_test_image("JPEG", color="red")
    upload_jpeg = client.post(
        "/api/auth/avatar",
        headers=headers,
        files={"file": ("photo.jpg", jpeg_bytes, "image/jpeg")},
    )
    assert upload_jpeg.status_code == 200
    assert upload_jpeg.json()["avatar"].startswith("data:image/jpeg;base64,")

    # 7. Delete avatar
    del_res = client.delete("/api/auth/avatar", headers=headers)
    assert del_res.status_code == 200
    assert del_res.json()["status"] == "ok"
    assert del_res.json()["avatar"] is None

    # 8. Check /api/auth/me is now None again
    me_final = client.get("/api/auth/me", headers=headers)
    assert me_final.status_code == 200
    assert me_final.json()["avatar"] is None


def test_avatar_invalid_file(client_with_db):
    client = client_with_db
    reg = client.post("/api/auth/register", json={"username": "invalid_avatar_user", "password": "password123"})
    token = reg.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Upload text file disguised as image
    res = client.post(
        "/api/auth/avatar",
        headers=headers,
        files={"file": ("not_an_image.txt", b"plain text content", "text/plain")},
    )
    assert res.status_code == 400
    assert "Invalid file format" in res.json()["detail"]

    # Upload corrupt image
    res_corrupt = client.post(
        "/api/auth/avatar",
        headers=headers,
        files={"file": ("fake.png", b"not a real png header", "image/png")},
    )
    assert res_corrupt.status_code == 400
    assert "Failed to process image" in res_corrupt.json()["detail"]
