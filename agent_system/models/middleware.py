"""Middleware — Semantic verifier/finalizer for outbound content (emails/reports).

Third tier alongside Brain (routes) and Worker (generates). Middleware does NOT
judge formatting or meta-tags — the deterministic sanitizer in core/llm_connector.py
already strips those. Middleware judges what only an LLM can judge:
  1. RELEVANCE   — does the body actually answer the user's request?
  2. CONSISTENCY — no internal contradictions, no unresolved placeholders, no
                   claims of "sent"/"attached" without basis.
  3. GROUNDING   — does content read as reporting real data, or as invented filler?

It acts as a FINALIZER, not a gate: on finding a real problem it edits the body
in place (one pass, no ping-pong loop back to the Worker) rather than just
approving/rejecting. Bias toward approval — only intervene for genuine
correctness problems, never for style preferences.
"""
import json
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)
from langchain_core.messages import SystemMessage, HumanMessage

from ..config import (
    MIDDLEWARE_PROVIDER,
    MIDDLEWARE_MODEL,
    MIDDLEWARE_TEMPERATURE,
    GEMINI_API_KEY,
    DEEPSEEK_API_KEY,
    OLLAMA_BASE_URL,
    VILAO_URL,
    VILAO_API_KEY,
    VILAO_SAFETY_BYPASS,
    GPT_API_KEY,
    GPT_BASE_URL,
    RETRY_MAX_ATTEMPTS,
    RETRY_INITIAL_WAIT,
    RETRY_MAX_WAIT,
    LLM_REQUEST_TIMEOUT,
)
from ..utils.logger import log
from .brain import TRANSIENT_ERRORS  # same transient-error set used by Brain/Router


MIDDLEWARE_SYSTEM_PROMPT = """You are the MIDDLEWARE — a strict but conservative quality
finalizer for outbound content (an email/report about to be sent to a real external
recipient). Formatting and meta-tags are already cleaned upstream — you judge SEMANTIC
correctness ONLY. Check exactly these three things:

1. RELEVANCE: Does the body actually answer/address the user's original request? (If the
   user asked about topic X, the body must be about X — not a different, unrelated topic.)
2. CONSISTENCY: No internal contradictions (e.g. claiming data is both available and
   unavailable), no unresolved placeholders, no claim of being "sent"/"attached" without
   basis in the content itself, no number attributed to the wrong subject within the SAME
   body (e.g. gold's price mislabeled as Bitcoin's).
3. STRUCTURAL GROUNDING: Flag content that reads as a templated shell with no real content
   (e.g. "the report has been prepared and attached" with no actual data), or text that is
   clearly meta-narration about the system itself rather than the report content.

CRITICAL — DO NOT DO THIS: You have NO access to live prices, news, or current events, and
your own training data may be outdated. NEVER reject or "correct" a number, price, date, or
fact merely because it looks unusual, too high/low, or unfamiliar compared to what you
remember — you cannot verify real-world figures and MUST NOT substitute your own
recollection for the system's live data. Only flag a number if it is INTERNALLY
inconsistent within the body itself (e.g. the same sentence states two different prices for
the same asset), never because it conflicts with your own world knowledge.

Respond ONLY with valid JSON, no markdown fences:
- {"approved": true} — the body is fine as-is.
- {"approved": false, "reasoning": "1 sentence", "revised_body": "<the corrected full body>"}
  — you found a genuine problem AND can fix it by editing the body.
- {"approved": false, "reasoning": "1 sentence", "revised_body": null} — a genuine problem
  exists but you cannot confidently fix it (be conservative: only use this for real defects).

Bias strongly toward {"approved": true}. Minor style/wording preferences are NEVER a reason
to intervene. Only act on genuine relevance, consistency, or grounding defects."""


def _log_retry(retry_state):
    exc = retry_state.outcome.exception()
    log.error(
        f"Middleware call failed ({type(exc).__name__}: {str(exc)[:120]}). "
        f"Retrying in {retry_state.next_action.sleep:.1f}s (attempt {retry_state.attempt_number}/{RETRY_MAX_ATTEMPTS})"
    )


class Middleware:
    """Single-LLM semantic reviewer/finalizer. Mirrors Brain's provider selection."""

    def __init__(self):
        provider = MIDDLEWARE_PROVIDER.lower()
        if provider == "vilao":
            model_name = MIDDLEWARE_MODEL or "alic/qwen3.7-max"
            llm_kwargs = {
                "model": model_name,
                "api_key": VILAO_API_KEY,
                "base_url": VILAO_URL,
                "temperature": MIDDLEWARE_TEMPERATURE,
                "timeout": LLM_REQUEST_TIMEOUT,
            }
            if VILAO_SAFETY_BYPASS:
                llm_kwargs["extra_body"] = {"safe_mode": False, "safety": False, "content_filter": False}
            from langchain_openai import ChatOpenAI
            self._llm = ChatOpenAI(**llm_kwargs)
            log.system(f"Middleware initialized: {model_name} (Vilao)")
        elif provider == "deepseek":
            from langchain_openai import ChatOpenAI
            self._llm = ChatOpenAI(
                model=MIDDLEWARE_MODEL,
                api_key=DEEPSEEK_API_KEY,
                base_url="https://api.deepseek.com",
                temperature=MIDDLEWARE_TEMPERATURE,
                timeout=LLM_REQUEST_TIMEOUT,
            )
            log.system(f"Middleware initialized: {MIDDLEWARE_MODEL} (DeepSeek)")
        elif provider == "gpt":
            from langchain_openai import ChatOpenAI
            self._llm = ChatOpenAI(
                model=MIDDLEWARE_MODEL,
                api_key=GPT_API_KEY,
                base_url=GPT_BASE_URL or None,
                temperature=MIDDLEWARE_TEMPERATURE,
                timeout=LLM_REQUEST_TIMEOUT,
            )
            log.system(f"Middleware initialized: {MIDDLEWARE_MODEL} (GPT/OpenAI-compatible)")
        elif provider == "ollama":
            from langchain_ollama import ChatOllama
            self._llm = ChatOllama(
                model=MIDDLEWARE_MODEL,
                base_url=OLLAMA_BASE_URL,
                temperature=MIDDLEWARE_TEMPERATURE,
                format="json",
                timeout=LLM_REQUEST_TIMEOUT,
            )
            log.system(f"Middleware initialized: {MIDDLEWARE_MODEL} (Ollama)")
        else:
            from langchain_google_genai import ChatGoogleGenerativeAI
            self._llm = ChatGoogleGenerativeAI(
                model=MIDDLEWARE_MODEL,
                google_api_key=GEMINI_API_KEY,
                temperature=MIDDLEWARE_TEMPERATURE,
                timeout=LLM_REQUEST_TIMEOUT,
            )
            log.system(f"Middleware initialized: {MIDDLEWARE_MODEL} (Gemini)")

    @retry(
        stop=stop_after_attempt(RETRY_MAX_ATTEMPTS),
        wait=wait_exponential(multiplier=RETRY_INITIAL_WAIT, max=RETRY_MAX_WAIT),
        retry=retry_if_exception_type(TRANSIENT_ERRORS),
        before_sleep=_log_retry,
    )
    def review(self, user_input: str, body: str) -> dict:
        """Review outbound content. Returns {"approved": bool, "reasoning": str, "revised_body": str|None}.

        Fail-open on any parse/provider error: callers should treat an exception
        here as "approved" (send the original body) rather than blocking delivery
        on a Middleware hiccup — matches the fail-safe pattern used elsewhere
        (_evaluate_result's self-correction floor, confirm_callback=None).
        """
        messages = [
            SystemMessage(content=MIDDLEWARE_SYSTEM_PROMPT),
            HumanMessage(content=f"User's original request: {user_input}\n\nContent to review:\n{body}"),
        ]
        response = self._llm.invoke(messages)
        raw = response.content
        if isinstance(raw, list):
            raw = "".join(c.text if hasattr(c, "text") else str(c) for c in raw)
        raw = raw.strip()

        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1]
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()

        if not raw:
            return {"approved": True}
        return json.loads(raw)
