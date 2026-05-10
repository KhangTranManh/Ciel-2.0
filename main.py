import sys
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
        scheduler.start_background()
        
        ciel = AgentLoop()
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