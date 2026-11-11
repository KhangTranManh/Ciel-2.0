"""Regression for Ciel's provider-independent native calendar."""
from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_system import config
from core.planner_store import PlannerStore, normalize_calendar_time
from skills.internal.native_calendar_ops import get_native_calendar_tools


class NativeCalendarTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="ciel_native_calendar_"))
        self.path = self.root / "planner.db"
        self.store = PlannerStore(self.path)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _timed(self, title: str, start: str, end: str, **kwargs):
        return self.store.add_calendar_event(
            title,
            timezone_name="Asia/Ho_Chi_Minh",
            start_at=start,
            end_at=end,
            **kwargs,
        )

    def test_schema_migration_is_repeatable_and_owned_by_planner_store(self):
        PlannerStore(self.path)
        with sqlite3.connect(self.path) as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            version = conn.execute("PRAGMA user_version").fetchone()[0]
        self.assertIn("calendar_events", tables)
        self.assertIn("calendar_event_alerts", tables)
        self.assertGreaterEqual(version, 3)

    def test_timed_event_normalizes_to_utc_and_retries_are_idempotent(self):
        event, created = self._timed(
            "Project review",
            "2026-11-02T10:00",
            "2026-11-02T11:30",
            location="Room A",
            notes="Private preparation",
            alert_offsets=[60, 15, 15],
        )
        repeated, repeated_created = self._timed(
            "project REVIEW",
            "2026-11-02T10:00",
            "2026-11-02T11:30",
            alert_offsets=[15, 60],
        )
        self.assertTrue(created)
        self.assertFalse(repeated_created)
        self.assertEqual(event["id"], repeated["id"])
        self.assertEqual(event["start_at_utc"], "2026-11-02T03:00:00+00:00")
        self.assertEqual(event["end_at_utc"], "2026-11-02T04:30:00+00:00")
        self.assertEqual(event["alert_offsets"], [15, 60])

    def test_invalid_or_mixed_event_shape_writes_nothing(self):
        with self.assertRaisesRegex(ValueError, "end must be after start"):
            self._timed("Bad interval", "2026-11-02T11:00", "2026-11-02T10:00")
        with self.assertRaisesRegex(ValueError, "must not mix"):
            self.store.add_calendar_event(
                "Mixed",
                timezone_name="Asia/Ho_Chi_Minh",
                all_day=True,
                start_at="2026-11-02T10:00",
                end_at="2026-11-02T11:00",
                start_date="2026-11-02",
                end_date_exclusive="2026-11-03",
            )
        with sqlite3.connect(self.path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM calendar_events").fetchone()[0]
        self.assertEqual(count, 0)

    def test_all_day_event_uses_dates_without_fake_midnight_instants(self):
        event, created = self.store.add_calendar_event(
            "Offsite",
            timezone_name="Asia/Ho_Chi_Minh",
            all_day=True,
            start_date="2026-11-02",
            end_date_exclusive="2026-11-04",
        )
        self.assertTrue(created)
        self.assertEqual(event["start_date"], "2026-11-02")
        self.assertEqual(event["end_date_exclusive"], "2026-11-04")
        self.assertIsNone(event["start_at_utc"])
        self.assertIsNone(event["end_at_utc"])

    def test_dst_gap_and_ambiguity_require_an_explicit_offset(self):
        with self.assertRaisesRegex(ValueError, "does not exist"):
            normalize_calendar_time("2026-03-08T02:30", "America/New_York")
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            normalize_calendar_time("2026-11-01T01:30", "America/New_York")
        utc_value, zone = normalize_calendar_time(
            "2026-11-01T01:30:00-04:00", "America/New_York"
        )
        self.assertEqual(utc_value, "2026-11-01T05:30:00+00:00")
        self.assertEqual(zone, "America/New_York")

    def test_range_query_uses_half_open_overlap_for_timed_and_all_day_events(self):
        self._timed("Ends at boundary", "2026-11-02T08:00", "2026-11-02T09:00")
        self._timed("Overlaps", "2026-11-02T09:30", "2026-11-02T10:30")
        self._timed("Starts at boundary", "2026-11-02T11:00", "2026-11-02T12:00")
        self.store.add_calendar_event(
            "All-day overlap",
            timezone_name="Asia/Ho_Chi_Minh",
            all_day=True,
            start_date="2026-11-02",
            end_date_exclusive="2026-11-04",
        )
        events = self.store.list_calendar_events(
            "2026-11-02T09:00",
            "2026-11-02T11:00",
            timezone_name="Asia/Ho_Chi_Minh",
        )
        self.assertEqual(
            {event["title"] for event in events}, {"Overlaps", "All-day overlap"}
        )

    def test_read_tools_are_bounded_and_only_explicit_get_returns_notes(self):
        event, _ = self._timed(
            "Private review",
            "2026-11-02T10:00",
            "2026-11-02T11:00",
            location="Secret room",
            notes="SECRET NOTES",
        )
        with patch.object(config, "PLANNER_DB_PATH", self.path):
            pack = get_native_calendar_tools()
            tools = {tool.name: tool for tool in pack["tools"]}
            listed = tools["list_native_calendar_events"].invoke({
                "range_start": "2026-11-02T00:00",
                "range_end": "2026-11-03T00:00",
                "timezone_name": "Asia/Ho_Chi_Minh",
                "limit": 5,
            })
            detail = tools["get_native_calendar_event"].invoke({"event_id": event["id"]})
        self.assertTrue(listed["success"])
        self.assertNotIn("SECRET", json.dumps(listed["data"]))
        self.assertNotIn("Secret room", json.dumps(listed["data"]))
        self.assertTrue(detail["success"])
        self.assertEqual(detail["data"]["event"]["notes"], "SECRET NOTES")
        self.assertEqual(detail["data"]["event"]["location"], "Secret room")

    def test_native_reads_are_auto_single_step_and_parallel_safe(self):
        from core.harness.classifier import SINGLE_READ_TOOLS
        from core.harness.signals import _RISK_DESCRIPTIONS
        from core.parallel import collect_parallel_safe
        from core.permissions import Decision, PermissionPolicy, _AUTO_TOOLS

        reads = {
            "list_native_calendar_events", "get_native_calendar_event",
            "suggest_native_calendar_defaults",
        }
        mutations = {
            "create_native_calendar_event", "update_native_calendar_event",
            "cancel_native_calendar_event",
        }
        pack = get_native_calendar_tools()
        self.assertEqual({tool.name for tool in pack["tools"]}, reads | mutations)
        self.assertTrue(reads.issubset(SINGLE_READ_TOOLS))
        self.assertTrue(reads.issubset(_AUTO_TOOLS))
        parallel_safe = collect_parallel_safe([pack])
        self.assertTrue(reads.issubset(parallel_safe))
        self.assertTrue(mutations.isdisjoint(parallel_safe))
        policy = PermissionPolicy(_RISK_DESCRIPTIONS)
        for name in reads:
            self.assertEqual(policy.decide(name, {})[0], Decision.AUTO)
        for name in mutations:
            self.assertNotIn(name, _AUTO_TOOLS)
            self.assertEqual(policy.decide(name, {})[0], Decision.ASK)
            self.assertEqual(policy.decide(name, {}, attended=False)[0], Decision.DEFER)

    def test_defaults_need_history_and_never_expose_notes(self):
        store = self.store
        def add(day, end, loc, status_cancel=False):
            ev, _ = store.add_calendar_event(
                "Review Ciel", timezone_name="Asia/Ho_Chi_Minh",
                start_at=f"2030-01-{day:02d}T09:00:00+07:00",
                end_at=f"2030-01-{day:02d}T{end}:00+07:00",
                location=loc, notes="SECRET")
            if status_cancel:
                store.cancel_calendar_event(ev["id"])
        add(1, "10:30", "Discord")
        add(2, "10:30", "Discord")
        self.assertFalse(store.suggest_calendar_defaults("review ciel")["enough_history"])
        add(3, "11:00", "Discord")
        add(4, "12:00", "Other", status_cancel=True)
        out = store.suggest_calendar_defaults("review ciel")
        self.assertEqual(out["sample_count"], 3)
        self.assertEqual(out["duration_minutes"], 90)
        self.assertEqual(out["location"], "Discord")
        self.assertEqual(out["timezone"], "Asia/Ho_Chi_Minh")
        self.assertNotIn("SECRET", json.dumps(out))
        self.assertEqual(store.suggest_calendar_defaults("Other")["sample_count"], 0)

    def test_update_and_cancel_are_atomic_versioned_and_idempotent(self):
        event, _ = self._timed(
            "Review", "2026-11-02T10:00", "2026-11-02T11:00",
            notes="keep private",
        )
        updated = self.store.update_calendar_event(
            event["id"], title="Deep review", start_at="2026-11-02T10:30"
        )
        self.assertEqual(updated["title"], "Deep review")
        self.assertEqual(updated["start_at_utc"], "2026-11-02T03:30:00+00:00")
        self.assertEqual(updated["notes"], "keep private")
        self.assertEqual(updated["schedule_version"], 2)

        cosmetic = self.store.update_calendar_event(
            event["id"], title="Deep review v2", location="Room B"
        )
        self.assertEqual(cosmetic["schedule_version"], 2)

        repeated = self.store.update_calendar_event(
            event["id"], title="Deep review v2", start_at="2026-11-02T10:30"
        )
        self.assertEqual(repeated["schedule_version"], 2)
        with self.assertRaisesRegex(ValueError, "end must be after start"):
            self.store.update_calendar_event(event["id"], start_at="2026-11-02T12:00")
        unchanged = self.store.get_calendar_event(event["id"])
        self.assertEqual(unchanged["start_at_utc"], "2026-11-02T03:30:00+00:00")
        self.assertEqual(unchanged["schedule_version"], 2)

        cancelled = self.store.cancel_calendar_event(event["id"])
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(cancelled["schedule_version"], 3)
        repeated_cancel = self.store.cancel_calendar_event(event["id"])
        self.assertEqual(repeated_cancel["schedule_version"], 3)
        with self.assertRaisesRegex(ValueError, "is cancelled"):
            self.store.update_calendar_event(event["id"], title="Must not change")

    def test_mutation_tools_use_bounded_results_and_store_private_details(self):
        with patch.object(config, "PLANNER_DB_PATH", self.path):
            tools = {
                tool.name: tool for tool in get_native_calendar_tools()["tools"]
            }
            created = tools["create_native_calendar_event"].invoke({
                "title": "Private planning",
                "start_at": "2026-11-02T10:00",
                "end_at": "2026-11-02T11:00",
                "timezone_name": "Asia/Ho_Chi_Minh",
                "location": "Secret room",
                "notes": "SECRET NOTES",
            })
            event_id = created["data"]["event"]["id"]
            updated = tools["update_native_calendar_event"].invoke({
                "event_id": event_id, "title": "Private planning v2"
            })
            cancelled = tools["cancel_native_calendar_event"].invoke({
                "event_id": event_id
            })
        self.assertTrue(created["success"])
        self.assertNotIn("SECRET", json.dumps(created["data"]))
        self.assertNotIn("Secret room", json.dumps(created["data"]))
        self.assertTrue(updated["success"])
        self.assertTrue(cancelled["success"])
        stored = self.store.get_calendar_event(event_id)
        self.assertEqual(stored["notes"], "SECRET NOTES")
        self.assertEqual(stored["location"], "Secret room")
        self.assertEqual(stored["status"], "cancelled")


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(NativeCalendarTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    failed = len(result.failures) + len(result.errors)
    passed = result.testsRun - failed
    print(f"RESULT: {passed}/{result.testsRun} passed")
    raise SystemExit(0 if result.wasSuccessful() else 1)
