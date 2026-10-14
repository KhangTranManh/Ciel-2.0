# Ciel 2.0 — Docker deployment

One container, `ciel`, runs everything that writes Ciel's state:

```text
                 Cloudflare (HTTPS, your hostname)
                         │  outbound-only tunnel
   ┌─────────────────────┼──────────────────────────────────────────┐
   │ VPS                 ▼                                          │
   │   ciel-tunnel ──► ciel:8000                                    │
   │                    ├─ /            dashboard (Flutter web)     │
   │                    ├─ /ws, /skills, /tts   API (token)         │
   │                    ├─ Telegram bot (long polling, in-process)  │
   │                    └─ scheduler (planner, reminders)           │
   │   127.0.0.1:8000 ─► ciel  (local checks only)                  │
   └────────────────────────────────────────────────────────────────┘
```

The phone/desktop app connects to the same hostname as the dashboard.

## Files

| File | Purpose |
|---|---|
| `Dockerfile` | Two stages: `dashboard` compiles `ciel_app/` for the web (Flutter 3.35.4); `runtime` is Python + the compiled dashboard at `/app/web`. Build context is the repo root. |
| `docker-compose.yml` | The deployment: `ciel`, plus `cloudflared` under the `tunnel` profile. |
| `tunnel.env.example` | Shape of `docker/tunnel.env` (gitignored): the Cloudflare tunnel token only. |
| `requirements-docker.txt` | Server dependency set without desktop-only packages (screen control, microphone). |

Why one container: Ciel's state (`ciel_data/`) is JSON/SQLite built for one writer.
API and Telegram used to be separate containers that could not run together; now the
bot runs inside the API process (`CIEL_API_TELEGRAM=true`, set in the compose file) and
turns from all front ends run one at a time.

`sentence-transformers` stays in the requirements: the existing
`ciel_data/vector_memory/` collection was created with it, and ChromaDB does not
migrate a collection to a different embedding function. A CPU-only `torch` is installed
first so the image stays around 3.5 GB instead of a ~10 GB GPU build.

## First-time setup

On the server, in the checked-out repository:

1. **`.env`** at the repo root, filled in (model keys, `TELEGRAM_BOT_TOKEN`,
   `TELEGRAM_CHAT_ID`, `DISABLED_SKILL_MODULES=vision_ops`) and:
   ```dotenv
   CIEL_API_TOKEN=<long random string>     # python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```
   Every route except `/health` and the dashboard files requires it.
2. **`credentials.json`** (Google OAuth client) at the repo root.
3. **`ciel_data/gmail_token.json`**: a valid Gmail token. It cannot be created inside a
   headless container. Authorize on a PC with a browser (delete the old token, run
   `python main.py`, approve), copy the file to `ciel_data/` on the server, then
   `docker compose -f docker/docker-compose.yml restart ciel`.
4. **Cloudflare Tunnel** (for the public HTTPS address):
   1. Cloudflare dashboard → **Zero Trust → Networks → Tunnels → Create a tunnel** →
      *Cloudflared* → name it (e.g. `ciel`).
   2. From the shown install command, copy only the token (the long string after
      `--token`) into `docker/tunnel.env`:
      ```bash
      cp docker/tunnel.env.example docker/tunnel.env && nano docker/tunnel.env
      chmod 600 docker/tunnel.env
      ```
   3. In the tunnel → **Public Hostname → Add**: subdomain + your domain (e.g.
      `ciel.example.com`), **Service** `HTTP` `ciel:8000`. WebSockets need no extra
      setting.
   4. Recommended: **Zero Trust → Access → Applications → Self-hosted** for the same
      hostname, policy "emails = yours". The browser then logs in with Cloudflare before
      the dashboard loads. The phone app talks to the API with `CIEL_API_TOKEN`; if you
      enable Access, add a bypass (or service token) policy for `/ws`, `/skills`,
      `/tts`, `/health` so the app is not sent to a login page.

## Run

```bash
# with the tunnel (production)
docker compose -f docker/docker-compose.yml --profile tunnel up -d --build

# without the tunnel (local machine, or before Cloudflare is set up)
docker compose -f docker/docker-compose.yml up -d --build

docker compose -f docker/docker-compose.yml --profile tunnel ps
docker compose -f docker/docker-compose.yml logs -f --tail=100 ciel
docker compose -f docker/docker-compose.yml logs -f --tail=50 cloudflared
curl -s http://127.0.0.1:8000/health
```

Healthy means: `ciel` is `healthy`, its log shows `Dashboard served from /app/web`,
`Loaded gmail_ops: 9 tool(s)` and `[Telegram] Bot online`, and the `cloudflared` log
shows `Registered tunnel connection`.

Then open `https://<your hostname>/`, go to **Settings**, paste `CIEL_API_TOKEN`, save.
The server address is already filled in (the page's own address). On the phone app,
enter `https://<your hostname>` and the same token.

## Update

```bash
git pull
docker compose -f docker/docker-compose.yml --profile tunnel up -d --build
```

| What changed | What the rebuild does |
|---|---|
| Python code only | reuses cached layers; mostly image export |
| `ciel_app/` | recompiles the dashboard stage (~1–2 min), Python layers cached |
| `requirements-docker.txt` or the `apt-get` line | reinstalls PyTorch and all packages: 5–10 min |
| only `.env` | no rebuild: `docker compose -f docker/docker-compose.yml up -d --force-recreate ciel` |
| a mounted file (Gmail token) | `docker compose -f docker/docker-compose.yml restart ciel` |

The dashboard is served with `Cache-Control: no-cache`, so a reload after a deploy
picks up the new version.

## Migrating from the old two-container layout

The old `ciel-telegram` / `ciel-api` containers must be stopped first, or the bot polls
twice and two processes write `ciel_data/`:

```bash
sudo docker rm -f ciel-telegram ciel-api 2>/dev/null
docker compose -f docker/docker-compose.yml --profile tunnel up -d --build
```

## Git inside the container

The image installs `git` but copies no `.git`. The compose file mounts the host
checkout read-only at `/repo` and sets `CIEL_REPO_PATH=/repo`, so Ciel's git tools show
the deployed code's status, diff and history; it cannot commit, pull or push. The mount
includes the host `.env` read-only; its values are already in the container
environment, and reading it still requires an approved shell command.

## Notes

- Port 8000 is bound to `127.0.0.1` only. Public access goes through the tunnel; do
  not open 8000 in the VPS firewall. Change the local port with `CIEL_PORT`.
- Container logs rotate at 3 × 10 MB.
- Do not commit the server address, SSH password, bot token, tunnel token, OAuth files,
  or `.env` values. `docker/tunnel.env` and `.env` are gitignored.
- Building the dashboard stage needs roughly 1.5 GB of free memory; on a small VPS,
  build while the server is otherwise quiet.
