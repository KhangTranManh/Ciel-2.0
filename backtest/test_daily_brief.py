"""Offline Phase-1 Daily Brief regression: stores, ranking, bounds, and tool contract."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from agent_system import config
from core.daily_brief import (
    DailyBriefCollector,
    DailyBriefReferenceError,
    DailyBriefReferenceStore,
    bind_daily_brief_decision,
    render_daily_brief,
)
from core.daily_brief_triggers import build_daily_brief_triggers, make_daily_brief_check
from core.daily_brief_context import (
    DailyBriefExternalContext,
    fetch_current_weather,
    fetch_gmail_metadata,
)
from core.gmail_client import build_gmail_api_service, close_gmail_api_service
from core.harness.models import Lane, RunState
from core.harness.store import RunStore
from core.notifier import Notifier
from core.planner_store import PlannerStore
from core.triggers import TriggerEngine
from skills.internal import daily_brief_ops
from skills.external import calendar_ops
from skills.external.calendar_ops import _event_identity, fetch_upcoming_calendar_events


class FakeTelegramChannel:
    name = "telegram"

    def __init__(self, accepts: bool = True):
        self.accepts = accepts
        self.sent = []

    def is_live(self, now: float) -> bool:
        return True

    def send(self, note, now: float) -> bool:
        if not self.accepts:
            return False
        self.sent.append((note, now))
        return True


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def read(self, _limit=-1):
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

class DailyBriefTests(unittest.TestCase):
    def test_gmail_builder_prefers_current_api_and_falls_back_for_old_versions(self):
        calls = []

        class CurrentUtils:
            @staticmethod
            def build_gmail_service(*, credentials):
                calls.append(("current", credentials))
                return "current-service"

            @staticmethod
            def build_resource_service(*, credentials):
                calls.append(("deprecated", credentials))
                return "deprecated-service"

        class LegacyUtils:
            @staticmethod
            def build_resource_service(*, credentials):
                calls.append(("legacy", credentials))
                return "legacy-service"

        self.assertEqual(
            build_gmail_api_service(credentials="token-a", utils_module=CurrentUtils),
            "current-service",
        )
        self.assertEqual(calls, [("current", "token-a")])
        self.assertEqual(
            build_gmail_api_service(credentials="token-b", utils_module=LegacyUtils),
            "legacy-service",
        )
        self.assertEqual(calls[-1], ("legacy", "token-b"))

        class Closable:
            closed = False

            def close(self):
                self.closed = True

        transient = Closable()
        close_gmail_api_service(transient)
        self.assertTrue(transient.closed)

    def setUp(self):
        # PlannerStore follows the project's existing short-lived SQLite pattern.
        # On Windows a connection handle can outlive one test until process teardown;
        # ignore that OS-only cleanup race exactly as planner/reminder suites do.
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.planner_path = self.root / "planner.db"
        self.todos_path = self.root / "todos.json"
        self.runs_path = self.root / "runs.db"
        self.references_path = self.root / "state" / "daily_brief.json"
        self.zone = ZoneInfo("Asia/Ho_Chi_Minh")
        self.now = datetime(2026, 10, 31, 8, 0, tzinfo=self.zone)
        self.store = PlannerStore(self.planner_path)

    def collector(self, timezone_name: str = "Asia/Ho_Chi_Minh") -> DailyBriefCollector:
        return DailyBriefCollector(
            self.planner_path, self.todos_path, self.runs_path, timezone_name
        )

    def seed_all_sources(self):
        goal, _ = self.store.add_monthly_goal("2026-10", "Ship Daily Command Center")
        self.store.add_weekly_task(
            "2026-10-26", "Today's scheduled work", weekday=5, local_time="10:00",
            monthly_goal_id=goal["id"],
        )
        self.store.add_weekly_task(
            "2026-10-26", "Tomorrow only", weekday=6, local_time="09:00"
        )
        self.store.add_weekly_task("2026-10-26", "This week's unscheduled work")

        for title, due in (
            ("Overdue reminder", self.now - timedelta(hours=1)),
            ("Soon reminder", self.now + timedelta(hours=1)),
            ("Later reminder", self.now + timedelta(hours=5)),
            ("Outside window", self.now + timedelta(hours=25)),
        ):
            self.store.add_reminder(
                title, due.astimezone(timezone.utc).isoformat(), timezone_name=self.zone.key
            )

        self.todos_path.write_text(json.dumps([
            {"id": 1, "task": "Open todo", "done": False},
            {"id": 2, "task": "Completed todo", "done": True},
        ]), encoding="utf-8")

        runs = RunStore(self.runs_path)
        interrupted = runs.create("Resume analysis", Lane.HARNESS, "test")
        runs.transition(interrupted, RunState.RUNNING)
        step = runs.start_step(interrupted, "read_file", {"token": "PRIVATE_STEP_ARG"})
        runs.finish_step(interrupted, step, "completed", "PRIVATE_ARTIFACT", 200)
        runs.transition(interrupted, RunState.INTERRUPTED)

        waiting = runs.create("Approval needed", Lane.HARNESS, "test")
        runs.transition(waiting, RunState.RUNNING)
        runs.transition(waiting, RunState.WAITING)

        completed = runs.create("Already done", Lane.SINGLE, "test")
        runs.transition(completed, RunState.COMPLETED)

    def test_collects_and_ranks_only_actionable_local_data(self):
        self.seed_all_sources()
        brief = self.collector().collect(now=self.now, max_items=20)
        titles = [item["title"] for item in brief["items"]]

        self.assertEqual(titles[:4], [
            "Overdue reminder", "Soon reminder", "Today's scheduled work", "Later reminder"
        ])
        self.assertIn("Approval needed", titles)
        self.assertIn("Resume analysis", titles)
        self.assertIn("This week's unscheduled work", titles)
        self.assertIn("Open todo", titles)
        self.assertNotIn("Tomorrow only", titles)
        self.assertNotIn("Outside window", titles)
        self.assertNotIn("Completed todo", titles)
        self.assertNotIn("Already done", titles)
        self.assertEqual([item["reference"] for item in brief["items"]],
                         [f"D{i}" for i in range(1, len(brief["items"]) + 1)])
        self.assertEqual(brief["monthly_goals"][0]["title"], "Ship Daily Command Center")
        serialized = json.dumps(brief, ensure_ascii=False)
        self.assertNotIn("PRIVATE_STEP_ARG", serialized)
        self.assertNotIn("PRIVATE_ARTIFACT", serialized)

    def test_result_is_bounded_and_reports_omissions(self):
        self.todos_path.write_text(json.dumps([
            {"id": i, "task": f"Todo {i}", "done": False} for i in range(1, 10)
        ]), encoding="utf-8")
        brief = self.collector().collect(now=self.now, max_items=3)
        self.assertEqual(len(brief["items"]), 3)
        self.assertEqual(brief["omitted_count"], 6)
        self.assertEqual(brief["source_counts"]["todos"], 9)

    def test_legacy_audit_runs_are_not_current_daily_work(self):
        RunStore(self.runs_path)
        timestamp = self.now.timestamp()
        with sqlite3.connect(self.runs_path) as db:
            db.execute(
                "INSERT INTO runs VALUES(?,?,?,?,?,?,?)",
                ("legacy-old", "Old location lookup", "harness_run", "interrupted",
                 "legacy_history", timestamp, timestamp),
            )
        brief = self.collector().collect(now=self.now)
        self.assertNotIn("Old location lookup", [item["title"] for item in brief["items"]])

    def test_deterministic_renderer_never_exposes_transport_json(self):
        brief = {
            "items": [{
                "reference": "D1", "source_type": "weekly_task", "source_id": "private-id",
                "title": "Hoàn thành báo cáo", "priority": 80, "status": "scheduled",
                "due_at": "2026-10-31T10:00+07:00", "monthly_goal": None,
            }],
            "monthly_goals": [], "omitted_count": 0, "unavailable_sources": [],
        }
        rendered = render_daily_brief(brief, "Vietnamese")
        self.assertIn("D1: Hoàn thành báo cáo", rendered)
        self.assertIn("đã lên lịch lúc 10:00 31/10", rendered)
        self.assertNotIn("source_id", rendered)
        self.assertNotIn("private-id", rendered)

    def test_one_bad_source_does_not_destroy_the_local_brief(self):
        self.store.add_weekly_task(
            "2026-10-26", "Planner survives", weekday=5, local_time="09:00"
        )
        self.todos_path.write_text("not-json", encoding="utf-8")
        brief = self.collector().collect(now=self.now)
        self.assertEqual([item["title"] for item in brief["items"]], ["Planner survives"])
        self.assertEqual(brief["unavailable_sources"], ["todos"])

    def test_native_calendar_is_bounded_private_and_failure_is_isolated(self):
        event, _ = self.store.add_calendar_event(
            "Ciel core review",
            timezone_name=self.zone.key,
            start_at="2026-10-31T09:00",
            end_at="2026-10-31T10:00",
            location="PRIVATE ROOM",
            notes="PRIVATE NOTES",
        )
        self.todos_path.write_text(json.dumps([
            {"id": 1, "task": "Todo survives", "done": False}
        ]), encoding="utf-8")
        brief = self.collector().collect(now=self.now, max_items=20)
        calendar_item = next(
            item for item in brief["items"]
            if item["source_type"] == "native_calendar_event"
        )
        self.assertEqual(calendar_item["source_id"], str(event["id"]))
        self.assertEqual(calendar_item["title"], "Ciel core review")
        self.assertEqual(calendar_item["due_at"], "2026-10-31T09:00+07:00")
        self.assertEqual(brief["source_counts"]["native_calendar_events"], 1)
        serialized = json.dumps(brief, ensure_ascii=False)
        self.assertNotIn("PRIVATE ROOM", serialized)
        self.assertNotIn("PRIVATE NOTES", serialized)

        with patch.object(
            DailyBriefCollector, "_native_calendar_items", side_effect=RuntimeError("down")
        ):
            degraded = self.collector().collect(now=self.now, max_items=20)
        self.assertIn("Todo survives", [item["title"] for item in degraded["items"]])
        self.assertEqual(degraded["unavailable_sources"], ["native_calendar"])

    def test_native_calendar_reference_binds_only_verified_event_identity(self):
        reference = self._reference_for_binding(
            "native_calendar_event", "42", "Grounded event"
        )
        updated, error = bind_daily_brief_decision({
            "action": "tool",
            "tool_name": "update_native_calendar_event",
            "tool_args": {"event_id": 999, "start_at": "2026-10-31T11:00"},
        }, reference)
        self.assertIsNone(error)
        self.assertEqual(updated["tool_args"]["event_id"], 42)
        self.assertEqual(updated["tool_args"]["start_at"], "2026-10-31T11:00")

        cancelled, error = bind_daily_brief_decision({
            "action": "tool",
            "tool_name": "cancel_native_calendar_event",
            "tool_args": {"event_id": 999},
        }, reference)
        self.assertIsNone(error)
        self.assertEqual(cancelled["tool_args"], {"event_id": 42})

        todo = self._reference_for_binding("todo", "42", "Not an event")
        unchanged = {
            "action": "tool", "tool_name": "cancel_native_calendar_event",
            "tool_args": {"event_id": 999},
        }
        bound, error = bind_daily_brief_decision(unchanged, todo)
        self.assertEqual(bound, unchanged)
        self.assertIn("is a todo", error)

    def test_dates_and_timezone_are_validated(self):
        with self.assertRaisesRegex(ValueError, "YYYY-MM-DD"):
            self.collector().collect("31/10/2026", now=self.now)
        with self.assertRaisesRegex(ValueError, "IANA"):
            self.collector("Mars/Olympus").collect(now=self.now)

    def test_tool_returns_the_standard_structured_envelope(self):
        self.store.add_weekly_task(
            "2026-10-26", "Tool-visible task", weekday=5, local_time="11:00"
        )
        old_planner = config.PLANNER_DB_PATH
        old_timezone = config.PLANNER_TIMEZONE
        try:
            config.PLANNER_DB_PATH = self.planner_path
            config.PLANNER_TIMEZONE = self.zone.key
            with (
                patch.object(daily_brief_ops, "TODO_FILE", self.todos_path),
                patch.object(daily_brief_ops, "RUNS_DB_PATH", self.runs_path),
                patch.object(daily_brief_ops, "REFERENCE_PATH", self.references_path),
                patch("core.daily_brief.datetime") as clock,
            ):
                clock.now.return_value = self.now
                clock.fromisoformat.side_effect = datetime.fromisoformat
                clock.combine.side_effect = datetime.combine
                clock.fromtimestamp.side_effect = datetime.fromtimestamp
                tool = daily_brief_ops.get_daily_brief_tools()["tools"][0]
                result = tool.invoke({"max_items": 7})
        finally:
            config.PLANNER_DB_PATH = old_planner
            config.PLANNER_TIMEZONE = old_timezone
        self.assertTrue(result["success"])
        self.assertEqual(result["meta"]["tool_name"], "get_daily_brief")
        self.assertEqual(result["data"]["items"][0]["title"], "Tool-visible task")
        self.assertTrue(self.references_path.exists())

    def test_reference_snapshot_is_bounded_identity_only(self):
        self.seed_all_sources()
        brief = self.collector().collect(now=self.now, max_items=20)
        store = DailyBriefReferenceStore(
            self.references_path, self.zone.key, ttl_seconds=3600
        )
        self.assertTrue(store.save(brief, now=self.now))

        payload = json.loads(self.references_path.read_text(encoding="utf-8"))
        self.assertEqual(set(payload), {
            "schema_version", "date", "timezone", "generated_at", "expires_at", "items"
        })
        self.assertEqual(set(payload["items"][0]), {
            "reference", "source_type", "source_id", "title"
        })
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("priority", serialized)
        self.assertNotIn("due_at", serialized)
        self.assertNotIn("status", serialized)
        self.assertNotIn("PRIVATE_STEP_ARG", serialized)
        self.assertNotIn("PRIVATE_ARTIFACT", serialized)

        resolved = store.resolve_request("Complete D1", now=self.now + timedelta(minutes=5))
        self.assertEqual(resolved.reference, "D1")
        self.assertEqual(resolved.title, "Overdue reminder")
        self.assertIn("normal permission/confirmation path", resolved.routing_note())

    def test_reference_binding_replaces_guessed_ids_and_titles(self):
        reference = self._reference_for_binding("todo", "17", "Grounded title")
        bound, error = bind_daily_brief_decision({
            "action": "tool", "tool_name": "complete_todo",
            "tool_args": {"todo_id": 999},
        }, reference)
        self.assertIsNone(error)
        self.assertEqual(bound["tool_args"], {"todo_id": 17})

        reminder, error = bind_daily_brief_decision({
            "action": "tool", "tool_name": "add_reminder",
            "tool_args": {"title": "invented", "message": "also invented", "delay_minutes": 5},
        }, reference)
        self.assertIsNone(error)
        self.assertEqual(reminder["tool_args"]["title"], "Grounded title")
        self.assertNotIn("message", reminder["tool_args"])

        _, error = bind_daily_brief_decision({
            "action": "tool", "tool_name": "complete_weekly_task",
            "tool_args": {"task_id": 17},
        }, reference)
        self.assertIn("is a todo", error)

    @staticmethod
    def _reference_for_binding(source_type: str, source_id: str, title: str):
        from core.daily_brief import DailyBriefReference

        return DailyBriefReference("D1", source_type, source_id, title, "2026-10-31")

    def test_reference_expiry_and_ambiguity_fail_before_routing(self):
        self.todos_path.write_text(json.dumps([
            {"id": 1, "task": "First", "done": False},
            {"id": 2, "task": "Second", "done": False},
        ]), encoding="utf-8")
        brief = self.collector().collect(now=self.now, max_items=2)
        store = DailyBriefReferenceStore(
            self.references_path, self.zone.key, ttl_seconds=3600
        )
        store.save(brief, now=self.now)

        with self.assertRaises(DailyBriefReferenceError) as ambiguous:
            store.resolve_request("Complete D1 and D2", now=self.now)
        self.assertEqual(ambiguous.exception.code, "REFERENCE_AMBIGUOUS")
        with self.assertRaises(DailyBriefReferenceError) as bare_number:
            store.resolve_request("Complete item 1", now=self.now)
        self.assertEqual(bare_number.exception.code, "REFERENCE_AMBIGUOUS")
        with self.assertRaises(DailyBriefReferenceError) as unknown:
            store.resolve_request("Complete D9", now=self.now)
        self.assertEqual(unknown.exception.code, "REFERENCE_UNKNOWN")
        with self.assertRaises(DailyBriefReferenceError) as stale:
            store.resolve_request("Complete D1", now=self.now + timedelta(hours=2))
        self.assertEqual(stale.exception.code, "REFERENCE_STALE")

    def test_non_today_brief_clears_action_references(self):
        self.todos_path.write_text(json.dumps([
            {"id": 1, "task": "Today", "done": False}
        ]), encoding="utf-8")
        store = DailyBriefReferenceStore(self.references_path, self.zone.key)
        today = self.collector().collect(now=self.now)
        store.save(today, now=self.now)
        self.assertTrue(self.references_path.exists())

        tomorrow = self.collector().collect(
            target_date="2026-11-01", now=self.now
        )
        self.assertFalse(store.save(tomorrow, now=self.now))
        self.assertFalse(self.references_path.exists())

    def test_runtime_policies_register_daily_brief_as_read_only(self):
        from core.harness.classifier import SINGLE_READ_TOOLS
        from core.llm_connector import CielCore
        from core.parallel import _DEFAULT_PARALLEL_SAFE
        from core.permissions import Decision, PermissionPolicy, _AUTO_TOOLS

        self.assertIn("get_daily_brief", SINGLE_READ_TOOLS)
        self.assertIn("get_daily_brief", _AUTO_TOOLS)
        self.assertIn("get_daily_brief", _DEFAULT_PARALLEL_SAFE)
        self.assertIn("get_daily_brief", CielCore._TOOLS_NEEDING_FORMAT)

    def test_proactive_trigger_is_opt_in_catch_up_aware_and_silent_when_empty(self):
        collector = self.collector()
        references = DailyBriefReferenceStore(
            self.references_path, self.zone.key, ttl_seconds=3600
        )
        external_calls = []
        check = make_daily_brief_check(
            collector,
            references,
            timezone_name=self.zone.key,
            hour=8,
            minute=30,
            external_context=DailyBriefExternalContext(
                gmail_enabled=True,
                gmail_fetcher=lambda **_kwargs: external_calls.append(1) or {"items": []},
            ),
        )
        before = datetime(2026, 10, 31, 8, 29, tzinfo=self.zone).timestamp()
        after = datetime(2026, 10, 31, 8, 30, tzinfo=self.zone).timestamp()
        self.assertIsNone(check(before))
        self.assertIsNone(check(after))
        self.assertEqual(external_calls, [])

        self.todos_path.write_text(json.dumps([
            {"id": 7, "task": "Send the morning update", "done": False}
        ]), encoding="utf-8")
        catch_up = datetime(2026, 10, 31, 11, 0, tzinfo=self.zone).timestamp()
        note = check(catch_up)
        self.assertIsNotNone(note)
        self.assertEqual(note.key, "daily_brief:2026-10-31")
        self.assertIn("D1: Send the morning update", note.detail)
        self.assertTrue(note.action)
        self.assertEqual(external_calls, [1])
        saved = json.loads(self.references_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["items"][0], {
            "reference": "D1", "source_type": "todo", "source_id": "7",
            "title": "Send the morning update",
        })

        disabled = build_daily_brief_triggers(
            enabled_names=["unknown"], planner_db=self.planner_path,
            todo_path=self.todos_path, runs_path=self.runs_path,
            reference_path=self.references_path, timezone_name=self.zone.key,
        )
        enabled = build_daily_brief_triggers(
            enabled_names=["daily_brief", "unknown"], planner_db=self.planner_path,
            todo_path=self.todos_path, runs_path=self.runs_path,
            reference_path=self.references_path, timezone_name=self.zone.key,
        )
        self.assertEqual(disabled, [])
        self.assertEqual([trigger.name for trigger in enabled], ["daily_brief"])

    def test_proactive_delivery_retries_then_dedupes_without_poll_log_noise(self):
        self.todos_path.write_text(json.dumps([
            {"id": 9, "task": "Retry-safe brief", "done": False}
        ]), encoding="utf-8")
        now = datetime(2026, 10, 31, 9, 0, tzinfo=self.zone).timestamp()
        state_path = self.root / "notify.json"
        channel = FakeTelegramChannel(accepts=False)
        notifier = Notifier(state_path=state_path, channels=[channel])
        trigger = build_daily_brief_triggers(
            enabled_names=["daily_brief"], planner_db=self.planner_path,
            todo_path=self.todos_path, runs_path=self.runs_path,
            reference_path=self.references_path, timezone_name=self.zone.key,
            hour=8, minute=0, reference_ttl_seconds=3600,
        )[0]
        engine = TriggerEngine(notifier, [trigger])

        first = engine.tick(now)
        self.assertEqual(first[0][1].status, "digest")
        self.assertEqual(channel.sent, [])
        self.assertEqual(len(notifier.peek_digest()), 1)

        channel.accepts = True
        second = engine.tick(now + 301)
        self.assertEqual(second[0][1].status, "delivered")
        self.assertEqual(len(channel.sent), 1)
        self.assertEqual(notifier.peek_digest(), [])

        restarted = Notifier(
            state_path=state_path, channels=[FakeTelegramChannel(accepts=True)]
        )
        restarted_trigger = build_daily_brief_triggers(
            enabled_names=["daily_brief"], planner_db=self.planner_path,
            todo_path=self.todos_path, runs_path=self.runs_path,
            reference_path=self.references_path, timezone_name=self.zone.key,
            hour=8, minute=0, reference_ttl_seconds=3600,
        )[0]
        logs = []
        restarted_engine = TriggerEngine(
            restarted, [restarted_trigger], logger=lambda a, b, c: logs.append((a, b, c))
        )
        third = restarted_engine.tick(now + 602)
        self.assertEqual(third[0][1].status, "suppressed")
        self.assertIn("cooldown", third[0][1].reason)
        self.assertEqual(logs, [])

    def test_external_context_is_bounded_identity_free_and_rendered(self):
        self.todos_path.write_text(json.dumps([
            {"id": 11, "task": "Keep local work visible", "done": False}
        ]), encoding="utf-8")
        brief = self.collector().collect(now=self.now)

        def gmail_fetcher(**_kwargs):
            return {"items": [
                {"from": f"sender-{index}@example.com", "subject": "S" * 400,
                 "date": "today"}
                for index in range(8)
            ], "unread_estimate": 8}

        def weather_fetcher(_city):
            return {"city": "Ho Chi Minh City", "condition": "Cloudy",
                    "temperature_c": 30, "feels_like_c": 34,
                    "humidity_percent": 70}

        def calendar_fetcher(**_kwargs):
            return [{"summary": f"Event {index}",
                     "start": f"2026-10-31T{index + 9:02d}:00:00+07:00",
                     "end": "", "all_day": False, "private": "must-drop"}
                    for index in range(8)]

        enriched = DailyBriefExternalContext(
            gmail_enabled=True, gmail_max_results=2,
            weather_enabled=True, weather_city="Ho Chi Minh City",
            calendar_enabled=True, calendar_max_results=2,
            gmail_fetcher=gmail_fetcher, weather_fetcher=weather_fetcher,
            calendar_fetcher=calendar_fetcher,
        ).enrich(brief, now=self.now)

        self.assertEqual(len(enriched["external_context"]["gmail"]["items"]), 2)
        self.assertEqual(len(enriched["external_context"]["calendar"]), 2)
        self.assertLessEqual(
            len(enriched["external_context"]["gmail"]["items"][0]["subject"]), 200
        )
        self.assertNotIn("private", enriched["external_context"]["calendar"][0])
        rendered = render_daily_brief(enriched, "Vietnamese")
        self.assertIn("D1: Keep local work visible", rendered)
        self.assertIn("Email Primary", rendered)
        self.assertIn("Thời tiết hiện tại", rendered)
        self.assertIn("Lịch sắp tới", rendered)

        references = DailyBriefReferenceStore(self.references_path, self.zone.key)
        references.save(enriched, now=self.now)
        saved = self.references_path.read_text(encoding="utf-8")
        self.assertNotIn("sender-", saved)
        self.assertNotIn("Cloudy", saved)
        self.assertNotIn("Event 0", saved)

    def test_each_external_failure_is_fail_open_for_local_brief(self):
        self.todos_path.write_text(json.dumps([
            {"id": 12, "task": "Local source survives", "done": False}
        ]), encoding="utf-8")
        brief = self.collector().collect(now=self.now)

        def broken(**_kwargs):
            raise RuntimeError("provider failed")

        enriched = DailyBriefExternalContext(
            gmail_enabled=True,
            weather_enabled=True,
            weather_city="Hanoi",
            calendar_enabled=True,
            gmail_fetcher=broken,
            weather_fetcher=lambda _city: {
                "city": "Hanoi", "condition": "Clear", "temperature_c": 25,
                "feels_like_c": 25, "humidity_percent": 50,
            },
            calendar_fetcher=broken,
        ).enrich(brief, now=self.now)

        self.assertEqual(enriched["items"][0]["title"], "Local source survives")
        self.assertEqual(enriched["external_unavailable"], ["gmail", "calendar"])
        self.assertEqual(enriched["external_context"]["weather"]["temperature_c"], 25)
        note = make_daily_brief_check(
            self.collector(),
            DailyBriefReferenceStore(self.references_path, self.zone.key),
            timezone_name=self.zone.key,
            external_context=DailyBriefExternalContext(
                gmail_enabled=True, gmail_fetcher=broken,
            ),
        )(self.now.timestamp())
        self.assertIsNotNone(note)
        self.assertIn("Local source survives", note.detail)
        self.assertIn("gmail", note.detail)

    def test_external_provider_parsers_keep_metadata_only(self):
        class Execute:
            def __init__(self, value):
                self.value = value

            def execute(self):
                return self.value

        class Messages:
            def list(self, **_kwargs):
                return Execute({"messages": [{"id": "m1"}], "resultSizeEstimate": 4})

            def get(self, **_kwargs):
                return Execute({"payload": {"headers": [
                    {"name": "From", "value": "Alice <alice@example.com>"},
                    {"name": "Subject", "value": "Quarterly review"},
                    {"name": "Date", "value": "Sat, 31 Oct 2026"},
                ]}, "snippet": "PRIVATE SNIPPET", "body": "PRIVATE BODY"})

        class GmailService:
            def users(self):
                return self

            def messages(self):
                return Messages()

        gmail = fetch_gmail_metadata(service=GmailService())
        serialized = json.dumps(gmail)
        self.assertIn("Quarterly review", serialized)
        self.assertNotIn("PRIVATE", serialized)

        weather = fetch_current_weather(
            "Hanoi",
            opener=lambda *_args, **_kwargs: FakeResponse({"current_condition": [{
                "temp_C": "26", "FeelsLikeC": "28", "humidity": "61",
                "weatherDesc": [{"value": "Partly cloudy"}],
            }]}),
        )
        self.assertEqual(weather, {
            "city": "Hanoi", "condition": "Partly cloudy", "temperature_c": 26,
            "feels_like_c": 28, "humidity_percent": 61,
        })

        class Events:
            def list(self, **_kwargs):
                return Execute({"items": [{
                    "id": "event-1", "summary": "Planning",
                    "start": {"dateTime": "2026-10-31T10:00:00+07:00"},
                    "end": {"dateTime": "2026-10-31T11:00:00+07:00"},
                    "description": "PRIVATE DESCRIPTION", "attendees": ["PRIVATE"],
                }]})

        class CalendarService:
            def events(self):
                return Events()

        calendar = fetch_upcoming_calendar_events(
            now=self.now, service=CalendarService(), max_results=3
        )
        serialized_calendar = json.dumps(calendar)
        self.assertIn("Planning", serialized_calendar)
        self.assertNotIn("PRIVATE", serialized_calendar)

    def test_calendar_pack_keeps_reads_auto_and_mutations_confirmed(self):
        from core.harness.classifier import SINGLE_READ_TOOLS
        from core.harness.signals import _RISK_DESCRIPTIONS
        from core.parallel import _DEFAULT_PARALLEL_SAFE
        from core.permissions import Decision, PermissionPolicy, _AUTO_TOOLS

        self.assertIn("list_calendar_events", _AUTO_TOOLS)
        self.assertIn("list_calendar_events", SINGLE_READ_TOOLS)
        self.assertNotIn("list_calendar_events", _DEFAULT_PARALLEL_SAFE)
        for name in (
            "create_calendar_event", "update_calendar_event", "delete_calendar_event"
        ):
            self.assertIn(name, _RISK_DESCRIPTIONS)
            self.assertNotIn(name, _AUTO_TOOLS)
        policy = PermissionPolicy(_RISK_DESCRIPTIONS)
        self.assertEqual(policy.decide("list_calendar_events", {})[0], Decision.AUTO)
        self.assertEqual(
            policy.decide("create_calendar_event", {"summary": "Review"})[0],
            Decision.ASK,
        )
        self.assertEqual(
            policy.decide(
                "delete_calendar_event", {"event_id": "event-1"}, attended=False
            )[0],
            Decision.DEFER,
        )
        first = _event_identity("primary", "Review", "start", "end")
        self.assertEqual(first, _event_identity("primary", "Review", "start", "end"))
        self.assertNotEqual(first, _event_identity("primary", "Review", "later", "end"))

    def test_calendar_tool_pack_uses_standard_envelopes_and_stable_create_id(self):
        class Execute:
            def __init__(self, value):
                self.value = value

            def execute(self):
                return self.value

        class Events:
            def __init__(self):
                self.inserted = []

            def list(self, **_kwargs):
                return Execute({"items": []})

            def insert(self, **kwargs):
                self.inserted.append(kwargs["body"])
                return Execute({**kwargs["body"], "status": "confirmed"})

            def patch(self, **kwargs):
                return Execute({
                    "id": kwargs["eventId"], "summary": kwargs["body"].get("summary", ""),
                    "start": {"dateTime": "2026-10-31T10:00:00+07:00"},
                    "end": {"dateTime": "2026-10-31T11:00:00+07:00"},
                    "status": "confirmed",
                })

            def delete(self, **_kwargs):
                return Execute({})

        class Service:
            def __init__(self):
                self.api = Events()

            def events(self):
                return self.api

        service = Service()
        with patch.object(calendar_ops, "_calendar_service", return_value=service):
            tools = {tool.name: tool for tool in calendar_ops.get_calendar_tools()["tools"]}

        self.assertEqual(set(tools), {
            "list_calendar_events", "create_calendar_event",
            "update_calendar_event", "delete_calendar_event",
        })
        listed = tools["list_calendar_events"].invoke({"lookahead_hours": 24})
        self.assertTrue(listed["success"])
        created = tools["create_calendar_event"].invoke({
            "summary": "Review", "start": "2026-10-31T10:00:00+07:00",
            "end": "2026-10-31T11:00:00+07:00",
            "timezone_name": self.zone.key,
        })
        self.assertTrue(created["success"])
        self.assertEqual(created["data"]["event"]["event_id"], service.api.inserted[0]["id"])
        blank = tools["create_calendar_event"].invoke({
            "summary": "   ", "start": "2026-10-31T10:00:00+07:00",
            "end": "2026-10-31T11:00:00+07:00",
            "timezone_name": self.zone.key,
        })
        self.assertFalse(blank["success"])
        self.assertEqual(blank["error"]["code"], "CALENDAR_CREATE_ERROR")
        updated = tools["update_calendar_event"].invoke({
            "event_id": created["data"]["event"]["event_id"], "summary": "New review"
        })
        self.assertTrue(updated["success"])
        deleted = tools["delete_calendar_event"].invoke({
            "event_id": created["data"]["event"]["event_id"]
        })
        self.assertTrue(deleted["success"])


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(DailyBriefTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    failed = len(result.failures) + len(result.errors)
    passed = result.testsRun - failed
    print(f"RESULT: {passed}/{result.testsRun} passed")
    raise SystemExit(0 if result.wasSuccessful() else 1)
