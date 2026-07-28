"""Entrypoint: run Ciel as a Telegram bot instead of the CLI (main.py) or the
WebSocket API (main_api.py). Same CielCore/AgentLoop underneath — this file
only wires it to core/telegram_interface.py and starts the background
scheduler, mirroring main.py's and main_api.py's own startup shape.

Run:
    ./myenv/Scripts/python.exe main_telegram.py
"""
import sys
import time
import langchain
from langchain_core.globals import set_verbose, set_debug
from colorama import Fore, Style
from core.agent_loop import AgentLoop
from core.scheduler import CielScheduler
from core.telegram_interface import TelegramInterface

langchain.debug = False
langchain.verbose = False
set_debug(False)
set_verbose(False)


def main():
    print(Fore.CYAN + "Ciel [System]: Core initialization (Telegram interface)..." + Style.RESET_ALL)
    scheduler = CielScheduler()
    ciel = AgentLoop()
    scheduler.cleanse_callback = ciel.core._brain_cleanse

    try:
        bot = TelegramInterface(ciel)
    except RuntimeError as e:
        print(Fore.RED + f"[Telegram Fatal] {e}" + Style.RESET_ALL)
        sys.exit(1)

    bot.start()
    scheduler.start_background()
    print(Fore.BLUE + "Ciel: Online via Telegram. Press Ctrl+C here to stop." + Style.RESET_ALL)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print(Fore.BLUE + "\nCiel: Shutting down Telegram interface." + Style.RESET_ALL)
        bot.stop()


if __name__ == "__main__":
    main()
