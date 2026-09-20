"""Deterministic reminder storage, tools, delivery, retry, and restart tests."""
from __future__ import annotations

import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_system import config
from core.notifier import Notifier
from core.planner_store import PlannerStore, normalize_reminder_due
from core.reminder_triggers import build_reminder_triggers
from core.triggers import TriggerEngine
from skills.internal.reminder_ops import get_reminder_tools


_passed = 0
_failed: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    global _passed
    if condition:
        _passed += 1
        print(f"  [PASS] {label}")
    else:
        _failed.append(label)
        print(f"  [FAIL] {label}{': ' + detail if detail else ''}")


class FakeChannel:
    name = "telegram"

    def __init__(self, accepts: bool = True):
        self.accepts = accepts
        self.sent = []

    def is_live(self, now: float) -> bool:
        return True

    def send(self, note, now: float) -> bool:
        if not self.accepts:
            return False
        self.sent.append((note, now))
        return True


def _utc(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def test_store(tmp: Path) -> None:
    print("\n[1] Reminder store and UTC normalization")
    store = PlannerStore(tmp / "planner.db")
    due_utc, zone = normalize_reminder_due("2026-09-05T18:30", "Asia/Ho_Chi_Minh")
    check("naive local time is stored as UTC", due_utc == "2026-09-05T11:30:00+00:00")
    check("the display timezone is retained", zone == "Asia/Ho_Chi_Minh")

    reminder, created = store.add_reminder(
        "Take laundry", "2026-09-05T18:30", timezone_name="Asia/Ho_Chi_Minh"
    )
    duplicate, created_again = store.add_reminder(
        "take laundry", "2026-09-05T18:30", timezone_name="Asia/Ho_Chi_Minh"
    )
    check("a reminder is created", created and reminder["status"] == "pending")
    check("an equivalent retry is idempotent",
          not created_again and duplicate["id"] == reminder["id"])
    check("it is not due before its instant",
          store.list_due_reminders("2026-09-05T11:29:59+00:00") == [])
    check("it becomes due at its instant",
          store.list_due_reminders("2026-09-05T11:30:00+00:00")[0]["id"] == reminder["id"])

    other, _ = store.add_reminder(
        "Other task", "2026-09-06T09:00", timezone_name="Asia/Ho_Chi_Minh"
    )
    cancelled = store.cancel_reminder(other["id"])
    check("cancellation keeps history", cancelled["status"] == "cancelled")


def test_delivery_and_retry(tmp: Path) -> None:
    print("\n[2] Due delivery, failure retry, and digest dedupe")
    store = PlannerStore(tmp / "delivery.db")
    now = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc).timestamp()
    reminder, _ = store.add_reminder(
        "Call home", "2026-09-05T11:59:00+00:00", timezone_name="Asia/Ho_Chi_Minh"
    )
    channel = FakeChannel(accepts=False)
    notifier = Notifier(state_path=tmp / "notify.json", channels=[channel])
    trigger = build_reminder_triggers(
        enabled_names=["reminder_due"], planner_db=store.path,
        timezone_name="Asia/Ho_Chi_Minh",
    )[0]
    logs = []
    engine = TriggerEngine(notifier, [trigger], logger=lambda a, b, c: logs.append((a, b, c)))

    first = engine.tick(now)
    check("a failed channel leaves the reminder pending",
          first[0][1].status == "digest" and store.get_reminder(reminder["id"])["status"] == "pending")
    engine.tick(now + 61)
    check("repeated failed polls keep one digest item", len(notifier.peek_digest()) == 1)

    channel.accepts = True
    delivered = engine.tick(now + 122)
    row = store.get_reminder(reminder["id"])
    check("a later successful retry delivers once",
          delivered[0][1].status == "delivered" and len(channel.sent) == 1)
    check("successful delivery closes the reminder",
          row["status"] == "delivered" and bool(row["notified_at"]))
    check("a closed reminder does not fire on the next poll", engine.tick(now + 183) == [])
    check("delivery has an honest audit action",
          any(actor == "TRIGGER" and action == "delivered" for actor, action, _ in logs))


def test_restart_recovery(tmp: Path) -> None:
    print("\n[3] Crash window recovery and quiet cooldown")
    store = PlannerStore(tmp / "restart.db")
    now = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc).timestamp()
    reminder, _ = store.add_reminder(
        "Restart-safe", "2026-09-05T11:59:00+00:00", timezone_name="UTC"
    )
    state = tmp / "restart_notify.json"
    channel = FakeChannel()
    notifier = Notifier(state_path=state, channels=[channel])

    # Simulate a crash after Notifier persisted the successful send but before the
    # reminder outcome callback could mark its database row delivered.
    trigger = build_reminder_triggers(
        enabled_names=["reminder_due"], planner_db=store.path, timezone_name="UTC"
    )[0]
    note = trigger.check(now)
    sent = notifier.deliver(note, now, cooldown=trigger.cooldown_seconds)
    check("the simulated pre-crash send succeeded", sent.status == "delivered")
    check("the row still models the crash window", store.get_reminder(reminder["id"])["status"] == "pending")

    restarted = Notifier(state_path=state, channels=[FakeChannel()])
    logs = []
    engine = TriggerEngine(restarted, [trigger], logger=lambda a, b, c: logs.append((a, b, c)))
    outcome = engine.tick(now + 61)
    check("persisted cooldown prevents a duplicate after restart",
          outcome[0][1].status == "suppressed")
    check("cooldown proof repairs the pending database row",
          store.get_reminder(reminder["id"])["status"] == "delivered")
    check("normal cooldown suppression does not pollute the audit log", logs == [])


def test_tools(tmp: Path) -> None:
    print("\n[4] Model-facing reminder tools")
    old = (config.PLANNER_DB_PATH, config.PROACTIVE_ENABLED,
           list(config.PROACTIVE_TRIGGERS), config.PLANNER_TIMEZONE)
    try:
        config.PLANNER_DB_PATH = tmp / "tools.db"
        config.PLANNER_TIMEZONE = "Asia/Ho_Chi_Minh"
        tools = {tool.name: tool for tool in get_reminder_tools()["tools"]}

        config.PROACTIVE_ENABLED = False
        config.PROACTIVE_TRIGGERS = []
        disabled = tools["add_reminder"].invoke({"title": "No delivery", "delay_minutes": 5})
        check("creation fails honestly when delivery is disabled",
              not disabled["success"] and disabled["error"]["code"] == "REMINDER_TRIGGER_DISABLED")

        config.PROACTIVE_ENABLED = True
        config.PROACTIVE_TRIGGERS = ["reminder_due"]
        made = tools["add_reminder"].invoke({"title": "Five minute smoke", "delay_minutes": 5})
        check("the model can schedule a relative reminder", made["success"] and made["data"]["created"])
        reminder_id = made["data"]["reminder"]["id"]
        alias_made = tools["add_reminder"].invoke({
            "message": "Compatibility alias smoke", "delay_minutes": 6,
        })
        check("message alias survives Brain argument drift",
              alias_made["success"] and alias_made["data"]["created"])
        listed = tools["list_reminders"].invoke({})
        check("the model can list pending reminders",
              listed["success"] and listed["data"]["reminders"][0]["id"] == reminder_id)
        cancelled = tools["cancel_reminder"].invoke({"reminder_id": reminder_id})
        check("the model can cancel a reminder",
              cancelled["success"] and cancelled["data"]["reminder"]["status"] == "cancelled")
    finally:
        config.PLANNER_DB_PATH, config.PROACTIVE_ENABLED, triggers, config.PLANNER_TIMEZONE = old
        config.PROACTIVE_TRIGGERS = triggers


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="ciel_reminders_"))
    print("=" * 72)
    print("CIEL REMINDER SUITE (no LLM, no network)")
    print("=" * 72)
    try:
        test_store(tmp)
        test_delivery_and_retry(tmp)
        test_restart_recovery(tmp)
        test_tools(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    total = _passed + len(_failed)
    print(f"\nRESULT: {_passed}/{total} passed")
    for label in _failed:
        print(f"  - {label}")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
