"""llm_connector.py — Bridge between Ciel's core and the Brain-Worker agent system.

Architecture:
  User Input → Brain (routing/planning) → decides:
    - "chat" → Worker generates natural response
    - "tool" → Ciel's existing ToolManager executes tools, Brain reflects
    - "code" → Worker generates code, buffer writes to disk

The Brain does ALL thinking. The Worker does ALL generating.
Ciel's tools (email, trading, shell, file ops) are executed by ToolManager directly.
"""
import os
import json
import httpx
import traceback
from pathlib import Path
from dotenv import load_dotenv
from colorama import Fore, Style
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_community.chat_message_histories import ChatMessageHistory

from .memory_manager import MemoryManager
from .tool_manager import ToolManager

# Import Brain and Worker from agent_system
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent_system.models.brain import Brain, TRANSIENT_ERRORS
from agent_system.models.worker import Worker
from agent_system.utils.logger import log
from agent_system.config import RETRY_MAX_ATTEMPTS, RETRY_INITIAL_WAIT, RETRY_MAX_WAIT

load_dotenv()


# ==========================================================
# BRAIN ROUTING PROMPT — tells the Brain about Ciel's tools
# ==========================================================
CIEL_ROUTER_PROMPT = """You are the BRAIN of an AI assistant called Ciel. You analyze user requests and route them.

You MUST output valid JSON with this schema:

CASE 1 — Normal conversation (no tools needed):
{{
  "action": "chat",
  "task": "Rephrase what the user wants so the Worker can answer it naturally"
}}

CASE 2 — User wants to use a Ciel tool (email, file ops, trading, shell):
{{
  "action": "tool",
  "tool_name": "exact_tool_name",
  "tool_args": {{"arg1": "value1", "arg2": "value2"}},
  "response_hint": "Short hint for how to present the result to the user"
}}

CASE 3 — User wants NEW code/program generated and saved:
{{
  "action": "code",
  "task": "Detailed description of what to generate",
  "filename": "agent_output/filename.py"
}}

AVAILABLE TOOLS:
{tool_list}

RULES:
- Output ONLY valid JSON. No prose, no markdown.
- For tool calls, match the exact tool name and argument names from the list above.
- If unsure, default to "chat".
- Never generate code yourself — that's the Worker's job.
- Use "chat" for greetings, questions, explanations, casual conversation.
- The "task" field must ALWAYS be a verb-led instruction for the Worker (e.g. "Explain what X is"). NEVER write the answer itself in the task field.
- ROUTING PRIORITY: If the user says "write X to a file" or "save X to a file" in the workspace, use "tool" with write_file. Only use "code" when the user wants you to GENERATE a new program/script and save it to agent_output/.
- For search_gmail: always include {{"resource": "messages"}} in tool_args unless the user specifically asks for threads.
"""


class CielCore:
    def __init__(self):
        self.tool_manager = ToolManager()
        self._tools = self.tool_manager.get_tools()

        # Build tool name -> schema map for the Brain
        self._tool_map = {t.name: t for t in self._tools}
        self._tool_list_str = self._build_tool_list()

        # Initialize Brain and Worker from agent_system
        self.brain = Brain()
        self.worker = Worker()

        log.system("CielCore initialized with Brain-Worker architecture")

        self.chat_history = ChatMessageHistory()
        self.max_history = 20
        self.base_dir = Path(__file__).resolve().parent.parent
        self.chat_memory_file = self.base_dir / "ciel_data" / "memory_bank.json"

        self.memory_manager = MemoryManager()
        self._load_chat_memory()

    # Hardcoded hints for tools whose auto-generated descriptions are incomplete
    _TOOL_HINTS = {
        "search_gmail": "Search emails. Args: query (required), resource='messages' (required, always use 'messages'), max_results (optional, default 5).",
    }

    # Only call the Worker to format these tools. Others are already readable.
    _TOOLS_NEEDING_FORMAT = {
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

            # Use hardcoded hint if available, otherwise auto-generate
            if name in self._TOOL_HINTS:
                lines.append(f"- {name}: {self._TOOL_HINTS[name]}")
                continue

            desc = tool.description[:80] if tool.description else "No description"
            # Get arg names from the schema if available
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
        if len(self.chat_history.messages) > self.max_history:
            self.chat_history.messages = self.chat_history.messages[-self.max_history:]

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

    @retry(
        stop=stop_after_attempt(RETRY_MAX_ATTEMPTS),
        wait=wait_exponential(multiplier=RETRY_INITIAL_WAIT, max=RETRY_MAX_WAIT),
        retry=retry_if_exception_type(TRANSIENT_ERRORS),
        before_sleep=lambda rs: log.error(
            f"Route call failed ({type(rs.outcome.exception()).__name__}). "
            f"Retrying in {rs.next_action.sleep:.1f}s (attempt {rs.attempt_number}/{RETRY_MAX_ATTEMPTS})"
        ),
    )
    def route(self, user_input: str) -> dict:
        """Ask the Brain to decide what to do with the user's input.

        Returns a dict with:
          {"action": "chat", "task": "..."}
          {"action": "tool", "tool_name": "...", "tool_args": {...}, "response_hint": "..."}
          {"action": "code", "task": "...", "filename": "..."}
        """
        # Build the routing prompt with the tool list
        prompt = CIEL_ROUTER_PROMPT.format(tool_list=self._tool_list_str)

        # Include recent chat history as proper alternating messages
        from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
        
        messages = [SystemMessage(content=prompt)]
        
        if self.chat_history.messages:
            for msg in self.chat_history.messages[-6:]:
                content = msg.content[:200]  # limit context length per message
                if msg.type == "human":
                    messages.append(HumanMessage(content=content))
                else:
                    messages.append(AIMessage(content=content))
                    
        messages.append(HumanMessage(content=user_input))

        self._log_thought("USER", "request", user_input)
        response = self.brain._router_llm.invoke(messages)
        raw = response.content.strip()
        self._log_thought("BRAIN", "route_decision", raw)

        # Strip markdown fences
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1]
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()

        # No silent fallback — JSONDecodeError fails fast.
        # Transient LLM errors (connection/timeout) are retried by tenacity above.
        parsed = json.loads(raw)

        action = parsed.get("action", "chat")
        log.brain(f"Routed: [{action.upper()}] {parsed.get('task', parsed.get('tool_name', ''))[:60]}")
        return parsed

    def execute_chat(self, task: str) -> str:
        """Worker generates a natural language response."""
        # Inject tool list to prevent hallucinated capabilities
        capabilities_context = ""
        task_lower = task.lower()
        if any(kw in task_lower for kw in ["capabilit", "what can you do", "able to do", "list your features"]):
            capabilities_context = (
                f"\n\nCRITICAL: The user is asking what you can do. You MUST ONLY list capabilities "
                f"from this exact tool list. Do NOT invent other skills like translation or research "
                f"unless explicitly covered by these tools:\n{self._tool_list_str}"
            )

        # Add Ciel's persona context
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
        """Execute a Ciel tool and have the Worker format the result."""
        log.tool(f"Executing: {tool_name}({tool_args})")

        # Validate tool exists
        if tool_name not in self._tool_map:
            log.error(f"Tool not found: {tool_name}")
            return f"[TOOL_ERROR] Tool '{tool_name}' not found."

        # Execute via ToolManager
        result = self.tool_manager.execute_tool(tool_name, tool_args)
        result_text = self.tool_manager.format_tool_result(result)

        # If the tool failed, return the raw error directly — don't send
        # it to the Worker where it gets wrapped in polite language and hidden.
        if "EXECUTION_ERROR" in result_text or "Error" in result_text[:30]:
            log.error(f"Tool {tool_name} failed: {result_text[:150]}")
            self._log_thought("TOOL", "error", f"{tool_name}: {result_text}")
            return f"[TOOL_ERROR] {tool_name}: {result_text}"

        self._log_thought("TOOL", "result", f"{tool_name}: {result_text}")

        # Skip formatting if the tool returns readable text natively
        if tool_name not in self._TOOLS_NEEDING_FORMAT:
            return result_text

        # Have Worker format SUCCESSFUL results into a nice response
        format_task = (
            f"You are Ciel. Format this tool output into the absolute shortest, clearest response possible.\n"
            f"Tool: {tool_name}\n"
            f"Raw result:\n{result_text[:2000]}\n\n"
            f"Hint: {response_hint}\n"
            f"CRITICAL: Be extremely concise. Give just the requested data. No conversational filler."
        )
        self._log_thought("WORKER", "format_task", format_task)
        formatted = self.worker.generate(format_task)
        self._log_thought("WORKER", "format_response", formatted)
        return formatted

    def execute_code(self, task: str, filename: str) -> str:
        """Worker generates code and writes it to disk."""
        from agent_system.tools.buffer_writer import buffer_writer

        buffer_writer.clear()  # Safety reset for dirty state
        self._log_thought("WORKER", "code_task", task)
        code = self.worker.generate(task)
        self._log_thought("WORKER", "code_response", code)
        buffer_writer.append(code)
        result = buffer_writer.flush(filename)
        log.tool(result)
        return f"Code written to {filename}"

    # ==========================================================
    # MAIN INTERFACE — called by agent_loop.py
    # ==========================================================
    def process(self, user_input: str) -> str:
        """Full pipeline: route → execute → respond.

        This replaces the old chat_with_tools() flow.
        """
        self.chat_history.add_user_message(user_input)

        try:
            # Step 1: Brain routes
            decision = self.route(user_input)
            action = decision.get("action", "chat")

            # Step 2: Execute based on routing
            if action == "tool":
                tool_name = decision.get("tool_name", "")
                tool_args = decision.get("tool_args", {})
                hint = decision.get("response_hint", "")
                response = self.execute_tool(tool_name, tool_args, hint)

            elif action == "code":
                task = decision.get("task", user_input)
                filename = decision.get("filename", "agent_output/output.py")
                response = self.execute_code(task, filename)

            else:  # "chat" or fallback
                task = decision.get("task", user_input)
                response = self.execute_chat(task)

            # Step 3: Save to history
            self.chat_history.add_ai_message(response)
            self._save_chat_memory()

            return response

        except Exception as e:
            log.error(f"Pipeline error: {e}")
            traceback.print_exc()
            return f"An error occurred: {str(e)[:200]}"

    # ==========================================================
    # LEGACY COMPATIBILITY — old agent_loop.py calls this
    # ==========================================================
    def chat_with_tools(self, user_input: str, use_coder: bool = False):
        """Legacy interface. Wraps process() in a fake AI message object."""
        response = self.process(user_input)

        # Return a simple object that agent_loop.py can extract content from
        class FakeAIMessage:
            def __init__(self, content):
                self.content = content
                self.tool_calls = []  # No native tool calls — Brain handles routing

        return FakeAIMessage(f"<RESPONSE>{response}</RESPONSE>")