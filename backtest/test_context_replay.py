"""Turn-by-turn replay of the context-loss fixtures through SessionCompact (0 LLM).

Unlike test_context_loss (which checks that a well-formed summary is accepted),
this replay drives the compact the way production does: one turn at a time, the
chat history trimmed to the last 20 messages, refresh every 4 turns, and the
config's default size cap. The fake summarizer is *grounded*: it may only write
a fixture's summary line when the evidence for it is actually present in the
prompt it receives (the earlier compact or the new transcript), and it echoes
any key-shaped string it sees, so a leak reaches the output. A decision that
never reaches the summarizer therefore really is forgotten.

Runs against old and new compact implementations alike (it only uses the shared
constructor/schedule_refresh/summary API), which is how the before/after score
for wave 3 was measured.
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

from core.session_compact import SessionCompact

try:
    from core.session_compact import HEADERS
except ImportError:  # pre-wave-3 compact had no fixed sections
    HEADERS = ("Đã chốt:", "Ràng buộc/sở thích:", "Đang làm:", "Còn chờ trả lời:", "Dữ kiện/số liệu:")

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "context_loss"
HISTORY_WINDOW = 20
REFRESH_TURNS = 4
_KEY_RE = re.compile(r"sk-[A-Za-z0-9]{8,}")
_WORD_RE = re.compile(r"\w{4,}", re.UNICODE)


def _msg(role: str, content: str) -> SimpleNamespace:
    return SimpleNamespace(type="human" if role == "human" else "ai", content=content)


def _turns(case: dict) -> list[tuple[str, str]]:
    turns = [tuple(case["dialogue"])]
    for n in range(case["padding_turns"]):
        turns.append((f"Lượt nhiễu {n + 1}: tiếp tục trao đổi bình thường.",
                      f"Đã ghi nhận lượt nhiễu {n + 1}."))
    return turns


def _evidence_present(case: dict, prompt: str) -> bool:
    low = prompt.lower()
    words = [w.lower() for w in _WORD_RE.findall(case["dialogue"][0]) if not _KEY_RE.match(w)]
    if not words:
        return False
    hits = sum(1 for w in words if w in low)
    return hits / len(words) >= 0.5


def _grounded_summarizer(case: dict):
    keys = ("decided", "constraints", "working", "open", "facts")

    def summarize(prompt: str) -> str:
        low = prompt.lower()
        grounded = _evidence_present(case, prompt)
        out = []
        for header, key in zip(HEADERS, keys):
            lines = [l for l in case["summary"].get(key, [])
                     if grounded or l.lower() in low]  # carried by earlier compact
            if key == "facts":
                lines += [f"Chuỗi thấy trong hội thoại: {k}" for k in _KEY_RE.findall(prompt)]
            out.append(header + "\n" + "\n".join(f"- {l}" for l in lines))
        return "\n".join(out)

    return summarize


def _wait(compact: SessionCompact, timeout: float = 2.0) -> None:
    until = time.time() + timeout
    while time.time() < until and getattr(compact, "_refreshing", False):
        time.sleep(0.005)


def replay(case: dict, root: Path) -> tuple[bool, str]:
    compact = SessionCompact(root / f"{case['id']}.json", refresh_turns=REFRESH_TURNS)
    summarizer = _grounded_summarizer(case)
    history: list = []
    for user, ai in _turns(case):
        history += [_msg("human", user), _msg("ai", ai)]
        history = history[-HISTORY_WINDOW:]
        compact.schedule_refresh(list(history), summarizer)
        _wait(compact)
    rendered = compact.summary or ""
    missing = [t for t in case.get("contains", []) if t not in rendered]
    leaked = [t for t in case.get("not_contains", []) if t in rendered]
    ok = not missing and not leaked
    why = "" if ok else f"missing={missing} leaked={leaked}"
    return ok, why


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cases = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(FIXTURES.glob("*.json"))]
    results = []
    with tempfile.TemporaryDirectory(prefix="ciel_context_replay_") as root:
        for case in cases:
            ok, why = replay(case, Path(root))
            results.append(ok)
            print(f"  [{'PASS' if ok else 'FAIL'}] {case['id']} ({case['padding_turns'] + 1} turns) {why}")
    passed = sum(results)
    print(f"RESULT: {passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
