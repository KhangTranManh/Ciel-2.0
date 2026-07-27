import os
import sys
import json
import asyncio
import threading
import traceback
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

import langchain
from langchain_core.globals import set_verbose, set_debug
from core.agent_loop import AgentLoop
from core.scheduler import CielScheduler

langchain.debug = False
langchain.verbose = False
set_debug(False)
set_verbose(False)

app = FastAPI(title="Ciel API", description="Ciel 2.0 WebSocket Backend")

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
    return {"status": "ok", "ready": ciel_agent is not None}


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


# SAFETY GATE: shared state for WebSocket confirmation
_confirm_event = threading.Event()
_confirm_result = {"approved": False}
_confirm_ws = None       # Active WebSocket for sending confirm requests
_confirm_lock = None     # asyncio.Lock for WebSocket sends

@app.on_event("startup")
async def startup_event():
    global ciel_agent
    print("[API] Initializing Ciel Core...")
    try:
        scheduler = CielScheduler()
        ciel_agent = AgentLoop()
        scheduler.cleanse_callback = ciel_agent.core._brain_cleanse

        # SAFETY GATE: WebSocket confirmation callback
        def _ws_confirm(tool_name: str, preview: str, tool_args: dict) -> bool:
            """Send confirmation request via WebSocket and block until response."""
            global _confirm_event, _confirm_result, _confirm_ws, _confirm_lock
            if _confirm_ws is None or _confirm_lock is None:
                print("[API Safety] No active WebSocket — auto-approving.")
                return True

            _confirm_event.clear()
            _confirm_result["approved"] = False

            # Schedule the async send from the sync thread
            async def _send():
                async with _confirm_lock:
                    try:
                        await _confirm_ws.send_json({
                            "type": "confirm_request",
                            "data": {
                                "tool_name": tool_name,
                                "preview": preview,
                                "tool_args": tool_args
                            }
                        })
                    except Exception as e:
                        print(f"[API Safety] Failed to send confirm request: {e}")

            try:
                loop = asyncio.get_event_loop()
                asyncio.run_coroutine_threadsafe(_send(), loop).result(timeout=5)
            except Exception as e:
                print(f"[API Safety] Error scheduling confirm send: {e}")
                return True  # Auto-approve on send failure

            # Block this thread until Flutter replies (max 60s)
            approved = _confirm_event.wait(timeout=60)
            if not approved:
                print("[API Safety] Confirmation timed out — auto-cancelling.")
                return False
            return _confirm_result["approved"]

        ciel_agent.core.confirm_callback = _ws_confirm

        scheduler.start_background()
        print("[API] Ciel Core initialized and ready.")
    except Exception as e:
        print(f"[API Fatal] Failed to initialize Ciel: {e}")
        traceback.print_exc()

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

async def broadcast_vitals(websocket: WebSocket, send_lock: asyncio.Lock):
    """Periodically checks system vitals and active tools and sends to frontend."""
    import subprocess
    import time
    
    try:
        while True:
            await asyncio.sleep(2)
            
            # 1. Real VRAM via nvidia-smi
            vram_used = 0.0
            vram_total = 12.0
            try:
                output = subprocess.check_output(
                    ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,nounits,noheader"],
                    text=True, creationflags=subprocess.CREATE_NO_WINDOW
                )
                used, total = map(float, output.strip().split(','))
                vram_used = round(used / 1024, 1)
                vram_total = round(total / 1024, 1)
            except Exception:
                vram_used = 3.2 # Fallback mock
                
            # 2. Which loaded skills fired recently — DYNAMIC from the skills manifest,
            #    so any new skill lights up automatically without editing this file.
            skills_activity = []
            try:
                recent = ""
                if thoughts_log_path.exists():
                    with open(thoughts_log_path, "r", encoding="utf-8") as f:
                        recent = "".join(f.readlines()[-40:]).lower()
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


async def run_agent_in_background(user_input: str, websocket: WebSocket, send_lock: asyncio.Lock):
    """Runs the synchronous ciel_agent.run_step in the thread pool executor and streams the response back."""
    global ciel_agent
    try:
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(None, ciel_agent.run_step, user_input)
        
        # Send the final response
        async with send_lock:
            try:
                await websocket.send_json({
                    "type": "response",
                    "data": response
                })
            except Exception:
                pass
    except Exception as e:
        error_msg = f"Ciel crashed during processing: {str(e)}"
        async with send_lock:
            try:
                await websocket.send_json({
                    "type": "error",
                    "data": error_msg
                })
            except Exception:
                pass


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    global _confirm_ws, _confirm_lock
    await websocket.accept()
    print("[API] New client connected via WebSocket.")
    
    send_lock = asyncio.Lock()
    _confirm_ws = websocket
    _confirm_lock = send_lock
    
    # Start the log tailer as a background task for this connection
    tail_task = asyncio.create_task(tail_thoughts_log(websocket, send_lock))
    vitals_task = asyncio.create_task(broadcast_vitals(websocket, send_lock))
    
    try:
        while True:
            # Receive message from Flutter Client
            data = await websocket.receive_text()
            print(f"[API] Received from client: {data}")
            
            try:
                # Parse if it's JSON
                msg_data = json.loads(data)

                # Handle confirmation responses from Flutter
                if msg_data.get("type") == "confirm_response":
                    _confirm_result["approved"] = msg_data.get("approved", False)
                    _confirm_event.set()
                    continue

                # TIER 5 — the UI's Stop button / Esc key sends this. Wired the same
                # way main.py binds Ctrl+C: cooperative, lands at the next step
                # boundary (never mid-tool), and the turn's own `run_agent_in_background`
                # still sends the normal `response`/`error` frame once it returns — no
                # separate "cancelled" frame needed, `ciel_agent.core.process()` already
                # returns a `[CANCELLED] ...` string like any other reply.
                if msg_data.get("type") == "cancel":
                    if ciel_agent is not None:
                        ciel_agent.core.request_cancel("cancelled from UI")
                    continue

                user_input = msg_data.get("message", "")
            except json.JSONDecodeError:
                user_input = data
            
            if not user_input:
                continue

            # Acknowledge receipt
            async with send_lock:
                try:
                    await websocket.send_json({
                        "type": "status",
                        "data": "processing"
                    })
                except Exception:
                    pass
            
            # Start Ciel Agent in a background task so the WebSocket loop remains responsive to read confirm answers
            asyncio.create_task(run_agent_in_background(user_input, websocket, send_lock))
                
    except WebSocketDisconnect:
        print("[API] Client disconnected.")
    finally:
        if _confirm_ws == websocket:
            _confirm_ws = None
            _confirm_lock = None
        tail_task.cancel()
        vitals_task.cancel()

if __name__ == "__main__":
    print("[API] Starting Ciel WebSocket Server on ws://localhost:8000/ws")
    uvicorn.run("main_api:app", host="0.0.0.0", port=8000, reload=False)
