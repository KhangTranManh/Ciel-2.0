"""task_state.py — Tier-2: a durable record of what Ciel is actually doing.

WHAT THIS FIXES
---------------
Before this, the only cross-turn state was ONE slot holding ONE action awaiting
confirmation. There was no notion of "this job is 3 of 7 steps in", so:
  * a request interrupted by a crash, a restart or a closed terminal vanished with no
    trace the user could act on;
  * "what were you doing?" could only be answered by the model guessing, since the
    Router never sees chat_history;
  * a multi-step job gave no visible progress — a wall of silence, then one blob.

WHAT IT DELIBERATELY IS NOT
---------------------------
Not a scheduler, not a queue, not a priority system. One active task at a time, matching
how the pending-confirmation slot already works and how a person actually converses.
The Brain never reads these records: feeding them into routing would re-open the context
problem this codebase deliberately closed, and cost tokens on every turn. They exist for
the Master and for resumption, and every decision here is made in plain Python.

THREAD SAFETY
-------------
`_run_steps` executes independent steps concurrently (see core/parallel.py), so several
threads append steps to the same record. Every mutation here holds a lock; without it
steps are lost to a read-modify-write race exactly as the log counters were.
"""
import json
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict

from core.continuation import _FAILED_STEP_RE


_SUMMARY_MAXLEN = 240      # keep the file small; the full result lives in thoughts.log
_MAX_RECORDS = 20          # rolling history, newest first


@dataclass
class TaskStep:
    n: int
    tool: str
    status: str            # done | failed | cancelled
    summary: str
    at: float = field(default_factory=time.time)


@dataclass
class TaskRecord:
    id: str
    goal: str
    status: str            # active | done | blocked | failed | interrupted | cancelled
    steps: list = field(default_factory=list)
    note: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def done_count(self) -> int:
        return sum(1 for s in self.steps if s.get("status") == "done")

    def describe(self) -> str:
        """One line a human can act on."""
        total, done = len(self.steps), self.done_count()
        age = max(0, int(time.time() - self.updated_at))
        when = f"{age // 60}m ago" if age >= 60 else f"{age}s ago"
        head = self.goal.strip().replace("\n", " ")
        if len(head) > 90:
            head = head[:90] + "…"
        tail = f" — {self.note}" if self.note else ""
        return f"[{self.status}] \"{head}\" · {done}/{total} step(s) · last activity {when}{tail}"


class TaskStore:
    """Owns TaskRecords and their file. All public methods are thread-safe."""

    def __init__(self, path, max_records: int = _MAX_RECORDS):
        self.path = path
        self.max_records = max_records
        self._lock = threading.RLock()
        self._records = []          # newest first
        self._active = None
        self._load()

    # ---------------------------------------------------------------- io
    def _load(self):
        """Read history. Any record still marked `active` must be from a process that
        is no longer running — this constructor only runs at start-up — so it is
        reclassified as `interrupted`, which is what makes resumption possible."""
        try:
            if not self.path.exists():
                return
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            for r in (raw if isinstance(raw, list) else []):
                if not isinstance(r, dict) or "id" not in r:
                    continue
                if r.get("status") == "active":
                    r["status"] = "interrupted"
                    r["note"] = r.get("note") or "stopped before finishing (process ended)"
                self._records.append(TaskRecord(**{
                    k: r.get(k) for k in ("id", "goal", "status", "steps", "note",
                                          "created_at", "updated_at")}))
        except Exception:
            self._records = []      # a corrupt history must never block start-up

    def _save_locked(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = [asdict(r) for r in self._records[: self.max_records]]
            self.path.write_text(json.dumps(payload, ensure_ascii=False, default=str),
                                 encoding="utf-8")
        except Exception:
            pass                    # persistence is best-effort; never break the request

    # ------------------------------------------------------------ mutation
    def start(self, goal: str) -> TaskRecord:
        """Open a task. Any previous active one is closed as superseded — one at a time."""
        with self._lock:
            if self._active and self._active.status == "active":
                self._active.status = "done"
                self._active.note = self._active.note or "superseded by a newer request"
            rec = TaskRecord(id=uuid.uuid4().hex[:10], goal=goal or "", status="active")
            self._records.insert(0, rec)
            del self._records[self.max_records:]
            self._active = rec
            self._save_locked()
            return rec

    def record_step(self, tool: str, result: str):
        """Append one executed step. Called from parallel worker threads."""
        with self._lock:
            if not self._active:
                return
            text = (result or "")
            # Was: only "[TOOL_ERROR"/"[EXECUTION_ERROR" prefixes counted as failed — any
            # other error_code (e.g. vision_act's "[VISION_LOOP_ERROR]") OR the friendly
            # "Sorry, ..." rephrase that execute_tool returns for a genuine failure both
            # read as "done". Reuse the same canonical failure pattern continuation.py
            # already uses for resuming a broken plan, instead of a second, narrower guess.
            status = "cancelled" if text.startswith("[CANCELLED") else (
                "failed" if _FAILED_STEP_RE.match(text) else "done")
            self._active.steps.append(asdict(TaskStep(
                n=len(self._active.steps) + 1,
                tool=tool or "?",
                status=status,
                summary=text.replace("\n", " ")[:_SUMMARY_MAXLEN],
            )))
            self._active.updated_at = time.time()
            self._save_locked()

    def finish(self, status: str = "done", note: str = ""):
        with self._lock:
            if not self._active:
                return
            self._active.status = status
            if note:
                self._active.note = note
            self._active.updated_at = time.time()
            self._save_locked()
            self._active = None

    # ------------------------------------------------------------- queries
    @property
    def active(self):
        return self._active

    def unfinished(self):
        """The most recent record that stopped without completing, or None."""
        with self._lock:
            for r in self._records:
                if r.status in ("interrupted", "blocked"):
                    return r
            return None

    def recent(self, limit: int = 5) -> list:
        with self._lock:
            return list(self._records[:limit])
