# IT Infrastructure Knowledge Base — RAG Agent

A production-ready **Retrieval-Augmented Generation (RAG)** application that indexes documents from the FidelityBank SharePoint IT Infrastructure Document Library and provides a ChatGPT-style chat interface for querying them.

---

## Table of Contents

1. [Overview](#1-overview)
2. [Architecture](#2-architecture)
3. [Prerequisites](#3-prerequisites)
4. [Azure AD App Registration](#4-azure-ad-app-registration)
5. [Environment Configuration](#5-environment-configuration)
6. [Project Structure](#6-project-structure)
7. [First-Time Setup (Local)](#7-first-time-setup-local)
8. [Running the App (Local)](#8-running-the-app-local)
9. [Docker & Production Deployment](#9-docker--production-deployment)
10. [Data Sync Operations](#10-data-sync-operations)
11. [RAG Tuning Reference](#11-rag-tuning-reference)
12. [Troubleshooting & FAQ](#12-troubleshooting--faq)
13. [Component Reference](#13-component-reference)

---

## 1. Overview

| Capability | Detail |
|-----------|--------|
| **Data source** | SharePoint Online — IT Infrastructure Document Library |
| **Authentication** | MSAL client-credentials (service principal, no user login required) |
| **Supported file types** | `.pdf`, `.docx`, `.txt`, `.md`, `.xlsx` |
| **Vector store** | ChromaDB — persisted to disk (`data/chroma/`) |
| **Embeddings** | OpenAI `text-embedding-3-small` |
| **LLM** | OpenAI GPT (configurable via `OPENAI_MODEL`) |
| **Memory** | Per-session conversation history (in-process, UUID-keyed) |
| **Sync** | Full ingest + incremental delta sync (Graph API `$deltaLink`) |
| **Frontend** | Streamlit — real-time streaming responses with source citations |
| **Deployment** | Docker Compose (app + background scheduler) |
| **Observability** | LangSmith tracing (auto-enabled when `LANGSMITH_API_KEY` is set) |

---

## 2. Architecture

### Data Flow

```
SharePoint Online
  IT Infrastructure Document Lib
         │
         │  Microsoft Graph API
         │  (MSAL client credentials)
         ▼
  ┌─────────────────────┐
  │  sharepoint/client  │  ← Authenticates, lists files, downloads bytes
  │  sharepoint/loader  │  ← Parses PDF/DOCX/TXT/XLSX → text chunks
  └────────┬────────────┘
           │  LangChain Documents (with metadata)
           ▼
  ┌─────────────────────┐
  │   ChromaDB          │  ← Persistent vector store
  │   (data/chroma/)    │     Embeddings: text-embedding-3-small
  └────────┬────────────┘
           │  Semantic (MMR) retrieval
           ▼
  ┌─────────────────────────────────────────────────────────┐
  │  LangChain LCEL RAG Chain                               │
  │                                                         │
  │  1. History-aware retriever                             │
  │     └─ Rephrases question using chat history            │
  │  2. Stuff-documents QA chain                            │
  │     └─ Formats retrieved docs + history into prompt     │
  │  3. ChatOpenAI (streaming)                              │
  │     └─ Generates grounded answer                        │
  │  4. RunnableWithMessageHistory                          │
  │     └─ Persists turns in InMemoryChatMessageHistory      │
  └────────┬────────────────────────────────────────────────┘
           │  Streamed tokens + source documents
           ▼
  ┌─────────────────────┐
  │  Streamlit UI        │  ← Chat interface, sidebar controls, citations
  └─────────────────────┘
```

### Sync Strategy

```
First run                     Subsequent runs
──────────────────────────    ─────────────────────────────────
scripts/ingest.py             scripts/sync.py
  1. List ALL files              1. Fetch delta since last token
  2. Download + chunk all        2. Delete removed-file chunks
  3. Clear Chroma collection     3. Re-embed changed files only
  4. Embed + store all chunks    4. Save new delta token
  5. Save initial delta token
```

### Container Architecture (Docker Compose)

```
┌──────────────────────────────────────────────────┐
│  Docker Compose Stack                            │
│                                                  │
│  ┌─────────────┐    ┌──────────────────────────┐ │
│  │  rag-app    │    │  rag-scheduler           │ │
│  │  Streamlit  │    │  sync.py --scheduler     │ │
│  │  :8501      │    │  (every SYNC_INTERVAL_   │ │
│  └──────┬──────┘    │   HOURS hours)           │ │
│         │           └──────────┬───────────────┘ │
│         └─────────────────┬────┘                 │
│                           │ shared volume         │
│                   ┌───────▼──────────────┐        │
│                   │  data/               │        │
│                   │  ├── chroma/         │        │
│                   │  └── delta_token.json│        │
│                   └──────────────────────┘        │
└──────────────────────────────────────────────────┘
```

---

## 3. Prerequisites

### Software

| Tool | Version | Purpose |
|------|---------|---------|
| Python | 3.12+ | Runtime |
| [uv](https://docs.astral.sh/uv/) | latest | Package manager |
| Docker + Docker Compose | 24+ | Production deployment |

### External Services

| Service | Required | Notes |
|---------|----------|-------|
| OpenAI API | Yes | GPT generation + embeddings |
| Azure AD (Microsoft 365) | Yes | SharePoint authentication |
| LangSmith | Optional | Chain tracing & observability |

### Azure AD Permissions

The app registration (service principal) needs the following **Application permissions** (not delegated) with **admin consent**:

| Permission | Type | Used for |
|-----------|------|---------|
| `Sites.Read.All` | Application | Resolve the SharePoint site ID |
| `Files.Read.All` | Application | List and download library files |

---

## 4. Azure AD App Registration

Follow these steps in the **Azure Portal** to create a service principal for the agent.

### Step 1 — Create the registration

1. Go to **Azure Active Directory → App registrations → New registration**
2. **Name**: `rag-agent-it-infra` (or any descriptive name)
3. **Supported account types**: `Accounts in this organizational directory only (Single tenant)`
4. Leave Redirect URI blank → **Register**

### Step 2 — Create a client secret

1. Open the new registration → **Certificates & secrets → Client secrets → New client secret**
2. **Description**: `rag-agent-prod`
3. **Expires**: choose your rotation policy (e.g. 24 months)
4. Click **Add** → **copy the secret Value immediately** (it won't be shown again)

### Step 3 — Add API permissions

1. **API permissions → Add a permission → Microsoft Graph → Application permissions**
2. Search and add:
   - `Sites.Read.All`
   - `Files.Read.All`
3. Click **Grant admin consent for [your tenant]** → confirm

### Step 4 — Collect credentials

From the app registration **Overview** page, note:

| Field | Where to find it | `.env` variable |
|-------|-----------------|----------------|
| Tenant ID | Overview → Directory (tenant) ID | `SP_TENANT_ID` |
| Client ID | Overview → Application (client) ID | `SP_CLIENT_ID` |
| Client Secret | Step 2 value | `SP_CLIENT_SECRET` |

---

## 5. Environment Configuration

Copy `.env.example` to the **project root** (alongside `pyproject.toml`) and fill in the values:

```bash
cp .env.example .env
```

### Full Reference

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `OPENAI_API_KEY` | — | **Yes** | OpenAI API key |
| `OPENAI_MODEL` | `gpt-4o-mini` | No | Chat model for generation |
| `OPENAI_EMBEDDING_MODEL` | `text-embedding-3-small` | No | Embedding model |
| `SP_TENANT_ID` | — | **Yes** | Azure AD tenant GUID |
| `SP_CLIENT_ID` | — | **Yes** | App registration client GUID |
| `SP_CLIENT_SECRET` | — | **Yes** | App registration secret value |
| `SP_SITE_HOSTNAME` | `myfidelitybank.sharepoint.com` | No | SharePoint hostname |
| `SP_SITE_PATH` | `/sites/ITInfrastructureCenter` | No | Site-relative path |
| `SP_DRIVE_NAME` | `IT Infrastructure Document Lib` | No | Document library name |
| `CHROMA_PERSIST_DIR` | `./data/chroma` | No | Chroma storage path |
| `CHROMA_COLLECTION_NAME` | `sharepoint_docs` | No | Chroma collection name |
| `RETRIEVER_K` | `6` | No | Chunks returned per query |
| `CHUNK_SIZE` | `1000` | No | Characters per document chunk |
| `CHUNK_OVERLAP` | `200` | No | Overlap between adjacent chunks |
| `SYNC_INTERVAL_HOURS` | `6` | No | Hours between scheduled syncs |
| `DELTA_TOKEN_PATH` | `./data/delta_token.json` | No | Where to store the delta token |
| `APP_TITLE` | `IT Infrastructure Knowledge Base` | No | Streamlit page title |
| `APP_PORT` | `8501` | No | Host port for Docker mapping |
| `INSECURE` | `False` | No | Set `True` behind SSL-inspection proxy |
| `LANGSMITH_TRACING` | `false` | No | Enable LangSmith tracing |
| `LANGSMITH_API_KEY` | — | No | LangSmith API key |
| `LANGSMITH_PROJECT` | `yk-ai` | No | LangSmith project name |

> **Security note**: Never commit `.env` to version control. It is listed in `.gitignore`.

---

## 6. Project Structure

```
rag_agent/
│
├── app.py                    Streamlit chat UI (entry point)
├── config.py                 Central settings (reads from .env)
│
├── sharepoint/
│   ├── client.py             MSAL + Microsoft Graph API client
│   └── loader.py             File download, parsing, and chunking
│
├── rag/
│   ├── vectorstore.py        ChromaDB persistent store helpers
│   ├── chain.py              LangChain LCEL RAG chain + conversation memory
│   └── tools.py              LangChain Tool (usable in agent pipelines)
│
├── scripts/
│   ├── ingest.py             Full re-ingestion script
│   └── sync.py               Incremental delta sync + scheduler
│
├── data/                     Runtime data (git-ignored)
│   ├── chroma/               ChromaDB persistent vector store
│   └── delta_token.json      Graph API delta link for incremental sync
│
├── Dockerfile
├── docker-compose.yml
├── .env.example
└── README.md                 ← you are here
```

---

## 7. First-Time Setup (Local)

### 1. Install dependencies

```bash
# From the project root (where pyproject.toml lives)
uv sync
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env — fill in OPENAI_API_KEY, SP_TENANT_ID, SP_CLIENT_ID, SP_CLIENT_SECRET
```

### 3. Run the initial ingestion

This downloads **all** files from the SharePoint library, chunks them, embeds them, and stores them in ChromaDB. Only needs to be run once (or after a full reset).

```bash
uv run python -m rag_agent.scripts.ingest
```

Expected output:
```
2025-01-01 10:00:00  INFO  Full SharePoint → Chroma Ingestion
2025-01-01 10:00:01  INFO  Found 87 files in SharePoint drive.
2025-01-01 10:00:45  INFO  Loaded 1,243 total chunks from 87 files.
2025-01-01 10:01:30  INFO  Ingestion complete: 1,243 chunks indexed.
```

### 4. Verify the index

```bash
uv run python -c "from rag_agent.rag.vectorstore import get_doc_count; print(get_doc_count(), 'chunks')"
```

---

## 8. Running the App (Local)

```bash
uv run streamlit run app.py
```

Open `http://localhost:8501` in your browser.

### Sidebar Controls

| Button | Action |
|--------|--------|
| **⚡ Full** | Clears Chroma and re-indexes everything from SharePoint |
| **🔄 Delta** | Fetches only new/changed/deleted files since last sync |
| **🗑️ Clear Chat** | Resets the conversation history for the current session |

---

## 9. Docker & Production Deployment

### Prerequisites

- Docker Engine 24+
- Docker Compose plugin (`docker compose` — not the legacy `docker-compose`)

### Build and start

All commands should be run from the project root:

```bash
# Create the data directory on the host (bind-mounted into containers)
mkdir -p data

# Build images and start both services in the background
docker compose up --build -d
```

### Run the initial ingestion inside Docker

Only required the first time (before the app is usable):

```bash
docker compose exec rag-app uv run python -m rag_agent.scripts.ingest
```

### Check service status

```bash
docker compose ps

# Live logs
docker compose logs -f rag-app
docker compose logs -f rag-scheduler
```

### Stop services

```bash
docker compose down
```

### Stop and remove data volumes (destructive — deletes the index)

```bash
docker compose down -v
```

> **Warning**: `-v` removes the `rag_data` volume. This deletes the Chroma database. You will need to run a full ingest again.

### Update to the latest code

```bash
git pull
docker compose up --build -d
```

### Expose behind a reverse proxy (Nginx / Caddy)

Point your proxy to `http://127.0.0.1:8501`. Example Nginx location block:

```nginx
location / {
    proxy_pass         http://127.0.0.1:8501;
    proxy_http_version 1.1;
    proxy_set_header   Upgrade $http_upgrade;
    proxy_set_header   Connection "upgrade";
    proxy_set_header   Host $host;
    proxy_read_timeout 86400;
}
```

Streamlit requires WebSocket support (`Upgrade` / `Connection` headers).

---

## 10. Data Sync Operations

### Manual sync (one-time)

```bash
# Local
uv run python -m rag_agent.scripts.sync

# Docker
docker compose exec rag-app uv run python -m rag_agent.scripts.sync
```

### Continuous scheduler (standalone)

```bash
uv run python -m rag_agent.scripts.sync --scheduler
```

The scheduler runs `sync.py` every `SYNC_INTERVAL_HOURS` hours (default: 6). In Docker, the `rag-scheduler` service handles this automatically.

### Force a full re-ingest

Use when documents have been significantly reorganised, or if the Chroma index is suspected to be corrupt:

```bash
# Local
uv run python -m rag_agent.scripts.ingest

# Docker
docker compose exec rag-app uv run python -m rag_agent.scripts.ingest
```

### Reset the delta token

If you want the next scheduled sync to re-process all files (without clearing the index):

```bash
rm data/delta_token.json
```

The next `sync.py` run will use the full delta endpoint and re-process everything.

### How the delta sync works

```
1. Load saved delta link from data/delta_token.json
   └─ If absent → use /drives/{id}/root/delta (full initial delta)

2. Call Graph API with the delta link
   └─ Returns only items changed since the link was created

3. For deleted items:
   └─ Delete their chunks from Chroma (matched by sp_item_id metadata)

4. For new/modified items:
   └─ Download, re-chunk, delete old chunks, embed new chunks

5. Save the new delta link to data/delta_token.json
```

---

## 11. RAG Tuning Reference

| Parameter | Variable | Default | Effect |
|-----------|----------|---------|--------|
| Chunks returned per query | `RETRIEVER_K` | `6` | Higher → more context, higher cost & latency |
| Characters per chunk | `CHUNK_SIZE` | `1000` | Smaller → more precise retrieval; Larger → more context per chunk |
| Chunk overlap | `CHUNK_OVERLAP` | `200` | Higher → reduces information loss at chunk boundaries |
| Chat model | `OPENAI_MODEL` | `gpt-4o-mini` | Swap to `gpt-4o` for higher quality at higher cost |
| Embedding model | `OPENAI_EMBEDDING_MODEL` | `text-embedding-3-small` | `text-embedding-3-large` for better semantic accuracy |

**Retrieval method**: Maximum Marginal Relevance (MMR) with `fetch_k = RETRIEVER_K × 3`. MMR reduces redundancy by selecting diverse chunks rather than the top-K most similar.

---

## 12. Troubleshooting & FAQ

### MSAL / SharePoint errors

#### `MSAL authentication failed: AADSTS700016`
The `SP_CLIENT_ID` does not exist in the tenant. Verify the Application (client) ID is correct.

#### `MSAL authentication failed: AADSTS7000215`
Invalid client secret. The secret may have expired or been copied incorrectly. Regenerate it in the Azure Portal.

#### `Drive 'IT Infrastructure Document Lib' not found`
The `SP_DRIVE_NAME` doesn't match exactly. To find the correct name:
```bash
uv run python -c "
from rag_agent.sharepoint.client import SharePointClient
c = SharePointClient()
import json, requests
token = c._get_token()
r = requests.get(
    f'https://graph.microsoft.com/v1.0/sites/{c.get_site_id()}/drives',
    headers={'Authorization': f'Bearer {token}'}
)
print([d['name'] for d in r.json()['value']])
"
```

#### `SSLError: certificate verify failed`
You are behind a corporate SSL inspection proxy. Set `INSECURE=True` in `.env`.

> **Note**: `INSECURE=True` disables TLS verification for HTTP requests to the Graph API. Use only in trusted internal network environments.

---

### ChromaDB errors

#### `Collection 'sharepoint_docs' not found` or empty index
The initial ingestion has not been run yet. Execute:
```bash
uv run python -m rag_agent.scripts.ingest
```

#### `Chroma store at ./data/chroma is corrupt`
Delete the store and re-ingest:
```bash
rm -rf data/chroma data/delta_token.json
uv run python -m rag_agent.scripts.ingest
```

---

### OpenAI errors

#### `AuthenticationError: Incorrect API key`
Check `OPENAI_API_KEY` in `.env`. Ensure there are no leading/trailing spaces.

#### `NotFoundError: The model 'gpt-5-mini' does not exist`
`OPENAI_MODEL` in `.env` references a non-existent model. Change it to a valid model such as `gpt-4o-mini` or `gpt-4o`.

#### `RateLimitError` during ingestion
The embeddings API rate limit was hit. The ingest script does not retry automatically. Re-run `ingest.py` — it will skip already-indexed files on subsequent runs (via delta token).

---

### Streamlit / Docker

#### Port 8501 is already in use
Change the host port in `.env`: `APP_PORT=8502`, then restart with `docker compose up -d`.

#### Chat input is disabled (greyed out)
The Chroma collection is empty. Run a full sync via the sidebar **⚡ Full** button, or execute the ingest script from the command line.

#### Scheduler container keeps restarting
Check the logs: `docker compose logs rag-scheduler`. The most common cause is missing MSAL credentials. Verify `SP_TENANT_ID`, `SP_CLIENT_ID`, and `SP_CLIENT_SECRET` in `.env`.

#### Responses do not reflect recently uploaded documents
Delta sync has not run yet since the upload. Click **🔄 Delta** in the sidebar to trigger an immediate sync.

---

### FAQ

**Q: How long does a full ingest take?**
Depends on file count and size. A library of ~100 mixed PDF/DOCX files typically takes 3–8 minutes. Most time is spent on OpenAI embedding API calls.

**Q: Is conversation history preserved across browser refreshes?**
No. History is stored in Streamlit session state (in-process memory). Refreshing the page starts a new session. The Chroma index is persistent and survives restarts.

**Q: Can multiple users use the app simultaneously?**
Yes. Each browser session gets its own UUID `session_id` and isolated conversation history. The shared Chroma index is read-only during normal use.

**Q: How do I add support for a new file type?**
Add the extension to `SUPPORTED_EXTENSIONS` in [sharepoint/loader.py](sharepoint/loader.py) and implement a `_load_<ext>` function following the existing pattern.

**Q: Can I use Azure OpenAI instead of OpenAI direct?**
Not without code changes. To switch, replace `ChatOpenAI` and `OpenAIEmbeddings` in [rag/chain.py](rag/chain.py) and [rag/vectorstore.py](rag/vectorstore.py) with `AzureChatOpenAI` and `AzureOpenAIEmbeddings` from `langchain-openai`, and add the corresponding `AZURE_OPENAI_*` env vars.

---

## 13. Component Reference

| File | Class / Function | Purpose |
|------|-----------------|---------|
| [config.py](config.py) | module constants | Loads `.env` → typed config values |
| [sharepoint/client.py](sharepoint/client.py) | `SharePointClient` | MSAL auth, file listing, download, delta |
| [sharepoint/loader.py](sharepoint/loader.py) | `load_from_sharepoint()` | Downloads items → chunked `Document` list |
| [rag/vectorstore.py](rag/vectorstore.py) | `get_vectorstore()` | Returns LangChain `Chroma` wrapper |
| [rag/vectorstore.py](rag/vectorstore.py) | `get_doc_count()` | Returns total chunk count |
| [rag/vectorstore.py](rag/vectorstore.py) | `upsert_documents()` | Delete-then-add for modified files |
| [rag/vectorstore.py](rag/vectorstore.py) | `delete_by_item_ids()` | Remove chunks by SharePoint item ID |
| [rag/chain.py](rag/chain.py) | `get_chain()` | Returns (or builds) the singleton RAG chain |
| [rag/chain.py](rag/chain.py) | `stream_answer()` | Generator: `(token, context_docs)` tuples |
| [rag/chain.py](rag/chain.py) | `clear_session_history()` | Clears memory for a given session ID |
| [rag/chain.py](rag/chain.py) | `invalidate_chain()` | Forces chain rebuild after re-indexing |
| [rag/tools.py](rag/tools.py) | `sharepoint_retriever_tool` | LangChain `Tool` for use in agent pipelines |
| [scripts/ingest.py](scripts/ingest.py) | `run_ingest()` | Full ingestion — clears + re-embeds all |
| [scripts/sync.py](scripts/sync.py) | `run_sync()` | Incremental delta sync (one cycle) |
| [scripts/sync.py](scripts/sync.py) | `run_scheduler()` | Runs `run_sync()` on a fixed interval |
| [app.py](app.py) | `main()` | Streamlit entry point |

---

*Built with LangChain v1 · ChromaDB · OpenAI · MSAL · Streamlit*
