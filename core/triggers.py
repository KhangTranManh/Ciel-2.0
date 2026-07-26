"""triggers.py — Tier-6b: what makes Ciel speak first.

WHAT THIS FIXES
---------------
`scheduler.py` can only fire on a wall-clock time, and it holds exactly one hardcoded
task. So Ciel can say "good morning" but cannot say "that job you started is still stuck"
or "something is failing repeatedly". Being proactive on a timer is a cron job; being
proactive on a *condition* is the part that behaves like an assistant.

THE DESIGN RULE, SAME AS EVERY TIER BEFORE IT
---------------------------------------------
Deterministic Python decides WHETHER to speak; the LLM (if involved at all) only ever
composes words. Every check in this module is plain Python over data Ciel already has on
disk, so an idle Ciel costs exactly zero tokens — the `_morning_digest` "Zero-Token
Standby" property, generalised.

EDGE, NOT LEVEL
---------------
The hard part of condition triggers is not detecting the condition, it is not repeating
yourself. A check that returns a Notification every time it runs would fire on every
poll and become indistinguishable from a timer. Two mechanisms keep it honest:

  * each Notification's `key` is built from the identity of the underlying thing (a task
    id, a tool name, a date bucket) — never from the message text;
  * the Notifier enforces a per-key cooldown, so a condition that merely *stays* true is
    announced once.

WHY GROUP A FIRST
-----------------
These three triggers watch Ciel itself: an unfinished job, a cost spike, a tool failing
over and over. They need no network, no API budget and no rate limit, they cannot spam
because the underlying events are genuinely rare, and they are the most assistant-like
thing here — a system that notices it is unwell and says so, instead of staying quiet
until the Master finds out the hard way.

READING thoughts.log
--------------------
Two checks parse the audit log. They are strictly READ-ONLY and treat the format as
given (see rule 1 in instructionAI/SKILL.md — the format is never to be changed). Only
the tail is read, bounded by `max_bytes`, because that file grows without limit; a check
that got slower as the log grew would eventually be a reason to turn proactivity off.
"""
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from .notifier import Notification, NOTIFY, ASK

try:                                    # cost pricing is optional: unknown models cost 0
    from .cost import estimate_cost
except Exception:                       # pragma: no cover - defensive import
    def estimate_cost(model, i, o):     # type: ignore
        return 0.0


_LOG_SEP = "-" * 60
_HEADER_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] \[([^\]]+)\] \[([^\]]+)\]")
_MODEL_RE = re.compile(r"model=(\S+)")
_IN_RE = re.compile(r"\bin=(\d+)")
_OUT_RE = re.compile(r"\bout=(\d+)")
_DEFAULT_TAIL_BYTES = 2_000_000         # ~a day of dense logging; bounded on purpose


# --- log reading -------------------------------------------------------------------

def _read_tail(path, max_bytes: int) -> str:
    """Last `max_bytes` of a file as text, or "" if unreadable.

    The first (probably partial) entry is dropped by the caller's split, so a truncated
    read can never produce a malformed entry — only a missing one, which for a threshold
    check means under-reporting rather than a false alarm.
    """
    try:
        if not path or not path.exists():
            return ""
        size = path.stat().st_size
        with open(path, "rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
            text = f.read().decode("utf-8", errors="replace")
    except Exception:
        return ""
    # `_log_thought` opens the log in text mode, so on Windows every newline it writes
    # is CRLF — the live thoughts.log is 100% CRLF. Reading in binary (which is what
    # bounds the tail) skips Python's newline translation, so without this the entry
    # separator never matches and EVERY check quietly reports "nothing found". A
    # monitoring trigger that silently never fires is indistinguishable from a healthy
    # system, which makes this the most expensive kind of bug to leave in.
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _iter_entries(path, since: float, max_bytes: int = _DEFAULT_TAIL_BYTES):
    """Yield (ts, actor, action, content) for log entries at or after `since`."""
    text = _read_tail(path, max_bytes)
    if not text:
        return
    for chunk in text.split("\n" + _LOG_SEP + "\n"):
        head, _, body = chunk.partition("\n")
        m = _HEADER_RE.match(head.strip())
        if not m:
            continue                    # partial first chunk, or a stray line
        try:
            ts = time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
        except Exception:
            continue
        if ts < since:
            continue
        yield ts, m.group(2).strip(), m.group(3).strip(), body


def _start_of_day(now: float) -> float:
    lt = time.localtime(now)
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))


# --- the engine --------------------------------------------------------------------

@dataclass
class Trigger:
    """One condition worth watching.

    `check` takes the current time and returns a Notification or None. It must not raise
    — but if it does, the engine absorbs it (see `TriggerEngine.tick`), because one
    broken trigger must never take proactivity down with it.
    """
    name: str
    check: Callable[[float], Optional[Notification]]
    every_seconds: float = 300.0        # how often to look
    cooldown_seconds: float = 3600.0    # how often the SAME finding may interrupt
    enabled: bool = True
    _last_run: float = field(default=0.0, repr=False)
    _errors: int = field(default=0, repr=False)


class TriggerEngine:
    """Polls triggers on their own cadences and hands findings to the Notifier."""

    MAX_ERRORS = 5                      # a trigger this broken is switched off, not retried forever

    def __init__(self, notifier, triggers=(), logger=None):
        self.notifier = notifier
        self.triggers = list(triggers)
        self._logger = logger
        self._lock = threading.RLock()

    def add(self, trigger: Trigger):
        with self._lock:
            self.triggers.append(trigger)

    def _log(self, action: str, msg: str):
        if self._logger:
            try:
                self._logger("TRIGGER", action, msg)
            except Exception:
                pass

    def due(self, now: float) -> list:
        with self._lock:
            return [t for t in self.triggers
                    if t.enabled and (now - t._last_run) >= t.every_seconds]

    def tick(self, now: float) -> list:
        """Run every due check, deliver what fires, then chase unanswered questions.

        Returns [(trigger_name, Outcome)] — used by tests and by the manual runner; the
        scheduler ignores it.
        """
        results = []
        for trig in self.due(now):
            trig._last_run = now
            try:
                note = trig.check(now)
            except Exception as e:
                trig._errors += 1
                self._log("check_error", f"{trig.name}: {type(e).__name__}: {e}")
                if trig._errors >= self.MAX_ERRORS:
                    trig.enabled = False
                    self._log("disabled", f"{trig.name}: {self.MAX_ERRORS} consecutive errors")
                continue
            trig._errors = 0
            if note is None:
                continue
            note.trigger = note.trigger or trig.name
            outcome = self.notifier.deliver(note, now, cooldown=trig.cooldown_seconds)
            results.append((trig.name, outcome))
            self._log("fired", f"{trig.name}: {outcome.status}"
                               f"{' via ' + outcome.channel if outcome.channel else ''}"
                               f"{' (' + outcome.reason + ')' if outcome.reason else ''}")
        try:
            self.notifier.escalate_stale(now)
        except Exception:
            pass
        return results

    def run_now(self, name: str, now: float = None):
        """Force one trigger to evaluate, ignoring its poll interval. For testing."""
        now = time.time() if now is None else now
        for t in self.triggers:
            if t.name == name:
                t._last_run = 0.0
                return self.tick(now)
        return []


# --- Group A: Ciel watching itself -------------------------------------------------

def make_unfinished_task_check(task_store, min_age_seconds: float = 1800.0):
    """A job that stopped mid-way and was never picked back up.

    Tier 2 already reclassifies anything left `active` at start-up as `interrupted`, and
    `main.py` reports it once at launch. That single report is easy to miss and never
    repeats, so a long-abandoned job silently becomes lost work. `min_age_seconds` keeps
    this from nagging about a task the Master is still in the middle of.
    """
    def check(now: float):
        rec = task_store.unfinished()
        if rec is None:
            return None
        if (now - float(rec.updated_at or 0)) < min_age_seconds:
            return None
        # A plan's total length is only known while it is running; an interrupted record
        # often holds just the steps that finished. Claiming "0 steps left" from that is
        # both wrong and self-evidently silly, so the count is quoted only when the
        # record actually proves there is work outstanding.
        remaining = len(rec.steps) - rec.done_count()
        how_many = f"chạy nốt {remaining} bước còn lại" if remaining > 0 else "chạy tiếp job này"
        return Notification(
            key=f"unfinished_task:{rec.id}",
            title="Một job dừng giữa chừng và chưa được xử lý",
            detail=f"{rec.describe()}\nĐã im lặng {int((now - rec.updated_at) / 60)} phút "
                   f"(ngưỡng {int(min_age_seconds / 60)} phút).",
            action=f"Trả lời 'tiếp tục' để {how_many}, hoặc 'bỏ' để đóng job này.",
            urgency=ASK,
            created_at=now,
        )
    return check


def make_daily_cost_check(log_path, usd_limit: float = 0.0, token_limit: int = 0,
                          max_bytes: int = _DEFAULT_TAIL_BYTES):
    """Today's spend crossed a ceiling you set.

    The failure this catches is specific and has already happened in this codebase: a
    loop or a retry storm burns tokens quietly, and the bill is the first anyone hears
    of it. Thresholds are opt-in — both default to 0, meaning off — because a wrong
    ceiling would fire every day and get proactivity switched off wholesale.
    """
    def check(now: float):
        if usd_limit <= 0 and token_limit <= 0:
            return None
        since = _start_of_day(now)
        tokens, usd, calls = 0, 0.0, 0
        for _ts, _actor, action, body in _iter_entries(log_path, since, max_bytes):
            if action != "LLM_CALL":
                continue
            i_m, o_m = _IN_RE.search(body), _OUT_RE.search(body)
            if not (i_m or o_m):
                continue
            inp = int(i_m.group(1)) if i_m else 0
            out = int(o_m.group(1)) if o_m else 0
            model_m = _MODEL_RE.search(body)
            tokens += inp + out
            calls += 1
            if model_m:
                usd += estimate_cost(model_m.group(1), inp, out)

        over = []
        if usd_limit > 0 and usd >= usd_limit:
            over.append(f"chi phí ước tính ${usd:.4f} ≥ ngưỡng ${usd_limit:.4f}")
        if token_limit > 0 and tokens >= token_limit:
            over.append(f"{tokens:,} token ≥ ngưỡng {token_limit:,}")
        if not over:
            return None
        # Keyed by day, so this fires once per day per threshold state, not per poll.
        return Notification(
            key=f"daily_cost:{time.strftime('%Y-%m-%d', time.localtime(now))}",
            title="Mức tiêu thụ hôm nay vượt ngưỡng",
            detail="; ".join(over) + f" (trên {calls} lần gọi LLM tính từ 00:00).",
            action="Chạy scripts/cost_report.py để xem tier nào tốn, hoặc hạ "
                   "AGENT_LOOP_MAX_ROUNDS nếu là do vòng lặp.",
            urgency=NOTIFY,
            created_at=now,
        )
    return check


def make_repeated_failure_check(log_path, threshold: int = 3, window_seconds: float = 3600.0,
                                max_bytes: int = _DEFAULT_TAIL_BYTES):
    """The same tool failing again and again — usually a dead credential, not bad luck.

    Ciel already retries and self-heals, which is exactly why this is invisible: each
    individual failure gets absorbed and the Master only sees degraded answers. Counting
    per tool inside a window separates "the network blipped" from "Gmail auth expired".
    """
    def check(now: float):
        since = now - window_seconds
        counts, samples = {}, {}
        for _ts, actor, action, body in _iter_entries(log_path, since, max_bytes):
            if actor != "TOOL" or action != "ERROR":
                continue
            # `_log_thought("TOOL", "error", f"{tool_name}: {result_text}")`
            name, _, rest = body.partition(":")
            name = name.strip()
            if not name or " " in name:     # not a tool name — skip rather than guess
                continue
            counts[name] = counts.get(name, 0) + 1
            samples[name] = rest.strip()[:160]

        worst = max(counts.items(), key=lambda kv: kv[1], default=None)
        if not worst or worst[1] < threshold:
            return None
        tool, n = worst
        # Bucketed by the hour so a persistent outage reports periodically rather than
        # once forever — the cooldown still governs how often that can interrupt.
        bucket = int(now // 3600)
        return Notification(
            key=f"tool_failure:{tool}:{bucket}",
            title=f"Tool '{tool}' hỏng lặp lại",
            detail=f"{n} lần lỗi trong {int(window_seconds / 60)} phút qua "
                   f"(ngưỡng {threshold}). Lỗi gần nhất: {samples.get(tool, '?')}",
            action=f"Kiểm tra credential/mạng cho '{tool}'. Ciel vẫn chạy nhưng "
                   f"kết quả nào cần tool này sẽ thiếu dữ liệu.",
            urgency=NOTIFY,
            created_at=now,
        )
    return check


def build_group_a(task_store, log_path, *, enabled_names=None,
                  unfinished_min_age: float = 1800.0,
                  cost_usd_limit: float = 0.0, cost_token_limit: int = 0,
                  failure_threshold: int = 3) -> list:
    """The self-monitoring set. `enabled_names=None` means none — proactivity is opt-in.

    Opt-in by name rather than a blacklist for the same reason skills are: this list
    will grow, and a default-on trigger added later would start talking without anyone
    choosing it.
    """
    wanted = set(enabled_names or ())
    specs = [
        Trigger(name="unfinished_task",
                check=make_unfinished_task_check(task_store, unfinished_min_age),
                every_seconds=300.0, cooldown_seconds=6 * 3600.0),
        Trigger(name="daily_cost",
                check=make_daily_cost_check(log_path, cost_usd_limit, cost_token_limit),
                every_seconds=900.0, cooldown_seconds=12 * 3600.0),
        Trigger(name="repeated_failure",
                check=make_repeated_failure_check(log_path, failure_threshold),
                every_seconds=600.0, cooldown_seconds=2 * 3600.0),
    ]
    return [t for t in specs if t.name in wanted]
