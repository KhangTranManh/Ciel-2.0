"""
CIEL 2.0 — VISION & UI INTERACTION ENGINE ("The Hands of Ciel")
================================================================
Gives Ciel eyes (screenshot + Gemini Vision) and hands (pyautogui).
Uses a grid overlay system for precise coordinate mapping.
"""

import os
import io
import re
import sys
import json
import time
import base64
import traceback
from pathlib import Path
from datetime import datetime
from langchain_core.tools import StructuredTool

# ==========================================
# DEPENDENCIES
# ==========================================
try:
    from PIL import Image, ImageDraw, ImageFont, ImageGrab
    PILLOW_AVAILABLE = True
except ImportError:
    PILLOW_AVAILABLE = False

try:
    import pyautogui
    pyautogui.FAILSAFE = True   # Move mouse to corner to abort
    pyautogui.PAUSE = 0.3       # Small pause between actions
    PYAUTOGUI_AVAILABLE = True
except ImportError:
    PYAUTOGUI_AVAILABLE = False

try:
    from google import genai
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False

# ==========================================
# CONSTANTS
# ==========================================
BASE_DIR = Path(__file__).resolve().parent.parent.parent
SCREENSHOT_DIR = BASE_DIR / "ciel_workspace" / "screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

GRID_COLS = 10   # A-J
GRID_ROWS = 8    # 1-8
MAX_VISION_STEPS = 10
VISION_STEP_DELAY = 0.5  # seconds between steps

# Column labels
COL_LABELS = [chr(ord('A') + i) for i in range(GRID_COLS)]

# ==========================================
# SYSTEM PROMPT FOR VISION
# ==========================================
VISION_OPS_PROMPT = """
[CIEL VISION & UI INTERACTION SYSTEM]
You possess Vision tools to SEE the Master's screen and ACT on it autonomously.

TOOLS:
1. `vision_act`: Execute a full vision loop — screenshot, analyze, click/type, verify. Up to 10 steps per call. Use this when the Master asks you to DO something on screen (open app, click button, fill form, etc.).
2. `vision_describe`: Take a screenshot and describe what is currently visible on screen. Use this when the Master asks "what do you see?" or "what's on my screen?".

RULES:
1. ALWAYS use `vision_act` when the Master wants you to interact with the desktop (click, type, open, close, navigate).
2. Use `vision_describe` when the Master just wants to know what's on screen without any action.
3. You can chain vision_act with other tools (e.g., search first, then vision_act to open a URL).
4. NEVER attempt to interact with system-critical dialogs (UAC prompts, BIOS, disk format) — refuse politely.
"""

# ==========================================
# VISION ANALYSIS PROMPT (sent to Gemini Vision)
# ==========================================
VISION_ANALYSIS_PROMPT = """You are an expert UI analyst for a Windows desktop automation system.

You are looking at a screenshot of a Windows desktop with a LABELED GRID OVERLAY.
- Columns are labeled A through J (left to right).
- Rows are labeled 1 through 8 (top to bottom).
- Each cell is identified like "B3", "F7", etc.

Your task: {task}

IMPORTANT INSTRUCTIONS:
1. Identify the UI element you need to interact with.
2. Find which grid cell contains that element's CENTER.
3. If you need to be more precise, specify a position within the cell: "B3-center", "B3-top-left", "B3-bottom-right".

Respond with EXACTLY ONE JSON object (no markdown, no extra text):
{{
  "observation": "What I see on screen relevant to the task",
  "action": "click" | "double_click" | "right_click" | "type" | "hotkey" | "scroll" | "wait" | "done",
  "grid": "B3" | "B3-center",
  "text": "text to type (only for 'type' action)",
  "keys": ["enter"] (only for 'hotkey' action, e.g. pressing enter to search),
  "scroll_amount": -3 (only for 'scroll' action, negative=down),
  "reason": "Why I chose this action",
  "progress": "What has been accomplished so far",
  "done": false
}}

CRITICAL RULES:
1. NEVER repeat the same action more than twice. If you clicked or typed the same thing and it didn't work, TRY A COMPLETELY DIFFERENT approach.
2. When you "type" into a text field, the system will auto-select existing text first. You do NOT need to manually clear fields.
3. After typing a search query, YOU MUST immediately follow with a "hotkey" action using ["enter"] to submit.
4. CHECK THE SCREEN CAREFULLY each step. If the desired end-state is ALREADY visible (e.g. a video is playing, search results are shown, the page you wanted is open), set "action": "done" and "done": true IMMEDIATELY.
5. If you see an error or unexpected state, describe it in "observation" and attempt recovery.
6. You MUST output ONLY a single raw JSON object. No markdown, no ``` fences, no text outside the JSON.

NAVIGATION TIPS (for browser tasks):
- To focus the browser address bar: use hotkey ["ctrl", "l"], then type the URL, then press ["enter"].
- To switch browser tabs: use hotkey ["ctrl", "tab"] (next tab) or click the tab directly.
- To open a new tab: use hotkey ["ctrl", "t"].
- NEVER try to type a URL by clicking inside the address bar with a grid reference — use ["ctrl", "l"] instead.
"""

VISION_DESCRIBE_PROMPT = """You are an expert UI analyst. Describe what you see on this Windows desktop screenshot in detail.
Focus on:
1. What applications/windows are open
2. What content is visible (text, images, etc.)
3. The state of the desktop (icons, taskbar, notifications)

Be concise but thorough. Use bullet points."""


# ==========================================
# HELPER FUNCTIONS
# ==========================================
def _make_result(success: bool, data=None, code: str = None, message: str = None, tool_name: str = "") -> dict:
    return {
        "success": success,
        "data": data,
        "error": None if success else {
            "code": code or "VISION_ERROR",
            "message": message or "Vision operation failed."
        },
        "meta": {"tool_name": tool_name}
    }


def _capture_screen() -> Image.Image:
    """Capture the full screen as a PIL Image."""
    return ImageGrab.grab()


def _draw_grid_overlay(img: Image.Image) -> Image.Image:
    """Draw a labeled grid overlay on the screenshot for spatial reference."""
    overlay = img.copy()
    draw = ImageDraw.Draw(overlay)
    
    width, height = overlay.size
    cell_w = width / GRID_COLS
    cell_h = height / GRID_ROWS
    
    # Try to load a decent font, fallback to default
    try:
        font = ImageFont.truetype("arial.ttf", 18)
        small_font = ImageFont.truetype("arial.ttf", 14)
    except (IOError, OSError):
        font = ImageFont.load_default()
        small_font = font
    
    # Draw grid lines
    for col in range(GRID_COLS + 1):
        x = int(col * cell_w)
        draw.line([(x, 0), (x, height)], fill=(0, 255, 0, 128), width=1)
    
    for row in range(GRID_ROWS + 1):
        y = int(row * cell_h)
        draw.line([(0, y), (width, y)], fill=(0, 255, 0, 128), width=1)
    
    # Draw labels at each cell
    for row in range(GRID_ROWS):
        for col in range(GRID_COLS):
            label = f"{COL_LABELS[col]}{row + 1}"
            x = int(col * cell_w + 4)
            y = int(row * cell_h + 2)
            
            # Background rectangle for readability
            bbox = draw.textbbox((x, y), label, font=small_font)
            draw.rectangle([bbox[0]-2, bbox[1]-1, bbox[2]+2, bbox[3]+1], fill=(0, 0, 0, 200))
            draw.text((x, y), label, fill=(0, 255, 0), font=small_font)
    
    return overlay


def _grid_to_pixel(grid_ref: str, screen_width: int, screen_height: int) -> tuple:
    """Convert a grid reference like 'B3' or 'B3-top-left' to pixel (x, y)."""
    # Parse the grid reference
    grid_ref = grid_ref.strip().upper()
    
    # Check for sub-position modifier
    position = "center"
    if "-" in grid_ref:
        parts = grid_ref.split("-", 1)
        grid_ref = parts[0]
        position = parts[1].lower()
    
    # Extract column letter and row number
    match = re.match(r"^([A-J])(\d+)$", grid_ref)
    if not match:
        raise ValueError(f"Invalid grid reference: {grid_ref}")
    
    col_letter = match.group(1)
    row_num = int(match.group(2))
    
    col = ord(col_letter) - ord('A')
    row = row_num - 1
    
    if col < 0 or col >= GRID_COLS or row < 0 or row >= GRID_ROWS:
        raise ValueError(f"Grid reference {grid_ref} out of bounds")
    
    cell_w = screen_width / GRID_COLS
    cell_h = screen_height / GRID_ROWS
    
    # Calculate position within cell
    if position == "center":
        x = int(col * cell_w + cell_w / 2)
        y = int(row * cell_h + cell_h / 2)
    elif "top" in position and "left" in position:
        x = int(col * cell_w + cell_w * 0.25)
        y = int(row * cell_h + cell_h * 0.25)
    elif "top" in position and "right" in position:
        x = int(col * cell_w + cell_w * 0.75)
        y = int(row * cell_h + cell_h * 0.25)
    elif "bottom" in position and "left" in position:
        x = int(col * cell_w + cell_w * 0.25)
        y = int(row * cell_h + cell_h * 0.75)
    elif "bottom" in position and "right" in position:
        x = int(col * cell_w + cell_w * 0.75)
        y = int(row * cell_h + cell_h * 0.75)
    elif "top" in position:
        x = int(col * cell_w + cell_w / 2)
        y = int(row * cell_h + cell_h * 0.25)
    elif "bottom" in position:
        x = int(col * cell_w + cell_w / 2)
        y = int(row * cell_h + cell_h * 0.75)
    elif "left" in position:
        x = int(col * cell_w + cell_w * 0.25)
        y = int(row * cell_h + cell_h / 2)
    elif "right" in position:
        x = int(col * cell_w + cell_w * 0.75)
        y = int(row * cell_h + cell_h / 2)
    else:
        x = int(col * cell_w + cell_w / 2)
        y = int(row * cell_h + cell_h / 2)
    
    return (x, y)


def _image_to_base64(img: Image.Image) -> str:
    """Convert PIL Image to base64 string for Gemini API."""
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def _call_gemini_vision(img: Image.Image, prompt: str) -> str:
    """Send an image + text prompt to Gemini Vision and return the response."""
    from dotenv import load_dotenv
    env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    load_dotenv(env_path)
    
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not found in .env")
    
    client = genai.Client(api_key=api_key)
    
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[prompt, img]
    )
    return response.text.strip()


def _execute_action(action_data: dict, screen_width: int, screen_height: int) -> str:
    """Execute a single UI action based on Vision AI instructions."""
    action = action_data.get("action", "").lower()
    grid = action_data.get("grid", "")
    text = action_data.get("text", "")
    keys = action_data.get("keys", [])
    scroll_amount = action_data.get("scroll_amount", -3)
    
    if action == "done":
        return "Task completed."
    
    if action == "wait":
        time.sleep(1.5)
        return "Waited 1.5 seconds."
    
    if action in ("click", "double_click", "right_click"):
        if not grid:
            return "Error: No grid reference provided for click action."
        
        x, y = _grid_to_pixel(grid, screen_width, screen_height)
        
        if action == "click":
            pyautogui.click(x, y)
            return f"Clicked at {grid} → pixel ({x}, {y})"
        elif action == "double_click":
            pyautogui.doubleClick(x, y)
            return f"Double-clicked at {grid} → pixel ({x}, {y})"
        elif action == "right_click":
            pyautogui.rightClick(x, y)
            return f"Right-clicked at {grid} → pixel ({x}, {y})"
    
    elif action == "type":
        if not text:
            return "Error: No text provided for type action."
        # If a grid reference is given, click there first and select all existing text
        if grid:
            x, y = _grid_to_pixel(grid, screen_width, screen_height)
            pyautogui.click(x, y)
            time.sleep(0.2)
            pyautogui.hotkey('ctrl', 'a')  # Select all existing text to replace it
            time.sleep(0.1)
        if text.isascii():
            pyautogui.typewrite(text, interval=0.03)
        else:
            # For non-ASCII (Vietnamese, etc.), use clipboard paste
            import pyperclip
            pyperclip.copy(text)
            pyautogui.hotkey('ctrl', 'v')
        return f"Typed: '{text}'"
    
    elif action == "hotkey":
        if not keys:
            return "Error: No keys provided for hotkey action."
        pyautogui.hotkey(*keys)
        return f"Pressed hotkey: {'+'.join(keys)}"
    
    elif action == "scroll":
        if grid:
            x, y = _grid_to_pixel(grid, screen_width, screen_height)
            pyautogui.moveTo(x, y)
        pyautogui.scroll(scroll_amount)
        return f"Scrolled {'down' if scroll_amount < 0 else 'up'} by {abs(scroll_amount)}"
    
    return f"Unknown action: {action}"


def _log_vision(step: int, action_data: dict, result: str):
    """Log vision actions to console for debugging."""
    action = action_data.get("action", "?")
    grid = action_data.get("grid", "?")
    reason = action_data.get("reason", "")
    print(f"  [VISION Step {step}] {action} @ {grid} → {result} | {reason}")


# ==========================================
# PRE-FLIGHT PLANNER (Tier 1: CLI shortcuts)
# ==========================================
PREFLIGHT_PROMPT = """You are a task planner for a Windows desktop automation system.
Given a user's UI task, determine if any part can be accomplished via CLI/URL shortcuts BEFORE using visual screen interaction.

Task: {task}

Analyze and respond with EXACTLY ONE JSON object:
{{
  "cli_actions": [
    {{
      "type": "open_url",
      "url": "https://...",
      "reason": "Why this shortcut helps"
    }}
  ],
  "remaining_vision_task": "What still needs to be done visually after CLI actions (or null if CLI handles everything)",
  "cli_only": false
}}

RULES:
- If the task involves opening a website/URL, use "open_url" to navigate there directly.
- For YouTube searches, use: https://www.youtube.com/results?search_query=QUERY (replace spaces with +)
- For Google searches, use: https://www.google.com/search?q=QUERY
- If the task can be done 100% via CLI (e.g. "open notepad"), set "cli_only": true and "remaining_vision_task": null.
- For app launches, use "open_url" with just the exe name (e.g. "notepad.exe").
- If the task requires clicking specific UI elements (buttons, links, etc.), those go in "remaining_vision_task".
- Output ONLY raw JSON. No markdown fences.
"""


def _call_gemini_text(prompt: str) -> str:
    """Send a text-only prompt to Gemini (cheap, no image)."""
    from dotenv import load_dotenv
    env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    load_dotenv(env_path)
    
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not found in .env")
    
    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[prompt]
    )
    return response.text.strip()


def _execute_cli_actions(cli_actions: list) -> list:
    """Execute pre-flight CLI shortcuts (open URLs, launch apps)."""
    import subprocess
    results = []
    for action in cli_actions:
        action_type = action.get("type", "")
        if action_type == "open_url":
            url = action.get("url", "")
            if url:
                try:
                    subprocess.Popen(["start", "", url], shell=True)
                    time.sleep(2)  # Wait for browser/app to open
                    results.append(f"Opened: {url}")
                except Exception as e:
                    results.append(f"Failed to open {url}: {e}")
    return results


def _preflight_plan(task: str) -> dict:
    """Tier 1: Ask Gemini (text-only) if the task can use CLI shortcuts."""
    try:
        raw = _call_gemini_text(PREFLIGHT_PROMPT.format(task=task))
        
        # Strip markdown fences
        cleaned = raw
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()
        
        return json.loads(cleaned)
    except Exception as e:
        print(f"  [VISION Preflight] Plan failed: {e}")
        # Fallback: no CLI shortcuts, do everything visually
        return {"cli_actions": [], "remaining_vision_task": task, "cli_only": False}


# ==========================================
# MAIN VISION TOOLS
# ==========================================
def _vision_act(task: str) -> dict:
    """Two-tier autonomous vision: CLI shortcuts first, then visual interaction."""
    if not PILLOW_AVAILABLE:
        return _make_result(False, code="MISSING_DEP", message="Pillow not installed.", tool_name="vision_act")
    if not PYAUTOGUI_AVAILABLE:
        return _make_result(False, code="MISSING_DEP", message="PyAutoGUI not installed.", tool_name="vision_act")
    if not GENAI_AVAILABLE:
        return _make_result(False, code="MISSING_DEP", message="google-generativeai not installed.", tool_name="vision_act")
    
    action_log = []
    
    try:
        # =============================================
        # TIER 1: Pre-flight CLI shortcuts (0 vision tokens)
        # =============================================
        print(f"  [VISION] Planning pre-flight for: {task[:80]}...")
        plan = _preflight_plan(task)
        
        cli_actions = plan.get("cli_actions", [])
        remaining_task = plan.get("remaining_vision_task", task)
        cli_only = plan.get("cli_only", False)
        
        if cli_actions:
            cli_results = _execute_cli_actions(cli_actions)
            for i, r in enumerate(cli_results):
                action_log.append({"step": f"preflight-{i+1}", "action": "cli_shortcut", "result": r})
                print(f"  [VISION Preflight] {r}")
        
        # If CLI handles everything, return immediately (zero vision cost)
        if cli_only or not remaining_task:
            return _make_result(
                True,
                data={
                    "message": f"✋ [VISION] Task completed via CLI shortcut (0 vision steps).\n" + "\n".join(
                        [e["result"] for e in action_log]
                    ),
                    "steps": action_log,
                    "total_steps": 0
                },
                tool_name="vision_act"
            )
        
        # Update task for the vision loop (only the remaining interaction)
        task = remaining_task
        print(f"  [VISION] Remaining vision task: {task[:80]}...")
        
        # =============================================
        # TIER 2: Vision loop (only for UI interaction)
        # =============================================
        for step in range(1, MAX_VISION_STEPS + 1):
            # 1. Capture screen
            screen = _capture_screen()
            screen_w, screen_h = screen.size
            
            # 2. Draw grid overlay
            gridded = _draw_grid_overlay(screen)
            
            # 3. Save gridded screenshot for audit
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            debug_path = SCREENSHOT_DIR / f"vision_step_{step}_{ts}.png"
            gridded.save(debug_path)
            
            # 4. Build context from previous actions
            history = ""
            if action_log:
                history = "\n\nPrevious actions in this session:\n"
                for entry in action_log:
                    history += f"- Step {entry['step']}: {entry['action']} @ {entry.get('grid', 'N/A')} → {entry['result']}\n"
            
            # 5. Ask Gemini Vision what to do
            prompt = VISION_ANALYSIS_PROMPT.format(task=task) + history
            raw_response = _call_gemini_vision(gridded, prompt)
            
            # 6. Parse the JSON response
            # Strip markdown fences if present
            cleaned = raw_response
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[-1]
                if cleaned.endswith("```"):
                    cleaned = cleaned[:-3]
                cleaned = cleaned.strip()
            
            try:
                action_data = json.loads(cleaned)
            except json.JSONDecodeError:
                action_log.append({"step": step, "action": "parse_error", "result": cleaned[:200]})
                continue
            
            # 6b. Detect repetitive actions (same action+grid 3 times = stuck)
            if len(action_log) >= 2:
                recent = action_log[-2:]
                current_sig = f"{action_data.get('action')}@{action_data.get('grid')}"
                if all(f"{e.get('action')}@{e.get('grid')}" == current_sig for e in recent):
                    action_log.append({"step": step, "action": "loop_break", "result": f"Detected repetitive action: {current_sig}. Breaking loop."})
                    return _make_result(
                        True,
                        data={
                            "message": f"✋ [VISION] Stopped after {step} steps — detected repetitive loop ({current_sig}).",
                            "steps": action_log,
                            "total_steps": step
                        },
                        tool_name="vision_act"
                    )
            
            # 7. Check if done
            if action_data.get("done", False) or action_data.get("action") == "done":
                progress = action_data.get("progress", "Task completed.")
                action_log.append({"step": step, "action": "done", "result": progress})
                _log_vision(step, action_data, "DONE")
                
                return _make_result(
                    True,
                    data={
                        "message": f"✋ [VISION] Task completed in {step} step(s).\n{progress}",
                        "steps": action_log,
                        "total_steps": step
                    },
                    tool_name="vision_act"
                )
            
            # 8. Execute the action
            try:
                result = _execute_action(action_data, screen_w, screen_h)
            except Exception as e:
                result = f"Execution error: {e}"
            
            action_log.append({
                "step": step,
                "action": action_data.get("action", "?"),
                "grid": action_data.get("grid", ""),
                "result": result,
                "reason": action_data.get("reason", ""),
                "observation": action_data.get("observation", "")
            })
            
            _log_vision(step, action_data, result)
            
            # 9. Brief pause before next iteration
            time.sleep(VISION_STEP_DELAY)
        
        # Max steps reached
        return _make_result(
            True,
            data={
                "message": f"✋ [VISION] Reached maximum {MAX_VISION_STEPS} steps. Partial progress below.",
                "steps": action_log,
                "total_steps": MAX_VISION_STEPS
            },
            tool_name="vision_act"
        )
        
    except Exception as e:
        traceback.print_exc()
        return _make_result(
            False,
            code="VISION_LOOP_ERROR",
            message=f"Vision loop crashed: {e}",
            tool_name="vision_act"
        )


def _vision_describe() -> dict:
    """Take a screenshot and describe what's on screen using Gemini Vision."""
    if not PILLOW_AVAILABLE:
        return _make_result(False, code="MISSING_DEP", message="Pillow not installed.", tool_name="vision_describe")
    if not GENAI_AVAILABLE:
        return _make_result(False, code="MISSING_DEP", message="google-generativeai not installed.", tool_name="vision_describe")
    
    try:
        screen = _capture_screen()
        
        # Save screenshot
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = SCREENSHOT_DIR / f"vision_describe_{ts}.png"
        screen.save(path)
        
        # Ask Gemini to describe
        description = _call_gemini_vision(screen, VISION_DESCRIBE_PROMPT)
        
        return _make_result(
            True,
            data={"message": f"👁️ [VISION] Screen Analysis:\n{description}"},
            tool_name="vision_describe"
        )
        
    except Exception as e:
        traceback.print_exc()
        return _make_result(False, code="VISION_DESCRIBE_ERROR", message=str(e), tool_name="vision_describe")


# ==========================================
# TOOL PACK EXPORT
# ==========================================
def get_vision_tools() -> dict:
    """Returns vision tools and their system prompt for ToolManager registration."""
    try:
        tools = []
        
        # 1. Vision Act — full autonomous UI interaction
        def vision_act(task: str) -> dict:
            """Execute a full autonomous vision loop to complete a UI task on the Master's screen."""
            return _vision_act(task)
        
        tools.append(StructuredTool.from_function(
            func=vision_act,
            name="vision_act",
            description=(
                "Autonomously interact with the Master's desktop screen. Takes a screenshot, analyzes it with AI vision, "
                "and performs click/type/scroll actions to complete the given task. "
                "USE THIS when the Master asks you to open an app, click a button, fill a form, navigate a UI, or do anything visual on screen. "
                "Example: 'Open Chrome and go to google.com', 'Click the Start menu', 'Type hello in the search box'."
            )
        ))
        
        # 2. Vision Describe — see what's on screen
        def vision_describe() -> dict:
            """Take a screenshot and describe what is currently visible on the Master's screen."""
            return _vision_describe()
        
        tools.append(StructuredTool.from_function(
            func=vision_describe,
            name="vision_describe",
            description=(
                "Take a screenshot and describe what is currently on the Master's screen. "
                "USE THIS when the Master asks 'what do you see?', 'what's on my screen?', 'describe my desktop', or similar."
            )
        ))
        
        return {"tools": tools, "prompt": VISION_OPS_PROMPT}
    
    except Exception as e:
        print(f"[Ciel System Error] Failed to arm Vision toolkit: {e}")
        traceback.print_exc()
        return {"tools": [], "prompt": ""}
