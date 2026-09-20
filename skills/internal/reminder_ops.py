"""Scheduled reminder tools backed by ``ciel_data/planner.db``."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from langchain_core.tools import StructuredTool

from agent_system import config
from core.planner_store import PlannerStore, normalize_reminder_due
from skills._result import make_result


REMINDER_PROMPT = """
[REMINDERS]
Reminders are one-time notifications delivered by the deterministic scheduler.
Use `add_reminder` when the user asks to be alerted at a specific time or after a
delay. Use `add_todo` only for an unscheduled checklist item.

For relative requests such as "in 90 minutes", pass `delay_minutes=90` and leave
`due_at` empty. For an absolute time, pass ISO-8601 `due_at` such as
`2026-09-05T18:30` and the user's IANA timezone. Do not invent an absolute time when
the request is ambiguous. Put the reminder text in `title`; `message` is accepted as a
compatibility alias. `list_reminders` shows pending reminders and `cancel_reminder`
cancels one by ID.
"""


def _store() -> PlannerStore:
    return PlannerStore(config.PLANNER_DB_PATH)


def _timezone_name(value: str = "") -> str:
    return str(value or "").strip() or config.PLANNER_TIMEZONE


def _local_due(reminder: dict) -> str:
    due = datetime.fromisoformat(str(reminder["due_at_utc"]).replace("Z", "+00:00"))
    try:
        due = due.astimezone(ZoneInfo(str(reminder["timezone"])))
    except Exception:
        due = due.astimezone(timezone.utc)
    return due.isoformat(timespec="minutes")


def _line(reminder: dict) -> str:
    return (
        f"#{reminder['id']} [{reminder['status']}] {_local_due(reminder)}: "
        f"{reminder['title']}"
    )


def _trigger_enabled() -> bool:
    return bool(config.PROACTIVE_ENABLED and "reminder_due" in config.PROACTIVE_TRIGGERS)


def get_reminder_tools() -> dict:
    def add_reminder(
        title: str = "",
        due_at: str = "",
        delay_minutes: int = 0,
        timezone_name: str = "",
        message: str = "",
    ) -> dict:
        """Schedule one notification by absolute local time or a minute delay."""
        try:
            if not _trigger_enabled():
                return make_result(
                    False,
                    code="REMINDER_TRIGGER_DISABLED",
                    message=(
                        "Reminder delivery is disabled. Enable PROACTIVE_ENABLED and add "
                        "reminder_due to PROACTIVE_TRIGGERS before scheduling reminders."
                    ),
                    tool_name="add_reminder",
                )
            title = str(title or "").strip()
            message = str(message or "").strip()
            if title and message and title.casefold() != message.casefold():
                raise ValueError("title and message disagree; provide only one reminder text")
            title = title or message
            if not title:
                raise ValueError("provide reminder text in title")
            due_at = str(due_at or "").strip()
            delay_minutes = int(delay_minutes or 0)
            if due_at and delay_minutes:
                raise ValueError("use either due_at or delay_minutes, not both")
            if not due_at and delay_minutes <= 0:
                raise ValueError("provide due_at or a positive delay_minutes")
            if delay_minutes > 525_600:
                raise ValueError("delay_minutes cannot exceed one year")

            zone_name = _timezone_name(timezone_name)
            now = datetime.now(timezone.utc)
            if delay_minutes:
                due_at = (now + timedelta(minutes=delay_minutes)).isoformat(timespec="seconds")
            normalized_due, _ = normalize_reminder_due(due_at, zone_name)
            if datetime.fromisoformat(normalized_due) <= now:
                raise ValueError("reminder time must be in the future")

            reminder, created = _store().add_reminder(
                title, normalized_due, timezone_name=zone_name
            )
            verb = "Scheduled" if created else "Already exists"
            return make_result(
                True,
                data={
                    "message": f"{verb}: reminder {_line(reminder)}.",
                    "reminder": reminder,
                    "created": created,
                },
                tool_name="add_reminder",
            )
        except Exception as exc:
            return make_result(False, code="REMINDER_ERROR", message=str(exc),
                               tool_name="add_reminder")

    def list_reminders(include_closed: bool = False) -> dict:
        """List pending reminders, or include delivered/cancelled history."""
        try:
            reminders = _store().list_reminders(include_closed=include_closed)
            lines = ["Reminders:"] + [_line(item) for item in reminders]
            if not reminders:
                lines.append("(no matching reminders)")
            return make_result(True, data={"message": "\n".join(lines),
                                           "reminders": reminders},
                               tool_name="list_reminders")
        except Exception as exc:
            return make_result(False, code="REMINDER_ERROR", message=str(exc),
                               tool_name="list_reminders")

    def cancel_reminder(reminder_id: int) -> dict:
        """Cancel one pending reminder by numeric ID."""
        try:
            reminder = _store().cancel_reminder(reminder_id)
            return make_result(True, data={
                "message": f"Cancelled: reminder {_line(reminder)}.",
                "reminder": reminder,
            }, tool_name="cancel_reminder")
        except Exception as exc:
            return make_result(False, code="REMINDER_ERROR", message=str(exc),
                               tool_name="cancel_reminder")

    tools = [
        StructuredTool.from_function(
            add_reminder,
            name="add_reminder",
            description=(
                "Schedule a one-time notification. For 'in N minutes', set delay_minutes=N. "
                "Put reminder text in title (message is accepted as a compatibility alias). "
                "For an absolute time, set due_at ISO-8601 and optional timezone_name."
            ),
        ),
        StructuredTool.from_function(
            list_reminders,
            name="list_reminders",
            description="List pending reminders, optionally including delivered/cancelled history.",
        ),
        StructuredTool.from_function(
            cancel_reminder,
            name="cancel_reminder",
            description="Cancel one pending reminder by numeric reminder_id.",
        ),
    ]
    return {"tools": tools, "prompt": REMINDER_PROMPT,
            "parallel_safe": ["list_reminders"]}
