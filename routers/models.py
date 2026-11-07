import json
import re
from typing import Optional
from pydantic import BaseModel, field_validator

import httpx
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse

from core.auth import get_optional_current_user
from core.config import OLLAMA_BASE_URL, OLLAMA_MODEL

router = APIRouter(prefix="/api/models", tags=["models"])

# Curated catalog of high-quality local models optimized for chat & RAG
RECOMMENDED_MODELS = [
    {
        "name": "llama3.2:latest",
        "displayName": "Llama 3.2 (3B)",
        "parameter_size": "3B",
        "size_str": "2.0 GB",
        "tag": "Balanced & Fast",
        "description": "Meta's flagship compact model; balanced general knowledge and reasoning.",
    },
    {
        "name": "llama3.2:1b",
        "displayName": "Llama 3.2 (1B)",
        "parameter_size": "1B",
        "size_str": "1.3 GB",
        "tag": "Ultra-Fast",
        "description": "Super lightweight model for maximum speed and lowest memory consumption.",
    },
    {
        "name": "qwen2.5:1.5b",
        "displayName": "Qwen 2.5 (1.5B)",
        "parameter_size": "1.5B",
        "size_str": "986 MB",
        "tag": "Smart & Compact",
        "description": "Alibaba's advanced compact model with strong reasoning and multilinguality.",
    },
    {
        "name": "deepseek-r1:1.5b",
        "displayName": "DeepSeek-R1 (1.5B)",
        "parameter_size": "1.5B",
        "size_str": "1.1 GB",
        "tag": "Reasoning Specialist",
        "description": "Chain-of-thought reasoning model specialized for logical puzzles and math.",
    },
    {
        "name": "phi3:mini",
        "displayName": "Phi-3 Mini (3.8B)",
        "parameter_size": "3.8B",
        "size_str": "2.2 GB",
        "tag": "High Accuracy",
        "description": "Microsoft compact powerhouse with exceptional benchmark performance.",
    },
    {
        "name": "mistral:latest",
        "displayName": "Mistral (7B)",
        "parameter_size": "7B",
        "size_str": "4.1 GB",
        "tag": "High Capability",
        "description": "Industry benchmark 7B model for nuanced text synthesis and complex tasks.",
    },
    {
        "name": "gemma2:2b",
        "displayName": "Gemma 2 (2B)",
        "parameter_size": "2B",
        "size_str": "1.6 GB",
        "tag": "Efficient & Clean",
        "description": "Google's lightweight open model built on Gemini research architecture.",
    },
    {
        "name": "qwen2.5-coder:1.5b",
        "displayName": "Qwen 2.5 Coder (1.5B)",
        "parameter_size": "1.5B",
        "size_str": "986 MB",
        "tag": "Code Specialist",
        "description": "Fine-tuned for programming, debugging, and software architecture.",
    },
]


def format_size(bytes_val: int) -> str:
    """Format raw byte count into human-readable size string."""
    if not bytes_val or bytes_val <= 0:
        return "Unknown size"
    if bytes_val < 1024 * 1024:
        return f"{bytes_val / 1024:.1f} KB"
    if bytes_val < 1024 * 1024 * 1024:
        return f"{bytes_val / (1024 * 1024):.1f} MB"
    return f"{bytes_val / (1024 * 1024 * 1024):.1f} GB"


class PullModelRequest(BaseModel):
    model: str

    @field_validator("model")
    @classmethod
    def validate_model_name(cls, v: str) -> str:
        name = v.strip()
        if not name:
            raise ValueError("Model name cannot be empty")
        # Allowed chars: alphanumeric, underscore, hyphen, dot, colon, slash
        if not re.match(r"^[a-zA-Z0-9_\-\.\/:]+$", name):
            raise ValueError("Invalid model name format")
        if len(name) > 100:
            raise ValueError("Model name too long")
        return name


@router.get("")
async def list_models():
    """
    List installed models in Ollama, default model, and curated recommended models.
    """
    ollama_ok = False
    installed_models = []

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{OLLAMA_BASE_URL}/api/tags")
            if resp.status_code == 200:
                payload = resp.json()
                raw_models = payload.get("models", [])
                for m in raw_models:
                    m_name = m.get("name", "")
                    # Filter out embedding / reranking models
                    if any(k in m_name.lower() for k in ("embed", "bge-", "e5-", "minilm")):
                        continue

                    size_bytes = m.get("size", 0)
                    is_def = (
                        m_name == OLLAMA_MODEL
                        or m_name == f"{OLLAMA_MODEL}:latest"
                        or (":" in OLLAMA_MODEL and m_name == OLLAMA_MODEL.split(":")[0] and OLLAMA_MODEL.endswith(":latest"))
                    )
                    installed_models.append({
                        "name": m_name,
                        "size": size_bytes,
                        "size_str": format_size(size_bytes),
                        "modified_at": m.get("modified_at"),
                        "is_default": is_def,
                    })
                ollama_ok = True
    except Exception:
        pass

    # Find matching details in recommended for installed ones
    rec_names = {r["name"]: r for r in RECOMMENDED_MODELS}
    for item in installed_models:
        clean = item["name"]
        clean_base = clean.split(":")[0] if ":" in clean else clean
        matched = rec_names.get(clean) or rec_names.get(f"{clean_base}:latest") or rec_names.get(clean_base)
        if matched:
            item["displayName"] = matched["displayName"]
            item["tag"] = matched["tag"]
            item["description"] = matched["description"]
        else:
            item["displayName"] = item["name"]
            item["tag"] = "Custom"
            item["description"] = "Installed local Ollama model"

    return {
        "status": "ok" if ollama_ok else "offline",
        "ollama_connected": ollama_ok,
        "default_model": OLLAMA_MODEL,
        "installed": installed_models,
        "recommended": RECOMMENDED_MODELS,
    }


@router.post("/pull")
async def pull_model(
    request: PullModelRequest,
    current_user: Optional[dict] = Depends(get_optional_current_user),
):
    """
    Stream Ollama model download progress via Server-Sent Events (SSE).
    """
    model_name = request.model

    async def stream_progress():
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream(
                    "POST",
                    f"{OLLAMA_BASE_URL}/api/pull",
                    json={"name": model_name, "stream": True},
                ) as response:
                    if response.status_code != 200:
                        err_text = await response.aread()
                        yield f"data: {json.dumps({'status': 'error', 'error': f'Ollama error ({response.status_code}): {err_text.decode()}'})}\n\n"
                        return

                    async for line in response.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            event_data = json.loads(line)
                            status = event_data.get("status", "")
                            completed = event_data.get("completed", 0)
                            total = event_data.get("total", 0)

                            pct = 0
                            if total and total > 0:
                                pct = round((completed / total) * 100, 1)

                            payload = {
                                "status": status,
                                "completed": completed,
                                "total": total,
                                "percent": pct,
                                "completed_str": format_size(completed),
                                "total_str": format_size(total),
                            }
                            yield f"data: {json.dumps(payload)}\n\n"

                            if status == "success":
                                yield f"data: {json.dumps({'status': 'done', 'model': model_name})}\n\n"
                                return
                        except json.JSONDecodeError:
                            continue

            yield f"data: {json.dumps({'status': 'done', 'model': model_name})}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'status': 'error', 'error': str(exc)})}\n\n"

    return StreamingResponse(
        stream_progress(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.delete("/{model_name:path}")
async def delete_model(
    model_name: str,
    current_user: Optional[dict] = Depends(get_optional_current_user),
):
    """
    Delete a model from Ollama local storage.
    """
    name = model_name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Model name required")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.request(
                "DELETE",
                f"{OLLAMA_BASE_URL}/api/delete",
                json={"name": name},
            )
            if resp.status_code == 200:
                return {"status": "ok", "deleted": name}
            else:
                detail = resp.text
                try:
                    detail = resp.json().get("error", detail)
                except Exception:
                    pass
                raise HTTPException(status_code=resp.status_code, detail=detail)
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Failed to connect to Ollama: {exc}")
