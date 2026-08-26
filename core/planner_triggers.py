"""Deterministic monthly/weekly planner notifications for Tier 6."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .notifier import Notification, NOTIFY
from .planner_store import PlannerStore
from .triggers import Trigger


_DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _local_datetime(now: float, timezone_name: str) -> datetime:
    try:
        return datetime.fromtimestamp(now, ZoneInfo(timezone_name))
    except Exception:
        return datetime.fromtimestamp(now).astimezone()


def _open_todos(path: str | Path | None, limit: int = 10) -> list[dict]:
    try:
        if path is None:
            return []
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return [item for item in raw if isinstance(item, dict) and not item.get("done")][:limit]
    except Exception:
        return []


def make_monthly_plan_check(
    store: PlannerStore,
    *,
    timezone_name: str,
    day: int = 1,
    hour: int = 8,
    minute: int = 0,
):
    """Return a catch-up check for the current month's high-level goals."""
    day = max(1, min(28, int(day)))

    def check(now: float):
        local = _local_datetime(now, timezone_name)
        if local.day < day:
            return None
        if local.day == day and (local.hour, local.minute) < (hour, minute):
            return None
        month = local.strftime("%Y-%m")
        goals = store.list_monthly_goals(month)
        if not goals:
            return None
        lines = [f"  {idx}. #{goal['id']} {goal['title']}"
                 + (f" — {goal['notes']}" if goal.get("notes") else "")
                 for idx, goal in enumerate(goals[:15], 1)]
        return Notification(
            key=f"monthly_plan:{month}",
            title=f"Kế hoạch tháng {month}",
            detail="\n".join(lines),
            action="Bảo Ciel cập nhật mục tiêu tháng nếu ưu tiên đã thay đổi.",
            urgency=NOTIFY,
            trigger="monthly_plan",
            created_at=now,
        )
    return check


def make_weekly_plan_check(
    store: PlannerStore,
    *,
    todo_path: str | Path | None,
    timezone_name: str,
    weekday: int = 0,
    hour: int = 8,
    minute: int = 0,
):
    """Return a catch-up check for weekly tasks plus open immediate todos."""
    weekday = max(0, min(6, int(weekday)))

    def check(now: float):
        local = _local_datetime(now, timezone_name)
        if local.weekday() < weekday:
            return None
        if local.weekday() == weekday and (local.hour, local.minute) < (hour, minute):
            return None
        monday = local.date() - timedelta(days=local.weekday())
        week_start = monday.isoformat()
        tasks = store.list_weekly_tasks(week_start)
        todos = _open_todos(todo_path)
        if not tasks and not todos:
            return None

        lines: list[str] = []
        if tasks:
            lines.append("Weekly plan:")
            for task in tasks[:20]:
                slot = "Unscheduled"
                if task.get("weekday") is not None:
                    slot = _DAY_NAMES[int(task["weekday"])]
                    if task.get("local_time"):
                        slot += f" {task['local_time']}"
                link = (f" (monthly #{task['monthly_goal_id']})"
                        if task.get("monthly_goal_id") else "")
                lines.append(f"  · #{task['id']} [{slot}] {task['title']}{link}")
        if todos:
            lines.append("Open todos:")
            for todo in todos:
                lines.append(f"  · todo #{todo.get('id', '?')} {todo.get('task', '')}")

        return Notification(
            key=f"weekly_plan:{week_start}",
            title=f"Kế hoạch tuần từ {week_start}",
            detail="\n".join(lines),
            action="Bảo Ciel cập nhật kế hoạch tuần hoặc đánh dấu việc đã hoàn thành.",
            urgency=NOTIFY,
            trigger="weekly_plan",
            created_at=now,
        )
    return check


def build_planner_triggers(
    *,
    enabled_names=(),
    planner_db: str | Path,
    todo_path: str | Path | None,
    timezone_name: str,
    monthly_day: int = 1,
    monthly_hour: int = 8,
    monthly_minute: int = 0,
    weekly_weekday: int = 0,
    weekly_hour: int = 8,
    weekly_minute: int = 0,
) -> list[Trigger]:
    wanted = set(enabled_names or ())
    if not wanted.intersection({"monthly_plan", "weekly_plan"}):
        return []
    store = PlannerStore(planner_db)
    triggers: list[Trigger] = []
    if "monthly_plan" in wanted:
        triggers.append(Trigger(
            name="monthly_plan",
            check=make_monthly_plan_check(
                store, timezone_name=timezone_name, day=monthly_day,
                hour=monthly_hour, minute=monthly_minute,
            ),
            every_seconds=300.0,
            cooldown_seconds=35 * 86400.0,
        ))
    if "weekly_plan" in wanted:
        triggers.append(Trigger(
            name="weekly_plan",
            check=make_weekly_plan_check(
                store, todo_path=todo_path, timezone_name=timezone_name,
                weekday=weekly_weekday, hour=weekly_hour, minute=weekly_minute,
            ),
            every_seconds=300.0,
            cooldown_seconds=8 * 86400.0,
        ))
    return triggers
