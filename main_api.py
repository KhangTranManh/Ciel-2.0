import os
import sys
import json
import asyncio
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

@app.on_event("startup")
async def startup_event():
    global ciel_agent
    print("[API] Initializing Ciel Core...")
    try:
        scheduler = CielScheduler()
        ciel_agent = AgentLoop()
        scheduler.cleanse_callback = ciel_agent.core._brain_cleanse
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
                
            # 2. Determine active armories by reading the last few thoughts
            web_active = False
            git_active = False
            trade_active = False
            vision_active = False
            
            try:
                if thoughts_log_path.exists():
                    with open(thoughts_log_path, "r", encoding="utf-8") as f:
                        # Read last 30 lines
                        lines = f.readlines()[-30:]
                        text_block = "\\n".join(lines).lower()
                        
                        # Look for recent tool calls
                        if "stealth_search" in text_block or "smart_scrape" in text_block:
                            web_active = True
                        if "git_" in text_block:
                            git_active = True
                        if "get_market_price" in text_block or "crypto" in text_block:
                            trade_active = True
                        if "vision_act" in text_block or "vision_describe" in text_block:
                            vision_active = True
            except Exception:
                pass
                
            # 3. Token estimation (dummy for now, based on log size)
            token_cost = 0.0
            try:
                if thoughts_log_path.exists():
                    size_kb = thoughts_log_path.stat().st_size / 1024
                    token_cost = min(round((size_kb * 0.0001), 2), 1.0)
            except:
                pass

            vitals = {
                "vram_used": vram_used,
                "vram_total": vram_total,
                "token_cost": token_cost,
                "armories": {
                    "Brain (Router)": True,
                    "Worker (Synth)": True,
                    "Web Agent": web_active,
                    "Git Ops": git_active,
                    "Trading Desk": trade_active,
                    "Vision (Eyes)": vision_active
                }
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


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    print("[API] New client connected via WebSocket.")
    
    send_lock = asyncio.Lock()
    
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
            
            # Run Ciel Agent (wrap in thread since it's synchronous)
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
                
    except WebSocketDisconnect:
        print("[API] Client disconnected.")
    finally:
        tail_task.cancel()
        vitals_task.cancel()

if __name__ == "__main__":
    print("[API] Starting Ciel WebSocket Server on ws://localhost:8000/ws")
    uvicorn.run("main_api:app", host="0.0.0.0", port=8000, reload=False)
