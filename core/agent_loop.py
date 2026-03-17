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

    def _log_interaction(self, user_input: str, thought: str, response: str, tool_used: str = None):
        if not self.debug_mode: return
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}]\n[MASTER] : {user_input.strip()}\n")
            f.write(f"[THOUGHT]: {thought.strip()}\n")
            if tool_used: f.write(f"[TOOL]   : {tool_used.strip()}\n")
            f.write(f"[RESPONSE]: {response.strip()}\n{'-'*50}\n")

    def run_step(self, user_input: str) -> str:
        ai_msg = self.core.chat_with_tools(user_input)
        
        # 1. Handle Tool Calls
        if ai_msg.tool_calls:
            for tool_call in ai_msg.tool_calls:
                tool_name = tool_call["name"].lower()
                tool_args = tool_call["args"]
                tool_used_log = f"{tool_name}({tool_args})"
                
                # Delegate to ToolManager
                exec_result = self.core.tool_manager.execute_tool(tool_name, tool_args)
                
                self.core.chat_history.add_user_message(user_input)
                self.core.chat_history.add_ai_message(exec_result)
                self.core._save_chat_memory()
                
                thought = f"I am executing the {tool_name} tool locally to protect Master's data."
                response = f"Master, I have securely executed the operation: {tool_used_log} in the local vault."
                
                self._log_interaction(user_input, thought, response, tool_used_log)
                return response

        # 2. Handle Normal Chat
        raw = ai_msg.content
        
        # --- GEMINI FALLBACK NORMALIZATION SHIELD ---
        # Forces Gemini's messy list format into a pure string
        if isinstance(raw, list):
            raw = "".join([block["text"] for block in raw if isinstance(block, dict) and "text" in block])
        elif not isinstance(raw, str):
            raw = str(raw)
        # --------------------------------------------

        self.core.chat_history.add_user_message(user_input)
        self.core.chat_history.add_ai_message(raw)
        self.core._save_chat_memory()

        thought_match = re.search(r'<THOUGHT>(.*?)</THOUGHT>', raw, re.S | re.I)
        response_match = re.search(r'<RESPONSE>(.*?)(?:</RESPONSE>|$)', raw, re.S | re.I)
        
        thought_text = thought_match.group(1) if thought_match else "EVALUATING."
        response_text = response_match.group(1).strip() if response_match else re.sub(r'<.*?>', '', raw, flags=re.S).strip()

        self._log_interaction(user_input, thought_text, response_text)
        return response_text if response_text else "Master, core formatting disrupted."