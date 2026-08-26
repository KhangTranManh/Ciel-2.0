"""Weekly execution-plan tools backed by ``ciel_data/planner.db``."""
from __future__ import annotations

import unicodedata
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from langchain_core.tools import StructuredTool

from agent_system import config
from core.planner_store import PlannerStore, normalize_week_start
from skills._result import make_result


WEEKLY_PLAN_PROMPT = """
[WEEKLY PLAN]
Weekly tasks are concrete actions for one ISO week. They may link to a monthly goal.
Use weekday 0..6 (Monday..Sunday) and optional 24-hour HH:MM. A date supplied as
week_start is normalized to that week's Monday. Immediate loose tasks still use todo.
"""

_DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_WEEKDAY_ALIASES = {
    "0": 0, "mon": 0, "monday": 0, "thu 2": 0,
    "1": 1, "tue": 1, "tuesday": 1, "thu 3": 1,
    "2": 2, "wed": 2, "wednesday": 2, "thu 4": 2,
    "3": 3, "thu": 3, "thursday": 3, "thu 5": 3,
    "4": 4, "fri": 4, "friday": 4, "thu 6": 4,
    "5": 5, "sat": 5, "saturday": 5, "thu 7": 5,
    "6": 6, "sun": 6, "sunday": 6, "chu nhat": 6,
}


def _store() -> PlannerStore:
    return PlannerStore(config.PLANNER_DB_PATH)


def _local_now() -> datetime:
    try:
        return datetime.now(ZoneInfo(config.PLANNER_TIMEZONE))
    except Exception:
        return datetime.now().astimezone()


def _current_week_start() -> str:
    today = _local_now().date()
    return (today - timedelta(days=today.weekday())).isoformat()


def _week(value: str) -> str:
    return str(value or "").strip() or _current_week_start()


def _weekday(value: str | int | None) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    key = unicodedata.normalize("NFKD", str(value).strip().lower())
    key = "".join(ch for ch in key if not unicodedata.combining(ch))
    key = " ".join(key.replace("-", " ").split())
    if key not in _WEEKDAY_ALIASES:
        raise ValueError("weekday must be Monday..Sunday (or 0..6)")
    return _WEEKDAY_ALIASES[key]


def _task_line(task: dict) -> str:
    slot = "Unscheduled"
    if task.get("weekday") is not None:
        slot = _DAY_NAMES[int(task["weekday"])]
        if task.get("local_time"):
            slot += f" {task['local_time']}"
    link = f" -> monthly #{task['monthly_goal_id']}" if task.get("monthly_goal_id") else ""
    notes = f" | {task['notes']}" if task.get("notes") else ""
    return f"#{task['id']} [{task['status']}] {slot}: {task['title']}{link}{notes}"


def get_weekly_plan_tools() -> dict:
    def add_weekly_task(
        title: str,
        week_start: str = "",
        weekday: str = "",
        time: str = "",
        monthly_goal_id: int = 0,
        notes: str = "",
    ) -> dict:
        """Add one concrete action to a week; identical retries return the existing task."""
        try:
            task, created = _store().add_weekly_task(
                _week(week_start), title, weekday=_weekday(weekday), local_time=time or None,
                monthly_goal_id=monthly_goal_id or None, notes=notes,
            )
            verb = "Created" if created else "Already exists"
            return make_result(True, data={
                "message": f"{verb}: weekly task {_task_line(task)} for week {task['week_start']}.",
                "task": task,
                "created": created,
            }, tool_name="add_weekly_task")
        except Exception as exc:
            return make_result(False, code="PLANNER_ERROR", message=str(exc),
                               tool_name="add_weekly_task")

    def list_weekly_plan(week_start: str = "", include_closed: bool = False) -> dict:
        """List concrete tasks for one ISO week; the current week is the default."""
        try:
            target = _week(week_start)
            tasks = _store().list_weekly_tasks(target, include_closed=include_closed)
            canonical = tasks[0]["week_start"] if tasks else normalize_week_start(target)
            lines = [f"Weekly plan {canonical}:"] + [_task_line(t) for t in tasks]
            if not tasks:
                lines.append("(no matching weekly tasks)")
            return make_result(True, data={"message": "\n".join(lines), "tasks": tasks},
                               tool_name="list_weekly_plan")
        except Exception as exc:
            return make_result(False, code="PLANNER_ERROR", message=str(exc),
                               tool_name="list_weekly_plan")

    def update_weekly_task(
        task_id: int,
        title: str | None = None,
        week_start: str | None = None,
        weekday: str | None = None,
        time: str | None = None,
        monthly_goal_id: int | None = None,
        notes: str | None = None,
        status: str | None = None,
        clear_schedule: bool = False,
        clear_monthly_goal: bool = False,
    ) -> dict:
        """Update selected fields of a weekly task by numeric ID."""
        try:
            parsed_weekday = None if weekday is None else _weekday(weekday)
            task = _store().update_weekly_task(
                task_id, title=title, week_start=week_start, weekday=parsed_weekday,
                local_time=time, monthly_goal_id=monthly_goal_id, notes=notes,
                status=status, clear_schedule=clear_schedule,
                clear_monthly_goal=clear_monthly_goal,
            )
            return make_result(True, data={
                "message": f"Updated weekly task {_task_line(task)} for week {task['week_start']}.",
                "task": task,
            }, tool_name="update_weekly_task")
        except Exception as exc:
            return make_result(False, code="PLANNER_ERROR", message=str(exc),
                               tool_name="update_weekly_task")

    def complete_weekly_task(task_id: int) -> dict:
        """Mark a weekly task completed without deleting its history."""
        try:
            task = _store().update_weekly_task(task_id, status="completed")
            return make_result(True, data={
                "message": f"Completed weekly task #{task_id}: {task['title']}.",
                "task": task,
            }, tool_name="complete_weekly_task")
        except Exception as exc:
            return make_result(False, code="PLANNER_ERROR", message=str(exc),
                               tool_name="complete_weekly_task")

    tools = [
        StructuredTool.from_function(add_weekly_task, name="add_weekly_task",
                                     description="Add a concrete task to an ISO week, optionally day/time/monthly goal."),
        StructuredTool.from_function(list_weekly_plan, name="list_weekly_plan",
                                     description="List concrete tasks for the current or requested ISO week."),
        StructuredTool.from_function(update_weekly_task, name="update_weekly_task",
                                     description="Update schedule, notes, link, title, or status for a weekly task ID."),
        StructuredTool.from_function(complete_weekly_task, name="complete_weekly_task",
                                     description="Mark one weekly task completed by numeric ID."),
    ]
    return {"tools": tools, "prompt": WEEKLY_PLAN_PROMPT,
            "parallel_safe": ["list_weekly_plan"]}
