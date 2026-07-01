import json
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type, retry_any
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain_community.chat_message_histories import ChatMessageHistory
from agent_system.models.brain import Brain, TRANSIENT_ERRORS
from agent_system.utils.logger import log
from agent_system.config import RETRY_MAX_ATTEMPTS, RETRY_INITIAL_WAIT, RETRY_MAX_WAIT

CIEL_ROUTER_PROMPT = """You are the BRAIN of an AI assistant called Ciel. You analyze user requests and route them.

Respond ONLY with valid JSON. Start with a hidden_thought object.

hidden_thought:
- "observation": short summary of user request
- "reasoning": why this action (1 sentence)
- "notes": any extra info or "none"

Then one of:
- {{"action": "chat", "task": "what the Worker should do"}}
- {{"action": "tool", "tool_name": "...", "tool_args": {{...}}, "response_hint": "..."}}
- {{"action": "code", "task": "...", "filename": "agent_output/xxx.py"}}
- {{"action": "multi_tool", "tools": [...], "response_hint": "..."}}

Use multi_tool only when the task clearly needs several independent tools in sequence.

AVAILABLE TOOLS:
{tool_list}

Strict rules:
- Only output the JSON object.
- Match tool names and arg names exactly.
- For Gmail search always pass resource="messages".
- Default to chat if unclear.

PATH HANDLING FOR WRITES / CREATE FILE:
- If the user's request does not mention a clear destination path (e.g. "ciel_workspace/..." or "agent_output/..."), the system will ask the user for the path before writing.
- If the request already contains the path ("where"), use it directly. Do not force agent_output or any default.

EMAIL SEND REQUESTS:
- If the request asks to send information via email (keywords like "gửi mail", "send email", "gửi đến", "send to kxctran@gmail.com", "gửi báo cáo"), the multi_tool MUST include "send_gmail_message" as the LAST tool in the list.
- Provide to= the email address, subject= suitable title, message= the evaluation (can use placeholder or summary based on data; self-correction will refine if needed).
- For market data + evaluation + send email (XAUUSD + BTC etc.): choose and base the message on the template from email_template/Report.pdf . Follow its sections, order, and tone exactly when building the content.

NEVER LEAK INTERNAL PATHS:
- In any email, external message, or report sent outside, NEVER mention internal paths like agent_output/, ciel_workspace/, or any filesystem locations.
- If a detailed report was saved, refer to it only generically as "the detailed evaluation" or "I have prepared the full analysis" without revealing where it is stored.
- Use "attached" or "as follows" if the content is in the email body itself.
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
