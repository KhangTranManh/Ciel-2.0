"""Offline harness tests, runnable per stage and as one regression suite.

python -m backtest.test_run_harness --stage storage
python -m backtest.test_run_harness
"""
from __future__ import annotations

import argparse
import json
import tempfile
import unittest
import threading
import copy
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from core.harness.models import Lane, RunPolicy, RunState
from core.harness.store import RunStore
from core.harness.classifier import classify, fast_preflight
from core.harness.manager import RunManager


class LaneTests(unittest.TestCase):
    def choose(self, request, action="tool", name="get_weather", args=None, **flags):
        decision = {"action": action, "tool_name": name, "tool_args": args or {"city": "Hanoi"}}
        decision.update(flags.pop("decision_fields", {}))
        return classify(request, decision, known_tools={"get_weather", "send_gmail_message", "new_tool"},
                        risky_tools={"send_gmail_message"}, **flags).lane

    def test_easy_paths(self):
        self.assertEqual(self.choose("Explain gravity", "chat"), Lane.FAST)
        self.assertEqual(self.choose("Hanoi weather?"), Lane.SINGLE)

    def test_mutations_never_obey_fast_model_hint(self):
        self.assertEqual(self.choose("send this", name="send_gmail_message",
                                     decision_fields={"lane": "fast_chat"}), Lane.HARNESS)
        self.assertEqual(self.choose("use new tool", name="new_tool"), Lane.HARNESS)
        self.assertEqual(self.choose("missing tool", name="unknown"), Lane.HARNESS)

    def test_dependencies_and_background_escalate(self):
        cases = [
            ("weather then send report", {}),
            ("if weather is warm then tell me", {}),
            ("read each file", {}),
            ("weather", {"unattended": True}),
            ("weather", {"failed_prior": True}),
            ("weather", {"args": {"city": "{step_1}"}}),
            ("weather", {"decision_fields": {"needs_followup": True}}),
        ]
        for request, flags in cases:
            with self.subTest(request=request, flags=flags):
                self.assertEqual(self.choose(request, **flags), Lane.HARNESS)

    def test_fast_preflight_is_narrow_and_does_not_bypass_pending(self):
        self.assertEqual(fast_preflight("Hello!")["action"], "chat")
        for request in ("hello and send mail", "yes", "continue", "weather", "hello\nread file"):
            self.assertIsNone(fast_preflight(request))
        self.assertIsNone(fast_preflight("hello", has_pending=True))
        self.assertIsNone(fast_preflight("hello", has_open_thread=True))
        self.assertIsNone(fast_preflight("hello", unattended=True))


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "runs.db"
        self.store = RunStore(self.path)

    def test_lifecycle_and_evidence_survive_restart(self):
        run = self.store.create("weather", Lane.SINGLE, "one read")
        self.store.transition(run, RunState.RUNNING)
        step = self.store.start_step(run, "get_weather", {"city": "Hanoi"})
        self.store.finish_step(run, step, "completed", "30 C", 100)
        self.store.transition(run, RunState.COMPLETED)
        restored = RunStore(self.path).get(run)
        self.assertEqual(restored["state"], "completed")
        self.assertEqual(restored["artifacts"][0]["content"], "30 C")
        self.assertEqual([e["kind"] for e in restored["events"]],
                         ["created", "state", "step_started", "step_finished", "state"])

    def test_restart_interrupts_without_replaying_or_restoring_approval(self):
        for state in (RunState.RUNNING, RunState.WAITING):
            run = self.store.create("send", Lane.HARNESS, "approval")
            self.store.transition(run, RunState.RUNNING)
            if state == RunState.WAITING:
                self.store.transition(run, state)
        self.assertEqual(self.store.recover_interrupted(), 2)
        self.assertTrue(all(r["state"] == "interrupted" for r in self.store.recent()))
        self.assertEqual(self.store.recover_interrupted(), 0)

    def test_terminal_run_cannot_be_reopened(self):
        run = self.store.create("read", Lane.SINGLE, "one read")
        self.store.transition(run, RunState.FAILED)
        with self.assertRaises(ValueError):
            self.store.transition(run, RunState.RUNNING)
        with self.assertRaises(ValueError):
            self.store.start_step(run, "read_file", {})

    def test_parallel_results_are_atomic_and_bounded(self):
        run = self.store.create("several reads", Lane.HARNESS, "multi")
        self.store.transition(run, RunState.RUNNING)

        def finish(n):
            step = self.store.start_step(run, "read_file", {"n": n})
            self.store.finish_step(run, step, "completed", "x" * 300, 20)

        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(finish, range(8)))
        result = self.store.get(run)
        self.assertEqual(len(result["steps"]), 8)
        self.assertEqual(len(result["artifacts"]), 8)
        self.assertTrue(all(len(a["content"]) == 20 for a in result["artifacts"]))
        self.assertTrue(all("args" not in json.loads(e["payload"]) for e in result["events"]))

    def test_legacy_import_is_idempotent_and_never_imports_consent(self):
        path = Path(self.temp.name) / "tasks.json"
        records = [{"id": "old", "goal": "send report", "status": "blocked", "steps": [
            {"tool": "read_file", "status": "done", "summary": "42"}]}]
        original = json.dumps(records)
        path.write_text(original, encoding="utf-8")
        self.assertEqual(self.store.import_legacy_tasks(path), 1)
        self.assertEqual(self.store.import_legacy_tasks(path), 0)
        run = self.store.get("legacy-old")
        self.assertEqual(run["state"], "interrupted")
        self.assertEqual(run["artifacts"][0]["content"], "42")
        self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_malformed_legacy_records_cannot_break_startup(self):
        path = Path(self.temp.name) / "tasks.json"
        path.write_text("not json", encoding="utf-8")
        self.assertEqual(self.store.import_legacy_tasks(path), 0)
        path.write_text(json.dumps([
            None, {"id": "bad-time", "created_at": float("nan")},
            {"id": "bad-shape", "status": [], "steps": 42},
        ]), encoding="utf-8")
        self.assertEqual(self.store.import_legacy_tasks(path), 1)
        self.assertEqual(self.store.get("legacy-bad-shape")["state"], "interrupted")


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.core = SimpleNamespace(
            _tool_map={"get_weather": object(), "read_file": object(), "send_gmail_message": object()},
            _HIGH_RISK_TOOLS={"send_gmail_message"}, unattended=False,
            cancelled=False, _pending_action=None, _log_thought=lambda *a: None,
        )
        self.store = RunStore(Path(self.temp.name) / "runs.db")
        self.manager = RunManager(self.core, self.store)

    def single(self, operation):
        return self.manager.execute("weather", {"action": "tool", "tool_name": "get_weather"}, operation)

    def test_fast_chat_has_no_persistence_or_extra_model_call(self):
        calls = []
        result = self.manager.execute("hello", {"action": "chat"}, lambda: calls.append("worker") or "hello")
        self.assertEqual(result, "hello")
        self.assertEqual(calls, ["worker"])
        self.assertEqual(self.store.recent(), [])

    def test_single_step_and_failed_recovery_preserve_evidence(self):
        def work():
            failed = self.manager.track_step("get_weather", {}, lambda: "[TOOL_ERROR] unavailable")
            self.assertIn("TOOL_ERROR", failed)
            good = self.manager.track_step("read_file", {}, lambda: "30 C")
            self.manager.resolve_failures("get_weather")
            return good

        self.assertEqual(self.single(work), "30 C")
        run = self.store.get(self.manager.last_run_id)
        self.assertEqual(run["state"], "completed")
        self.assertEqual(run["lane"], "harness_run")
        self.assertEqual([s["state"] for s in run["steps"]], ["failed", "completed"])
        self.assertIn("step_recovered", [e["kind"] for e in run["events"]])

    def test_step_limit_is_shared_by_parallel_workers(self):
        self.manager.policy = RunPolicy(max_steps=2)
        actual = []

        def work():
            active = self.manager.active
            def step(n):
                with self.manager.bind(active):
                    return self.manager.track_step("read_file", {"n": n}, lambda: actual.append(n) or "ok")
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(step, range(8)))
            return "partial data"

        self.assertIn("RUN_LIMIT", self.single(work))
        self.assertEqual(len(actual), 2)
        self.assertEqual(self.store.get(self.manager.last_run_id)["state"], "budget_exhausted")

    def test_time_limit_checked_before_starting_next_tool(self):
        actual = []
        def work():
            self.manager.active.started -= 1000
            return self.manager.track_step("get_weather", {}, lambda: actual.append(1) or "ok")
        self.assertIn("RUN_LIMIT", self.single(work))
        self.assertEqual(actual, [])

    def test_continuation_budget_checked_before_planner_cost(self):
        self.manager.policy = RunPolicy(max_steps=1)
        def work():
            self.assertTrue(self.manager.may_continue())
            self.manager.track_step("get_weather", {}, lambda: "30 C")
            self.assertFalse(self.manager.may_continue())
            return "partial"
        self.assertIn("RUN_LIMIT", self.single(work))

    def test_cancelled_or_waiting_run_is_not_completed(self):
        def wait():
            self.core._pending_action = {"tool": "send_gmail_message"}
            return "preview"
        self.single(wait)
        self.assertEqual(self.store.get(self.manager.last_run_id)["state"], "waiting_for_approval")
        self.core._pending_action = None
        self.single(lambda: "[CANCELLED] declined")
        self.assertEqual(self.store.get(self.manager.last_run_id)["state"], "cancelled")

    def test_failed_run_is_not_completed_by_friendly_finalizer(self):
        def work():
            self.manager.track_step("get_weather", {}, lambda: "[TOOL_ERROR] unavailable")
            return "The weather is unavailable today."
        self.single(work)
        self.assertEqual(self.store.get(self.manager.last_run_id)["state"], "failed")

    def test_exception_records_interruption_and_cleans_binding(self):
        def work():
            raise RuntimeError("simulated failure")
        with self.assertRaises(RuntimeError):
            self.single(work)
        self.assertIsNone(self.manager.active)
        self.assertEqual(self.store.get(self.manager.last_run_id)["state"], "interrupted")

    def test_store_error_never_replays_work(self):
        calls = []
        with patch.object(self.store, "create", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.single(lambda: calls.append(1) or "ok")
        self.assertEqual(calls, [])

    def test_frontend_status_excludes_private_arguments_and_artifacts(self):
        self.single(lambda: self.manager.track_step("get_weather", {"city": "PRIVATE"}, lambda: "SECRET"))
        payload = json.dumps(self.manager.status())
        self.assertNotIn("PRIVATE", payload)
        self.assertNotIn("SECRET", payload)

    def test_feature_flag_construction_is_local_and_disabled_has_no_database(self):
        from core.harness.runtime import build_manager
        self.core.base_dir = Path(self.temp.name) / "runtime"
        self.core.tasks = SimpleNamespace(path=Path(self.temp.name) / "tasks.json")
        with patch("agent_system.config.RUN_HARNESS_ENABLED", False):
            self.assertIsNone(build_manager(self.core))
        database = self.core.base_dir / "ciel_data" / "runs.db"
        self.assertFalse(database.exists())
        with patch("agent_system.config.RUN_HARNESS_ENABLED", True):
            manager = build_manager(self.core)
        self.assertIsInstance(manager, RunManager)
        self.assertTrue(database.exists())


class ReplayTests(unittest.TestCase):
    """Real CielCore methods with only provider and tool I/O replaced.

    __new__ avoids startup/OAuth/model construction entirely. All state lives in
    a temporary directory. Assertions are on effects, evidence, and model counts.
    """
    def setUp(self):
        from core.llm_connector import CielCore
        from core.task_state import TaskStore
        from core.permissions import PermissionPolicy, DeferredStore
        from core.active_subject import ActiveSubject
        from langchain_community.chat_message_histories import ChatMessageHistory
        from core.tool_manager import ToolManager

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.core = core = object.__new__(CielCore)
        core.base_dir = Path(self.temp.name)
        core._log_lock = threading.RLock()
        core._ctx = threading.local()
        core._cancel_event = threading.Event()
        core._pending_action = None
        core._pending_state_path = core.base_dir / "pending.json"
        core._active_lookup = None
        core._sent_this_turn = set()
        core._telegram_delivery_sent_this_turn = False
        core.chat_history = ChatMessageHistory()
        core.ciel_persona = "You are Ciel."
        core.max_history = 20
        core.active_subject = ActiveSubject()
        core.tasks = TaskStore(core.base_dir / "tasks.json")
        core.deferred = DeferredStore(core.base_dir / "deferred.json")
        core.permissions = PermissionPolicy(core._HIGH_RISK_TOOLS, deny_names=set(), gate_disabled=False)
        core.confirm_callback = lambda *a: True
        self.logs, self.prompts, self.routes, self.evaluations, self.calls = [], [], [], [], []
        core._log_thought = lambda *args: self.logs.append(args)
        core._save_chat_memory = lambda: None
        core._learn_from_turn = lambda *args: None
        core._profile_block = lambda: ""
        core._recent_entities_note = lambda: ""
        core._open_thread_note = lambda *args: ""
        core.session_compact = SimpleNamespace(render=lambda: "", schedule_refresh=lambda *a: None)
        core.middleware = None
        core._middleware_review = lambda request, name, body: body
        self.decision = {"action": "chat"}
        self.results = {
            "get_weather": "Hanoi: 30 C / 86 F",
            "read_file": "RAW evidence: 42",
            "get_current_time": "2026-10-30 UTC",
            "search_gmail": '[{"id":"abc123", "subject":"Grounded subject"}]',
            "get_gmail_message": "Message Id: abc123; body: Grounded content",
            "send_gmail_message": "Message Id: abc123",
            "write_file": "Written successfully",
        }
        core._tool_map = {name: SimpleNamespace(name=name) for name in self.results}
        core._tools = list(core._tool_map.values())
        core._parallel_safe = {"read_file", "get_current_time", "get_weather"}
        core._tool_list_str = ", ".join(core._tool_map)

        def route(*args):
            self.routes.append(args)
            return copy.deepcopy(self.decision)

        def generate(prompt):
            self.prompts.append(prompt)
            return "Grounded report: 30 C and 42 units."

        def evaluate(messages):
            self.evaluations.append(messages)
            return SimpleNamespace(content='{"satisfied":true}')

        def execute(name, args):
            self.calls.append((name, copy.deepcopy(args)))
            value = self.results[name]
            if isinstance(value, dict):
                return value
            return {"success": True, "data": value, "error": None, "meta": {"tool_name": name}}

        core.router = SimpleNamespace(route=route)
        core.worker = SimpleNamespace(generate=generate)
        core.brain = SimpleNamespace(_router_llm=SimpleNamespace(invoke=evaluate))
        core.recovery = SimpleNamespace(heal_tool_error=lambda *a: (False, "failed", {"error": "offline"}))
        core.tool_manager = SimpleNamespace(
            tools=core._tools, execute_tool=execute,
            format_tool_result=lambda result: ToolManager.format_tool_result(None, result),
            validate_tool_args=lambda *a: None,
        )
        core.run_manager = RunManager(core, RunStore(core.base_dir / "runs.db"))
        self.recall = patch("core.llm_connector.rag_manager.search_similar", return_value="")
        self.recall.start()
        self.addCleanup(self.recall.stop)

    def last_run(self):
        return self.core.run_manager.store.get(self.core.run_manager.last_run_id)

    def test_greeting_calls_only_worker_and_preserves_history(self):
        response = self.core.process("hello")
        self.assertIn("Grounded", response)
        self.assertEqual(len(self.prompts), 1)
        self.assertEqual(self.routes, [])
        self.assertEqual(self.calls, [])
        self.assertEqual(len(self.core.chat_history.messages), 2)
        self.assertEqual(self.core.run_manager.store.recent(), [])

    def test_real_constructor_and_first_turn_with_harness_enabled(self):
        import core.llm_connector as connector
        from agent_system import config
        startup_root = Path(self.temp.name) / "startup"
        worker_calls = []
        worker = SimpleNamespace(generate=lambda prompt: worker_calls.append(prompt) or "Hello")
        tools = SimpleNamespace(get_tools=lambda: [], skill_data=[])
        with (
            patch.object(connector, "__file__", str(startup_root / "core" / "llm_connector.py")),
            patch.object(connector, "ToolManager", return_value=tools),
            patch.object(connector, "Brain", return_value=SimpleNamespace()),
            patch.object(connector, "Worker", return_value=worker),
            patch.object(connector, "Router", return_value=SimpleNamespace()),
            patch.object(connector, "MIDDLEWARE_ENABLED", False),
            patch.object(config, "RUN_HARNESS_ENABLED", True),
        ):
            core = connector.CielCore()
        self.assertEqual(core.base_dir, startup_root)
        self.assertTrue((startup_root / "ciel_data" / "runs.db").exists())
        self.assertEqual(core.process("hello"), "Hello")
        self.assertEqual(len(worker_calls), 1)
        self.assertEqual(core.run_manager.store.recent(), [])

    def test_single_weather_never_spends_a_verifier_call(self):
        self.decision = {"action": "tool", "tool_name": "get_weather", "tool_args": {"city": "Hanoi"}}
        self.core.process("weather in Hanoi?")
        self.assertEqual(len(self.routes), 1)
        self.assertEqual(self.evaluations, [])
        self.assertEqual([name for name, _ in self.calls], ["get_weather"])
        self.assertEqual(self.last_run()["state"], "completed")
        self.assertEqual(self.last_run()["lane"], "single_step")

    def test_multi_tool_synthesizes_once_from_raw_facts(self):
        self.decision = {"action": "multi_tool", "tools": [
            {"tool_name": "read_file", "tool_args": {"filename": "ciel_workspace/x.txt"}},
            {"tool_name": "get_current_time", "tool_args": {}},
        ]}
        response = self.core.process("Read the note and check the time")
        self.assertIn("Grounded", response)
        self.assertEqual(len(self.prompts), 1)
        self.assertIn("RAW evidence: 42", self.prompts[0])
        self.assertIn("2026-10-30", self.prompts[0])
        self.assertEqual(len(self.last_run()["steps"]), 2)
        self.assertEqual(self.last_run()["lane"], "harness_run")

    def test_dependent_gmail_read_uses_raw_message_id(self):
        self.decision = {"action": "multi_tool", "tools": [
            {"tool_name": "search_gmail", "tool_args": {"query": "subject:Grounded"}},
            {"tool_name": "get_gmail_message", "tool_args": {"message_id": "{prev}"}},
        ]}
        self.core.process("Find and open the message")
        self.assertEqual(self.calls[1], ("get_gmail_message", {"message_id": "abc123"}))
        self.assertEqual(len(self.prompts), 1)
        self.assertEqual(self.last_run()["state"], "completed")

    def email_plan(self):
        self.decision = {"action": "multi_tool", "tools": [
            {"tool_name": "get_weather", "tool_args": {"city": "Hanoi"}},
            {"tool_name": "send_gmail_message", "tool_args": {
                "to": "test@example.com", "subject": "Weather",
                "message": "[PROFESSIONAL_EMAIL_BODY_TO_BE_SYNTHESIZED]"}},
        ]}

    def test_denied_plan_never_executes_even_its_read_steps(self):
        self.email_plan()
        self.core.confirm_callback = lambda *a: False
        response = self.core.process("Check weather then email the report to test@example.com")
        self.assertIn("CANCELLED", response)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.prompts, [])
        self.assertEqual(self.last_run()["state"], "cancelled")

    def test_unattended_cannot_inherit_session_or_open_gate(self):
        self.email_plan()
        self.core.unattended = True
        self.core.permissions._gate_disabled = True
        self.core.permissions._session_grants.add("send_gmail_message")
        self.core.process("Check weather then email the report to test@example.com")
        self.assertEqual(self.calls, [])
        self.assertTrue(self.core.deferred.pending())
        self.assertEqual(self.last_run()["lane"], "harness_run")

    def test_approved_email_gets_grounded_body_and_sends_once(self):
        self.email_plan()
        self.core.process("Check weather then email the report to test@example.com")
        sends = [args for name, args in self.calls if name == "send_gmail_message"]
        self.assertEqual(len(sends), 1)
        self.assertIn("30 C", sends[0]["message"])
        self.assertNotIn("SYNTHESIZED", sends[0]["message"])
        self.assertEqual(len(self.prompts), 1)
        self.assertEqual(self.last_run()["state"], "completed")

    def test_invalid_plan_has_no_tool_effect(self):
        self.decision = {"action": "multi_tool", "tools": [
            {"tool_name": "missing_tool", "tool_args": {}}]}
        response = self.core.process("Check this")
        self.assertIn("PLAN_INVALID", response)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.last_run()["state"], "failed")

    def test_unfixable_error_does_not_call_model_for_repair(self):
        self.decision = {"action": "tool", "tool_name": "get_weather", "tool_args": {"city": "Hanoi"}}
        self.results["get_weather"] = {
            "success": False, "error": {"code": "TOOL_NOT_FOUND", "message": "Tool unavailable"}, "data": None}
        response = self.core.process("weather in Hanoi?")
        self.assertIn("TOOL_ERROR", response)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.prompts, [])
        self.assertEqual(self.evaluations, [])
        self.assertEqual(self.last_run()["lane"], "harness_run")
        self.assertEqual(self.last_run()["state"], "failed")

    def test_pending_confirmation_still_uses_gate_and_records_a_run(self):
        self.core._set_pending_action("send_gmail_message", {
            "to": "test@example.com", "subject": "Review", "message": "Approved text"}, "preview")
        self.core.confirm_callback = lambda *a: False
        self.core.process("yes")
        self.assertEqual(self.routes, [])
        self.assertEqual(self.calls, [])
        self.assertEqual(self.last_run()["state"], "cancelled")

    def test_legacy_feature_off_preserves_multi_tool_behavior(self):
        self.core.run_manager = None
        self.decision = {"action": "multi_tool", "tools": [
            {"tool_name": "read_file", "tool_args": {"filename": "ciel_workspace/x.txt"}},
            {"tool_name": "get_current_time", "tool_args": {}},
        ]}
        self.assertIn("Grounded", self.core.process("Read note and check time"))
        self.assertEqual(len(self.prompts), 1)
        self.assertEqual(len(self.calls), 2)

    def test_empty_gmail_source_blocks_dependent_read_and_is_not_completed(self):
        self.results["search_gmail"] = "No emails found."
        self.decision = {"action": "multi_tool", "tools": [
            {"tool_name": "search_gmail", "tool_args": {"query": "subject:Missing"}},
            {"tool_name": "get_gmail_message", "tool_args": {"message_id": "{prev}"}},
        ]}
        self.core.process("Find and open the message")
        self.assertEqual([name for name, _ in self.calls], ["search_gmail"])
        self.assertEqual(self.last_run()["state"], "failed")
        self.assertIn("SKIPPED_DEPENDENCY", self.prompts[0])

    def test_failed_source_prevents_email_even_with_approval(self):
        self.email_plan()
        self.results["get_weather"] = {
            "success": False, "error": {"code": "TOOL_NOT_FOUND", "message": "Tool unavailable"}, "data": None}
        response = self.core.process("Check weather then email the report to test@example.com")
        self.assertEqual([name for name, _ in self.calls], ["get_weather"])
        self.assertIn("RUN_BLOCKED", response)
        self.assertEqual(self.last_run()["state"], "failed")

    def test_replan_cannot_repeat_completed_work(self):
        decision = {"action": "multi_tool", "needs_followup": True, "tools": [
            {"tool_name": "read_file", "tool_args": {"filename": "ciel_workspace/x.txt"}}]}
        self.decision = decision
        self.core.process("Check the note")
        self.assertEqual(len(self.routes), 2)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(self.prompts), 1)
        self.assertEqual(self.last_run()["state"], "completed")

    def test_cancellation_between_steps_prevents_delivery_and_further_models(self):
        self.email_plan()
        actual = self.core.tool_manager.execute_tool
        def cancel_after_read(name, args):
            result = actual(name, args)
            self.core.request_cancel("offline replay")
            return result
        self.core.tool_manager.execute_tool = cancel_after_read
        response = self.core.process("Check weather then email the report to test@example.com")
        self.assertEqual([name for name, _ in self.calls], ["get_weather"])
        self.assertEqual(self.prompts, [])
        self.assertIn("CANCELLED", response)
        self.assertEqual(self.last_run()["state"], "cancelled")

    def test_code_write_goes_through_tool_boundary_and_is_recorded(self):
        self.decision = {"action": "code", "task": "Produce a short Python program", "filename": "agent_output/test.py"}
        self.core.worker.generate = lambda prompt: "```python\nprint(42)\n```"
        self.core.process("Generate code in agent_output/test.py")
        writes = [args for name, args in self.calls if name == "write_file"]
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0]["content"], "print(42)")
        self.assertEqual(self.last_run()["steps"][0]["tool"], "write_file")
        self.assertEqual(self.last_run()["state"], "completed")

    def test_invalid_generated_code_is_never_written(self):
        self.decision = {"action": "code", "task": "Produce Python", "filename": "agent_output/test.py"}
        self.core.worker.generate = lambda prompt: "def broken(:"
        response = self.core.process("Generate code in agent_output/test.py")
        self.assertIn("TOOL_ERROR", response)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.last_run()["state"], "failed")

    def test_actual_confirmation_callback_exposes_waiting_state(self):
        self.email_plan()
        seen = []
        def deny(*args):
            seen.append(self.core.run_manager.store.recent()[0]["state"])
            return False
        self.core.confirm_callback = deny
        self.core.process("Check weather then email test@example.com")
        self.assertEqual(seen, ["waiting_for_approval"])
        self.assertEqual(self.last_run()["state"], "cancelled")

    def test_preview_followup_creates_fresh_run_and_closes_old_wait(self):
        pending_args = {"to": "test@example.com", "subject": "Review", "message": "Text"}
        self.results["read_file"] = {"success": True, "data": "Preview only", "error": None,
                                     "confirm": {"tool": "send_gmail_message", "args": pending_args}}
        self.decision = {"action": "tool", "tool_name": "read_file", "tool_args": {"filename": "review.txt"}}
        self.core.process("Read the preview")
        first_run = self.core.run_manager.last_run_id
        self.assertEqual(self.last_run()["state"], "waiting_for_approval")
        self.core.confirm_callback = lambda *a: False
        self.core.process("yes")
        self.assertNotEqual(first_run, self.core.run_manager.last_run_id)
        old = self.core.run_manager.store.get(first_run)
        self.assertEqual(old["state"], "interrupted")
        self.assertEqual(self.last_run()["state"], "cancelled")
        self.assertFalse(any(name == "send_gmail_message" for name, _ in self.calls))

    def test_syntax_validation_is_deterministic(self):
        from core.harness.repair import RecoveryManager
        worker = SimpleNamespace(generate=lambda *args: self.fail("Syntax validation called a model"))
        recovery = RecoveryManager(worker, lambda *a: None)
        self.assertEqual(recovery.check_syntax("x = 42"), (True, ""))
        self.assertFalse(recovery.check_syntax("def broken(:")[0])

    def test_failed_read_escalates_and_successful_correction_retains_failure_evidence(self):
        self.decision = {"action": "tool", "tool_name": "read_file", "tool_args": {"filename": "missing.txt"}}
        actual = self.core.tool_manager.execute_tool
        def read(name, args):
            if args.get("filename") == "missing.txt":
                self.calls.append((name, copy.deepcopy(args)))
                return {"success": True, "data": "Error: File not found", "error": None}
            return actual(name, args)
        self.core.tool_manager.execute_tool = read
        answers = iter([
            '{"satisfied":false,"action":"tool","tool_name":"read_file","tool_args":{"filename":"existing.txt"}}',
            '{"satisfied":true}',
        ])
        self.core.brain._router_llm.invoke = lambda messages: SimpleNamespace(content=next(answers))
        response = self.core.process("Read the note")
        self.assertIn("RAW evidence", response)
        self.assertEqual(len(self.calls), 2)
        run = self.last_run()
        self.assertEqual(run["lane"], "harness_run")
        self.assertEqual(run["state"], "completed")
        self.assertEqual([step["state"] for step in run["steps"]], ["failed", "completed"])
        self.assertIn("step_recovered", [event["kind"] for event in run["events"]])

    def test_chat_search_fallback_escalates_and_tracks_its_bounded_retry(self):
        self.results["stealth_search"] = "Search Results: https://example.com/news"
        self.core._tool_map["stealth_search"] = SimpleNamespace(name="stealth_search")
        self.core.tool_manager.tools.append(self.core._tool_map["stealth_search"])
        outputs = iter(["I do not have current information.", "tournament updates", "tournament official news", "Grounded answer."])
        def generate(prompt):
            self.prompts.append(prompt)
            return next(outputs)
        self.core.worker.generate = generate
        actual = self.core.tool_manager.execute_tool
        hits = 0
        def search(name, args):
            nonlocal hits
            hits += 1
            result = actual(name, args)
            if hits == 1:
                result["data"] = "No results found for 'tournament updates'"
            return result
        self.core.tool_manager.execute_tool = search
        response = self.core.process("What is the latest tournament update?")
        self.assertEqual(response, "Grounded answer.")
        self.assertEqual(hits, 2)
        self.assertEqual(len(self.prompts), 4)
        self.assertEqual(self.last_run()["lane"], "harness_run")
        self.assertEqual(len(self.last_run()["steps"]), 2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=("storage", "lanes", "runtime", "replay"))
    args = parser.parse_args()
    groups = {"storage": StorageTests, "lanes": LaneTests, "runtime": RuntimeTests, "replay": ReplayTests}
    suite = unittest.TestSuite()
    for stage, group in groups.items():
        if args.stage is None or args.stage == stage:
            suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(group))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    failed = len(result.failures) + len(result.errors)
    print(f"RESULT: {result.testsRun - failed}/{result.testsRun} passed")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
