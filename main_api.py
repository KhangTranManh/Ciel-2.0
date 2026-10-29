import os
import sys
import json
import asyncio
import secrets
import traceback
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import uvicorn

import langchain
from langchain_core.globals import set_verbose, set_debug
from core.agent_loop import AgentLoop
from core.scheduler import CielScheduler
from core.proactive_setup import setup_proactive
from core.redact import redact_secrets
from api.confirm import WebSocketConfirmer

langchain.debug = False
langchain.verbose = False
set_debug(False)
set_verbose(False)

app = FastAPI(title="Ciel API", description="Ciel 2.0 WebSocket Backend")

# Clients on other devices reach this API over the network, and it can run shell
# commands and send email — so when CIEL_API_TOKEN is set, every route except
# /health requires it. Unset keeps the old open behaviour for localhost-only use.
API_TOKEN = os.getenv("CIEL_API_TOKEN", "").strip()
# Raw thoughts.log lines carry full prompts and email bodies; no shipped client
# renders them, so they are only streamed when explicitly enabled.
STREAM_THOUGHTS = os.getenv("CIEL_API_STREAM_THOUGHTS", "false").strip().lower() in ("1", "true", "yes")
# Run the Telegram bot inside this process instead of a second container, so one
# process owns ciel_data/. Opt-in: a local `python main_api.py` sharing the VPS .env
# must not start a second poller on the same bot.
RUN_TELEGRAM = os.getenv("CIEL_API_TELEGRAM", "false").strip().lower() in ("1", "true", "yes")
WS_UNAUTHORIZED = 4401


def _bearer(value: str | None) -> str | None:
    if value and value.lower().startswith("bearer "):
        return value[7:].strip()
    return None


def token_ok(candidate: str | None) -> bool:
    if not API_TOKEN:
        return True
    return bool(candidate) and secrets.compare_digest(candidate, API_TOKEN)


# Registered before CORS so CORS stays outermost and a 401 still carries CORS
# headers — otherwise a browser client reports a CORS error instead of "unauthorized".
@app.middleware("http")
async def require_token(request: Request, call_next):
    if request.method == "OPTIONS" or request.url.path == "/health":
        return await call_next(request)
    if not token_ok(_bearer(request.headers.get("authorization"))):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global variables
ciel_agent = None
thoughts_log_path = Path(__file__).resolve().parent / "ciel_data" / "logs" / "thoughts.log"


@app.get("/health")
async def health():
    """Liveness/readiness probe for the UI to poll before opening the WebSocket."""
    return {"status": "ok", "ready": ciel_agent is not None, "auth_required": bool(API_TOKEN)}


@app.get("/skills")
async def get_skills():
    """Dynamic manifest of loaded skills. The UI renders its skill panels from THIS —
    so dropping a new skills/*.py file into the backend surfaces in the UI with no
    frontend or API edits. Never hardcode a skill list on either side."""
    if ciel_agent is None:
        return {"ready": False, "skills": [], "totals": {"modules": 0, "tools": 0}}
    try:
        manifest = ciel_agent.core.tool_manager.get_skills_manifest()
    except Exception as e:
        return {"ready": True, "skills": [], "totals": {"modules": 0, "tools": 0}, "error": str(e)}
    return {
        "ready": True,
        "skills": manifest,
        "totals": {
            "modules": len(manifest),
            "tools": sum(s.get("tool_count", 0) for s in manifest),
        },
    }

@app.post("/tts")
async def tts(payload: dict):
    """Text-to-speech for the browser UI (approach B): normalize with the SAME
    `to_speech()` used by the CLI, synthesize with edge-tts, and return MP3 bytes.
    The UI sends the raw reply text and just plays the audio — no client-side voice
    cleanup needed, and the neural voice matches the CLI exactly. POST (not GET) so
    long replies aren't capped by URL length. Voice/rate/etc. come from .env."""
    from fastapi.responses import Response
    text = (payload or {}).get("text", "")
    voice = (payload or {}).get("voice") or None
    if not text or not text.strip():
        return Response(status_code=204)
    try:
        from core.speech_output import synth_to_bytes
        loop = asyncio.get_event_loop()
        audio = await loop.run_in_executor(None, lambda: synth_to_bytes(text, voice))
    except Exception as e:
        print(f"[API TTS Error] {e}")
        return Response(status_code=502)
    if not audio:
        return Response(status_code=204)
    return Response(content=audio, media_type="audio/mpeg")


_main_loop = None        # server event loop; approvals are asked from agent worker threads
_telegram_bot = None


def _deny_without_client(tool_name: str, preview: str, tool_args: dict) -> bool:
    """Default callback for work no client started (e.g. a background trigger):
    nobody is here to answer, and silence never resolves to "yes"."""
    try:
        ciel_agent.core.deferred.add(tool_name, tool_args,
                                     reason="no client to ask", source="api (no client)")
        ciel_agent.core._log_thought("SAFETY", "deferred", f"{tool_name}: no client to ask")
    except Exception as e:
        print(f"[API Safety] Failed to record deferred action: {e}")
    return False


def _start_telegram(agent):
    from core.telegram_interface import TelegramInterface
    try:
        bot = TelegramInterface(agent)
    except RuntimeError as e:
        print(f"[API] Telegram not started: {e}")
        return None
    bot.start()
    return bot


@app.on_event("startup")
async def startup_event():
    global ciel_agent, _main_loop, _telegram_bot
    _main_loop = asyncio.get_running_loop()
    print("[API] Initializing Ciel Core...")
    if not API_TOKEN:
        print("[API Security] CIEL_API_TOKEN is not set — anyone who can reach this port "
              "can control Ciel. Set it before exposing the API beyond localhost.")
    try:
        scheduler = CielScheduler()
        ciel_agent = AgentLoop()
        scheduler.cleanse_callback = ciel_agent.core._brain_cleanse
        # Each turn passes its own front end's callback to run_step; this default
        # only covers work that no connected client started.
        ciel_agent.core.confirm_callback = _deny_without_client

        # Proactive notifications go to Telegram; the WebSocket channel is not wired yet.
        setup_proactive(ciel_agent, scheduler, include_cli=False)
        if RUN_TELEGRAM:
            _telegram_bot = _start_telegram(ciel_agent)
        scheduler.start_background()
        print("[API] Ciel Core initialized and ready.")
    except Exception as e:
        print(f"[API Fatal] Failed to initialize Ciel: {e}")
        traceback.print_exc()


@app.on_event("shutdown")
async def shutdown_event():
    if _telegram_bot is not None:
        _telegram_bot.stop()

async def tail_thoughts_log(websocket: WebSocket, send_lock: asyncio.Lock):
    """Watches the thoughts.log file and streams new lines to the websocket."""
    # Ensure file exists
    thoughts_log_path.parent.mkdir(parents=True, exist_ok=True)
    if not thoughts_log_path.exists():
        thoughts_log_path.write_text("", encoding="utf-8")
    
    # Start tailing from the END of the file (current size)
    last_pos = thoughts_log_path.stat().st_size
    
    try:
        while True:
            await asyncio.sleep(0.5)
            
            # Check if file got truncated (e.g. wiped)
            current_size = thoughts_log_path.stat().st_size
            if current_size < last_pos:
                last_pos = 0
                
            if current_size > last_pos:
                with open(thoughts_log_path, "r", encoding="utf-8") as f:
                    f.seek(last_pos)
                    new_lines = f.readlines()
                    last_pos = f.tell()
                
                for line in new_lines:
                    if line.strip():
                        # Send thought to frontend
                        async with send_lock:
                            try:
                                await websocket.send_json({
                                    "type": "thought",
                                    "data": line.strip()
                                })
                            except Exception:
                                pass
    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f"[API Error in tail_thoughts_log] {e}")

def _read_gpu_memory() -> tuple[float, float]:
    """(used_gb, total_gb) from nvidia-smi, or (0, 0) when there is no NVIDIA GPU."""
    import subprocess
    try:
        output = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,nounits,noheader"],
            text=True, timeout=3,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        used, total = map(float, output.strip().splitlines()[0].split(','))
        return round(used / 1024, 1), round(total / 1024, 1)
    except Exception:
        return 0.0, 0.0


def _thoughts_log_tail(max_bytes: int = 16384) -> str:
    """Last few KB of thoughts.log. The file grows to many MB; reading it whole every
    2s per client blocked the event loop and delayed every WebSocket frame."""
    try:
        with open(thoughts_log_path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - max_bytes))
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


async def broadcast_vitals(websocket: WebSocket, send_lock: asyncio.Lock):
    """Periodically checks system vitals and active tools and sends to frontend."""
    try:
        while True:
            await asyncio.sleep(2)

            # 1. GPU memory, off the event loop (nvidia-smi takes ~1s).
            vram_used, vram_total = await asyncio.to_thread(_read_gpu_memory)

            # 2. Which loaded skills fired recently — DYNAMIC from the skills manifest,
            #    so any new skill lights up automatically without editing this file.
            skills_activity = []
            try:
                recent = (await asyncio.to_thread(_thoughts_log_tail)).lower()
                manifest = ciel_agent.core.tool_manager.get_skills_manifest() if ciel_agent else []
                for skill in manifest:
                    tool_names = [t["name"].lower() for t in skill.get("tools", [])]
                    skills_activity.append({
                        "module": skill["module"],
                        "category": skill.get("category", ""),
                        "tool_count": skill.get("tool_count", 0),
                        "active": any(tn in recent for tn in tool_names),
                    })
            except Exception:
                pass

            # 3. Real per-tier LLM usage for this session (replaces the old fake
            #    "log-size * 0.0001" estimate). Call counts + exact token totals from the
            #    provider + estimated USD cost (core/cost.py) — all live from the
            #    _log_thought chokepoint counters.
            llm_calls = {"BRAIN": 0, "WORKER": 0, "MIDDLEWARE": 0}
            llm_tokens = {t: {"input": 0, "output": 0, "total": 0} for t in ("BRAIN", "WORKER", "MIDDLEWARE")}
            llm_cost = {"BRAIN": 0.0, "WORKER": 0.0, "MIDDLEWARE": 0.0}
            middleware_on = False
            try:
                if ciel_agent:
                    llm_calls = dict(ciel_agent.core.llm_call_counts)
                    llm_tokens = {k: dict(v) for k, v in ciel_agent.core.llm_token_counts.items()}
                    llm_cost = dict(ciel_agent.core.llm_cost_usd)
                    middleware_on = ciel_agent.core.middleware is not None
            except Exception:
                pass

            vitals = {
                "vram_used": vram_used,
                "vram_total": vram_total,
                "llm_calls": llm_calls,
                "llm_calls_total": sum(llm_calls.values()),
                "llm_tokens": llm_tokens,
                "llm_tokens_total": sum(v.get("total", 0) for v in llm_tokens.values()),
                "llm_cost_usd": llm_cost,
                "llm_cost_usd_total": round(sum(llm_cost.values()), 6),
                "tiers": {
                    "Brain (Router)": True,
                    "Worker (Synth)": True,
                    "Middleware": middleware_on,
                },
                "skills": skills_activity,
            }
            
            async with send_lock:
                try:
                    await websocket.send_json({
                        "type": "vitals",
                        "data": vitals
                    })
                except Exception:
                    pass
    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f"[API Vitals Error] {e}")


async def run_agent_in_background(user_input: str, websocket: WebSocket, send_lock: asyncio.Lock,
                                  confirmer: WebSocketConfirmer):
    """Run one turn on a worker thread; its approvals go back to this connection."""
    try:
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(None, ciel_agent.run_step, user_input, confirmer)
        frame = {"type": "response", "data": response}
    except Exception as e:
        frame = {"type": "error", "data": f"Ciel crashed during processing: {redact_secrets(e)}"}
    async with send_lock:
        try:
            await websocket.send_json(frame)
        except Exception:
            pass


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    # Browsers cannot set headers on a WebSocket, so the token may also arrive as ?token=.
    supplied = websocket.query_params.get("token") or _bearer(websocket.headers.get("authorization"))
    if not token_ok(supplied):
        await websocket.send_json({"type": "error", "data": "unauthorized"})
        await websocket.close(code=WS_UNAUTHORIZED)
        return
    print("[API] New client connected via WebSocket.")

    send_lock = asyncio.Lock()

    async def send_json(frame: dict):
        async with send_lock:
            await websocket.send_json(frame)

    confirmer = WebSocketConfirmer(_main_loop or asyncio.get_running_loop(), send_json)
    tail_task = asyncio.create_task(tail_thoughts_log(websocket, send_lock)) if STREAM_THOUGHTS else None
    vitals_task = asyncio.create_task(broadcast_vitals(websocket, send_lock))

    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg_data = json.loads(data)
            except json.JSONDecodeError:
                msg_data = {"message": data}
            if not isinstance(msg_data, dict):
                continue

            if msg_data.get("type") == "confirm_response":
                confirmer.answer(msg_data.get("approved"))
                continue

            # TIER 5 — Stop button / Esc. Cooperative: lands at the next step boundary,
            # and the turn still ends with its normal response frame ("[CANCELLED] ...").
            if msg_data.get("type") == "cancel":
                if ciel_agent is not None:
                    ciel_agent.core.request_cancel("cancelled from UI")
                continue

            user_input = str(msg_data.get("message") or "").strip()
            if not user_input:
                continue
            if ciel_agent is None:
                await send_json({"type": "error", "data": "Ciel is still starting — try again shortly."})
                continue

            await send_json({"type": "status", "data": "processing"})
            # A background task keeps this loop free to receive the approval answer.
            asyncio.create_task(run_agent_in_background(user_input, websocket, send_lock, confirmer))

    except WebSocketDisconnect:
        print("[API] Client disconnected.")
    finally:
        confirmer.close()
        if tail_task:
            tail_task.cancel()
        vitals_task.cancel()


if __name__ == "__main__":
    port = int(os.getenv("CIEL_PORT", "8000"))
    print(f"[API] Starting Ciel on http://localhost:{port} (WebSocket /ws)")
    uvicorn.run("main_api:app", host="0.0.0.0", port=port, reload=False)
