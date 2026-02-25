"""
Conversational RAG chain (LangChain v1 LCEL style).

Architecture:
  1. History-aware retriever   — rephrases questions using chat history
  2. Stuff-documents QA chain  — answers from retrieved context
  3. RunnableWithMessageHistory — wires per-session in-memory history

Memory is kept in-process (per session_id). Each Streamlit session gets its
own UUID session_id, so conversations are fully isolated.
"""
from __future__ import annotations

import logging
from typing import Generator

from langchain_classic.chains import create_history_aware_retriever, create_retrieval_chain
from langchain_classic.chains.combine_documents import create_stuff_documents_chain
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain_openai import ChatOpenAI

from rag_agent import config
from rag_agent.rag.vectorstore import get_vectorstore

log = logging.getLogger(__name__)

# ── In-process session store ───────────────────────────────────────────────
try:
    from langchain_core.chat_history import InMemoryChatMessageHistory
    _HistoryCls = InMemoryChatMessageHistory
except ImportError:
    from langchain_community.chat_message_histories import ChatMessageHistory
    _HistoryCls = ChatMessageHistory  # type: ignore[assignment]

_session_store: dict[str, object] = {}


def get_session_history(session_id: str):
    if session_id not in _session_store:
        _session_store[session_id] = _HistoryCls()
    return _session_store[session_id]


def clear_session_history(session_id: str) -> None:
    if session_id in _session_store:
        _session_store[session_id].clear()


# ── System prompts ─────────────────────────────────────────────────────────

_CONTEXTUALIZE_SYSTEM = (
    "Given a conversation history and the latest user question, rewrite the question "
    "as a complete standalone question that is fully understandable without the history. "
    "Do NOT answer — only reformulate if needed, otherwise return it unchanged."
)

_QA_SYSTEM = """\
You are an expert IT Infrastructure assistant for FidelityBank. \
Your answers are grounded exclusively in internal documentation retrieved from SharePoint.

Retrieved context:
{context}

Guidelines:
- Answer only from the context above. If the information is not there, say so clearly.
- Be concise and professional; use numbered steps for procedures.
- Cite the source document name when referencing specific information.
- If the question is unrelated to IT infrastructure, politely redirect the user.
"""

# ── Chain factory ──────────────────────────────────────────────────────────

def build_rag_chain() -> RunnableWithMessageHistory:
    llm = ChatOpenAI(
        model=config.OPENAI_MODEL,
        temperature=0,
        streaming=True,
        openai_api_key=config.OPENAI_API_KEY,
    )

    retriever = get_vectorstore().as_retriever(
        search_type="mmr",
        search_kwargs={
            "k": config.RETRIEVER_K,
            "fetch_k": config.RETRIEVER_K * 3,
        },
    )

    # Step 1 — history-aware retriever
    contextualize_prompt = ChatPromptTemplate.from_messages(
        [
            ("system", _CONTEXTUALIZE_SYSTEM),
            MessagesPlaceholder("chat_history"),
            ("human", "{input}"),
        ]
    )
    history_aware_retriever = create_history_aware_retriever(
        llm, retriever, contextualize_prompt
    )

    # Step 2 — QA chain that stuffs retrieved docs into the prompt
    qa_prompt = ChatPromptTemplate.from_messages(
        [
            ("system", _QA_SYSTEM),
            MessagesPlaceholder("chat_history"),
            ("human", "{input}"),
        ]
    )
    qa_chain = create_stuff_documents_chain(llm, qa_prompt)

    # Step 3 — combine into a full retrieval chain
    retrieval_chain = create_retrieval_chain(history_aware_retriever, qa_chain)

    # Step 4 — wrap with per-session message history
    return RunnableWithMessageHistory(
        retrieval_chain,
        get_session_history,
        input_messages_key="input",
        history_messages_key="chat_history",
        output_messages_key="answer",
    )


# ── Lazy singleton ─────────────────────────────────────────────────────────

_chain: RunnableWithMessageHistory | None = None


def get_chain() -> RunnableWithMessageHistory:
    global _chain
    if _chain is None:
        _chain = build_rag_chain()
    return _chain


def invalidate_chain() -> None:
    """Force chain rebuild on the next call (e.g. after re-indexing)."""
    global _chain
    _chain = None


# ── Streaming helper ───────────────────────────────────────────────────────

def stream_answer(
    question: str,
    session_id: str,
) -> Generator[tuple[str, list], None, None]:
    """
    Stream the RAG answer token-by-token.

    Yields (token: str, context_docs: list) tuples.
    - While streaming: token is non-empty, context_docs is [].
    - Final yield: token is "", context_docs contains retrieved Documents.
    """
    chain = get_chain()
    context_docs: list = []

    for chunk in chain.stream(
        {"input": question},
        config={"configurable": {"session_id": session_id}},
    ):
        if "answer" in chunk and chunk["answer"]:
            yield chunk["answer"], []
        if "context" in chunk:
            context_docs = chunk["context"]

    yield "", context_docs
