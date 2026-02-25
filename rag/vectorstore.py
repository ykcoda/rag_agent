"""
Persistent Chroma vector store with OpenAI embeddings.

Uses the chromadb PersistentClient directly for low-level operations
(count, upsert, delete) and wraps it in a LangChain Chroma object for
retrieval.
"""
from __future__ import annotations

import logging
from pathlib import Path

import chromadb
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings

from rag_agent import config

log = logging.getLogger(__name__)


def _embeddings() -> OpenAIEmbeddings:
    return OpenAIEmbeddings(
        model=config.OPENAI_EMBEDDING_MODEL,
        openai_api_key=config.OPENAI_API_KEY,
    )


def _chroma_client() -> chromadb.PersistentClient:
    Path(config.CHROMA_PERSIST_DIR).mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=config.CHROMA_PERSIST_DIR)


# ── Public API ─────────────────────────────────────────────────────────────

def get_vectorstore() -> Chroma:
    """Return the LangChain Chroma wrapper (creates collection if absent)."""
    return Chroma(
        collection_name=config.CHROMA_COLLECTION_NAME,
        embedding_function=_embeddings(),
        persist_directory=config.CHROMA_PERSIST_DIR,
    )


def get_doc_count() -> int:
    """Number of embedded chunks currently in the collection."""
    try:
        client = _chroma_client()
        col = client.get_or_create_collection(config.CHROMA_COLLECTION_NAME)
        return col.count()
    except Exception:
        return 0


def clear_collection() -> None:
    """Delete every chunk from the collection (used before full re-ingest)."""
    try:
        client = _chroma_client()
        client.delete_collection(config.CHROMA_COLLECTION_NAME)
        client.create_collection(config.CHROMA_COLLECTION_NAME)
        log.info("Collection '%s' cleared.", config.CHROMA_COLLECTION_NAME)
    except Exception as exc:
        log.warning("Could not clear collection: %s", exc)


def upsert_documents(docs: list[Document], item_ids: list[str] | None = None) -> None:
    """
    Add (or replace) documents in Chroma.

    If item_ids is provided, all existing chunks for those SharePoint item IDs
    are deleted first to avoid duplicates.
    """
    if item_ids:
        delete_by_item_ids(item_ids)

    if not docs:
        return

    vs = get_vectorstore()
    batch_size = 500
    for i in range(0, len(docs), batch_size):
        vs.add_documents(docs[i : i + batch_size])
    log.info("Upserted %d chunks into Chroma.", len(docs))


def delete_by_item_ids(item_ids: list[str]) -> None:
    """Remove all chunks whose metadata field 'sp_item_id' matches any of item_ids."""
    if not item_ids:
        return

    client = _chroma_client()
    col = client.get_or_create_collection(config.CHROMA_COLLECTION_NAME)

    deleted_total = 0
    for item_id in item_ids:
        try:
            # Chroma filter for single value (avoids $in edge-cases)
            result = col.get(where={"sp_item_id": item_id}, include=[])
            ids = result.get("ids", [])
            if ids:
                col.delete(ids=ids)
                deleted_total += len(ids)
        except Exception as exc:
            log.warning("Could not delete chunks for item '%s': %s", item_id, exc)

    if deleted_total:
        log.info(
            "Deleted %d stale chunks for %d items.", deleted_total, len(item_ids)
        )
