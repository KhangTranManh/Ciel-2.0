"""Deterministic delivery trigger for native calendar event alerts."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .notifier import Notification, NOTIFY
from .planner_store import PlannerStore
from .triggers import Trigger

KEY_PREFIX = "calendar_event"


def calendar_alert_key(event_id: int, schedule_version: int, offset_minutes: int) -> str:
    return f"{KEY_PREFIX}:{int(event_id)}:{int(schedule_version)}:{int(offset_minutes)}"


def _display_start(event: dict) -> str:
    zone_name = str(event.get("timezone") or "UTC")
    if event.get("all_day"):
        day = datetime.fromisoformat(str(event["start_date"]))
        return f"{day.strftime('%d/%m/%Y')} (cả ngày, {zone_name})"
    start = datetime.fromisoformat(str(event["start_at_utc"]).replace("Z", "+00:00"))
    try:
        start = start.astimezone(ZoneInfo(zone_name))
    except Exception:
        zone_name = "UTC"
        start = start.astimezone(timezone.utc)
    return f"{start.strftime('%H:%M %d/%m/%Y')} ({zone_name})"


def _lead(offset: int) -> str:
    if offset == 0:
        return "sự kiện bắt đầu ngay bây giờ"
    if offset % 1440 == 0:
        return f"còn {offset // 1440} ngày nữa"
    if offset % 60 == 0:
        return f"còn {offset // 60} giờ nữa"
    return f"còn {offset} phút nữa"


def make_calendar_due_check(store: PlannerStore, *, catchup_minutes: int):
    """One alert per poll (the engine delivers one note per check); polls run every
    10 seconds so simultaneous alerts drain well inside the >= 5 minute window.
    Earliest undelivered alert whose fire time is inside the catch-up window.

    Silent (None) before the fire time, after the window, for cancelled events, and
    for alerts already delivered under the current schedule version.
    """
    def check(now: float):
        now_utc = datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds")
        due = store.list_due_calendar_alerts(now_utc, catchup_minutes=catchup_minutes, limit=10)
        event = None
        for candidate in due:
            # Revalidate right before handing to the Notifier: a cancel/reschedule that
            # landed after the list must not send an identity from the old schedule.
            if store.calendar_alert_is_current(
                candidate["id"], int(candidate["alert_offset"]), int(candidate["schedule_version"])
            ):
                event = candidate
                break
        if event is None:
            return None
        offset = int(event["alert_offset"])
        lines = [str(event["title"]), f"Bắt đầu: {_display_start(event)}"]
        if event.get("location"):
            lines.append(f"Địa điểm: {event['location']}")
        return Notification(
            key=calendar_alert_key(event["id"], event["schedule_version"], offset),
            title=f"Sắp tới lịch: {_lead(offset)}",
            detail="\n".join(lines),
            action=f"Báo Ciel nếu muốn dời hoặc hủy sự kiện #{event['id']}.",
            urgency=NOTIFY,
            trigger="calendar_event_due",
            created_at=now,
        )
    return check


def make_calendar_outcome_handler(store: PlannerStore):
    """Mark delivered only after a real send or a persisted prior send (cooldown).

    Budget/mute/no-live-channel outcomes (digest) and provider errors leave the alert
    pending so it is retried until the catch-up window closes.
    """
    def handle(note: Notification, outcome, now: float) -> None:
        delivered = outcome.status == "delivered"
        recovered = (
            outcome.status == "suppressed"
            and str(outcome.reason or "").startswith("cooldown ")
        )
        if not (delivered or recovered):
            return
        _, event_id, version, offset = note.key.split(":")
        stamp = datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds")
        store.mark_calendar_alert_delivered(int(event_id), int(offset), int(version), stamp)
    return handle


def build_calendar_triggers(
    *,
    enabled_names=(),
    planner_db: str | Path,
    catchup_minutes: int = 30,
) -> list[Trigger]:
    if "calendar_event_due" not in set(enabled_names or ()):
        return []
    catchup_minutes = max(5, min(int(catchup_minutes), 1440))
    store = PlannerStore(planner_db)
    return [Trigger(
        name="calendar_event_due",
        check=make_calendar_due_check(store, catchup_minutes=catchup_minutes),
        every_seconds=10.0,
        cooldown_seconds=366 * 86400.0,
        on_outcome=make_calendar_outcome_handler(store),
    )]
