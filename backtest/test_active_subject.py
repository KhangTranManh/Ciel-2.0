"""Offline regression checks for the compact Brain-routing subject handoff."""
from __future__ import annotations

from core.active_subject import ActiveSubject


def main() -> int:
    checks: list[tuple[str, bool]] = []

    state = ActiveSubject(ttl_seconds=900, max_idle_turns=3, max_entities=5)
    checks.append(("empty state renders nothing", state.begin_turn(now=100) == ""))

    state.observe_tool(
        "stealth_search",
        {"query": "Windows laptop 16GB RAM around 15 million VND"},
        "Lenovo ThinkPad T14 and HP EliteBook 840 are available.",
    )
    snap = state.complete_turn(
        "find suitable laptops",
        "Options: **Lenovo ThinkPad T14**, **HP EliteBook 840**, **Invented Model Z**.",
        "tool",
        now=101,
    )
    checks.append(("search query becomes topic", bool(snap and "16GB" in snap.topic)))
    checks.append(("only evidence-grounded entities survive", bool(
        snap and snap.entities == ("Lenovo ThinkPad T14", "HP EliteBook 840")
    )))

    note = state.begin_turn(now=102)
    checks.append(("Brain note carries structured fields", all(
        marker in note for marker in ("Topic:", "Grounded entities:", "Last completed action:")
    )))
    checks.append(("Brain note excludes raw prior request", "find suitable laptops" not in note))

    state.complete_turn("thanks", "you're welcome", "chat", now=103)
    state.begin_turn(now=104)
    state.complete_turn("hello", "hello", "chat", now=104)
    state.begin_turn(now=105)
    expired = state.complete_turn("unrelated", "answer", "chat", now=105)
    checks.append(("three unrelated turns expire subject", expired is None and state.snapshot is None))

    unsafe = ActiveSubject()
    unsafe.begin_turn(now=200)
    unsafe.observe_tool("stealth_search", {"query": "mail x@example.com token abc"}, "result")
    unsafe.observe_tool("read_file", {"name": "D:/private/file.txt"}, "result")
    unsafe_snap = unsafe.complete_turn("x", "x", "tool", now=201)
    checks.append(("recipient secret and path cannot seed subject", unsafe_snap is None))

    ttl = ActiveSubject(ttl_seconds=10)
    ttl.begin_turn(now=300)
    ttl.observe_tool("get_market_price", {"symbol": "XAU/USD"}, "XAU/USD 2400")
    ttl.complete_turn("price", "**XAU/USD** 2400", "tool", now=300)
    checks.append(("wall-clock TTL expires subject", ttl.begin_turn(now=311) == "" and ttl.snapshot is None))

    for name, passed in checks:
        print(f"[{'PASS' if passed else 'FAIL'}] {name}")
    passed = sum(ok for _, ok in checks)
    print(f"RESULT: {passed}/{len(checks)} passed")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
