"""Read-only tools for Ciel's provider-independent native calendar."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from langchain_core.tools import StructuredTool

from agent_system import config
from core.planner_store import PlannerStore
from skills._result import make_result


NATIVE_CALENDAR_PROMPT = """
[NATIVE CALENDAR]
The native calendar is private Ciel-owned schedule data, independent of Google.
Use `list_native_calendar_events` for a bounded time range and
`get_native_calendar_event` only when the user explicitly needs one event's details.
These Phase-1 tools are read-only; do not claim an event was created or changed.
"""


def _store() -> PlannerStore:
    return PlannerStore(config.PLANNER_DB_PATH)


def _timezone_name(value: str = "") -> str:
    return str(value or "").strip() or config.PLANNER_TIMEZONE


def _public_event(event: dict) -> dict:
    return {
        "id": int(event["id"]),
        "title": str(event["title"]),
        "start_at_utc": event.get("start_at_utc"),
        "end_at_utc": event.get("end_at_utc"),
        "timezone": str(event["timezone"]),
        "all_day": bool(event["all_day"]),
        "start_date": event.get("start_date"),
        "end_date_exclusive": event.get("end_date_exclusive"),
        "status": str(event["status"]),
        "schedule_version": int(event["schedule_version"]),
        "alert_offsets": list(event.get("alert_offsets") or []),
    }


def _when(event: dict) -> str:
    if event.get("all_day"):
        return f"{event['start_date']}..{event['end_date_exclusive']} (all day)"
    start = datetime.fromisoformat(str(event["start_at_utc"]))
    end = datetime.fromisoformat(str(event["end_at_utc"]))
    try:
        zone = ZoneInfo(str(event["timezone"]))
    except Exception:
        zone = timezone.utc
    return (
        f"{start.astimezone(zone).isoformat(timespec='minutes')}.."
        f"{end.astimezone(zone).isoformat(timespec='minutes')}"
    )


def get_native_calendar_tools() -> dict:
    def list_native_calendar_events(
        range_start: str = "",
        range_end: str = "",
        timezone_name: str = "",
        include_cancelled: bool = False,
        limit: int = 20,
    ) -> dict:
        """List bounded native events overlapping an ISO-8601 time range."""
        try:
            zone_name = _timezone_name(timezone_name)
            now = datetime.now(timezone.utc)
            start = str(range_start or "").strip() or now.isoformat(timespec="seconds")
            end = str(range_end or "").strip() or (
                now + timedelta(days=7)
            ).isoformat(timespec="seconds")
            events = _store().list_calendar_events(
                start,
                end,
                timezone_name=zone_name,
                include_cancelled=bool(include_cancelled),
                limit=max(1, min(int(limit), 50)),
            )
            public = [_public_event(event) for event in events]
            lines = ["Native calendar:"] + [
                f"#{event['id']} [{event['status']}] {_when(event)}: {event['title']}"
                for event in public
            ]
            if not public:
                lines.append("(no matching native events)")
            return make_result(
                True,
                data={"message": "\n".join(lines), "events": public},
                tool_name="list_native_calendar_events",
            )
        except Exception as exc:
            return make_result(
                False,
                code="NATIVE_CALENDAR_READ_ERROR",
                message=str(exc),
                tool_name="list_native_calendar_events",
            )

    def get_native_calendar_event(event_id: int) -> dict:
        """Read one native event by numeric ID, including its private detail fields."""
        try:
            event = _store().get_calendar_event(int(event_id))
            if event is None:
                return make_result(
                    False,
                    code="NATIVE_CALENDAR_NOT_FOUND",
                    message=f"native calendar event #{int(event_id)} was not found",
                    tool_name="get_native_calendar_event",
                )
            return make_result(
                True,
                data={
                    "message": (
                        f"Native event #{event['id']} [{event['status']}] "
                        f"{_when(event)}: {event['title']}"
                    ),
                    "event": event,
                },
                tool_name="get_native_calendar_event",
            )
        except Exception as exc:
            return make_result(
                False,
                code="NATIVE_CALENDAR_READ_ERROR",
                message=str(exc),
                tool_name="get_native_calendar_event",
            )

    tools = [
        StructuredTool.from_function(
            list_native_calendar_events,
            name="list_native_calendar_events",
            description=(
                "List Ciel-owned native calendar events in a bounded range. Read-only; "
                "independent of Google. Optional range_start/range_end ISO-8601, "
                "timezone_name, include_cancelled, and limit."
            ),
        ),
        StructuredTool.from_function(
            get_native_calendar_event,
            name="get_native_calendar_event",
            description=(
                "Read one Ciel-owned native calendar event by numeric event_id, including "
                "notes/location. Read-only; use only for explicit event detail."
            ),
        ),
    ]
    names = [tool.name for tool in tools]
    return {"tools": tools, "prompt": NATIVE_CALENDAR_PROMPT, "parallel_safe": names}
