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
        # Add the user's initial command to history
        self.core.chat_history.add_user_message(user_input)
        
        # Initial core evaluation
        ai_msg = self.core.chat_with_tools(user_input)
        
        # 1. Handle Tool Calls (The Reflection Loop)
        # 1. Handle Tool Calls (The Reflection Loop)
        if hasattr(ai_msg, 'tool_calls') and ai_msg.tool_calls:
            for tool_call in ai_msg.tool_calls:
                tool_name = tool_call["name"].lower()
                tool_args = tool_call["args"]
                tool_used_log = f"{tool_name}({tool_args})"
                
                # Execute the weapon locally
                exec_result = self.core.tool_manager.execute_tool(tool_name, tool_args)
                exec_result_text = exec_result if isinstance(exec_result, str) else str(exec_result)
                
                # Only log the action to history, DO NOT dump raw JSON into history to prevent memory pollution
                self.core.chat_history.add_ai_message(f"Action: {tool_used_log}")
                
                # Log the background execution internally
                thought = f"I executed {tool_name} to gather intelligence for the Master."
                self._log_interaction(user_input, thought, "Tool executed silently.", tool_used_log)

                # FORCE THE CORE TO ANALYZE ONLY THE NEW DATA (DYNAMIC INJECTION)
                reflection_prompt = (
                    f"I just executed the tool '{tool_name}'. Here is the RAW OUTPUT:\n"
                    f"-----------------\n{exec_result_text}\n-----------------\n"
                    f"Analyze EXACTLY this new data and respond to my original command: '{user_input}'. "
                    f"Do NOT use old emails from chat history. Apply your strict formatting rules."
                )
                
                # Call the LLM again with the tightly packaged raw data
                ai_msg = self.core.chat_with_tools(reflection_prompt)
                
        # 2. Handle Final Chat Formatting (Both normal chat AND post-tool analysis)
        raw = getattr(ai_msg, 'content', str(ai_msg))
        
        # --- GEMINI FALLBACK NORMALIZATION SHIELD ---
        if isinstance(raw, list):
            raw = "".join([block.get("text", "") for block in raw if isinstance(block, dict)])
        elif not isinstance(raw, str):
            raw = str(raw)
        # --------------------------------------------

        self.core.chat_history.add_ai_message(raw)
        self.core._save_chat_memory()

        # Extract the structured thought and response
        thought_match = re.search(r'<THOUGHT>(.*?)</THOUGHT>', raw, re.S | re.I)
        response_match = re.search(r'<RESPONSE>(.*?)(?:</RESPONSE>|$)', raw, re.S | re.I)
        
        thought_text = thought_match.group(1).strip() if thought_match else "EVALUATING."
        response_text = response_match.group(1).strip() if response_match else re.sub(r'<.*?>', '', raw, flags=re.S).strip()

        self._log_interaction(user_input, thought_text, response_text)
        return response_text if response_text else "Master, core formatting disrupted."