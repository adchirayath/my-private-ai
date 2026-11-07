from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# Modular package imports
from routers.auth import router as auth_router
from routers.chat import router as chat_router
from routers.files import router as files_router
from routers.health import router as health_router

# Re-exports for backwards compatibility
from services.chunker import split_text
from services.extractors import extract_text, decode_text
from services.ollama import (
    ollama_embed,
    ask_ollama,
    stream_ollama,
    make_search_query,
    build_chat_messages,
)
from services.rag import (
    index_document,
    search_documents_hybrid as search_documents,
)
from core.db import (
    documents_collection,
    get_conversation_history,
    save_message,
    clear_conversation_history,
)

# ---------------------------------------------------------
# FastAPI App Initialization
# ---------------------------------------------------------
app = FastAPI(
    title="My Private AI",
    description="Your private documents, your hardware, Zero Cloud",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount Static Assets
app.mount(
    "/static",
    StaticFiles(directory="static"),
    name="static",
)

# Include Routers
app.include_router(health_router)
app.include_router(auth_router)
app.include_router(files_router)
app.include_router(chat_router)


@app.api_route("/", methods=["GET", "HEAD"])
def home():
    """Serve the single-page application frontend."""
    return FileResponse("static/index.html")


@app.get("/docs/beginners_guide.pdf")
def serve_beginners_guide():
    """Serve the printable 3-page Beginner's Guide & Architecture PDF inline."""
    return FileResponse(
        "docs/beginners_guide.pdf",
        media_type="application/pdf",
        content_disposition_type="inline",
    )
