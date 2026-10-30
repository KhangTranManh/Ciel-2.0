"""Offline operational-readiness regression checks (no mailbox or web calls)."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from core.tool_manager import ToolManager
from scripts.harness.attribution import trace_target
from scripts.prompt_harness import _tag_self_correction_reasoning, _target_for
from skills.external.gmail_ops import _gmail_failure_reason
from skills.internal.productivity_ops import _normalize_weather_temperature

_passed, _failed = 0, []


def check(name: str, condition: bool, detail: str = "") -> None:
    global _passed
    if condition:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed.append(name)
        print(f"  FAIL  {name}" + (f" -- {detail}" if detail else ""))


def test_gmail_readiness() -> None:
    print("\n[1] Gmail readiness is safe and actionable")
    manager = object.__new__(ToolManager)
    manager.skill_statuses = [{
        "module": "gmail_ops", "category": "external", "state": "unavailable",
        "reason": "oauth_or_credentials_error", "tool_count": 0,
    }]
    status = manager.get_skill_status("gmail_ops")
    check("preserves a classified Gmail failure", status["reason"] == "oauth_or_credentials_error")
    status["reason"] = "changed"
    check("status is returned as a copy", manager.get_skill_status("gmail_ops")["reason"] == "oauth_or_credentials_error")
    missing = manager.get_skill_status("unknown_ops")
    check("unknown pack has safe fallback", missing["state"] == "not_loaded" and missing["tool_count"] == 0)
    check("OAuth errors are classified without raw error text",
          _gmail_failure_reason(RuntimeError("invalid_grant: token revoked")) == "oauth_or_credentials_error")
    check("non-OAuth initialization errors remain generic",
          _gmail_failure_reason(RuntimeError("incompatible toolkit")) == "initialization_error")


def test_weather_normalization() -> None:
    print("\n[2] Weather temperatures always compare in both units")
    degree = chr(176)
    metric = _normalize_weather_temperature(f"Hanoi: +30{degree}C")
    imperial = _normalize_weather_temperature(f"Hanoi: 86{degree}F")
    check("metric source adds Fahrenheit", f"30.0{degree}C (86.0{degree}F)" in metric, ascii(metric))
    check("imperial source converts to Celsius", f"30.0{degree}C (86.0{degree}F)" in imperial, ascii(imperial))
    both = f"Hanoi: 30{degree}C / 86{degree}F"
    check("existing two-unit result stays intact", _normalize_weather_temperature(both) == both)


def test_harness_categories() -> None:
    print("\n[3] Harness separates runtime causes from prompt-quality signals")
    cases = {
        "Gmail OAuth refresh token expired": "GMAIL_AUTH_OR_CONFIGURATION",
        "Tool search_gmail does not exist": "TOOL_UNAVAILABLE",
        f"Weather returned 85{chr(176)}F but the answer needs Celsius": "WEATHER_UNIT_CONVERSION",
        "Search results were irrelevant to the request": "SEARCH_RESULT_QUALITY",
    }
    for text, expected in cases.items():
        check(f"tags {expected}", _tag_self_correction_reasoning(text) == expected)
    target = _target_for("SELF_CORRECTION:GMAIL_AUTH_OR_CONFIGURATION", "search_gmail")
    check("Gmail auth points to runtime diagnostics", target[0] == "Gmail runtime configuration")
    blocked = trace_target("SELF_CORRECTION:GMAIL_AUTH_OR_CONFIGURATION", "search_gmail", target)
    check("Gmail auth cannot produce a prompt candidate", not blocked.proposal_eligible)
    quality_target = _target_for("SELF_CORRECTION:SEARCH_RESULT_QUALITY", "stealth_search")
    check("search quality keeps the web prompt owner", quality_target[0].endswith("web_agent_ops.py"))


def test_health_payload() -> None:
    print("\n[4] API health exposes only safe Gmail operational state")
    import main_api

    class Manager:
        def get_skill_status(self, module: str) -> dict:
            assert module == "gmail_ops"
            return {"module": module, "state": "ready", "reason": "tools_loaded", "tool_count": 9}

    saved = main_api.ciel_agent
    try:
        main_api.ciel_agent = SimpleNamespace(core=SimpleNamespace(tool_manager=Manager()))
        payload = asyncio.run(main_api.health())
        gmail = payload["integrations"]["gmail"]
        check("health reports loaded Gmail tools", gmail == {"module": "gmail_ops", "state": "ready", "reason": "tools_loaded", "tool_count": 9}, repr(gmail))
    finally:
        main_api.ciel_agent = saved


def main() -> int:
    test_gmail_readiness()
    test_weather_normalization()
    test_harness_categories()
    test_health_payload()
    total = _passed + len(_failed)
    print(f"\nRESULT: {_passed}/{total} passed")
    if _failed:
        print("Failures: " + ", ".join(_failed))
    return 0 if not _failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
