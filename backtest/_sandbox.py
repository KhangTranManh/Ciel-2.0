"""_sandbox.py — shared test-isolation helper for backtest/live_conversation_test.py.

Bug found live (2026-07-27): a "fresh" CielCore() per test conversation is NOT
isolated — pending_action.json, memory_bank.json, tasks.json, deferred.json,
facts.json, and ciel_workspace/todos.json are all read/written from the REAL
project paths regardless of how many CielCore instances get constructed. One
100-sample run left a REAL, unresolved git_confirm_push pending against this
actual repo, and by sample ~70 every subsequent sample got the SAME stale
"pending confirmation" reply, unrelated to its own content — contaminating
roughly a third of the run. `isolate()` gives every test conversation a
throwaway directory for exactly these files, so testing can never again write
into (or leak state from) the Master's real data.

Bug found live (2026-07-27, round 2): the fix above only isolated the JSON
state files — the REAL ciel_workspace/ and agent_output/ directories were
still shared, since WORKSPACE_DIR/AGENT_OUTPUT_DIR in system_ops.py are
module-level constants computed once from the real project root. A "Ngọc,
CV update" test session did `read_file("cv_new.txt")` and got back an
unrelated "Minh Anh" CV left over from a completely different earlier test —
different persona, different goal, same file. `isolate()` now also redirects
the workspace/output/screenshot directories the same way it already redirects
the JSON state.

STILL not isolated, and cannot be via path redirection: open_application and
vision_act's actual mouse/keyboard control act on the REAL desktop — only
WHERE their output gets saved is redirected now, not what they see or click.
Generated conversations exercising these WILL still have real, visible side
effects on the machine running this harness — inherent to testing tools whose
entire job is to control the real screen, not a bug.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from core.llm_connector import CielCore
from core.task_state import TaskStore
from core.permissions import DeferredStore
from langchain_community.chat_message_histories import ChatMessageHistory
import skills.internal.memory_ops as memory_ops
import skills.internal.productivity_ops as productivity_ops
import skills.internal.system_ops as system_ops
import skills.internal.vision_ops as vision_ops
import skills.internal.os_ops as os_ops


def isolate(core: CielCore, sandbox: Path) -> None:
    sandbox.mkdir(parents=True, exist_ok=True)

    core.chat_history = ChatMessageHistory()
    core.chat_memory_file = sandbox / "memory_bank.json"
    core._pending_action = None
    core._pending_state_path = sandbox / "pending_action.json"
    core.tasks = TaskStore(sandbox / "tasks.json")
    core.deferred = DeferredStore(sandbox / "deferred.json")

    # All of the below are module-level constants in the skill files themselves
    # (not CielCore attributes), read fresh on every call via Python's normal
    # module-global lookup — safe to monkeypatch for the duration of one test
    # conversation since nothing resolves them until the tool actually runs.
    memory_ops.FACTS_FILE = sandbox / "facts.json"
    productivity_ops.TODO_FILE = sandbox / "todos.json"

    workspace_dir = sandbox / "ciel_workspace"
    agent_output_dir = sandbox / "agent_output"
    screenshot_dir = workspace_dir / "screenshots"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    agent_output_dir.mkdir(parents=True, exist_ok=True)
    screenshot_dir.mkdir(parents=True, exist_ok=True)

    system_ops.WORKSPACE_DIR = workspace_dir
    system_ops.AGENT_OUTPUT_DIR = agent_output_dir
    vision_ops.SCREENSHOT_DIR = screenshot_dir
    os_ops.SCREENSHOT_DIR = screenshot_dir


def new_sandbox(prefix: str) -> Path:
    return Path(tempfile.mkdtemp(prefix=prefix))
