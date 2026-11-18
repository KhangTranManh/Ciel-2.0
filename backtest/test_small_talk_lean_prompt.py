"""Offline regression coverage for the fast-path small-talk lean Worker prompt.

Run: python backtest/test_small_talk_lean_prompt.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from agent_system import config
from core.harness.classifier import fast_preflight
from core.llm_connector import CielCore


class Worker:
    def __init__(self):
        self.prompts = []

    def generate(self, prompt):
        self.prompts.append(prompt)
        return "ok"


class FakeCore:
    ciel_persona = "PERSONA"
    _CHAT_LONG_INPUT_TOKENS = CielCore._CHAT_LONG_INPUT_TOKENS

    def __init__(self):
        self.worker = Worker()
        self.chat_history = SimpleNamespace(messages=[
            SimpleNamespace(type="human", content="OLD_A"),
            SimpleNamespace(type="ai", content="OLD_B"),
            SimpleNamespace(type="human", content="OLD_C"),
            SimpleNamespace(type="ai", content="OLD_D"),
            SimpleNamespace(type="human", content="chào"),
        ])
        self.logs = []
        self.session_compact = SimpleNamespace(render=lambda: "SESSION_COMPACT")

    def _log_thought(self, actor, action, content):
        self.logs.append((actor, action, content))

    def _profile_block(self):
        return "PROFILE\n\n"

    def _references_session_history(self, _task):
        return False

    def _recent_turns_block(self, max_turns=None, line_chars=240):
        return "FULL_RECENT"


# Bind the real helpers under test without constructing a configured CielCore.
FakeCore._small_talk_recent_block = CielCore._small_talk_recent_block
FakeCore._lean_small_talk_prompt_enabled = staticmethod(CielCore._lean_small_talk_prompt_enabled)


def prompt_for(*, lean, enabled=True):
    core = FakeCore()
    with patch.object(config, "FAST_SMALL_TALK_LEAN_PROMPT", enabled):
        CielCore.execute_chat(core, "chào", lean_prompt=lean)
    return core, core.worker.prompts[-1]


failures = []


def check(name, condition):
    print(("PASS " if condition else "FAIL ") + name)
    if not condition:
        failures.append(name)


fast = fast_preflight("chào")
check("greeting is marked small_talk", fast == {"action": "chat", "lane": "fast_chat", "small_talk": True})
check("pending greeting is not fast-path", fast_preflight("chào", has_pending=True) is None)
check("open-thread greeting is not fast-path", fast_preflight("chào", has_open_thread=True) is None)

lean_core, lean_prompt = prompt_for(lean=True)
check("lean prompt has persona", "PERSONA" in lean_prompt)
check("lean prompt omits profile", "PROFILE" not in lean_prompt)
check("lean prompt omits session compact", "SESSION_COMPACT" not in lean_prompt)
check("lean prompt has at most three prior messages", all(x in lean_prompt for x in ("OLD_B", "OLD_C", "OLD_D")) and "OLD_A" not in lean_prompt)
check("lean thought is labeled", any(action == "chat_task lean=1" for _, action, _ in lean_core.logs))

normal_core, normal_prompt = prompt_for(lean=False)
disabled_core, disabled_prompt = prompt_for(lean=True, enabled=False)
check("normal chat retains full profile", "PROFILE" in normal_prompt and "SESSION_COMPACT" in normal_prompt)
check("disabled flag restores exact full prompt", disabled_prompt == normal_prompt)
check("disabled flag uses ordinary telemetry", any(action == "chat_task" for _, action, _ in disabled_core.logs))

raise SystemExit(1 if failures else 0)
