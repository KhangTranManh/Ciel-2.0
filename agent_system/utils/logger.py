"""Color-coded terminal logger for the Brain-Worker pipeline."""
import sys
import os

os.environ["PYTHONIOENCODING"] = "utf-8"
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from colorama import Fore, Style, init

init(autoreset=True)


class Logger:
    """Unified logger with color-coded output per actor."""

    @staticmethod
    def brain(msg: str):
        """Yellow — Brain routing/planning."""
        print(Fore.YELLOW + f"[BRAIN]  {msg}" + Style.RESET_ALL)

    @staticmethod
    def worker(msg: str):
        """Cyan — Worker generating content."""
        print(Fore.CYAN + f"[WORKER] {msg}" + Style.RESET_ALL)

    @staticmethod
    def tool(msg: str):
        """Green — Tool execution."""
        print(Fore.GREEN + f"[TOOL]   {msg}" + Style.RESET_ALL)

    @staticmethod
    def middleware(msg: str):
        """Blue — Middleware verification/finalization of outbound content."""
        print(Fore.BLUE + f"[MIDDLEWARE] {msg}" + Style.RESET_ALL)

    @staticmethod
    def error(msg: str):
        """Red — Errors."""
        print(Fore.RED + f"[ERROR]  {msg}" + Style.RESET_ALL)

    @staticmethod
    def system(msg: str):
        """White/dim — System info."""
        print(Fore.WHITE + f"[SYSTEM] {msg}" + Style.RESET_ALL)

    @staticmethod
    def result(msg: str):
        """Magenta — Final output."""
        print(Fore.MAGENTA + f"[RESULT] {msg}" + Style.RESET_ALL)

    @staticmethod
    def divider():
        print(Fore.WHITE + Style.DIM + "-" * 60 + Style.RESET_ALL)


log = Logger()
