import pytest
from fastapi.testclient import TestClient

from app import app
from routers.models import format_size, RECOMMENDED_MODELS, PullModelRequest


def test_format_size():
    assert format_size(0) == "Unknown size"
    assert format_size(-10) == "Unknown size"
    assert format_size(500 * 1024) == "500.0 KB"
    assert format_size(100 * 1024 * 1024) == "100.0 MB"
    assert format_size(2 * 1024 * 1024 * 1024) == "2.0 GB"


def test_pull_model_request_validation():
    req = PullModelRequest(model="llama3.2:1b")
    assert req.model == "llama3.2:1b"

    req2 = PullModelRequest(model="  qwen2.5:1.5b  ")
    assert req2.model == "qwen2.5:1.5b"

    with pytest.raises(ValueError):
        PullModelRequest(model="")

    with pytest.raises(ValueError):
        PullModelRequest(model="model; rm -rf /")


def test_list_models_api():
    client = TestClient(app)
    response = client.get("/api/models")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert "default_model" in data
    assert "installed" in data
    assert "recommended" in data
    assert len(data["recommended"]) == len(RECOMMENDED_MODELS)
