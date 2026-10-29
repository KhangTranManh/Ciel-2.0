# Ciel 2.0 — Docker deployment

One container, `ciel`, runs everything that writes Ciel's state:

```text
                 Cloudflare (HTTPS, your hostname)
                         │  outbound-only tunnel
   ┌─────────────────────┼──────────────────────────────────────────┐
   │ VPS                 ▼                                          │
   │   ciel-tunnel ──► ciel:8000                                    │
   │                    ├─ /ws, /skills, /tts   API (token)         │
   │                    ├─ Telegram bot (long polling, in-process)  │
   │                    └─ scheduler (planner, reminders)           │
   │   127.0.0.1:8000 ─► ciel  (local checks only)                  │
   └────────────────────────────────────────────────────────────────┘
```

Remote clients connect to the API hostname.

## Files

| File | Purpose |
|---|---|
| `Dockerfile` | Python runtime image for the API and optional in-process Telegram bot. Build context is the repo root. |
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
   Every route except `/health` requires it.
2. **`ciel_data/credentials.json`** (Google OAuth client).
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
      hostname, policy "emails = yours". Remote clients use `CIEL_API_TOKEN`; give each
      client a compatible Access policy for `/ws`, `/skills`, `/tts`, and `/health`.

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

Healthy means: `ciel` is `healthy`, its log shows `Loaded gmail_ops: 9 tool(s)` and
`[Telegram] Bot online`, and the `cloudflared` log shows `Registered tunnel connection`.

Configure your remote client with `https://<your hostname>` and `CIEL_API_TOKEN`.

## Update

```bash
git pull
docker compose -f docker/docker-compose.yml --profile tunnel up -d --build
```

| What changed | What the rebuild does |
|---|---|
| Python code only | reuses cached layers; mostly image export |
| `requirements-docker.txt` or the `apt-get` line | reinstalls PyTorch and all packages: 5–10 min |
| only `.env` | no rebuild: `docker compose -f docker/docker-compose.yml up -d --force-recreate ciel` |
| a mounted file (Gmail token) | `docker compose -f docker/docker-compose.yml restart ciel` |


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
