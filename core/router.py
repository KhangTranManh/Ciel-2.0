import json
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type, retry_any
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain_community.chat_message_histories import ChatMessageHistory
from agent_system.models.brain import Brain, TRANSIENT_ERRORS
from agent_system.utils.logger import log
from agent_system.config import RETRY_MAX_ATTEMPTS, RETRY_INITIAL_WAIT, RETRY_MAX_WAIT

CIEL_ROUTER_PROMPT = """You are the BRAIN of an AI assistant called Ciel. You analyze user requests and route them.

EVERY JSON response MUST begin with a "hidden_thought" object containing:
- "observation": What you literally see in the user's input (1 sentence).
- "reasoning": Why you chose this action/tool, and what alternatives you rejected (1-2 sentences).
- "risk": Any risk identified (file overwrite, destructive command, sensitive data) or "none".

After "hidden_thought", include the action fields for one of these 4 cases:

CASE 1 -- Chat: {{"hidden_thought": {{...}}, "action": "chat", "task": "instruction for Worker"}}
CASE 2 -- Tool: {{"hidden_thought": {{...}}, "action": "tool", "tool_name": "name", "tool_args": {{}}, "response_hint": "..."}}
CASE 3 -- Code: {{"hidden_thought": {{...}}, "action": "code", "task": "description", "filename": "agent_output/file.py"}}
CASE 4 -- Multi-tool: {{"hidden_thought": {{...}}, "action": "multi_tool", "tools": [{{...}}], "response_hint": "..."}}

### MULTI-TOOL WORKFLOW LOGIC:
Sequence tool calls logically (search -> fetch -> combine). ONLY use CASE 4 when the user explicitly needs data from multiple sources. Do NOT use it for simple single-tool requests.

### ROLE: WINDOWS SYSTEM ARCHITECT
When routing to 'execute_shell_command' or 'run_python_script':
1. ALWAYS use absolute paths wrapped in double quotes (e.g., "D:\\Ciel 2.0\\script.py").
2. Prefer 'python -m' prefix for all module-related commands to avoid PATH conflicts.
3. If a previous task failed due to "File in use", suggest 'taskkill' first.

AVAILABLE TOOLS:
{tool_list}

RULES:
- Output ONLY valid JSON. No prose, no markdown.
- ALWAYS include "hidden_thought" as the FIRST field.
- For tool calls, match the exact tool name and argument names from the list above.
- NEVER output "action": "shell_command". Use "action": "tool" with "tool_name": "execute_shell_command".
- If unsure, default to "chat".
- Never generate code yourself -- that's the Worker's job.
- Use "chat" for greetings, questions, explanations, casual conversation.
- The "task" field must ALWAYS be a verb-led instruction for the Worker. NEVER write the answer itself.
- ROUTING PRIORITY: "write X to a file" -> use write_file tool. "Generate a program" -> use "code" action.
- WRITE+EXECUTE RULE: If the user asks to BOTH write/create a script AND run/execute it, you MUST use "multi_tool" with two steps: first a "code" step to generate the file, then a "tool" step with "run_python_script" to execute it. NEVER use "code" alone when execution is also requested.
- For search_gmail: always include {{"resource": "messages"}} in tool_args unless the user asks for threads.
- RECALLED CONTEXT: If a [RECALLED PAST CONTEXT] block already has the answer, use "chat" with that info. Do NOT call get_fact redundantly.
- For multi-step tasks ONLY, use "multi_tool" to sequentially gather data from multiple sources before responding.
"""

class Router:
    """Handles parsing and routing decisions with tenacity retry protection."""
    
    def __init__(self, brain: Brain, log_thought_fn, persona: str = ""):
        self.brain = brain
        self.log_thought = log_thought_fn
        self.persona = persona

    @retry(
        stop=stop_after_attempt(RETRY_MAX_ATTEMPTS),
        wait=wait_exponential(multiplier=RETRY_INITIAL_WAIT, max=RETRY_MAX_WAIT),
        retry=retry_any(
            retry_if_exception_type(TRANSIENT_ERRORS),
            retry_if_exception_type((ValueError, json.JSONDecodeError)),
        ),
        before_sleep=lambda rs: log.error(
            f"Route call failed ({type(rs.outcome.exception()).__name__}). "
            f"Retrying in {rs.next_action.sleep:.1f}s (attempt {rs.attempt_number}/{RETRY_MAX_ATTEMPTS})"
        ),
    )
    def route(self, user_input: str, tool_list_str: str, chat_history: ChatMessageHistory) -> dict:
        base_prompt = f"{self.persona}\n\n{CIEL_ROUTER_PROMPT}" if self.persona else CIEL_ROUTER_PROMPT
        prompt = base_prompt.format(tool_list=tool_list_str)
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
        # Gemini occasionally returns content as a list of parts instead of a plain string
        content = response.content
        if isinstance(content, list):
            content = "".join(c.text if hasattr(c, "text") else str(c) for c in content)
        raw = content.strip()
        self.log_thought("BRAIN", "route_decision", raw)

        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1]
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()

        if not raw:
            raise ValueError("Router LLM returned empty response -- retrying")

        parsed = json.loads(raw)

        # Extract and log Chain-of-Thought, then strip from decision
        hidden_thought = parsed.pop("hidden_thought", None)
        if hidden_thought:
            thought_lines = [
                f"Observation: {hidden_thought.get('observation', 'N/A')}",
                f"Reasoning:   {hidden_thought.get('reasoning', 'N/A')}",
                f"Risk:        {hidden_thought.get('risk', 'N/A')}",
            ]
            self.log_thought("BRAIN", "chain_of_thought", "\n".join(thought_lines))

        action = parsed.get("action", "chat")
        log.brain(f"Routed: [{action.upper()}] {parsed.get('task', parsed.get('tool_name', ''))[:60]}")
        return parsed
