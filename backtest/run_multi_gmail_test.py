"""Run Ciel bot simulation for complicated multi-tool Gmail + productivity task.
Uses AgentLoop + auto-confirm for send_gmail_message etc.
Sends real email to kxctran@gmail.com if the task requires sending.
Run with .venv:
  .\.venv\Scripts\python.exe backtest/run_multi_gmail_test.py
"""

import sys
import os
from pathlib import Path

# Fix Windows encoding
os.environ["PYTHONIOENCODING"] = "utf-8"
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from colorama import Fore, Style, init as colorama_init
colorama_init()

from core.agent_loop import AgentLoop

def auto_confirm(tool_name: str, preview: str, tool_args: dict) -> bool:
    print(Fore.YELLOW + f"\n[AUTO-CONFIRM] Approving high-risk tool: {tool_name}" + Style.RESET_ALL)
    print(Fore.WHITE + f"Preview: {preview[:200]}..." if len(preview) > 200 else preview + Style.RESET_ALL)
    return True

def main():
    print(Fore.CYAN + "=== Ciel Multi-Tool Gmail Test (Brain+Worker, Vilao Brain, DeepSeek Worker) ===" + Style.RESET_ALL)
    print("Setting up AgentLoop with auto-approve for safety gate (send_gmail_message, trash etc.)...\n")

    ciel = AgentLoop()
    ciel.core.confirm_callback = auto_confirm

    # Complex multi-tool query designed to trigger Brain router -> multi_tool or chained tools
    # Involves: search_gmail, add_todo/list_todos, get_current_time, get_weather, calculate, grep_in_workspace, send_gmail_message
    query = (
        "Please do a complicated task using multiple tools: "
        "1. Search my Gmail (search_gmail) for recent emails related to productivity, todo, test, or important updates. Use query like 'productivity OR todo OR test' or 'is:unread category:primary', max_results=8. "
        "2. Add a new todo about 'Follow up on Gmail multi-tool test results and summarize to Master'. "
        "3. List current todos. "
        "4. Tell me the current time. "
        "5. Get weather for Ho Chi Minh City. "
        "6. Calculate 123 * 7 + 42. "
        "7. Grep the workspace for the word 'productivity' or 'diversity'. "
        "Finally, after gathering info, send a concise summary email to kxctran@gmail.com with subject 'Ciel Multi-Tool Test 2026-07-01' that lists: the key emails found (senders/subjects), the todo I added, the calc result, weather snippet, and any grep hits. Use send_gmail_message. Confirm recipient exactly kxctran@gmail.com."
    )

    print(Fore.GREEN + "\nMaster: " + query + Style.RESET_ALL)
    print(Fore.CYAN + "\n[Ciel]: Processing multi-tool request (may use Brain router for multi_tool plan, Gmail + productivity tools, possibly self-correction)..." + Style.RESET_ALL)

    try:
        response = ciel.run_step(query)
        print(Fore.BLUE + "\n=== FINAL CIEL RESPONSE ===" + Style.RESET_ALL)
        print(Fore.WHITE + response + Style.RESET_ALL)
    except Exception as e:
        print(Fore.RED + f"Fatal error during run: {e}" + Style.RESET_ALL)
        import traceback
        traceback.print_exc()

    print(Fore.CYAN + "\n=== Test run complete. Check ciel_data/logs/thoughts.log for full Brain/Worker steps and tool calls. ===" + Style.RESET_ALL)

    # Also show last few todos to verify
    try:
        import json
        todos_path = Path("ciel_workspace") / "todos.json"
        if todos_path.exists():
            todos = json.loads(todos_path.read_text(encoding="utf-8"))
            print(Fore.GREEN + f"\nCurrent todos.json ({len(todos)} items):" + Style.RESET_ALL)
            for t in todos[-5:]:
                print(f"  #{t.get('id')}: {t.get('task')} [done={t.get('done')}]")
    except Exception as ex:
        print("Could not read todos:", ex)

if __name__ == "__main__":
    main()
