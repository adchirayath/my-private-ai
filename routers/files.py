import re
import shutil
import uuid
from pathlib import Path
from pydantic import BaseModel

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

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
def list_workspaces():
    """
    List all known workspaces with file counts, conversation counts,
    and indexed chunk counts.
    """
    names = {"default"}

    # 1. Inspect directories on disk
    if UPLOAD_DIR.exists():
        for p in UPLOAD_DIR.iterdir():
            if p.is_dir() and not p.name.startswith("."):
                names.add(p.name)

    # 2. Inspect SQLite for any workspaces with chat history
    conv_counts = {}
    try:
        conv_counts = get_workspace_conversation_counts()
        names.update(conv_counts.keys())
    except Exception:
        pass

    # 3. Inspect ChromaDB for any indexed chunks
    chunk_counts = {}
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
        ws_dir = UPLOAD_DIR / ws
        file_count = 0
        if ws_dir.exists() and ws_dir.is_dir():
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
def delete_workspace(request: DeleteWorkspaceRequest):
    """
    Completely remove a non-default workspace, including files on disk,
    ChromaDB vectors, and SQLite conversation history.
    """
    clean_name = normalize_workspace(request.workspace)
    if clean_name == "default":
        raise HTTPException(
            status_code=400,
            detail="The 'default' workspace cannot be deleted.",
        )

    # 1. Remove vector chunks from ChromaDB
    try:
        documents_collection.delete(where={"workspace": clean_name})
    except Exception:
        pass

    # 2. Remove conversation messages from SQLite
    try:
        delete_workspace_messages(clean_name)
    except Exception:
        pass

    # 3. Remove files directory from disk
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
):
    """
    Upload multiple files, extract text with OCR fallbacks, generate embeddings,
    and persist vectors into ChromaDB.
    """
    workspace = normalize_workspace(workspace)
    workspace_dir = get_workspace_dir(workspace)

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
            extracted_text = extract_text(original_name, data)
            if not extracted_text.strip():
                raise ValueError("No readable text was extracted from this file.")

            stored_path.write_bytes(data)

            try:
                chunk_count = await index_document(
                    workspace=workspace,
                    file_id=file_id,
                    filename=original_name,
                    text=extracted_text,
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
    }


@router.get("/files")
def list_files(workspace: str = "default"):
    """
    List uploaded files for the selected workspace with a single metadata query.
    """
    workspace = normalize_workspace(workspace)
    workspace_dir = get_workspace_dir(workspace)

    id_to_filename = {}
    try:
        workspace_records = documents_collection.get(
            where={"workspace": workspace},
            include=["metadatas"],
        )
        for meta in workspace_records.get("metadatas", []):
            if meta and "file_id" in meta and "filename" in meta:
                id_to_filename[meta["file_id"]] = meta["filename"]
    except Exception:
        pass

    files = []
    for path in workspace_dir.iterdir():
        if not path.is_file() or "__" not in path.name:
            continue

        file_id, stored_name = path.name.split("__", 1)
        original_name = id_to_filename.get(file_id, stored_name)

        files.append({
            "file_id": file_id,
            "filename": original_name,
            "size_bytes": path.stat().st_size,
        })

    return {
        "workspace": workspace,
        "files": files,
    }


@router.delete("/files")
def delete_file(request: DeleteRequest):
    """
    Delete a file and its indexed vector chunks.
    """
    if not re.match(r"^[A-Za-z0-9_-]+$", request.file_id):
        raise HTTPException(
            status_code=400,
            detail="Invalid file_id format.",
        )

    workspace = normalize_workspace(request.workspace)
    workspace_dir = get_workspace_dir(workspace)

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

