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
)
from ..utils.logger import log


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
        # callback logging "1 Worker call happened" into thoughts.log. Covers every
        # caller of generate() for free — direct formatting calls in llm_connector.py
        # AND recovery_manager.py's healing/syntax-check calls, since they all share
        # this same Worker instance. None by default so Worker stays usable standalone.
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
                self.on_call(WORKER_MODEL)
            except Exception:
                pass
        content = response.content.strip()

        # Issue 3 fix: Robust markdown stripping
        content = _strip_markdown_fences(content)

        log.worker(f"Generated {len(content)} chars")
        return content
