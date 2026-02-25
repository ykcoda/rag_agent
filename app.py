"""
IT Infrastructure Knowledge Base — Streamlit Chat Application

ChatGPT-style interface backed by:
  - SharePoint documents (MSAL + Microsoft Graph)
  - ChromaDB vector store (persistent, local)
  - LangChain LCEL conversational RAG chain
  - OpenAI GPT for generation + embeddings
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime

import streamlit as st

# ── Must be the FIRST Streamlit call ──────────────────────────────────────
st.set_page_config(
    page_title="IT Infrastructure KB",
    page_icon="🏛️",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={
        "Get Help": None,
        "Report a bug": None,
        "About": "IT Infrastructure Knowledge Base — FidelityBank",
    },
)

from rag_agent import config  # noqa: E402
from rag_agent.rag.vectorstore import get_doc_count  # noqa: E402
from rag_agent.rag.chain import (  # noqa: E402
    clear_session_history,
    get_chain,
    invalidate_chain,
    stream_answer,
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# ── Custom CSS for a polished chat look ────────────────────────────────────
st.markdown(
    """
    <style>
    /* Hide the default Streamlit footer */
    footer { visibility: hidden; }

    /* Chat message avatars */
    .stChatMessage [data-testid="stChatMessageAvatarUser"] {
        background-color: #0078d4;
    }

    /* Source expander styling */
    .source-header {
        font-size: 0.85rem;
        color: #6c757d;
        margin-top: 0.5rem;
    }

    /* Sidebar status indicators */
    .status-ok   { color: #28a745; font-weight: bold; }
    .status-warn { color: #ffc107; font-weight: bold; }
    </style>
    """,
    unsafe_allow_html=True,
)


# ── Session state initialisation ───────────────────────────────────────────

def _init_session() -> None:
    if "session_id" not in st.session_state:
        st.session_state.session_id = str(uuid.uuid4())

    # messages: list of dicts {role, content, sources?}
    if "messages" not in st.session_state:
        st.session_state.messages = []

    if "last_sync_time" not in st.session_state:
        st.session_state.last_sync_time = None


# ── Sidebar ────────────────────────────────────────────────────────────────

def render_sidebar() -> None:
    with st.sidebar:
        st.title("🏛️ IT Infra KB")
        st.caption("FidelityBank · IT Infrastructure Center")
        st.divider()

        # ── Status ────────────────────────────────────────────────────────
        st.subheader("📊 Status")

        doc_count = get_doc_count()
        if doc_count > 0:
            st.success(f"✅ {doc_count:,} chunks indexed")
        else:
            st.warning("⚠️ No documents indexed yet")

        st.caption(f"Model: `{config.OPENAI_MODEL}`")
        st.caption(f"Embeddings: `{config.OPENAI_EMBEDDING_MODEL}`")
        st.caption(f"Collection: `{config.CHROMA_COLLECTION_NAME}`")

        if st.session_state.last_sync_time:
            st.caption(f"Last sync: {st.session_state.last_sync_time}")

        st.divider()

        # ── Sync controls ─────────────────────────────────────────────────
        st.subheader("🔄 Data Sync")
        st.caption(
            "**Full Sync** clears and re-indexes all SharePoint documents.  \n"
            "**Delta Sync** fetches only changes since the last sync."
        )

        col_full, col_delta = st.columns(2)
        with col_full:
            if st.button(
                "⚡ Full",
                use_container_width=True,
                help="Re-index everything from SharePoint (slow)",
                key="btn_full_sync",
            ):
                _trigger_full_sync()

        with col_delta:
            if st.button(
                "🔄 Delta",
                use_container_width=True,
                help="Sync only new/changed/deleted files (fast)",
                key="btn_delta_sync",
            ):
                _trigger_delta_sync()

        st.divider()

        # ── Conversation controls ─────────────────────────────────────────
        st.subheader("💬 Conversation")
        if st.button("🗑️ Clear Chat", use_container_width=True, key="btn_clear"):
            st.session_state.messages = []
            clear_session_history(st.session_state.session_id)
            # New session so history starts fresh
            st.session_state.session_id = str(uuid.uuid4())
            st.rerun()

        st.divider()

        # ── Info ──────────────────────────────────────────────────────────
        with st.expander("ℹ️ About", expanded=False):
            st.markdown(
                "This assistant answers questions from the **IT Infrastructure "
                "Document Library** on SharePoint.\n\n"
                "Documents are indexed into a local **ChromaDB** vector store "
                "and retrieved using semantic (MMR) search.\n\n"
                "All answers are grounded in your internal documentation "
                "with source citations provided below each response."
            )


def _trigger_full_sync() -> None:
    with st.spinner("Running full ingestion — may take several minutes..."):
        try:
            from rag_agent.scripts.ingest import run_ingest

            total = run_ingest()
            st.session_state.last_sync_time = datetime.now().strftime("%Y-%m-%d %H:%M")
            st.success(f"Full sync complete — {total:,} chunks indexed.")
            st.rerun()
        except Exception as exc:
            st.error(f"Full sync failed: {exc}")
            log.exception("Full sync error")


def _trigger_delta_sync() -> None:
    with st.spinner("Syncing changes from SharePoint..."):
        try:
            from rag_agent.scripts.sync import run_sync

            count = run_sync()
            st.session_state.last_sync_time = datetime.now().strftime("%Y-%m-%d %H:%M")
            if count > 0:
                st.success(f"Delta sync — {count} items updated.")
            else:
                st.info("Delta sync complete — no changes found.")
            st.rerun()
        except Exception as exc:
            st.error(f"Delta sync failed: {exc}")
            log.exception("Delta sync error")


# ── Source citations renderer ──────────────────────────────────────────────

def _render_sources(sources: list[dict]) -> None:
    """Render document citations inside a collapsible expander."""
    if not sources:
        return

    # Deduplicate by source filename
    seen: set[str] = set()
    unique_sources = []
    for s in sources:
        name = s.get("source", "Unknown")
        if name not in seen:
            seen.add(name)
            unique_sources.append(s)

    with st.expander(f"📚 Sources ({len(unique_sources)} document(s))", expanded=False):
        for s in unique_sources:
            name = s.get("source", "Unknown")
            url = s.get("url", "")
            excerpt = s.get("excerpt", "")
            modified = s.get("last_modified", "")

            if url:
                st.markdown(f"**[{name}]({url})**")
            else:
                st.markdown(f"**{name}**")

            if modified:
                st.caption(f"Last modified: {modified[:10]}")

            if excerpt:
                st.text(excerpt[:300] + ("..." if len(excerpt) >= 300 else ""))

            st.divider()


# ── Chat history renderer ──────────────────────────────────────────────────

def render_chat() -> None:
    st.title(config.APP_TITLE)
    st.caption(
        "Answers are grounded in your SharePoint IT Infrastructure documentation."
    )

    # Empty-state prompt
    if get_doc_count() == 0:
        st.info(
            "📭 **No documents indexed yet.**  \n"
            "Click **⚡ Full** in the sidebar to index your SharePoint documents."
        )

    # Render conversation history
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg.get("sources"):
                _render_sources(msg["sources"])

    # ── Chat input ────────────────────────────────────────────────────────
    input_disabled = get_doc_count() == 0
    user_input = st.chat_input(
        "Ask about IT infrastructure procedures, policies, runbooks...",
        disabled=input_disabled,
    )

    if not user_input:
        return

    # Display user message
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    # Stream assistant response
    with st.chat_message("assistant"):
        placeholder = st.empty()
        full_answer = ""
        context_docs: list = []

        try:
            for token, docs in stream_answer(user_input, st.session_state.session_id):
                if token:
                    full_answer += token
                    placeholder.markdown(full_answer + "▌")
                if docs:
                    context_docs = docs

            placeholder.markdown(full_answer)

        except Exception as exc:
            full_answer = (
                f"❌ **Error generating response:** {exc}\n\n"
                "_Check your OpenAI API key and that documents are indexed._"
            )
            placeholder.markdown(full_answer)
            log.exception("Chain invocation error")

        # Render sources beneath the answer
        source_list = [
            {
                "source": d.metadata.get("source", "Unknown"),
                "url": d.metadata.get("web_url", ""),
                "excerpt": d.page_content,
                "last_modified": d.metadata.get("last_modified", ""),
            }
            for d in context_docs
        ]
        if source_list:
            _render_sources(source_list)

    # Persist assistant message to history
    st.session_state.messages.append(
        {
            "role": "assistant",
            "content": full_answer,
            "sources": source_list,
        }
    )


# ── Entry point ────────────────────────────────────────────────────────────

def main() -> None:
    _init_session()
    render_sidebar()
    render_chat()


if __name__ == "__main__":
    main()
