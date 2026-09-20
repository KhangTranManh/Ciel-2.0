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
import json
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
    on_outcome: Optional[Callable[[Notification, object, float], None]] = field(
        default=None, repr=False
    )
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
            if trig.on_outcome is not None:
                try:
                    trig.on_outcome(note, outcome, now)
                except Exception as e:
                    self._log("outcome_error", f"{trig.name}: {type(e).__name__}: {e}")

            message = (f"{trig.name}: {outcome.status}"
                       f"{' via ' + outcome.channel if outcome.channel else ''}"
                       f"{' (' + outcome.reason + ')' if outcome.reason else ''}")
            if outcome.status == "delivered":
                self._log("delivered", message)
            elif outcome.status == "digest":
                self._log("digested", message)
            elif not str(outcome.reason or "").startswith("cooldown "):
                # Cooldown is expected steady state. Logging it every poll made almost
                # half of thoughts.log say FIRED when nothing was actually delivered.
                self._log("suppressed", message)
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


def make_deferred_approval_check(deferred_store, min_items: int = 1):
    """A background run wanted to do something that needed the Master, and stopped.

    This is the visible half of the unattended permission ceiling (see
    `core/permissions.py`): DEFER is only safe because the Master eventually hears about
    it. Without this trigger, deferring would just be a quieter way of dropping work.
    """
    def check(now: float):
        items = deferred_store.pending()
        if len(items) < max(1, min_items):
            return None
        newest = max((float(i.get("last_at") or i.get("at") or 0)) for i in items)
        return Notification(
            # Keyed on how many are waiting, so the Master is told again when a NEW one
            # arrives, but not merely because the old ones are still there.
            key=f"deferred_approval:{len(items)}",
            title=f"{len(items)} hành động nền đang chờ bạn duyệt",
            detail=(deferred_store.describe()
                    + f"\nChúng bị hoãn vì không có ai ở máy lúc chúng muốn chạy."),
            action="Xem bằng 'chờ duyệt', rồi ra lệnh lại nếu vẫn muốn làm — "
                   "Ciel không tự chạy lại lệnh cũ trên dữ liệu đã thay đổi.",
            urgency=ASK,
            created_at=max(now, newest),
        )
    return check


# --- Group B: the clock ------------------------------------------------------------

def daily_at(hour: int, minute: int, inner, state: dict = None):
    """Wrap a check so it can only fire once per day, at or after a wall-clock time.

    Lets a scheduled task be expressed as a Trigger like everything else, instead of
    living in a second mechanism with its own semantics. Firing "at or after" rather
    than "exactly at" matters: the engine polls, and a machine that was asleep at 08:00
    should still get its digest when it wakes, not skip the day.
    """
    seen = state if state is not None else {}

    def check(now: float):
        lt = time.localtime(now)
        today = time.strftime("%Y-%m-%d", lt)
        if seen.get("day") == today:
            return None
        if (lt.tm_hour, lt.tm_min) < (hour, minute):
            return None
        seen["day"] = today
        return inner(now)
    return check


def make_digest_check(notifier, max_items: int = 12):
    """Deliver everything that was held back — the other half of the budget promise.

    A finding demoted for budget or for having no action is only "not lost" if something
    eventually reads it out. This drains the queue, so the digest is genuinely a summary
    of what was suppressed rather than a second place for things to rot.
    """
    def check(now: float):
        held = notifier.drain_digest()
        if not held:
            return None
        lines = []
        for h in held[-max_items:]:
            when = time.strftime("%H:%M", time.localtime(h.get("at") or now))
            lines.append(f"  · [{when}] {h.get('title', '?')}"
                         + (f" — {h.get('action')}" if h.get("action") else ""))
        more = f"\n  (và {len(held) - max_items} mục nữa)" if len(held) > max_items else ""
        return Notification(
            key=f"digest:{time.strftime('%Y-%m-%d', time.localtime(now))}",
            title=f"Tóm tắt {len(held)} việc đã được giữ lại",
            detail="\n".join(lines) + more,
            action="Đọc lướt; mục nào cần thì bảo Ciel làm tiếp.",
            urgency=NOTIFY,
            created_at=now,
        )
    return check


# --- Group C: the outside world ----------------------------------------------------
# These cost network calls and can be wrong, so every one of them fires on a threshold
# the Master set explicitly. A condition trigger without a threshold is just a timer.

_PRICE_SPEC_RE = re.compile(r"^\s*([A-Za-z0-9/\-]+)\s*(>=|<=|>|<)\s*([0-9.,]+)\s*$")
_NUM_RE = re.compile(r"[0-9]+(?:[.,][0-9]+)?")


def parse_price_alerts(spec: str) -> list:
    """`"XAU/USD>2400, BTC/USDT<60000"` → [(symbol, op, threshold), …].

    Unparseable entries are dropped rather than guessed at: a misread threshold would
    either fire constantly or never, and both look like the feature working.
    """
    out = []
    for part in (spec or "").split(","):
        m = _PRICE_SPEC_RE.match(part)
        if not m:
            continue
        try:
            out.append((m.group(1).upper(), m.group(2), float(m.group(3).replace(",", ""))))
        except ValueError:
            continue
    return out


def _extract_price(text: str):
    """Pull the first number out of a `fetch_market_price` string, or None."""
    m = _NUM_RE.search((text or "").replace(",", ""))
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def make_price_alert_check(alerts, fetcher=None, state: dict = None):
    """Fire when a price CROSSES a threshold — not while it merely sits past it.

    The distinction is the whole feature. A level check would re-announce "gold is above
    2400" on every poll for as long as it stays there; only the crossing is news. State
    is per-alert and in memory, so a restart re-arms rather than replaying.
    """
    armed = state if state is not None else {}

    def check(now: float):
        if not alerts:
            return None
        if fetcher is None:
            from skills.external.trading_ops import fetch_market_price as f
        else:
            f = fetcher
        for symbol, op, threshold in alerts:
            raw = f(symbol)
            price = _extract_price(raw)
            if price is None:
                continue
            hit = price >= threshold if op == ">=" else \
                  price > threshold if op == ">" else \
                  price <= threshold if op == "<=" else price < threshold
            key = f"{symbol}{op}{threshold}"
            was = armed.get(key)
            armed[key] = hit
            if not hit or was is True:
                continue        # not crossed, or already reported while it stayed true
            if was is None:
                continue        # first observation only arms the alert; it is not a crossing
            return Notification(
                key=f"price_alert:{key}:{int(now // 3600)}",
                title=f"{symbol} vừa vượt ngưỡng bạn đặt",
                detail=f"Giá hiện tại {price:g} {op} {threshold:g}. Nguồn: {raw.strip()[:120]}",
                action=f"Bảo Ciel phân tích {symbol} nếu bạn muốn xem kỹ hơn.",
                urgency=NOTIFY,
                created_at=now,
            )
        return None
    return check


def make_important_sender_check(senders, fetcher=None, state: dict = None):
    """An unread email from someone on a list the Master wrote — not from anyone.

    Filtering by sender is what separates this from a notification for every email, which
    is a thing the Master's own mail client already does better.
    """
    seen = state if state is not None else set()
    wanted = [s.strip().lower() for s in (senders or []) if s.strip()]

    def check(now: float):
        if not wanted:
            return None
        if fetcher is None:
            from .scheduler import _fetch_unread_emails as f
        else:
            f = fetcher
        text = f(10) or ""
        if text.startswith("[Gmail"):
            return None                     # an auth/API error is repeated_failure's job
        for line in text.splitlines():
            low = line.lower()
            who = next((s for s in wanted if s in low), None)
            if not who or line in seen:
                continue
            seen.add(line)
            return Notification(
                key=f"important_email:{who}:{abs(hash(line)) % 10**8}",
                title=f"Mail chưa đọc từ {who}",
                detail=line.strip()[:200],
                action="Bảo Ciel đọc hoặc trả lời nếu cần.",
                urgency=NOTIFY,
                created_at=now,
            )
        return None
    return check


def make_stale_todo_check(todo_path, min_age_days: float = 7.0, max_show: int = 3):
    """Todos that have been open a long time.

    Note what this is NOT: a due-date reminder. `productivity_ops` todos carry only
    `created`, with no due field, so age is the only honest signal available — promising
    deadline reminders on a store that has no deadlines would be a lie in the UI.
    """
    def check(now: float):
        try:
            raw = json.loads(todo_path.read_text(encoding="utf-8")) if todo_path.exists() else []
        except Exception:
            return None
        cutoff = now - min_age_days * 86400.0
        stale = []
        for t in raw if isinstance(raw, list) else []:
            if not isinstance(t, dict) or t.get("done"):
                continue
            try:
                created = time.mktime(time.strptime(str(t.get("created", ""))[:19],
                                                    "%Y-%m-%dT%H:%M:%S"))
            except Exception:
                continue
            if created <= cutoff:
                stale.append((created, t))
        if not stale:
            return None
        stale.sort()
        oldest_days = int((now - stale[0][0]) / 86400.0)
        listing = "\n".join(f"  · #{t.get('id')} {str(t.get('task'))[:70]}"
                            for _, t in stale[:max_show])
        return Notification(
            key=f"stale_todo:{len(stale)}:{int(now // 86400)}",
            title=f"{len(stale)} việc trong todo đã để lâu",
            detail=f"{listing}\nCũ nhất: {oldest_days} ngày (ngưỡng {int(min_age_days)} ngày).",
            action="Bảo Ciel 'complete_todo <id>' nếu xong, hoặc bỏ qua nếu không còn cần.",
            urgency=NOTIFY,
            created_at=now,
        )
    return check


# --- assembly ----------------------------------------------------------------------

def build_triggers(*, enabled_names=None, task_store=None, log_path=None, notifier=None,
                   deferred_store=None, todo_path=None,
                   unfinished_min_age: float = 1800.0,
                   cost_usd_limit: float = 0.0, cost_token_limit: int = 0,
                   failure_threshold: int = 3,
                   price_alerts=(), important_senders=(),
                   stale_todo_days: float = 7.0,
                   digest_hour: int = 8, digest_minute: int = 0) -> list:
    """Build exactly the triggers named in `enabled_names`, and nothing else.

    Opt-in by name rather than a blacklist for the same reason skills are: this list
    will grow, and a default-on trigger added later would start talking without anyone
    choosing it. A trigger whose dependency is missing (no store, no thresholds) is
    dropped silently — asking for `price_alert` with no thresholds configured should be
    a no-op, not a crash at start-up.
    """
    wanted = set(enabled_names or ())
    specs = []

    def add(name, check, every, cooldown, ok=True):
        if name in wanted and ok and check is not None:
            specs.append(Trigger(name=name, check=check, every_seconds=every,
                                 cooldown_seconds=cooldown))

    # Group A — Ciel watching itself.
    # `is not None` everywhere, never a truthiness test: `DeferredStore` defines
    # __len__, so an EMPTY store is falsy — and empty is its normal state. Testing it
    # for truth silently dropped the one trigger whose whole job is to report on it.
    add("unfinished_task",
        make_unfinished_task_check(task_store, unfinished_min_age)
        if task_store is not None else None, 300.0, 6 * 3600.0)
    add("daily_cost",
        make_daily_cost_check(log_path, cost_usd_limit, cost_token_limit)
        if log_path is not None else None, 900.0, 12 * 3600.0)
    add("repeated_failure",
        make_repeated_failure_check(log_path, failure_threshold)
        if log_path is not None else None, 600.0, 2 * 3600.0)
    add("deferred_approval",
        make_deferred_approval_check(deferred_store)
        if deferred_store is not None else None, 300.0, 3 * 3600.0)

    # Group B — the clock.
    add("digest",
        daily_at(digest_hour, digest_minute, make_digest_check(notifier))
        if notifier is not None else None, 300.0, 12 * 3600.0)
    if "morning_digest" in wanted:
        # Imported lazily: scheduler.py pulls in Gmail/trading/Worker, none of which
        # should be loaded just to build a trigger list that may not include this one.
        from .scheduler import make_morning_digest_check
        add("morning_digest",
            daily_at(digest_hour, digest_minute, make_morning_digest_check()),
            300.0, 12 * 3600.0)

    # Group C — the outside world, each behind a threshold the Master set.
    add("price_alert", make_price_alert_check(list(price_alerts)) if price_alerts else None,
        600.0, 1800.0)
    add("important_email",
        make_important_sender_check(list(important_senders)) if important_senders else None,
        900.0, 1800.0)
    add("stale_todo",
        make_stale_todo_check(todo_path, stale_todo_days) if todo_path is not None else None,
        6 * 3600.0, 24 * 3600.0)

    return specs


def build_group_a(task_store, log_path, *, enabled_names=None,
                  unfinished_min_age: float = 1800.0,
                  cost_usd_limit: float = 0.0, cost_token_limit: int = 0,
                  failure_threshold: int = 3) -> list:
    """Back-compat shim for the Group-A-only call site. Prefer `build_triggers`."""
    return build_triggers(enabled_names=enabled_names, task_store=task_store,
                          log_path=log_path, unfinished_min_age=unfinished_min_age,
                          cost_usd_limit=cost_usd_limit, cost_token_limit=cost_token_limit,
                          failure_threshold=failure_threshold)
