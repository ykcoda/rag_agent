FROM python:3.12-slim

# ── System dependencies ────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    curl \
    poppler-utils \
    libmagic1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Install uv ────────────────────────────────────────────────────────────
RUN pip install --no-cache-dir uv

# ── Copy dependency manifests and install (layer-cached) ──────────────────
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# ── Copy application source ────────────────────────────────────────────────
COPY . .

# ── Data directory (overridden by volume in production) ───────────────────
RUN mkdir -p /app/data/chroma

# ── Streamlit port ────────────────────────────────────────────────────────
EXPOSE 8501

# ── Health check ──────────────────────────────────────────────────────────
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8501/_stcore/health || exit 1

# ── Default command: Streamlit app ────────────────────────────────────────
CMD ["uv", "run", "streamlit", "run", "app.py", \
     "--server.address=0.0.0.0", \
     "--server.port=8501", \
     "--server.headless=true", \
     "--browser.gatherUsageStats=false", \
     "--server.enableCORS=false", \
     "--server.enableXsrfProtection=true"]
