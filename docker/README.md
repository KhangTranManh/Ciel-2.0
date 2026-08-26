# Ciel 2.0 — Docker

Runs Ciel in a container. Two independent front-ends, same image, same
`Dockerfile` — kept as **separate compose files** so each can be built,
started, stopped, or deployed to a different host without touching the other:

- `docker-compose.api.yml` → `ciel-api` — `main_api.py` (FastAPI + WebSocket
  backend). The Vercel frontend talks to this over HTTP/WS; CORS is already
  open (`allow_origins=["*"]`), so no backend change is needed to point a
  Vercel deployment at it.
- `docker-compose.telegram.yml` → `ciel-telegram` — `main_telegram.py`, a
  Telegram bot front-end (see below).

## Layout

- `Dockerfile` — server image, shared by both services. Build context is the
  **repo root** (not this folder), so it can `COPY . .` the whole project.
- `docker-compose.api.yml` / `docker-compose.telegram.yml` — one service each.
- `requirements-docker.txt` — slimmed dependency set. Drops `pyautogui` /
  `pyperclip` (vision control — no display in a container) and
  `sounddevice` / `faster-whisper` / `SpeechRecognition` (mic capture — no
  input device in a container). `edge-tts` is kept: it's cloud TTS, no
  hardware needed, and `main_api.py` does use it. `sentence-transformers` is
  KEPT (tried dropping it in favor of ChromaDB's ONNX embedding function, but
  the real `ciel_data/vector_memory/` collection was created with
  sentence-transformers and ChromaDB persists that choice in the collection
  itself — a different embedding function at runtime doesn't migrate it, it
  just fails at query time). The `Dockerfile` installs a CPU-only `torch`
  wheel first specifically so this doesn't balloon into the ~10GB a default
  GPU build pulls in — expect ~3.5GB.

## Before first run

1. `.env` at the repo root must exist and be filled in (API keys, etc.) — both
   compose files load it via `env_file`. `DISABLED_SKILL_MODULES=vision_ops`
   is already set there, matching the requirements above (keep both in sync if
   you ever re-enable vision for a deployment that has a real display).
2. `credentials.json` (Google OAuth client secret, gitignored) must exist at
   the repo root — it's bind-mounted read-only into the container.
3. `ciel_data/`, `agent_output/`, `ciel_workspace/` at the repo root are
   bind-mounted so state (facts, memory, gmail token, logs, generated files)
   survives rebuilds. They don't need to pre-exist with content — an empty
   `ciel_data/` is fine, the app creates what it needs.
4. For the Telegram bot specifically: `TELEGRAM_BOT_TOKEN` and a real numeric
   `TELEGRAM_CHAT_ID` (message `@userinfobot` on Telegram, or hit `getUpdates`
   after messaging your own bot, to find it — it's a number, not any of your
   other API keys).

## Run — API (Vercel-facing)

```
docker compose -f docker/docker-compose.api.yml up -d --build
curl http://localhost:8000/health
docker compose -f docker/docker-compose.api.yml logs -f
docker compose -f docker/docker-compose.api.yml down
```

## Run — Telegram bot

```
docker compose -f docker/docker-compose.telegram.yml up -d --build
docker compose -f docker/docker-compose.telegram.yml logs -f
docker compose -f docker/docker-compose.telegram.yml down
```

The Telegram compose deliberately disables the shared image's `GET /health` check:
that endpoint belongs to the API entry point and a long-polling bot has no listener on
port 8000. Judge the Telegram deployment from `docker compose ... ps` and the
`[Telegram] Bot online` log instead.

### Update an existing Telegram deployment

The Dockerfile uses `COPY . .`, so every Python/source, dependency, or Dockerfile
change requires an image rebuild. An `.env`-only change needs recreation but not a
rebuild:

```
# Source, dependencies, or Dockerfile changed
docker compose -f docker/docker-compose.telegram.yml up -d --build --force-recreate

# Only .env changed
docker compose -f docker/docker-compose.telegram.yml up -d --force-recreate

docker compose -f docker/docker-compose.telegram.yml ps
docker compose -f docker/docker-compose.telegram.yml logs -f --tail=100 ciel-telegram
```

Do not place the server address, SSH password, bot token, OAuth credentials, or copied
`.env` values in this repository. The server keeps those files privately alongside the
checked-out project; persistent `ciel_data/` remains mounted across recreations.

## Shared memory, and why not to run both yet

Both files mount the SAME `ciel_data/agent_output/ciel_workspace` on purpose —
whichever front-end the Master used, the other one sees the same facts, chat
history, and long-term (RAG) memory. That's also the reason **not** to run
`ciel-api` and `ciel-telegram` at the same time yet: CielCore's state is
JSON-file / SQLite-backed, built for one writer, and two independent processes
touching it concurrently is a real race condition, not a theoretical one.
Treat them as alternatives to pick one from for now, not a pair to run
together — until that's addressed with a proper shared store or a lock.

## Notes

- Change the API's host port with `CIEL_PORT` (e.g. `CIEL_PORT=8080 docker
  compose -f docker/docker-compose.api.yml up -d`) instead of editing the file.
- Both services are verified working end-to-end against a real Docker daemon
  (build, `/health`, `/skills`, container logs, and a live Telegram round-trip)
  — not just written blind.
