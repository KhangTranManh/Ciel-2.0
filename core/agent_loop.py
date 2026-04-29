import os
from pathlib import Path
from colorama import Fore, Style
from .llm_connector import CielCore

class AgentLoop:
    def __init__(self):
        self.core = CielCore()
        base_dir = Path(__file__).resolve().parent.parent
        self.log_path = base_dir / "ciel_data" / "logs" / "thoughts.log"
        self.debug_mode = os.getenv("DEBUG", "false").lower() == "true"
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def run_step(self, user_input: str) -> str:
        print(Fore.CYAN + f"\n[Ciel]: Processing..." + Style.RESET_ALL)
        
        try:
            # The robust CielCore handles routing, execution, self-healing, and formatting natively
            response_text = self.core.process(user_input)
            return response_text if response_text else "Master, core formatting disrupted."
            
        except Exception as e:
            error_msg = f"[Ciel Fatal] Pipeline crashed: {e}"
            print(Fore.RED + error_msg + Style.RESET_ALL)
            return error_msg
