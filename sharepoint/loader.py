"""
Downloads SharePoint file content and converts it to chunked LangChain Documents.

Supported formats: .pdf, .docx, .txt, .md, .xlsx
"""
from __future__ import annotations

import io
import logging
import os
import tempfile
from pathlib import Path

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from rag_agent import config
from rag_agent.sharepoint.client import SharePointClient

log = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".pdf", ".doc", ".docx", ".xlsx"}


# ── Per-format loaders ─────────────────────────────────────────────────────

def _load_pdf(content: bytes, meta: dict) -> list[Document]:
    from langchain_community.document_loaders import PyPDFLoader

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(content)
        tmp = f.name
    try:
        docs = PyPDFLoader(tmp).load()
        return docs
    finally:
        os.unlink(tmp)


def _load_docx(content: bytes, meta: dict) -> list[Document]:
    from langchain_community.document_loaders import Docx2txtLoader

    with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as f:
        f.write(content)
        tmp = f.name
    try:
        return Docx2txtLoader(tmp).load()
    finally:
        os.unlink(tmp)


def _load_doc(content: bytes, meta: dict) -> list[Document]:
    """Load legacy .doc files via docx2txt (best-effort; falls back to empty)."""
    with tempfile.NamedTemporaryFile(suffix=".doc", delete=False) as f:
        f.write(content)
        tmp = f.name
    try:
        import docx2txt
        text = docx2txt.process(tmp)
        if text and text.strip():
            return [Document(page_content=text, metadata=meta)]
        log.warning("docx2txt returned empty content for '%s'", meta.get("source"))
        return []
    finally:
        os.unlink(tmp)


def _load_xlsx(content: bytes, meta: dict) -> list[Document]:
    try:
        import openpyxl
    except ImportError:
        log.warning("openpyxl not installed — skipping %s", meta.get("source"))
        return []

    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    sheets: list[str] = []
    for sheet in wb.worksheets:
        rows = []
        for row in sheet.iter_rows(values_only=True):
            row_str = "\t".join(str(c) for c in row if c is not None)
            if row_str.strip():
                rows.append(row_str)
        if rows:
            sheets.append(f"Sheet: {sheet.title}\n" + "\n".join(rows))

    text = "\n\n".join(sheets)
    return [Document(page_content=text, metadata=meta)] if text.strip() else []


# ── Public helpers ─────────────────────────────────────────────────────────

def file_to_documents(item: dict, content: bytes) -> list[Document]:
    """
    Convert a Graph API file item + raw bytes into chunked LangChain Documents.
    Returns an empty list for unsupported or unloadable files.
    """
    name: str = item.get("name", "unknown")
    ext = Path(name).suffix.lower()

    if ext not in SUPPORTED_EXTENSIONS:
        log.debug("Skipping unsupported file type: %s", name)
        return []

    # Extract the folder path from the Graph API parentReference
    # e.g. "/drives/{id}/root:/Memos/2026" → "Memos/2026"
    folder_path = (
        item.get("parentReference", {})
        .get("path", "")
        .split("/root:", 1)[-1]
        .lstrip("/")
    )

    base_meta = {
        "source": name,
        "sp_item_id": item.get("id", ""),
        "sp_etag": item.get("eTag", ""),
        "last_modified": item.get("lastModifiedDateTime", ""),
        "file_size": item.get("size", 0),
        "web_url": item.get("webUrl", ""),
        "folder_path": folder_path,
    }

    try:
        if ext == ".pdf":
            raw_docs = _load_pdf(content, base_meta)
        elif ext == ".docx":
            raw_docs = _load_docx(content, base_meta)
        elif ext == ".doc":
            raw_docs = _load_doc(content, base_meta)
        elif ext == ".xlsx":
            raw_docs = _load_xlsx(content, base_meta)
        else:
            return []
    except Exception as exc:
        log.error("Failed to parse '%s': %s", name, exc)
        return []

    # Enrich metadata on every page/document; ensure 'page' always exists
    for doc in raw_docs:
        doc.metadata.update(base_meta)
        doc.metadata.setdefault("page", "")

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=config.CHUNK_SIZE,
        chunk_overlap=config.CHUNK_OVERLAP,
        separators=["\n\n", "\n", " ", ""],
    )
    chunks = splitter.split_documents(raw_docs)

    # Prepend a document header to every chunk so the filename and folder are
    # part of the indexed text. This lets queries like "list all approved memos
    # 2026" find documents by name even when the content doesn't repeat the title.
    doc_header = f"[Document: {name}"
    if folder_path:
        doc_header += f" | Folder: {folder_path}"
    doc_header += "]\n"
    for chunk in chunks:
        chunk.page_content = doc_header + chunk.page_content

    log.debug("'%s' → %d chunks", name, len(chunks))
    return chunks


def load_from_sharepoint(
    client: SharePointClient, items: list[dict]
) -> list[Document]:
    """
    Download and chunk all given SharePoint file items.
    Skips unsupported file types silently; logs errors without raising.
    Returns a flat list of Document chunks.
    """
    all_docs: list[Document] = []
    total = len(items)

    for i, item in enumerate(items, 1):
        name = item.get("name", "?")
        ext = Path(name).suffix.lower()

        if ext not in SUPPORTED_EXTENSIONS:
            log.debug("[%d/%d] Skipping %s (unsupported)", i, total, name)
            continue

        log.info("[%d/%d] Downloading: %s", i, total, name)
        try:
            content = client.download_file(item)
            chunks = file_to_documents(item, content)
            all_docs.extend(chunks)
        except Exception as exc:
            log.error("[%d/%d] Error processing '%s': %s", i, total, name, exc)

    log.info("Loaded %d total chunks from %d files.", len(all_docs), total)
    return all_docs
