"""Brain — The Router/Orchestrator.
Supports Ollama (local) and Gemini (cloud) as providers.
Only outputs JSON plans. Never generates long text or code."""
import json
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
    RETRY_MAX_ATTEMPTS,
    RETRY_INITIAL_WAIT,
    RETRY_MAX_WAIT,
    ALLOWED_TOOL_NAMES,
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
    {"step": 3, "task": "Generate scraper.py with a Scraper class that takes a Config instance", "assign": "worker", "shared_context": "Config() is in config.py with attributes: base_url, timeout, headers."},
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
- If the user asks to WRITE CODE or CREATE A FILE -> worker step + buffer_write tool step with flush_to.
- If the user asks a QUESTION or wants TEXT -> single worker step, no file output, set "output_filepath" to null.
- If the user asks to WRITE MULTIPLE FILES -> separate worker+buffer_write cycles per file. Use "flush_to" in each buffer_write step.
- Keep task descriptions concise but specific enough for the Worker to execute.
- NEVER include actual code in your output. The Worker writes all code.
- The ONLY allowed tool_name is "buffer_write". Do NOT invent other tool names.

FILE PATH & IMPORT RULES:
- ALWAYS prefix file paths with "agent_output/" when writing files.
- NEVER use bare filenames like "calculator.py" — always "agent_output/calculator.py".
- IMPORTS MUST BE FLAT: Always use flat imports between project files (e.g., `from config import Config` or `from scraper import Scraper`). Do NOT use the `agent_output.` prefix in Python import statements."""


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
            )
            self._reflect_llm = ChatOllama(
                model=BRAIN_MODEL,
                base_url=OLLAMA_BASE_URL,
                temperature=BRAIN_TEMPERATURE,
            )
            log.system(f"Brain initialized: {BRAIN_MODEL} (Ollama, dual-instance)")
        elif BRAIN_PROVIDER.lower() == "deepseek":
            self._router_llm = ChatOpenAI(
                model=BRAIN_MODEL,
                api_key=DEEPSEEK_API_KEY,
                base_url="https://api.deepseek.com",
                temperature=BRAIN_TEMPERATURE,
            )
            self._reflect_llm = self._router_llm
            log.system(f"Brain initialized: {BRAIN_MODEL} (DeepSeek)")
        else:
            self._router_llm = ChatGoogleGenerativeAI(
                model=BRAIN_MODEL,
                google_api_key=GEMINI_API_KEY,
                temperature=BRAIN_TEMPERATURE,
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
