import sys
from pathlib import Path
from dotenv import load_dotenv
from colorama import Fore, Style

base_dir = Path(__file__).resolve().parent.parent
sys.path.append(str(base_dir))

from skills.external.zalo_messenger import send_zalo_message

def run_zalo_test():
    load_dotenv()
    print(Fore.CYAN + "Ciel [System]: Initiating Zalo API Stealth Test..." + Style.RESET_ALL)
    
    target = input(Fore.YELLOW + "Master, please enter the target phone number: " + Style.RESET_ALL)
    payload = "Ciel System: Stealth connection established. Local core operating at 100%."
    
    print(Fore.BLUE + f"Ciel: Dispatching payload to {target}..." + Style.RESET_ALL)
    
    try:
        result = send_zalo_message.invoke({"target_phone": target, "message": payload})
        
        if "successfully" in result.lower():
            print(Fore.GREEN + f"Ciel [Success]: {result}" + Style.RESET_ALL)
        else:
            print(Fore.RED + f"Ciel [Failure]: {result}" + Style.RESET_ALL)
            
    except Exception as e:
        print(Fore.RED + f"Ciel [Fatal]: Execution crashed. {e}" + Style.RESET_ALL)

if __name__ == "__main__":
    run_zalo_test()