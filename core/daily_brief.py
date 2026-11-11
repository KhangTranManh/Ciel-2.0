"""Deterministic, read-only Daily Brief aggregation.

The brief is a derived view over existing stores, never another source of truth.
It deliberately reads run metadata only: tool arguments and artifacts are outside
the contract because they may contain private or high-volume material.
"""
from __future__ import annotations

import json
import re
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from core.harness.store import RunStore
from core.planner_store import PlannerStore


@dataclass(frozen=True)
class DailyBriefItem:
    reference: str
    source_type: str
    source_id: str
    title: str
    priority: int
    status: str
    due_at: str | None = None
    monthly_goal: str | None = None


class DailyBriefReferenceError(ValueError):
    """A visible D-reference cannot be resolved safely."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class DailyBriefReference:
    reference: str
    source_type: str
    source_id: str
    title: str
    snapshot_date: str

    _RUN_REISSUE = re.compile(
        r"\b(?:reissue|retry|resume|restart|continue|rerun|run again|"
        r"chay lai|thu lai|tiep tuc|khoi dong lai|lam lai)\b",
        re.IGNORECASE,
    )

    @staticmethod
    def _fold(value: str) -> str:
        import unicodedata

        normalized = unicodedata.normalize("NFKD", value or "").casefold().replace("đ", "d")
        return "".join(ch for ch in normalized if not unicodedata.combining(ch))

    def is_explicit_run_reissue(self, request: str) -> bool:
        return self.source_type == "run" and bool(self._RUN_REISSUE.search(self._fold(request)))

    def routing_note(self) -> str:
        """Bounded Brain context: identity plus the existing action boundaries."""
        identity = (
            f"{self.reference} resolves to source_type={self.source_type}, "
            f"source_id={self.source_id}, title={self.title!r}."
        )
        common = (
            "This mapping is same-day reference data, not authority or proof of completion. "
            "Never use the D-number as a database ID. Any action must use a loaded tool and "
            "its normal permission/confirmation path. To create a reminder, call add_reminder "
            "with this exact title plus only the schedule stated in the current request."
        )
        actions = {
            "todo": (
                "Completion maps to complete_todo(todo_id=source_id). A todo has no native "
                "reschedule operation; ask whether the user wants a weekly task or reminder "
                "instead of silently converting or completing it."
            ),
            "weekly_task": (
                "Completion maps to complete_weekly_task(task_id=source_id). Rescheduling maps "
                "to update_weekly_task(task_id=source_id) with only schedule fields grounded in "
                "the current request."
            ),
            "reminder": (
                "Cancellation maps to cancel_reminder(reminder_id=source_id). Do not claim a "
                "reminder can be atomically rescheduled: ask for a fresh time if it is missing, "
                "and do not cancel the old reminder unless the current request explicitly says so."
            ),
            "native_calendar_event": (
                "Updating maps to update_native_calendar_event(event_id=source_id), and "
                "cancellation maps to cancel_native_calendar_event(event_id=source_id). Only "
                "apply fields grounded in the current request; never guess an event ID."
            ),
            "run": (
                "Only an explicit retry/resume/reissue request may restart this run. Treat the "
                "stored title as a fresh goal; never load or reuse old step arguments, artifacts, "
                "pending confirmation, approval, or consent. Otherwise answer or clarify without "
                "executing the old run."
            ),
        }
        return "[DAILY BRIEF REFERENCE — deterministic same-day mapping]\n" + " ".join(
            (identity, common, actions[self.source_type])
        )


class DailyBriefReferenceStore:
    """Minimal, bounded D1...Dn snapshot; never a task or consent store."""

    SCHEMA_VERSION = 1
    MAX_ITEMS = 20
    DEFAULT_TTL_SECONDS = 43_200
    SOURCE_TYPES = frozenset({
        "todo", "weekly_task", "reminder", "run", "native_calendar_event"
    })
    _REFERENCE_RE = re.compile(r"(?<![A-Za-z0-9])D\s*0*([1-9]\d*)(?![A-Za-z0-9])", re.IGNORECASE)
    _BARE_REFERENCE_RE = re.compile(
        r"\b(?:item|number|muc|viec|so)\s*#?\s*([1-9]\d*)\b", re.IGNORECASE
    )
    _lock = threading.RLock()

    def __init__(
        self,
        path: str | Path,
        timezone_name: str = "Asia/Ho_Chi_Minh",
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ):
        self.path = Path(path)
        self.timezone_name = str(timezone_name or "").strip()
        self.ttl_seconds = max(60, min(int(ttl_seconds), 86_400))

    def _zone(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.timezone_name)
        except Exception as exc:
            raise DailyBriefReferenceError(
                "INVALID_TIMEZONE", "Daily Brief references require a valid IANA timezone."
            ) from exc

    @staticmethod
    def _parse_datetime(value: Any, zone: ZoneInfo) -> datetime:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return parsed.replace(tzinfo=zone) if parsed.tzinfo is None else parsed.astimezone(zone)

    def clear(self) -> None:
        with self._lock:
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                pass

    def save(self, brief: dict[str, Any], *, now: datetime | None = None) -> bool:
        """Persist only identity fields for a brief generated for the local current day."""
        if not isinstance(brief, dict):
            raise ValueError("brief must be an object")
        zone = self._zone()
        local_now = now or datetime.now(zone)
        local_now = (local_now.replace(tzinfo=zone) if local_now.tzinfo is None
                     else local_now.astimezone(zone))
        snapshot_date = str(brief.get("date") or "")
        if snapshot_date != local_now.date().isoformat():
            self.clear()
            return False
        generated = self._parse_datetime(brief.get("generated_at"), zone)
        expires = min(
            generated + timedelta(seconds=self.ttl_seconds),
            datetime.combine(local_now.date() + timedelta(days=1), time.min, tzinfo=zone),
        )
        items = []
        for raw in (brief.get("items") if isinstance(brief.get("items"), list) else [])[:self.MAX_ITEMS]:
            if not isinstance(raw, dict):
                continue
            source_type = str(raw.get("source_type") or "").strip()
            source_id = str(raw.get("source_id") or "").strip()[:128]
            title = " ".join(str(raw.get("title") or "").split())[:300]
            match = self._REFERENCE_RE.fullmatch(str(raw.get("reference") or "").strip())
            if source_type not in self.SOURCE_TYPES or not source_id or not title or not match:
                continue
            items.append({
                "reference": f"D{int(match.group(1))}",
                "source_type": source_type,
                "source_id": source_id,
                "title": title,
            })
        payload = {
            "schema_version": self.SCHEMA_VERSION,
            "date": snapshot_date,
            "timezone": zone.key,
            "generated_at": generated.isoformat(timespec="seconds"),
            "expires_at": expires.isoformat(timespec="seconds"),
            "items": items,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        with self._lock:
            try:
                temporary.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                temporary.replace(self.path)
            finally:
                temporary.unlink(missing_ok=True)
        return True

    def _load(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise DailyBriefReferenceError(
                "REFERENCE_MISSING",
                "That Daily Brief reference is unavailable. Please request today's brief again.",
            ) from exc
        except (OSError, ValueError) as exc:
            raise DailyBriefReferenceError(
                "REFERENCE_INVALID",
                "The saved Daily Brief reference is invalid. Please request today's brief again.",
            ) from exc
        if not isinstance(payload, dict) or payload.get("schema_version") != self.SCHEMA_VERSION:
            raise DailyBriefReferenceError(
                "REFERENCE_INVALID",
                "The saved Daily Brief reference is invalid. Please request today's brief again.",
            )
        return payload

    def resolve_request(
        self, request: str, *, now: datetime | None = None
    ) -> DailyBriefReference | None:
        numbers = [int(value) for value in self._REFERENCE_RE.findall(request or "")]
        if not numbers:
            folded = DailyBriefReference._fold(request or "")
            bare = self._BARE_REFERENCE_RE.search(folded)
            if bare and self.path.exists():
                raise DailyBriefReferenceError(
                    "REFERENCE_AMBIGUOUS",
                    f"The number {bare.group(1)} is ambiguous. Use the visible Daily Brief "
                    f"reference D{int(bare.group(1))}, or request today's brief again.",
                )
            return None
        unique = sorted(set(numbers))
        if len(unique) != 1:
            refs = ", ".join(f"D{number}" for number in unique)
            raise DailyBriefReferenceError(
                "REFERENCE_AMBIGUOUS",
                f"That request names multiple Daily Brief references ({refs}). Please act on one D-number at a time.",
            )
        reference = f"D{unique[0]}"
        payload = self._load()
        zone = self._zone()
        local_now = now or datetime.now(zone)
        local_now = (local_now.replace(tzinfo=zone) if local_now.tzinfo is None
                     else local_now.astimezone(zone))
        try:
            generated = self._parse_datetime(payload.get("generated_at"), zone)
            persisted_expiry = self._parse_datetime(payload.get("expires_at"), zone)
        except (TypeError, ValueError) as exc:
            raise DailyBriefReferenceError(
                "REFERENCE_INVALID",
                "The saved Daily Brief reference is invalid. Please request today's brief again.",
            ) from exc
        effective_expiry = min(
            persisted_expiry,
            generated + timedelta(seconds=self.ttl_seconds),
            datetime.combine(local_now.date() + timedelta(days=1), time.min, tzinfo=zone),
        )
        if (str(payload.get("date") or "") != local_now.date().isoformat()
                or generated > local_now + timedelta(minutes=5)
                or local_now >= effective_expiry):
            raise DailyBriefReferenceError(
                "REFERENCE_STALE",
                f"{reference} is stale or expired. Please request today's Daily Brief again.",
            )
        raw_items = payload.get("items")
        if not isinstance(raw_items, list):
            raise DailyBriefReferenceError(
                "REFERENCE_INVALID",
                "The saved Daily Brief reference is invalid. Please request today's brief again.",
            )
        matches = [item for item in raw_items
                   if isinstance(item, dict) and item.get("reference") == reference]
        if len(matches) != 1:
            raise DailyBriefReferenceError(
                "REFERENCE_UNKNOWN",
                f"{reference} is not an unambiguous item in the latest Daily Brief. Please refresh the brief.",
            )
        item = matches[0]
        source_type = str(item.get("source_type") or "")
        source_id = str(item.get("source_id") or "").strip()
        title = " ".join(str(item.get("title") or "").split())[:300]
        if source_type not in self.SOURCE_TYPES or not source_id or not title:
            raise DailyBriefReferenceError(
                "REFERENCE_INVALID",
                f"{reference} has invalid saved identity data. Please refresh the brief.",
            )
        return DailyBriefReference(
            reference=reference,
            source_type=source_type,
            source_id=source_id,
            title=title,
            snapshot_date=str(payload["date"]),
        )


def bind_daily_brief_decision(
    decision: dict[str, Any], reference: DailyBriefReference
) -> tuple[dict[str, Any], str | None]:
    """Bind model-selected action tools to the verified source identity.

    The Brain still interprets completion/reminder/reschedule intent, but it
    cannot substitute a guessed database ID or reminder title after Python has
    resolved the D-number. The returned plan still executes through the normal
    validator, permission policy and tool boundary.
    """
    if not isinstance(decision, dict):
        return decision, None
    id_tools = {
        "complete_todo": ("todo", "todo_id"),
        "complete_weekly_task": ("weekly_task", "task_id"),
        "update_weekly_task": ("weekly_task", "task_id"),
        "cancel_reminder": ("reminder", "reminder_id"),
        "update_native_calendar_event": ("native_calendar_event", "event_id"),
        "cancel_native_calendar_event": ("native_calendar_event", "event_id"),
    }

    def bind_step(step: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
        if not isinstance(step, dict):
            return step, None
        name = str(step.get("tool_name") or "")
        args = step.get("tool_args")
        if not isinstance(args, dict):
            # Preserve malformed arguments for the normal schema validator to
            # reject; reference binding must never heal an invalid plan into one
            # that can execute.
            return step, None
        args = dict(args)
        if name in id_tools:
            expected_source, id_field = id_tools[name]
            if reference.source_type != expected_source:
                return step, (
                    f"{reference.reference} is a {reference.source_type}, so {name} cannot "
                    "act on it. Please refresh the Daily Brief or choose an action supported "
                    "for that item type."
                )
            try:
                args[id_field] = int(reference.source_id)
            except (TypeError, ValueError):
                return step, (
                    f"{reference.reference} has an invalid saved ID. Please request today's "
                    "Daily Brief again."
                )
        elif name == "add_reminder":
            args["title"] = reference.title
            # The compatibility alias must not disagree with the grounded title.
            args.pop("message", None)
        elif name == "add_weekly_task" and reference.source_type != "run":
            args["title"] = reference.title
        bound = dict(step)
        bound["tool_args"] = args
        return bound, None

    action = decision.get("action")
    if action == "tool":
        bound, error = bind_step(decision)
        return bound, error
    if action == "multi_tool":
        bound_tools = []
        for step in decision.get("tools", []):
            bound, error = bind_step(step)
            if error:
                return decision, error
            bound_tools.append(bound)
        bound_decision = dict(decision)
        bound_decision["tools"] = bound_tools
        return bound_decision, None
    return decision, None


def _brief_time(value: Any) -> str:
    """Render an ISO timestamp without leaking the internal schema."""
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return parsed.strftime("%H:%M %d/%m")
    except (TypeError, ValueError):
        return ""


def render_daily_brief(brief: dict[str, Any], language: str = "Vietnamese") -> str:
    """Human-readable fail-safe when the presentation model is unavailable.

    The Worker remains the normal presentation path. This renderer prevents a
    provider-specific formatting failure from exposing the JSON transport schema
    to the user after the underlying read-only tool already succeeded.
    """
    vietnamese = str(language).strip().lower().startswith("viet")
    items = brief.get("items") if isinstance(brief, dict) else None
    items = items if isinstance(items, list) else []
    goals = brief.get("monthly_goals") if isinstance(brief, dict) else None
    goals = goals if isinstance(goals, list) else []
    unavailable = brief.get("unavailable_sources") if isinstance(brief, dict) else None
    unavailable = unavailable if isinstance(unavailable, list) else []
    external = brief.get("external_context") if isinstance(brief, dict) else None
    external = external if isinstance(external, dict) else {}
    external_unavailable = (
        brief.get("external_unavailable") if isinstance(brief, dict) else None
    )
    external_unavailable = (
        external_unavailable if isinstance(external_unavailable, list) else []
    )

    if vietnamese:
        if not items:
            lines = ["Hiện chưa có việc nào được ghi nhận cho hôm nay."]
        else:
            lines = ["Việc cần ưu tiên:"]
            status_labels = {
                "overdue": "đã quá hạn",
                "due_soon": "sắp đến hạn",
                "upcoming": "sắp tới",
                "scheduled": "đã lên lịch",
                "unscheduled": "chưa xếp giờ",
                "open": "chưa hoàn thành",
                "waiting_for_approval": "đang chờ xác nhận",
                "interrupted": "đang bị gián đoạn",
                "all_day": "cả ngày",
                "in_progress": "đang diễn ra",
                "started": "đã bắt đầu",
            }
            for item in items:
                if not isinstance(item, dict):
                    continue
                reference = str(item.get("reference") or "").strip()
                title = str(item.get("title") or "").strip() or "Việc chưa có tên"
                status = str(item.get("status") or "").strip()
                label = status_labels.get(status, status.replace("_", " "))
                when = _brief_time(item.get("due_at"))
                source_type = str(item.get("source_type") or "")
                if status == "all_day":
                    detail = label
                elif source_type == "run" and when:
                    detail = f"{label}; cập nhật gần nhất {when}"
                elif when:
                    detail = f"{label} lúc {when}"
                else:
                    detail = label
                suffix = f" — {detail}" if detail else ""
                lines.append(f"- {reference}: {title}{suffix}")
        goal_titles = [str(goal.get("title") or "").strip()
                       for goal in goals if isinstance(goal, dict) and goal.get("title")]
        if goal_titles:
            lines.append("Mục tiêu tháng liên quan: " + "; ".join(goal_titles))
        omitted = int(brief.get("omitted_count") or 0) if isinstance(brief, dict) else 0
        if omitted:
            lines.append(f"Còn {omitted} việc ưu tiên thấp hơn chưa hiển thị.")
        if unavailable:
            lines.append("Chưa đọc được nguồn: " + ", ".join(map(str, unavailable)) + ".")
        gmail = external.get("gmail") if isinstance(external.get("gmail"), dict) else {}
        gmail_items = gmail.get("items") if isinstance(gmail.get("items"), list) else []
        if gmail_items:
            lines.append("Email Primary chưa đọc (chỉ metadata):")
            for item in gmail_items:
                if isinstance(item, dict):
                    lines.append(
                        f"- {str(item.get('from') or 'Unknown').strip()} — "
                        f"{str(item.get('subject') or '(không có tiêu đề)').strip()}"
                    )
        weather = external.get("weather") if isinstance(external.get("weather"), dict) else {}
        if weather:
            weather_bits = [str(weather.get("condition") or "").strip()]
            if weather.get("temperature_c") is not None:
                weather_bits.append(f"{weather['temperature_c']}°C")
            if weather.get("feels_like_c") is not None:
                weather_bits.append(f"cảm giác {weather['feels_like_c']}°C")
            if weather.get("humidity_percent") is not None:
                weather_bits.append(f"độ ẩm {weather['humidity_percent']}%")
            lines.append(
                f"Thời tiết hiện tại — {str(weather.get('city') or '').strip()}: "
                + ", ".join(bit for bit in weather_bits if bit)
            )
        calendar = (
            external.get("calendar") if isinstance(external.get("calendar"), list) else []
        )
        if calendar:
            lines.append("Lịch sắp tới:")
            for event in calendar:
                if isinstance(event, dict):
                    lines.append(
                        f"- {str(event.get('start') or '').strip()}: "
                        f"{str(event.get('summary') or '(không có tiêu đề)').strip()}"
                    )
        if external_unavailable:
            lines.append(
                "Ngữ cảnh ngoài chưa khả dụng: "
                + ", ".join(map(str, external_unavailable)) + "."
            )
        return "\n".join(lines)

    if not items:
        lines = ["No recorded work is due today."]
    else:
        lines = ["Priorities:"]
        status_labels = {
            "overdue": "overdue", "due_soon": "due soon", "upcoming": "upcoming",
            "scheduled": "scheduled", "unscheduled": "not yet scheduled",
            "open": "open", "waiting_for_approval": "waiting for approval",
            "interrupted": "interrupted", "all_day": "all day",
            "in_progress": "in progress", "started": "started",
        }
        for item in items:
            if not isinstance(item, dict):
                continue
            reference = str(item.get("reference") or "").strip()
            title = str(item.get("title") or "").strip() or "Untitled item"
            status = str(item.get("status") or "").strip()
            label = status_labels.get(status, status.replace("_", " "))
            when = _brief_time(item.get("due_at"))
            if status == "all_day":
                timing = ""
            elif item.get("source_type") == "run" and when:
                timing = f"; last updated {when}"
            else:
                timing = f" at {when}" if when else ""
            detail = f" — {label}{timing}" if label else ""
            lines.append(f"- {reference}: {title}{detail}")
    goal_titles = [str(goal.get("title") or "").strip()
                   for goal in goals if isinstance(goal, dict) and goal.get("title")]
    if goal_titles:
        lines.append("Related monthly goals: " + "; ".join(goal_titles))
    omitted = int(brief.get("omitted_count") or 0) if isinstance(brief, dict) else 0
    if omitted:
        lines.append(f"{omitted} lower-priority item(s) are not shown.")
    if unavailable:
        lines.append("Unavailable source(s): " + ", ".join(map(str, unavailable)) + ".")
    gmail = external.get("gmail") if isinstance(external.get("gmail"), dict) else {}
    gmail_items = gmail.get("items") if isinstance(gmail.get("items"), list) else []
    if gmail_items:
        lines.append("Unread Primary email metadata:")
        for item in gmail_items:
            if isinstance(item, dict):
                lines.append(
                    f"- {str(item.get('from') or 'Unknown').strip()} — "
                    f"{str(item.get('subject') or '(no subject)').strip()}"
                )
    weather = external.get("weather") if isinstance(external.get("weather"), dict) else {}
    if weather:
        weather_bits = [str(weather.get("condition") or "").strip()]
        if weather.get("temperature_c") is not None:
            weather_bits.append(f"{weather['temperature_c']}°C")
        if weather.get("feels_like_c") is not None:
            weather_bits.append(f"feels like {weather['feels_like_c']}°C")
        if weather.get("humidity_percent") is not None:
            weather_bits.append(f"humidity {weather['humidity_percent']}%")
        lines.append(
            f"Current weather — {str(weather.get('city') or '').strip()}: "
            + ", ".join(bit for bit in weather_bits if bit)
        )
    calendar = external.get("calendar") if isinstance(external.get("calendar"), list) else []
    if calendar:
        lines.append("Upcoming calendar events:")
        for event in calendar:
            if isinstance(event, dict):
                lines.append(
                    f"- {str(event.get('start') or '').strip()}: "
                    f"{str(event.get('summary') or '(untitled event)').strip()}"
                )
    if external_unavailable:
        lines.append(
            "Unavailable external context: "
            + ", ".join(map(str, external_unavailable)) + "."
        )
    return "\n".join(lines)


class DailyBriefCollector:
    """Collect and rank bounded planning data without invoking a model."""

    MAX_ITEMS = 20
    MAX_LOOKAHEAD_HOURS = 168
    RUN_STATES = frozenset({"waiting_for_approval", "interrupted"})

    def __init__(
        self,
        planner_path: str | Path,
        todos_path: str | Path,
        runs_path: str | Path,
        timezone_name: str = "Asia/Ho_Chi_Minh",
    ):
        self.planner_path = Path(planner_path)
        self.todos_path = Path(todos_path)
        self.runs_path = Path(runs_path)
        self.timezone_name = str(timezone_name or "").strip()

    @staticmethod
    def _title(value: Any) -> str:
        return " ".join(str(value or "").split())[:300]

    @staticmethod
    def _parse_utc(value: Any) -> datetime:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _sort_key(item: dict) -> tuple:
        return (
            -int(item["priority"]),
            item.get("due_at") or "9999-12-31T23:59:59+00:00",
            item["source_type"],
            str(item["source_id"]),
        )

    def _planner_items(
        self,
        target: date,
        now: datetime,
        lookahead_hours: int,
    ) -> tuple[list[dict], list[dict]]:
        store = PlannerStore(self.planner_path)
        monday = target - timedelta(days=target.weekday())
        items: list[dict] = []

        for task in store.list_weekly_tasks(monday.isoformat()):
            weekday = task.get("weekday")
            if weekday is not None and int(weekday) != target.weekday():
                continue
            due_at = None
            priority = 50
            status = "unscheduled"
            if weekday is not None:
                priority = 80
                status = "scheduled"
                if task.get("local_time"):
                    parsed_time = time.fromisoformat(str(task["local_time"]))
                    local_due = datetime.combine(target, parsed_time, tzinfo=now.tzinfo)
                    due_at = local_due.isoformat(timespec="minutes")
                    if target == now.date() and local_due < now:
                        priority = 85
                        status = "overdue"
            items.append({
                "source_type": "weekly_task",
                "source_id": str(task["id"]),
                "title": self._title(task.get("title")),
                "priority": priority,
                "status": status,
                "due_at": due_at,
                "monthly_goal": self._title(task.get("monthly_goal_title")) or None,
            })

        if target == now.date():
            window_start = now
        else:
            window_start = datetime.combine(target, time.min, tzinfo=now.tzinfo)
        window_end = window_start + timedelta(hours=lookahead_hours)
        for reminder in store.list_reminders():
            due_utc = self._parse_utc(reminder.get("due_at_utc"))
            local_due = due_utc.astimezone(now.tzinfo)
            if target == now.date():
                if local_due > window_end:
                    continue
            elif not (window_start <= local_due <= window_end):
                continue
            if local_due < now:
                priority, status = 100, "overdue"
            elif local_due <= now + timedelta(hours=2):
                priority, status = 90, "due_soon"
            else:
                priority, status = 70, "upcoming"
            items.append({
                "source_type": "reminder",
                "source_id": str(reminder["id"]),
                "title": self._title(reminder.get("title")),
                "priority": priority,
                "status": status,
                "due_at": local_due.isoformat(timespec="minutes"),
                "monthly_goal": None,
            })

        goals = [
            {"id": str(goal["id"]), "title": self._title(goal.get("title"))}
            for goal in store.list_monthly_goals(target.strftime("%Y-%m"))[:5]
        ]
        return items, goals

    def _todo_items(self) -> list[dict]:
        if not self.todos_path.exists():
            return []
        payload = json.loads(self.todos_path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("todos root must be a list")
        items = []
        for todo in payload[:200]:
            if not isinstance(todo, dict) or todo.get("done"):
                continue
            title = self._title(todo.get("task"))
            if not title:
                continue
            items.append({
                "source_type": "todo",
                "source_id": str(todo.get("id", "")),
                "title": title,
                "priority": 30,
                "status": "open",
                "due_at": None,
                "monthly_goal": None,
            })
        return items

    def _native_calendar_items(
        self,
        target: date,
        now: datetime,
    ) -> list[dict]:
        """Read only bounded event identity/timing; private detail never enters a brief."""
        window_start = datetime.combine(target, time.min, tzinfo=now.tzinfo)
        window_end = window_start + timedelta(days=1)
        events = PlannerStore(self.planner_path).list_calendar_events(
            window_start.isoformat(),
            window_end.isoformat(),
            timezone_name=self.timezone_name,
            limit=self.MAX_ITEMS,
        )
        items = []
        for event in events:
            if event.get("all_day"):
                event_date = date.fromisoformat(str(event["start_date"]))
                due_at = datetime.combine(
                    event_date, time.min, tzinfo=now.tzinfo
                ).isoformat(timespec="minutes")
                priority, status = 78, "all_day"
            else:
                start = self._parse_utc(event.get("start_at_utc")).astimezone(now.tzinfo)
                end = self._parse_utc(event.get("end_at_utc")).astimezone(now.tzinfo)
                due_at = start.isoformat(timespec="minutes")
                if target == now.date() and start <= now < end:
                    priority, status = 95, "in_progress"
                elif target == now.date() and start < now:
                    priority, status = 82, "started"
                elif start <= now + timedelta(hours=2):
                    priority, status = 88, "due_soon"
                else:
                    priority, status = 76, "scheduled"
            items.append({
                "source_type": "native_calendar_event",
                "source_id": str(event["id"]),
                "title": self._title(event.get("title")),
                "priority": priority,
                "status": status,
                "due_at": due_at,
                "monthly_goal": None,
            })
        return items

    def _run_items(self) -> list[dict]:
        if not self.runs_path.exists():
            return []
        store = RunStore(self.runs_path)
        items = []
        for run in store.recent(50):
            state = str(run.get("state") or "")
            if state not in self.RUN_STATES:
                continue
            # tasks.json was migrated into runs.db for historical audit only.
            # Those legacy rows were never resumable harness runs and must not
            # reappear as current work in the Daily Brief.
            if str(run.get("reason") or "") == "legacy_history" or str(run.get("id") or "").startswith("legacy-"):
                continue
            title = self._title(run.get("goal"))
            if not title:
                continue
            updated = datetime.fromtimestamp(float(run["updated_at"]), timezone.utc)
            items.append({
                "source_type": "run",
                "source_id": str(run["id"]),
                "title": title,
                "priority": 65 if state == "waiting_for_approval" else 60,
                "status": state,
                "due_at": updated.astimezone(self._zone()).isoformat(timespec="minutes"),
                "monthly_goal": None,
            })
        return items

    def _zone(self) -> ZoneInfo:
        if not self.timezone_name:
            raise ValueError("timezone cannot be empty")
        try:
            return ZoneInfo(self.timezone_name)
        except Exception as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc

    def collect(
        self,
        target_date: str = "",
        lookahead_hours: int = 24,
        max_items: int = 7,
        *,
        now: datetime | None = None,
    ) -> dict:
        zone = self._zone()
        local_now = (now or datetime.now(zone))
        if local_now.tzinfo is None:
            local_now = local_now.replace(tzinfo=zone)
        else:
            local_now = local_now.astimezone(zone)
        try:
            target = date.fromisoformat(target_date) if target_date else local_now.date()
        except ValueError as exc:
            raise ValueError("date must use YYYY-MM-DD") from exc
        lookahead = max(1, min(int(lookahead_hours), self.MAX_LOOKAHEAD_HOURS))
        limit = max(1, min(int(max_items), self.MAX_ITEMS))

        items: list[dict] = []
        goals: list[dict] = []
        unavailable: list[str] = []
        counts = {
            "weekly_tasks": 0, "reminders": 0, "todos": 0, "runs": 0,
            "native_calendar_events": 0,
        }

        try:
            planner_items, goals = self._planner_items(target, local_now, lookahead)
            items.extend(planner_items)
            counts["weekly_tasks"] = sum(i["source_type"] == "weekly_task" for i in planner_items)
            counts["reminders"] = sum(i["source_type"] == "reminder" for i in planner_items)
        except Exception:
            unavailable.append("planner")
        try:
            calendar_items = self._native_calendar_items(target, local_now)
            items.extend(calendar_items)
            counts["native_calendar_events"] = len(calendar_items)
        except Exception:
            unavailable.append("native_calendar")
        try:
            todo_items = self._todo_items()
            items.extend(todo_items)
            counts["todos"] = len(todo_items)
        except Exception:
            unavailable.append("todos")
        try:
            run_items = self._run_items()
            items.extend(run_items)
            counts["runs"] = len(run_items)
        except Exception:
            unavailable.append("runs")

        items.sort(key=self._sort_key)
        omitted = max(0, len(items) - limit)
        rendered = []
        for index, item in enumerate(items[:limit], 1):
            rendered.append(asdict(DailyBriefItem(reference=f"D{index}", **item)))

        return {
            "schema_version": 1,
            "date": target.isoformat(),
            "timezone": zone.key,
            "generated_at": local_now.isoformat(timespec="seconds"),
            "items": rendered,
            "monthly_goals": goals,
            "omitted_count": omitted,
            "source_counts": counts,
            "unavailable_sources": unavailable,
        }
