"""Token-usage extraction — one place that knows how to read token counts off a
LangChain chat response, across every provider Ciel uses (DeepSeek/Vilao via
ChatOpenAI, Gemini, Ollama).

Why a shared helper: Worker, Middleware, and the Router (Brain) all invoke an LLM
and all feed the same cost-tracking chokepoint (CielCore._log_thought). Keeping the
"where do the numbers live on the response object" knowledge in ONE function means a
provider quirk is fixed once, not in three places. Always returns a dict with the
same three int keys so callers never have to null-check.
"""
import time
from typing import Any, Callable


def extract_usage(response: Any) -> dict:
    """Best-effort token usage from a LangChain chat response.

    Returns {"input", "output", "total", "cached"} ints. Zeros when the provider
    did not report usage (never raises — cost tracking must never break a real call).
    ``cached`` is the prompt-cache hit count (DeepSeek prompt_cache_hit_tokens or
    OpenAI-style cached_tokens), 0 when absent.
    """
    try:
        return _extract_usage(response)
    except Exception:
        return {"input": 0, "output": 0, "total": 0, "cached": 0}


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _cached_tokens(response: Any) -> int:
    um = getattr(response, "usage_metadata", None)
    if isinstance(um, dict):
        details = um.get("input_token_details")
        if isinstance(details, dict) and _int(details.get("cache_read")):
            return _int(details.get("cache_read"))
    rm = getattr(response, "response_metadata", None) or {}
    tu = rm.get("token_usage") or rm.get("usage") or {}
    if isinstance(tu, dict):
        hit = _int(tu.get("prompt_cache_hit_tokens"))
        if hit:
            return hit
        ptd = tu.get("prompt_tokens_details")
        if isinstance(ptd, dict):
            return _int(ptd.get("cached_tokens"))
    return 0


def _extract_usage(response: Any) -> dict:
    cached = _cached_tokens(response)
    # Preferred: modern langchain-core AIMessage.usage_metadata (normalized across
    # providers). ChatOpenAI (DeepSeek/Vilao), Gemini, and Ollama all populate this.
    um = getattr(response, "usage_metadata", None)
    if isinstance(um, dict) and um:
        inp = _int(um.get("input_tokens"))
        out = _int(um.get("output_tokens"))
        tot = _int(um.get("total_tokens")) or (inp + out)
        if inp or out or tot:
            return {"input": inp, "output": out, "total": tot, "cached": cached}

    # Fallback: raw response_metadata.token_usage (OpenAI-style prompt/completion).
    rm = getattr(response, "response_metadata", None) or {}
    tu = rm.get("token_usage") or rm.get("usage") or {}
    if isinstance(tu, dict) and tu:
        inp = _int(tu.get("prompt_tokens"))
        out = _int(tu.get("completion_tokens"))
        tot = _int(tu.get("total_tokens")) or (inp + out)
        return {"input": inp, "output": out, "total": tot, "cached": cached}

    return {"input": 0, "output": 0, "total": 0, "cached": 0}


def format_usage(model: str, usage: dict) -> str:
    """Render the [LLM_CALL] log content line. Kept here so the WRITE format and the
    READ format (scripts/cost_report.py, CielCore._log_thought) stay in lockstep.

    Format: ``model=<name> in=<n> out=<n> total=<n>``
    The ``model=`` token stays first and space-delimited so the existing
    ``re.search(r"model=(\\S+)")`` consumers keep working unchanged.
    """
    usage = usage or {}
    inp = int(usage.get("input", 0) or 0)
    out = int(usage.get("output", 0) or 0)
    tot = int(usage.get("total", inp + out) or (inp + out))
    cached = int(usage.get("cached", 0) or 0)
    line = f"model={model} in={inp} out={out} total={tot}"
    return f"{line} cached={cached}" if cached > 0 else line


def format_latency(component: str, model: str, duration_ms: int, outcome: str) -> str:
    """Render secret-free latency metadata in a separate audit event."""
    clean_component = "_".join(str(component or "unknown").split())[:48]
    clean_model = "_".join(str(model or "unknown").split())[:120]
    clean_outcome = "success" if outcome == "success" else "error"
    return (
        f"component={clean_component} model={clean_model} "
        f"duration_ms={max(0, int(duration_ms))} outcome={clean_outcome}"
    )


def invoke_with_latency(
    client: Any,
    messages: Any,
    *,
    component: str,
    model: str,
    on_latency: Callable[[str, str, int, str], None] | None = None,
):
    """Invoke one provider attempt and report timing without changing its result/error."""
    started = time.perf_counter()
    outcome = "error"
    try:
        response = client.invoke(messages)
        outcome = "success"
        return response
    finally:
        duration_ms = int((time.perf_counter() - started) * 1000)
        if on_latency is not None:
            try:
                on_latency(component, model, duration_ms, outcome)
            except Exception:
                # Telemetry must never turn a successful provider call into a failed turn.
                pass


def estimate_tokens(text: Any) -> int:
    """Cheap token estimate (len/4). Never raises; counts only, never keeps content."""
    try:
        return (len(text) + 3) // 4 if isinstance(text, str) else (len(str(text or "")) + 3) // 4
    except Exception:
        return 0


def format_blocks(component: str, blocks: dict, provider_in: int = 0, cached: int = 0) -> str:
    """Render the [PERF] [PROMPT_BLOCKS] line: block NAMES and estimated token counts
    only — never prompt content. ``est_total`` is the sum of the blocks; ``in`` is the
    provider-reported input tokens (0 if unreported).
    """
    clean = "_".join(str(component or "unknown").split())[:48]
    parts = []
    total = 0
    for name, text in (blocks or {}).items():
        n = text if isinstance(text, int) else estimate_tokens(text)
        total += n
        parts.append(f"{'_'.join(str(name).split())[:32]}={n}")
    return f"component={clean} {' '.join(parts)} est_total={total} in={int(provider_in or 0)} cache={int(cached or 0)}"


def log_blocks(log_fn: Callable[[str, str, str], None] | None, component: str,
               blocks: dict, response: Any = None) -> None:
    """Best-effort prompt-block telemetry; telemetry must never break a real call."""
    if log_fn is None:
        return
    try:
        usage = extract_usage(response) if response is not None else {}
        log_fn("PERF", "PROMPT_BLOCKS",
               format_blocks(component, blocks, usage.get("input", 0), usage.get("cached", 0)))
    except Exception:
        pass
