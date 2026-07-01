"""Minimal bot run to verify Gmail send works end-to-end via AgentLoop.
Sends to kxctran@gmail.com with correct args.
"""
import sys, os
from pathlib import Path
os.environ["PYTHONIOENCODING"] = "utf-8"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from colorama import Fore, Style, init
init()
from core.agent_loop import AgentLoop

def auto(n, p, a): 
    print(Fore.YELLOW + "[AUTO] approving " + n + Style.RESET_ALL)
    return True

print(Fore.CYAN + "=== Quick bot verification run: Gmail multi + send ===" + Style.RESET_ALL)
bot = AgentLoop()
bot.core.confirm_callback = auto

q = "Use search_gmail to find recent Ciel test emails (3 results), then send a short email to kxctran@gmail.com confirming the tool works. Subject: 'Quick Verify - Ciel send_gmail_message via bot'. Include that multi tool routing succeeded and the correct 'message' param is used internally."
print("Running query...")
out = bot.run_step(q)
print(Fore.GREEN + "\nFinal bot output:\n" + out + Style.RESET_ALL)
print(Fore.CYAN + "Done. Check for new email at kxctran@gmail.com" + Style.RESET_ALL)
