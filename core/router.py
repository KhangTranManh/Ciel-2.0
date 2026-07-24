import json
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type, retry_any
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_community.chat_message_histories import ChatMessageHistory
from agent_system.models.brain import Brain, TRANSIENT_ERRORS
from agent_system.utils.logger import log
from agent_system.utils.usage import extract_usage, format_usage
from agent_system.config import RETRY_MAX_ATTEMPTS, RETRY_INITIAL_WAIT, RETRY_MAX_WAIT, BRAIN_MODEL

def _extract_json_object(raw: str) -> str:
    """Return the first balanced top-level {...} block in `raw`.

    The Router requires pure-JSON output, but some Brain models (observed live with
    Opus via the Vilao gateway) intermittently wrap the JSON in prose or emit trailing
    text after the closing brace. Each such reply used to fail json.loads outright and
    burn a full ~14s Brain retry (3 in a row observed in one hard_special run) even
    though a perfectly valid decision object was sitting inside the reply. Slicing out
    the balanced object first makes those replies parse on the first attempt; replies
    with no JSON at all still raise ValueError so tenacity retries as before.

    Brace-counting is done outside string literals only (a '{' or '}' inside a quoted
    hidden_thought sentence must not shift the balance).
    """
    start = raw.find("{")
    if start == -1:
        raise ValueError("Router LLM reply contains no JSON object")
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(raw)):
        ch = raw[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return raw[start:i + 1]
    raise ValueError("Router LLM reply contains an unterminated JSON object")


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

MULTI_TOOL DECOMPOSITION (critical):
- Break the request into ONE tools[] entry PER numbered step / distinct sub-task, each with its own correct tool and minimal args. NEVER collapse a compound request into a single catch-all call (e.g. do NOT dump the whole request text into one search query).
- tool_args values must be the minimal literal argument for that step ("BTC/USD", "Hanoi"), never a copy of the request sentence.
- Example — "Check my unread Gmail, get the weather for Hanoi, calculate 12*9, then email a summary to a@b.com" →
  tools: [search_gmail(query="is:unread", resource="messages"), get_weather(city="Hanoi"), calculate(expression="12*9"), send_gmail_message(to="a@b.com", subject=<from request>, message="[PROFESSIONAL_EMAIL_BODY_TO_BE_SYNTHESIZED]")]
- For a final email/file-report step whose content depends on the other tools' data, set the body/content to the placeholder "[PROFESSIONAL_EMAIL_BODY_TO_BE_SYNTHESIZED]" (email) or "[REPORT_CONTENT_TO_BE_SYNTHESIZED]" (file) EXACTLY — the system fills it with the synthesized report after the data tools run. Do not write your own draft there and do not reword the placeholder.
- Email subject: use the user's stated subject verbatim if given; otherwise a short title of the TOPIC (never the request text itself).

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
- If the request asks to send information via email (keywords like "gửi mail", "send email", "gửi đến", "send to kxctran@gmail.com", "gửi báo cáo"), use multi_tool to first gather the necessary data/tools, then send a professional email as the final step.
- The email should be a clean, professional message that directly addresses the user's request using only real data from the tools. Do not force any specific template or dashboard layout unless the user explicitly asks for visual/dashboard style.
- Professional email means: clear structure, polite tone, facts only, useful and direct, no internal paths, no meta comments. Use send_gmail_message (or send_gmail_html_message if richer formatting improves readability) with the body synthesized after data collection.
- ANTI-HALLUCINATION FOR EMAIL BODIES: if you choose action="tool" for an email-sending tool (send_gmail_message, send_gmail_html_message, reply_to_email) and compose the body yourself, you may ONLY reuse facts/numbers that already appear verbatim in the conversation history above. Note that this history is truncated per message — if you cannot see the full prior content or the user is asking for NEW data (prices, stats, news) you don't already have, route to multi_tool to fetch it instead of inventing numbers, indices, or statistics to make the email sound complete.

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
        # Anchor the model to the real current date — its training data is older, so it
        # otherwise assumes a past year and searches e.g. "latest AI news 2024" in 2026.
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        cur_year = datetime.now().year
        date_note = (f"\n\nCURRENT DATE: {today}. The current year is {cur_year}. "
                     f"When the user asks for 'latest'/'recent'/'mới nhất' information or builds a web "
                     f"search query, use {cur_year} (or no year) — NEVER default to an older year.")
        messages = [SystemMessage(content=prompt + date_note)]

        # `chat_history` is intentionally NOT fed into the routing call (removed July
        # 2026). Classifying the CURRENT request's action doesn't need prior turns, and
        # including the last few raw messages let the Brain conflate an old unresolved
        # request (e.g. "create a todo script" left pending on a missing path) with a
        # LATER, unrelated request — observed producing an extra, unrequested file once
        # a usable path appeared in the new turn. Genuine cross-turn continuity is
        # already handled by two safer, more deliberate mechanisms: RAG recall (semantic-
        # relevance-gated, injected into `user_input` itself before this call) and
        # `_is_referential_send()` (deterministic "send that/gửi cái vừa rồi" handling).
        # The `chat_history` parameter is kept only for call-site/signature compatibility.
        messages.append(HumanMessage(content=user_input))

        self.log_thought("USER", "request", user_input)
        
        response = self.brain._router_llm.invoke(messages)
        self.log_thought("BRAIN", "LLM_CALL", format_usage(BRAIN_MODEL, extract_usage(response)))
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

        # Tolerate prose/trailing text around the decision object (see
        # _extract_json_object) instead of failing the whole ~14s call over wrapping.
        parsed = json.loads(_extract_json_object(raw))

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
