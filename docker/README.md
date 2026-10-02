# Ciel 2.0 — Docker

Runs Ciel in a container. Two independent front-ends, same image, same
`Dockerfile` — kept as **separate compose files** so each can be built,
started, stopped, or deployed to a different host without touching the other:

- `docker-compose.api.yml` → `ciel-api` — `main_api.py` (FastAPI + WebSocket
  backend) for the Flutter app in `ciel_app/`. CORS is open (`allow_origins=["*"]`) so
  the web build can call it from another origin; access is controlled by
  `CIEL_API_TOKEN`, not by CORS.
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
5. For the API specifically: `CIEL_API_TOKEN` (a long random string) before the port is
   reachable from any other device, and HTTPS in front of it (a Caddy/nginx reverse
   proxy) before it is reachable from the internet. Without the token the API logs a
   warning and accepts anyone who can reach the port.
6. `ciel_data/gmail_token.json` must be a valid, unexpired Gmail token. It cannot be
   created inside a headless container: authorize on a machine with a browser (delete
   the old token, run `python main.py`, approve in the browser), then copy the new file
   to `ciel_data/` on the server and restart the service.

## Run — API (Flutter app backend)

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

A Dockerfile change to the `apt-get` layer (for example adding `git`) invalidates every
later layer, so PyTorch and all requirements reinstall: expect 5–10 minutes. Code-only
changes rebuild from cache; most of the time is exporting the image.

`docker compose … restart` is enough after replacing a mounted file such as
`ciel_data/gmail_token.json`; it needs no rebuild.

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

## Git inside the container

The image installs `git` but copies no `.git` (`.dockerignore`). Both compose files mount
the host checkout read-only at `/repo` and set `CIEL_REPO_PATH=/repo`, so Ciel's git
tools and `git -C /repo log` show the deployed code's status, diff, and history. The
mount is read-only: Ciel cannot commit, pull, or push from the container. It does expose
the host `.env` to the container read-only; its values are already in the container
environment, and reading it still requires an approved shell command.

## Notes

- Change the API's host port with `CIEL_PORT` (e.g. `CIEL_PORT=8080 docker
  compose -f docker/docker-compose.api.yml up -d`) instead of editing the file.
- Both services are verified working end-to-end against a real Docker daemon
  (build, `/health`, `/skills`, container logs, and a live Telegram round-trip)
  — not just written blind.
