"""Offline checks for the rolling, structured, Worker-only session compact."""
from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

from core.session_compact import SessionCompact, HEADERS


def message(kind: str, content: str):
    return SimpleNamespace(type=kind, content=content)


def structured(decided=(), open_=(), facts=()):
    def sec(h, items):
        return h + "\n" + "\n".join(f"- {i}" for i in items)
    return "\n".join([sec(HEADERS[0], decided), sec(HEADERS[1], []), sec(HEADERS[2], []),
                      sec(HEADERS[3], open_), sec(HEADERS[4], facts)])


def wait(compact, cond, timeout=3):
    end = time.time() + timeout
    while time.time() < end:
        if cond() and not compact._refreshing:
            return True
        time.sleep(0.01)
    return False


def main() -> int:
    checks = []
    log = []
    logger = lambda *a: log.append(a)

    with tempfile.TemporaryDirectory(prefix="ciel_compact_") as d:
        path = Path(d) / "session_compact.json"

        # 1. Rolling over 44 turns with a history window of 20: turn-2 decision survives.
        compact = SessionCompact(path, refresh_turns=4, max_chars=1200)
        history, prompts = [], []
        decided = []

        def summarizer(prompt):
            prompts.append(prompt)
            if "Chọn gói Basic" in prompt and "Chọn gói Basic" not in "".join(decided):
                decided.append("Chọn gói Basic")
            return structured(decided, ["giá chưa chốt"], ["16 giờ"])

        for t in range(44):
            human = "Chọn gói Basic nhé" if t == 1 else f"câu {t} về tối ưu token DeepSeek"
            history.extend([message("human", human), message("ai", f"ok {t}")])
            history = history[-20:]
            compact.schedule_refresh(history, summarizer, logger)
            wait(compact, lambda: True)
        wait(compact, lambda: compact.version >= 5)
        checks.append(("rolling compact keeps a turn-2 decision after 40+ turns",
                       "Chọn gói Basic" in compact.summary and compact.version >= 5))
        checks.append(("later prompts carry the previous compact",
                       any("[COMPACT TRƯỚC]\nĐã chốt:" in p for p in prompts[2:])))
        checks.append(("plain word 'token' is kept in the source, not the whole message",
                       any("tối ưu token DeepSeek" in p for p in prompts)
                       and not any("sensitive content omitted" in p for p in prompts)))
        checks.append(("renders as factual Worker-only background",
                       "SESSION COMPACT" in compact.render() and "authorization" in compact.render()))

        # 2. Secret values masked; email masked.
        c2 = SessionCompact(Path(d) / "c2.json", refresh_turns=1)
        seen = []
        c2.schedule_refresh([
            message("human", "mail master@example.com; password is do-not-store; token=abc123xyz; "
                             "key sk-1234567890123456789012345678901234567890 tối ưu token nhé"),
            message("ai", "ok")], lambda p: seen.append(p) or structured(["x"]), logger)
        wait(c2, lambda: bool(seen))
        s = seen[0] if seen else ""
        checks.append(("secret values never reach the model",
                       bool(seen) and all(x not in s for x in (
                           "master@example.com", "do-not-store", "abc123xyz", "sk-1234567890"))))
        checks.append(("bare word token survives redaction", "tối ưu token nhé" in s))

        # 3. A candidate that drops a decided line is rejected, old kept.
        c3 = SessionCompact(Path(d) / "c3.json", refresh_turns=1)
        c3.schedule_refresh([message("human", "dùng PostgreSQL"), message("ai", "ok")],
                            lambda p: structured(["Dùng PostgreSQL"]), logger)
        wait(c3, lambda: c3.version == 1)
        before = c3.summary
        c3.schedule_refresh([message("human", "thêm cache"), message("ai", "ok")],
                            lambda p: structured(["Có cache"]), logger)
        wait(c3, lambda: any(l[1] == "session_compact_rejected" for l in log))
        checks.append(("candidate dropping a decided line is rejected",
                       c3.summary == before and c3.version == 1
                       and any(l[1] == "session_compact_rejected" for l in log)))
        # ...but allowed when the new transcript cancels it.
        c3.schedule_refresh([message("human", "hủy PostgreSQL, đổi sang SQLite"), message("ai", "ok")],
                            lambda p: structured(["Dùng SQLite"]), logger)
        wait(c3, lambda: c3.version == 2)
        checks.append(("explicit change lets a decided line be replaced", "SQLite" in c3.summary))

        # 4. Missing headers / empty rejected.
        c4 = SessionCompact(Path(d) / "c4.json", refresh_turns=1)
        c4.schedule_refresh([message("human", "a"), message("ai", "b")],
                            lambda p: "- chỉ có bullet", logger)
        wait(c4, lambda: False, 0.3)
        checks.append(("unstructured candidate rejected", c4.version == 0 and not c4.summary))

        # 5. Expiry: >24h old compact is not rendered.
        c3._updated_at = int(time.time()) - 25 * 3600
        checks.append(("expired compact is not rendered", c3.render() == ""))

        # 6. Legacy file loads and is rendered as legacy.
        legacy = Path(d) / "legacy.json"
        legacy.write_text(json.dumps({"summary": "- Topic: HC\n- Fact: 7,215",
                                      "turn_count": 4, "source_turn_count": 4,
                                      "updated_at": int(time.time())}), encoding="utf-8")
        lc = SessionCompact(legacy)
        checks.append(("legacy compact file loads", "7,215" in lc.summary and lc.version == 0
                       and "7,215" in lc.render()))

        # 7. Cap trimming drops whole low-priority lines, keeps Đã chốt.
        c7 = SessionCompact(Path(d) / "c7.json", refresh_turns=1, max_chars=200)
        c7.schedule_refresh([message("human", "x"), message("ai", "y")],
                            lambda p: structured(["Quyết định quan trọng"], ["hỏi giá?"],
                                                 [f"dữ kiện số {i} " + "z" * 30 for i in range(10)]), logger)
        wait(c7, lambda: c7.version == 1)
        checks.append(("cap trims facts first and keeps decided + open question",
                       len(c7.summary) <= 200 and "Quyết định quan trọng" in c7.summary
                       and "hỏi giá?" in c7.summary))

        # 8. Persistence + clear.
        restored = SessionCompact(Path(d) / "c3.json", refresh_turns=1)
        checks.append(("persists across restart with version", restored.version == 2))
        restored.clear()
        checks.append(("clear removes the durable compact",
                       not (Path(d) / "c3.json").exists() and not restored.summary))

    for name, ok in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
    passed = sum(ok for _, ok in checks)
    print(f"RESULT: {passed}/{len(checks)} passed")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
