"""llm_connector.py — Bridge between Ciel's core and the Brain-Worker agent system.

Architecture:
  User Input → Router (Brain) → decides:
    - "chat" → Worker generates natural response
    - "tool" → Ciel's ToolManager executes, Worker formats (if needed)
    - "code" → Worker generates code, buffer writes to disk
    - "multi_tool" → Executes sequentially, Worker synthesizes
"""
import os
import re
import json
import traceback
from pathlib import Path
from dotenv import load_dotenv

from langchain_community.chat_message_histories import ChatMessageHistory

from .tool_manager import ToolManager
from .router import Router
from .recovery_manager import RecoveryManager
from . import rag_manager

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent_system.models.brain import Brain
from agent_system.models.worker import Worker
from agent_system.utils.logger import log

load_dotenv()


# ==========================================================
# MAIN ORCHESTRATOR
# ==========================================================
class CielCore:
    def __init__(self):
        self.tool_manager = ToolManager()
        self._tools = self.tool_manager.get_tools()

        # Build tool name -> schema map
        self._tool_map = {t.name: t for t in self._tools}
        self._tool_list_str = self._build_tool_list()

        # Initialize Brain and Worker from agent_system
        self.brain = Brain()
        self.worker = Worker()

        # Modular Components
        self.router = Router(self.brain, self._log_thought)
        self.recovery = RecoveryManager(self.worker, self._log_thought)

        log.system("CielCore initialized with Modular Brain-Worker architecture")

        self.chat_history = ChatMessageHistory()
        self.max_history = 20
        self.base_dir = Path(__file__).resolve().parent.parent
        self.chat_memory_file = self.base_dir / "ciel_data" / "memory_bank.json"

        self._load_chat_memory()

    # Hardcoded hints for tools whose auto-generated descriptions are incomplete
    _TOOL_HINTS = {
        "search_gmail": "Search emails. Args: query (required), resource='messages' (required, always use 'messages'), max_results (optional, default 5).",
    }

    # Only call the Worker to format these tools. Others are already readable.
    _TOOLS_NEEDING_FORMAT = {
        "search_gmail",
        "get_market_price",
        "analyze_crypto_technical",
        "get_gmail_message",
        "get_gmail_thread"
    }

    def _log_thought(self, actor: str, action: str, content: str):
        """Append a record of the Brain/Worker thought process to the thoughts.log file."""
        log_file = self.base_dir / "ciel_data" / "logs" / "thoughts.log"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        import datetime
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = f"[{timestamp}] [{actor}] [{action.upper()}]\n{content}\n{'-'*60}\n"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(entry)

    def _build_tool_list(self) -> str:
        """Build a compact tool list string for the Brain's routing prompt."""
        lines = []
        for tool in self._tools:
            name = tool.name
            if name in self._TOOL_HINTS:
                lines.append(f"- {name}: {self._TOOL_HINTS[name]}")
                continue

            desc = tool.description[:80] if tool.description else "No description"
            args_info = ""
            if hasattr(tool, 'args_schema') and tool.args_schema:
                try:
                    schema = tool.args_schema.schema()
                    props = schema.get("properties", {})
                    required = schema.get("required", [])
                    arg_parts = []
                    for arg_name, arg_schema in props.items():
                        arg_type = arg_schema.get("type", "string")
                        req = " (required)" if arg_name in required else ""
                        arg_parts.append(f"{arg_name}: {arg_type}{req}")
                    args_info = ", ".join(arg_parts)
                except Exception:
                    args_info = "(see tool description)"
            lines.append(f"- {name}({args_info}): {desc}")
        return "\n".join(lines)

    def _trim_history(self):
        """Trim chat history to max_history. Archived messages go to long-term RAG memory."""
        if len(self.chat_history.messages) > self.max_history:
            # Archive the messages that are about to be trimmed
            overflow = self.chat_history.messages[:-self.max_history]
            self._archive_to_rag(overflow)
            self.chat_history.messages = self.chat_history.messages[-self.max_history:]

    def _archive_to_rag(self, messages: list):
        """Send trimmed messages to long-term RAG memory as user+assistant pairs."""
        pairs = []
        current_pair = []
        for msg in messages:
            current_pair.append(f"{msg.type.capitalize()}: {msg.content[:300]}")
            if msg.type == "ai":
                pairs.append(" | ".join(current_pair))
                current_pair = []
        # Save any leftover (unpaired user message)
        if current_pair:
            pairs.append(" | ".join(current_pair))

        for pair_text in pairs:
            saved = rag_manager.embed_and_save(pair_text)
            if saved:
                self._log_thought("RAG", "archived", pair_text[:100])

    def _load_chat_memory(self):
        if self.chat_memory_file.exists():
            try:
                data = json.loads(self.chat_memory_file.read_text(encoding="utf-8"))
                toxic_markers = [
                    "Thư viện CIEL không cung cấp",
                    "I do not have the capability",
                    "As an AI",
                    "I have used the tool",
                    "I've used the tool",
                    "I have searched",
                    "echo I have used",
                    "echo Show me",
                    "Action: search_gmail({'count'",
                    "Action: execute_shell_command({'command': 'echo",
                    "label:new",
                ]
                for msg in data:
                    content = msg.get("content", "")
                    if any(marker in content for marker in toxic_markers):
                        continue
                    if msg.get("type") == "human":
                        self.chat_history.add_user_message(content)
                    else:
                        self.chat_history.add_ai_message(content)
                self._trim_history()
            except Exception as e:
                print(f"[Ciel Warning] Failed to load chat memory: {e}")
                traceback.print_exc()
                self.chat_history = ChatMessageHistory()

    def _save_chat_memory(self):
        self.chat_memory_file.parent.mkdir(parents=True, exist_ok=True)
        self._trim_history()
        data = [{"type": m.type, "content": m.content} for m in self.chat_history.messages]
        self.chat_memory_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def execute_chat(self, task: str) -> str:
        """Worker generates a natural language response."""
        capabilities_context = ""
        task_lower = task.lower()
        if any(kw in task_lower for kw in ["capabilit", "what can you do", "able to do", "list your features"]):
            capabilities_context = (
                f"\n\nCRITICAL: The user is asking what you can do. You MUST ONLY list capabilities "
                f"from this exact tool list. Do NOT invent other skills like translation or research "
                f"unless explicitly covered by these tools:\n{self._tool_list_str}"
            )

        persona_task = (
            f"You are Ciel, an AI assistant. "
            f"Respond EXTREMELY concisely. Give the absolute shortest, clearest answer possible. "
            f"No filler, no pleasantries.{capabilities_context}\n\n"
            f"User's request: {task}"
        )
        self._log_thought("WORKER", "chat_task", persona_task)
        response = self.worker.generate(persona_task)
        self._log_thought("WORKER", "chat_response", response)
        return response

    def execute_tool(self, tool_name: str, tool_args: dict, response_hint: str = "") -> str:
        """Execute a Ciel tool and format the result."""
        log.tool(f"Executing: {tool_name}({tool_args})")

        if tool_name not in self._tool_map:
            log.error(f"Tool not found: {tool_name}")
            return f"[TOOL_ERROR] Tool '{tool_name}' not found."

        result = self.tool_manager.execute_tool(tool_name, tool_args)
        result_text = self.tool_manager.format_tool_result(result)

        # SELF-HEALING HOOK (UP TO 3 ATTEMPTS)
        max_attempts = 3
        attempt = 1
        current_args = tool_args

        while attempt <= max_attempts and ("EXECUTION_ERROR" in result_text or "Error" in result_text[:30] or ("Lỗi chạy script" in result_text and tool_name == "run_python_script")):
            self._log_thought("HEALING", f"attempt_{attempt}", f"Starting heal attempt {attempt}/{max_attempts}")
            
            previous_code = ""
            if tool_name == "run_python_script" and "filename" in current_args:
                try:
                    from skills.internal.system_ops import _is_safe_path
                    safe_path = _is_safe_path(current_args["filename"])
                    if os.path.exists(safe_path):
                        with open(safe_path, "r", encoding="utf-8") as f:
                            previous_code = f.read()
                except Exception:
                    pass

            success, action, data = self.recovery.heal_tool_error(tool_name, current_args, result_text, attempt, previous_code)
            
            if not success:
                result_text += f"\n\n[Self-Healing Failed] {data.get('error', 'Unknown error')}"
                break
                
            if action == "code_fix":
                try:
                    from skills.internal.system_ops import _is_safe_path
                    safe_path = _is_safe_path(data["filename"])
                    
                    # Validate syntax before saving!
                    syntax_valid, syntax_error = self.recovery.check_syntax(data["code"])
                    if not syntax_valid:
                        self._log_thought("HEALING", "syntax_error", syntax_error)
                        result_text = f"[Syntax Error Validation Failed]\n{syntax_error}"
                        attempt += 1
                        continue
                    
                    with open(safe_path, "w", encoding="utf-8") as f:
                        f.write(data["code"])
                    self._log_thought("HEALING", "apply_fix", f"Code updated for {data['filename']}. Re-running script.")
                    
                    # Retry tool
                    result = self.tool_manager.execute_tool(tool_name, current_args)
                    retry_text = self.tool_manager.format_tool_result(result)
                    result_text = f"[Self-Healing Activated] Analyzed code error, fixed it, and re-ran.\n\nNew Output:\n{retry_text}"
                    
                except Exception as e:
                    result_text = f"[Self-Healing Error] {e}"
                    
            elif action == "retry_tool":
                self._log_thought("HEALING", "retry_tool_args", str(data))
                current_args = data  # Update arguments for the next attempt if it fails
                result = self.tool_manager.execute_tool(tool_name, current_args)
                retry_text = self.tool_manager.format_tool_result(result)
                result_text = f"[Self-Healing Activated] Analyzed parameter error, corrected args, and re-ran.\n\nNew Output:\n{retry_text}"
                
            attempt += 1
            
        if "EXECUTION_ERROR" in result_text or "Error" in result_text[:30] or ("Lỗi chạy script" in result_text and tool_name == "run_python_script"):
            log.error(f"Tool {tool_name} failed after {attempt-1} self-healing attempts.")
            self._log_thought("TOOL", "error", f"{tool_name}: {result_text}")
            # STRUCTURED ERROR: Rephrase raw error for the user
            try:
                friendly = self.worker.generate(
                    f"Rephrase this error for the user in one plain, helpful sentence. "
                    f"Do NOT include technical stack traces.\n\nError: {result_text[:500]}"
                )
                self._log_thought("WORKER", "error_rephrase", friendly)
                return f"Sorry, {friendly}"
            except Exception:
                return f"[TOOL_ERROR] {tool_name}: {result_text}"

        self._log_thought("TOOL", "result", f"{tool_name}: {result_text}")

        if tool_name not in self._TOOLS_NEEDING_FORMAT:
            return result_text

        # GMAIL: Smart truncation — keep all emails visible, trim each body
        if "gmail" in tool_name.lower():
            clean_text = self._compact_email_result(result_text)
        else:
            clean_text = result_text[:2000]

        format_task = (
            f"You are Ciel. Format this tool output into the absolute shortest, clearest response possible.\n"
            f"Tool: {tool_name}\n"
            f"Raw result:\n{clean_text}\n\n"
            f"Hint: {response_hint}\n"
            f"CRITICAL: Be extremely concise. Give just the requested data. No conversational filler."
        )
        self._log_thought("WORKER", "format_task", format_task)
        formatted = self.worker.generate(format_task)
        self._log_thought("WORKER", "format_response", formatted)
        return formatted

    @staticmethod
    def _strip_html(text: str) -> str:
        """Strip HTML tags, zero-width chars, and collapse whitespace."""
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'[\u200b\u200c\u200d\ufeff\xa0]', '', text)  # zero-width + nbsp
        text = re.sub(r'&nbsp;', ' ', text, flags=re.IGNORECASE)
        text = re.sub(r'&amp;', '&', text)
        text = re.sub(r'&lt;', '<', text)
        text = re.sub(r'&gt;', '>', text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text

    def _compact_email_result(self, text: str) -> str:
        """Parse email JSON, strip HTML bodies, truncate each to 150 chars."""
        try:
            emails = json.loads(text)
            if not isinstance(emails, list):
                return self._strip_html(text)[:2000]
            
            compact = []
            for em in emails:
                body_raw = em.get("body", "")
                body_clean = self._strip_html(body_raw)[:150]
                compact.append({
                    "sender": em.get("sender", ""),
                    "subject": em.get("subject", ""),
                    "body": body_clean,
                })
            return json.dumps(compact, ensure_ascii=False, indent=1)
        except (json.JSONDecodeError, TypeError):
            return self._strip_html(text)[:2000]

    def execute_code(self, task: str, filename: str) -> str:
        """Worker generates code and writes it to disk."""
        from agent_system.tools.buffer_writer import buffer_writer

        buffer_writer.clear()
        self._log_thought("WORKER", "code_task", task)
        code = self.worker.generate(task)
        self._log_thought("WORKER", "code_response", code)
        buffer_writer.append(code)
        result = buffer_writer.flush(filename)
        log.tool(result)
        return f"Code written to {filename}"

    def execute_multi_tool(self, tools: list, response_hint: str) -> str:
        """Execute multiple tools sequentially and synthesize the result."""
        results = []
        for t in tools:
            name = t.get("tool_name", "")
            args = t.get("tool_args", {})
            log.tool(f"Executing step: {name}({args})")
            
            if name not in self._tool_map:
                res = f"[TOOL_ERROR] {name} not found."
            else:
                raw_res = self.tool_manager.execute_tool(name, args)
                res = self.tool_manager.format_tool_result(raw_res)
                
            self._log_thought("TOOL", f"result_{name}", res)
            results.append(f"--- Output from {name} ---\n{res}")
            
        combined_results = "\n\n".join(results)
        
        format_task = (
            f"You are Ciel. Synthesize the following data from multiple tools into a cohesive report.\n"
            f"{combined_results}\n\n"
            f"Hint: {response_hint}\n"
            f"CRITICAL: Be concise. Deliver a unified report without conversational filler."
        )
        self._log_thought("WORKER", "multi_tool_format_task", format_task)
        formatted = self.worker.generate(format_task)
        self._log_thought("WORKER", "multi_tool_format_response", formatted)
        return formatted

    def process(self, user_input: str) -> str:
        """Full pipeline: recall → route → execute → respond."""
        self.chat_history.add_user_message(user_input)

        # RAG RECALL: Search long-term memory for relevant past context
        recalled = rag_manager.search_similar(user_input)
        if recalled:
            self._log_thought("RAG", "recalled", recalled[:300])

        try:
            # Inject recalled context into the user input for the Router
            enriched_input = user_input
            if recalled:
                enriched_input = (
                    f"[RECALLED PAST CONTEXT (from previous conversations)]:\n"
                    f"{recalled}\n\n"
                    f"[CURRENT USER REQUEST]:\n{user_input}"
                )
            decision = self.router.route(enriched_input, self._tool_list_str, self.chat_history)
            action = decision.get("action", "chat")

            if action == "tool":
                tool_name = decision.get("tool_name", "")
                tool_args = decision.get("tool_args", {})
                hint = decision.get("response_hint", "")
                response = self.execute_tool(tool_name, tool_args, hint)

            elif action == "code":
                task = decision.get("task", user_input)
                filename = decision.get("filename", "agent_output/output.py")
                response = self.execute_code(task, filename)

            elif action == "multi_tool":
                tools = decision.get("tools", [])
                hint = decision.get("response_hint", "")
                response = self.execute_multi_tool(tools, hint)

            else:
                task = decision.get("task", user_input)
                response = self.execute_chat(task)

            self.chat_history.add_ai_message(response)
            self._save_chat_memory()

            return response

        except Exception as e:
            log.error(f"Pipeline error: {e}")
            traceback.print_exc()
            return f"An error occurred: {str(e)[:200]}"

    # ==========================================================
    # LEGACY COMPATIBILITY
    # ==========================================================
    def chat_with_tools(self, user_input: str, use_coder: bool = False):
        response = self.process(user_input)
        class FakeAIMessage:
            def __init__(self, content):
                self.content = content
                self.tool_calls = []
        return FakeAIMessage(f"<RESPONSE>{response}</RESPONSE>")