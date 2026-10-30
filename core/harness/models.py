"""Small contracts shared by lane selection, execution, and persistence."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Lane(str, Enum):
    FAST = "fast_chat"
    SINGLE = "single_step"
    HARNESS = "harness_run"


class RunState(str, Enum):
    PLANNED = "planned"
    RUNNING = "running"
    WAITING = "waiting_for_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"
    LIMITED = "budget_exhausted"


TERMINAL_STATES = frozenset({
    RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED,
    RunState.INTERRUPTED, RunState.LIMITED,
})


@dataclass(frozen=True)
class LaneDecision:
    lane: Lane
    reason: str


@dataclass(frozen=True)
class RunPolicy:
    """Ceilings checked at boundaries; an atomic tool call is never interrupted."""
    max_steps: int = 24
    max_seconds: float = 180.0
    max_artifact_chars: int = 12000

    def __post_init__(self):
        if self.max_steps < 1 or self.max_seconds <= 0 or self.max_artifact_chars < 1:
            raise ValueError("Run limits must be positive")
