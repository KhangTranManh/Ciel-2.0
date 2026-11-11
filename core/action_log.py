"""action_log.py — turn successful side-effect tool calls into short action records.

Only a small allow-list of tools is recorded, and the record is built from the call
ARGUMENTS (never from tool output, email bodies, or file contents). Volatile subjects
(prices, markets, weather, news) keep the fact that something happened but never the
numbers, so an old value cannot be replayed as if it were current.

Writing happens on a daemon thread; a failure is swallowed, so recording can never slow
down or break a turn. It is a no-op unless ``KNOWLEDGE_INDEX_ENABLED`` is true.
"""
from __future__ import annotations

import re
import threading
from pathlib import Path

_VOLATILE_RE = re.compile(
    r"giá|vàng|btc|bitcoin|crypto|eth\b|forex|chứng khoán|cổ phiếu|thị trường|"
    r"thời tiết|tin tức|\bprice|\bgold|\bmarket|\bstock|\bweather|\bnews",
    re.IGNORECASE,
)
_DIGITS_RE = re.compile(r"\d[\d.,]*")
_SUMMARY_MAX = 240


def subject_key_for(text: str) -> str:
    """Stable key for recurring volatile reports; "" when the subject is not volatile."""
    t = (text or "").lower()
    if re.search(r"thời tiết|weather", t):
        return "weather_report"
    if re.search(r"tin tức|news", t):
        return "news_report"
    if _VOLATILE_RE.search(t):
        return "market_report"
    return ""


def _scrub(text: str, volatile: bool) -> str:
    text = " ".join(str(text or "").split())
    if volatile:
        text = _DIGITS_RE.sub("…", text)
    return text[:_SUMMARY_MAX]


def _clip(value, n=90) -> str:
    return " ".join(str(value or "").split())[:n]


def build_record(tool_name: str, args: dict) -> dict | None:
    """Return ``{action, summary, subject_key, target}`` or None when not recorded."""
    args = args if isinstance(args, dict) else {}
    key = ""
    if tool_name in ("send_gmail_message", "send_gmail_html_message"):
        subject = _clip(args.get("subject"))
        key = subject_key_for(subject)
        summary, target, action = f"Đã gửi email tới {_clip(args.get('to'), 60)}: {subject}", _clip(args.get("to"), 60), "send_email"
    elif tool_name in ("send_telegram", "send_telegram_document"):
        what = _clip(args.get("caption") or args.get("filename") or args.get("message"), 80)
        key = subject_key_for(what)
        summary, target, action = f"Đã gửi Telegram: {what}", "telegram", "send_telegram"
    elif tool_name in ("build_market_report_html", "build_analysis_report_html"):
        title = _clip(args.get("title") or args.get("topic") or "báo cáo")
        key = subject_key_for(title) or ("market_report" if tool_name == "build_market_report_html" else "")
        summary, target, action = f"Đã tạo báo cáo: {title}", "", "build_report"
    elif tool_name == "write_file":
        name = Path(str(args.get("filename") or args.get("path") or "")).name
        if not name:
            return None
        summary, target, action = f"Đã ghi file {name}", name, "write_file"
    elif tool_name == "create_native_calendar_event":
        title = _clip(args.get("title"))
        when = _clip(args.get("start_at") or args.get("start_date"), 25)
        summary, target, action = f"Đã đặt lịch: {title} ({when})", title, "calendar_create"
    elif tool_name == "cancel_native_calendar_event":
        summary, target, action = f"Đã hủy lịch #{_clip(args.get('event_id'), 12)}", "", "calendar_cancel"
    elif tool_name == "add_reminder":
        title = _clip(args.get("title") or args.get("message"))
        summary, target, action = f"Đã đặt nhắc: {title}", title, "reminder_add"
    else:
        return None
    volatile = bool(key)
    return {
        "action": action,
        "summary": _scrub(summary, volatile),
        "subject_key": key,
        "target": _scrub(target, volatile),
    }


def record_tool_success(tool_name: str, args: dict, *, index=None, enabled: bool | None = None,
                        background: bool = True) -> bool:
    """Record one successful call. Returns True when a write was scheduled/performed."""
    try:
        if enabled is None:
            from agent_system import config
            enabled = bool(getattr(config, "KNOWLEDGE_INDEX_ENABLED", False))
        if not enabled:
            return False
        record = build_record(tool_name, args)
        if record is None:
            return False

        def write():
            try:
                target = index
                if target is None:
                    from agent_system import config
                    from core.knowledge_index import KnowledgeIndex
                    target = _shared_index(KnowledgeIndex, config.KNOWLEDGE_INDEX_DIR)
                target.record_action(
                    record["summary"], action=record["action"],
                    subject_key=record["subject_key"], target=record["target"],
                )
            except Exception:
                pass

        if background:
            threading.Thread(target=write, name="action-log", daemon=True).start()
        else:
            write()
        return True
    except Exception:
        return False


_SHARED = {}
_SHARED_LOCK = threading.Lock()


def _shared_index(cls, directory):
    with _SHARED_LOCK:
        key = str(directory)
        if key not in _SHARED:
            _SHARED[key] = cls(directory)
        return _SHARED[key]
