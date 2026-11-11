"""Action log: allow-listed, redacted, number-free for volatile subjects, never blocks."""
from __future__ import annotations

import unittest

from core.action_log import build_record, record_tool_success, subject_key_for


class Recorder:
    def __init__(self, fail=False):
        self.rows, self.fail = [], fail

    def record_action(self, summary, **kw):
        if self.fail:
            raise RuntimeError("boom")
        self.rows.append((summary, kw))


class ActionLogTests(unittest.TestCase):
    def test_only_allow_listed_side_effect_tools_are_recorded(self):
        self.assertIsNone(build_record("get_weather", {"city": "HN"}))
        self.assertIsNone(build_record("read_file", {"filename": "x"}))
        self.assertIsNone(build_record("write_file", {}))
        rec = build_record("add_reminder", {"title": "Uống thuốc 8h"})
        self.assertEqual(rec["action"], "reminder_add")

    def test_volatile_subjects_keep_the_event_but_not_the_numbers(self):
        rec = build_record("send_gmail_message",
                           {"to": "a@b.com", "subject": "Báo cáo giá vàng 72.500.000 hôm nay", "message": "SECRET BODY"})
        self.assertEqual(rec["subject_key"], "market_report")
        self.assertNotRegex(rec["summary"], r"\d")
        self.assertNotIn("SECRET BODY", rec["summary"])
        calm = build_record("send_gmail_message", {"to": "a@b.com", "subject": "Họp 9h ngày 12"})
        self.assertEqual(calm["subject_key"], "")
        self.assertIn("9h", calm["summary"])
        self.assertEqual(subject_key_for("thời tiết Hà Nội"), "weather_report")

    def test_disabled_by_default_and_failures_are_swallowed(self):
        idx = Recorder()
        self.assertFalse(record_tool_success("add_reminder", {"title": "x"}, index=idx, enabled=False))
        self.assertEqual(idx.rows, [])
        self.assertTrue(record_tool_success("add_reminder", {"title": "x"}, index=idx,
                                            enabled=True, background=False))
        self.assertEqual(len(idx.rows), 1)
        bad = Recorder(fail=True)
        self.assertTrue(record_tool_success("add_reminder", {"title": "x"}, index=bad,
                                            enabled=True, background=False))
        self.assertFalse(record_tool_success("get_weather", {}, index=idx, enabled=True))


class KnowledgeToolTests(unittest.TestCase):
    def test_tools_are_auto_single_read_and_parallel_safe(self):
        from core.harness.classifier import SINGLE_READ_TOOLS
        from core.parallel import collect_parallel_safe
        from core.permissions import Decision, PermissionPolicy, _AUTO_TOOLS
        from core.harness.signals import _RISK_DESCRIPTIONS
        from skills.internal.knowledge_ops import get_knowledge_tools

        names = {"recall_past_actions", "search_my_documents"}
        pack = get_knowledge_tools()
        self.assertEqual({t.name for t in pack["tools"]}, names)
        self.assertTrue(names <= SINGLE_READ_TOOLS)
        self.assertTrue(names <= _AUTO_TOOLS)
        self.assertTrue(names <= collect_parallel_safe([pack]))
        policy = PermissionPolicy(_RISK_DESCRIPTIONS)
        for n in names:
            self.assertEqual(policy.decide(n, {})[0], Decision.AUTO)

    def test_live_queries_carry_a_history_only_note(self):
        from unittest.mock import patch
        from skills.internal import knowledge_ops

        class Empty:
            def search(self, *a, **k):
                return []

        with patch.object(knowledge_ops, "_index", return_value=Empty()):
            out = knowledge_ops._search("actions", "giá BTC bây giờ", "recall_past_actions", 3)
            calm = knowledge_ops._search("actions", "lần trước gửi mail cho A", "recall_past_actions", 3)
        self.assertTrue(out["success"])
        self.assertIn("historical", out["data"]["note"])
        self.assertEqual(calm["data"]["note"], "")


class LegacyRagFlagTests(unittest.TestCase):
    def test_flag_defaults_on_and_gates_archive(self):
        from unittest.mock import MagicMock, patch
        from agent_system import config
        from core import llm_connector
        self.assertTrue(config.LEGACY_RAG_ENABLED)
        fake = MagicMock()
        msg = MagicMock(type="human", content="hi")
        ai = MagicMock(type="ai", content="yo")
        with patch.object(llm_connector.rag_manager, "embed_and_save", return_value=True) as save:
            with patch.object(config, "LEGACY_RAG_ENABLED", False):
                llm_connector.CielCore._archive_to_rag(fake, [msg, ai])
            self.assertEqual(save.call_count, 0)


if __name__ == "__main__":
    unittest.main()
