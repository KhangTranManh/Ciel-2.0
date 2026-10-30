"""Lane selection is deterministic and consumes the existing Brain decision.

A model lane is only an escalation hint. It cannot make writes, unknown tools,
dependencies, or background work enter the single read-only path. We deliberately
do not guess argument values from natural language.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping

from core.continuation import ContinuationPolicy
from .models import Lane, LaneDecision


# Dedicated small whitelist: permission AUTO includes some mutations and must not
# be mistaken for a read-only declaration. Unknown tools go through the harness.
SINGLE_READ_TOOLS = frozenset({
    "get_weather", "calculate", "get_current_time", "get_fact", "list_todos",
    "read_file", "get_file_info", "list_workspace", "grep_in_workspace",
    "git_status", "git_diff", "git_list_repos", "list_monthly_goals",
    "list_weekly_plan", "list_reminders", "get_market_price", "get_crypto_stats",
    "analyze_crypto_technical", "search_gmail", "get_gmail_message",
    "get_gmail_thread", "stealth_search", "smart_scrape", "read_document",
})


def _fold(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text or "").casefold().replace("đ", "d")
    return "".join(c for c in normalized if not unicodedata.combining(c))


_COMPLEX = re.compile(
    r"\b(?:then|afterwards|after that|research|investigate|debug|refactor|implement|"
    r"compare sources|multiple sources|step by step|roi|sau do|nghien cuu|"
    r"dieu tra|sua loi|nhieu nguon|tung buoc)\b", re.I)
_REFERENCE = re.compile(r"\{{1,2}\s*(?:prev|step[_ ]?\d+)(?:\.output)?\s*\}{1,2}", re.I)
_GREETING = re.compile(
    r"(?:hi|hello|hey|hello ciel|hi ciel|xin chao|chao ciel|chao|cam on|thanks|thank you)"
    r"[\s!.]*", re.I)


def fast_preflight(user_input: str, *, has_pending: bool = False,
                   has_open_thread: bool = False, unattended: bool = False) -> dict | None:
    """Skip Brain for exact standalone greetings only. Never skip turn bookkeeping."""
    if has_pending or has_open_thread or unattended:
        return None
    if _GREETING.fullmatch(_fold(user_input).strip()):
        return {"action": "chat", "lane": Lane.FAST.value}
    return None


def classify(user_input: str, decision: Mapping, *, known_tools=(), risky_tools=(),
             unattended: bool = False, failed_prior: bool = False) -> LaneDecision:
    if unattended:
        return LaneDecision(Lane.HARNESS, "background_work")
    if failed_prior:
        return LaneDecision(Lane.HARNESS, "failed_step_recovery")
    action = decision.get("action", "chat")
    if action in {"multi_tool", "code"}:
        return LaneDecision(Lane.HARNESS, "multi_step_or_code")
    if decision.get("lane") == Lane.HARNESS.value or decision.get("needs_followup"):
        return LaneDecision(Lane.HARNESS, "planner_requested_loop")
    if (ContinuationPolicy.request_is_conditional(user_input)
            or ContinuationPolicy.request_is_distributive(user_input)
            or _COMPLEX.search(_fold(user_input))):
        return LaneDecision(Lane.HARNESS, "dependent_or_complex_request")
    if action == "chat":
        return LaneDecision(Lane.FAST, "conversation")
    name = decision.get("tool_name")
    args = decision.get("tool_args", {})
    if action != "tool" or not isinstance(args, dict):
        return LaneDecision(Lane.HARNESS, "invalid_or_unknown_action")
    if name not in set(known_tools):
        return LaneDecision(Lane.HARNESS, "tool_unavailable")
    if name in set(risky_tools) or name not in SINGLE_READ_TOOLS:
        return LaneDecision(Lane.HARNESS, "mutation_or_unknown_capability")
    if _REFERENCE.search(str(args)):
        return LaneDecision(Lane.HARNESS, "step_dependency")
    return LaneDecision(Lane.SINGLE, "one_read_only_tool")
