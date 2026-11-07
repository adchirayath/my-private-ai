import asyncio
import re
import shutil
import uuid
from pathlib import Path
from typing import Optional
from pydantic import BaseModel

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from core.auth import get_current_user, get_optional_current_user
from core.config import (
    MAX_FILE_BYTES,
    MAX_FILE_MB,
    SUPPORTED_EXTENSIONS,
    UPLOAD_DIR,
    get_workspace_dir,
    normalize_workspace,
)
from core.db import (
    documents_collection,
    delete_workspace_messages,
    get_workspace_conversation_counts,
    get_workspace_document_counts,
    get_workspace_chunk_counts,
    save_document,
    delete_document,
    list_documents_db,
    get_document,
)
from services.extractors import extract_text
from services.rag import index_document

router = APIRouter(prefix="/api", tags=["files"])


class DeleteRequest(BaseModel):
    file_id: str
    workspace: str = "default"


class CreateWorkspaceRequest(BaseModel):
    name: str


class DeleteWorkspaceRequest(BaseModel):
    workspace: str


@router.get("/workspaces")
def list_workspaces(current_user: Optional[dict] = Depends(get_optional_current_user)):
    """
    List all known workspaces with file counts, conversation counts,
    and indexed chunk counts, scoped to the current user if authenticated.
    """
    names = {"default"}
    user_id = current_user["id"] if current_user else None

    # 1. Inspect directories on disk
    if UPLOAD_DIR.exists():
        for p in UPLOAD_DIR.iterdir():
            if p.is_dir() and not p.name.startswith("."):
                names.add(p.name)

    # 2. Inspect SQLite for any workspaces with chat history
    conv_counts = {}
    try:
        conv_counts = get_workspace_conversation_counts(user_id=user_id)
        names.update(conv_counts.keys())
    except Exception:
        pass

    # 3. Inspect SQLite for documents & chunks
    doc_counts = {}
    chunk_counts = {}
    try:
        doc_counts = get_workspace_document_counts(user_id=user_id)
        chunk_counts = get_workspace_chunk_counts(user_id=user_id)
        names.update(doc_counts.keys())
    except Exception:
        pass

    # Fallback: if unauthenticated and no sqlite documents yet, inspect ChromaDB
    if user_id is None and not doc_counts:
        try:
            records = documents_collection.get(include=["metadatas"])
            for meta in records.get("metadatas", []):
                if meta and "workspace" in meta:
                    ws = meta["workspace"]
                    chunk_counts[ws] = chunk_counts.get(ws, 0) + 1
                    names.add(ws)
        except Exception:
            pass

    workspaces = []
    for ws in sorted(names, key=lambda x: (x != "default", x)):
        if user_id is not None:
            file_count = doc_counts.get(ws, 0)
        else:
            ws_dir = UPLOAD_DIR / ws
            file_count = doc_counts.get(ws, 0)
            if file_count == 0 and ws_dir.exists() and ws_dir.is_dir():
                file_count = sum(
                    1 for p in ws_dir.iterdir()
                    if p.is_file() and "__" in p.name
                )

        workspaces.append({
            "name": ws,
            "file_count": file_count,
            "conversation_count": conv_counts.get(ws, 0),
            "chunk_count": chunk_counts.get(ws, 0),
            "is_default": ws == "default",
        })

    return {"workspaces": workspaces}


@router.post("/workspaces")
def create_workspace(request: CreateWorkspaceRequest):
    """
    Create a new workspace directory for document and chat isolation.
    """
    raw_name = (request.name or "").strip()
    clean_name = normalize_workspace(raw_name)

    if not raw_name or not clean_name or clean_name == "default":
        raise HTTPException(
            status_code=400,
            detail="Invalid workspace name. Must contain alphanumeric characters and cannot be 'default'.",
        )

    workspace_dir = get_workspace_dir(clean_name)
    return {
        "status": "created",
        "workspace": clean_name,
        "path": str(workspace_dir),
    }


@router.delete("/workspaces")
def delete_workspace(
    request: DeleteWorkspaceRequest,
    current_user: Optional[dict] = Depends(get_optional_current_user),
):
    """
    Completely remove a non-default workspace.
    If authenticated, purges the current user's documents and chat history in this workspace.
    If unauthenticated (or no other user documents exist), cleans disk files.
    """
    clean_name = normalize_workspace(request.workspace)
    if clean_name == "default":
        raise HTTPException(
            status_code=400,
            detail="The 'default' workspace cannot be deleted.",
        )

    user_id = current_user["id"] if current_user else None

    if user_id is not None:
        # 1. Remove user vector chunks from ChromaDB
        try:
            documents_collection.delete(
                where={
                    "$and": [
                        {"workspace": clean_name},
                        {"user_id": user_id},
                    ]
                }
            )
        except Exception:
            pass

        # 2. Remove user messages and documents from SQLite
        try:
            delete_workspace_messages(clean_name, user_id=user_id)
        except Exception:
            pass

        # 3. Clean files from disk if no remaining user documents
        remaining_docs = list_documents_db(clean_name)
        deleted_from_disk = False
        workspace_dir = UPLOAD_DIR / clean_name
        if not remaining_docs and workspace_dir.exists():
            shutil.rmtree(workspace_dir, ignore_errors=True)
            deleted_from_disk = True
    else:
        # Unauthenticated fallback
        try:
            documents_collection.delete(where={"workspace": clean_name})
        except Exception:
            pass

        try:
            delete_workspace_messages(clean_name)
        except Exception:
            pass

        workspace_dir = UPLOAD_DIR / clean_name
        deleted_from_disk = False
        if workspace_dir.exists():
            shutil.rmtree(workspace_dir, ignore_errors=True)
            deleted_from_disk = True

    return {
        "status": "deleted",
        "workspace": clean_name,
        "deleted_from_disk": deleted_from_disk,
    }


@router.post("/upload")
async def upload_files(
    files: list[UploadFile] = File(...),
    workspace: str = Form("default"),
    current_user: dict = Depends(get_current_user),
):
    """
    Upload multiple files, extract text with OCR fallbacks, generate embeddings,
    and persist vectors into ChromaDB scoped to the authenticated user.
    """
    workspace = normalize_workspace(workspace)
    workspace_dir = get_workspace_dir(workspace)
    user_id = current_user["id"]

    if len(files) > 50:
        raise HTTPException(
            status_code=400,
            detail="Maximum 50 files allowed per upload.",
        )

    uploaded = []
    errors = []

    for uploaded_file in files:
        original_name = Path(uploaded_file.filename or "unnamed").name
        ext = Path(original_name).suffix.lower()

        if ext not in SUPPORTED_EXTENSIONS:
            errors.append({
                "filename": original_name,
                "error": f"Unsupported file extension: {ext}",
            })
            continue

        try:
            data = await uploaded_file.read(MAX_FILE_BYTES + 1)
        except Exception as read_err:
            errors.append({
                "filename": original_name,
                "error": f"Failed reading file: {read_err}",
            })
            continue

        if not data:
            errors.append({
                "filename": original_name,
                "error": "The file is empty.",
            })
            continue

        if len(data) > MAX_FILE_BYTES:
            errors.append({
                "filename": original_name,
                "error": f"File exceeds {MAX_FILE_MB} MB limit.",
            })
            continue

        file_id = uuid.uuid4().hex
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", original_name).strip("._") or "uploaded_file"
        stored_path = workspace_dir / f"{file_id}__{safe_name}"

        try:
            # Offload CPU-heavy text & OCR extraction to thread pool to prevent event loop stalls
            extracted_text = await asyncio.to_thread(extract_text, original_name, data)
            if not extracted_text.strip():
                raise ValueError("No readable text was extracted from this file.")

            await asyncio.to_thread(stored_path.write_bytes, data)

            try:
                chunk_count = await index_document(
                    workspace=workspace,
                    file_id=file_id,
                    filename=original_name,
                    text=extracted_text,
                    user_id=user_id,
                )
                # Persist file metadata in SQLite documents table scoped to user
                save_document(
                    file_id=file_id,
                    workspace=workspace,
                    filename=original_name,
                    file_size=len(data),
                    chunk_count=chunk_count,
                    user_id=user_id,
                )
            except Exception:
                stored_path.unlink(missing_ok=True)
                raise

            uploaded.append({
                "file_id": file_id,
                "filename": original_name,
                "workspace": workspace,
                "chunks_indexed": chunk_count,
                "characters_extracted": len(extracted_text),
                "user_id": user_id,
                "owner": current_user["username"],
                "status": "indexed",
            })

        except Exception as exc:
            stored_path.unlink(missing_ok=True)
            errors.append({
                "filename": original_name,
                "error": str(exc),
            })

    return {
        "workspace": workspace,
        "uploaded": uploaded,
        "errors": errors,
        "user_id": user_id,
        "owner": current_user["username"],
    }


@router.get("/files")
def list_files(
    workspace: str = "default",
    current_user: Optional[dict] = Depends(get_optional_current_user),
):
    """
    List uploaded files for the selected workspace.
    Scoped strictly to the authenticated user.
    """
    workspace = normalize_workspace(workspace)
    if not current_user:
        return {
            "workspace": workspace,
            "files": [],
            "authenticated": False,
        }

    user_id = current_user["id"]
    db_records = list_documents_db(workspace, user_id=user_id)

    return {
        "workspace": workspace,
        "files": db_records,
        "authenticated": True,
        "owner": current_user["username"],
    }


@router.delete("/files")
def delete_file(
    request: DeleteRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Delete a file, its relational metadata, and its indexed vector & FTS chunks.
    Ensures that only the document owner can delete the file.
    """
    if not re.match(r"^[A-Za-z0-9_-]+$", request.file_id):
        raise HTTPException(
            status_code=400,
            detail="Invalid file_id format.",
        )

    workspace = normalize_workspace(request.workspace)
    workspace_dir = get_workspace_dir(workspace)
    user_id = current_user["id"]

    # Verify ownership
    doc = get_document(request.file_id, workspace)
    if not doc:
        matches = list(workspace_dir.glob(f"{request.file_id}__*"))
        if not matches:
            raise HTTPException(status_code=404, detail="File not found.")

    if doc and doc.get("user_id") is not None and doc["user_id"] != user_id:
        raise HTTPException(
            status_code=403,
            detail="You do not have permission to delete this file.",
        )

    # 1. Delete from ChromaDB
    try:
        documents_collection.delete(
            where={
                "$and": [
                    {"workspace": workspace},
                    {"file_id": request.file_id},
                ]
            }
        )
    except Exception:
        pass

    # 2. Delete from SQLite documents & FTS table
    try:
        delete_document(request.file_id, workspace, user_id=user_id)
    except Exception:
        pass

    # 3. Delete file from disk
    deleted_on_disk = False
    for path in workspace_dir.glob(f"{request.file_id}__*"):
        path.unlink(missing_ok=True)
        deleted_on_disk = True

    return {
        "status": "deleted",
        "file_id": request.file_id,
        "workspace": workspace,
        "deleted_from_disk": deleted_on_disk,
    }


@router.get("/files/preview")
def preview_file(
    file_id: str,
    workspace: str = "default",
    current_user: Optional[dict] = Depends(get_optional_current_user),
):
    """
    Return extracted text and chunk breakdown for an uploaded document,
    verifying ownership permissions.
    """
    if not re.match(r"^[A-Za-z0-9_-]+$", file_id):
        raise HTTPException(
            status_code=400,
            detail="Invalid file_id format.",
        )

    if not current_user:
        raise HTTPException(
            status_code=401,
            detail="Authentication required to preview this document.",
        )

    workspace = normalize_workspace(workspace)
    workspace_dir = get_workspace_dir(workspace)
    user_id = current_user["id"]

    doc_info = get_document(file_id, workspace)
    if doc_info and doc_info.get("user_id") is not None and doc_info["user_id"] != user_id:
        raise HTTPException(
            status_code=403,
            detail="You do not have permission to preview this document.",
        )

    matches = list(workspace_dir.glob(f"{file_id}__*"))
    if not matches:
        raise HTTPException(status_code=404, detail="Document not found on disk.")

    file_path = matches[0]
    filename = doc_info["filename"] if doc_info else file_path.name.split("__", 1)[-1]
    data = file_path.read_bytes()

    try:
        extracted = extract_text(filename, data)
    except Exception as exc:
        extracted = f"Error extracting text: {exc}"

    # Fetch chunk count and sample chunks from ChromaDB
    sample_chunks = []
    total_chunks = doc_info["chunk_count"] if doc_info else 0
    try:
        chroma_res = documents_collection.get(
            where={
                "$and": [
                    {"workspace": workspace},
                    {"file_id": file_id},
                ]
            },
            include=["documents", "metadatas"],
        )
        docs = chroma_res.get("documents", [])
        metas = chroma_res.get("metadatas", [])
        total_chunks = len(docs)
        for i, (cdoc, cmeta) in enumerate(zip(docs[:6], metas[:6])):
            sample_chunks.append({
                "chunk_index": cmeta.get("chunk_index", i) if cmeta else i,
                "page_number": cmeta.get("page_number", "") if cmeta else "",
                "snippet": cdoc[:300] + ("…" if len(cdoc) > 300 else ""),
            })
    except Exception:
        pass

    return {
        "file_id": file_id,
        "workspace": workspace,
        "filename": filename,
        "size_bytes": file_path.stat().st_size,
        "chunk_count": total_chunks,
        "characters_extracted": len(extracted),
        "text_preview": extracted[:6000] + ("\n\n… [Preview truncated to first 6,000 characters]" if len(extracted) > 6000 else ""),
        "sample_chunks": sample_chunks,
    }

