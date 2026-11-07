import os
import re
from pathlib import Path

# ---------------------------------------------------------
# Ollama and Model Settings
# ---------------------------------------------------------
OLLAMA_BASE_URL = os.getenv(
    "OLLAMA_BASE_URL",
    "http://localhost:11434",
).rstrip("/")

OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")
OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "8192"))
DEFAULT_TEMPERATURE = float(os.getenv("DEFAULT_TEMPERATURE", "0.2"))

# ---------------------------------------------------------
# Security & Origins
# ---------------------------------------------------------
_raw_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000")
ALLOWED_ORIGINS = [o.strip() for o in _raw_origins.split(",") if o.strip()]

# ---------------------------------------------------------
# Storage Paths & Limits
# ---------------------------------------------------------
CHROMA_DB_PATH = os.getenv("CHROMA_DB_PATH", "./chroma_db")
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "./uploads"))
SQLITE_DB_PATH = Path(os.getenv("SQLITE_DB_PATH", Path(CHROMA_DB_PATH) / "chat_history.db"))

MAX_FILE_MB = int(os.getenv("MAX_FILE_MB", "25"))
MAX_FILE_BYTES = MAX_FILE_MB * 1024 * 1024

RETRIEVAL_TOP_K = int(os.getenv("RETRIEVAL_TOP_K", "5"))
MAX_RETRIEVAL_DISTANCE = float(os.getenv("MAX_RETRIEVAL_DISTANCE", "0.52"))
MAX_HISTORY_MESSAGES = int(os.getenv("MAX_HISTORY_MESSAGES", "12"))

# Ensure storage directories exist
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
Path(CHROMA_DB_PATH).mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------
# Supported File Extensions
# ---------------------------------------------------------
TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".log",
    ".py", ".js", ".jsx", ".ts", ".tsx",
    ".java", ".c", ".h", ".cpp", ".hpp",
    ".cs", ".go", ".rs", ".php", ".rb",
    ".swift", ".kt", ".kts", ".scala",
    ".sql", ".sh", ".bash", ".ps1", ".bat",
    ".cmd", ".r", ".R", ".jl",
    ".html", ".htm", ".xml", ".json", ".jsonl",
    ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".conf", ".env", ".properties", ".gitignore",
    ".dockerignore", ".makefile", ".css", ".scss",
    ".less", ".vue", ".svelte", ".ipynb",
    ".rst", ".tex", ".rtf",
}

SPREADSHEET_EXTENSIONS = {
    ".csv", ".tsv", ".xlsx", ".xls", ".ods",
}

IMAGE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".bmp",
    ".tif", ".tiff", ".webp",
}

SUPPORTED_EXTENSIONS = (
    TEXT_EXTENSIONS
    | SPREADSHEET_EXTENSIONS
    | IMAGE_EXTENSIONS
    | {".pdf", ".docx", ".pptx"}
)


# ---------------------------------------------------------
# Helpers
# ---------------------------------------------------------
def normalize_workspace(value: str) -> str:
    """
    Convert a workspace name into a safe directory/database key.
    Examples: 'Marketing Team' -> 'marketing-team'
    """
    value = (value or "default").strip().lower()
    value = re.sub(r"[^a-z0-9_-]+", "-", value)
    value = value.strip("-_")
    return value[:60] or "default"


def get_workspace_dir(workspace: str) -> Path:
    safe_workspace = normalize_workspace(workspace)
    directory = UPLOAD_DIR / safe_workspace
    directory.mkdir(parents=True, exist_ok=True)
    return directory

