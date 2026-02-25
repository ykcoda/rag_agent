"""
LangChain Tool wrapping the SharePoint Chroma retriever.

Can be plugged into any LangChain agent that needs to search
the IT Infrastructure document library.

Example usage with an agent:
    from langchain.agents import create_openai_tools_agent
    from rag_agent.rag.tools import sharepoint_retriever_tool
    agent = create_openai_tools_agent(llm, [sharepoint_retriever_tool], prompt)
"""
from __future__ import annotations

from langchain_core.tools import Tool

from rag_agent import config
from rag_agent.rag.vectorstore import get_vectorstore


def _retrieve(query: str) -> str:
    """Run MMR similarity search and return formatted document excerpts."""
    vs = get_vectorstore()
    docs = vs.max_marginal_relevance_search(query, k=config.RETRIEVER_K)
    if not docs:
        return "No relevant documents found in the IT Infrastructure library."

    parts: list[str] = []
    for i, doc in enumerate(docs, 1):
        source = doc.metadata.get("source", "Unknown")
        modified = doc.metadata.get("last_modified", "")
        url = doc.metadata.get("web_url", "")
        header = f"[{i}] {source}"
        if modified:
            header += f"  (modified: {modified[:10]})"
        if url:
            header += f"\n    URL: {url}"
        parts.append(f"{header}\n\n{doc.page_content[:800]}")

    return "\n\n" + ("\n\n---\n\n".join(parts))


sharepoint_retriever_tool = Tool(
    name="sharepoint_it_retriever",
    func=_retrieve,
    description=(
        "Search the FidelityBank IT Infrastructure SharePoint document library. "
        "Use this tool to find procedures, policies, runbooks, network diagrams, "
        "and technical documentation. "
        "Input should be a specific question or keywords related to IT infrastructure."
    ),
)
