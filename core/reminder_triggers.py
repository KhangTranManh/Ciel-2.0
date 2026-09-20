"""Deterministic delivery trigger for one-time reminders."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .notifier import Notification, NOTIFY
from .planner_store import PlannerStore
from .triggers import Trigger


def _display_due(reminder: dict, fallback_timezone: str) -> str:
    due = datetime.fromisoformat(str(reminder["due_at_utc"]).replace("Z", "+00:00"))
    zone_name = str(reminder.get("timezone") or fallback_timezone)
    try:
        due = due.astimezone(ZoneInfo(zone_name))
    except Exception:
        zone_name = "UTC"
        due = due.astimezone(timezone.utc)
    return f"{due.strftime('%H:%M %d/%m/%Y')} ({zone_name})"


def make_due_reminder_check(store: PlannerStore, *, timezone_name: str):
    """Return the earliest pending reminder whose UTC deadline has arrived."""
    def check(now: float):
        now_utc = datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds")
        due = store.list_due_reminders(now_utc, limit=1)
        if not due:
            return None
        reminder = due[0]
        return Notification(
            key=f"reminder:{reminder['id']}",
            title="Nhắc việc",
            detail=f"{reminder['title']}\nĐến hạn: {_display_due(reminder, timezone_name)}",
            action=f"Báo Ciel nếu muốn đặt lại hoặc hủy reminder #{reminder['id']}.",
            urgency=NOTIFY,
            trigger="reminder_due",
            created_at=now,
        )
    return check


def make_reminder_outcome_handler(store: PlannerStore):
    """Persist completion only after delivery or persisted cooldown proves a prior send."""
    def handle(note: Notification, outcome, now: float) -> None:
        delivered = outcome.status == "delivered"
        recovered_after_restart = (
            outcome.status == "suppressed"
            and str(outcome.reason or "").startswith("cooldown ")
        )
        if not (delivered or recovered_after_restart):
            return
        reminder_id = int(note.key.split(":", 1)[1])
        notified_at = datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds")
        store.mark_reminder_delivered(reminder_id, notified_at)
    return handle


def build_reminder_triggers(
    *,
    enabled_names=(),
    planner_db: str | Path,
    timezone_name: str,
) -> list[Trigger]:
    if "reminder_due" not in set(enabled_names or ()):
        return []
    store = PlannerStore(planner_db)
    return [Trigger(
        name="reminder_due",
        check=make_due_reminder_check(store, timezone_name=timezone_name),
        every_seconds=60.0,
        cooldown_seconds=366 * 86400.0,
        on_outcome=make_reminder_outcome_handler(store),
    )]
