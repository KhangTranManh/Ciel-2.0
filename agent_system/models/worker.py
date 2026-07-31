"""Worker — The local Ollama model that does all heavy generation.
No tools, no routing. Pure text/code generation."""
import re
import httpx
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

from ..config import (
    WORKER_PROVIDER,
    DEEPSEEK_API_KEY,
    OLLAMA_BASE_URL,
    WORKER_MODEL,
    WORKER_TEMPERATURE,
    WORKER_NUM_CTX,
    RETRY_MAX_ATTEMPTS,
    RETRY_INITIAL_WAIT,
    RETRY_MAX_WAIT,
    LLM_REQUEST_TIMEOUT,
    VILAO_URL,
    VILAO_API_KEY,
    API_KEY,
    BASE_URL,
)
from ..utils.logger import log
from ..utils.usage import extract_usage


# Transient errors the Worker should retry on
WORKER_TRANSIENT_ERRORS = (
    ConnectionError,
    TimeoutError,
    OSError,
    httpx.ConnectError,
    httpx.ReadTimeout,
    httpx.ConnectTimeout,
    httpx.RemoteProtocolError,
)


WORKER_SYSTEM_PROMPT = """You are a Worker assistant. You receive a specific task and produce ONLY the requested output.

RULES:
1. Output ONLY the content requested — code, text, or data.
2. Do NOT add explanations, commentary, or meta-text unless the task explicitly asks for it.
3. Do NOT wrap code in markdown fences unless asked.
4. Do NOT call tools or suggest tool usage. You have no tools.
5. Be precise and complete. The output you produce will be used directly."""


def _log_worker_retry(retry_state):
    """Log Worker retry attempts."""
    exc = retry_state.outcome.exception()
    exc_name = type(exc).__name__
    exc_msg = str(exc)[:120]
    wait = retry_state.next_action.sleep
    attempt = retry_state.attempt_number
    log.error(
        f"Worker call failed ({exc_name}: {exc_msg}). "
        f"Retrying in {wait:.1f}s (attempt {attempt}/{RETRY_MAX_ATTEMPTS})"
    )


def _strip_markdown_fences(content: str) -> str:
    """Robustly strip markdown code fences from Worker output.

    Handles:
      - Full wrap: ```python\\ncode\\n```
      - Trailing newline after closing fence
      - Prose + multiple code blocks (extracts first code block only)
      - No fences at all (returns as-is)
    """
    content = content.strip()

    # Pattern 1: Entire output is one code block (with optional trailing whitespace)
    full_block = re.match(r'^```\w*\n(.*?)```\s*$', content, re.DOTALL)
    if full_block:
        return full_block.group(1).strip()

    # Pattern 2: Output contains prose + code blocks — extract the FIRST code block
    first_block = re.search(r'```\w*\n(.*?)```', content, re.DOTALL)
    if first_block:
        # Check if there's meaningful content outside the fence
        outside = content[:first_block.start()].strip() + content[first_block.end():].strip()
        if len(outside) < 20:
            # Almost everything was in the fence — use the extracted block
            return first_block.group(1).strip()
        else:
            # Significant prose around the code — still extract just the code
            # because this output goes into a .py file
            log.worker("Warning: Worker output contained prose around code block — extracting code only")
            return first_block.group(1).strip()

    # Pattern 3: No fences at all — return as-is
    return content


class Worker:
    """Local Ollama model for content/code generation. No tools attached."""

    def __init__(self):
        # Cost/usage tracking hook (optional): the owner (CielCore) sets this to a
        # callback logging one Worker call + its token usage into thoughts.log. Covers
        # every caller of generate() for free — direct formatting calls in
        # llm_connector.py AND recovery_manager.py's healing/syntax-check calls, since
        # they all share this same Worker instance. Signature: on_call(model, usage)
        # where usage is {"input","output","total"}. None by default so Worker stays
        # usable standalone.
        self.on_call = None

        if WORKER_PROVIDER.lower() == "deepseek":
            self._llm = ChatOpenAI(
                model=WORKER_MODEL,
                api_key=DEEPSEEK_API_KEY,
                base_url="https://api.deepseek.com",
                temperature=WORKER_TEMPERATURE,
                timeout=LLM_REQUEST_TIMEOUT,
            )
            log.system(f"Worker initialized: {WORKER_MODEL} (DeepSeek)")
        elif WORKER_PROVIDER.lower() == "vilao":
            # OpenAI-compatible Vilao gateway — mirrors the Brain's vilao branch
            # (agent_system/models/brain.py). Added because the Worker previously
            # supported only deepseek/gemini/ollama and silently fell through to the
            # Ollama localhost branch for provider=vilao, producing a WinError 10061
            # connection-refused when no local Ollama is running. Reuses the same
            # optional safety-bypass hints the Brain sends to Vilao.
            import os
            extra_body = None
            if (os.getenv("VILAO_SAFETY_BYPASS", "false").lower() in ("true", "1", "yes")
                    or os.getenv("SAFETY_OPEN", "true").lower() in ("true", "1", "yes")):
                extra_body = {"safe_mode": False, "safety": False, "content_filter": False}
            llm_kwargs = {
                "model": WORKER_MODEL,
                "api_key": VILAO_API_KEY,
                "base_url": VILAO_URL,
                "temperature": WORKER_TEMPERATURE,
                "timeout": LLM_REQUEST_TIMEOUT,
            }
            if extra_body:
                llm_kwargs["extra_body"] = extra_body
            self._llm = ChatOpenAI(**llm_kwargs)
            log.system(f"Worker initialized: {WORKER_MODEL} (Vilao, safety-bypass={bool(extra_body)})")
        elif WORKER_PROVIDER.lower() == "custom":
            # Any single OpenAI-compatible endpoint — see brain.py's "custom" branch
            # for the rationale (2-var .env swap instead of a new branch per provider).
            self._llm = ChatOpenAI(
                model=WORKER_MODEL,
                api_key=API_KEY,
                base_url=BASE_URL or None,
                temperature=WORKER_TEMPERATURE,
                timeout=LLM_REQUEST_TIMEOUT,
            )
            log.system(f"Worker initialized: {WORKER_MODEL} (custom/OpenAI-compatible)")
        elif WORKER_PROVIDER.lower() == "gemini":
            from langchain_google_genai import ChatGoogleGenerativeAI
            from ..config import GEMINI_API_KEY
            self._llm = ChatGoogleGenerativeAI(
                model=WORKER_MODEL,
                google_api_key=GEMINI_API_KEY,
                temperature=WORKER_TEMPERATURE,
                timeout=LLM_REQUEST_TIMEOUT,
            )
            log.system(f"Worker initialized: {WORKER_MODEL} (Gemini)")
        else:
            self._llm = ChatOllama(
                model=WORKER_MODEL,
                base_url=OLLAMA_BASE_URL,
                temperature=WORKER_TEMPERATURE,
                num_ctx=WORKER_NUM_CTX,
                timeout=LLM_REQUEST_TIMEOUT,
            )
            log.system(f"Worker initialized: {WORKER_MODEL} @ {OLLAMA_BASE_URL}")

    # Bonus: Worker gets retry protection for GPU timeouts and connection drops
    @retry(
        stop=stop_after_attempt(RETRY_MAX_ATTEMPTS),
        wait=wait_exponential(multiplier=RETRY_INITIAL_WAIT, max=RETRY_MAX_WAIT),
        retry=retry_if_exception_type(WORKER_TRANSIENT_ERRORS),
        before_sleep=_log_worker_retry,
    )
    def generate(self, task: str, context: str = "") -> str:
        """Generate content for a single task. Returns raw output string."""
        log.worker(f"Generating: \"{task[:70]}{'...' if len(task) > 70 else ''}\"")

        prompt = task
        if context:
            prompt = f"Context from previous steps:\n{context}\n\nTask:\n{task}"

        messages = [
            SystemMessage(content=WORKER_SYSTEM_PROMPT),
            HumanMessage(content=prompt),
        ]

        response = self._llm.invoke(messages)
        if self.on_call:
            try:
                self.on_call(WORKER_MODEL, extract_usage(response))
            except Exception:
                pass
        content = response.content.strip()

        # Issue 3 fix: Robust markdown stripping
        content = _strip_markdown_fences(content)

        # A provider returning an empty string is NOT an exception, so the @retry
        # decorator above never sees it and never retries — found live: this reached
        # the Master as the generic "core formatting disrupted." fallback with zero
        # diagnostic value. One immediate retry here covers the common transient case
        # (a content-filter flake, a truncated stream) without the cost of a full
        # backoff cycle; if it's STILL empty, let the caller see that honestly instead
        # of silently returning "" a second time.
        if not content:
            log.worker("Empty response — retrying once before giving up")
            response = self._llm.invoke(messages)
            if self.on_call:
                try:
                    self.on_call(WORKER_MODEL, extract_usage(response))
                except Exception:
                    pass
            content = _strip_markdown_fences(response.content.strip())

        log.worker(f"Generated {len(content)} chars")
        return content
