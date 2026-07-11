"""Estimated LLM cost — turns token counts into a USD estimate.

Prices are ESTIMATES in USD per 1,000,000 tokens and WILL drift over time; treat the
output as a ballpark for spotting cost regressions, not as a bill. This module is the
single source of truth for pricing so the live vitals feed and the offline
scripts/cost_report.py agree on the numbers.

Override WITHOUT touching code by dropping a JSON file at
``ciel_data/model_pricing.json`` (gitignored runtime dir):

    { "deepseek-chat": {"input": 0.27, "output": 1.10},
      "alic/qwen3.7-max": {"input": 0.5, "output": 1.5} }

Unknown models fall back to zero cost — call/token counts are still tracked, you just
add the price when you know it, so a new provider never breaks cost tracking.
"""
import json
from pathlib import Path

_BASE_DIR = Path(__file__).resolve().parent.parent
_PRICING_OVERRIDE_FILE = _BASE_DIR / "ciel_data" / "model_pricing.json"

# USD per 1,000,000 tokens. Best-effort public list prices as of mid-2026 — EDIT here
# or override via the JSON file above to match your actual plan. Vilao is a custom
# provider with no public price, so it defaults to 0 (set it to your real rate).
DEFAULT_PRICING = {
    "deepseek-chat":    {"input": 0.27, "output": 1.10},
    "deepseek":         {"input": 0.27, "output": 1.10},
    "alic/qwen3.7-max": {"input": 0.0,  "output": 0.0},   # Vilao — set to your plan
    "qwen":             {"input": 0.0,  "output": 0.0},
    "gemini-2.5-pro":   {"input": 1.25, "output": 10.0},
    "gemini-2.5-flash": {"input": 0.30, "output": 2.50},
    "gemini":           {"input": 0.30, "output": 2.50},
}

_ZERO = {"input": 0.0, "output": 0.0}
_override_cache = None


def _load_overrides() -> dict:
    """Read ciel_data/model_pricing.json once (cached). Never raises."""
    global _override_cache
    if _override_cache is not None:
        return _override_cache
    data = {}
    try:
        if _PRICING_OVERRIDE_FILE.exists():
            raw = json.loads(_PRICING_OVERRIDE_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = raw
    except Exception:
        data = {}
    _override_cache = data
    return data


def price_for(model: str) -> dict:
    """Return {"input","output"} USD-per-1M for a model id.

    Match order: exact (case-insensitive) → longest pricing key that appears as a
    substring of the model id (so "op/deepseek/deepseek-v4-pro" matches "deepseek").
    Falls back to zero when nothing matches.
    """
    if not model:
        return dict(_ZERO)
    m = model.strip().lower()
    merged = {**DEFAULT_PRICING, **_load_overrides()}
    # Exact match wins.
    for key, price in merged.items():
        if key.lower() == m:
            return price
    # Otherwise the longest key that is a substring of the model id.
    best_key = None
    for key in merged:
        if key.lower() in m and (best_key is None or len(key) > len(best_key)):
            best_key = key
    return merged[best_key] if best_key else dict(_ZERO)


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimated USD cost for one call. Zero for unknown models / zero tokens."""
    p = price_for(model)
    inp = (int(input_tokens or 0) / 1_000_000) * float(p.get("input", 0.0))
    out = (int(output_tokens or 0) / 1_000_000) * float(p.get("output", 0.0))
    return round(inp + out, 6)
