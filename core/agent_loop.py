import re
import os
from datetime import datetime
from pathlib import Path
from .llm_connector import CielCore

class AgentLoop:
    def __init__(self):
        self.core = CielCore()
        base_dir = Path(__file__).resolve().parent.parent
        self.log_path = base_dir / "ciel_data" / "logs" / "thoughts.log"
        self.debug_mode = os.getenv("DEBUG", "false").lower() == "true"
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def _log_interaction(self, user_input: str, thought: str, response: str, action: str = None):
        if not self.debug_mode: return
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}]\n[MASTER] : {user_input.strip()}\n")
            f.write(f"[THOUGHT]: {thought.strip()}\n")
            if action:
                f.write(f"[ACTION] : {action.strip()}\n")
            f.write(f"[RESPONSE]: {response.strip()}\n{'-'*50}\n")

    def _execute_action(self, action_text: str):
        action_text = action_text.strip()
        try:
            if action_text.startswith("SAVE_FACT:"):
                parts = action_text.replace("SAVE_FACT:", "").split("||")
                if len(parts) == 2:
                    self.core.memory_manager.save_fact(parts[0].strip(), parts[1].strip())
            elif action_text.startswith("DELETE_FACT:"):
                key = action_text.replace("DELETE_FACT:", "").strip()
                self.core.memory_manager.delete_fact(key)
        except Exception:
            pass 

    def run_step(self, user_input: str) -> str:
        raw = self.core.chat(user_input)
        raw = re.sub(r'```[a-zA-Z]*\n(.*?)```', r'\1', raw, flags=re.S)

        thought_match = re.search(r'<THOUGHT>(.*?)</THOUGHT>', raw, re.S | re.I)
        action_match = re.search(r'<ACTION>(.*?)</ACTION>', raw, re.S | re.I)
        response_match = re.search(r'<RESPONSE>(.*?)(?:</RESPONSE>|$)', raw, re.S | re.I)
        
        thought_text = thought_match.group(1) if thought_match else "FORMAT_ERROR"
        action_text = action_match.group(1) if action_match else None
        response_text = response_match.group(1).strip() if response_match else re.sub(r'<.*?>', '', raw, flags=re.S).strip()

        # Execute internal memory operations if an action is generated
        if action_text:
            self._execute_action(action_text)

        self._log_interaction(user_input, thought_text, response_text, action_text)
        
        return response_text if response_text else "Master, my core formatting was disrupted."