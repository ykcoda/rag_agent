"""
Incremental sync script — fetches only changes since the last delta token
and updates Chroma accordingly (add new, update modified, remove deleted).

On first run (no delta token saved), it behaves like a full delta pull.
Run ingest.py first for the initial bulk load.

Usage:
    # One-time sync:
    uv run python -m rag_agent.scripts.sync

    # Continuous scheduler (runs every SYNC_INTERVAL_HOURS hours):
    uv run python -m rag_agent.scripts.sync --scheduler

    # Docker service command:
    uv run python -m rag_agent.scripts.sync --scheduler
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
log = logging.getLogger(__name__)


def run_sync() -> int:
    """
    Perform one incremental sync cycle.
    Returns the number of items processed (added + deleted).
    """
    from rag_agent.sharepoint.client import SharePointClient
    from rag_agent.sharepoint.loader import load_from_sharepoint
    from rag_agent.rag.vectorstore import upsert_documents, delete_by_item_ids
    from rag_agent.rag.chain import invalidate_chain

    log.info("--- Incremental SharePoint Sync ---")
    client = SharePointClient()

    new_or_modified, deleted, delta_link = client.get_delta()

    processed = 0

    # Handle deletions first
    if deleted:
        ids = [item["id"] for item in deleted]
        log.info("Removing %d deleted items from Chroma...", len(ids))
        delete_by_item_ids(ids)
        processed += len(ids)

    # Handle new/modified files
    if new_or_modified:
        log.info("Processing %d new/modified files...", len(new_or_modified))
        docs = load_from_sharepoint(client, new_or_modified)
        if docs:
            # Collect unique SharePoint item IDs to replace their old chunks
            item_ids = list(
                {d.metadata["sp_item_id"] for d in docs if d.metadata.get("sp_item_id")}
            )
            upsert_documents(docs, item_ids=item_ids)
            processed += len(docs)

    # Persist the new delta token regardless of whether there were changes
    if delta_link:
        client.save_delta_token(delta_link)

    # Rebuild chain so new embeddings are picked up immediately
    if processed > 0:
        invalidate_chain()
        log.info("Sync complete: %d items processed.", processed)
    else:
        log.info("Sync complete: no changes detected.")

    return processed


def run_scheduler() -> None:
    """Continuous sync loop. Runs run_sync() every SYNC_INTERVAL_HOURS hours."""
    from rag_agent import config

    interval_secs = config.SYNC_INTERVAL_HOURS * 3600
    log.info(
        "Scheduler started. Syncing every %d hour(s). Press Ctrl+C to stop.",
        config.SYNC_INTERVAL_HOURS,
    )

    while True:
        try:
            run_sync()
        except KeyboardInterrupt:
            log.info("Scheduler stopped by user.")
            break
        except Exception as exc:
            log.error("Sync cycle failed: %s", exc, exc_info=True)

        log.info("Next sync in %d hour(s). Sleeping...", config.SYNC_INTERVAL_HOURS)
        try:
            time.sleep(interval_secs)
        except KeyboardInterrupt:
            log.info("Scheduler stopped by user.")
            break


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="SharePoint incremental sync for RAG agent"
    )
    parser.add_argument(
        "--scheduler",
        action="store_true",
        help="Run as a continuous background scheduler",
    )
    args = parser.parse_args()

    if args.scheduler:
        run_scheduler()
    else:
        count = run_sync()
        sys.exit(0 if count >= 0 else 1)
