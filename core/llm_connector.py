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
from langchain_core.messages import SystemMessage, HumanMessage

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
# SELF-CORRECTION PROMPT — Brain evaluates tool results
# ==========================================================
SELF_CORRECTION_PROMPT = """You are evaluating whether a tool's result adequately answers the user's original request.

Respond ONLY with valid JSON (no markdown, no prose):

If the result answers the user's request adequately:
{{"satisfied": true}}

If the result is empty, incomplete, or wrong AND you know a better approach:
{{"satisfied": false, "reasoning": "1-sentence explanation", "action": "tool", "tool_name": "alternative_tool", "tool_args": {{"key": "value"}}, "response_hint": "how to present"}}

If the result is insufficient and no tool can help, explain to user:
{{"satisfied": false, "reasoning": "1-sentence explanation", "action": "chat", "task": "instruction for Worker to explain the situation"}}

RULES:
- Return satisfied=true if the result reasonably answers the request, even partially.
- Only return satisfied=false if you have a CONCRETE better alternative.
- NEVER suggest the same tool with identical arguments.
- Keep reasoning to 1 sentence.
"""


RAG_LLM_COMPRESS_CHAR_THRESHOLD = 4000  # Roughly 1000 tokens.
RAG_LLM_COMPRESS_INPUT_LIMIT = 20000
RAG_LLM_COMPRESS_OUTPUT_LIMIT = 5000


# ==========================================================
# MAIN ORCHESTRATOR
# ==========================================================

# Risk descriptions for high-risk tools (shown in confirmation prompt)
_RISK_DESCRIPTIONS = {
    "delete_file":           "Permanently DELETE a file from your workspace",
    "execute_shell_command": "Run an OS shell command on your machine",
    "send_gmail_message":    "Send an email from your Gmail account",
    "trash_email":           "Move an email to Trash in your Gmail",
    "git_confirm_push":      "Commit and PUSH code to the remote repository",
    "vision_act":            "Autonomously control your screen (click, type, scroll)",
}

class CielCore:
    def __init__(self):
        self.base_dir = Path(__file__).resolve().parent.parent
        self.tool_manager = ToolManager()
        self._tools = self.tool_manager.get_tools()

        # Load Official Personality
        self.persona_file = self.base_dir / "persona" / "official_ciel_personality.txt"
        self.ciel_persona = "You are Ciel, an AI assistant."
        if self.persona_file.exists():
            self.ciel_persona = self.persona_file.read_text(encoding="utf-8")

        # Build tool name -> schema map
        self._tool_map = {t.name: t for t in self._tools}
        self._tool_list_str = self._build_tool_list()

        # Initialize Brain and Worker from agent_system
        self.brain = Brain()
        self.worker = Worker()

        # Modular Components
        self.router = Router(self.brain, self._log_thought, persona=self.ciel_persona)
        self.recovery = RecoveryManager(self.worker, self._log_thought)

        log.system("CielCore initialized with Modular Brain-Worker architecture")

        # SAFETY GATE: confirmation callback for high-risk tools
        # Set by main.py (CLI) or main_api.py (WebSocket) at startup.
        # Signature: confirm_callback(tool_name: str, preview: str, tool_args: dict) -> bool
        self.confirm_callback = None

        self.chat_history = ChatMessageHistory()
        self.max_history = 20
        self.chat_memory_file = self.base_dir / "ciel_data" / "memory_bank.json"

        self._load_chat_memory()
        self._check_bootup_cleanse()

    # Tools that require Master's explicit Y/N approval before execution
    _HIGH_RISK_TOOLS = set(_RISK_DESCRIPTIONS.keys())

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
        "get_gmail_thread",
        "stealth_search",
        "smart_scrape"
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

    def _check_bootup_cleanse(self):
        import datetime
        import os
        if self.chat_memory_file.exists() and self.chat_history.messages:
            mtime = os.path.getmtime(self.chat_memory_file)
            last_date = datetime.datetime.fromtimestamp(mtime).date()
            today = datetime.datetime.now().date()
            if last_date < today:
                self._brain_cleanse(reason="boot-up date mismatch")

    def _brain_cleanse(self, reason="nightly"):
        if not self.chat_history.messages:
            return

        import datetime
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        print(f"\n[{ts}] [System] Initiating Brain Cleanse ({reason})...")

        transcript = []
        for msg in self.chat_history.messages:
            transcript.append(f"{msg.type.capitalize()}: {msg.content}")
        transcript_text = "\n".join(transcript)

        prompt = (
            "You are Ciel. Write a very concise Daily Summary of the following conversation.\n"
            "Focus only on key facts, decisions, and outcomes. Make it 2-3 paragraphs max.\n"
            "Transcript:\n" + transcript_text[:50000]
        )
        try:
            summary = self.worker.generate(prompt)
            date_str = datetime.datetime.now().strftime("%Y-%m-%d")
            summary_entry = f"Daily Summary ({date_str}):\n{summary}"

            # 1. Archive everything
            self._archive_to_rag(self.chat_history.messages)
            rag_manager.embed_and_save(summary_entry)
            self._log_thought("RAG", "archived_daily_summary", summary_entry[:100])

            # 2. Clear memory and save
            self.chat_history.messages = []
            self._save_chat_memory()

            # 3. Notify
            print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] [System] Brain Cleanse complete.")
            try:
                from core.scheduler import _send_telegram
                _send_telegram(f"🧠 Brain Cleanse complete ({reason}). Memory archived successfully.")
            except Exception:
                pass
        except Exception as e:
            print(f"[Brain Cleanse Error] {e}")

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
            f"{self.ciel_persona}\n\n"
            f"Respond EXTREMELY concisely. Give the absolute shortest, clearest answer possible. "
            f"No filler, no pleasantries. Always address the user as 'Master'.{capabilities_context}\n\n"
            f"User's request: {task}"
        )
        self._log_thought("WORKER", "chat_task", persona_task)
        response = self.worker.generate(persona_task)
        self._log_thought("WORKER", "chat_response", response)
        return response

    def _request_confirmation(self, tool_name: str, tool_args: dict) -> bool:
        """Request Master's approval before executing a high-risk tool."""
        risk = _RISK_DESCRIPTIONS.get(tool_name, f"Execute {tool_name}")
        args_preview = json.dumps(tool_args, ensure_ascii=False, indent=2)
        preview = (
            f"Action: {risk}\n"
            f"Tool:   {tool_name}\n"
            f"Args:   {args_preview}"
        )
        self._log_thought("SAFETY", "confirm_requested",
                          f"{tool_name}({args_preview})")

        if self.confirm_callback is None:
            # No callback set (e.g. forgot to wire up) — auto-proceed with warning
            self._log_thought("SAFETY", "confirm_auto_approved",
                              "No confirm_callback set — auto-approving.")
            return True

        try:
            approved = self.confirm_callback(tool_name, preview, tool_args)
        except Exception as e:
            self._log_thought("SAFETY", "confirm_error", str(e))
            approved = False

        tag = "confirm_approved" if approved else "confirm_denied"
        self._log_thought("SAFETY", tag, tool_name)
        return approved

    def execute_tool(self, tool_name: str, tool_args: dict, response_hint: str = "", user_input: str = "") -> str:
        """Execute a Ciel tool and format the result."""
        log.tool(f"Executing: {tool_name}({tool_args})")

        if tool_name not in self._tool_map:
            log.error(f"Tool not found: {tool_name}")
            return f"[TOOL_ERROR] Tool '{tool_name}' not found."

        # SAFETY GATE: require confirmation for high-risk tools
        if tool_name in self._HIGH_RISK_TOOLS:
            if not self._request_confirmation(tool_name, tool_args):
                return f"[CANCELLED] Master denied execution of {tool_name}. No action was taken."

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

        if tool_name in {"get_fact", "save_fact", "delete_fact"}:
            return self._format_fact_result(tool_name, result_text)

        if tool_name not in self._TOOLS_NEEDING_FORMAT:
            return result_text

        # GMAIL: Smart truncation — keep all emails visible, trim each body
        if "gmail" in tool_name.lower():
            clean_text = self._compact_email_result(result_text)
        elif tool_name == "smart_scrape":
            # Let the Worker read up to 40,000 characters of the scraped website
            clean_text = result_text[:40000]
        else:
            clean_text = result_text[:2000]

        format_task = (
            f"{self.ciel_persona}\n\n"
            f"Format this tool output into the absolute shortest, clearest response possible.\n"
            f"User's request: {user_input}\n"
            f"Tool: {tool_name}\n"
            f"Raw result:\n{clean_text}\n\n"
            f"Hint: {response_hint}\n"
            f"RULES:\n"
            f"1. Be extremely concise. Give just the requested data. No conversational filler.\n"
            f"2. Always address the user as 'Master' at the beginning of your response.\n"
            f"3. ANTI-HALLUCINATION: ONLY use facts present in the Raw result above. "
            f"If the raw result contains an error, 'file not found', 'N/A', or is empty, "
            f"report the error honestly to Master. Say 'the data is unavailable' or 'the tool returned an error'. "
            f"NEVER invent, fabricate, or simulate data that is not in the raw result. "
            f"NEVER generate fake file contents, fake statistics, or fake execution output.\n"
            f"4. NO PROCESS NARRATION: Do NOT write status lines like 'Retrieving...', "
            f"'Scanning...', 'Initiating...', 'Fetching...'. Report ONLY the final data/facts."
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

    def _format_fact_result(self, tool_name: str, result_text: str) -> str:
        """Convert memory vault tool output into clean user-facing text."""
        if tool_name == "get_fact":
            match = re.match(r"Fact '([^']+)':\s*(.*)", result_text, flags=re.DOTALL)
            if match:
                key = match.group(1).replace("_", " ")
                value = match.group(2).strip()
                return f"Master, your {key} is {value}."
            match = re.match(r"No fact found for key '([^']+)'", result_text)
            if match:
                key = match.group(1).replace("_", " ")
                return f"Master, I do not have a saved {key}."
            return result_text

        if tool_name == "save_fact":
            match = re.match(r"Fact saved successfully:\s*([^.]+)\.", result_text)
            if match:
                key = match.group(1).replace("_", " ")
                return f"Master, I saved your {key}."
            return result_text

        if tool_name == "delete_fact":
            match = re.match(r"Fact deleted successfully:\s*([^.]+)\.", result_text)
            if match:
                key = match.group(1).replace("_", " ")
                return f"Master, I deleted your {key}."
            return result_text

        return result_text

    def _refine_recalled_context(self, recalled: str) -> str:
        """Second-stage RAG compression using the Worker only when needed."""
        if not recalled or len(recalled) <= RAG_LLM_COMPRESS_CHAR_THRESHOLD:
            return recalled

        prompt = (
            "You are a context compressor for Ciel's long-term memory.\n"
            "Read the recalled memory logs below and compress them into factual core interactions.\n"
            "Strip raw HTML, scraped webpage text, long code blocks, raw diffs, stack traces, and redundant chatter.\n"
            "Keep only facts that could help answer the current user request.\n"
            "Use this exact format, one interaction per line:\n"
            "[YYYY-MM-DD] Human: ... | Ai: ...\n\n"
            "Rules:\n"
            "- Do not invent facts.\n"
            "- Keep dates when present; use [unknown] only if no date exists.\n"
            "- Keep file names, decisions, preferences, and final outcomes.\n"
            "- Output only the compressed context lines.\n\n"
            f"RECALLED MEMORY LOGS:\n{recalled[:RAG_LLM_COMPRESS_INPUT_LIMIT]}"
        )

        try:
            self._log_thought("RAG", "compress_task", recalled[:1000])
            compressed = self.worker.generate(prompt).strip()
            if not compressed:
                return recalled
            compressed = compressed[:RAG_LLM_COMPRESS_OUTPUT_LIMIT].strip()
            self._log_thought("RAG", "compressed", compressed[:1000])
            return compressed
        except Exception as e:
            self._log_thought("RAG", "compress_error", str(e))
            return recalled

    def execute_code(self, task: str, filename: str) -> str:
        """Worker generates code and writes it to disk."""
        from agent_system.tools.buffer_writer import buffer_writer

        buffer_writer.clear()
        # Add anti-hallucination guardrail for code generation
        code_guardrail = (
            "\n\nCRITICAL RULES FOR CODE GENERATION:\n"
            "1. Output ONLY production-ready code. Do NOT include dummy/test data, "
            "demonstration values, or example scaffolding unless explicitly asked.\n"
            "2. If input files may not exist, add proper error handling — do NOT fabricate their contents.\n"
            "3. Do NOT simulate script execution output or invent fake results.\n"
            "4. Do NOT add a dummy 'if __name__ == \"__main__\"' block with fake test data or "
            "file creation for demonstration. The main block should only call the real function "
            "with the real parameters from the task.\n"
            "5. Keep code concise: use brief inline comments only where logic is non-obvious. "
            "Do NOT write multi-line docstrings for every function. Do NOT add verbose "
            "explanatory comments on every line."
        )
        augmented_task = task + code_guardrail
        self._log_thought("WORKER", "code_task", task)
        code = self.worker.generate(augmented_task)
        self._log_thought("WORKER", "code_response", code)
        buffer_writer.append(code)
        result = buffer_writer.flush(filename)
        log.tool(result)
        return f"Code written to {filename}"

    def execute_multi_tool(self, tools: list, response_hint: str, user_input: str = "") -> str:
        """Execute multiple tools sequentially and synthesize the result."""
        results = []
        for t in tools:
            name = t.get("tool_name", "")
            args = t.get("tool_args", {})
            log.tool(f"Executing step: {name}({args})")
            
            if name not in self._tool_map:
                res = f"[TOOL_ERROR] {name} not found."
            else:
                res = self.execute_tool(name, args, response_hint=response_hint, user_input=user_input)
                
            self._log_thought("TOOL", f"result_{name}", res)
            results.append(f"--- Output from {name} ---\n{res}")
            if res.startswith("[CANCELLED]"):
                break
            
        combined_results = "\n\n".join(results)
        
        format_task = (
            f"{self.ciel_persona}\n\n"
            f"Synthesize the following data from multiple tools into a cohesive report.\n"
            f"User's request: {user_input}\n"
            f"{combined_results}\n\n"
            f"Hint: {response_hint}\n"
            f"RULES:\n"
            f"1. Be concise. Deliver a unified report without conversational filler.\n"
            f"2. Always address the user as 'Master'.\n"
            f"3. ONLY use facts present in the tool outputs above. NEVER invent data.\n"
            f"4. If any tool returned an error, 'file not found', or empty result, "
            f"report that honestly. Do NOT fabricate fake data, fake file contents, or fake execution output."
        )
        self._log_thought("WORKER", "multi_tool_format_task", format_task)
        formatted = self.worker.generate(format_task)
        self._log_thought("WORKER", "multi_tool_format_response", formatted)
        return formatted

    def _self_correct(self, user_input: str, tool_name: str, tool_args: dict, result: str, max_attempts: int = 2) -> str:
        """Brain evaluates tool result and tries alternative approach if unsatisfactory."""
        for attempt in range(max_attempts):
            evaluation = self._evaluate_result(user_input, tool_name, tool_args, result)
            if evaluation.get("satisfied", True):
                if attempt > 0:
                    log.system(f"Self-Correction satisfied after {attempt} correction(s)")
                return result

            reasoning = evaluation.get("reasoning", "Result was insufficient")
            new_action = evaluation.get("action", "chat")
            self._log_thought("BRAIN", "self_correction",
                f"Attempt {attempt+1}/{max_attempts}: {reasoning}\n"
                f"Previous: {tool_name}({json.dumps(tool_args, ensure_ascii=False)})\n"
                f"Next: {new_action} → {evaluation.get('tool_name', evaluation.get('task', 'N/A'))}")
            log.brain(f"Self-Correction [{attempt+1}]: {reasoning[:80]}")

            if new_action == "tool":
                new_tool = evaluation.get("tool_name", "")
                new_args = evaluation.get("tool_args", {})
                new_hint = evaluation.get("response_hint", "")

                # Prevent infinite loop — don't retry same tool with same args
                if new_tool == tool_name and new_args == tool_args:
                    self._log_thought("BRAIN", "self_correction", "Aborted: same tool+args, would loop.")
                    return result

                new_result = self.execute_tool(new_tool, new_args, new_hint, user_input)
                result = new_result  # HIDE ERROR: Only return the new successful result to the user
                # Update for next evaluation iteration
                tool_name = new_tool
                tool_args = new_args

            elif new_action == "chat":
                task = evaluation.get("task", user_input)
                # Inject the actual tool result so Worker doesn't hallucinate
                enriched_task = (
                    f"{task}\n\n"
                    f"[ACTUAL DATA from previous tool '{tool_name}']:\n"
                    f"{result[:3000]}"
                )
                chat_response = self.execute_chat(enriched_task)
                return chat_response  # HIDE ERROR: Only return the new chat response to the user
            else:
                return result

        return result

    def _evaluate_result(self, user_input: str, tool_name: str, tool_args: dict, result: str) -> dict:
        """Ask Brain to evaluate if a tool result satisfies the user's request."""
        # Ensure result is never empty (Gemini rejects empty content)
        safe_result = (result or "No output returned.").strip()
        if not safe_result:
            safe_result = "No output returned."

        eval_request = (
            f"User's request: {user_input}\n"
            f"Tool used: {tool_name}({json.dumps(tool_args, ensure_ascii=False)})\n"
            f"Tool result:\n{safe_result[:3000]}\n\n"
            f"Available tools: {self._tool_list_str}\n\n"
            f"Does this result adequately answer the user's request? Respond with JSON only."
        )
        try:
            messages = [
                SystemMessage(content=SELF_CORRECTION_PROMPT),
                HumanMessage(content=eval_request)
            ]
            response = self.brain._router_llm.invoke(messages)
            # Gemini occasionally returns content as a list of parts instead of a plain string
            _eval_content = response.content
            if isinstance(_eval_content, list):
                _eval_content = "".join(c.text if hasattr(c, "text") else str(c) for c in _eval_content)
            raw = _eval_content.strip()
            self._log_thought("BRAIN", "evaluate_result", raw)

            # Strip markdown fences if present
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1]
                if raw.endswith("```"):
                    raw = raw[:-3]
                raw = raw.strip()

            if not raw:
                return None
            return json.loads(raw)
        except (json.JSONDecodeError, Exception) as e:
            self._log_thought("BRAIN", "evaluate_result_error", str(e))
            return {"satisfied": True}  # Fail-safe: assume satisfied if evaluation fails

    def process(self, user_input: str) -> str:
        """Full pipeline: recall → route → execute → respond."""
        self.chat_history.add_user_message(user_input)

        # RAG RECALL: Search long-term memory for relevant past context
        recalled = rag_manager.search_similar(user_input)
        if recalled:
            recalled = self._refine_recalled_context(recalled)
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
                response = self.execute_tool(tool_name, tool_args, hint, user_input)

                # SELF-CORRECTION: Brain evaluates if result is satisfactory
                response = self._self_correct(user_input, tool_name, tool_args, response)

            elif action == "code":
                task = decision.get("task", user_input)
                filename = decision.get("filename", "agent_output/output.py")
                response = self.execute_code(task, filename)

            elif action == "multi_tool":
                tools = decision.get("tools", [])
                hint = decision.get("response_hint", "")
                response = self.execute_multi_tool(tools, hint, user_input)

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


def build_worker_prompt(user_request: str, tool_results_string: str) -> str:
    """Format the input for the Local Worker model based on tool results."""
    return f"""[USER REQUEST]
{user_request}

[TOOL RESULTS]
{tool_results_string}

Task: Based ONLY on the [TOOL RESULTS] above, answer the [USER REQUEST]. Be extremely concise. Address user as 'Master'."""
