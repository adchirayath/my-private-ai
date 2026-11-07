from typing import Optional
import httpx
from fastapi import APIRouter, Depends, Query

from core.auth import get_optional_current_user
from core.config import OLLAMA_BASE_URL, OLLAMA_MODEL, OLLAMA_EMBED_MODEL
from core.db import documents_collection, get_workspace_chunk_counts

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
async def health(
    workspace: Optional[str] = Query(None),
    current_user: Optional[dict] = Depends(get_optional_current_user),
):
    """
    Check connectivity to Ollama, configured models, and ChromaDB chunk count.
    If a workspace is provided, reports the chunk count scoped to that workspace.
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

    chat_models = [
        m for m in ollama_models
        if not any(k in m.lower() for k in ("embed", "bge-", "e5-", "minilm"))
    ]

    try:
        total_chunks = documents_collection.count()
    except Exception:
        total_chunks = 0

    workspace_chunks = 0
    user_id = current_user["id"] if current_user else None

    if workspace:
        clean_ws = workspace.strip()
        if user_id is not None:
            try:
                chunk_counts = get_workspace_chunk_counts(user_id=user_id)
                workspace_chunks = chunk_counts.get(clean_ws, 0)
            except Exception:
                workspace_chunks = 0
        else:
            workspace_chunks = 0
    else:
        # If no workspace specified but user is logged in, sum user's chunks
        if user_id is not None:
            try:
                chunk_counts = get_workspace_chunk_counts(user_id=user_id)
                workspace_chunks = sum(chunk_counts.values())
            except Exception:
                workspace_chunks = 0
        else:
            workspace_chunks = 0

    return {
        "status": "ok" if ollama_ok else "degraded",
        "ollama_connected": ollama_ok,
        "chat_model": OLLAMA_MODEL,
        "embedding_model": OLLAMA_EMBED_MODEL,
        "available_models": ollama_models,
        "chat_models": chat_models or ([OLLAMA_MODEL] if ollama_ok else []),
        "chroma_document_chunks": workspace_chunks,
        "workspace_document_chunks": workspace_chunks,
        "total_document_chunks": total_chunks,
    }

