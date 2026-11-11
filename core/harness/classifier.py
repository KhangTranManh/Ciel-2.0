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
    "get_daily_brief", "analyze_crypto_technical", "search_gmail", "get_gmail_message",
    "get_gmail_thread", "list_calendar_events", "list_native_calendar_events",
    "get_native_calendar_event", "suggest_native_calendar_defaults", "recall_past_actions",
    "search_my_documents", "stealth_search", "smart_scrape",
    "read_document",
})


def _fold(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text or "").casefold().replace("đ", "d")
    return "".join(c for c in normalized if not unicodedata.combining(c))


_COMPLEX = re.compile(
    r"\b(?:then|afterwards|after that|research|investigate|debug|refactor|implement|"
    r"compare sources|multiple sources|step by step|sau do|nghien cuu|"
    r"dieu tra|sua loi|nhieu nguon|tung buoc)\b", re.I)
_REFERENCE = re.compile(r"\{{1,2}\s*(?:prev|step[_ ]?\d+)(?:\.output)?\s*\}{1,2}", re.I)
_GREETING = re.compile(
    r"(?:hi|hello|hey|hello ciel|hi ciel|xin chao|chao ciel|chao|cam on|thanks|thank you)"
    r"[\s!.]*", re.I)


# Conservative "pure small talk" detector. The ONLY cost of a false negative is one extra
# Brain call, while a false positive could answer a real request with chat, so every
# signal of intent (a verb Ciel can act on, a question, a number, a path/URL) vetoes it.
_INTENT = re.compile(
    r"\b(?:gui|mail|email|gmail|tim|kiem|check|tra|search|gia|ty gia|thoi tiet|tin tuc|"
    r"dat|lich|nhac|tao|viet|doc|mo|xoa|them|sua|file|chay|lay|cho t|cho toi|cho minh|"
    r"phan tich|danh gia|hay|lam|nho|quen|bao cao|tom tat|dich|tinh|mua|ban|nen|"
    r"bao nhieu|may gio|hom nay|hom qua|ngay mai|tuan|thang|nam|upload|tai|cai|"
    r"install|git|commit|push|code|script|list|show|xem|ghi|luu|goi y|giai thich|"
    r"la gi|tai sao|vi sao|sao|nao|dau|ai|bao gio|khi nao|the nao|ra sao|"
    r"telegram|todo|reminder|plan|goal|btc|bitcoin|crypto|vang|forex|co phieu|"
    r"anh|hinh|pdf|excel|server|docker|vps|api|key|token|"
    # continuations, confirmations, preference/instruction statements, names
    r"tiep|duoc|co the|dong y|di|lan sau|uu tien|tu nay|luon|dung|khoi|goi|ten|"
    r"scrape|kien truc|data|dao|keo|cho|ke|thu|nua|loi|bug|sai)\b")
_QUESTION_END = re.compile(r"\b(?:khong|ko|chua|k)\s*$")
_SMALL_TALK_MAX_CHARS = 50
_SMALL_TALK_MAX_WORDS = 10
_SMALL_TALK_VETO = re.compile(
    r"[0-9?/\@:#<>{}\[\]=_`]|http|www\.|\.(?:py|txt|md|json|png|jpg|pdf)\b")


_OFFER = re.compile(
    r"muon|co the|de minh|de toi|ban co|can minh|want me|would you|should i|shall i|"
    r"nhe\s*$|khong\s*$|\?")
_VI_MARK = re.compile(r"[ăâđêôơưáàảãạấầẩẫậắằẳẵặéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ]", re.I)


def looks_like_small_talk(user_input: str, last_assistant: str = "") -> bool:
    # A reply to a question/offer is a continuation, never small talk.
    if last_assistant and _OFFER.search(_fold(last_assistant)[-160:]):
        return False
    # Unaccented text is English, a command, or a bare "yes/continue": let Brain decide.
    if not _VI_MARK.search(user_input):
        return False
    text = _fold(user_input).strip()
    if not text or len(text) > _SMALL_TALK_MAX_CHARS or len(text.split()) > _SMALL_TALK_MAX_WORDS:
        return False
    if _SMALL_TALK_VETO.search(text) or "\n" in (user_input or "").strip():
        return False
    return not (_INTENT.search(text) or _QUESTION_END.search(text))


def fast_preflight(user_input: str, *, has_pending: bool = False,
                   has_open_thread: bool = False, unattended: bool = False,
                   last_assistant: str = "") -> dict | None:
    """Skip Brain for exact standalone greetings only. Never skip turn bookkeeping."""
    if has_pending or has_open_thread or unattended:
        return None
    if _GREETING.fullmatch(_fold(user_input).strip()):
        return {"action": "chat", "lane": Lane.FAST.value}
    if _small_talk_enabled() and looks_like_small_talk(user_input, last_assistant):
        return {"action": "chat", "lane": Lane.FAST.value}
    return None


def _small_talk_enabled() -> bool:
    try:
        from agent_system import config
        return bool(getattr(config, "FAST_SMALL_TALK_ENABLED", True))
    except Exception:
        return False


def classify(user_input: str, decision: Mapping, *, known_tools=(), risky_tools=(),
             unattended: bool = False, failed_prior: bool = False) -> LaneDecision:
    if unattended:
        return LaneDecision(Lane.HARNESS, "background_work")
    if failed_prior:
        return LaneDecision(Lane.HARNESS, "failed_step_recovery")
    action = decision.get("action")
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
