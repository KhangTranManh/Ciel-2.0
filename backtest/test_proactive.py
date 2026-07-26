"""Tier-6 proactivity suite — Notifier routing + TriggerEngine + the Group-A checks.

Zero LLM calls, zero network, zero waiting. Every decision in core/notifier.py and
core/triggers.py takes `now` as a parameter instead of reading the clock, so a whole day
of proactive behaviour — cooldowns expiring, a daily budget filling up, an unanswered
question escalating after 30 minutes, midnight rolling over — is simulated here in
milliseconds. That property is the reason this suite can exist at all, and it is worth
protecting: a check that calls time.time() internally is untestable by construction.

What is deliberately NOT covered: whether Telegram actually delivers, and whether the UI
renders. Those are integration concerns behind the channel boundary; the fake channels
here assert that the ROUTING decision was right, which is the part that is ours.

Run from the Ciel 2.0 directory:
    ./myenv/Scripts/python.exe -m backtest.test_proactive
"""
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

os.environ["PYTHONIOENCODING"] = "utf-8"
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.notifier import (          # noqa: E402
    ASK, NOTIFY, SILENT, Channel, CliChannel, Notification, Notifier, Presence,
)
from core.triggers import (          # noqa: E402
    Trigger, TriggerEngine, build_group_a, build_triggers, daily_at, make_daily_cost_check,
    make_deferred_approval_check, make_digest_check, make_important_sender_check,
    make_price_alert_check, make_repeated_failure_check, make_stale_todo_check,
    make_unfinished_task_check, parse_price_alerts,
)
from core.task_state import TaskStore   # noqa: E402
from core.permissions import Decision, DeferredStore, PermissionPolicy   # noqa: E402


HOUR = 3600.0
T0 = time.mktime((2026, 7, 26, 10, 0, 0, 0, 0, -1))     # a fixed, local, mid-morning start

_passed, _failed = 0, []


def check(name, cond, detail=""):
    global _passed
    if cond:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed.append(name)
        print(f"  FAIL  {name}" + (f"  -- {detail}" if detail else ""))


class FakeChannel(Channel):
    """A channel whose liveness and success are set by the test, not by the world."""

    def __init__(self, name, live=True, succeed=True):
        self.name = name
        self.live = live
        self.succeed = succeed
        self.sent = []

    def is_live(self, now):
        return self.live

    def send(self, note, now):
        if not self.succeed:
            return False
        self.sent.append(note)
        return True


_notifier_seq = [0]


def make_notifier(tmp, channels, **kw):
    """A notifier with its OWN state file.

    Sharing one path across tests silently carried cooldowns, spent budget and pending
    questions from one scenario into the next — which is exactly the persistence the
    Notifier is supposed to have, showing up as four unrelated failures.
    """
    kw.setdefault("daily_budget", 8)
    kw.setdefault("default_cooldown", HOUR)
    kw.setdefault("ask_escalate_seconds", 1800.0)
    _notifier_seq[0] += 1
    return Notifier(state_path=Path(tmp) / f"notify_{_notifier_seq[0]}.json",
                    channels=channels, **kw)


def note(key="k1", action="do the thing", urgency=NOTIFY, title="something happened"):
    return Notification(key=key, title=title, detail="because a threshold was crossed",
                        action=action, urgency=urgency, trigger="t")


# ─────────────────────────────────────────────────────────────────────────────
def test_message_contract(tmp):
    print("\n[1] Message contract — no action means no interrupt")
    ch = FakeChannel("app")
    n = make_notifier(tmp, [ch])

    out = n.deliver(note(action=""), T0)
    check("actionless notification is demoted to digest",
          out.status == "digest" and not ch.sent, f"status={out.status}")
    check("demoted message is kept, not dropped", len(n.peek_digest()) == 1)

    out = n.deliver(note(key="k2", action="", urgency=ASK), T0)
    check("even an ASK with no action cannot interrupt", out.status == "digest")

    check("effective_urgency downgrades actionless to SILENT",
          note(action="").effective_urgency() == SILENT)
    check("an unknown urgency string falls back to NOTIFY",
          Notification(key="x", title="t", action="a", urgency="LOUD").effective_urgency() == NOTIFY)

    rendered = note().render()
    check("render carries all three parts",
          "something happened" in rendered and "threshold" in rendered and "→ do the thing" in rendered)


def test_cooldown(tmp):
    print("\n[2] Cooldown — a condition that stays true is announced once")
    ch = FakeChannel("app")
    n = make_notifier(tmp, [ch])

    check("first occurrence is delivered", n.deliver(note(), T0).status == "delivered")
    out = n.deliver(note(), T0 + 60)
    check("the same key inside the window is suppressed",
          out.status == "suppressed" and len(ch.sent) == 1, out.reason)
    check("a DIFFERENT key is not suppressed",
          n.deliver(note(key="other"), T0 + 60).status == "delivered")
    check("the same key after the window is delivered again",
          n.deliver(note(), T0 + HOUR + 1).status == "delivered")

    # The cooldown travels with the trigger, so it is supplied on every delivery.
    n.deliver(note(key="k9"), T0, cooldown=10.0)
    check("a short per-trigger cooldown beats the long default",
          n.deliver(note(key="k9"), T0 + 20, cooldown=10.0).status == "delivered")
    n.deliver(note(key="k10"), T0, cooldown=10 * HOUR)
    check("a long per-trigger cooldown also beats the default",
          n.deliver(note(key="k10"), T0 + 2 * HOUR, cooldown=10 * HOUR).status == "suppressed")


def test_budget(tmp):
    print("\n[3] Daily budget — a hard ceiling on interruptions, counted in Python")
    ch = FakeChannel("app")
    n = make_notifier(tmp, [ch], daily_budget=3)

    for i in range(3):
        n.deliver(note(key=f"b{i}"), T0 + i)
    check("budget is spent after 3 interrupts", n.budget_left(T0) == 0)

    out = n.deliver(note(key="b3"), T0 + 10)
    check("over budget the finding drops to the digest, not the floor",
          out.status == "digest" and len(ch.sent) == 3, out.reason)
    check("the over-budget finding is still retrievable",
          any(d["key"] == "b3" for d in n.peek_digest()))

    tomorrow = T0 + 24 * HOUR
    check("budget resets on the next calendar day", n.budget_left(tomorrow) == 3)
    check("and interrupts flow again",
          n.deliver(note(key="b4"), tomorrow).status == "delivered")

    silent = n.deliver(note(key="b5", action=""), tomorrow)
    check("digest-only messages never consume budget",
          silent.status == "digest" and n.budget_left(tomorrow) == 2)


def test_routing(tmp):
    print("\n[4] Routing — liveness decides, and failures fall through")
    app = FakeChannel("app", live=True)
    tg = FakeChannel("telegram", live=True)
    n = make_notifier(tmp, [app, tg])

    out = n.deliver(note(key="r1"), T0)
    check("the first live channel wins", out.channel == "app" and not tg.sent)

    app.live = False
    out = n.deliver(note(key="r2"), T0)
    check("a dead channel is skipped", out.channel == "telegram" and len(tg.sent) == 1)

    app.live, app.succeed = True, False
    out = n.deliver(note(key="r3"), T0)
    check("a channel that accepts then fails falls through to the next",
          out.channel == "telegram" and len(tg.sent) == 2, out.channel)

    app.live = tg.live = False
    out = n.deliver(note(key="r4"), T0)
    check("with nobody home the message is held, not lost", out.status == "digest")


def test_presence_and_cli(tmp):
    print("\n[5] Presence + CLI — a live process is not a live human")
    printed = []
    presence = Presence(idle_threshold=600.0, now=T0)
    cli = CliChannel(presence, printer=printed.append)
    tg = FakeChannel("telegram")
    n = make_notifier(tmp, [cli, tg])

    check("CLI is live right after an interaction", cli.is_live(T0 + 60))
    check("CLI is dead once idle passes the threshold", not cli.is_live(T0 + 601))

    out = n.deliver(note(key="c1"), T0 + 60)
    check("a NOTIFY goes to the CLI while someone is there", out.channel == "cli")
    check("...but is queued, not printed into a half-typed line", printed == [])
    drained = n.drain_cli()
    check("drain hands it over exactly once",
          len(drained) == 1 and n.drain_cli() == [])

    n.deliver(note(key="c2", urgency=ASK), T0 + 120)
    check("an ASK prints immediately — an unseen question is worse than a broken line",
          len(printed) == 1 and "do the thing" in printed[0])

    out = n.deliver(note(key="c3"), T0 + 700)
    check("once the Master walks away, routing moves to the fallback",
          out.channel == "telegram")

    presence.touch(T0 + 800)
    check("touch() brings the CLI back", cli.is_live(T0 + 810))


def test_escalation(tmp):
    print("\n[6] Escalation — chase an unanswered question, but only an absent human")
    presence = Presence(idle_threshold=600.0, now=T0)
    cli = CliChannel(presence, printer=lambda *_: None)
    tg = FakeChannel("telegram")
    n = make_notifier(tmp, [cli, tg], ask_escalate_seconds=1800.0)

    n.deliver(note(key="e1", urgency=ASK), T0)
    check("no escalation before the window", n.escalate_stale(T0 + 1000) == [])
    check("nothing sent to the fallback yet", not tg.sent)

    sent = n.escalate_stale(T0 + 2000)
    check("an unanswered ASK escalates to the fallback channel",
          sent == ["e1"] and len(tg.sent) == 1, str(sent))
    check("escalation carries the original text", "do the thing" in tg.sent[0].render())
    check("it escalates once, not forever", n.escalate_stale(T0 + 5000) == [])

    tg.sent.clear()
    # The Master must be PRESENT here, or the question routes straight to Telegram and
    # a delivery would be mistaken for an escalation.
    presence.touch(T0 + 6000)
    out = n.deliver(note(key="e2", urgency=ASK), T0 + 6000)
    check("the question reaches the terminal while the Master is at it",
          out.channel == "cli" and not tg.sent, out.channel)
    n.ack_seen()
    check("a Master who was demonstrably present is never chased",
          n.escalate_stale(T0 + 60000) == [] and not tg.sent)

    tg.sent.clear()
    n2 = make_notifier(tmp, [tg], ask_escalate_seconds=100.0)
    n2.deliver(note(key="e3", urgency=ASK), T0)
    n2.escalate_stale(T0 + 500)
    check("an ASK already on the fallback is not re-sent to itself", len(tg.sent) == 1)


def test_persistence(tmp):
    print("\n[7] Persistence — a restart does not re-announce yesterday's news")
    d = Path(tmp) / "persist"
    d.mkdir(exist_ok=True)
    ch = FakeChannel("app")
    n1 = Notifier(state_path=d / "notify.json", channels=[ch], daily_budget=5,
                  default_cooldown=HOUR)
    n1.deliver(note(key="p1"), T0)
    n1.deliver(note(key="p2", action=""), T0)

    ch2 = FakeChannel("app")
    n2 = Notifier(state_path=d / "notify.json", channels=[ch2], daily_budget=5,
                  default_cooldown=HOUR)
    out = n2.deliver(note(key="p1"), T0 + 60)
    check("the cooldown survives a restart", out.status == "suppressed" and not ch2.sent)
    check("the digest survives a restart", any(x["key"] == "p2" for x in n2.peek_digest()))
    check("the spent budget survives a restart", n2.budget_left(T0) == 4)
    check("drain_digest empties it", n2.drain_digest() and n2.peek_digest() == [])

    (d / "notify.json").write_text("{ this is not json", encoding="utf-8")
    n3 = Notifier(state_path=d / "notify.json", channels=[FakeChannel("app")])
    check("a corrupt state file never blocks start-up or silences alerts",
          n3.deliver(note(key="p3"), T0).status == "delivered")


def test_engine(tmp):
    print("\n[8] Trigger engine — cadence, and one broken trigger never takes the rest down")
    ch = FakeChannel("app")
    n = make_notifier(tmp, [ch])
    calls = {"a": 0, "boom": 0}

    def check_a(now):
        calls["a"] += 1
        return note(key=f"a{int(now)}")

    def check_boom(now):
        calls["boom"] += 1
        raise RuntimeError("this trigger is broken")

    t_a = Trigger(name="a", check=check_a, every_seconds=300.0, cooldown_seconds=1.0)
    t_b = Trigger(name="boom", check=check_boom, every_seconds=300.0)
    eng = TriggerEngine(n, [t_b, t_a])          # broken one FIRST, on purpose

    eng.tick(T0)
    check("a healthy trigger still fires when an earlier one raised",
          calls["a"] == 1 and len(ch.sent) == 1)

    eng.tick(T0 + 60)
    check("a trigger is not re-run before its interval elapses", calls["a"] == 1)
    eng.tick(T0 + 301)
    check("...and is re-run once it has", calls["a"] == 2)

    for i in range(TriggerEngine.MAX_ERRORS + 2):
        eng.tick(T0 + 400 + i * 400)
    check("a persistently broken trigger is switched off, not retried forever",
          not t_b.enabled and calls["boom"] <= TriggerEngine.MAX_ERRORS,
          f"enabled={t_b.enabled} calls={calls['boom']}")
    check("switching it off leaves the healthy one running", t_a.enabled)

    check("a check returning None produces nothing",
          eng.tick(T0 + 99999) is not None)

    logged = []
    eng2 = TriggerEngine(n, [Trigger(name="a2", check=check_a, cooldown_seconds=1.0)],
                         logger=lambda a, b, c: logged.append((a, b, c)))
    eng2.tick(T0 + 200000)
    check("firings are written to the audit log",
          any(x[0] == "TRIGGER" and x[1] == "fired" for x in logged))


def test_unfinished_task_trigger(tmp):
    print("\n[9] Group A — an abandoned job")
    store = TaskStore(Path(tmp) / "tasks.json")
    rec = store.start("dọn workspace và gửi báo cáo")
    store.record_step("list_workspace", "ok: 12 files")
    store.finish("interrupted", "stopped before finishing")

    chk = make_unfinished_task_check(store, min_age_seconds=1800.0)
    rec.updated_at = T0 - 60
    check("a job the Master may still be watching is left alone", chk(T0) is None)

    rec.updated_at = T0 - 3600
    out = chk(T0)
    check("a genuinely abandoned job is raised", out is not None)
    check("it asks rather than announces", out.effective_urgency() == ASK)
    check("the key is the task id, so repeated polls are one event",
          out.key == f"unfinished_task:{rec.id}" and chk(T0 + 5).key == out.key)
    check("the action is concrete", "tiếp tục" in out.action and "bỏ" in out.action)
    check("the detail carries the real progress", "1 step" in out.detail or "1/1" in out.detail)
    # A record whose steps are all `done` cannot tell us how much work is left; quoting
    # "0 bước còn lại" from it is worse than saying nothing about the count.
    check("no bogus step count when the record proves nothing outstanding",
          "0 bước" not in out.action, out.action)

    store2 = TaskStore(Path(tmp) / "tasks3.json")
    r2 = store2.start("job with real work left")
    store2.record_step("read_file", "ok")
    store2.record_step("send_gmail_message", "[TOOL_ERROR] boom")
    store2.finish("interrupted")
    r2.updated_at = T0 - 3600
    check("a real outstanding count IS quoted when the record supports it",
          "1 bước còn lại" in make_unfinished_task_check(store2)(T0).action)

    store.finish("done")
    empty = TaskStore(Path(tmp) / "tasks_empty.json")
    check("nothing unfinished means nothing to say",
          make_unfinished_task_check(empty)(T0) is None)


def _write_log(path, entries):
    """Write a thoughts.log in the exact format core/llm_connector.py emits."""
    out = []
    for ts, actor, action, body in entries:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
        out.append(f"[{stamp}] [{actor}] [{action}]\n{body}\n{'-' * 60}\n")
    path.write_text("".join(out), encoding="utf-8")


def test_cost_trigger(tmp):
    print("\n[10] Group A — a cost spike")
    log = Path(tmp) / "thoughts.log"
    _write_log(log, [
        (T0 - 7200, "BRAIN", "LLM_CALL", "model=nt/cx/gpt-5.6-sol in=4000 out=500 total=4500"),
        (T0 - 3600, "WORKER", "LLM_CALL", "model=nt/cx/gpt-5.6-sol in=3000 out=900 total=3900"),
        (T0 - 60, "BRAIN", "CHAT", "not an llm call entry"),
        (T0 - 30, "BRAIN", "LLM_CALL", "model=nt/cx/gpt-5.6-sol in=2000 out=600 total=2600"),
    ])

    check("with no ceiling set, this trigger says nothing",
          make_daily_cost_check(log, 0.0, 0)(T0) is None)

    out = make_daily_cost_check(log, 0.0, 10000)(T0)
    check("it fires once today's tokens cross the ceiling", out is not None)
    check("the number reported is the real sum (4500+3900+2600 = 11000)",
          out and "11,000" in out.detail, out.detail if out else "")
    check("non-LLM_CALL entries are not counted", out and "3 lần gọi" in out.detail)
    check("the key is the day, so it fires once a day, not once a poll",
          out.key == f"daily_cost:{time.strftime('%Y-%m-%d', time.localtime(T0))}")

    check("a ceiling above today's usage stays quiet",
          make_daily_cost_check(log, 0.0, 99999)(T0) is None)

    # Yesterday's spending must not count toward today.
    _write_log(log, [(T0 - 26 * HOUR, "BRAIN", "LLM_CALL",
                      "model=x in=999999 out=999999 total=1999998")])
    check("yesterday's tokens do not leak into today's total",
          make_daily_cost_check(log, 0.0, 1000)(T0) is None)

    check("a missing log file is silence, not a crash",
          make_daily_cost_check(Path(tmp) / "nope.log", 0.0, 1)(T0) is None)


def test_failure_trigger(tmp):
    print("\n[11] Group A — a tool failing over and over")
    log = Path(tmp) / "fails.log"

    _write_log(log, [
        (T0 - 600, "TOOL", "ERROR", "send_gmail_message: invalid_grant"),
        (T0 - 500, "TOOL", "ERROR", "send_gmail_message: invalid_grant"),
    ])
    check("two failures is bad luck, not a story",
          make_repeated_failure_check(log, threshold=3)(T0) is None)

    _write_log(log, [
        (T0 - 600, "TOOL", "ERROR", "send_gmail_message: invalid_grant"),
        (T0 - 500, "TOOL", "ERROR", "send_gmail_message: invalid_grant"),
        (T0 - 400, "TOOL", "ERROR", "send_gmail_message: token expired"),
        (T0 - 300, "TOOL", "ERROR", "stealth_search: timeout"),
        (T0 - 200, "TOOL", "RESULT", "send_gmail_message: fine"),
    ])
    out = make_repeated_failure_check(log, threshold=3)(T0)
    check("three failures of one tool is", out is not None)
    check("it names the worst offender, not just any failure",
          out and "send_gmail_message" in out.title, out.title if out else "")
    check("counts are per tool, so one blip elsewhere does not add up",
          out and "3 lần lỗi" in out.detail, out.detail if out else "")
    check("the most recent error text is quoted", out and "token expired" in out.detail)
    check("successful results are not counted as failures", out and "4 lần" not in out.detail)

    _write_log(log, [(T0 - 10 * HOUR, "TOOL", "ERROR", f"x_tool: e{i}") for i in range(9)])
    check("failures outside the window are forgotten",
          make_repeated_failure_check(log, threshold=3, window_seconds=HOUR)(T0) is None)


def test_build_group_a(tmp):
    print("\n[12] Opt-in wiring — nothing talks unless it was chosen by name")
    store = TaskStore(Path(tmp) / "t2.json")
    log = Path(tmp) / "thoughts.log"

    check("no names means no triggers", build_group_a(store, log) == [])
    check("an unknown name is ignored rather than guessed",
          build_group_a(store, log, enabled_names=["nope"]) == [])

    got = build_group_a(store, log, enabled_names=["unfinished_task", "repeated_failure"])
    check("only the chosen triggers are built",
          [t.name for t in got] == ["unfinished_task", "repeated_failure"])
    check("each carries its own cadence and cooldown",
          all(t.every_seconds > 0 and t.cooldown_seconds > t.every_seconds for t in got))


def test_repeat_muting(tmp):
    print("\n[13] Feedback — a finding the Master keeps ignoring goes quiet")
    ch = FakeChannel("app")
    n = make_notifier(tmp, [ch], repeat_limit=3, default_cooldown=10.0)

    t = T0
    for i in range(3):
        out = n.deliver(note(key="stuck"), t)
        check(f"repeat {i + 1} still interrupts", out.status == "delivered", out.reason)
        t += 20

    out = n.deliver(note(key="stuck"), t)
    check("past the limit it drops to the digest instead of interrupting",
          out.status == "digest" and len(ch.sent) == 3, out.reason)
    check("the reason names the mechanism, not just 'suppressed'",
          "unheeded" in out.reason, out.reason)
    check("the finding is still recoverable from the digest",
          any(d["key"] == "stuck" for d in n.peek_digest()))
    check("muted_keys reports it", n.muted_keys(t) == ["stuck"])

    check("a DIFFERENT finding is unaffected — muting is per key, not per trigger",
          n.deliver(note(key="fresh"), t).status == "delivered")

    check("unmute lets it speak again", n.unmute("stuck")
          and n.deliver(note(key="stuck"), t + 100).status == "delivered")

    # A condition that goes away and comes back much later is news again.
    n2 = make_notifier(tmp, [FakeChannel("app")], repeat_limit=2,
                       default_cooldown=10.0, repeat_reset_seconds=1000.0)
    n2.deliver(note(key="x"), T0)
    n2.deliver(note(key="x"), T0 + 20)
    check("muted after the limit", n2.deliver(note(key="x"), T0 + 40).status == "digest")
    check("a long silence re-arms it — that is a new event, not the old one nagging",
          n2.deliver(note(key="x"), T0 + 5000).status == "delivered")


def test_digest_trigger(tmp):
    print("\n[14] Digest — the other half of the budget promise")
    ch = FakeChannel("app")
    n = make_notifier(tmp, [ch], daily_budget=1)
    n.deliver(note(key="d1"), T0)                       # spends the budget
    n.deliver(note(key="d2"), T0 + 1)                   # -> digest
    n.deliver(note(key="d3", action=""), T0 + 2)        # -> digest (no action)

    chk = make_digest_check(n)
    out = chk(T0 + 100)
    check("the digest reports everything that was held back", out is not None)
    check("...and says how many", out and "2 việc" in out.title, out.title if out else "")
    check("it lists the held-back titles", out and out.detail.count("·") == 2)
    check("draining empties the queue — held findings must not rot there",
          n.peek_digest() == [] and chk(T0 + 200) is None)

    check("nothing held back means no digest is sent", make_digest_check(n)(T0) is None)


def test_daily_at(tmp):
    print("\n[15] daily_at — a clock task expressed as a trigger")
    fired = []
    state = {}
    inner = lambda now: note(key=f"clock{int(now)}")     # noqa: E731
    gate = daily_at(8, 0, lambda now: (fired.append(now), inner(now))[1], state)

    seven = time.mktime((2026, 7, 26, 7, 30, 0, 0, 0, -1))
    check("before the hour, nothing happens", gate(seven) is None and not fired)

    eight = time.mktime((2026, 7, 26, 8, 0, 0, 0, 0, -1))
    check("at the hour it fires", gate(eight) is not None and len(fired) == 1)
    check("and not again the same day", gate(eight + 3600) is None and len(fired) == 1)

    tomorrow = time.mktime((2026, 7, 27, 9, 15, 0, 0, 0, -1))
    check("the next day it fires again", gate(tomorrow) is not None and len(fired) == 2)

    # A machine asleep at 08:00 must still get its digest when it wakes.
    late_state = {}
    late = daily_at(8, 0, inner, late_state)
    check("a late first poll still fires that day, rather than skipping it",
          late(time.mktime((2026, 7, 28, 14, 0, 0, 0, 0, -1))) is not None)


def test_price_alert(tmp):
    print("\n[16] Price alerts — the CROSSING is the news, not the level")
    check("specs are parsed", parse_price_alerts("XAU/USD>2400, BTC/USDT<60000")
          == [("XAU/USD", ">", 2400.0), ("BTC/USDT", "<", 60000.0)])
    check("garbage is dropped, never guessed at",
          parse_price_alerts("nonsense, XAU/USD??, >5") == [])

    prices = {"XAU/USD": "Giá XAU/USD: 2350.00 USD"}
    chk = make_price_alert_check([("XAU/USD", ">", 2400.0)],
                                 fetcher=lambda s: prices[s], state={})
    check("below the threshold says nothing", chk(T0) is None)

    prices["XAU/USD"] = "Giá XAU/USD: 2450.00 USD"
    out = chk(T0 + 600)
    check("crossing it fires", out is not None)
    check("the message carries the real number", out and "2450" in out.detail)

    check("staying above it does NOT fire again", chk(T0 + 1200) is None)
    prices["XAU/USD"] = "Giá XAU/USD: 2100.00 USD"
    check("falling back says nothing", chk(T0 + 1800) is None)
    prices["XAU/USD"] = "Giá XAU/USD: 2500.00 USD"
    check("crossing again IS news", chk(T0 + 2400) is not None)

    # First observation must only arm the alert — otherwise every restart re-announces.
    armed_fresh = make_price_alert_check([("XAU/USD", ">", 2400.0)],
                                         fetcher=lambda s: "Giá: 2450.00", state={})
    check("the very first reading arms rather than fires", armed_fresh(T0) is None)

    bad = make_price_alert_check([("X", ">", 1.0)], fetcher=lambda s: "no number here",
                                 state={})
    check("an unparseable price is skipped, not crashed on", bad(T0) is None)


def test_important_email(tmp):
    print("\n[17] Important senders — a list the Master wrote, not every email")
    mail = ("- From: Boss <boss@corp.com> | Subject: quarterly\n"
            "- From: noreply@spam.io | Subject: sale")
    chk = make_important_sender_check(["boss@corp.com"], fetcher=lambda n: mail, state=set())
    out = chk(T0)
    check("mail from the list fires", out is not None and "boss@corp.com" in out.title)
    check("the same mail does not fire twice", chk(T0 + 60) is None)

    check("mail from nobody on the list is ignored",
          make_important_sender_check(["x@y.z"], fetcher=lambda n: mail, state=set())(T0) is None)
    check("an empty list means the trigger is inert",
          make_important_sender_check([], fetcher=lambda n: mail)(T0) is None)
    check("a Gmail auth error is not reported here — that is repeated_failure's job",
          make_important_sender_check(["boss@corp.com"],
                                      fetcher=lambda n: "[Gmail Error] invalid_grant",
                                      state=set())(T0) is None)


def test_stale_todo(tmp):
    print("\n[18] Stale todos — age, because the store has no due dates")
    p = Path(tmp) / "todos.json"
    old = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(T0 - 20 * 86400))
    new = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(T0 - 3600))
    p.write_text(json.dumps([
        {"id": 1, "task": "việc để lâu", "done": False, "created": old},
        {"id": 2, "task": "việc mới", "done": False, "created": new},
        {"id": 3, "task": "đã xong từ lâu", "done": True, "created": old},
    ]), encoding="utf-8")

    out = make_stale_todo_check(p, min_age_days=7)(T0)
    check("an old open todo is raised", out is not None)
    check("only ONE is counted — recent and completed ones do not qualify",
          out and "1 việc" in out.title, out.title if out else "")
    check("the age is reported honestly against the threshold",
          out and "20 ngày" in out.detail and "7 ngày" in out.detail)
    check("a high threshold silences it", make_stale_todo_check(p, min_age_days=90)(T0) is None)
    check("a missing todo file is silence, not a crash",
          make_stale_todo_check(Path(tmp) / "nope.json")(T0) is None)


def test_unattended_permissions(tmp):
    print("\n[19] Unattended ceiling — silence must never resolve to consent")
    pol = PermissionPolicy(risky_names={"delete_file", "send_gmail_message"})

    check("attended: a risky tool asks",
          pol.decide("delete_file", {"p": "x"})[0] == Decision.ASK)
    check("UNATTENDED: the same tool defers instead",
          pol.decide("delete_file", {"p": "x"}, attended=False)[0] == Decision.DEFER)
    check("read-only tools still run unattended",
          pol.decide("read_file", {}, attended=False)[0] == Decision.AUTO)

    pol.grant_for_session("delete_file")
    check("a session grant does NOT transfer to an unattended run",
          pol.decide("delete_file", {"p": "x"}, attended=False)[0] == Decision.DEFER)
    pol.grant_for_plan([{"tool_name": "delete_file", "tool_args": {"p": "x"}}])
    check("a plan approval does NOT transfer either",
          pol.decide("delete_file", {"p": "x"}, attended=False)[0] == Decision.DEFER)

    open_gate = PermissionPolicy(risky_names={"delete_file"}, gate_disabled=True)
    check("attended + open gate: it runs",
          open_gate.decide("delete_file", {}, attended=True)[0] == Decision.AUTO)
    check("DISABLE_SAFETY_GATE means 'stop asking', not 'act unsupervised'",
          open_gate.decide("delete_file", {}, attended=False)[0] == Decision.DEFER)

    denied = PermissionPolicy(risky_names={"delete_file"}, deny_names={"delete_file"})
    check("DENY still outranks everything, unattended included",
          denied.decide("delete_file", {}, attended=False)[0] == Decision.DENY)

    os.environ["CIEL_UNATTENDED_AUTO_TOOLS"] = "send_gmail_message"
    try:
        opted = PermissionPolicy(risky_names={"delete_file", "send_gmail_message"})
        check("the escape hatch is per tool and explicit",
              opted.decide("send_gmail_message", {}, attended=False)[0] == Decision.AUTO
              and opted.decide("delete_file", {}, attended=False)[0] == Decision.DEFER)
        hatch_vs_deny = PermissionPolicy(risky_names={"send_gmail_message"},
                                         deny_names={"send_gmail_message"})
        check("...and cannot be used to route around the deny list",
              hatch_vs_deny.decide("send_gmail_message", {}, attended=False)[0] == Decision.DENY)
    finally:
        os.environ.pop("CIEL_UNATTENDED_AUTO_TOOLS", None)

    plan = [{"tool_name": "read_file", "tool_args": {}},
            {"tool_name": "delete_file", "tool_args": {"p": "x"}}]
    review = PermissionPolicy(risky_names={"delete_file"}).review_plan(plan, attended=False)
    check("review_plan reports the deferred steps so the plan can be stopped whole",
          review[Decision.DEFER] == ["delete_file"] and review[Decision.ASK] == [])


def test_deferred_store(tmp):
    print("\n[20] Deferred store — record, tell, and deliberately do NOT replay")
    d = DeferredStore(Path(tmp) / "deferred.json")
    d.add("delete_file", {"path": "a.txt"}, reason="needs approval", source="trigger", now=T0)
    check("a blocked action is recorded", len(d) == 1)

    d.add("delete_file", {"path": "a.txt"}, now=T0 + 60)
    check("an identical repeat collapses onto one entry", len(d) == 1)
    check("...but the attempt count rises", d.pending()[0]["hits"] == 2)

    d.add("delete_file", {"path": "b.txt"}, now=T0 + 90)
    check("a DIFFERENT argument is a different action", len(d) == 2)

    text = d.describe()
    check("describe is human-readable and names the tool", "delete_file" in text)
    check("...and shows repeated attempts", "2 lần" in text)

    d2 = DeferredStore(Path(tmp) / "deferred.json")
    check("it survives a restart — that is the whole point", len(d2) == 2)
    check("resolving removes one", d2.resolve(d2.pending()[0]["id"]) and len(d2) == 1)
    check("resolving an unknown id is not an error", not d2.resolve("nope"))
    check("clear empties it", d2.clear() == 1 and len(d2) == 0)

    chk = make_deferred_approval_check(d2)
    check("nothing pending means nothing to say", chk(T0) is None)
    d2.add("send_gmail_message", {"to": "x@y.z"}, now=T0)
    out = chk(T0 + 10)
    check("pending items are raised as a question", out is not None
          and out.effective_urgency() == ASK)
    check("the action tells the Master to re-issue rather than promising a replay",
          out and "ra lệnh lại" in out.action.lower())


def test_build_triggers_full(tmp):
    print("\n[21] Assembly — a trigger with no threshold is skipped, not crashed on")
    store = TaskStore(Path(tmp) / "bt.json")
    log = Path(tmp) / "bt.log"
    n = make_notifier(tmp, [FakeChannel("app")])
    d = DeferredStore(Path(tmp) / "bt_def.json")

    got = build_triggers(enabled_names=["price_alert", "important_email", "stale_todo"],
                         task_store=store, log_path=log, notifier=n)
    check("Group C is skipped when its thresholds are unset", got == [])

    got = build_triggers(
        enabled_names=["unfinished_task", "daily_cost", "repeated_failure",
                       "deferred_approval", "digest", "price_alert", "stale_todo"],
        task_store=store, log_path=log, notifier=n, deferred_store=d,
        todo_path=Path(tmp) / "todos.json",
        price_alerts=[("XAU/USD", ">", 2400.0)])
    names = [t.name for t in got]
    check("everything asked for and satisfiable is built",
          names == ["unfinished_task", "daily_cost", "repeated_failure",
                    "deferred_approval", "digest", "price_alert", "stale_todo"], str(names))
    check("an unknown name is still ignored",
          build_triggers(enabled_names=["nope"], task_store=store) == [])
    check("a missing dependency drops that trigger rather than raising",
          [t.name for t in build_triggers(enabled_names=["unfinished_task", "digest"],
                                          task_store=store)] == ["unfinished_task"])
    check("build_group_a still works for the old call shape",
          [t.name for t in build_group_a(store, log, enabled_names=["daily_cost"])]
          == ["daily_cost"])


def main():
    tmp = tempfile.mkdtemp(prefix="ciel_proactive_")
    print("=" * 72)
    print("TIER 6 — PROACTIVITY SUITE (no LLM, no network, simulated clock)")
    print("=" * 72)
    try:
        for fn in (test_message_contract, test_cooldown, test_budget, test_routing,
                   test_presence_and_cli, test_escalation, test_persistence, test_engine,
                   test_unfinished_task_trigger, test_cost_trigger, test_failure_trigger,
                   test_build_group_a, test_repeat_muting, test_digest_trigger,
                   test_daily_at, test_price_alert, test_important_email, test_stale_todo,
                   test_unattended_permissions, test_deferred_store,
                   test_build_triggers_full):
            fn(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 72)
    total = _passed + len(_failed)
    print(f"RESULT: {_passed}/{total} passed")
    if _failed:
        for name in _failed:
            print(f"  - {name}")
    print("=" * 72)
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
