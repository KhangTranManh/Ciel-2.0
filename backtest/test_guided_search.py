"""Offline contract checks for Ciel's bounded guided web research."""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from core.continuation import ContinuationPolicy, LoopBudget, StepRecord
from core.search_research import (
    SearchTurnState, canonical_url, constrain_citations, evidence_gap, parse_search_hits,
    scrape_failed,
)
from skills.external.web_agent_ops import _rank_rows, get_web_tools


GOOD = """Search Results for 'Vietnam news' (as of 2026-11-03, recency: past day, source: News RSS):
1. Title: Specific event in Vietnam
   Published: 2026-11-03 (today)
   Source: Example News
   URL: https://example.com/news/event
   Snippet: The event happened in Hanoi and officials confirmed the details today.
"""
THIN = """Search Results for 'Vietnam news' (as of 2026-11-03, recency: past day, source: News RSS):
1. Title: Specific event in Vietnam
   Published: 2026-11-03 (today)
   Source: Example News
   URL: https://example.com/news/event
"""


class GuidedSearchTests(unittest.TestCase):
    def test_structural_evidence_and_provenance(self):
        hits = parse_search_hits(GOOD)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].publisher, "Example News")
        self.assertEqual(hits[0].provider, "News RSS")
        self.assertEqual(hits[0].url, "https://example.com/news/event")
        self.assertEqual(evidence_gap("latest Vietnam news", GOOD), "")
        self.assertEqual(evidence_gap("latest Vietnam news", THIN), "thin_snippets")
        self.assertEqual(evidence_gap("latest Vietnam news", "Search API Error: timeout"),
                         "provider_error")
        self.assertEqual(evidence_gap("latest Vietnam news", "No results found"), "no_hits")
        undated = GOOD.replace("Published: 2026-11-03 (today)", "Published: UNKNOWN")
        self.assertEqual(evidence_gap("latest Vietnam news", undated), "no_dated_evidence")
        off_topic = GOOD.replace("Specific event in Vietnam", "Unrelated game update").replace(
            "The event happened in Hanoi and officials confirmed the details today.",
            "The game changed its tournament schedule and released patch notes.")
        self.assertEqual(evidence_gap("latest Vietnam news", off_topic), "off_topic_hits")
        self.assertEqual(evidence_gap("giá vàng hôm nay", GOOD), "off_topic_hits")
        gold_without_number = GOOD.replace("Specific event in Vietnam", "Giá vàng hôm nay").replace(
            "The event happened in Hanoi and officials confirmed the details today.",
            "Giá vàng được cập nhật trong ngày nhưng bài viết không đưa con số cụ thể.")
        self.assertEqual(evidence_gap("giá vàng hôm nay", gold_without_number),
                         "no_price_evidence")

    def test_one_turn_budget_and_grounded_page_reads(self):
        state = SearchTurnState()
        args, reason = state.reserve("stealth_search", {"query": "  Vietnam   NEWS "})
        self.assertEqual(reason, "")
        self.assertEqual(args["query"], "Vietnam   NEWS")
        self.assertIn("already searched", state.reserve("stealth_search", {"query": "vietnam news"})[1])
        self.assertIn("not a search result", state.reserve("smart_scrape", {"url": "https://evil.example/a"})[1])
        state.observe("stealth_search", GOOD)
        args, reason = state.reserve("smart_scrape", {"url": THIN})
        self.assertEqual(reason, "")
        self.assertEqual(args["url"], "https://example.com/news/event")
        self.assertIn("already read", state.reserve("smart_scrape", {"url": args["url"]})[1])
        self.assertEqual(state.reserve("stealth_search", {"query": "Vietnam event update"})[1], "")
        self.assertIn("limit reached", state.reserve("stealth_search", {"query": "third query"})[1])

    def test_public_url_and_explicit_user_url(self):
        self.assertEqual(canonical_url("https://EXAMPLE.com/a#frag"), "https://example.com/a")
        self.assertEqual(canonical_url("http://127.0.0.1/private"), "")
        self.assertEqual(canonical_url("http://localhost/private"), "")
        state = SearchTurnState()
        args, reason = state.reserve("smart_scrape", {"url": "https://example.org/article"},
                                     "Please read https://example.org/article")
        self.assertEqual(reason, "")
        self.assertEqual(args["url"], "https://example.org/article")

    def test_generated_citations_cannot_invent_a_source(self):
        answer = ("Verified [source](https://example.com/news/event). "
                  "Invented [source](https://fake.example/post) and https://fake.example/also")
        clean = constrain_citations(answer, {"https://example.com/news/event"})
        self.assertIn("[source](https://example.com/news/event)", clean)
        self.assertNotIn("fake.example", clean)

    def test_continuation_only_for_real_gap_with_capacity(self):
        budget = LoopBudget()
        thin_record = [StepRecord("stealth_search", {"query": "Vietnam news"}, THIN)]
        state = SearchTurnState()
        state.reserve("stealth_search", {"query": "Vietnam news"})
        state.observe("stealth_search", THIN)
        self.assertTrue(ContinuationPolicy.assess("latest Vietnam news", thin_record, budget,
                                                   search_state=state).should_continue)
        good_record = [StepRecord("stealth_search", {"query": "Vietnam news"}, GOOD)]
        self.assertFalse(ContinuationPolicy.assess("latest Vietnam news", good_record, budget,
                                                    search_state=state).should_continue)
        after_read = thin_record + [StepRecord("smart_scrape", {"url": "https://example.com/news/event"},
                                               "Full article evidence")]
        self.assertFalse(ContinuationPolicy.assess("latest Vietnam news", after_read, budget,
                                                    search_state=state).should_continue)
        state.reserve("stealth_search", {"query": "Vietnam latest event"})
        state.reserve("smart_scrape", {"url": "https://example.com/news/event"})
        state.reserve("smart_scrape", {"url": "https://example.org/second"},
                      "Read https://example.org/second")
        self.assertFalse(ContinuationPolicy.assess("latest Vietnam news", thin_record, budget,
                                                    search_state=state).should_continue)

    def test_failed_page_read_can_trigger_one_changed_query(self):
        state = SearchTurnState()
        state.reserve("stealth_search", {"query": "Vietnam news"})
        state.observe("stealth_search", THIN)
        state.reserve("smart_scrape", {"url": "https://example.com/news/event"})
        failed = "Failed to scrape URL. Google News RSS link blocked direct reading."
        self.assertTrue(scrape_failed(failed))
        records = [StepRecord("stealth_search", {"query": "Vietnam news"}, THIN),
                   StepRecord("smart_scrape", {"url": "https://example.com/news/event"}, failed)]
        verdict = ContinuationPolicy.assess("latest Vietnam news", records, LoopBudget(),
                                             search_state=state)
        self.assertTrue(verdict.should_continue)
        self.assertIn("page_read_failed", verdict.reason)

    def test_result_ranking_demotes_unrequested_social_hit(self):
        article = {"title": "Vietnam gold price today", "url": "https://news.example/gold/today",
                   "body": "Detailed gold prices by market and currency", "dt": datetime.now(timezone.utc)}
        social = {"title": "Vietnam gold price today", "url": "https://facebook.com/post/1",
                  "body": "Vietnam gold price today viral post", "dt": None}
        ranked = _rank_rows([social, article], "Vietnam gold price today")
        self.assertEqual(ranked[0]["url"], article["url"])
        self.assertEqual(len(ranked), 2)  # distinct publishers remain available
        self.assertEqual(_rank_rows([social], "Facebook Vietnam gold price")[0]["url"],
                         social["url"])

    def test_recency_never_restores_stale_or_undated_hits(self):
        now = datetime.now(timezone.utc)
        stale = {"title": "Specific old event", "url": "https://example.com/old/event",
                 "body": "The old event happened long ago.", "date": "2020-01-01",
                 "age": "old", "dt": now - timedelta(days=1000), "source": "Example"}
        undated = dict(stale, title="Specific undated event", url="https://example.com/new/event",
                       date="", age="", dt=None)
        tool = next(t for t in get_web_tools()["tools"] if t.name == "stealth_search")
        with patch("skills.external.web_agent_ops._serpapi_search", return_value=[stale, undated]), \
             patch("skills.external.web_agent_ops._google_news_rss", return_value=[]):
            result = tool.invoke({"query": "latest Vietnam event", "max_results": 5, "timelimit": "d"})
        self.assertIn("No results found", result)
        self.assertNotIn("Specific old event", result)
        self.assertNotIn("Specific undated event", result)

    def test_missing_primary_key_is_reported_without_secret(self):
        now = datetime.now(timezone.utc)
        row = {"title": "Specific Vietnam event", "url": "https://example.com/news/event",
               "body": "Officials confirmed the specific event in Vietnam today.",
               "date": now.date().isoformat(), "age": "today", "dt": now,
               "source": "Example News", "provider": "Google News RSS"}
        tool = next(t for t in get_web_tools()["tools"] if t.name == "stealth_search")
        with patch.dict("os.environ", {"SEARCH_API_KEY": ""}), \
             patch("skills.external.web_agent_ops._google_news_rss", return_value=[row]), \
             patch("ddgs.DDGS") as ddgs:
            ddgs.return_value.__enter__.return_value.text.return_value = []
            result = tool.invoke({"query": "latest Vietnam event", "max_results": 3, "timelimit": "d"})
        self.assertIn("primary=missing_key", result)
        self.assertIn("Provider: Google News RSS", result)


def main() -> int:
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(GuidedSearchTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    failed = len(result.failures) + len(result.errors)
    print(f"RESULT: {result.testsRun - failed}/{result.testsRun} passed")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
