# Ciel 2.0 — one image: API + optional in-process Telegram.
# Build context is the REPO ROOT (`context: ..` in docker/docker-compose.yml), so COPY
# paths are relative to the project root, not this docker/ folder.

FROM python:3.13-slim AS runtime

# build-essential: some deps (chromadb's sqlite extensions) need a compiler if
# no prebuilt wheel matches the image's platform.
# curl: used by the container HEALTHCHECK to hit /health.
# git: git tools read the host checkout mounted read-only at /repo; it is owned by
# the host user, so it must be marked safe or git refuses to open it as root.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        git \
    && rm -rf /var/lib/apt/lists/* \
    && git config --system --add safe.directory /repo

WORKDIR /app

# Dependencies first so this layer is cached across code-only changes.
COPY docker/requirements-docker.txt ./requirements-docker.txt
# CPU-only torch FIRST: sentence-transformers pulls torch as a dependency, and left
# to its own defaults pip resolves the full CUDA build — 1.7GB+ of NVIDIA libraries
# with no GPU in this container to use them.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir -r requirements-docker.txt

COPY . .

# Persistent state lives under these paths — see docker-compose.yml's volumes.
# Created here so a fresh volume mount still has a directory to land in.
RUN mkdir -p ciel_data/logs ciel_data/vector_memory ciel_data/state \
             agent_output ciel_workspace

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "main_api:app", "--host", "0.0.0.0", "--port", "8000"]
