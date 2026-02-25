"""
Full ingestion script — clears Chroma and re-indexes all SharePoint documents.

Run once before starting the app, or whenever you want a complete re-index.

Usage:
    # From project root:
    uv run python -m rag_agent.scripts.ingest

    # Or directly:
    uv run python scripts/ingest.py
"""
from __future__ import annotations

import logging
import pickle
import sys
from pathlib import Path

# Allow running as a standalone script from the project root
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
log = logging.getLogger(__name__)


def run_ingest() -> int:
    """
    Full ingestion: clear Chroma, download all files, embed and store.

    A chunk cache is written to DATA_DIR after the (expensive) download+parse
    step.  If the script is re-run after a failed embedding run the cache is
    reused automatically, skipping the SharePoint download and saving both
    time and OpenAI embedding costs.  The cache is deleted on success.

    Returns the total number of chunks indexed.
    """
    from rag_agent import config  # noqa: F401 — triggers DATA_DIR creation
    from rag_agent.sharepoint.client import SharePointClient
    from rag_agent.sharepoint.loader import load_from_sharepoint
    from rag_agent.rag.vectorstore import clear_collection, get_vectorstore
    from rag_agent.rag.chain import invalidate_chain

    CACHE_PATH = Path(config.DATA_DIR) / "ingest_cache.pkl"

    log.info("=" * 60)
    log.info("Full SharePoint → Chroma Ingestion")
    log.info("=" * 60)

    # ── Load docs (use cache if a previous run failed mid-embedding) ─────────
    log.info("Connecting to SharePoint (MSAL)...")
    client = SharePointClient()

    if CACHE_PATH.exists():
        log.info("Resuming from chunk cache at '%s' (skipping SharePoint download)...", CACHE_PATH)
        with CACHE_PATH.open("rb") as f:
            docs = pickle.load(f)
        log.info("Cache loaded: %d chunks.", len(docs))
    else:
        log.info("Listing all files in '%s'...", config.SP_DRIVE_NAME)
        items = client.list_all_files()
        if not items:
            log.warning(
                "No files found. Verify SP_DRIVE_NAME and app permissions (Sites.Read.All)."
            )
            return 0

        log.info("Loading and chunking %d files...", len(items))
        docs = load_from_sharepoint(client, items)
        if not docs:
            log.warning("No documents could be loaded. Check file formats and logs above.")
            return 0

        log.info("Saving chunk cache to '%s' (safe to delete manually)...", CACHE_PATH)
        with CACHE_PATH.open("wb") as f:
            pickle.dump(docs, f)
    # ─────────────────────────────────────────────────────────────────────────

    log.info(
        "Clearing existing collection '%s'...", config.CHROMA_COLLECTION_NAME
    )
    clear_collection()

    log.info(
        "Embedding %d chunks → Chroma at '%s'...", len(docs), config.CHROMA_PERSIST_DIR
    )
    vs = get_vectorstore()
    batch_size = 500
    for i in range(0, len(docs), batch_size):
        batch = docs[i : i + batch_size]
        log.info("  Upserting chunks %d–%d...", i + 1, min(i + batch_size, len(docs)))
        vs.add_documents(batch)

    # Save delta token so the next sync only fetches changes
    log.info("Saving delta token for incremental future syncs...")
    _, _, delta_link = client.get_delta()
    if delta_link:
        client.save_delta_token(delta_link)
    else:
        log.warning("No delta link returned — next sync will fall back to full delta.")

    # Invalidate chain so it rebuilds with the fresh index
    invalidate_chain()

    # Everything succeeded — remove the cache so the next full ingest is fresh
    if CACHE_PATH.exists():
        CACHE_PATH.unlink()
        log.info("Chunk cache deleted.")

    log.info("=" * 60)
    log.info("Ingestion complete: %d chunks indexed.", len(docs))
    log.info("=" * 60)
    return len(docs)


if __name__ == "__main__":
    total = run_ingest()
    sys.exit(0 if total > 0 else 1)
