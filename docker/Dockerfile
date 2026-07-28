# Ciel 2.0 — API server image.
# Build context is the REPO ROOT (see docker-compose.yml: `context: ..`), so
# COPY paths below are relative to the project root, not this docker/ folder.

FROM python:3.13-slim AS base

# build-essential: some deps (chromadb's sqlite extensions, sentence-transformers'
# tokenizers) need a compiler if no prebuilt wheel matches the image's platform.
# curl: used by the container HEALTHCHECK to hit /health.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first so this layer is cached across code-only changes.
COPY docker/requirements-docker.txt ./requirements-docker.txt
RUN pip install --no-cache-dir -r requirements-docker.txt

COPY . .

# Persistent state lives under these paths — see docker-compose.yml's volumes.
# Created here so a fresh volume mount still has a directory to land in.
RUN mkdir -p ciel_data/logs ciel_data/vector_memory ciel_data/state \
             agent_output ciel_workspace

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "main_api:app", "--host", "0.0.0.0", "--port", "8000"]
