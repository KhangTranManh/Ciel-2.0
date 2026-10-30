"""Private durable run history. Never feeds historical actions back into routing.

One Ciel process owns this store. Connections are short-lived and each mutation,
including its event, is atomic. Startup marks unfinished work interrupted; it
never replays an action or restores consent from disk.
"""
from __future__ import annotations

import json
import hashlib
import math
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .models import Lane, RunState, TERMINAL_STATES


class RunStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, goal TEXT NOT NULL, lane TEXT NOT NULL,
                    state TEXT NOT NULL, reason TEXT NOT NULL,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS steps (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
                    tool TEXT NOT NULL, args TEXT NOT NULL, state TEXT NOT NULL,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS artifacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES runs(id),
                    step_id TEXT REFERENCES steps(id), kind TEXT NOT NULL,
                    content TEXT NOT NULL, created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES runs(id),
                    kind TEXT NOT NULL, payload TEXT NOT NULL, created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS steps_run ON steps(run_id);
                CREATE INDEX IF NOT EXISTS events_run ON events(run_id, id);
                CREATE TABLE IF NOT EXISTS harness_meta (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL
                );
            """)

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _event(db, run_id: str, kind: str, payload: dict):
        db.execute("INSERT INTO events(run_id,kind,payload,created_at) VALUES(?,?,?,?)",
                   (run_id, kind, json.dumps(payload, ensure_ascii=False), time.time()))

    def create(self, goal: str, lane: Lane, reason: str) -> str:
        run_id, now = uuid.uuid4().hex, time.time()
        with self._connection() as db:
            db.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?)",
                       (run_id, goal, lane.value, RunState.PLANNED.value, reason, now, now))
            self._event(db, run_id, "created", {"lane": lane.value, "reason": reason})
        return run_id

    def transition(self, run_id: str, state: RunState):
        with self._connection() as db:
            row = db.execute("SELECT state FROM runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                raise KeyError(run_id)
            before = RunState(row["state"])
            if before == state:
                return
            allowed = (
                before == RunState.PLANNED and state in ({RunState.RUNNING} | TERMINAL_STATES)
                or before == RunState.RUNNING and state in ({RunState.WAITING} | TERMINAL_STATES)
                or before == RunState.WAITING and state in {RunState.RUNNING, RunState.CANCELLED, RunState.INTERRUPTED}
            )
            if not allowed:
                raise ValueError(f"Invalid run transition: {before.value} -> {state.value}")
            db.execute("UPDATE runs SET state=?,updated_at=? WHERE id=?", (state.value, time.time(), run_id))
            self._event(db, run_id, "state", {"from": before.value, "to": state.value})

    def start_step(self, run_id: str, tool: str, args: dict) -> str:
        step_id, now = uuid.uuid4().hex, time.time()
        with self._connection() as db:
            row = db.execute("SELECT state FROM runs WHERE id=?", (run_id,)).fetchone()
            if not row or row["state"] != RunState.RUNNING.value:
                raise ValueError("Steps require a running run")
            db.execute("INSERT INTO steps VALUES(?,?,?,?,?,?,?)",
                       (step_id, run_id, tool, json.dumps(args, ensure_ascii=False), "running", now, now))
            self._event(db, run_id, "step_started", {"step_id": step_id, "tool": tool})
        return step_id

    def escalate(self, run_id: str, reason: str):
        with self._connection() as db:
            changed = db.execute("UPDATE runs SET lane=?,reason=?,updated_at=? WHERE id=? AND state='running'",
                                 (Lane.HARNESS.value, reason, time.time(), run_id)).rowcount
            if changed != 1:
                raise ValueError("Only a running run can escalate")
            self._event(db, run_id, "escalated", {"lane": Lane.HARNESS.value, "reason": reason})

    def resolve_failure(self, run_id: str, step_id: str):
        # Preserve the original step and artifact. Recovery is an additional event,
        # so an eventual completion never erases the evidence of a failed attempt.
        with self._connection() as db:
            row = db.execute("SELECT state FROM steps WHERE id=? AND run_id=?", (step_id, run_id)).fetchone()
            if not row or row["state"] != "failed":
                raise ValueError("Only a failed step can be recovered")
            self._event(db, run_id, "step_recovered", {"step_id": step_id})

    @staticmethod
    def _pending_key(pending: dict) -> str:
        identity = json.dumps({"tool": pending.get("tool"), "args": pending.get("args") or {}},
                              sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()

    def note_pending(self, run_id: str, pending: dict):
        with self._connection() as db:
            self._event(db, run_id, "awaiting_confirmation", {"action_key": self._pending_key(pending)})

    def resolve_pending(self, pending: dict, *, cancelled: bool, followup_run: str | None = None):
        """Link explicit reissue/cancellation without persisting permission grants."""
        action_key = self._pending_key(pending)
        with self._connection() as db:
            rows = db.execute("SELECT e.run_id,e.payload FROM events e JOIN runs r ON r.id=e.run_id "
                              "WHERE r.state='waiting_for_approval' AND e.kind='awaiting_confirmation'").fetchall()
            for row in rows:
                if json.loads(row["payload"]).get("action_key") != action_key:
                    continue
                state = "cancelled" if cancelled else "interrupted"
                db.execute("UPDATE runs SET state=?,updated_at=? WHERE id=?", (state, time.time(), row["run_id"]))
                self._event(db, row["run_id"], "pending_resolved", {
                    "cancelled": cancelled, "followup_run": followup_run, "approval_inherited": False})

    def finish_step(self, run_id: str, step_id: str, state: str, output: str, cap: int):
        if state not in {"completed", "failed", "cancelled", "waiting_for_approval"}:
            raise ValueError("Invalid step outcome")
        with self._connection() as db:
            changed = db.execute(
                "UPDATE steps SET state=?,updated_at=? WHERE id=? AND run_id=? AND state='running'",
                (state, time.time(), step_id, run_id)).rowcount
            if changed != 1:
                raise ValueError("Step is missing or already finished")
            db.execute("INSERT INTO artifacts(run_id,step_id,kind,content,created_at) VALUES(?,?,?,?,?)",
                       (run_id, step_id, "tool_result", str(output)[:cap], time.time()))
            self._event(db, run_id, "step_finished", {"step_id": step_id, "state": state})

    def recover_interrupted(self) -> int:
        """Call once on runtime startup, never on an ordinary read/API request."""
        with self._connection() as db:
            ids = [row[0] for row in db.execute(
                "SELECT id FROM runs WHERE state IN ('planned','running','waiting_for_approval')")]
            for run_id in ids:
                db.execute("UPDATE runs SET state='interrupted',updated_at=? WHERE id=?", (time.time(), run_id))
                db.execute("UPDATE steps SET state='interrupted',updated_at=? WHERE run_id=? AND state='running'",
                           (time.time(), run_id))
                self._event(db, run_id, "interrupted", {"automatic_replay": False})
        return len(ids)

    def get(self, run_id: str) -> dict | None:
        with self._connection() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                return None
            result = dict(row)
            result["steps"] = [dict(r) for r in db.execute("SELECT * FROM steps WHERE run_id=? ORDER BY rowid", (run_id,))]
            result["events"] = [dict(r) for r in db.execute("SELECT * FROM events WHERE run_id=? ORDER BY id", (run_id,))]
            result["artifacts"] = [dict(r) for r in db.execute("SELECT * FROM artifacts WHERE run_id=? ORDER BY id", (run_id,))]
            return result

    def recent(self, limit: int = 5) -> list[dict]:
        with self._connection() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 100)),))]

    def import_legacy_tasks(self, path: Path) -> int:
        """Copy historical summaries once; never mutate the JSON or replay a step."""
        try:
            records = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return 0
        if not isinstance(records, list):
            return 0
        count = 0
        states = {"done": "completed", "failed": "failed", "cancelled": "cancelled"}
        with self._connection() as db:
            if db.execute("SELECT 1 FROM harness_meta WHERE key='legacy_tasks_imported'").fetchone():
                return 0
            for record in records[:100]:
                if not isinstance(record, dict) or not isinstance(record.get("id"), str):
                    continue
                run_id = "legacy-" + record["id"]
                try:
                    created = float(record.get("created_at") or time.time())
                    updated = float(record.get("updated_at") or created)
                except (TypeError, ValueError):
                    continue
                if not math.isfinite(created) or not math.isfinite(updated):
                    continue
                changed = db.execute("INSERT OR IGNORE INTO runs VALUES(?,?,?,?,?,?,?)", (
                    run_id, str(record.get("goal") or "")[:8000], Lane.HARNESS.value,
                    states.get(str(record.get("status")), "interrupted"), "legacy_history", created, updated,
                )).rowcount
                if not changed:
                    continue
                count += 1
                self._event(db, run_id, "legacy_import", {"automatic_replay": False})
                old_steps = record.get("steps")
                for index, step in enumerate(old_steps if isinstance(old_steps, list) else []):
                    if not isinstance(step, dict):
                        continue
                    step_id = f"{run_id}-{index}"
                    db.execute("INSERT INTO steps VALUES(?,?,?,?,?,?,?)", (
                        step_id, run_id, str(step.get("tool") or "unknown"), "{}",
                        states.get(str(step.get("status")), "interrupted"), created, updated))
                    db.execute("INSERT INTO artifacts(run_id,step_id,kind,content,created_at) VALUES(?,?,?,?,?)", (
                        run_id, step_id, "legacy_summary", str(step.get("summary") or "")[:240], updated))
            db.execute("INSERT INTO harness_meta VALUES('legacy_tasks_imported','1')")
        return count
