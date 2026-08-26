"""Monthly/weekly planner regression suite (SQLite, tools, triggers; no LLM/network)."""
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from agent_system import config
from core.notifier import Channel, Notifier
from core.planner_store import (
    PlannerStore,
    normalize_month,
    normalize_time,
    normalize_week_start,
)
from core.planner_triggers import (
    build_planner_triggers,
    make_monthly_plan_check,
    make_weekly_plan_check,
)
from skills.internal.monthly_plan_ops import get_monthly_plan_tools
from skills.internal.weekly_plan_ops import get_weekly_plan_tools


if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

_passed = 0
_failed: list[str] = []


def check(name: str, condition, detail: str = "") -> None:
    global _passed
    if condition:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed.append(name)
        print(f"  FAIL  {name}" + (f" -- {detail}" if detail else ""))


class FakeChannel(Channel):
    name = "fake"

    def __init__(self):
        self.sent = []

    def is_live(self, now: float) -> bool:
        return True

    def send(self, note, now: float) -> bool:
        self.sent.append(note)
        return True


def _tool_map(factory) -> dict:
    return {tool.name: tool for tool in factory()["tools"]}


def test_normalization() -> None:
    print("\n[1] Period and schedule normalization")
    check("month remains canonical", normalize_month("2026-08") == "2026-08")
    check("a date is normalized to ISO Monday",
          normalize_week_start("2026-08-26") == "2026-08-24")
    check("time remains canonical", normalize_time("09:05") == "09:05")
    try:
        normalize_time("9pm")
    except ValueError:
        invalid = True
    else:
        invalid = False
    check("ambiguous time is rejected instead of guessed", invalid)


def test_store(tmp: Path) -> tuple[PlannerStore, dict, dict]:
    print("\n[2] SQLite store, idempotence, links, and lifecycle")
    store = PlannerStore(tmp / "planner.db")
    with sqlite3.connect(store.path) as conn:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
    check("monthly and weekly data use separate tables",
          {"monthly_goals", "weekly_tasks"}.issubset(tables), str(tables))

    goal, created = store.add_monthly_goal("2026-08", "Ship planner", "Stable and tested")
    check("monthly goal is created", created and goal["status"] == "active")
    duplicate, created_again = store.add_monthly_goal("2026-08", "ship PLANNER")
    check("equivalent monthly retry is idempotent",
          not created_again and duplicate["id"] == goal["id"])
    check("active monthly listing finds the goal",
          [g["id"] for g in store.list_monthly_goals("2026-08")] == [goal["id"]])

    task, task_created = store.add_weekly_task(
        "2026-08-26", "Write planner tests", weekday=2, local_time="09:30",
        monthly_goal_id=goal["id"], notes="No network",
    )
    check("weekly task is normalized and linked",
          task_created and task["week_start"] == "2026-08-24"
          and task["monthly_goal_title"] == "Ship planner")
    same, same_created = store.add_weekly_task(
        "2026-08-24", "write PLANNER tests", weekday=2, local_time="09:30",
        monthly_goal_id=goal["id"],
    )
    check("equivalent weekly retry is idempotent",
          not same_created and same["id"] == task["id"])
    second, second_created = store.add_weekly_task(
        "2026-08-24", "Write planner tests", weekday=4, local_time="09:30"
    )
    check("same title at a different weekly slot is allowed",
          second_created and second["id"] != task["id"])

    updated = store.update_weekly_task(task["id"], local_time="10:00", notes="Updated")
    check("weekly task fields update atomically",
          updated["local_time"] == "10:00" and updated["notes"] == "Updated")
    completed = store.update_weekly_task(task["id"], status="completed")
    check("completed weekly task leaves active view",
          completed["status"] == "completed"
          and all(t["id"] != task["id"] for t in store.list_weekly_tasks("2026-08-24")))
    check("completed history remains queryable",
          any(t["id"] == task["id"]
              for t in store.list_weekly_tasks("2026-08-24", include_closed=True)))
    goal_done = store.update_monthly_goal(goal["id"], status="completed")
    check("completed monthly history is retained",
          goal_done["status"] == "completed"
          and store.list_monthly_goals("2026-08") == [])

    try:
        store.add_weekly_task("2026-08-24", "Bad time", local_time="08:00")
    except ValueError:
        guarded = True
    else:
        guarded = False
    check("time without weekday is rejected", guarded)
    return store, goal, second


def test_separate_tools(tmp: Path) -> None:
    print("\n[3] Separate auto-discoverable tool packs")
    old_path = config.PLANNER_DB_PATH
    config.PLANNER_DB_PATH = tmp / "tool_planner.db"
    try:
        monthly = _tool_map(get_monthly_plan_tools)
        weekly = _tool_map(get_weekly_plan_tools)
        check("monthly pack has only monthly planner tools",
              set(monthly) == {"add_monthly_goal", "list_monthly_goals",
                               "update_monthly_goal", "complete_monthly_goal"})
        check("weekly pack has only weekly planner tools",
              set(weekly) == {"add_weekly_task", "list_weekly_plan",
                              "update_weekly_task", "complete_weekly_task"})

        added = monthly["add_monthly_goal"].invoke({
            "title": "Tool-created goal", "month": "2026-09", "notes": ""
        })
        goal_id = added["data"]["goal"]["id"]
        check("monthly tool returns the standard result envelope",
              added["success"] and added["data"]["created"])
        weekly_added = weekly["add_weekly_task"].invoke({
            "title": "Tool-created action", "week_start": "2026-09-02",
            "weekday": "Wednesday", "time": "14:00",
            "monthly_goal_id": goal_id, "notes": "",
        })
        check("weekly tool parses names and links the monthly goal",
              weekly_added["success"]
              and weekly_added["data"]["task"]["weekday"] == 2
              and weekly_added["data"]["task"]["monthly_goal_id"] == goal_id)
        repeated = weekly["add_weekly_task"].invoke({
            "title": "tool-created ACTION", "week_start": "2026-09-01",
            "weekday": "thu 4", "time": "14:00",
            "monthly_goal_id": goal_id, "notes": "",
        })
        check("tool-level retry reports existing instead of duplicating",
              repeated["success"] and not repeated["data"]["created"])
    finally:
        config.PLANNER_DB_PATH = old_path


def test_triggers(tmp: Path) -> None:
    print("\n[4] Monthly/weekly catch-up triggers and notifier dedupe")
    tz = ZoneInfo("Asia/Ho_Chi_Minh")
    store = PlannerStore(tmp / "trigger_planner.db")
    goal, _ = store.add_monthly_goal("2026-08", "August outcome")
    store.add_weekly_task(
        "2026-08-24", "Monday action", weekday=0, local_time="10:00",
        monthly_goal_id=goal["id"],
    )
    todo_path = tmp / "todos.json"
    todo_path.write_text(json.dumps([
        {"id": 1, "task": "Open immediate task", "done": False},
        {"id": 2, "task": "Already done", "done": True},
    ]), encoding="utf-8")

    monthly = make_monthly_plan_check(
        store, timezone_name="Asia/Ho_Chi_Minh", day=1, hour=8, minute=0
    )
    before_month = datetime(2026, 8, 1, 7, 59, tzinfo=tz).timestamp()
    after_month = datetime(2026, 8, 1, 8, 0, tzinfo=tz).timestamp()
    check("monthly overview waits for configured local time", monthly(before_month) is None)
    month_note = monthly(after_month)
    check("monthly overview contains active goals and a stable period key",
          month_note is not None and "August outcome" in month_note.detail
          and month_note.key == "monthly_plan:2026-08")
    check("monthly overview catches up after day one",
          monthly(datetime(2026, 8, 3, 12, 0, tzinfo=tz).timestamp()) is not None)

    weekly = make_weekly_plan_check(
        store, todo_path=todo_path, timezone_name="Asia/Ho_Chi_Minh",
        weekday=0, hour=8, minute=0,
    )
    before_week = datetime(2026, 8, 24, 7, 59, tzinfo=tz).timestamp()
    after_week = datetime(2026, 8, 24, 8, 0, tzinfo=tz).timestamp()
    check("weekly agenda waits for configured local time", weekly(before_week) is None)
    week_note = weekly(after_week)
    check("weekly agenda includes weekly actions and open todos",
          week_note is not None and "Monday action" in week_note.detail
          and "Open immediate task" in week_note.detail)
    check("weekly agenda excludes completed todos",
          week_note is not None and "Already done" not in week_note.detail)
    check("weekly agenda key is the ISO week identity",
          week_note is not None and week_note.key == "weekly_plan:2026-08-24")
    check("weekly agenda catches up later in the same week",
          weekly(datetime(2026, 8, 26, 9, 0, tzinfo=tz).timestamp()) is not None)

    built = build_planner_triggers(
        enabled_names=["monthly_plan", "weekly_plan", "unknown"],
        planner_db=store.path, todo_path=todo_path,
        timezone_name="Asia/Ho_Chi_Minh",
    )
    check("planner trigger assembly is opt-in and ignores unknown names",
          [item.name for item in built] == ["monthly_plan", "weekly_plan"])

    channel = FakeChannel()
    notifier_path = tmp / "notify.json"
    notifier = Notifier(state_path=notifier_path, channels=[channel], daily_budget=8)
    first = notifier.deliver(week_note, after_week, cooldown=8 * 86400)
    restarted = Notifier(state_path=notifier_path, channels=[channel], daily_budget=8)
    second = restarted.deliver(week_note, after_week + 60, cooldown=8 * 86400)
    check("successful agenda delivery is persisted", first.status == "delivered")
    check("the same week's agenda is suppressed after restart",
          second.status == "suppressed" and "cooldown" in second.reason)


def main() -> int:
    tmp_root = Path(tempfile.mkdtemp(prefix="ciel_planner_"))
    print("=" * 72)
    print("MONTHLY/WEEKLY PLANNER SUITE (no LLM, no network)")
    print("=" * 72)
    try:
        test_normalization()
        test_store(tmp_root)
        test_separate_tools(tmp_root)
        test_triggers(tmp_root)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)

    total = _passed + len(_failed)
    print("\n" + "=" * 72)
    print(f"RESULT: {_passed}/{total} passed")
    if _failed:
        for name in _failed:
            print(f"  - {name}")
    print("=" * 72)
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
