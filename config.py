"""
Central configuration for the RAG agent.
All settings are read from environment variables (loaded from .env at project root).
"""
from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).parent / ".env", override=False)
except ImportError:
    pass  # dotenv not available; rely on pre-set env vars

# ── Data directory (auto-created on import) ────────────────────────────────
DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ── LLM provider ───────────────────────────────────────────────────────────
# Set LLM_PROVIDER=ollama to use a local Ollama model instead of OpenAI.
# When using Ollama you must run `ollama pull <model>` first.
# Changing the embedding provider requires a Full Sync to re-index documents.
LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "openai")  # "openai" | "ollama"

# ── OpenAI ─────────────────────────────────────────────────────────────────
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_EMBEDDING_MODEL: str = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")

# ── Ollama ──────────────────────────────────────────────────────────────────
OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "mistral")
OLLAMA_EMBEDDING_MODEL: str = os.getenv("OLLAMA_EMBEDDING_MODEL", "nomic-embed-text")

# ── SharePoint / MSAL ──────────────────────────────────────────────────────
# Azure AD App Registration with Sites.Read.All + Files.Read.All permissions
SP_TENANT_ID: str = os.getenv("SP_TENANT_ID", "")
SP_CLIENT_ID: str = os.getenv("SP_CLIENT_ID", "")
SP_CLIENT_SECRET: str = os.getenv("SP_CLIENT_SECRET", "")
SP_SITE_HOSTNAME: str = os.getenv("SP_SITE_HOSTNAME", "myfidelitybank.sharepoint.com")
SP_SITE_PATH: str = os.getenv("SP_SITE_PATH", "/sites/ITInfrastructureCenter")
SP_DRIVE_NAME: str = os.getenv("SP_DRIVE_NAME", "IT Infrastructure Document Lib")
# Comma-separated list of top-level folder names to scan.
# Only files inside these folders (and their sub-folders) are ingested.
# Leave empty to scan the entire drive (original behaviour).
# Example: SP_SCAN_FOLDERS=Infrastructure Knowledge Base,Infrastructure PO
SP_SCAN_FOLDERS: list[str] = [
    f.strip()
    for f in os.getenv("SP_SCAN_FOLDERS", "").split(",")
    if f.strip()
]

# ── Chroma ─────────────────────────────────────────────────────────────────
CHROMA_PERSIST_DIR: str = os.getenv(
    "CHROMA_PERSIST_DIR", str(DATA_DIR / "chroma")
)
CHROMA_COLLECTION_NAME: str = os.getenv("CHROMA_COLLECTION_NAME", "sharepoint_docs")

# ── RAG tuning ─────────────────────────────────────────────────────────────
# k: number of chunks returned to the LLM. Higher = more context, higher cost.
RETRIEVER_K: int = int(os.getenv("RETRIEVER_K", "10"))
# fetch_k: MMR candidate pool size before diversity re-ranking (must be >= k).
# Larger values give MMR more candidates to diversify from.
RETRIEVER_FETCH_K: int = int(os.getenv("RETRIEVER_FETCH_K", "40"))
# Kept for reference / potential future use with similarity_score_threshold search type.
RETRIEVER_SCORE_THRESHOLD: float = float(os.getenv("RETRIEVER_SCORE_THRESHOLD", "0.35"))
CHUNK_SIZE: int = int(os.getenv("CHUNK_SIZE", "1000"))
CHUNK_OVERLAP: int = int(os.getenv("CHUNK_OVERLAP", "200"))

# ── Sync ───────────────────────────────────────────────────────────────────
SYNC_INTERVAL_HOURS: int = int(os.getenv("SYNC_INTERVAL_HOURS", "6"))
DELTA_TOKEN_PATH: str = os.getenv(
    "DELTA_TOKEN_PATH", str(DATA_DIR / "delta_token.json")
)

# ── App ────────────────────────────────────────────────────────────────────
APP_TITLE: str = os.getenv("APP_TITLE", "IT Infrastructure Knowledge Base")

# ── SSL (enterprise proxy) ─────────────────────────────────────────────────
# Set INSECURE=True only when behind a corporate SSL inspection proxy.
# WARNING: disables certificate verification for internal API calls.
INSECURE: bool = os.getenv("INSECURE", "False").lower() == "true"
