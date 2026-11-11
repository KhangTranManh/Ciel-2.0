"""Tools for Ciel's provider-independent native calendar."""
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
Use create/update/cancel only for Ciel-owned native events, never Google Calendar.
Before creating a timed event whose end time or location the user did not give, call
`suggest_native_calendar_defaults` and, if it has enough history, put the suggested
values explicitly into the create call so confirmation shows them. Never guess the
start date/time; if the suggestion has no history, ask the user.
Native reads are automatic. Every mutation follows the normal confirmation path.
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

    def suggest_native_calendar_defaults(title: str) -> dict:
        """Suggest duration/location from past events with the same title."""
        try:
            envelope = _store().suggest_calendar_defaults(title)
            return make_result(
                True,
                data={"message": f"{envelope['sample_count']} similar past event(s)",
                      "suggestion": envelope},
                tool_name="suggest_native_calendar_defaults",
            )
        except Exception as exc:
            return make_result(False, code="NATIVE_CALENDAR_READ_ERROR", message=str(exc),
                               tool_name="suggest_native_calendar_defaults")

    def create_native_calendar_event(
        title: str,
        start_at: str = "",
        end_at: str = "",
        timezone_name: str = "",
        all_day: bool = False,
        start_date: str = "",
        end_date_exclusive: str = "",
        location: str = "",
        notes: str = "",
    ) -> dict:
        """Create one Ciel-owned timed or all-day event."""
        try:
            event, created = _store().add_calendar_event(
                title,
                timezone_name=_timezone_name(timezone_name),
                start_at=start_at,
                end_at=end_at,
                all_day=all_day,
                start_date=start_date,
                end_date_exclusive=end_date_exclusive,
                location=location,
                notes=notes,
            )
            verb = "Created" if created else "Already exists"
            return make_result(
                True,
                data={
                    "message": f"{verb}: native event #{event['id']} {_when(event)}: {event['title']}",
                    "event": _public_event(event),
                    "created": created,
                },
                tool_name="create_native_calendar_event",
            )
        except Exception as exc:
            return make_result(False, code="NATIVE_CALENDAR_CREATE_ERROR", message=str(exc),
                               tool_name="create_native_calendar_event")

    def update_native_calendar_event(
        event_id: int,
        title: str | None = None,
        start_at: str | None = None,
        end_at: str | None = None,
        timezone_name: str | None = None,
        all_day: bool | None = None,
        start_date: str | None = None,
        end_date_exclusive: str | None = None,
        location: str | None = None,
        notes: str | None = None,
    ) -> dict:
        """Partially update one Ciel-owned event by verified numeric ID."""
        try:
            event = _store().update_calendar_event(
                event_id,
                title=title,
                start_at=start_at,
                end_at=end_at,
                timezone_name=timezone_name,
                all_day=all_day,
                start_date=start_date,
                end_date_exclusive=end_date_exclusive,
                location=location,
                notes=notes,
            )
            return make_result(
                True,
                data={
                    "message": f"Updated: native event #{event['id']} {_when(event)}: {event['title']}",
                    "event": _public_event(event),
                },
                tool_name="update_native_calendar_event",
            )
        except Exception as exc:
            return make_result(False, code="NATIVE_CALENDAR_UPDATE_ERROR", message=str(exc),
                               tool_name="update_native_calendar_event")

    def cancel_native_calendar_event(event_id: int) -> dict:
        """Cancel one Ciel-owned event by verified numeric ID."""
        try:
            event = _store().cancel_calendar_event(event_id)
            return make_result(
                True,
                data={
                    "message": f"Cancelled: native event #{event['id']}: {event['title']}",
                    "event": _public_event(event),
                },
                tool_name="cancel_native_calendar_event",
            )
        except Exception as exc:
            return make_result(False, code="NATIVE_CALENDAR_CANCEL_ERROR", message=str(exc),
                               tool_name="cancel_native_calendar_event")
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
        StructuredTool.from_function(
            suggest_native_calendar_defaults,
            name="suggest_native_calendar_defaults",
            description=(
                "Read-only. Given an event title, return aggregated duration_minutes, "
                "location and timezone from past similar events, only when enough "
                "history exists. Use to fill end_at/location before create."
            ),
        ),
        StructuredTool.from_function(
            create_native_calendar_event,
            name="create_native_calendar_event",
            description=(
                "Create a Ciel-owned native calendar event. Timed events need ISO start_at/end_at; "
                "all-day events need start_date/end_date_exclusive. Requires confirmation."
            ),
        ),
        StructuredTool.from_function(
            update_native_calendar_event,
            name="update_native_calendar_event",
            description=(
                "Partially update a Ciel-owned native event by exact numeric event_id. "
                "Requires confirmation."
            ),
        ),
        StructuredTool.from_function(
            cancel_native_calendar_event,
            name="cancel_native_calendar_event",
            description=(
                "Cancel a Ciel-owned native event by exact numeric event_id. Requires confirmation."
            ),
        ),
    ]
    return {
        "tools": tools,
        "prompt": NATIVE_CALENDAR_PROMPT,
        "parallel_safe": [
            "list_native_calendar_events", "get_native_calendar_event",
            "suggest_native_calendar_defaults",
        ],
    }
