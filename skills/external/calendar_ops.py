"""Separate Google Calendar tool pack with read/write permission boundaries."""
from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from langchain_core.tools import StructuredTool

from agent_system import config
from skills._result import make_result


CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar"
BASE_DIR = Path(__file__).resolve().parent.parent.parent


def _clean(value: Any, limit: int = 300) -> str:
    return " ".join(str(value or "").split())[:limit]


def _token_path(base_dir: str | Path | None = None) -> Path:
    root = Path(base_dir) if base_dir is not None else BASE_DIR
    configured = Path(config.CALENDAR_TOKEN_PATH)
    if configured.is_absolute() or base_dir is None:
        return configured
    return root / configured


def _calendar_service(base_dir: str | Path | None = None):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    token_path = _token_path(base_dir)
    if not token_path.exists():
        raise RuntimeError("calendar_token_missing")
    credentials = Credentials.from_authorized_user_file(str(token_path), [CALENDAR_SCOPE])
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        token_path.write_text(credentials.to_json(), encoding="utf-8")
    if not credentials.valid:
        raise RuntimeError("calendar_credentials_invalid")
    return build("calendar", "v3", credentials=credentials, cache_discovery=False)


def _event_metadata(event: dict) -> dict:
    start = event.get("start") if isinstance(event.get("start"), dict) else {}
    end = event.get("end") if isinstance(event.get("end"), dict) else {}
    start_value = start.get("dateTime") or start.get("date") or ""
    end_value = end.get("dateTime") or end.get("date") or ""
    return {
        "event_id": _clean(event.get("id"), 128),
        "summary": _clean(event.get("summary") or "(untitled event)", 200),
        "start": _clean(start_value, 64),
        "end": _clean(end_value, 64),
        "all_day": bool(start.get("date") and not start.get("dateTime")),
        "status": _clean(event.get("status"), 32),
    }


def fetch_upcoming_calendar_events(
    *,
    now: datetime | None = None,
    lookahead_hours: int = 24,
    max_results: int = 5,
    base_dir: str | Path | None = None,
    calendar_id: str | None = None,
    service=None,
) -> list[dict]:
    """Return bounded event metadata; no descriptions, attendees, or attachments."""
    service = service or _calendar_service(base_dir)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    lookahead = max(1, min(int(lookahead_hours), 168))
    limit = max(1, min(int(max_results), 10))
    result = service.events().list(
        calendarId=str(calendar_id or config.CALENDAR_ID),
        timeMin=current.isoformat().replace("+00:00", "Z"),
        timeMax=(current + timedelta(hours=lookahead)).isoformat().replace("+00:00", "Z"),
        maxResults=limit,
        singleEvents=True,
        orderBy="startTime",
    ).execute()
    rows = result.get("items") if isinstance(result, dict) else []
    return [_event_metadata(row) for row in (rows if isinstance(rows, list) else [])[:limit]
            if isinstance(row, dict)]


def _parse_datetime(value: str, timezone_name: str) -> datetime:
    parsed = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
    zone = ZoneInfo(timezone_name)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone)
    return parsed.astimezone(zone)


def _event_identity(calendar_id: str, summary: str, start: str, end: str) -> str:
    payload = json.dumps(
        [calendar_id, _clean(summary, 200).casefold(), start, end],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    # Google event IDs accept base32hex (0-9, a-v). A deterministic ID makes exact
    # create retries safe even if the provider response was lost.
    digest = base64.b32hexencode(hashlib.sha256(payload).digest()).decode("ascii").lower()
    return "ciel" + digest[:40]


def get_calendar_tools() -> dict:
    try:
        service = _calendar_service()
    except Exception as exc:
        reason = "token_missing" if "token_missing" in str(exc) else "oauth_or_credentials_error"
        return {
            "tools": [],
            "prompt": "",
            "readiness": {"state": "not_configured", "reason": reason},
        }

    def list_calendar_events(
        lookahead_hours: int = 168,
        max_results: int = 10,
        calendar_id: str = "",
    ) -> dict:
        """List upcoming event metadata from one calendar."""
        try:
            events = fetch_upcoming_calendar_events(
                lookahead_hours=lookahead_hours,
                max_results=max_results,
                calendar_id=calendar_id or config.CALENDAR_ID,
                service=service,
            )
            return make_result(True, data={"events": events}, tool_name="list_calendar_events")
        except Exception:
            return make_result(False, code="CALENDAR_READ_ERROR",
                               message="Calendar events are unavailable.",
                               tool_name="list_calendar_events")

    def create_calendar_event(
        summary: str,
        start: str,
        end: str,
        timezone_name: str = "",
        calendar_id: str = "",
    ) -> dict:
        """Create one event; exact retries resolve to the same deterministic event ID."""
        try:
            clean_summary = _clean(summary, 200)
            if not clean_summary:
                raise ValueError("summary is required")
            zone_name = str(timezone_name or config.PLANNER_TIMEZONE)
            start_dt = _parse_datetime(start, zone_name)
            end_dt = _parse_datetime(end, zone_name)
            if end_dt <= start_dt:
                raise ValueError("end must be after start")
            target = str(calendar_id or config.CALENDAR_ID)
            event_id = _event_identity(
                target, clean_summary, start_dt.isoformat(), end_dt.isoformat()
            )
            body = {
                "id": event_id,
                "summary": clean_summary,
                "start": {"dateTime": start_dt.isoformat(), "timeZone": zone_name},
                "end": {"dateTime": end_dt.isoformat(), "timeZone": zone_name},
            }
            try:
                event = service.events().insert(calendarId=target, body=body).execute()
                created = True
            except Exception as exc:
                if getattr(getattr(exc, "resp", None), "status", None) != 409:
                    raise
                event = service.events().get(calendarId=target, eventId=event_id).execute()
                created = False
            return make_result(True, data={"event": _event_metadata(event), "created": created},
                               tool_name="create_calendar_event")
        except ValueError as exc:
            return make_result(False, code="CALENDAR_CREATE_ERROR", message=str(exc),
                               tool_name="create_calendar_event")
        except Exception:
            return make_result(False, code="CALENDAR_CREATE_ERROR",
                               message="Calendar event creation failed.",
                               tool_name="create_calendar_event")

    def update_calendar_event(
        event_id: str,
        summary: str = "",
        start: str = "",
        end: str = "",
        timezone_name: str = "",
        calendar_id: str = "",
    ) -> dict:
        """Patch title and/or times of one event by provider event ID."""
        try:
            if not any((summary, start, end)):
                raise ValueError("provide summary, start, or end")
            zone_name = str(timezone_name or config.PLANNER_TIMEZONE)
            patch: dict[str, Any] = {}
            if summary:
                clean_summary = _clean(summary, 200)
                if not clean_summary:
                    raise ValueError("summary cannot be blank")
                patch["summary"] = clean_summary
            if start:
                start_dt = _parse_datetime(start, zone_name)
                patch["start"] = {"dateTime": start_dt.isoformat(), "timeZone": zone_name}
            if end:
                end_dt = _parse_datetime(end, zone_name)
                patch["end"] = {"dateTime": end_dt.isoformat(), "timeZone": zone_name}
            event = service.events().patch(
                calendarId=str(calendar_id or config.CALENDAR_ID),
                eventId=_clean(event_id, 128),
                body=patch,
            ).execute()
            return make_result(True, data={"event": _event_metadata(event)},
                               tool_name="update_calendar_event")
        except ValueError as exc:
            return make_result(False, code="CALENDAR_UPDATE_ERROR", message=str(exc),
                               tool_name="update_calendar_event")
        except Exception:
            return make_result(False, code="CALENDAR_UPDATE_ERROR",
                               message="Calendar event update failed.",
                               tool_name="update_calendar_event")

    def delete_calendar_event(event_id: str, calendar_id: str = "") -> dict:
        """Delete one event by provider event ID."""
        try:
            service.events().delete(
                calendarId=str(calendar_id or config.CALENDAR_ID),
                eventId=_clean(event_id, 128),
            ).execute()
            return make_result(True, data={"event_id": _clean(event_id, 128), "deleted": True},
                               tool_name="delete_calendar_event")
        except Exception as exc:
            if getattr(getattr(exc, "resp", None), "status", None) == 404:
                return make_result(True, data={"event_id": _clean(event_id, 128),
                                               "deleted": False, "already_missing": True},
                                   tool_name="delete_calendar_event")
            return make_result(False, code="CALENDAR_DELETE_ERROR",
                               message="Calendar event deletion failed.",
                               tool_name="delete_calendar_event")

    tools = [
        StructuredTool.from_function(
            list_calendar_events,
            name="list_calendar_events",
            description="List bounded upcoming Calendar event metadata; read-only.",
        ),
        StructuredTool.from_function(
            create_calendar_event,
            name="create_calendar_event",
            description="Create a Calendar event with summary, ISO start/end, and timezone.",
        ),
        StructuredTool.from_function(
            update_calendar_event,
            name="update_calendar_event",
            description="Update a Calendar event by exact event_id; changes require confirmation.",
        ),
        StructuredTool.from_function(
            delete_calendar_event,
            name="delete_calendar_event",
            description="Delete a Calendar event by exact event_id; requires confirmation.",
        ),
    ]
    return {
        "tools": tools,
        "prompt": "",
        # Deliberately no parallel_safe declaration: closures share one Google client.
        "readiness": {"state": "ready", "reason": "tools_loaded"},
    }
