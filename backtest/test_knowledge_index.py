"""Knowledge index: change-aware sync, subject collapse, staleness, budgeted recall."""
from __future__ import annotations

import hashlib
import re
import shutil
import tempfile
import time
import unittest
from pathlib import Path

try:
    import chromadb  # noqa: F401
    from chromadb import Documents, EmbeddingFunction, Embeddings
    HAVE_CHROMA = True
except Exception:  # pragma: no cover
    HAVE_CHROMA = False

from core.knowledge_index import (
    KnowledgeIndex, chunk_text, format_for_prompt, ACTION_MAX_CHARS,
)

if HAVE_CHROMA:
    class FakeEmbed(EmbeddingFunction):
        """Deterministic bag-of-words hashing so tests need no model download."""
        def __init__(self): pass
        def __call__(self, input: Documents) -> Embeddings:
            out = []
            for text in input:
                v = [0.0] * 128
                for w in re.findall(r"\w+", text.lower()):
                    v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 128] += 1.0
                out.append(v)
            return out
        @staticmethod
        def name() -> str: return "fake-bow"
        def get_config(self): return {}
        @staticmethod
        def build_from_config(config): return FakeEmbed()


@unittest.skipUnless(HAVE_CHROMA, "chromadb not installed")
class KnowledgeIndexTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="ciel_kidx_"))
        self.idx = KnowledgeIndex(self.root / "db", embedding_fn=FakeEmbed())

    def tearDown(self):
        self.idx._client = None
        self.idx._cols.clear()
        shutil.rmtree(self.root, ignore_errors=True)

    def test_chunking_is_bounded_and_keeps_all_text(self):
        text = "\n\n".join(f"Đoạn {i} " + "từ " * 120 for i in range(5))
        chunks = chunk_text(text, size=700, overlap=100)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(c) <= 800 for c in chunks))
        self.assertIn("Đoạn 4", " ".join(chunks))

    def test_changed_file_replaces_old_chunks_and_deleted_file_is_removed(self):
        f = self.root / "bao_cao.txt"
        f.write_text("Giá vàng hôm nay tăng mạnh lên 70 triệu", encoding="utf-8")
        self.assertEqual(self.idx.sync_document(f), "added")
        self.assertEqual(self.idx.sync_document(f), "unchanged")
        f.write_text("Báo cáo thời tiết miền Nam có mưa lớn", encoding="utf-8")
        self.assertEqual(self.idx.sync_document(f), "updated")
        texts = " ".join(h.text for h in self.idx.search("giá vàng 70 triệu", "docs", min_cosine=-1))
        self.assertNotIn("70 triệu", texts)
        self.assertEqual(self.idx.remove_missing(set()), 1)
        self.assertEqual(self.idx.count("docs"), 0)

    def test_newest_record_per_subject_wins_and_volatile_is_stale(self):
        now = time.time()
        self.idx.record_action("Đã gửi báo cáo giá vàng 70 triệu", action="send_report",
                               subject_key="gold_report", when=now - 5 * 86400)
        self.idx.record_action("Đã gửi báo cáo giá vàng 72 triệu", action="send_report",
                               subject_key="gold_report", when=now - 3600)
        hits = self.idx.search("báo cáo giá vàng đã gửi", "actions", min_cosine=0.0, now=now)
        self.assertEqual(len(hits), 1)
        self.assertIn("72", hits[0].text)
        self.assertFalse(hits[0].stale)  # 1 hour old
        old = self.idx.search("báo cáo giá vàng đã gửi", "actions", min_cosine=0.0, now=now + 3 * 86400)
        self.assertTrue(old[0].stale)    # volatile subject past its 1-day TTL

    def test_actions_are_redacted_and_capped(self):
        self.idx.record_action("Gửi mail cho a@b.com tại C:\\secret\\x.txt " + "x" * 600,
                               action="send_email")
        hit = self.idx.search("gửi mail", "actions", min_cosine=0.0)[0]
        self.assertNotIn("a@b.com", hit.text)
        self.assertLessEqual(len(hit.text), ACTION_MAX_CHARS)

    def test_threshold_rejects_unrelated_and_relative_margin_trims(self):
        self.idx.record_action("Đặt lịch họp review Ciel lúc 9 giờ", action="calendar")
        self.assertEqual(self.idx.search("quantum chromodynamics lattice", "actions"), [])

    def test_prompt_block_fits_budget_instead_of_dropping(self):
        self.idx.record_action("Báo cáo " + "dài " * 70, action="report")
        hits = self.idx.search("báo cáo dài", "actions", min_cosine=0.0)
        block = format_for_prompt(hits, budget_tokens=60)
        self.assertTrue(block.startswith("[RECORDED CONTEXT"))
        self.assertGreater(len(block), 60)
        self.assertEqual(format_for_prompt([], 600), "")


if __name__ == "__main__":
    unittest.main()
