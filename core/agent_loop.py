import re
import os
from datetime import datetime
from .llm_connector import CielCore

class AgentLoop:
    def __init__(self):
        self.core = CielCore()
        self.log_path = "./ciel_data/logs/thoughts.log"
        self.debug_mode = os.getenv("DEBUG", "false").lower() == "true"
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)

    def _log_thought(self, text):
        # Only log if Master allows or for debugging purposes
        if not self.debug_mode:
            return 
            
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] THOUGHT: {text.strip()}\n{'-'*40}\n")

    def run_step(self, user_input: str) -> str:
        raw = self.core.chat(user_input)
        print("[DEBUG] Raw LLM Output:", raw)  # Debugging raw output
        
        # Robust regex for thought and response extraction [cite: 8, 10]
        thought = re.search(r'<THOUGHT>(.*?)</THOUGHT>', raw, re.S | re.I)
        response = re.search(r'<RESPONSE>(.*?)</RESPONSE>', raw, re.S | re.I)
        
        if thought: 
            self._log_thought(thought.group(1))
        
        if response:
            return response.group(1).strip()
            
        # Fallback logic for format errors 
        clean_output = re.sub(r'<.*?>', '', raw).strip()
        self._log_thought(f"FORMAT_ERROR: {raw[:100]}...") 
        return clean_output