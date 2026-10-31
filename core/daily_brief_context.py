"""Optional, bounded external context for the Daily Brief.

Each source is isolated deliberately: Gmail, weather, or Calendar may disappear
without suppressing the local planner/todo/reminder/run view.
"""
from __future__ import annotations

import inspect
import json
import urllib.parse
import urllib.request
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable


def _clean_text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _bounded_int(value: Any, low: int, high: int) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if low <= parsed <= high else None


def fetch_gmail_metadata(
    *, max_results: int = 3, base_dir: str | Path | None = None, service=None
) -> dict[str, Any]:
    """Fetch unread Primary metadata only; never email bodies or snippets."""
    limit = max(1, min(int(max_results), 5))
    owns_service = service is None
    try:
        if service is None:
            from langchain_google_community.gmail.utils import get_gmail_credentials
            from core.gmail_client import build_gmail_api_service

            root = (
                Path(base_dir)
                if base_dir is not None
                else Path(__file__).resolve().parent.parent
            )
            credentials_file = root / "ciel_data" / "credentials.json"
            token_file = root / "ciel_data" / "gmail_token.json"
            # Enrichment can run from the background scheduler. Never start an
            # interactive OAuth flow there; the Gmail skill must provision a token
            # first.
            if not credentials_file.exists() or not token_file.exists():
                raise RuntimeError("gmail_not_configured")
            params = inspect.signature(get_gmail_credentials).parameters
            common = {
                "token_file": str(token_file),
                "scopes": ["https://mail.google.com/"],
            }
            if "client_secrets_file" in params:
                credentials = get_gmail_credentials(
                    **common, client_secrets_file=str(credentials_file)
                )
            elif "client_sercret_file" in params:
                credentials = get_gmail_credentials(
                    **common, client_sercret_file=str(credentials_file)
                )
            else:
                raise RuntimeError("gmail_credential_api_unsupported")
            service = build_gmail_api_service(credentials=credentials)

        result = service.users().messages().list(
            userId="me",
            q="is:unread category:primary",
            maxResults=limit,
        ).execute()
        message_refs = result.get("messages") if isinstance(result, dict) else []
        message_refs = message_refs if isinstance(message_refs, list) else []
        items = []
        for ref in message_refs[:limit]:
            if not isinstance(ref, dict) or not ref.get("id"):
                continue
            message = service.users().messages().get(
                userId="me",
                id=str(ref["id"]),
                format="metadata",
                metadataHeaders=["From", "Subject", "Date"],
            ).execute()
            payload = message.get("payload") if isinstance(message, dict) else {}
            headers = payload.get("headers") if isinstance(payload, dict) else []
            mapped = {
                str(header.get("name") or "").casefold(): header.get("value")
                for header in (headers if isinstance(headers, list) else [])
                if isinstance(header, dict)
            }
            items.append({
                "from": _clean_text(mapped.get("from") or "Unknown", 160),
                "subject": _clean_text(mapped.get("subject") or "(no subject)", 200),
                "date": _clean_text(mapped.get("date"), 80),
            })
        estimate = _bounded_int(
            result.get("resultSizeEstimate") if isinstance(result, dict) else None,
            0,
            1_000_000,
        )
        return {"items": items, "shown": len(items), "unread_estimate": estimate}
    finally:
        if owns_service and service is not None:
            from core.gmail_client import close_gmail_api_service

            close_gmail_api_service(service)


def fetch_current_weather(
    city: str, *, opener: Callable[..., Any] | None = None
) -> dict[str, Any]:
    """Fetch and parse one current-condition record from wttr.in."""
    city = _clean_text(city, 100)
    if not city:
        raise ValueError("weather_city_required")
    open_url = opener or urllib.request.urlopen
    url = f"https://wttr.in/{urllib.parse.quote(city)}?format=j1&m"
    with open_url(url, timeout=10) as response:
        payload = json.loads(response.read(131_072).decode("utf-8"))
    current = payload.get("current_condition") if isinstance(payload, dict) else None
    if not isinstance(current, list) or not current or not isinstance(current[0], dict):
        raise ValueError("weather_current_condition_missing")
    row = current[0]
    descriptions = row.get("weatherDesc")
    description = ""
    if isinstance(descriptions, list) and descriptions and isinstance(descriptions[0], dict):
        description = _clean_text(descriptions[0].get("value"), 100)
    return {
        "city": city,
        "condition": description,
        "temperature_c": _bounded_int(row.get("temp_C"), -100, 100),
        "feels_like_c": _bounded_int(row.get("FeelsLikeC"), -100, 100),
        "humidity_percent": _bounded_int(row.get("humidity"), 0, 100),
    }


class DailyBriefExternalContext:
    """Collect configured sources independently and return a bounded brief copy."""

    def __init__(
        self,
        *,
        gmail_enabled: bool = False,
        gmail_max_results: int = 3,
        weather_enabled: bool = False,
        weather_city: str = "",
        calendar_enabled: bool = False,
        calendar_max_results: int = 5,
        calendar_lookahead_hours: int = 24,
        base_dir: str | Path | None = None,
        gmail_fetcher: Callable[..., dict] | None = None,
        weather_fetcher: Callable[..., dict] | None = None,
        calendar_fetcher: Callable[..., list] | None = None,
    ):
        self.gmail_enabled = bool(gmail_enabled)
        self.gmail_max_results = max(1, min(int(gmail_max_results), 5))
        self.weather_enabled = bool(weather_enabled)
        self.weather_city = _clean_text(weather_city, 100)
        self.calendar_enabled = bool(calendar_enabled)
        self.calendar_max_results = max(1, min(int(calendar_max_results), 10))
        self.calendar_lookahead_hours = max(1, min(int(calendar_lookahead_hours), 168))
        self.base_dir = Path(base_dir) if base_dir is not None else None
        self.gmail_fetcher = gmail_fetcher
        self.weather_fetcher = weather_fetcher
        self.calendar_fetcher = calendar_fetcher

    def _gmail(self) -> dict:
        fetcher = self.gmail_fetcher or fetch_gmail_metadata
        raw = fetcher(max_results=self.gmail_max_results, base_dir=self.base_dir)
        items = raw.get("items") if isinstance(raw, dict) else []
        bounded = []
        for item in (items if isinstance(items, list) else [])[:self.gmail_max_results]:
            if not isinstance(item, dict):
                continue
            bounded.append({
                "from": _clean_text(item.get("from"), 160),
                "subject": _clean_text(item.get("subject"), 200),
                "date": _clean_text(item.get("date"), 80),
            })
        estimate = _bounded_int(
            raw.get("unread_estimate") if isinstance(raw, dict) else None, 0, 1_000_000
        )
        return {"items": bounded, "shown": len(bounded), "unread_estimate": estimate}

    def _weather(self) -> dict:
        fetcher = self.weather_fetcher or fetch_current_weather
        raw = fetcher(self.weather_city)
        if not isinstance(raw, dict):
            raise ValueError("invalid_weather_result")
        return {
            "city": _clean_text(raw.get("city") or self.weather_city, 100),
            "condition": _clean_text(raw.get("condition"), 100),
            "temperature_c": _bounded_int(raw.get("temperature_c"), -100, 100),
            "feels_like_c": _bounded_int(raw.get("feels_like_c"), -100, 100),
            "humidity_percent": _bounded_int(raw.get("humidity_percent"), 0, 100),
        }

    def _calendar(self, now) -> list[dict]:
        if self.calendar_fetcher is None:
            from skills.external.calendar_ops import fetch_upcoming_calendar_events

            fetcher = fetch_upcoming_calendar_events
        else:
            fetcher = self.calendar_fetcher
        rows = fetcher(
            now=now,
            lookahead_hours=self.calendar_lookahead_hours,
            max_results=self.calendar_max_results,
            base_dir=self.base_dir,
        )
        bounded = []
        for row in (rows if isinstance(rows, list) else [])[:self.calendar_max_results]:
            if not isinstance(row, dict):
                continue
            bounded.append({
                "summary": _clean_text(row.get("summary") or "(untitled event)", 200),
                "start": _clean_text(row.get("start"), 64),
                "end": _clean_text(row.get("end"), 64),
                "all_day": bool(row.get("all_day")),
            })
        return bounded

    def enrich(self, brief: dict[str, Any], *, now=None) -> dict[str, Any]:
        result = deepcopy(brief)
        context: dict[str, Any] = {}
        unavailable: list[str] = []
        if self.gmail_enabled:
            try:
                context["gmail"] = self._gmail()
            except Exception:
                unavailable.append("gmail")
        if self.weather_enabled:
            try:
                context["weather"] = self._weather()
            except Exception:
                unavailable.append("weather")
        if self.calendar_enabled:
            try:
                context["calendar"] = self._calendar(now)
            except Exception:
                unavailable.append("calendar")
        result["external_context"] = context
        result["external_unavailable"] = unavailable
        return result
