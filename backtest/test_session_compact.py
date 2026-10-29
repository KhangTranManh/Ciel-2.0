"""Offline checks for the Worker-only persisted conversation compact."""
from __future__ import annotations

import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

from core.session_compact import SessionCompact


def message(kind: str, content: str):
    return SimpleNamespace(type=kind, content=content)


def main() -> int:
    checks = []
    with tempfile.TemporaryDirectory(prefix="ciel_compact_") as directory:
        path = Path(directory) / "session_compact.json"
        compact = SessionCompact(path, refresh_turns=2, max_chars=180)
        transcript = [
            message("human", "HC income is 7,215 after 16 hours."),
            message("ai", "A package should beat the grind value."),
            message("human", "Contact me at master@example.com; password is do-not-store; "
                             "key sk-1234567890123456789012345678901234567890."),
            message("ai", "We need package tiers and an unresolved price decision."),
        ]
        seen = []

        def summarizer(prompt: str) -> str:
            seen.append(prompt)
            return "- Topic: HC game packages\n- Fact: 7,215 HC at 16 hours\n- Open: choose prices"

        checks.append(("queues after the configured number of completed turns",
                       compact.schedule_refresh(transcript, summarizer)))
        deadline = time.time() + 2
        while not compact.summary and time.time() < deadline:
            time.sleep(0.01)
        checks.append(("stores a compact summary", "7,215 HC" in compact.summary))
        checks.append(("redacts email and secret-like transcript content before the model sees it",
                       bool(seen) and "master@example.com" not in seen[0]
                       and "do-not-store" not in seen[0]
                       and "sk-1234567890123456789012345678901234567890" not in seen[0]))
        checks.append(("renders as factual Worker-only background",
                       "SESSION COMPACT" in compact.render() and "authorization" in compact.render()))

        restored = SessionCompact(path, refresh_turns=2, max_chars=180)
        checks.append(("persists across a process restart", restored.summary == compact.summary))
        restored.clear()
        checks.append(("clear removes the durable compact", not path.exists() and not restored.summary))

    for name, passed in checks:
        print(f"[{'PASS' if passed else 'FAIL'}] {name}")
    passed = sum(ok for _, ok in checks)
    print(f"RESULT: {passed}/{len(checks)} passed")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
