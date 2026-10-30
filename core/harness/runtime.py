"""Construction boundary for the feature flag and private run store."""
from pathlib import Path

from .manager import RunManager
from .models import RunPolicy
from .store import RunStore


def build_manager(core):
    from agent_system.config import (
        RUN_HARNESS_ENABLED, RUN_HARNESS_MAX_STEPS, RUN_HARNESS_MAX_SECONDS,
    )
    if not RUN_HARNESS_ENABLED:
        return None
    policy = RunPolicy(max_steps=RUN_HARNESS_MAX_STEPS, max_seconds=RUN_HARNESS_MAX_SECONDS)
    store = RunStore(Path(core.base_dir) / "ciel_data" / "runs.db")
    store.recover_interrupted()
    store.import_legacy_tasks(core.tasks.path)
    return RunManager(core, store, policy)
