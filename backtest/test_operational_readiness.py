"""Offline operational-readiness regression checks (no mailbox or web calls)."""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from core.tool_manager import ToolManager
from core.capability_triggers import build_capability_loss_triggers
from core.notifier import Notifier
from core.triggers import TriggerEngine
from scripts.harness.attribution import trace_target
from scripts.prompt_harness import _tag_self_correction_reasoning, _target_for
from skills.external.gmail_ops import _gmail_failure_reason
from skills.internal.productivity_ops import _normalize_weather_temperature
from agent_system.utils.usage import format_latency, format_usage, invoke_with_latency

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


def test_capability_loss_notification() -> None:
    print("\n[5] Capability loss alerts are opt-in, safe, and deduplicated")
    statuses = [
        {"module": "gmail_ops", "state": "unavailable",
         "reason": "oauth_or_credentials_error", "exception": "private provider text"},
        {"module": "calendar_ops", "state": "not_configured",
         "reason": "token_missing"},
        {"module": "vision_ops", "state": "disabled",
         "reason": "disabled_by_configuration"},
        {"module": "todo_ops", "state": "ready", "reason": "tools_loaded"},
    ]
    check("disabled unless explicitly selected",
          build_capability_loss_triggers(enabled_names=(), statuses=statuses) == [])
    triggers = build_capability_loss_triggers(
        enabled_names=("capability_loss",), statuses=statuses
    )
    check("only unexpected failure states alert", len(triggers) == 1)
    note = triggers[0].check(1000.0)
    check("uses stable classified identity",
          note.key == "capability_loss:gmail_ops:oauth_or_credentials_error")
    check("does not retain raw provider exception text", "private" not in note.render())
    check("gives an actionable Gmail recovery step", "Authorize Gmail" in note.action)

    class Channel:
        name = "test"

        def __init__(self):
            self.sent = []

        def is_live(self, _now):
            return True

        def send(self, sent_note, _now):
            self.sent.append(sent_note.key)
            return True

    channel = Channel()
    notifier = Notifier(channels=(channel,), daily_budget=8)
    engine = TriggerEngine(notifier, triggers)
    first = engine.tick(1000.0)
    second = engine.tick(1301.0)
    check("first startup finding is delivered", first[0][1].status == "delivered")
    check("same finding is suppressed by cooldown",
          second[0][1].status == "suppressed" and len(channel.sent) == 1)


def test_latency_telemetry_contract() -> None:
    print("\n[6] LLM latency telemetry is separate and failure-transparent")
    check("LLM_CALL usage format is unchanged",
          format_usage("model-a", {"input": 2, "output": 3, "total": 5})
          == "model=model-a in=2 out=3 total=5")
    check("latency format contains metadata only",
          format_latency("worker", "model-a", 12, "success")
          == "component=worker model=model-a duration_ms=12 outcome=success")

    events = []

    class Client:
        def __init__(self, fail=False):
            self.fail = fail

        def invoke(self, _messages):
            if self.fail:
                raise RuntimeError("private provider failure")
            return "provider-result"

    callback = lambda *event: events.append(event)
    result = invoke_with_latency(
        Client(), [], component="brain", model="model-b", on_latency=callback
    )
    check("successful result passes through unchanged", result == "provider-result")
    check("successful attempt emits one timing",
          len(events) == 1 and events[0][3] == "success")
    try:
        invoke_with_latency(
            Client(fail=True), [], component="worker", model="model-c",
            on_latency=callback,
        )
    except RuntimeError as exc:
        same_error = str(exc) == "private provider failure"
    else:
        same_error = False
    check("provider errors still propagate unchanged", same_error)
    check("failed attempt emits timing without exception text",
          len(events) == 2 and events[1][3] == "error" and len(events[1]) == 4)


def test_minimal_env_example() -> None:
    print("\n[7] Minimal environment template is safe and complete")
    path = Path(__file__).resolve().parent.parent / ".env.example"
    check("tracked environment template exists", path.is_file())
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    values = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    required = {
        "BRAIN_PROVIDER", "BRAIN_MODEL", "WORKER_PROVIDER", "CODER_MODEL",
        "API_KEY", "BASE_URL",
    }
    check("documents every standard provider input", required <= values.keys(), repr(values.keys()))
    check("uses CODER_MODEL rather than ignored WORKER_MODEL",
          "CODER_MODEL" in values and "WORKER_MODEL" not in values)
    check("keeps optional model roles disabled",
          values.get("ROUTER_ASSISTANT_ENABLED") == "false"
          and values.get("MIDDLEWARE_ENABLED") == "false")
    check("keeps destructive confirmation enabled",
          values.get("DISABLE_SAFETY_GATE") == "false")
    check("contains placeholders, not a deployed credential",
          "replace-with-provider-key" in text
          and "TELEGRAM_BOT_TOKEN" not in values
          and "CIEL_API_TOKEN" not in values)


def main() -> int:
    test_gmail_readiness()
    test_weather_normalization()
    test_harness_categories()
    test_health_payload()
    test_capability_loss_notification()
    test_latency_telemetry_contract()
    test_minimal_env_example()
    total = _passed + len(_failed)
    print(f"\nRESULT: {_passed}/{total} passed")
    if _failed:
        print("Failures: " + ", ".join(_failed))
    return 0 if not _failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
