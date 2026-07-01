"""Run the bot (AgentLoop) *again* for another complicated multi-tool + Gmail task.
Two-phase:
1. Gather using Gmail search + productivity + other tools (multi-tool likely).
2. Send a clean, informed summary email to kxctran@gmail.com using chat context.

Uses auto-confirm for safety.

Run: .\.venv\Scripts\python.exe backtest/run_bot_again_test.py
"""

import sys
import os
from pathlib import Path
import json

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
    print(Fore.YELLOW + f"\n[AUTO-CONFIRM] {tool_name}" + Style.RESET_ALL)
    return True

def main():
    print(Fore.CYAN + "=== Running Ciel Bot AGAIN: Complicated Multi-Tool + Gmail Test ===" + Style.RESET_ALL)
    print("Phase 1: Multi-tool gather (Gmail search + productivity + info tools)")
    print("Phase 2: Send informed summary email to kxctran@gmail.com\n")

    ciel = AgentLoop()
    ciel.core.confirm_callback = auto_confirm

    # Phase 1: Complicated gather request (expect multi_tool or chain + SC if needed)
    phase1 = (
        "Complicated task with multiple tools: Search Gmail for recent messages about 'Ciel' or 'Multi-Tool Test' or 'productivity' "
        "(use search_gmail with good query, max_results=5). Then list current todos, add a new todo for 'Review latest bot multi-tool Gmail test results'. "
        "Get current time, weather for Hanoi, calculate 99*4 + 7, and grep workspace for 'Ciel' or 'multi'. "
        "Summarize the key findings from Gmail and tools."
    )
    print(Fore.GREEN + "Master (phase 1): " + phase1 + Style.RESET_ALL)
    print(Fore.CYAN + "[Ciel] Processing phase 1..." + Style.RESET_ALL)

    resp1 = ciel.run_step(phase1)
    print(Fore.BLUE + "\n--- Phase 1 Response ---" + Style.RESET_ALL)
    print(resp1)
    print(Fore.BLUE + "--- End Phase 1 ---\n" + Style.RESET_ALL)

    # Phase 2: Now send a proper email using the context from above (chat history helps)
    phase2 = (
        "Now send a clean, concise summary email to kxctran@gmail.com. "
        "Subject exactly: 'Ciel Bot Run Again - Multi-Tool + Gmail Results 2026-07-01'. "
        "Body should include: the Gmail search results you just got (senders, subjects, brief content), the new todo added, "
        "the time/weather/calc/grep outcomes, and confirm that Brain (alic/qwen3.7-max on Vilao) + Worker handled the multi-tool routing successfully. "
        "Use the send_gmail_message tool."
    )
    print(Fore.GREEN + "Master (phase 2): " + phase2 + Style.RESET_ALL)
    print(Fore.CYAN + "[Ciel] Processing phase 2 (send)..." + Style.RESET_ALL)

    resp2 = ciel.run_step(phase2)
    print(Fore.BLUE + "\n--- Phase 2 Response ---" + Style.RESET_ALL)
    print(resp2)
    print(Fore.BLUE + "--- End Phase 2 ---\n" + Style.RESET_ALL)

    print(Fore.CYAN + "=== Bot run again complete. Emails sent to kxctran@gmail.com as requested. ===" + Style.RESET_ALL)

    # Show latest todos
    try:
        todos = json.loads((Path("ciel_workspace") / "todos.json").read_text(encoding="utf-8"))
        print(Fore.GREEN + f"Latest todos (showing last 3 of {len(todos)}):" + Style.RESET_ALL)
        for t in todos[-3:]:
            print(f"  #{t['id']}: {t['task']}")
    except Exception as e:
        print("Todos read error:", e)

if __name__ == "__main__":
    main()
