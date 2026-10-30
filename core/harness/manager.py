"""Own run identity, persistence and hard limits around the shared tool boundary.

Planning, permissions, synthesis and recovery use the extracted Ciel services.
The manager adds no LLM call to classification or verification. Fast chat creates
no database row unless it escalates to real tool work.
"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field

from .classifier import classify
from .models import Lane, LaneDecision, RunPolicy, RunState
from .store import RunStore
from .verifier import step_outcome


@dataclass
class RunContext:
    goal: str
    selection: LaneDecision
    id: str | None = None
    started: float = field(default_factory=time.monotonic)
    steps: int = 0
    outcomes: list[str] = field(default_factory=list)
    failures: dict[str, str] = field(default_factory=dict)
    limited: bool = False
    lock: object = field(default_factory=threading.RLock)


class RunManager:
    def __init__(self, core, store: RunStore, policy: RunPolicy | None = None):
        self.core = core
        self.store = store
        self.policy = policy or RunPolicy()
        self._local = threading.local()
        self.last_decision: LaneDecision | None = None
        self.last_run_id: str | None = None
        self.last_turn_state: RunState | None = None

    @property
    def active(self) -> RunContext | None:
        return getattr(self._local, "active", None)

    @contextmanager
    def bind(self, run):
        """Explicitly propagate run identity to a parallel tool worker."""
        previous = self.active
        self._local.active = run
        try:
            yield
        finally:
            self._local.active = previous

    def _persist_start(self, run):
        if run.id is None:
            run.id = self.store.create(run.goal, run.selection.lane, run.selection.reason)
            self.store.transition(run.id, RunState.RUNNING)
            self.last_run_id = run.id

    def execute(self, goal: str, decision: dict, operation):
        if self.active is not None:
            raise RuntimeError("Nested run dispatch is not supported")
        selection = classify(goal, decision, known_tools=self.core._tool_map,
                             risky_tools=self.core._HIGH_RISK_TOOLS,
                             unattended=self.core.unattended)
        self.last_decision = selection
        self.last_turn_state = None
        run = RunContext(goal, selection)
        self.core._log_thought("RUN", "lane_selected", f"lane={selection.lane.value} reason={selection.reason}")
        # Persist before any side effect. A database failure never triggers a
        # second execution of the legacy path: the caller reports one error.
        if selection.lane != Lane.FAST:
            self._persist_start(run)
        with self.bind(run):
            try:
                response = operation()
                if run.id:
                    if self.core.cancelled or "cancelled" in run.outcomes or step_outcome(response) == "cancelled":
                        state = RunState.CANCELLED
                    elif self.core._pending_action:
                        state = RunState.WAITING
                    elif run.limited:
                        state = RunState.LIMITED
                    elif run.failures or step_outcome(response) == "failed":
                        state = RunState.FAILED
                    else:
                        state = RunState.COMPLETED
                    self.store.transition(run.id, state)
                    if state == RunState.WAITING:
                        self.store.note_pending(run.id, self.core._pending_action)
                    self.last_turn_state = state
                    self.core._log_thought("RUN", "finished", f"id={run.id} state={state.value} steps={run.steps}")
                if run.limited:
                    return str(response or "") + "\n\n[RUN_LIMIT] Work stopped at the configured step/time limit."
                return response
            except BaseException:
                if run.id and self.last_turn_state is None:
                    self.store.transition(run.id, RunState.INTERRUPTED)
                    self.last_turn_state = RunState.INTERRUPTED
                raise

    @contextmanager
    def approval_wait(self):
        """Expose an in-process approval wait; no answer/consent is stored."""
        run = self.active
        if run is None or run.id is None:
            yield
            return
        self.store.transition(run.id, RunState.WAITING)
        try:
            yield
        finally:
            # Only this live callback returns to running. Startup always turns an
            # abandoned wait into interrupted, and never consumes a stored grant.
            self.store.transition(run.id, RunState.RUNNING)

    def track_step(self, tool: str, args: dict, operation, *, evidence=None):
        run = self.active
        if run is None:
            return operation()
        with run.lock:
            if self.core.cancelled:
                run.outcomes.append("cancelled")
                return "[CANCELLED] No additional step was started."
            if run.steps >= self.policy.max_steps or time.monotonic() - run.started >= self.policy.max_seconds:
                run.limited = True
                return "[RUN_LIMIT] No additional step was started."
            if run.selection.lane == Lane.FAST or (run.selection.lane == Lane.SINGLE and run.steps):
                # A failed single step or chat knowledge gap can escalate, but it
                # never restarts the original action or discards collected evidence.
                self.escalate("runtime_escalation")
            self._persist_start(run)
            run.steps += 1
            step_id = self.store.start_step(run.id, tool, args)
        try:
            output = operation()
            raw = evidence() if evidence else output
            status = step_outcome(raw or output, pending=bool(self.core._pending_action))
            self.store.finish_step(run.id, step_id, status, raw or output, self.policy.max_artifact_chars)
            with run.lock:
                run.outcomes.append(status)
                if status == "failed":
                    run.failures[step_id] = tool
            return output
        except BaseException:
            self.store.finish_step(run.id, step_id, "failed", "Step raised an exception; see the private audit log.",
                                   self.policy.max_artifact_chars)
            with run.lock:
                run.outcomes.append("failed")
            raise

    def may_continue(self) -> bool:
        """Check before spending another planner/recovery call, without a model."""
        run = self.active
        if run is None:
            return True
        with run.lock:
            if self.core.cancelled:
                return False
            if run.limited or run.steps >= self.policy.max_steps or time.monotonic() - run.started >= self.policy.max_seconds:
                run.limited = True
                return False
            return True

    def legacy_task_state(self) -> str:
        """Keep the old TaskStore readers/triggers coherent during migration."""
        return {
            RunState.FAILED: "failed", RunState.CANCELLED: "cancelled",
            RunState.WAITING: "blocked", RunState.LIMITED: "blocked",
            RunState.INTERRUPTED: "interrupted",
        }.get(self.last_turn_state, "done")

    def escalate(self, reason: str):
        run = self.active
        if run is None:
            return
        with run.lock:
            if run.selection.lane == Lane.HARNESS:
                return
            run.selection = LaneDecision(Lane.HARNESS, reason)
            self.last_decision = run.selection
            self.core._log_thought("RUN", "escalated", f"lane=harness_run reason={reason}")
            if run.id:
                self.store.escalate(run.id, reason)

    def resolve_failures(self, tool: str):
        """Only a successful explicit corrective attempt resolves a failed step."""
        run = self.active
        if run is None:
            return
        with run.lock:
            ids = [step for step, failed_tool in run.failures.items() if failed_tool == tool]
            for step in ids:
                self.store.resolve_failure(run.id, step)
                del run.failures[step]

    def status(self) -> dict:
        """Small metadata view for front ends; no args or tool outputs."""
        recent = self.store.recent(5)
        return {
            "lane": self.last_decision.lane.value if self.last_decision else None,
            "runs": [{k: row[k] for k in ("id", "lane", "state", "created_at", "updated_at")}
                     for row in recent],
        }
