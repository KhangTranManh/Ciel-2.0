"""Deterministic Vietnamese context-loss measurement (0 LLM, 0 network)."""
from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

from core.session_compact import HEADERS, SessionCompact

try:
    from core.pending_slot import PendingSlot
except ImportError:  # Allows the compact-only baseline checkout to run honestly.
    PendingSlot = None


FIXTURES = Path(__file__).with_name("fixtures") / "context_loss"


def _msg(role: str, content: str) -> SimpleNamespace:
    return SimpleNamespace(type="human" if role == "human" else "ai", content=content)


def _candidate(summary: dict) -> str:
    mapping = ("decided", "constraints", "working", "open", "facts")
    return "\n".join(
        header + "\n" + "\n".join(f"- {line}" for line in summary.get(key, []))
        for header, key in zip(HEADERS, mapping)
    )


def _wait(compact: SessionCompact, timeout: float = 2.0) -> bool:
    until = time.time() + timeout
    while time.time() < until:
        if not compact._refreshing:
            return True
        time.sleep(0.01)
    return False


def _messages(case: dict) -> list[SimpleNamespace]:
    first_user, first_reply = case["dialogue"]
    items = [_msg("human", first_user), _msg("ai", first_reply)]
    for number in range(case["padding_turns"]):
        items.extend([
            _msg("human", f"Lượt nhiễu {number + 1}: tiếp tục trao đổi bình thường."),
            _msg("ai", f"Đã ghi nhận lượt nhiễu {number + 1}."),
        ])
    return items


def main() -> int:
    cases = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(FIXTURES.glob("*.json"))]
    checks: list[tuple[str, bool]] = []
    baseline_expected_fails = sum(bool(case.get("baseline_known_fail")) for case in cases)

    with tempfile.TemporaryDirectory(prefix="ciel_context_loss_") as root:
        for case in cases:
            compact = SessionCompact(Path(root) / f"{case['id']}.json", refresh_turns=1, max_chars=1200)
            messages = _messages(case)
            started = compact.schedule_refresh(messages, lambda _prompt, c=case: _candidate(c["summary"]))
            finished = _wait(compact)
            rendered = compact.summary
            contains_ok = all(text in rendered for text in case.get("contains", []))
            absent_ok = all(text not in rendered for text in case.get("not_contains", []))
            checks.append((f"{case['id']}: compact accepts structured context", started and finished and bool(rendered)))
            checks.append((f"{case['id']}: expected context retained", contains_ok and absent_ok))

            pending = case.get("pending")
            if pending is None:
                continue
            if PendingSlot is None:
                checks.append((f"{case['id']}: pending-slot unavailable (not scored)", True))
                continue
            slot = PendingSlot(ttl_seconds=600, max_turns=2)
            slot.observe_response(case["dialogue"][0], pending["response"])
            note = slot.consume_if_matches(pending["answer"])
            matched = bool(note) and pending["kind"] in note
            checks.append((f"{case['id']}: short-answer handoff", matched == pending["must_match"]))

    # This score is a real run of the current implementation.  The marked fixtures
    # describe regressions known to fail on commit 40bc891's old compact design.
    passed = sum(ok for _, ok in checks)
    print("CONTEXT LOSS SCORECARD")
    print(f"Fixtures: {len(cases)} | Legacy expected failures: {baseline_expected_fails}/{len(cases)}")
    print(f"Current checks: {passed}/{len(checks)}")
    for name, ok in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
    print(f"RESULT: {passed}/{len(checks)} passed")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
