import httpx
from fastapi import APIRouter

from core.config import OLLAMA_BASE_URL, OLLAMA_MODEL, OLLAMA_EMBED_MODEL
from core.db import documents_collection

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
async def health():
    """
    Check connectivity to Ollama, configured models, and ChromaDB chunk count.
    """
    ollama_ok = False
    ollama_models = []

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{OLLAMA_BASE_URL}/api/tags")
            if response.status_code == 200:
                payload = response.json()
                ollama_models = [
                    model.get("name", "")
                    for model in payload.get("models", [])
                ]
                ollama_ok = True
    except Exception:
        pass

    return {
        "status": "ok" if ollama_ok else "degraded",
        "ollama_connected": ollama_ok,
        "chat_model": OLLAMA_MODEL,
        "embedding_model": OLLAMA_EMBED_MODEL,
        "available_models": ollama_models,
        "chroma_document_chunks": documents_collection.count(),
    }

