import json
from pydantic import BaseModel

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from core.config import normalize_workspace
from core.db import (
    get_conversation_history,
    save_message,
    clear_conversation_history,
    list_conversations,
)
from services.ollama import (
    ask_ollama,
    make_search_query,
    stream_ollama,
)
from services.rag import search_documents_hybrid, is_greeting_or_smalltalk

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatRequest(BaseModel):
    message: str
    workspace: str = "default"
    conversation_id: str = "default"
    stream: bool = True


class ConversationResetRequest(BaseModel):
    workspace: str = "default"
    conversation_id: str = "default"


@router.post("")
async def chat(request: ChatRequest):
    """
    Search documents using Phase 3 Hybrid Retrieval (BM25 + ChromaDB + RRF)
    and answer using persistent SQLite conversation history.
    Supports real-time token streaming via Server-Sent Events (SSE).
    """
    question = request.message.strip()

    if not question:
        raise HTTPException(
            status_code=400,
            detail="Please enter a question.",
        )

    if len(question) > 12000:
        raise HTTPException(
            status_code=400,
            detail="The question is too long.",
        )

    if len(request.conversation_id) > 120:
        raise HTTPException(
            status_code=400,
            detail="The conversation ID is too long.",
        )

    workspace = normalize_workspace(request.workspace)
    conversation_id = request.conversation_id.strip() or "default"

    # Phase 2: Persistent SQLite History
    history = get_conversation_history(workspace, conversation_id)

    if is_greeting_or_smalltalk(question):
        search_query = question
        matches = []
    else:
        try:
            search_query = await make_search_query(question, history)
            # Phase 3: Hybrid Search (ChromaDB vectors + BM25 keyword matching)
            matches = await search_documents_hybrid(
                workspace=workspace,
                question=search_query,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=str(exc),
            ) from exc

    sources = []
    for match in matches:
        if match["filename"] not in sources:
            sources.append(match["filename"])

    if request.stream:
        async def stream_generator():
            # 1. Yield metadata event immediately
            meta_payload = {
                "workspace": workspace,
                "conversation_id": conversation_id,
                "sources": sources,
                "retrieved_chunks": len(matches),
                "search_query": search_query,
            }
            yield f"event: meta\ndata: {json.dumps(meta_payload)}\n\n"

            # 2. Stream tokens from Ollama asynchronously
            accumulated_tokens = []
            try:
                async for token, done in stream_ollama(
                    question=question,
                    context=matches,
                    workspace=workspace,
                    history=history,
                ):
                    if token:
                        accumulated_tokens.append(token)
                        yield f"event: delta\ndata: {json.dumps({'token': token})}\n\n"
                    if done:
                        break
            except Exception as stream_err:
                yield f"event: error\ndata: {json.dumps({'detail': str(stream_err)})}\n\n"
                return

            full_answer = "".join(accumulated_tokens).strip()

            # 3. Phase 2: Save to persistent SQLite database
            if full_answer:
                save_message(workspace, conversation_id, "user", question)
                save_message(workspace, conversation_id, "assistant", full_answer, sources)

            # 4. Final done event
            yield f"event: done\ndata: {json.dumps({'answer': full_answer})}\n\n"

        return StreamingResponse(
            stream_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # Non-streaming fallback
    try:
        answer = await ask_ollama(
            question=question,
            context=matches,
            workspace=workspace,
            history=history,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc),
        ) from exc

    save_message(workspace, conversation_id, "user", question)
    save_message(workspace, conversation_id, "assistant", answer, sources)

    return {
        "workspace": workspace,
        "conversation_id": conversation_id,
        "answer": answer,
        "sources": sources,
        "retrieved_chunks": len(matches),
        "search_query": search_query,
    }


@router.post("/reset")
def reset_conversation(request: ConversationResetRequest):
    """Clear persistent SQLite history for one conversation."""
    workspace = normalize_workspace(request.workspace)
    conversation_id = request.conversation_id.strip() or "default"

    if len(conversation_id) > 120:
        raise HTTPException(
            status_code=400,
            detail="The conversation ID is too long.",
        )

    clear_conversation_history(workspace, conversation_id)
    return {"status": "reset", "workspace": workspace}


@router.get("/history")
def fetch_history(workspace: str = "default", conversation_id: str = "default"):
    """
    Retrieve stored chat history from SQLite for workspace restoration on page refresh.
    """
    workspace = normalize_workspace(workspace)
    conversation_id = conversation_id.strip() or "default"
    messages = get_conversation_history(workspace, conversation_id, limit=50)
    return {
        "workspace": workspace,
        "conversation_id": conversation_id,
        "messages": messages,
    }


@router.get("/conversations")
def fetch_conversations(workspace: str = "default"):
    """
    List all saved conversation threads for a workspace.
    """
    workspace = normalize_workspace(workspace)
    conversations = list_conversations(workspace)
    return {
        "workspace": workspace,
        "conversations": conversations,
    }


@router.delete("/conversations")
def delete_conversation(request: ConversationResetRequest):
    """
    Delete a specific conversation thread and its message history from SQLite.
    """
    workspace = normalize_workspace(request.workspace)
    conversation_id = request.conversation_id.strip() or "default"
    clear_conversation_history(workspace, conversation_id)
    return {
        "status": "deleted",
        "workspace": workspace,
        "conversation_id": conversation_id,
    }

