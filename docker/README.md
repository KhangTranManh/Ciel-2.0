# Ciel 2.0 — Docker

Runs `main_api.py` (FastAPI + WebSocket backend) in a container. The frontend
(Vercel) talks to this over HTTP/WS; CORS in `main_api.py` is already open
(`allow_origins=["*"]`), so no backend change is needed to point a Vercel
deployment at it.

## Layout

- `Dockerfile` — server image. Build context is the **repo root** (not this
  folder), so it can `COPY . .` the whole project.
- `docker-compose.yml` — one service, `ciel-api`, port `8000` by default.
- `requirements-docker.txt` — slimmed dependency set. Drops `pyautogui` /
  `pyperclip` (vision control — no display in a container) and
  `sounddevice` / `faster-whisper` / `SpeechRecognition` (mic capture — no
  input device in a container). `edge-tts` is kept: it's cloud TTS, no
  hardware needed, and `main_api.py` does use it.

## Before first run

1. `.env` at the repo root must exist and be filled in (API keys, etc.) —
   `docker-compose.yml` loads it via `env_file`. `DISABLED_SKILL_MODULES=vision_ops`
   is already set there, matching the requirements above (keep both in sync if
   you ever re-enable vision for a deployment that has a real display).
2. `credentials.json` (Google OAuth client secret, gitignored) must exist at
   the repo root — it's bind-mounted read-only into the container.
3. `ciel_data/`, `agent_output/`, `ciel_workspace/` at the repo root are
   bind-mounted so state (facts, memory, gmail token, logs, generated files)
   survives rebuilds. They don't need to pre-exist with content — an empty
   `ciel_data/` is fine, the app creates what it needs.

## Run

```
docker compose -f docker/docker-compose.yml up -d --build
```

Check it's up:

```
curl http://localhost:8000/health
```

Logs:

```
docker compose -f docker/docker-compose.yml logs -f
```

Stop:

```
docker compose -f docker/docker-compose.yml down
```

## Notes

- Change the host port with `CIEL_PORT` (e.g. `CIEL_PORT=8080 docker compose ... up -d`)
  instead of editing the compose file.
- This was authored without a local Docker daemon available to build/run it end
  to end — sanity-check the first build output, in particular that
  `chromadb`/`sentence-transformers`/`torch` resolve to prebuilt wheels for
  `python:3.13-slim` (linux/amd64) rather than trying to compile from source.
