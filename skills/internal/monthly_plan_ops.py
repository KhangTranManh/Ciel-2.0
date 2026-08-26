"""Monthly planning tools backed by ``ciel_data/planner.db``."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from langchain_core.tools import StructuredTool

from agent_system import config
from core.planner_store import PlannerStore
from skills._result import make_result


MONTHLY_PLAN_PROMPT = """
[MONTHLY PLAN]
Monthly goals are high-level outcomes and milestones, not immediate todo items.
Use add_monthly_goal/list_monthly_goals/update_monthly_goal/complete_monthly_goal.
The month format is YYYY-MM. Omitting it means the current planner-timezone month.
"""


def _store() -> PlannerStore:
    return PlannerStore(config.PLANNER_DB_PATH)


def _current_month() -> str:
    try:
        return datetime.now(ZoneInfo(config.PLANNER_TIMEZONE)).strftime("%Y-%m")
    except Exception:
        return datetime.now().strftime("%Y-%m")


def _month(value: str) -> str:
    return str(value or "").strip() or _current_month()


def _goal_line(goal: dict) -> str:
    notes = f" | {goal['notes']}" if goal.get("notes") else ""
    return f"#{goal['id']} [{goal['status']}] {goal['title']}{notes}"


def get_monthly_plan_tools() -> dict:
    def add_monthly_goal(title: str, month: str = "", notes: str = "") -> dict:
        """Add one high-level goal to a month; identical retries return the existing goal."""
        try:
            goal, created = _store().add_monthly_goal(_month(month), title, notes)
            verb = "Created" if created else "Already exists"
            return make_result(True, data={
                "message": f"{verb}: monthly goal {_goal_line(goal)} for {goal['month']}.",
                "goal": goal,
                "created": created,
            }, tool_name="add_monthly_goal")
        except Exception as exc:
            return make_result(False, code="PLANNER_ERROR", message=str(exc),
                               tool_name="add_monthly_goal")

    def list_monthly_goals(month: str = "", include_closed: bool = False) -> dict:
        """List goals for one month. By default only active goals are returned."""
        try:
            target = _month(month)
            goals = _store().list_monthly_goals(target, include_closed=include_closed)
            lines = [f"Monthly plan {target}:"] + [_goal_line(g) for g in goals]
            if not goals:
                lines.append("(no matching monthly goals)")
            return make_result(True, data={"message": "\n".join(lines), "goals": goals},
                               tool_name="list_monthly_goals")
        except Exception as exc:
            return make_result(False, code="PLANNER_ERROR", message=str(exc),
                               tool_name="list_monthly_goals")

    def update_monthly_goal(
        goal_id: int,
        title: str | None = None,
        month: str | None = None,
        notes: str | None = None,
        status: str | None = None,
    ) -> dict:
        """Update selected fields of an existing monthly goal by numeric ID."""
        try:
            goal = _store().update_monthly_goal(
                goal_id, title=title, month=month, notes=notes, status=status
            )
            return make_result(True, data={
                "message": f"Updated monthly goal {_goal_line(goal)} for {goal['month']}.",
                "goal": goal,
            }, tool_name="update_monthly_goal")
        except Exception as exc:
            return make_result(False, code="PLANNER_ERROR", message=str(exc),
                               tool_name="update_monthly_goal")

    def complete_monthly_goal(goal_id: int) -> dict:
        """Mark a monthly goal completed without deleting its history."""
        try:
            goal = _store().update_monthly_goal(goal_id, status="completed")
            return make_result(True, data={
                "message": f"Completed monthly goal #{goal_id}: {goal['title']}.",
                "goal": goal,
            }, tool_name="complete_monthly_goal")
        except Exception as exc:
            return make_result(False, code="PLANNER_ERROR", message=str(exc),
                               tool_name="complete_monthly_goal")

    tools = [
        StructuredTool.from_function(add_monthly_goal, name="add_monthly_goal",
                                     description="Add a big goal or milestone to a YYYY-MM monthly plan."),
        StructuredTool.from_function(list_monthly_goals, name="list_monthly_goals",
                                     description="List high-level goals for a month; month defaults to current."),
        StructuredTool.from_function(update_monthly_goal, name="update_monthly_goal",
                                     description="Update title, month, notes, or status of a monthly goal ID."),
        StructuredTool.from_function(complete_monthly_goal, name="complete_monthly_goal",
                                     description="Mark one monthly goal completed by numeric ID."),
    ]
    return {"tools": tools, "prompt": MONTHLY_PLAN_PROMPT,
            "parallel_safe": ["list_monthly_goals"]}
