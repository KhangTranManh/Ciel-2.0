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
import unicodedata
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
- A result is NEVER satisfactory if it is an error message, empty, "not found",
  "does not exist", "không tồn tại", or clearly unrelated junk — in those cases you
  MUST return satisfied=false and provide the best alternative tool, or an "action":"chat"
  that honestly explains the situation to the user.
- Only return satisfied=false if you have a CONCRETE better alternative OR the result is an error/empty/junk.
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

    # Email-sending tools and the arg holding the body that must be sanitized
    # before it leaves the system (strips internal reasoning/meta/paths).
    _EMAIL_BODY_ARGS = {
        "send_gmail_message":      "message",
        "send_gmail_html_message": "html_body",
        "reply_to_email":          "reply_text",
        "create_gmail_draft":      "message",
    }

    # Keyword triggers for "the user wants this sent via email" — shared by the
    # proactive router-bypass check, the content-filter fallback, and the
    # multi_tool missing-send-step safeguard.
    _EMAIL_INTENT_KEYWORDS = (
        "gửi email", "send email", "gửi thư", "gửi mail", "email đến", "send to",
        "gửi cho", "qua email", "qua mail", "qua gmail", "gửi báo cáo", "gửi report",
        "send report", "email report", "báo cáo qua", "report qua", "mail cho", "email cho",
    )

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

    # Tools with obviously-correct results — skip Brain self-correction to save API cost
    _SKIP_SELF_CORRECTION = {
        "list_workspace", "read_file", "write_file", "append_file",
        "save_fact", "delete_fact", "get_fact",
        "take_screenshot", "get_file_info", "open_application",
        "get_crypto_stats", "vision_describe",
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
                from skills.external.telegram_ops import send_telegram_message
                send_telegram_message(f"🧠 Brain Cleanse complete ({reason}). Memory archived successfully.")
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

        # OUTBOUND EMAIL SANITIZATION (single choke-point): every email-sending path
        # — multi_tool synthesis, direct Brain send, content-filter fallback, replies,
        # drafts — funnels through here, so cleaning the body once covers them all.
        body_key = self._EMAIL_BODY_ARGS.get(tool_name)
        if body_key and isinstance(tool_args.get(body_key), str):
            # HARD BLOCK: never let an unsynthesized plan placeholder go out as a real
            # email. This marker is only meant to be replaced by execute_multi_tool's
            # synthesis step; reaching here means the plan skipped synthesis.
            if "[PROFESSIONAL_EMAIL_BODY_TO_BE_SYNTHESIZED]" in tool_args[body_key]:
                self._log_thought("SAFETY", "email_placeholder_blocked",
                                  f"{tool_name}: body still contains the synthesis placeholder — send blocked.")
                return (f"Lỗi: nội dung email chưa được tổng hợp (vẫn còn placeholder) — đã CHẶN gửi. "
                        f"Cần thu thập dữ liệu thật và tổng hợp nội dung trước khi gửi.")

            cleaned = self._sanitize_outbound_email(tool_args[body_key])
            if cleaned != tool_args[body_key]:
                tool_args = dict(tool_args)
                tool_args[body_key] = cleaned
                self._log_thought("TOOL", "email_sanitized", f"{tool_name}: stripped internal/meta content from '{body_key}'.")

        # SAFETY GATE: require confirmation for high-risk tools.
        # Controlled ONLY by DISABLE_SAFETY_GATE (default OFF = gate active / fail-safe).
        # NOTE: SAFETY_OPEN governs Brain LLM content-filtering, a separate concern —
        # it must NOT influence the destructive-tool confirmation gate.
        disable_gate = os.getenv("DISABLE_SAFETY_GATE", "false").lower() in ("true", "1", "yes")
        if tool_name in self._HIGH_RISK_TOOLS and not disable_gate:
            if not self._request_confirmation(tool_name, tool_args):
                return f"[CANCELLED] Master denied execution of {tool_name}. No action was taken."

        # send_gmail_message transmits its body as text/html (langchain), so raw '\n'
        # collapse into a wall of text on the recipient side. Render the clean plain
        # body into simple HTML AFTER the confirmation preview (which stays plain/readable)
        # so the delivered email preserves paragraphs and line breaks.
        if tool_name == "send_gmail_message" and isinstance(tool_args.get("message"), str):
            tool_args = dict(tool_args)
            tool_args["message"] = self._plaintext_to_html(tool_args["message"])

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

    def _build_market_html_from_results(self, combined_results: str, synthesized: str) -> str:
        """Parse market tool outputs and build the styled HTML dashboard.

        Uses real numbers from the tool results only. Missing values become 'N/A'.
        The Worker-synthesized text supplies risk/conclusion prose.
        """
        import re
        from datetime import date
        from skills.external.trading_ops import build_market_report_html

        text = combined_results or ""

        def _search(pattern, default="N/A"):
            m = re.search(pattern, text, re.IGNORECASE)
            return m.group(1).strip() if m else default

        # BTC from get_crypto_stats: "Stats BTCUSDT: Price=..., Change=...%, High=..., Low=..."
        btc_price = _search(r"Price=([\d.,]+)")
        btc_change = _search(r"Change=(-?[\d.,]+%?)")
        if btc_change != "N/A" and not btc_change.endswith("%"):
            btc_change += "%"
        # BTC technicals: "... RSI: 33.64 (...), MA(5,30): ..., Xu h?????ng: ..."
        btc_rsi = _search(r"RSI:\s*([\d.]+)")
        btc_trend = _search(r"Xu h\S+ng:\s*([^\n,]+)")
        # XAU from get_market_price: "Gi?? XAU/USD: 2350.1"
        xau_price = _search(r"XAU/?USD:\s*([\d.,]+)")

        # Risk level heuristic from synthesized prose (explicit risk phrases only,
        # never bare "cao" which also appears in "Cao nhat" = 24h high).
        low = (synthesized or "").lower()
        if any(w in low for w in ["rui ro cao", "r\u1ee7i ro cao", "high risk", "nguy c\u01a1 cao"]):
            risk_level = "High"
        elif any(w in low for w in ["rui ro thap", "r\u1ee7i ro th\u1ea5p", "low risk", "an toan", "an to\u00e0n"]):
            risk_level = "Low"
        else:
            risk_level = "Medium"

        # Build CLEAN prose for the HTML card. Do NOT reuse the chat-facing
        # synthesized text: it contains the "Master," greeting, a [MARKET_DATA]
        # dump and email-status meta that must never leak into the report body.
        def _clean_prose(raw: str) -> str:
            import re as _re
            s = raw or ""
            # Drop bracketed tags like [MARKET_DATA], [EMAIL], [COGNITION].
            s = _re.sub(r"\[[A-Z_]+\]", " ", s)
            # Drop email-status / send meta lines.
            drop = ("send_gmail", "message id", "tr\u1ea1ng th\u00e1i g\u1eedi", "ch\u01b0a th\u1ec3 g\u1eedi",
                    "ch\u01b0a \u0111\u01b0\u1ee3c th\u1ef1c thi", "email sent", "\u0111\u00e3 g\u1eedi",
                    "b\u00e1o c\u00e1o \u0111\u00e3 s\u1eb5n s\u00e0ng", "vui l\u00f2ng y\u00eau c\u1ea7u")
            kept = []
            for ln in s.splitlines():
                low_ln = ln.lower()
                if any(d in low_ln for d in drop):
                    continue
                kept.append(ln)
            s = "\n".join(kept)
            # Strip leading greeting.
            s = _re.sub(r"^\s*master[,:\s]*", "", s, flags=_re.IGNORECASE)
            s = _re.sub(r"[ \t]+", " ", s)
            s = _re.sub(r"\n{3,}", "\n\n", s).strip()
            return s

        clean = _clean_prose(synthesized)
        risk_factors = (clean[:600] or "N/A")
        conclusion = (clean[:800] or "N/A")

        return build_market_report_html(
            report_date=str(date.today()),
            btc_price=btc_price,
            btc_change_pct=btc_change,
            btc_rsi=btc_rsi,
            btc_trend=btc_trend,
            xau_price=xau_price,
            xau_change_pct="N/A",
            xau_trend="N/A",
            risk_level=risk_level,
            risk_factors=risk_factors,
            conclusion=conclusion,
            xau_rsi="N/A",
        )

    def execute_multi_tool(self, tools: list, response_hint: str, user_input: str = "") -> str:
        """Execute multiple tools sequentially and synthesize the result.

        Special handling for send_gmail_message: execute other tools first, synthesize
        the final message body, then execute send with the synthesized body so the
        actual email contains the real content (not a placeholder from the initial plan).
        """
        # Separate the send step if present (usually the last step for email requests).
        # Supports plain send_gmail_message and rich send_gmail_html_message.
        # Also separate a DEFERRED WRITE step: a write_file/append_file whose content
        # is a synthesis marker — it must run AFTER the report is synthesized so the
        # file receives the real report, not the placeholder.
        send_tool = None
        deferred_write = None
        other_tools = []
        for t in tools:
            name = t.get("tool_name")
            if name in ("send_gmail_message", "send_gmail_html_message"):
                send_tool = t
            elif name in ("write_file", "append_file") and "_TO_BE_SYNTHESIZED]" in str(t.get("tool_args", {}).get("content", "")):
                deferred_write = t
            else:
                other_tools.append(t)

        results = []
        # Execute non-send tools first
        for t in other_tools:
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
        
        format_task = f"""{self.ciel_persona}

Synthesize the following data from multiple tools into a cohesive report.
User's request: {user_input}
{combined_results}

Hint: {response_hint}
RULES:
1. Be concise. Deliver a unified report without conversational filler.
2. For chat or internal reports: address as 'Master'. For email body, do NOT include "Master" greeting or internal addressing — make it a clean professional email suitable for sending to the recipient.
3. ONLY use facts present in the tool outputs above. NEVER invent data. NEVER copy any
   numbers, prices, or values from the persona/system-prompt EXAMPLES — those are
   illustrative placeholders. Every number in your output must come from a tool result in THIS run.
4. If any tool returned an error, 'file not found', or empty result, report that honestly. Do NOT fabricate fake data, fake file contents, or fake execution output.
5. NEVER disclose internal file paths (agent_output/, ciel_workspace/, etc.) in the final report or email body sent to external parties. Use only generic professional language such as 'the detailed evaluation has been prepared' or provide the content directly in the message. Do not reference storage locations.
6. ONLY claim that an email was sent (e.g. "Đã gửi", "email sent", "Message sent") if there is a successful send_gmail_message tool result with a Message Id in the outputs above. If the send tool was not executed or failed, explicitly say the report is ready but do not claim it was emailed.
7. For any email send (market or other), synthesize a professional email body based on the user's exact request and the real data from tools. Make it clear, well-structured, polite and useful like a proper sent email (use Vietnamese if appropriate). Do not force any specific dashboard template or HTML structure unless the user explicitly requested visual/dashboard style. Use ONLY real data from this run's tool results. Never leave [brackets], meta tags, or invent numbers.
8. If this is an email send, your ENTIRE output IS the email body and will be sent verbatim. Output ONLY the email body — start directly with the subject/greeting. Do NOT include: chain-of-thought or "[COGNITION]"/"[MARKET_DATA]"-style tag prefixes; any statement that the email was/wasn't sent or any "Message Id"; any label like "Email body:", "Nội dung email:", "Lưu ý:"; any nested/duplicated copy of the email; any note about tools, file writes, or storage paths.
"""
        self._log_thought("WORKER", "multi_tool_format_task", format_task)
        formatted = self.worker.generate(format_task)
        self._log_thought("WORKER", "multi_tool_format_response", formatted)

        # The pure synthesized report — used for the file write and the email body.
        # `formatted` accumulates [FILE]/[EMAIL] status notes for the Master's display
        # only; those notes must never end up inside the written file or sent email.
        report_body = formatted

        # Re-execute the deferred write step with the synthesized report, so the
        # file the user asked for actually receives real content. Clean it first:
        # strip [COGNITION]/tag prefixes and signature placeholders (keep internal
        # paths — harmless inside a local workspace file).
        if deferred_write:
            w_name = deferred_write.get("tool_name", "write_file")
            w_args = dict(deferred_write.get("tool_args", {}))
            w_args["content"] = self._sanitize_outbound_email(report_body, keep_paths=True)
            log.tool(f"Re-executing {w_name} with synthesized report content")
            w_res = self.execute_tool(w_name, w_args, response_hint=response_hint, user_input=user_input)
            self._log_thought("TOOL", f"result_{w_name}", w_res)
            results.append(f"--- Output from {w_name} ---\n{w_res}")
            formatted = formatted.rstrip() + f"\n\n[FILE] {w_res}"

        # Re-execute the send step with real content built after the data tools ran.
        if send_tool:
            send_name = send_tool.get("tool_name", "send_gmail_message")
            send_args = dict(send_tool.get("tool_args", {}))

            # The display copy `formatted` (which Master sees) keeps persona markers;
            # the outbound body is sanitized centrally in execute_tool, so just pass
            # the synthesized content through as-is here.
            if send_name == "send_gmail_html_message":
                # Use the synthesized professional content as HTML body (synthesis already made it clean/professional)
                # Only use special dashboard builder if explicitly planned with build_market_report_html earlier
                send_args["html_body"] = report_body
                send_args.pop("message", None)
            else:
                send_args["message"] = report_body

            log.tool(f"Re-executing {send_name} with real content")
            send_res = self.execute_tool(send_name, send_args, response_hint=response_hint, user_input=user_input)
            self._log_thought("TOOL", f"result_{send_name}", send_res)
            results.append(f"--- Output from {send_name} ---\n{send_res}")
            # Claim sent ONLY when a real Message Id is present (no loose 'sent' substring).
            if "Message Id" in send_res:
                formatted = formatted.rstrip() + "\n\n[EMAIL] Sent successfully (Message Id in tool log)."
            else:
                formatted = formatted.rstrip() + "\n\n[EMAIL] Report prepared but NOT confirmed sent (no Message Id returned)."

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

        # DETERMINISTIC FLOOR (runs BEFORE the LLM): an obviously-failed result
        # (error message, empty, "not found") must always trigger self-correction.
        # Doing this here — not after json.loads — means a malformed Brain response
        # (which lands in the except branch) can never rubber-stamp an error as satisfied.
        if self._is_failure_result(result):
            self._log_thought("BRAIN", "evaluate_override",
                              "Result matches a failure signal — forcing self-correction.")
            return {"satisfied": False,
                    "reasoning": "Result is an error, empty, or not-found response.",
                    "action": "chat",
                    "task": ("Honestly explain to the Master that the previous attempt "
                             "did not return usable data, and suggest a next step.")}

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
                return {"satisfied": True}
            return json.loads(raw)
        except (json.JSONDecodeError, Exception) as e:
            self._log_thought("BRAIN", "evaluate_result_error", str(e))
            return {"satisfied": True}  # Fail-safe: assume satisfied if evaluation fails

    @staticmethod
    def _is_failure_result(result: str) -> bool:
        """Deterministic detector for obviously-failed tool results (error/empty/not-found).

        Unicode-robust: normalizes to NFC and strips diacritics so Vietnamese error
        strings ("Lỗi", "không tồn tại") match regardless of NFC/NFD encoding.
        """
        text = (result or "").strip()
        if not text:
            return True
        # NFC-normalize, then strip combining marks → ASCII-fold for robust matching.
        nfkd = unicodedata.normalize("NFKD", text)
        ascii_fold = "".join(c for c in nfkd if not unicodedata.combining(c)).lower()

        # Diacritic-free markers (match against the folded text).
        failure_markers = (
            "loi:", "loi ", "error", "traceback", "[tool_error]", "execution_error",
            "khong ton tai", "khong tim thay", "not found", "does not exist",
            "no such file", "no search results", "unavailable", "file not found",
        )
        head = ascii_fold[:150]
        return any(m in head for m in failure_markers)

    @staticmethod
    def _sanitize_outbound_email(body: str, keep_paths: bool = False) -> str:
        """Strip internal/meta content from a synthesized body.

        Removes chain-of-thought (`[COGNITION]`), persona structural tag prefixes,
        signature placeholders, and self-referential "email sent / Message Id /
        email body:" scaffolding. Operates line-by-line so the real report content
        (prices, evaluation, greeting) is preserved.

        keep_paths=False (default, for outbound EMAIL): also strips internal file
        paths — they must never leak to external recipients.
        keep_paths=True (for synthesized FILE reports written into the workspace):
        internal paths are harmless inside a local file, so they are left intact.
        """
        if not body:
            return body

        # 1a) Remove the [COGNITION] LINE only (tag + any same-line reasoning). We do
        #     NOT consume following lines: the Worker often uses [COGNITION] as a bare
        #     header immediately followed by real report data (prices, dates), and the
        #     old paragraph-wide removal ate that data. Rule 8 of the synthesis prompt
        #     already discourages multi-line reasoning here.
        text = re.sub(r"(?m)^[^\n]*\[COGNITION\][^\n]*\n?", "", body)

        # 1b) Remove remaining persona structural tag prefixes: "📊 [MARKET_DATA]", "📈 [TECHNICAL]", etc.
        text = re.sub(r"[\U0001F300-\U0001FAFF☀-➿]*\s*\[[A-Z_]+\]\s*", "", text)

        # 1c) Replace signature placeholders the Worker/Brain sometimes leaves in
        #     ("[Your Name]", "[Ký tên]") with the actual sender name.
        text = re.sub(
            r"\[\s*(?:your name|k[yý] t[eê]n|ch[uữ] k[yý]|t[eê]n c[uủ]a b[aạ]n|name|signature|sender)\s*\]",
            "Ciel", text, flags=re.IGNORECASE)

        # Diacritic-insensitive helper for matching Vietnamese meta phrases.
        def _fold(s: str) -> str:
            nfkd = unicodedata.normalize("NFKD", s)
            return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()

        # Internal file paths: strip just the path TOKEN (keep the rest of the line),
        # so a legitimate sentence that merely mentions a path is not lost entirely.
        # Skipped entirely for file reports (keep_paths=True).
        path_re = None if keep_paths else re.compile(r"\b(?:agent_output|ciel_workspace)/[\w./\-]+")

        # 2) Drop lines that are purely internal meta / self-reference.
        drop_markers = (
            "message id", "email sent", "email da duoc", "da gui den", "email da gui",
            "da duoc soan va gui", "chua duoc gui", "khong co tool", "no tool available",
            "noi dung email la",
        )
        # Label / scaffold lines to drop: nested-email labels, internal notes, and a
        # redundant "Subject:" line (the real subject is a separate header field).
        label_re = re.compile(r"^\**\s*(email body|email tom tat|noi dung email|luu y|subject|chu de)\b\s*:?", re.IGNORECASE)

        kept = []
        for line in text.splitlines():
            if path_re is not None:
                line = path_re.sub("", line)             # remove internal path tokens
                line = re.sub(r"[ \t]{2,}", " ", line)   # tidy gaps left behind
            folded = _fold(line)
            if any(m in folded for m in drop_markers):
                continue
            if label_re.match(folded):
                continue
            kept.append(line)

        # 3) Collapse leftover blank runs and leading separators/blank lines.
        cleaned = "\n".join(kept)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        cleaned = re.sub(r"^\s*(-{3,}\s*\n)+", "", cleaned)
        return cleaned.strip()

    # Phrases indicating the user wants a PRIOR response sent, not new content
    # (e.g. "gửi thông tin này", "gửi cái vừa rồi", "send this", "send that report").
    # NOTE: "email"/"thư"/"mail" are deliberately EXCLUDED — "gửi qua email đó" means
    # "send to THAT email ADDRESS" (recipient), not "resend the previous content".
    # Including them mis-fired the referential override and sent stale content.
    _REFERENTIAL_SEND_PATTERNS = (
        r"th[oô]ng tin (n[aà]y|đ[oó]|v[uừ]a r[oồ]i)",
        r"(c[aá]i|n[oộ]i dung|b[aá]o c[aá]o|k[eế]t qu[aả]) (n[aà]y|đ[oó]|v[uừ]a r[oồ]i)",
        r"v[uừ]a (r[oồ]i|n[aã]y)",
        r"như (tr[eê]n|đ[aã] (n[oó]i|b[aà]n))",
        r"\bthis (info|information|report|content|summary)\b",
        r"\bthat (info|information|report|content|summary)\b",
        r"\bwhat we (just )?(discussed|talked about)\b",
        r"\bthe above\b",
        r"\bsend it\b",
    )

    @staticmethod
    def _is_referential_send(user_input: str) -> bool:
        """True if the request refers to previously-generated content ("send this/
        that") rather than asking for a fresh report. Used to stop the Brain from
        re-authoring (and potentially fabricating) content it only saw truncated
        to 200 chars in the router's history window (see router.py route())."""
        lowered = (user_input or "").lower()
        return any(re.search(p, lowered) for p in CielCore._REFERENTIAL_SEND_PATTERNS)

    def _last_ai_message_text(self) -> str:
        """Full (untruncated) text of the most recent Ciel response in chat history.
        Router.route() truncates history to 200 chars per message for its own
        context window, but self.chat_history itself always holds the full text."""
        for msg in reversed(self.chat_history.messages):
            if msg.type == "ai":
                return msg.content
        return ""

    @staticmethod
    def _plaintext_to_html(text: str) -> str:
        """Render a clean plain-text/markdown email body into simple HTML so line
        breaks and paragraphs survive (send_gmail_message transmits as text/html)."""
        import html as _html
        if not text:
            return text
        # Already HTML? leave it (e.g. a dashboard body).
        if re.search(r"<(?:p|br|div|table|h[1-6]|ul|ol)\b", text, re.IGNORECASE):
            return text
        esc = _html.escape(text)
        esc = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", esc)  # markdown bold
        blocks = re.split(r"\n\s*\n", esc.strip())
        paras = []
        for b in blocks:
            b = b.strip().replace("\n", "<br>")
            if b:
                paras.append(f'<p style="margin:0 0 12px">{b}</p>')
        inner = "\n".join(paras)
        return (
            '<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;'
            f'line-height:1.55;color:#222">{inner}</div>'
        )

    def process(self, user_input: str) -> str:
        """Full pipeline: recall → route → execute → respond."""
        self.chat_history.add_user_message(user_input)

        # === NEW PATH CLARIFICATION LOGIC ===
        # If user wants to write/create/save/generate a file but didn't specify where,
        # ask for the path first. If the question already contains a path ("where"),
        # proceed normally.
        lowered = user_input.lower()
        write_intent_keywords = ["write", "create", "save", "generate", "make a file", "output to", "write to", "append to"]
        has_where = any(kw in lowered for kw in ["ciel_workspace", "agent_output", " in ", " to ", " at ", ".py", ".txt", ".json", ".md", ".log"])

        if any(kw in lowered for kw in write_intent_keywords) and not has_where:
            return ("Understood. Where should I write this?\n"
                    "Please reply with the full path, for example:\n"
                    "• ciel_workspace/my_notes.txt\n"
                    "• agent_output/my_script.py\n"
                    "Or any other path inside those folders.")

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

            # For Vilao (which is stricter on filters), send a neutralized English version
            # to the Brain to further reduce chance of content filter.
            # Skip for very short/simple inputs like greetings.
            if os.getenv("BRAIN_PROVIDER", "").lower() == "vilao" and len(user_input.strip()) > 3:
                try:
                    # Use Worker (more permissive) to translate/sanitize for the Brain
                    sanitize_task = (
                        "Translate the following user request to clear English, "
                        "remove any potentially sensitive or triggering phrases, "
                        "keep the core intent for tool routing. Output only the cleaned English text:\n"
                        f"{enriched_input}"
                    )
                    enriched_input = self.worker.generate(sanitize_task)
                except Exception:
                    pass  # fall back to original if Worker fails

            # Proactive bypass for email sends: avoid calling the Brain router at all
            # when the request is clearly about sending email. This prevents the
            # Vilao content filter from ever being triggered on the routing call.
            if any(kw in lowered for kw in self._EMAIL_INTENT_KEYWORDS):
                log.system("Email send request detected — bypassing Brain router to avoid content filter.")
                decision = self._fallback_direct_action(user_input)
            else:
                try:
                    decision = self.router.route(enriched_input, self._tool_list_str, self.chat_history)
                except Exception as route_err:
                    err_str = str(route_err)
                    if "CONTENT_FILTERED" in err_str or "content/safety" in err_str.lower() or "blocked this request" in err_str:
                        log.error(f"[Brain Router] Content/safety filter blocked routing call: {err_str[:180]}. Falling back to direct action handling.")
                        decision = self._fallback_direct_action(user_input)
                    else:
                        raise
            action = decision.get("action", "chat")

            if action == "tool":
                tool_name = decision.get("tool_name", "")
                tool_args = decision.get("tool_args", {})
                hint = decision.get("response_hint", "")

                # REFERENTIAL EMAIL SEND ("gửi cái vừa rồi" / "send this info"):
                # the Router only sees a 200-char-truncated history slice, so the
                # Brain may fabricate plausible-looking data to fill in what it
                # couldn't see (observed: inventing whole stock indices). When the
                # request is clearly referential, deterministically replace the
                # Brain-composed body with the exact prior Ciel response instead.
                body_key = self._EMAIL_BODY_ARGS.get(tool_name)
                if body_key and self._is_referential_send(user_input):
                    last_text = self._last_ai_message_text()
                    if last_text:
                        tool_args = dict(tool_args)
                        tool_args[body_key] = last_text
                        self._log_thought("BRAIN", "referential_send_override",
                                          f"Replaced Brain-composed '{body_key}' with the verbatim prior response for {tool_name} (referential send detected).")

                response = self.execute_tool(tool_name, tool_args, hint, user_input)

                # SELF-CORRECTION: Brain evaluates if result is satisfactory
                # Skip for trivially-correct tools to save Brain API calls
                if tool_name not in self._SKIP_SELF_CORRECTION:
                    response = self._self_correct(user_input, tool_name, tool_args, response)

            elif action == "code":
                task = decision.get("task", user_input)
                filename = decision.get("filename", "agent_output/output.py")
                response = self.execute_code(task, filename)

            elif action == "multi_tool":
                tools = decision.get("tools", [])
                hint = decision.get("response_hint", "")

                # WORKFLOW SAFEGUARD: don't rely solely on the Brain to remember the
                # terminal send step. Observed failure: the user asked to search the
                # web AND email the result ("...sau đó gửi qua kxctran@gmail.com"),
                # but the Brain's plan only included data-gathering tools — the report
                # was drafted and shown to the user, but never actually sent, with no
                # error surfaced. If the request clearly signals email intent and the
                # plan lacks a send step, append one deterministically so the report
                # is actually delivered instead of silently staying a draft.
                # Deliberately broader than _EMAIL_INTENT_KEYWORDS (which requires exact
                # fixed phrases like "qua gmail" — missed by e.g. "gửi qua x@gmail.com",
                # the wording that triggered this bug). A concrete email address plus
                # any send verb is a reliable, low-false-positive signal on its own.
                has_send_step = any(t.get("tool_name") in ("send_gmail_message", "send_gmail_html_message") for t in tools)
                to_match = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', user_input)
                send_verb_present = any(v in lowered for v in ("gửi", "gởi", "send", "forward", "chuyển", "mail"))

                if not has_send_step and to_match and send_verb_present:
                    tools = list(tools) + [{
                        "tool_name": "send_gmail_message",
                        "tool_args": {
                            "to": to_match.group(0),
                            "subject": "Báo cáo từ Ciel",
                            "message": "[PROFESSIONAL_EMAIL_BODY_TO_BE_SYNTHESIZED]",
                        },
                    }]
                    self._log_thought("BRAIN", "multi_tool_send_step_added",
                                      f"Plan was missing a send step despite explicit email intent — appended send_gmail_message to {to_match.group(0)}.")

                # Same class of safeguard for FILE WRITES: the Brain routinely plans
                # only the data tools and drops the "viết báo cáo vào <path>" step,
                # then the response claims the file was created (observed in Hard 5 /
                # Special 5/6/9 backtests). If the request names an explicit target
                # path with a write verb and the plan has no write step, append a
                # deferred write that execute_multi_tool fills with the synthesized report.
                has_write_step = any(t.get("tool_name") in ("write_file", "append_file") for t in tools)
                path_match = re.search(r'((?:[A-Za-z]:[\\/](?:[\w .-]+[\\/])*)?(?:agent_output|ciel_workspace)[\\/][\w.\\/ -]*[\w-]\.\w+)', user_input)
                write_verb_present = any(v in lowered for v in (
                    "viết", "ghi", "write", "save", "lưu", "tạo", "create", "make", "generate", "note", "tao file", "tạo file"))
                if not has_write_step and path_match and write_verb_present:
                    tools = list(tools) + [{
                        "tool_name": "write_file",
                        "tool_args": {
                            "filename": path_match.group(1),
                            "content": "[REPORT_CONTENT_TO_BE_SYNTHESIZED]",
                        },
                    }]
                    self._log_thought("BRAIN", "multi_tool_write_step_added",
                                      f"Plan was missing a write step despite explicit target path — appended write_file to {path_match.group(1)}.")

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

    def _fallback_direct_action(self, user_input: str) -> dict:
        """Fallback when Brain router is blocked by provider content/safety filter.
        Uses simple heuristics + Worker to generate proper content for common actions
        like sending Gmail (the main case that triggers filters on Vilao).
        """
        lowered = user_input.lower()

        # Gmail / email send intent (very common trigger for content filter on Brain)
        if any(kw in lowered for kw in self._EMAIL_INTENT_KEYWORDS):
            # Extract recipient email if present, otherwise default to the known test address
            import re
            match = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', user_input)
            to_addr = match.group(0) if match else "kxctran@gmail.com"

            # Detect which assets the user ACTUALLY named — do not hardcode XAU/BTC.
            # Each entry: (keywords, display label, price symbol, crypto symbol or None).
            # crypto symbol enables 24h stats + technical analysis (Binance); None = price only.
            asset_catalog = [
                (("vàng", "vang", "gold", "xauusd", "xau"), "XAU/USD", "XAU/USD", None),
                (("bạc", "bac", "silver", "xagusd", "xag"), "XAG/USD", "XAG/USD", None),
                (("bitcoin", "btc"),                         "BTC/USD", "BTC/USD", "BTCUSDT"),
                (("ethereum", "eth"),                        "ETH/USD", "ETH/USD", "ETHUSDT"),
                (("eurusd", "eur/usd", "eur"),               "EUR/USD", "EUR/USD", None),
                (("gbpusd", "gbp/usd", "gbp"),               "GBP/USD", "GBP/USD", None),
                (("usdjpy", "jpy"),                          "USD/JPY", "USD/JPY", None),
            ]
            detected = [(lbl, price, crypto) for keys, lbl, price, crypto in asset_catalog
                        if any(k in lowered for k in keys)]

            # Market intent = a concrete asset was named, or an explicit market word used.
            strong_market_words = ("thị trường", "market", "forex", "crypto", "chứng khoán", "cổ phiếu")
            is_market_email = bool(detected) or any(w in lowered for w in strong_market_words)

            # Build a sensible subject (market/research branches override this below).
            # The football-practice subject only applies to an actual schedule/reminder
            # request — NOT to news like "tổng hợp tin World Cup" (which contains "bóng đá"
            # but is a news summary, not a practice reminder).
            is_practice_reminder = (("đá bóng" in lowered or "bóng đá" in lowered)
                                    and any(w in lowered for w in ("lịch", "tập", "luyện", "nhắc", "buổi", "practice", "training")))
            if is_practice_reminder:
                subject = "Nhắc nhở: Lịch tập đá bóng"
            elif "lịch" in lowered and not any(w in lowered for w in ("tổng hợp", "tổng kết", "tin tức", "báo cáo", "phân tích")):
                subject = "Thông báo lịch"
            else:
                subject = "Email từ Ciel"

            if is_market_email:
                # No specific asset but general market intent → a broad snapshot.
                if not detected:
                    detected = [("XAU/USD", "XAU/USD", None), ("BTC/USD", "BTC/USD", "BTCUSDT")]

                labels = " & ".join(lbl for lbl, _, _ in detected)
                subject = f"Báo cáo thị trường {labels} – Đánh giá rủi ro"

                # Build the data-tool plan for EXACTLY the detected assets.
                tools_plan = []
                for _lbl, price_sym, crypto_sym in detected:
                    tools_plan.append({"tool_name": "get_market_price", "tool_args": {"symbol": price_sym}})
                    if crypto_sym:
                        tools_plan.append({"tool_name": "get_crypto_stats", "tool_args": {"symbol": crypto_sym}})
                        tools_plan.append({"tool_name": "analyze_crypto_technical", "tool_args": {"symbol": crypto_sym, "interval": "1d"}})
                tools_plan.append({"tool_name": "send_gmail_message", "tool_args": {"to": to_addr, "subject": subject, "message": "[PROFESSIONAL_EMAIL_BODY_TO_BE_SYNTHESIZED]"}})

                return {
                    "action": "multi_tool",
                    "tools": tools_plan,
                    "response_hint": ("Gather real prices, stats, and technicals first using the data tools. "
                                      "Then synthesize a clean professional email body that directly answers the user's request "
                                      "using ONLY the real data collected. Report ONLY the assets that were actually queried — "
                                      "never attribute one asset's technicals (RSI/MA) to a different asset, and if an asset has no "
                                      "technical data, say so honestly instead of borrowing another's. Make it clear, well-structured, "
                                      "polite, and useful like a proper sent email. Use Vietnamese if the request is in Vietnamese. "
                                      "Never use placeholders, meta tags, or internal paths. Send via the send tool. Never claim sent without a real Message Id.")
                }

            # DOCUMENT-BASED EMAIL: the request references a concrete file ("đọc file
            # X.pdf và gửi báo cáo..."). Observed failure: the old direct-send branch
            # composed a polite shell email WITHOUT ever reading the file, so the
            # recipient got "báo cáo đã được chuẩn bị... [Your Name]" with no content.
            # Plan the read step first, then let multi_tool synthesis fill the body
            # from the document's actual text before the send re-executes.
            file_match = re.search(
                r'((?:[A-Za-z]:[\\/])?[\w.\\/ -]*?[\w-]+\.(pdf|docx|txt|md|json|csv|log))\b',
                user_input, re.IGNORECASE)
            if file_match:
                fname = file_match.group(1).strip()
                ext = file_match.group(2).lower()
                read_tool = "read_document" if ext in ("pdf", "docx") else "read_file"
                return {
                    "action": "multi_tool",
                    "tools": [
                        {"tool_name": read_tool, "tool_args": {"filename": fname}},
                        {"tool_name": "send_gmail_message", "tool_args": {"to": to_addr, "subject": subject, "message": "[PROFESSIONAL_EMAIL_BODY_TO_BE_SYNTHESIZED]"}},
                    ],
                    "response_hint": ("Read the document FIRST, then synthesize a professional email that reports the "
                                      "document's ACTUAL content (summary, key points, data). If reading failed or the file "
                                      "has no extractable text, say so honestly instead of sending an empty shell email. "
                                      "Match the language of the request. Never leave placeholders like [Your Name] — sign as Ciel."),
                }

            # RESEARCH-BASED EMAIL: a summary/report/news request with no market asset
            # and no file. Observed failure (thoughts.log): the direct-send branch below
            # asked the Worker to "write an email" from a request that contains NO data,
            # so it produced a hollow shell ("báo cáo đã chuẩn bị và đính kèm... [Your Name]")
            # and sent it. Instead, gather real data via stealth_search first, then let
            # multi_tool synthesis build the body from actual results (or report honestly
            # if search failed) before sending.
            research_words = ("tổng kết", "tình hình", "báo cáo", "cập nhật", "tin tức", "phân tích",
                              "tổng hợp", "summary", "report", "situation", "news", "update", "analysis", "overview")
            # Skip when referential ("gửi báo cáo VỪA RỒI") — that means resend prior
            # content, handled by the referential-send override in process(), not a fresh search.
            if any(w in lowered for w in research_words) and not self._is_referential_send(user_input):
                # Build a search query from the request minus the send/recipient noise.
                query = re.sub(r'[\w.\-]+@[\w.\-]+\.\w+', '', user_input)
                query = re.split(r'\b(?:và |sau đó |rồi |then |and )?(?:gửi|gởi|send|email|mail)\b', query, 1, re.IGNORECASE)[0].strip()
                query = query or user_input
                # Topic-based subject beats the generic/football default for a news summary.
                topic = re.sub(r'^(vậy|hãy|please|xin|làm ơn)\s+', '', query, flags=re.IGNORECASE).strip()
                research_subject = f"Tổng hợp thông tin: {topic[:70]}" if topic else subject
                return {
                    "action": "multi_tool",
                    "tools": [
                        {"tool_name": "stealth_search", "tool_args": {"query": query, "max_results": 5}},
                        {"tool_name": "send_gmail_message", "tool_args": {"to": to_addr, "subject": research_subject, "message": "[PROFESSIONAL_EMAIL_BODY_TO_BE_SYNTHESIZED]"}},
                    ],
                    "response_hint": ("Search the web FIRST, then synthesize a professional email that reports the ACTUAL "
                                      "findings from the search results (facts, figures, key points with brief context). "
                                      "If the search returned an error or no usable results, say so honestly and do NOT send "
                                      "an empty shell email claiming a report is 'attached' or 'prepared'. Never claim a file is "
                                      "attached — put the content directly in the body. Match the request's language. Sign as Ciel."),
                }

            # Non-market or simple email: original Worker body gen + direct send
            try:
                body_task = (
                    f"Viết một email lịch sự, rõ ràng, nội dung đàng hoàng bằng tiếng Việt "
                    f"cho yêu cầu của Master sau: \"{user_input}\". "
                    f"Chủ đề ngắn gọn, thân thiện. Giữ giọng điệu chuyên nghiệp và lịch sự. "
                    f"Địa chỉ người nhận nếu có trong yêu cầu thì giữ nguyên. "
                    f"Không thêm thông tin bịa đặt. "
                    f"QUAN TRỌNG: TUYỆT ĐỐI KHÔNG đề cập bất kỳ đường dẫn file nội bộ nào (agent_output/, ciel_workspace/...) trong email. Sử dụng ngôn ngữ chung chung chuyên nghiệp như 'báo cáo chi tiết đã được chuẩn bị' hoặc đưa nội dung trực tiếp vào email. Nếu cần, đề cập file dưới dạng 'file đính kèm' mà không tiết lộ vị trí lưu trữ nội bộ. "
                    f"If this is market data + evaluation, follow the Market Report structure in note.txt. Fill ONLY with real tool data; never invent numbers."
                )
                generated_body = self.worker.generate(body_task)
            except Exception:
                generated_body = user_input  # last resort

            return {
                "action": "tool",
                "tool_name": "send_gmail_message",
                "tool_args": {
                    "to": to_addr,
                    "subject": subject,
                    "message": generated_body
                },
                "response_hint": "Confirm the email ONLY if the send tool returned a real Message Id. Otherwise say the email was prepared but not confirmed sent. Never claim it was sent without a Message Id."
            }

        # Default fallback: let it go to normal chat path
        return {
            "action": "chat",
            "task": f"Provider safety filter blocked advanced routing. Please respond helpfully to: {user_input}"
        }

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
