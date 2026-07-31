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
            if response_text:
                return response_text
            # Last-resort fallback — Worker.generate() already retries once internally
            # on an empty response, so reaching this means that ALSO came back empty.
            # The old message here ("Master, core formatting disrupted.") was hardcoded
            # English regardless of the Master's language and gave no actionable next
            # step — same class of bug as the fact-vault formatter fixed earlier.
            if self.core._detect_language(user_input) == "Vietnamese":
                return "Xin lỗi Master, mình chưa tạo được phản hồi lần này — thử hỏi lại giúp mình nhé."
            return "Sorry Master, I couldn't generate a response this time — please try asking again."
            
        except Exception as e:
            error_msg = f"[Ciel Fatal] Pipeline crashed: {e}"
            print(Fore.RED + error_msg + Style.RESET_ALL)
            return error_msg
