import json
import re
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type, retry_any
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_community.chat_message_histories import ChatMessageHistory
from langchain_openai import ChatOpenAI
from agent_system.models.brain import Brain, TRANSIENT_ERRORS
from agent_system.utils.logger import log
from agent_system.utils.usage import extract_usage, format_usage
from agent_system.config import (
    RETRY_MAX_ATTEMPTS, RETRY_INITIAL_WAIT, RETRY_MAX_WAIT, BRAIN_MODEL,
    ROUTER_ASSISTANT_ENABLED, ROUTER_ASSISTANT_PROVIDER, ROUTER_ASSISTANT_MODEL,
    VILAO_URL, VILAO_API_KEY, API_KEY, BASE_URL, LLM_REQUEST_TIMEOUT,
    ROUTER_PERSONA_MODE,
)

# Stage-0 deterministic gate: any of these signals means the turn almost certainly needs
# the Brain to plan a tool/action, so we skip the assistant and go straight to the Brain.
# An email address or a workspace path, or an action verb (EN + VI). Kept broad on purpose —
# a false "needs Brain" only costs latency; a false "chat" would skip a real action.
_TOOL_SIGNAL_RE = re.compile(
    r"[\w.+\-]+@[\w.\-]+\.\w+"                                         # email address (+tag form included)
    r"|(?:ciel_workspace|agent_output)[\\/]"                            # workspace path
    r"|\.(?:py|txt|json|md|log|csv|pdf|docx|html|xlsx|png)\b"           # a filename token
    r"|\b(?:send|gửi|gởi|mail|email|read|đọc|write|ghi|save|lưu|create|tạo|delete|xóa|xoá|"
    r"run|chạy|execute|price|giá|search|tìm|scrape|git|commit|push|todo|weather|calculate|"
    r"tính|screenshot|vision|remember|nhớ|fact)\b",
    re.IGNORECASE)


# Slim triage prompt for the assistant — deliberately NO persona and NO tool schema, so it
# is fast/cheap. Its ONLY job is CHAT-vs-ESCALATE; the Brain still does all real planning.
_ASSISTANT_TRIAGE_PROMPT = (
    "You are a fast intent-triage step for an AI assistant that has tools (files, email, "
    "shell, market data, web search, git, etc.).\n"
    "Decide ONE thing about the user's message:\n"
    "  CHAT — it can be answered with a plain conversational reply, NO tool/action needed.\n"
    "  ESCALATE — it needs a tool, an action, data lookup, or any multi-step planning.\n"
    "Reply with EXACTLY one word: CHAT or ESCALATE. When in doubt, reply ESCALATE."
)

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
Any tool/multi_tool plan may also carry an optional "needs_followup": true (see RESULT-DEPENDENT REQUESTS).

Use multi_tool when the task needs several tools in sequence — whether independent OR dependent (a later step needing an earlier step's output).

DEPENDENT STEPS (a step that needs a previous step's result):
- In a later step's tool_args, reference an earlier step's raw output with "{{prev}}" (the immediately previous step) or "{{step_N}}" (the N-th step, 1-indexed). The system substitutes the real output at run time — you do NOT need to know that value now.
- Example — "read ciel_workspace/note.txt then send its content to Telegram" →
  tools: [read_file(filename="ciel_workspace/note.txt"), send_telegram(message="{{step_1}}")]
- Only use a reference when a step genuinely depends on a prior result. For the FINAL report/email/file body that summarizes several tools, still use the synthesis placeholder (below), NOT {{stepN}} — the system synthesizes those from all outputs.

RESULT-DEPENDENT REQUESTS ("if X then Y" — tools[] cannot express a condition):
- Plan ONLY the steps you are already sure of (the check itself) and add "needs_followup": true. Never guess the conditional step or invent its arguments — you will be re-asked with the real results in view.
- When re-asked and nothing remains, reply action="chat". Finishing is the expected outcome, not a failure.

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

WORKING DIRECTORY / REPO PATH:
- The request carries a "[WORKING DIRECTORY: ...]" note. Use that path for any tool argument that needs a repository or project location (git_status, git_diff, git_commit_and_push, git_confirm_push, git_list_repos) unless the Master explicitly names a different one.
- NEVER stop to ask the Master "which repository?" when that note is present — you already know. Asking for information you were just given reads as amnesia.

SEARCH QUERY LANGUAGE (stealth_search):
- The request carries a "[USER LANGUAGE: X]" note = the language the user ACTUALLY wrote in (the rest may have been translated to English for you).
- News / current events / weather / prices / anything with a LOCAL angle → write the query in THAT language (Vietnamese request → Vietnamese query, e.g. "tin tức nổi bật hôm nay"), so results come from that country's outlets. An English query returns foreign coverage the user never asked for.
- USER LANGUAGE: English, or an international / technical / scientific topic with no local angle → an English query is correct.
- NEVER put a literal date string in the query ("July 25 2026"): it matches pages that merely contain that text. Use the `timelimit` argument for recency ('d' day, 'w' week, 'm' month).

LIVE WEB / PREFER GOOGLE (stealth_search) — default when live data is needed:
- `stealth_search` is the primary live web path (real Google via SerpApi when configured;
  not "I can't search"). Prefer it over chat inventing an answer, and over claiming
  tools are unavailable.
- Master says "tra google", "search google", "look it up", "tra web", "lấy đi" after a
  failed/missing answer → action=tool, tool_name=stealth_search (or multi_tool +
  smart_scrape). Do NOT re-ask what to search if [OPEN THREAD] already states the topic.
- Weather / forecast: get_weather is CURRENT conditions only (one city, now). For
  "ngày mai", "dự báo", "tuần này", multi-day forecast, or when get_weather already
  failed / "chưa lấy được" → use stealth_search (timelimit='d' or 'w'), then
  smart_scrape a forecast page if snippets are thin. Prefer Google over refusing.
- News, prices-without-a-market-tool, "hôm nay có gì", any question that needs the
  open web → stealth_search first, not chat.

PATH HANDLING FOR WRITES / CREATE FILE:
- If the user's request does not mention a clear destination path (e.g. "ciel_workspace/..." or "agent_output/..."), the system will ask the user for the path before writing.
- If the request already contains the path ("where"), use it directly. Do not force agent_output or any default.
- A "[RECENT ENTITIES]" block may appear alongside the request — file paths and
  email addresses literally named in the last few turns of THIS conversation.
  USE these to fill in a path/address the CURRENT message itself doesn't repeat
  (e.g. Master named "stuff.txt" two turns ago, then just now supplied the
  content with no path in this message — the path is in [RECENT ENTITIES], not
  missing). Real failure this prevents: asking "where should I save this?"
  again after the Master already answered, then claiming "I can't write files"
  when nothing actually blocked it — the destination was sitting right there.
- An "[OPEN THREAD]" block may appear when the Master's CURRENT message is a
  short answer or nudge to YOUR last clarifying question / "couldn't fetch"
  admission (e.g. you asked "which city?", they replied "Hồ Chí Minh"; or they
  said "tra google để lấy đi" after you claimed no weather data), OR a deictic
  file follow-up ("append vào file đó", "đọc lại file đó") with an Active file
  path grounded from THIS conversation. Treat PRIOR + NOW as ONE request and
  call tools. Prefer stealth_search when the Master asked to Google/search, or
  the open ask is forecast/news/live web; use get_weather only for current
  conditions once the city is known; use append_file/read_file/write_file when
  an Active file path is given. Do NOT re-ask the same gap, do NOT route to chat
  just because the current line alone is a bare place-name / "look it up" /
  "file đó", and NEVER claim workspace or search tools are missing when they
  appear in the tool list.

EMAIL SEND REQUESTS:
- If the request asks to send information via email (keywords like "gửi mail", "send email", "gửi đến", "send to kxctran@gmail.com", "gửi báo cáo"), use multi_tool to first gather the necessary data/tools, then send a professional email as the final step.
- The email should be a clean, professional message that directly addresses the user's request using only real data from the tools. Do not force any specific template or dashboard layout unless the user explicitly asks for visual/dashboard style.
- Professional email means: clear structure, polite tone, facts only, useful and direct, no internal paths, no meta comments. Use send_gmail_message (or send_gmail_html_message if richer formatting improves readability) with the body synthesized after data collection.
- ANTI-HALLUCINATION FOR EMAIL BODIES: if you choose action="tool" for an email-sending tool (send_gmail_message, send_gmail_html_message, reply_to_email) and compose the body yourself, you may ONLY reuse facts/numbers that already appear verbatim in the conversation history above. Note that this history is truncated per message — if you cannot see the full prior content or the user is asking for NEW data (prices, stats, news) you don't already have, route to multi_tool to fetch it instead of inventing numbers, indices, or statistics to make the email sound complete.

DEICTIC / PRONOUN REFERENCES ("it", "that", "đó", "cái đó", "nó", "cái vừa rồi") —
WHICH TURN THEY MEAN:
- These words point at something said in the LIVE conversation's immediately
  preceding turn — NEVER at anything inside a "[RECALLED PAST CONTEXT]" block.
  That block is retrieved by topic similarity from a possibly UNRELATED past
  session (maybe days old); it is background only, never a stand-in for "what
  did we just say".
- You do not see the full live chat history — only "[CURRENT USER REQUEST]", any
  "[RECALLED...]" block, optional "[RECENT ENTITIES]", and optional "[OPEN THREAD]".
  When [OPEN THREAD] is present, resolve the deictic against THAT prior exchange
  (live, this session) and act. If a request is deictic and nothing in front of
  you names the referent, do NOT borrow one from the recalled block just because
  it is topically similar. Prefer action="chat" and ask what it refers to, or set
  "needs_followup": true, rather than guess a concrete entity (a symbol, a name,
  an address) that turns a request about X into tool_args about Y.
- Real failure this rule exists for: user asked for the EUR/USD quote, then said
  "compare it with the price you just checked for gold" — recalled context from
  days earlier happened to mention Bitcoin and gold together, and the Brain
  wrongly fetched Bitcoin data instead of EUR/USD. "it" meant EUR/USD (asked ONE
  turn ago, in this live conversation), never Bitcoin — recalled context is not
  where "it" gets resolved from.

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

        # Optional fast front-line triage model (see architecture: assistant + Brain).
        # Off unless ROUTER_ASSISTANT_ENABLED and a model name are set. Vilao-only wiring
        # for now; any failure here disables it (fail-safe → Brain-only, never crash).
        self._assistant_llm = None
        if ROUTER_ASSISTANT_ENABLED and ROUTER_ASSISTANT_MODEL:
            try:
                if ROUTER_ASSISTANT_PROVIDER.lower() == "vilao":
                    self._assistant_llm = ChatOpenAI(
                        model=ROUTER_ASSISTANT_MODEL,
                        api_key=VILAO_API_KEY,
                        base_url=VILAO_URL,
                        temperature=0.0,
                        timeout=LLM_REQUEST_TIMEOUT,
                    )
                    log.system(f"Router assistant: {ROUTER_ASSISTANT_MODEL} (Vilao, triage)")
                elif ROUTER_ASSISTANT_PROVIDER.lower() == "custom":
                    self._assistant_llm = ChatOpenAI(
                        model=ROUTER_ASSISTANT_MODEL,
                        api_key=API_KEY,
                        base_url=BASE_URL or None,
                        temperature=0.0,
                        timeout=LLM_REQUEST_TIMEOUT,
                    )
                    log.system(f"Router assistant: {ROUTER_ASSISTANT_MODEL} (custom, triage)")
                else:
                    log.system(f"Router assistant provider '{ROUTER_ASSISTANT_PROVIDER}' not wired — assistant disabled.")
            except Exception as e:
                log.system(f"Router assistant init failed ({e}) — falling back to Brain-only routing.")
                self._assistant_llm = None

    def route(self, user_input: str, tool_list_str: str, chat_history: ChatMessageHistory) -> dict:
        """Two-tier routing. A fast assistant triages CHAT-vs-ESCALATE for conversational
        turns; anything with a deterministic tool signal, or that the assistant escalates,
        goes to the full Brain planner. Assistant off (or any failure) → Brain-only, i.e.
        exactly the previous behavior."""
        self.log_thought("USER", "request", user_input)

        if self._assistant_llm is not None:
            # STAGE 0 — deterministic gate: obvious tool/action signals skip the assistant
            # (no point asking; they need the Brain anyway) and pay zero assistant latency.
            if _TOOL_SIGNAL_RE.search(user_input or ""):
                self.log_thought("ROUTER", "tier_brain", "tool signal in request → straight to Brain.")
            else:
                # STAGE 1 — assistant triage on a no-signal (likely conversational) turn.
                verdict = self._classify_with_assistant(user_input)
                if verdict == "chat":
                    self.log_thought("ROUTER", "tier_assistant", "assistant → CHAT (fast path).")
                    log.brain("Routed: [CHAT] (assistant fast-path)")
                    return {"action": "chat", "task": user_input}
                self.log_thought("ROUTER", "tier_brain", f"assistant → {verdict} → escalating to Brain.")

        # STAGE 2 — full Brain planner.
        return self._route_brain(user_input, tool_list_str, chat_history)

    def _classify_with_assistant(self, user_input: str) -> str:
        """Return 'chat' or 'escalate'. Fail-safe: any error/ambiguity → 'escalate', so a
        broken assistant never silently turns a real action into a chat reply."""
        try:
            resp = self._assistant_llm.invoke([
                SystemMessage(content=_ASSISTANT_TRIAGE_PROMPT),
                HumanMessage(content=user_input),
            ])
            self.log_thought("ROUTER", "LLM_CALL",
                             format_usage(ROUTER_ASSISTANT_MODEL, extract_usage(resp)))
            content = resp.content
            if isinstance(content, list):
                content = "".join(c.text if hasattr(c, "text") else str(c) for c in content)
            up = (content or "").upper()
            if "ESCALATE" in up and "CHAT" not in up:
                return "escalate"
            if "CHAT" in up and "ESCALATE" not in up:
                return "chat"
            return "escalate"  # both/neither present → ambiguous → fail-safe
        except Exception as e:
            self.log_thought("ROUTER", "assistant_error", f"{type(e).__name__}: {e} → escalating.")
            return "escalate"

    # TIER 4 — the router emits JSON and nothing else, yet it has always been sent the
    # full 1,205-token character description: 28% of every Brain call spent on voice,
    # for a component that never speaks. What routing actually needs from the persona is
    # the handful of facts that change a CLASSIFICATION — who the operator is, and that
    # acting on their machine and accounts is in scope, so a legitimate request is not
    # refused as impossible. That is a sentence, not a page.
    #
    # Kept behind a config switch with `full` as the default, because "obviously
    # redundant" is exactly the kind of claim that turns out to be wrong once measured;
    # see the A/B numbers in note.md before changing the default.
    _SLIM_PERSONA = (
        "You are Ciel, the personal AI assistant and System Sentinel of your Master. "
        "You genuinely operate the Master's machine, files and accounts, so legitimate "
        "in-scope requests must be routed to real tools, never refused as impossible.")

    def _router_persona(self) -> str:
        mode = ROUTER_PERSONA_MODE
        if mode == "none":
            return ""
        if mode == "slim":
            return self._SLIM_PERSONA
        return self.persona

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
    def _route_brain(self, user_input: str, tool_list_str: str, chat_history: ChatMessageHistory) -> dict:
        """Full Brain (Opus) routing — the original route() body. Only invoked when the
        turn escalates past the fast assistant triage (or when the assistant is off)."""
        base_prompt = f"{self._router_persona()}\n\n{CIEL_ROUTER_PROMPT}" \
            if self._router_persona() else CIEL_ROUTER_PROMPT
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
