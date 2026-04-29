import json
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain_community.chat_message_histories import ChatMessageHistory
from agent_system.models.brain import Brain, TRANSIENT_ERRORS
from agent_system.utils.logger import log
from agent_system.config import RETRY_MAX_ATTEMPTS, RETRY_INITIAL_WAIT, RETRY_MAX_WAIT

CIEL_ROUTER_PROMPT = """You are the BRAIN of an AI assistant called Ciel. You analyze user requests and route them.

You MUST output valid JSON with this schema:

CASE 1 — Normal conversation (no tools needed):
{{
  "action": "chat",
  "task": "Rephrase what the user wants so the Worker can answer it naturally"
}}

CASE 2 — User wants to use ONE Ciel tool (email, file ops, trading, shell):
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

CASE 4 — Multi-tool workflow (ONLY use when the user requests multiple different things or a complex workflow):
{{
  "action": "multi_tool",
  "tools": [
    {{"tool_name": "exact_tool_name", "tool_args": {{"arg1": "value1"}}}},
    {{"tool_name": "another_tool", "tool_args": {{"arg2": "value2"}}}}
  ],
  "response_hint": "How to synthesize the combined data"
}}

### MULTI-TOOL WORKFLOW LOGIC:
If using CASE 4, sequence the tool calls logically:
- Step A: Search for info.
- Step B: Fetch details.
- Step C: Combine with other data.
*Example:* If user explicitly asks for a morning briefing on BTC and emails, combine `get_market_price` and `search_gmail`.
*CRITICAL WARNING:* DO NOT use CASE 4 for simple requests. If the user just says "check my email", use CASE 2 (`search_gmail`). Do not hallucinate prices, weather, or extra steps!

### ROLE: WINDOWS SYSTEM ARCHITECT
When routing to 'execute_shell_command' or 'run_python_script':
1. ALWAYS use absolute paths wrapped in double quotes (e.g., "D:\Ciel 2.0\script.py").
2. Prefer 'python -m' prefix for all module-related commands to avoid PATH conflicts.
3. If a previous task failed due to "File in use", suggest a task to 'taskkill' the offending process first.

AVAILABLE TOOLS:
{tool_list}

RULES:
- Output ONLY valid JSON. No prose, no markdown.
- For tool calls, match the exact tool name and argument names from the list above.
- NEVER output "action": "shell_command". To run a shell command, you MUST use "action": "tool" and "tool_name": "execute_shell_command".
- If unsure, default to "chat".
- Never generate code yourself — that's the Worker's job.
- Use "chat" for greetings, questions, explanations, casual conversation.
- The "task" field must ALWAYS be a verb-led instruction for the Worker (e.g. "Explain what X is"). NEVER write the answer itself in the task field.
- ROUTING PRIORITY: If the user says "write X to a file" or "save X to a file" in the workspace, use "tool" with write_file. Only use "code" when the user wants you to GENERATE a new program/script and save it to agent_output/.
- For search_gmail: always include {{"resource": "messages"}} in tool_args unless the user specifically asks for threads.
- For multi-step tasks ONLY, use "multi_tool" to sequentially gather data from multiple sources before responding.
"""

class Router:
    """Handles parsing and routing decisions with tenacity retry protection."""
    
    def __init__(self, brain: Brain, log_thought_fn):
        self.brain = brain
        self.log_thought = log_thought_fn

    @retry(
        stop=stop_after_attempt(RETRY_MAX_ATTEMPTS),
        wait=wait_exponential(multiplier=RETRY_INITIAL_WAIT, max=RETRY_MAX_WAIT),
        retry=retry_if_exception_type(TRANSIENT_ERRORS),
        before_sleep=lambda rs: log.error(
            f"Route call failed ({type(rs.outcome.exception()).__name__}). "
            f"Retrying in {rs.next_action.sleep:.1f}s (attempt {rs.attempt_number}/{RETRY_MAX_ATTEMPTS})"
        ),
    )
    def route(self, user_input: str, tool_list_str: str, chat_history: ChatMessageHistory) -> dict:
        prompt = CIEL_ROUTER_PROMPT.format(tool_list=tool_list_str)
        messages = [SystemMessage(content=prompt)]
        
        if chat_history.messages:
            for msg in chat_history.messages[-6:]:
                content = msg.content[:200]
                if msg.type == "human":
                    messages.append(HumanMessage(content=content))
                else:
                    messages.append(AIMessage(content=content))
                    
        messages.append(HumanMessage(content=user_input))

        self.log_thought("USER", "request", user_input)
        
        response = self.brain._router_llm.invoke(messages)
        raw = response.content.strip()
        self.log_thought("BRAIN", "route_decision", raw)

        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1]
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()

        parsed = json.loads(raw)
        action = parsed.get("action", "chat")
        log.brain(f"Routed: [{action.upper()}] {parsed.get('task', parsed.get('tool_name', ''))[:60]}")
        return parsed
