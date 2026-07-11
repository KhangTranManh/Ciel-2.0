"""Token-usage extraction — one place that knows how to read token counts off a
LangChain chat response, across every provider Ciel uses (DeepSeek/Vilao via
ChatOpenAI, Gemini, Ollama).

Why a shared helper: Worker, Middleware, and the Router (Brain) all invoke an LLM
and all feed the same cost-tracking chokepoint (CielCore._log_thought). Keeping the
"where do the numbers live on the response object" knowledge in ONE function means a
provider quirk is fixed once, not in three places. Always returns a dict with the
same three int keys so callers never have to null-check.
"""
from typing import Any


def extract_usage(response: Any) -> dict:
    """Best-effort token usage from a LangChain chat response.

    Returns {"input": int, "output": int, "total": int}. Zeros when the provider
    did not report usage (never raises — cost tracking must never break a real call).
    """
    # Preferred: modern langchain-core AIMessage.usage_metadata (normalized across
    # providers). ChatOpenAI (DeepSeek/Vilao), Gemini, and Ollama all populate this.
    um = getattr(response, "usage_metadata", None)
    if isinstance(um, dict) and um:
        inp = int(um.get("input_tokens", 0) or 0)
        out = int(um.get("output_tokens", 0) or 0)
        tot = int(um.get("total_tokens", inp + out) or (inp + out))
        if inp or out or tot:
            return {"input": inp, "output": out, "total": tot}

    # Fallback: raw response_metadata.token_usage (OpenAI-style prompt/completion).
    rm = getattr(response, "response_metadata", None) or {}
    tu = rm.get("token_usage") or rm.get("usage") or {}
    if isinstance(tu, dict) and tu:
        inp = int(tu.get("prompt_tokens", 0) or 0)
        out = int(tu.get("completion_tokens", 0) or 0)
        tot = int(tu.get("total_tokens", inp + out) or (inp + out))
        return {"input": inp, "output": out, "total": tot}

    return {"input": 0, "output": 0, "total": 0}


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
    return f"model={model} in={inp} out={out} total={tot}"
