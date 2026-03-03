from core.agent_loop import AgentLoop
from colorama import Fore, Style
import sys

def main():
    print(Fore.CYAN + "Ciel [System]: Đang khởi động Agent Loop..." + Style.RESET_ALL)
    
    try:
        ciel_loop = AgentLoop()
        print(Fore.BLUE + "Ciel: Nền tảng lõi đã ổn định. Ngài cần tôi làm gì, Master?" + Style.RESET_ALL)
    except Exception as e:
        print(Fore.RED + f"Ciel [Fatal Error]: {e}" + Style.RESET_ALL)
        sys.exit(1)

    while True:
        try:
            user_input = input(Fore.GREEN + "\nMaster: " + Style.RESET_ALL)
            if user_input.lower() in ['exit', 'quit', 'tắt']:
                print(Fore.BLUE + "Ciel: Đóng băng lõi an toàn." + Style.RESET_ALL)
                break
            
            # Gửi qua Agent Loop thay vì LLM Connector
            output = ciel_loop.run_step(user_input)
            print(Fore.BLUE + f"Ciel: {output}" + Style.RESET_ALL)
            
        except KeyboardInterrupt:
            break

if __name__ == "__main__":
    main()