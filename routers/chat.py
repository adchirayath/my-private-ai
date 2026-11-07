import json
from typing import Optional
from pydantic import BaseModel

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import StreamingResponse

from core.auth import get_current_user, get_optional_current_user
from core.config import normalize_workspace, OLLAMA_MODEL
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
from services.starter_prompts import get_starter_prompts

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatRequest(BaseModel):
    message: str
    workspace: str = "default"
    conversation_id: str = "default"
    stream: bool = True
    model: Optional[str] = None
    temperature: Optional[float] = None
    system_prompt: Optional[str] = None


class ConversationResetRequest(BaseModel):
    workspace: str = "default"
    conversation_id: str = "default"


@router.post("")
async def chat(
    request: ChatRequest,
    current_user: Optional[dict] = Depends(get_optional_current_user),
):
    """
    Search documents using Hybrid Retrieval (FTS5 BM25 + ChromaDB + RRF)
    and answer using persistent SQLite conversation history scoped to the user.
    Supports real-time token streaming with hardware stats via Server-Sent Events (SSE).
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
    user_id = current_user["id"] if current_user else None

    # Persistent SQLite History scoped to authenticated user
    history = get_conversation_history(workspace, conversation_id, user_id=user_id)

    # Save user turn immediately so conversation appears in chat history right away
    save_message(workspace, conversation_id, "user", question, user_id=user_id)

    if is_greeting_or_smalltalk(question):
        search_query = question
        matches = []
    else:
        try:
            search_query = await make_search_query(question, history)
            # Hybrid Search (ChromaDB vectors + SQLite FTS5 BM25 keyword matching scoped to user)
            matches = await search_documents_hybrid(
                workspace=workspace,
                question=search_query,
                user_id=user_id,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=str(exc),
            ) from exc

    # Compile citations with filenames and optional page numbers
    sources = []
    citations = []
    for match in matches:
        fname = match["filename"]
        if fname not in sources:
            sources.append(fname)
        citations.append({
            "filename": fname,
            "file_id": match.get("file_id", ""),
            "page_number": match.get("page_number"),
            "snippet": match.get("snippet", match["text"][:280]),
            "distance": match.get("distance"),
            "score": match.get("score"),
        })

    active_model = request.model.strip() if request.model else OLLAMA_MODEL

    if request.stream:
        async def stream_generator():
            # 1. Yield metadata event immediately with inspectable citations
            meta_payload = {
                "workspace": workspace,
                "conversation_id": conversation_id,
                "sources": sources,
                "citations": citations,
                "retrieved_chunks": len(matches),
                "search_query": search_query,
                "authenticated": bool(current_user),
                "user": current_user["username"] if current_user else None,
                "model": active_model,
            }
            yield f"event: meta\ndata: {json.dumps(meta_payload)}\n\n"

            # 2. Stream tokens from Ollama asynchronously
            accumulated_tokens = []
            generation_stats = None
            try:
                async for stream_item in stream_ollama(
                    question=question,
                    context=matches,
                    workspace=workspace,
                    history=history,
                    model=request.model,
                    temperature=request.temperature,
                    system_prompt_override=request.system_prompt,
                ):
                    token = stream_item[0]
                    done = stream_item[1]
                    stats = stream_item[2] if len(stream_item) > 2 else None

                    if token:
                        accumulated_tokens.append(token)
                        yield f"event: delta\ndata: {json.dumps({'token': token})}\n\n"
                    if done:
                        generation_stats = stats
                        break
            except Exception as stream_err:
                yield f"event: error\ndata: {json.dumps({'detail': str(stream_err)})}\n\n"
                return

            full_answer = "".join(accumulated_tokens).strip()

            # 3. Save assistant message to persistent SQLite database (scoped to user)
            if full_answer:
                save_message(workspace, conversation_id, "assistant", full_answer, sources, user_id=user_id)

            # 4. Final done event with hardware / token speed metrics
            done_payload = {
                "answer": full_answer,
                "stats": generation_stats,
            }
            yield f"event: done\ndata: {json.dumps(done_payload)}\n\n"

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
            model=request.model,
            temperature=request.temperature,
            system_prompt_override=request.system_prompt,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc),
        ) from exc

    save_message(workspace, conversation_id, "assistant", answer, sources, user_id=user_id)

    return {
        "workspace": workspace,
        "conversation_id": conversation_id,
        "answer": answer,
        "sources": sources,
        "citations": citations,
        "retrieved_chunks": len(matches),
        "search_query": search_query,
        "authenticated": bool(current_user),
        "model": active_model,
    }


@router.get("/export")
def export_conversation(
    workspace: str = "default",
    conversation_id: str = "default",
    format: str = "markdown",
    current_user: Optional[dict] = Depends(get_optional_current_user),
):
    """
    Export a conversation thread as Markdown or JSON.
    """
    workspace = normalize_workspace(workspace)
    conversation_id = conversation_id.strip() or "default"
    user_id = current_user["id"] if current_user else None
    messages = get_conversation_history(workspace, conversation_id, limit=200, user_id=user_id)

    if format.lower() == "json":
        return {
            "workspace": workspace,
            "conversation_id": conversation_id,
            "messages": messages,
        }

    lines = [
        f"# Chat Export: {conversation_id}",
        f"**Workspace:** `{workspace}`  ",
        f"**Exported Messages:** {len(messages)}  ",
        "\n---\n",
    ]
    for msg in messages:
        role_label = "👤 **User**" if msg["role"] == "user" else "🤖 **Assistant**"
        lines.append(f"### {role_label}\n")
        lines.append(msg["content"].strip())
        if msg.get("sources"):
            lines.append("\n\n*Sources:* " + ", ".join(f"`{s}`" for s in msg["sources"]))
        lines.append("\n\n---\n")

    return {
        "workspace": workspace,
        "conversation_id": conversation_id,
        "format": "markdown",
        "content": "\n".join(lines),
    }


@router.post("/reset")
def reset_conversation(
    request: ConversationResetRequest,
    current_user: dict = Depends(get_current_user),
):
    """Clear persistent SQLite history for one conversation (requires login)."""
    workspace = normalize_workspace(request.workspace)
    conversation_id = request.conversation_id.strip() or "default"

    if len(conversation_id) > 120:
        raise HTTPException(
            status_code=400,
            detail="The conversation ID is too long.",
        )

    clear_conversation_history(workspace, conversation_id, user_id=current_user["id"])
    return {"status": "reset", "workspace": workspace}


@router.get("/history")
def fetch_history(
    workspace: str = "default",
    conversation_id: str = "default",
    current_user: dict = Depends(get_current_user),
):
    """
    Retrieve stored chat history from SQLite for workspace restoration on page refresh.
    Requires authentication to protect private user chat history.
    """
    workspace = normalize_workspace(workspace)
    conversation_id = conversation_id.strip() or "default"
    messages = get_conversation_history(
        workspace,
        conversation_id,
        limit=50,
        user_id=current_user["id"],
    )
    return {
        "workspace": workspace,
        "conversation_id": conversation_id,
        "messages": messages,
        "user": current_user["username"],
    }


@router.get("/conversations")
def fetch_conversations(
    workspace: str = "default",
    current_user: dict = Depends(get_current_user),
):
    """
    List all saved conversation threads for a workspace belonging to current user.
    Requires authentication to protect private chat threads.
    """
    workspace = normalize_workspace(workspace)
    conversations = list_conversations(workspace, user_id=current_user["id"])
    return {
        "workspace": workspace,
        "conversations": conversations,
        "user": current_user["username"],
    }


@router.delete("/conversations")
def delete_conversation(
    request: ConversationResetRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Delete a specific conversation thread and its message history from SQLite.
    Requires authentication to ensure users only delete their own conversations.
    """
    workspace = normalize_workspace(request.workspace)
    conversation_id = request.conversation_id.strip() or "default"
    clear_conversation_history(workspace, conversation_id, user_id=current_user["id"])
    return {
        "status": "deleted",
        "workspace": workspace,
        "conversation_id": conversation_id,
    }


@router.get("/starter-prompts")
def fetch_starter_prompts(
    workspace: str = "default",
    background_tasks: BackgroundTasks = None,
    current_user: Optional[dict] = Depends(get_optional_current_user),
):
    """
    Return generic starter prompt chips if no documents are attached to the workspace,
    or dynamic document-specific prompt questions if documents are attached.
    """
    user_id = current_user["id"] if current_user else None
    return get_starter_prompts(
        workspace=workspace,
        user_id=user_id,
        background_tasks=background_tasks,
    )


