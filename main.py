import sys
import os
import langchain
from langchain_core.globals import set_verbose, set_debug
from colorama import Fore, Style
from core.agent_loop import AgentLoop
from core.scheduler import CielScheduler

langchain.debug = False
langchain.verbose = False
set_debug(False)
set_verbose(False)


def main():
    print(Fore.CYAN + "Ciel [System]: Core initialization..." + Style.RESET_ALL)
    try:
        scheduler = CielScheduler()
        
        ciel = AgentLoop()
        scheduler.cleanse_callback = ciel.core._brain_cleanse

        # SAFETY GATE: CLI confirmation handler for high-risk tools.
        # Controlled ONLY by DISABLE_SAFETY_GATE (default OFF = gate active).
        # SAFETY_OPEN is unrelated here — it tunes Brain content-filtering, not tool approval.
        disable_gate = os.getenv("DISABLE_SAFETY_GATE", "false").lower() in ("true", "1", "yes")
        if not disable_gate:
            def _cli_confirm(tool_name: str, preview: str, tool_args: dict) -> bool:
                """Blocking CLI confirmation prompt for destructive tools."""
                print(Fore.YELLOW + f"\n⚠️  SAFETY CHECK — {tool_name}" + Style.RESET_ALL)
                print(Fore.WHITE + preview + Style.RESET_ALL)
                while True:
                    answer = input(Fore.YELLOW + "Approve? (Y/N): " + Style.RESET_ALL).strip().lower()
                    if answer in ("y", "yes"):
                        return True
                    if answer in ("n", "no"):
                        return False
            ciel.core.confirm_callback = _cli_confirm
        else:
            ciel.core.confirm_callback = lambda n, p, a: True  # auto-approve everything
            print(Fore.YELLOW + "[System] Safety gate open (permissive mode for non-violent categories)" + Style.RESET_ALL)

        scheduler.start_background()
        print(Fore.BLUE + "Ciel: Online. Awaiting your command, Master." + Style.RESET_ALL)
    except Exception as e:
        print(Fore.RED + f"Ciel [Fatal]: {e}" + Style.RESET_ALL)
        sys.exit(1)

    while True:
        try:
            user_input = input(Fore.GREEN + "\nMaster: " + Style.RESET_ALL)
            if user_input.lower() in ['exit', 'quit']:
                print(Fore.BLUE + "Ciel: Entering sleep mode." + Style.RESET_ALL)
                break
            
            output = ciel.run_step(user_input)
            print(Fore.BLUE + f"Ciel: {output}" + Style.RESET_ALL)
        except KeyboardInterrupt:
            break

if __name__ == "__main__":
    main()