"""Deterministic outcome checks. No model is called to approve obvious success."""
from __future__ import annotations

import re


_FAILURE = re.compile(
    r"^\s*(?:\[(?:TOOL_ERROR|EXECUTION_ERROR|INVALID_ARGS|TOOL_NOT_FOUND|PLAN_INVALID|"
    r"SKIPPED_DEPENDENCY|RUN_LIMIT)|Error[:\s]|L\u1ed7i[:\s]|Sorry,|Search API Error:|"
    r"Scrape Error:|Failed to |File not found|No such file|Tool .{0,80}not found)", re.I)


def step_outcome(text: str, *, pending: bool = False) -> str:
    result = (text or "").strip()
    if result.startswith("[CANCELLED]"):
        return "cancelled"
    if pending:
        return "waiting_for_approval"
    if not result or _FAILURE.match(result):
        return "failed"
    return "completed"
