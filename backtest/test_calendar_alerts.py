"""Deterministic native calendar alert tests (Phase 3): no LLM, no network."""
from __future__ import annotations

import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from agent_system import config
from core.calendar_triggers import build_calendar_triggers
from core.notifier import Notifier
from core.planner_store import PlannerStore
from core.triggers import TriggerEngine
from skills.internal.native_calendar_ops import get_native_calendar_tools


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
        self.sent.append(note)
        return True


def _ts(value: str) -> float:
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp()


def _make(tmp: Path, name: str, alerts=(60, 15), catchup=30, accepts=True):
    store = PlannerStore(tmp / f"{name}.db")
    event, _ = store.add_calendar_event(
        "Họp nhóm", timezone_name="UTC", start_at="2026-09-05T10:00",
        end_at="2026-09-05T11:00", alert_offsets=list(alerts),
    )
    channel = FakeChannel(accepts)
    state = tmp / f"{name}_notify.json"
    return store, event, channel, state


def _engine(store, state, channel, catchup=30, logs=None):
    notifier = Notifier(state_path=state, channels=[channel])
    trigger = build_calendar_triggers(
        enabled_names=["calendar_event_due"], planner_db=store.path, catchup_minutes=catchup,
    )[0]
    sink = logs if logs is not None else []
    return TriggerEngine(notifier, [trigger], logger=lambda a, b, c: sink.append((a, b, c))), notifier


def test_delivery(tmp: Path) -> None:
    print("\n[1] Silent before time, once per offset, key format")
    store, event, channel, state = _make(tmp, "deliver")
    logs: list = []
    engine, _ = _engine(store, state, channel, logs=logs)
    check("trigger is opt-in",
          build_calendar_triggers(enabled_names=[], planner_db=store.path) == [])
    check("before the first offset the poll is silent and unlogged",
          engine.tick(_ts("2026-09-05T08:59:00")) == [] and logs == [])
    out = engine.tick(_ts("2026-09-05T09:00:30"))
    check("the 60-minute alert delivers", out and out[0][1].status == "delivered")
    check("key is calendar_event:<id>:<version>:<offset>",
          channel.sent[0].key == f"calendar_event:{event['id']}:1:60")
    check("message is Vietnamese with title and start",
          "Họp nhóm" in channel.sent[0].detail and "10:00 05/09/2026" in channel.sent[0].detail)
    check("same alert is not repeated", engine.tick(_ts("2026-09-05T09:01:40")) == [])
    out = engine.tick(_ts("2026-09-05T09:45:30"))
    check("the 15-minute alert delivers once",
          out and out[0][1].status == "delivered" and len(channel.sent) == 2
          and channel.sent[1].key.endswith(":1:15"))
    check("no further alerts afterwards", engine.tick(_ts("2026-09-05T09:47:00")) == [])
    check("both offsets are marked delivered",
          store.list_due_calendar_alerts("2026-09-05T09:46:00+00:00", catchup_minutes=30) == [])


def test_retry_and_restart(tmp: Path) -> None:
    print("\n[2] Failure retry and restart safety")
    store, event, channel, state = _make(tmp, "retry", alerts=[15], accepts=False)
    engine, notifier = _engine(store, state, channel)
    now = _ts("2026-09-05T09:45:30")
    out = engine.tick(now)
    check("failed send is not marked delivered (digest)", out[0][1].status == "digest")
    check("alert still pending after failure",
          len(store.list_due_calendar_alerts("2026-09-05T09:46:00+00:00", catchup_minutes=30)) == 1)
    channel.accepts = True
    out = engine.tick(now + 61)
    check("retry delivers exactly once", out[0][1].status == "delivered" and len(channel.sent) == 1)
    check("no duplicate digest copy remains", notifier.peek_digest() == [])

    # Crash window: notifier persisted the send, DB row not yet marked.
    store2, _, channel2, state2 = _make(tmp, "crash", alerts=[15])
    engine2, notifier2 = _engine(store2, state2, channel2)
    trigger = engine2.triggers[0]
    note = trigger.check(now)
    sent = notifier2.deliver(note, now, cooldown=trigger.cooldown_seconds)
    check("pre-crash send succeeded", sent.status == "delivered")
    ch3 = FakeChannel()
    engine3, _ = _engine(PlannerStore(store2.path), state2, ch3)  # fresh store+engine+notifier
    out = engine3.tick(now + 61)
    check("restart does not duplicate the alert",
          out and out[0][1].status == "suppressed" and ch3.sent == [])
    check("cooldown proof marks the row delivered",
          store2.list_due_calendar_alerts("2026-09-05T09:46:00+00:00", catchup_minutes=30) == [])


def test_reschedule(tmp: Path) -> None:
    print("\n[3] Cosmetic edits, reschedule, cancel, catch-up window")
    store, event, channel, state = _make(tmp, "edit", alerts=[15])
    engine, _ = _engine(store, state, channel)
    engine.tick(_ts("2026-09-05T09:45:30"))
    check("initial alert delivered", len(channel.sent) == 1)

    store.update_calendar_event(event["id"], title="Họp nhóm (đổi tên)", notes="x", location="Phòng 2")
    check("cosmetic edit keeps schedule_version",
          store.get_calendar_event(event["id"])["schedule_version"] == 1)
    check("cosmetic edit does not re-alert", engine.tick(_ts("2026-09-05T09:47:00")) == [])

    moved = store.update_calendar_event(
        event["id"], start_at="2026-09-05T14:00", end_at="2026-09-05T15:00")
    check("reschedule bumps schedule_version", moved["schedule_version"] == 2)
    check("old time stays silent", engine.tick(_ts("2026-09-05T09:50:00")) == [])
    out = engine.tick(_ts("2026-09-05T13:45:30"))
    check("new time alerts with the new version key",
          out and channel.sent[-1].key == f"calendar_event:{event['id']}:2:15")
    check("old version identity was never replayed",
          [n.key for n in channel.sent].count(f"calendar_event:{event['id']}:1:15") == 1)

    changed = store.update_calendar_event(event["id"], alert_offsets=[10])
    check("changing alerts bumps version and replaces offsets",
          changed["schedule_version"] == 3 and changed["alert_offsets"] == [10])
    check("replaced alert delivers under version 3",
          engine.tick(_ts("2026-09-05T13:50:30"))[0][1].status == "delivered"
          and channel.sent[-1].key.endswith(":3:10"))

    store_c, ev_c, ch_c, st_c = _make(tmp, "cancel", alerts=[15])
    eng_c, _ = _engine(store_c, st_c, ch_c)
    store_c.cancel_calendar_event(ev_c["id"])
    check("cancelled event stays silent", eng_c.tick(_ts("2026-09-05T09:46:00")) == [])

    store_w, ev_w, ch_w, st_w = _make(tmp, "window", alerts=[15])
    eng_w, _ = _engine(store_w, st_w, ch_w, catchup=30)
    check("outside the catch-up window stays silent",
          eng_w.tick(_ts("2026-09-05T10:30:00")) == [] and ch_w.sent == [])
    store_m, ev_m, ch_m, st_m = _make(tmp, "missed", alerts=[15])
    eng_m, _ = _engine(store_m, st_m, ch_m, catchup=30)
    out = eng_m.tick(_ts("2026-09-05T10:10:00"))
    check("a missed alert still sends inside the window", out and out[0][1].status == "delivered")

    allday = PlannerStore(tmp / "allday.db")
    ev_a, _ = allday.add_calendar_event(
        "Sinh nhật", timezone_name="Asia/Ho_Chi_Minh", all_day=True,
        start_date="2026-09-06", end_date_exclusive="2026-09-07", alert_offsets=[60])
    ch_a = FakeChannel()
    eng_a, _ = _engine(allday, tmp / "allday_notify.json", ch_a)
    # all-day starts 00:00 ICT = 17:00 UTC previous day; alert fires 16:00 UTC
    check("all-day alert is silent before local midnight - offset",
          eng_a.tick(_ts("2026-09-05T15:59:00")) == [])
    out = eng_a.tick(_ts("2026-09-05T16:00:30"))
    check("all-day alert uses the event timezone", out and out[0][1].status == "delivered")


def test_tools_disabled(tmp: Path) -> None:
    print("\n[4] Alerts requested while calendar_event_due is disabled")
    old = (config.PLANNER_DB_PATH, config.PROACTIVE_ENABLED,
           list(config.PROACTIVE_TRIGGERS), config.PLANNER_TIMEZONE)
    try:
        config.PLANNER_DB_PATH = tmp / "tools.db"
        config.PLANNER_TIMEZONE = "Asia/Ho_Chi_Minh"
        config.PROACTIVE_ENABLED = False
        config.PROACTIVE_TRIGGERS = []
        tools = {t.name: t for t in get_native_calendar_tools()["tools"]}
        args = {"title": "Khám", "start_at": "2026-09-05T10:00", "end_at": "2026-09-05T11:00"}
        bad = tools["create_native_calendar_event"].invoke({**args, "alert_offsets": [15]})
        check("create with alerts fails honestly",
              not bad["success"] and bad["error"]["code"] == "NATIVE_CALENDAR_ALERTS_DISABLED")
        store = PlannerStore(config.PLANNER_DB_PATH)
        check("failed create wrote nothing",
              store.list_calendar_events("2026-09-01T00:00", "2026-09-30T00:00",
                                         timezone_name="UTC") == [])
        ok = tools["create_native_calendar_event"].invoke(args)
        check("create without alerts still works", ok["success"] and ok["data"]["created"])
        eid = ok["data"]["event"]["id"]
        bad = tools["update_native_calendar_event"].invoke(
            {"event_id": eid, "title": "Khám 2", "alert_offsets": [15]})
        after = store.get_calendar_event(eid)
        check("update with alerts fails atomically",
              not bad["success"] and after["title"] == "Khám" and after["alert_offsets"] == []
              and after["schedule_version"] == 1)

        config.PROACTIVE_ENABLED = True
        config.PROACTIVE_TRIGGERS = ["calendar_event_due"]
        good = tools["update_native_calendar_event"].invoke(
            {"event_id": eid, "alert_offsets": [15]})
        check("update with alerts works when enabled",
              good["success"] and good["data"]["event"]["alert_offsets"] == [15])
    finally:
        config.PLANNER_DB_PATH, config.PROACTIVE_ENABLED, trig, config.PLANNER_TIMEZONE = old
        config.PROACTIVE_TRIGGERS = trig


def test_review_fixes(tmp: Path) -> None:
    import os, subprocess, sys
    root = Path(__file__).resolve().parent.parent
    # Blocker 2: config never crashes and is clamped to 5..1440.
    for raw, want in (("not-a-number", 30), ("0", 5), ("-2", 5), ("1441", 1440), ("45", 45)):
        env = dict(os.environ, PROACTIVE_CALENDAR_CATCHUP_MINUTES=raw)
        res = subprocess.run(
            [sys.executable, "-c",
             "from agent_system import config; print(config.PROACTIVE_CALENDAR_CATCHUP_MINUTES)"],
            cwd=root, env=env, capture_output=True, text=True)
        check(f"catch-up config {raw!r} -> {want}",
              res.returncode == 0 and res.stdout.strip().splitlines()[-1] == str(want),
              (res.stderr or res.stdout)[-200:])
    # Blocker 1: two alerts due together are both delivered, even with catch-up 0.
    store, _, channel, state = _make(tmp, "multi", alerts=(15,))
    store.add_calendar_event("Họp hai", timezone_name="UTC", start_at="2026-09-05T10:00",
                             end_at="2026-09-05T11:00", alert_offsets=[15])
    engine, _ = _engine(store, state, channel, catchup=0)
    engine.tick(_ts("2026-09-05T09:45:00"))
    engine.tick(_ts("2026-09-05T09:45:10"))
    check("two simultaneous alerts both send", len(channel.sent) == 2)
    # Race: cancel / reschedule between list and deliver sends nothing stale.
    for mode in ("cancel", "reschedule"):
        store, event, channel, state = _make(tmp, "race_" + mode, alerts=(15,))
        engine, _ = _engine(store, state, channel)
        real = store.__class__.list_due_calendar_alerts
        def hooked(self, *a, _real=real, _mode=mode, _id=event["id"], **kw):
            rows = _real(self, *a, **kw)
            if _mode == "cancel":
                self.cancel_calendar_event(_id)
            else:
                self.update_calendar_event(_id, start_at="2026-09-05T12:00", end_at="2026-09-05T13:00")
            return rows
        store.__class__.list_due_calendar_alerts = hooked
        try:
            out = engine.tick(_ts("2026-09-05T09:46:00"))
        finally:
            store.__class__.list_due_calendar_alerts = real
        check(f"{mode} between list and deliver sends nothing", out == [] and not channel.sent)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="ciel_cal_alerts_"))
    print("=" * 72)
    print("CIEL CALENDAR ALERT SUITE (no LLM, no network)")
    print("=" * 72)
    try:
        test_delivery(tmp)
        test_retry_and_restart(tmp)
        test_reschedule(tmp)
        test_tools_disabled(tmp)
        test_review_fixes(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    total = _passed + len(_failed)
    print(f"\nRESULT: {_passed}/{total} passed")
    for label in _failed:
        print(f"  - {label}")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
