"""notifier.py — Tier-6a: whether a proactive message goes out, and where to.

WHAT THIS FIXES
---------------
Before this, the only proactive path was `scheduler._morning_digest`, which printed to
stdout and pushed to Telegram unconditionally. That has three problems:

  * it ignores whether anyone is actually there — a CLI left running overnight is not a
    reader, so a message "delivered" to that terminal is a message lost;
  * every notification is equally loud, so the only defence against noise is to send
    less, which is the same as not being proactive;
  * nothing dedupes. A condition that stays true (a task stuck at 3/7 steps) would be
    re-announced on every poll, which trains the Master to ignore the channel.

The routing rule here is deliberately about LIVENESS, not configuration: try the richest
channel that a human can plausibly be watching right now, and fall through when it fails.

THE MESSAGE CONTRACT
--------------------
A notification must say what happened, why it is being raised (which trigger, which
threshold, with numbers), and ONE concrete thing the Master can do. The third part is
enforced in code: a Notification with no `action` is demoted to the digest and can never
interrupt. "FYI" messages are exactly what makes an assistant tiresome, so the type
system refuses to carry them at interrupt urgency.

WHY EVERYTHING TAKES `now`
--------------------------
Cooldowns, daily budgets and escalation are all time-based, so every decision function
takes the current time as a parameter and never calls time.time() internally. That is
what lets a whole day of proactive behaviour be simulated in a unit test with zero LLM
calls and zero waiting — the same property that made Tiers 1-3 testable.

DELIBERATELY NOT HERE
---------------------
No LLM. This module decides *whether* and *where*; composing the words is the trigger's
job (and for Group-A triggers, that too is plain Python). Nothing here imports from
llm_connector, so a proactive path can never re-enter the agent by accident.
"""
import json
import threading
import time
from dataclasses import dataclass, field


# --- Urgency levels ----------------------------------------------------------------
# SILENT never interrupts; it accumulates for the next digest.
# NOTIFY interrupts once, needs no reply.
# ASK expects a decision and escalates to a fallback channel if the Master never shows up.
SILENT = "silent"
NOTIFY = "notify"
ASK = "ask"

_DIGEST_MAX = 40            # keep the state file small; the digest is a summary, not a log
_TITLE_MAX = 120


@dataclass
class Notification:
    """One proactive message. `key` is its stable identity across polls.

    Two different occurrences of the same condition must share a key (so the cooldown
    suppresses the repeat) while genuinely different occurrences must not (so a real new
    event is never swallowed). Getting this wrong in either direction is the difference
    between a useful assistant and a noisy one, so triggers build keys from the identity
    of the *thing* — a task id, a tool name, a date — never from a formatted message.
    """
    key: str
    title: str                      # what happened
    detail: str = ""                # why you're hearing it: trigger + threshold + numbers
    action: str = ""                # the ONE thing the Master can do; empty => digest-only
    urgency: str = NOTIFY
    trigger: str = ""
    created_at: float = field(default_factory=time.time)

    def effective_urgency(self) -> str:
        """An actionless message cannot interrupt, whatever the trigger asked for.

        This is the deterministic half of the message contract. A trigger author who
        forgets `action` gets a quieter assistant, not a louder one — the failure mode
        points the safe way.
        """
        if not self.action.strip():
            return SILENT
        return self.urgency if self.urgency in (SILENT, NOTIFY, ASK) else NOTIFY

    def render(self) -> str:
        parts = [f"[Ciel] {self.title.strip()[:_TITLE_MAX]}"]
        if self.detail.strip():
            parts.append(self.detail.strip())
        if self.action.strip():
            parts.append(f"→ {self.action.strip()}")
        return "\n".join(parts)


@dataclass
class Outcome:
    status: str                     # delivered | digest | suppressed
    channel: str = ""
    reason: str = ""


class Presence:
    """Is the Master actually here?

    A live process is not a live human. `touch()` is called on every real interaction;
    anything past `idle_threshold` counts as absent, which routes notifications to a
    channel that reaches someone away from the machine.
    """

    def __init__(self, idle_threshold: float = 600.0, now: float = None):
        self.idle_threshold = float(idle_threshold)
        self._lock = threading.Lock()
        # Start "present": whoever launched the process was at the keyboard a moment ago.
        self._last_seen = time.time() if now is None else now

    def touch(self, now: float = None):
        with self._lock:
            self._last_seen = time.time() if now is None else now

    def idle_seconds(self, now: float) -> float:
        with self._lock:
            return max(0.0, now - self._last_seen)

    def is_present(self, now: float) -> bool:
        return self.idle_seconds(now) < self.idle_threshold


# --- Channels ----------------------------------------------------------------------
# A channel answers two questions: can a human read me right now, and did the send work.
# Both are best-effort and must never raise into the engine.

class Channel:
    name = "?"

    def is_live(self, now: float) -> bool:
        raise NotImplementedError

    def send(self, note: Notification, now: float) -> bool:
        raise NotImplementedError


class CliChannel(Channel):
    """The terminal — live only while someone is demonstrably at it.

    `main.py` blocks in input(), so printing from the scheduler thread lands in the
    middle of a half-typed line. NOTIFY messages are therefore queued and flushed just
    before the next prompt is drawn, where they cost nothing. ASK is shown immediately:
    a question the Master never sees is worse than a scrambled input line.
    """
    name = "cli"

    def __init__(self, presence: Presence, printer=print):
        self.presence = presence
        self._printer = printer
        self._lock = threading.Lock()
        self._queue = []

    def is_live(self, now: float) -> bool:
        return self.presence.is_present(now)

    def send(self, note: Notification, now: float) -> bool:
        try:
            if note.effective_urgency() == ASK:
                self._printer("\n" + note.render())
            else:
                with self._lock:
                    self._queue.append(note)
            return True
        except Exception:
            return False

    def drain(self) -> list:
        """Hand over queued messages; the caller prints them where it is safe to."""
        with self._lock:
            out, self._queue = self._queue, []
            return out


class AppChannel(Channel):
    """The desktop/browser UI. Richest channel — it can render a real approve button —
    so it sits first when a socket is attached. Wired through callables so this module
    stays free of FastAPI."""
    name = "app"

    def __init__(self, is_live_fn, send_fn):
        self._is_live_fn = is_live_fn
        self._send_fn = send_fn

    def is_live(self, now: float) -> bool:
        try:
            return bool(self._is_live_fn())
        except Exception:
            return False

    def send(self, note: Notification, now: float) -> bool:
        try:
            return bool(self._send_fn(note))
        except Exception:
            return False


class TelegramChannel(Channel):
    """The fallback. Needs no presence — that is the entire point of it."""
    name = "telegram"

    def __init__(self, sender=None, configured=None):
        self._sender = sender
        self._configured = configured

    def _resolve(self):
        if self._sender is None:
            from skills.external.telegram_ops import send_telegram_message
            self._sender = send_telegram_message
        return self._sender

    def is_live(self, now: float) -> bool:
        if self._configured is not None:
            try:
                return bool(self._configured())
            except Exception:
                return False
        import os
        return bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"))

    def send(self, note: Notification, now: float) -> bool:
        try:
            return bool(self._resolve()(note.render()))
        except Exception:
            return False


# --- The notifier ------------------------------------------------------------------

class Notifier:
    """Routes notifications, and refuses to send most of them.

    Suppression order matters and is checked cheapest-first: contract, then cooldown,
    then budget. Budget is checked last so a duplicate never burns a slot the Master
    would rather spend on a genuinely new event.
    """

    def __init__(self, state_path=None, channels=(), daily_budget: int = 8,
                 default_cooldown: float = 3600.0, ask_escalate_seconds: float = 1800.0,
                 fallback_channel: str = "telegram", repeat_limit: int = 4,
                 repeat_reset_seconds: float = 7 * 86400.0):
        self.state_path = state_path
        self.channels = list(channels)
        self.daily_budget = int(daily_budget)
        self.default_cooldown = float(default_cooldown)
        self.ask_escalate_seconds = float(ask_escalate_seconds)
        self.fallback_channel = fallback_channel
        # After this many interrupts about the SAME key, stop interrupting about it.
        self.repeat_limit = int(repeat_limit)
        self.repeat_reset_seconds = float(repeat_reset_seconds)
        self._lock = threading.RLock()
        self._last_sent = {}        # key -> ts of the last interrupt for that key
        self._digest = []           # notifications waiting for the next digest
        self._pending_ack = {}      # key -> {"at": ts, "note": {...}} for ASK escalation
        self._repeats = {}          # key -> [times interrupted, last interrupt ts]
        self._day = ""
        self._count = 0             # interrupts sent today
        self._load()

    # ---------------------------------------------------------------- io
    @staticmethod
    def _day_of(now: float) -> str:
        return time.strftime("%Y-%m-%d", time.localtime(now))

    def _load(self):
        try:
            if not self.state_path or not self.state_path.exists():
                return
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return
            self._last_sent = {k: float(v) for k, v in (raw.get("last_sent") or {}).items()
                               if isinstance(v, (int, float))}
            self._digest = [d for d in (raw.get("digest") or []) if isinstance(d, dict)]
            self._pending_ack = {k: v for k, v in (raw.get("pending_ack") or {}).items()
                                 if isinstance(v, dict)}
            self._repeats = {k: [int(v[0]), float(v[1])]
                             for k, v in (raw.get("repeats") or {}).items()
                             if isinstance(v, (list, tuple)) and len(v) == 2}
            self._day = raw.get("day") or ""
            self._count = int(raw.get("count") or 0)
        except Exception:
            # A corrupt state file must not silence notifications, and must not crash
            # start-up. Losing cooldown history costs at most one duplicate message.
            self._last_sent, self._digest, self._pending_ack = {}, [], {}
            self._repeats = {}
            self._day, self._count = "", 0

    def _save_locked(self):
        try:
            if not self.state_path:
                return
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps({
                "last_sent": self._last_sent,
                "digest": self._digest[-_DIGEST_MAX:],
                "pending_ack": self._pending_ack,
                "repeats": self._repeats,
                "day": self._day,
                "count": self._count,
            }, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass                    # persistence is best-effort, exactly as TaskStore

    def _roll_day_locked(self, now: float):
        today = self._day_of(now)
        if self._day != today:
            self._day, self._count = today, 0

    # ------------------------------------------------------------- delivery
    def deliver(self, note: Notification, now: float, cooldown: float = None) -> Outcome:
        """Decide and route. Never raises — a failing notifier must not break a trigger."""
        try:
            return self._deliver_inner(note, now, cooldown)
        except Exception as e:
            return Outcome("suppressed", reason=f"notifier_error: {type(e).__name__}: {e}")

    def _deliver_inner(self, note: Notification, now: float, cooldown) -> Outcome:
        with self._lock:
            self._roll_day_locked(now)
            urgency = note.effective_urgency()

            # 1. Contract: no action means no interrupt, ever.
            if urgency == SILENT:
                self._push_digest_locked(note)
                self._save_locked()
                return Outcome("digest", reason="no action declared")

            # 2. Cooldown: the same condition re-observed is not a new event. This is
            #    what turns a level (the task is still stuck) into an edge (it just
            #    became stuck), and it is the single most important anti-noise rule.
            window = self.default_cooldown if cooldown is None else float(cooldown)
            last = self._last_sent.get(note.key)
            if last is not None and (now - last) < window:
                left = int(window - (now - last))
                return Outcome("suppressed", reason=f"cooldown {left}s left")

            # 3. Feedback. If the same finding has interrupted this many times and is
            #    STILL being raised, the condition has outlived the Master's interest in
            #    it — a stuck task they have decided not to finish, a threshold that no
            #    longer means anything. Repeating it further only teaches them to ignore
            #    the channel, so it goes quiet without going away. Muting the KEY rather
            #    than the trigger is deliberate: this stuck task falls silent, a
            #    different one still gets through.
            if self._is_muted_locked(note.key, now):
                self._push_digest_locked(note)
                self._save_locked()
                return Outcome("digest",
                               reason=f"muted after {self.repeat_limit} unheeded repeats")

            # 4. Budget. Deterministic, counted in Python — never a request that the
            #    model restrain itself. Over budget, the message still survives, it
            #    just stops being an interruption.
            if self._count >= self.daily_budget:
                self._push_digest_locked(note)
                self._save_locked()
                return Outcome("digest", reason=f"daily budget {self.daily_budget} spent")

            # 5. Route: first live channel that actually accepts it.
            for ch in self.channels:
                if not ch.is_live(now):
                    continue
                if ch.send(note, now):
                    self._last_sent[note.key] = now
                    self._count += 1
                    self._bump_repeat_locked(note.key, now)
                    if urgency == ASK:
                        self._pending_ack[note.key] = {
                            "at": now, "channel": ch.name, "text": note.render(),
                        }
                    self._save_locked()
                    return Outcome("delivered", channel=ch.name)

            # 6. Nobody home and no fallback worked — hold it rather than drop it.
            self._push_digest_locked(note)
            self._save_locked()
            return Outcome("digest", reason="no live channel")

    # ------------------------------------------------------------- feedback
    def _is_muted_locked(self, key: str, now: float) -> bool:
        rec = self._repeats.get(key)
        if not rec:
            return False
        count, last = rec
        # A long quiet spell means the condition went away and came back — that is a new
        # event, not the old one nagging, so the count starts over.
        if (now - last) > self.repeat_reset_seconds:
            self._repeats.pop(key, None)
            return False
        return count >= self.repeat_limit

    def _bump_repeat_locked(self, key: str, now: float):
        rec = self._repeats.get(key)
        if rec and (now - rec[1]) <= self.repeat_reset_seconds:
            self._repeats[key] = [rec[0] + 1, now]
        else:
            self._repeats[key] = [1, now]

    def muted_keys(self, now: float) -> list:
        with self._lock:
            return [k for k in list(self._repeats) if self._is_muted_locked(k, now)]

    def unmute(self, key: str) -> bool:
        """Let a silenced finding speak again — the Master changed their mind."""
        with self._lock:
            if self._repeats.pop(key, None) is None:
                return False
            self._save_locked()
            return True

    def _push_digest_locked(self, note: Notification):
        self._digest.append({
            "key": note.key, "title": note.title, "detail": note.detail,
            "action": note.action, "trigger": note.trigger, "at": note.created_at,
        })
        del self._digest[:-_DIGEST_MAX]

    # ------------------------------------------------------------ escalation
    def escalate_stale(self, now: float) -> list:
        """Re-route unanswered ASKs to the fallback channel.

        A question shown to an empty terminal is not a question. Escalation exists for
        exactly one case: the Master walked away between the prompt and the answer.
        """
        sent = []
        with self._lock:
            for key, rec in list(self._pending_ack.items()):
                at = float(rec.get("at") or 0)
                if (now - at) < self.ask_escalate_seconds:
                    continue
                if rec.get("channel") == self.fallback_channel:
                    self._pending_ack.pop(key, None)   # already there; nowhere left to go
                    continue
                for ch in self.channels:
                    if ch.name != self.fallback_channel or not ch.is_live(now):
                        continue
                    note = Notification(
                        key=key, title="Còn một câu chưa trả lời",
                        detail=str(rec.get("text") or ""),
                        action="Trả lời khi bạn quay lại.", urgency=NOTIFY,
                        trigger="escalation", created_at=now)
                    if ch.send(note, now):
                        sent.append(key)
                    break
                self._pending_ack.pop(key, None)
            if sent:
                self._save_locked()
        return sent

    def ack_seen(self, now: float = None):
        """Clear pending ASKs because the Master was demonstrably at the keyboard.

        This records that the question was *seen*, not that it was *answered* — which is
        all escalation needs to know, since escalation only ever chases an absent human.
        """
        with self._lock:
            if not self._pending_ack:
                return
            self._pending_ack.clear()
            self._save_locked()

    # --------------------------------------------------------------- digest
    def drain_digest(self) -> list:
        """Take everything held back. The digest trigger owns what to do with it."""
        with self._lock:
            out, self._digest = list(self._digest), []
            self._save_locked()
            return out

    def peek_digest(self) -> list:
        with self._lock:
            return list(self._digest)

    def budget_left(self, now: float) -> int:
        with self._lock:
            self._roll_day_locked(now)
            return max(0, self.daily_budget - self._count)

    def drain_cli(self) -> list:
        """Convenience for the CLI entry point: rendered strings ready to print."""
        for ch in self.channels:
            if isinstance(ch, CliChannel):
                return [n.render() for n in ch.drain()]
        return []
