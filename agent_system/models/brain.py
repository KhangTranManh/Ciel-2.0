"""Brain — The Router/Orchestrator.
Supports Ollama, DeepSeek, Vilao (via OpenAI compat), and Gemini.
Only outputs JSON plans. Never generates long text or code."""
import json
import os
import httpx
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)
from langchain_ollama import ChatOllama
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

from ..config import (
    BRAIN_PROVIDER,
    BRAIN_MODEL,
    BRAIN_TEMPERATURE,
    GEMINI_API_KEY,
    DEEPSEEK_API_KEY,
    OLLAMA_BASE_URL,
    VILAO_URL,
    VILAO_API_KEY,
    GPT_API_KEY,
    GPT_BASE_URL,
    RETRY_MAX_ATTEMPTS,
    RETRY_INITIAL_WAIT,
    RETRY_MAX_WAIT,
    ALLOWED_TOOL_NAMES,
    LLM_REQUEST_TIMEOUT,
)
from ..utils.logger import log


# ==========================================================
# TRANSIENT ERRORS — only these get retried.
# Programming bugs (KeyError, JSONDecodeError, etc.) fail immediately.
# ==========================================================
TRANSIENT_ERRORS = (
    ConnectionError,
    TimeoutError,
    OSError,
    httpx.ConnectError,
    httpx.ReadTimeout,
    httpx.ConnectTimeout,
    httpx.RemoteProtocolError,
)


# ==========================================================
# SYSTEM PROMPT — This IS the model's ground truth.
# ==========================================================
BRAIN_SYSTEM_PROMPT = """You are the BRAIN — a strict JSON router and task planner.

ABSOLUTE RULES:
1. You ONLY output valid JSON. Never prose, never code blocks, never markdown.
2. You decompose the user's request into numbered steps.
3. Each step is assigned to either "worker" (for content/code generation) or "tool" (for buffer/file ops).
4. Your JSON output MUST follow this exact schema:

SINGLE FILE OUTPUT:
{
  "plan": [
    {"step": 1, "task": "Generate the complete content for the file", "assign": "worker"},
    {"step": 2, "task": "Save to file", "assign": "tool", "tool_name": "buffer_write", "flush_to": "agent_output/filename.py"}
  ],
  "output_filepath": null
}

MULTIPLE FILE OUTPUT (each file gets its own worker+buffer+flush cycle):
{
  "plan": [
    {"step": 1, "task": "Generate config.py with a Config class containing base_url, timeout, headers", "assign": "worker"},
    {"step": 2, "task": "Save to file", "assign": "tool", "tool_name": "buffer_write", "flush_to": "agent_output/config.py"},
    {"step": 3, "task": "Generate scraper.py with a Scraper class that takes a Config instance", "assign": "worker", "shared_context": "Config has: base_url (str), timeout (int), headers (dict). Import with: from config import Config"},
    {"step": 4, "task": "Save to file", "assign": "tool", "tool_name": "buffer_write", "flush_to": "agent_output/scraper.py"},
    {"step": 5, "task": "Generate main.py runner script", "assign": "worker", "shared_context": "Config() is in config.py. Scraper(config) is in scraper.py and takes a Config instance. Import with: from scraper import Scraper"},
    {"step": 6, "task": "Save to file", "assign": "tool", "tool_name": "buffer_write", "flush_to": "agent_output/main.py"}
  ],
  "output_filepath": null
}

CRITICAL: WORKER ISOLATION
- Each Worker step is COMPLETELY ISOLATED. It cannot see the output of any previous step.
- If a later step needs to know about earlier work (e.g. "import Config from config"), you MUST include a "shared_context" field in that step.
- "shared_context" must contain ONLY: class names, file locations, and constructor signatures.
- NEVER put full code or method names in shared_context. The Worker will figure out methods itself.

ROUTING LOGIC:
- If the user asks to WRITE CODE or CREATE A FILE -> worker step + buffer_write tool step with flush_to (use the path the user specifies or clarify).
- If the user asks a QUESTION or wants TEXT -> single worker step, no file output, set "output_filepath" to null.
- If the user asks to WRITE MULTIPLE FILES -> separate worker+buffer_write cycles per file. Use "flush_to" in each buffer_write step.
- Keep task descriptions concise but specific enough for the Worker to execute.
- NEVER include actual code in your output. The Worker writes all code.
- The ONLY allowed tool_name is "buffer_write". Do NOT invent other tool names.

FILE PATH RULES:
- Respect the path the user provides if mentioned.
- Otherwise, the system will ask for the destination path (ciel_workspace/ or agent_output/ or specific).

NEVER LEAK INTERNAL PATHS IN EXTERNAL COMMUNICATIONS:
- When the final output is an email or message to an external recipient, NEVER include any internal paths such as agent_output/, ciel_workspace/, or any file system locations.
- Refer to saved reports only in generic terms: "the detailed evaluation has been prepared", "see the attached summary", or include the content directly in the email body.
- If the user specifically asks to share a file path with an external party, confirm explicitly before doing so.

EMAIL TEMPLATES AND SELECTION:
When preparing content for emails (especially when request mentions market data/evaluation + send email), first determine the type:
- For email sends with data/reports: Gather facts first, then create a professional email body based on the user's request + real data. Do not force any specific fixed template or Report.pdf dashboard unless user explicitly asks for visual/dashboard style.
- Todo/Productivity: use Todo / Productivity Summary template.
- General task/status: use General Task / Status Report template.
- Alerts/digests: use Alert / Warning / Digest template.
- Gmail replies/summaries: use Gmail-related template.
- Else: General / Custom Content template.
Fill only with real data from tool results. Follow exact sections, order, and tone from the chosen template. Generate the email body from it.
"""

# ==========================================================
# RETRY LOGGING HELPER
# ==========================================================
def _log_retry(retry_state):
    """Log retry attempts with error context."""
    exc = retry_state.outcome.exception()
    exc_name = type(exc).__name__
    exc_msg = str(exc)[:120]
    wait = retry_state.next_action.sleep
    attempt = retry_state.attempt_number
    log.error(
        f"Brain call failed ({exc_name}: {exc_msg}). "
        f"Retrying in {wait:.1f}s (attempt {attempt}/{RETRY_MAX_ATTEMPTS})"
    )


class Brain:
    """Router that plans tasks as strict JSON."""

    def __init__(self):
        # TWO LLM instances:
        # _router_llm: format="json" for plan() — forces valid JSON output
        # _reflect_llm: no format constraint for reflect() — allows natural text
        if BRAIN_PROVIDER.lower() == "ollama":
            self._router_llm = ChatOllama(
                model=BRAIN_MODEL,
                base_url=OLLAMA_BASE_URL,
                temperature=BRAIN_TEMPERATURE,
                format="json",
                timeout=LLM_REQUEST_TIMEOUT,
            )
            self._reflect_llm = ChatOllama(
                model=BRAIN_MODEL,
                base_url=OLLAMA_BASE_URL,
                temperature=BRAIN_TEMPERATURE,
                timeout=LLM_REQUEST_TIMEOUT,
            )
            log.system(f"Brain initialized: {BRAIN_MODEL} (Ollama, dual-instance)")
        elif BRAIN_PROVIDER.lower() == "deepseek":
            self._router_llm = ChatOpenAI(
                model=BRAIN_MODEL,
                api_key=DEEPSEEK_API_KEY,
                base_url="https://api.deepseek.com",
                temperature=BRAIN_TEMPERATURE,
                timeout=LLM_REQUEST_TIMEOUT,
            )
            self._reflect_llm = self._router_llm
            log.system(f"Brain initialized: {BRAIN_MODEL} (DeepSeek)")
        elif BRAIN_PROVIDER.lower() == "vilao":
            model_name = BRAIN_MODEL or "alic/qwen3.7-max"
            # Try to reduce upstream content/safety filtering on Vilao.
            # The provider still has the final say, but these hints + lighter prompts help.
            extra = {}
            if os.getenv("VILAO_SAFETY_BYPASS", "false").lower() in ("true", "1", "yes") or os.getenv("SAFETY_OPEN", "true").lower() in ("true", "1", "yes"):
                extra = {"extra_body": {"safe_mode": False, "safety": False, "content_filter": False}}

            llm_kwargs = {
                "model": model_name,
                "api_key": VILAO_API_KEY,
                "base_url": VILAO_URL,
                "temperature": BRAIN_TEMPERATURE,
                "timeout": LLM_REQUEST_TIMEOUT,
            }
            if extra and "extra_body" in extra:
                llm_kwargs["extra_body"] = extra["extra_body"]
            self._router_llm = ChatOpenAI(**llm_kwargs)
            self._reflect_llm = self._router_llm
            log.system(f"Brain initialized: {model_name} (Vilao, safety-bypass={bool(extra)})")
        elif BRAIN_PROVIDER.lower() == "gpt":
            # Support for GPT / OpenAI-compatible providers (e.g. custom gateways with model gx/gpt-5.5)
            base = GPT_BASE_URL or None
            self._router_llm = ChatOpenAI(
                model=BRAIN_MODEL,
                api_key=GPT_API_KEY,
                base_url=base,
                temperature=BRAIN_TEMPERATURE,
                timeout=LLM_REQUEST_TIMEOUT,
            )
            self._reflect_llm = self._router_llm
            log.system(f"Brain initialized: {BRAIN_MODEL} (GPT/OpenAI-compatible)")
        else:
            self._router_llm = ChatGoogleGenerativeAI(
                model=BRAIN_MODEL,
                google_api_key=GEMINI_API_KEY,
                temperature=BRAIN_TEMPERATURE,
                timeout=LLM_REQUEST_TIMEOUT,
            )
            self._reflect_llm = self._router_llm  # Gemini handles both fine
            log.system(f"Brain initialized: {BRAIN_MODEL} (Gemini)")

    # Issue 1 fix: Only retry transient network/connection errors.
    # Programming bugs (KeyError, JSONDecodeError) fail immediately.
    @retry(
        stop=stop_after_attempt(RETRY_MAX_ATTEMPTS),
        wait=wait_exponential(multiplier=RETRY_INITIAL_WAIT, max=RETRY_MAX_WAIT),
        retry=retry_if_exception_type(TRANSIENT_ERRORS),
        before_sleep=_log_retry,
    )
    def plan(self, user_request: str) -> dict:
        """Send user request to Brain LLM, return parsed & validated JSON plan."""
        log.brain(f"Analyzing request: \"{user_request[:80]}{'...' if len(user_request) > 80 else ''}\"")
        log.divider()

        messages = [
            SystemMessage(content=BRAIN_SYSTEM_PROMPT),
            HumanMessage(content=user_request),
        ]

        response = self._router_llm.invoke(messages)
        raw = response.content.strip()

        # Strip markdown code fences if model wraps JSON in ```json ... ```
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1]  # remove first line
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()

        # Issue 2 fix: No silent fallback. If JSON is broken, raise so
        # the caller sees the real error instead of a fake plan.
        parsed = json.loads(raw)  # Raises JSONDecodeError — fails fast, no retry

        # TOOL NAME VALIDATION
        # Reject any tool_name not in the allowed set.
        plan_steps = parsed.get("plan", [])
        for step in plan_steps:
            if step.get("assign") == "tool":
                tool_name = step.get("tool_name", "")
                if tool_name not in ALLOWED_TOOL_NAMES:
                    log.error(f"Brain hallucinated tool: '{tool_name}' — replacing with 'buffer_write'")
                    step["tool_name"] = "buffer_write"

        filepath = parsed.get("output_filepath")

        log.brain(f"Plan created: {len(plan_steps)} step(s)")
        for step in plan_steps:
            assign_label = "WORKER" if step.get("assign") == "worker" else "TOOL"
            log.brain(f"  Step {step.get('step', '?')}: [{assign_label}] {step.get('task', '')[:60]}")
        if filepath:
            log.brain(f"  Output file: {filepath}")
        log.divider()

        return parsed

    @retry(
        stop=stop_after_attempt(RETRY_MAX_ATTEMPTS),
        wait=wait_exponential(multiplier=RETRY_INITIAL_WAIT, max=RETRY_MAX_WAIT),
        retry=retry_if_exception_type(TRANSIENT_ERRORS),
        before_sleep=_log_retry,
    )
    def reflect(self, user_request: str, step_results: list) -> str:
        """Ask Brain to produce a final summary from all step results."""
        log.brain("Assembling final response...")

        # Truncate each result to 200 chars for the summary
        truncated = [r[:200] + "..." if len(r) > 200 else r for r in step_results]
        combined = "\n\n---\n\n".join(truncated)

        messages = [
            SystemMessage(content=(
                "You are assembling a final response for the user. "
                "You receive truncated summaries of each step's result. "
                "Produce a clean, concise summary or confirmation. "
                "If the output is code, just confirm it was written successfully. "
                "Keep it short. Do NOT output JSON."
            )),
            HumanMessage(content=(
                f"Original request: {user_request}\n\n"
                f"Step results:\n{combined}"
            )),
        ]

        # Uses _reflect_llm (no format="json" constraint)
        response = self._reflect_llm.invoke(messages)
        return response.content.strip()
