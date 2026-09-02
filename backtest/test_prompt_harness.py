"""Offline regression checks for the deny-by-default prompt harness."""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.harness.agent_loop import diagnose_finding, diagnosis_allows_prompt, propose_value
from scripts.harness.attribution import trace_target
from scripts.harness.policy import HarnessPolicy, PolicyError, PromptTarget, SAFE_TEST_COMMAND
from scripts.harness.project_report import discover_tool_inventory, write_project_report
from scripts.harness.prompt_value import CandidateError, apply_candidate, build_candidate
from scripts.harness.sanitizer import sanitize_text


_passed = 0
_failed: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    global _passed
    if condition:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed.append(name)
        print(f"  FAIL  {name}" + (f" -- {detail}" if detail else ""))


def _policy() -> HarnessPolicy:
    return HarnessPolicy(
        targets=(PromptTarget("prompts.py", "SYSTEM_PROMPT"),),
        blocked_parts=(".env", "credentials.json", "ciel_data", "logs", "secrets"),
        max_value_chars=1000,
        max_growth_chars=200,
        test_command=SAFE_TEST_COMMAND,
    )


def test_private_boundaries_and_sanitizer() -> None:
    print("\n[1] Private data is blocked or redacted before model use")
    policy = HarnessPolicy(
        targets=(PromptTarget(".env", "SYSTEM_PROMPT"),),
        blocked_parts=(".env", "credentials.json", "ciel_data"),
        max_value_chars=1000,
        max_growth_chars=200,
        test_command=("{python}", "-c", "pass"),
    )
    try:
        policy.resolve_target(Path.cwd(), policy.targets[0])
        blocked = False
    except PolicyError:
        blocked = True
    check("allow-list cannot override the private-path deny list", blocked)

    immutable_policy = HarnessPolicy(
        targets=(PromptTarget("config/credentials.py", "SYSTEM_PROMPT"),),
        blocked_parts=(),
        max_value_chars=1000,
        max_growth_chars=200,
        test_command=("{python}", "-c", "pass"),
    )
    try:
        immutable_policy.resolve_target(Path.cwd(), immutable_policy.targets[0])
        immutable_blocked = False
    except PolicyError:
        immutable_blocked = True
    check("custom policy cannot disable immutable private-name blocks", immutable_blocked)

    raw = "email=a@example.com API_KEY=sk-abcdefghijklmnopqrstuvwxyz123456 path=C:\\Users\\me\\secret.txt"
    clean = sanitize_text(raw)
    check("email is redacted", "a@example.com" not in clean, clean)
    check("token is redacted", "sk-abcdefghijklmnopqrstuvwxyz" not in clean, clean)
    check("local path is redacted", "C:\\Users" not in clean, clean)


def test_value_only_apply_and_rollback() -> None:
    print("\n[2] Apply changes only one literal value and validates tests")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = root / "prompts.py"
        original = b'SYSTEM_PROMPT = "old rule"\r\nKEEP = "untouched"\r\n'
        path.write_bytes(original)
        policy = _policy()
        exact_value = 'old rule\\n is text\nnew bounded rule with """ marker'
        candidate = build_candidate(root, policy, policy.targets[0], exact_value, "recurring omission")
        with patch("scripts.harness.prompt_value.subprocess.run", return_value=SimpleNamespace(
            returncode=0, stdout="ok", stderr="",
        )):
            result = apply_candidate(root, policy, candidate)
        updated = path.read_bytes().decode("utf-8")
        check("passing candidate is applied", result.changed and result.tests_passed)
        check("unrelated source remains byte-identical", 'KEEP = "untouched"\r\n' in updated, updated)
        namespace: dict = {}
        exec(updated, namespace)
        check("only runtime prompt value changed", namespace["SYSTEM_PROMPT"] == exact_value, repr(namespace["SYSTEM_PROMPT"]))
        check("multiline prompt remains reviewable", updated.startswith('SYSTEM_PROMPT = """'), updated)

        before_failure = path.read_bytes()
        failing_policy = _policy()
        failing = build_candidate(
            root, failing_policy, failing_policy.targets[0], "old rule\nsecond rule", "bad candidate",
        )
        with patch("scripts.harness.prompt_value.subprocess.run", return_value=SimpleNamespace(
            returncode=7, stdout="failed", stderr="",
        )):
            failed_result = apply_candidate(root, failing_policy, failing)
        check("failed tests report no retained change", not failed_result.changed and not failed_result.tests_passed)
        check("failed tests restore exact original bytes", path.read_bytes() == before_failure)

        timeout_candidate = build_candidate(
            root, failing_policy, failing_policy.targets[0], "old rule\ntimeout rule", "timeout",
        )
        with patch("scripts.harness.prompt_value.subprocess.run", side_effect=subprocess.TimeoutExpired("unit", 300)):
            timeout_result = apply_candidate(root, failing_policy, timeout_candidate)
        check("test timeout restores exact original bytes", not timeout_result.changed and path.read_bytes() == before_failure)


def test_stale_and_secret_candidates() -> None:
    print("\n[3] Secret-like and stale proposals fail closed")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = root / "prompts.py"
        path.write_text('SYSTEM_PROMPT = "old"\n', encoding="utf-8")
        policy = _policy()
        try:
            build_candidate(root, policy, policy.targets[0], "API_KEY=sk-abcdefghijklmnopqrstuvwxyz123456", "x")
            secret_blocked = False
        except ValueError:
            secret_blocked = True
        check("secret-like model output is rejected", secret_blocked)

        candidate = build_candidate(root, policy, policy.targets[0], "new", "x")
        path.write_text('SYSTEM_PROMPT = "changed elsewhere"\n', encoding="utf-8")
        try:
            apply_candidate(root, policy, candidate)
            stale_blocked = False
        except CandidateError:
            stale_blocked = True
        check("stale candidate cannot overwrite newer source", stale_blocked)


def test_brain_worker_contract() -> None:
    print("\n[4] Brain and Worker receive bounded contracts")
    seen: dict[str, str] = {}

    def brain(prompt: str) -> str:
        seen["brain"] = prompt
        return ('{"root_cause":"missing completion rule",'
                '"recommended_change":"add one completion rule","confidence":0.8,'
                '"change_type":"prompt","target_supported":true}')

    diagnosis = diagnose_finding({
        "signature": "INCOMPLETE_MULTISTEP",
        "target": "prompts.py::SYSTEM_PROMPT",
        "examples": ["sent to owner@example.com from C:\\Users\\owner\\private.txt"],
    }, brain)

    def worker(prompt: str) -> str:
        seen["worker"] = prompt
        return '{"new_value":"old rule\\ncomplete every requested step"}'

    value = propose_value("old rule", diagnosis, worker)
    check("Brain evidence is sanitized", "owner@example.com" not in seen["brain"] and "C:\\Users" not in seen["brain"])
    check("Brain classifies the exact target before Worker runs", diagnosis_allows_prompt(diagnosis))
    check("Worker returns value, not a patch", value == "old rule\ncomplete every requested step", repr(value))


def test_attribution_and_contract_boundaries() -> None:
    print("\n[5] Attribution and caller contracts fail closed")
    middleware_target = ("agent_system/models/middleware.py", "MIDDLEWARE_SYSTEM_PROMPT")
    inactive = trace_target(
        "HOLLOW_CONTENT",
        "send_gmail_message",
        middleware_target,
        runtime_controls={"MIDDLEWARE_ENABLED": False},
    )
    check("historical Middleware evidence is preserved", inactive.relation == "direct-event")
    check("currently disabled Middleware is blocked before model calls", not inactive.proposal_eligible)

    enabled = trace_target(
        "HOLLOW_CONTENT",
        "send_gmail_message",
        middleware_target,
        runtime_controls={"MIDDLEWARE_ENABLED": True},
    )
    check("enabled direct target may reach Brain review", enabled.proposal_eligible)

    provider = trace_target(
        "PROVIDER_ERROR",
        "send_gmail_message",
        middleware_target,
        runtime_controls={"MIDDLEWARE_ENABLED": True},
    )
    check("provider failures never become prompt candidates", not provider.proposal_eligible)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = root / "prompts.py"
        path.write_text('SYSTEM_PROMPT = \'Return {"approved": true, "revised_body": null}\'\n', encoding="utf-8")
        policy = _policy()
        try:
            build_candidate(
                root,
                policy,
                policy.targets[0],
                'Return {"approved": false, "block": true, "revised_body": null}',
                "invented caller contract",
            )
            schema_blocked = False
        except CandidateError:
            schema_blocked = True
        check("new JSON response fields require a code change", schema_blocked)

        try:
            build_candidate(
                root,
                policy,
                policy.targets[0],
                'Return {"approved": false, "revised_body": null}. Caller MUST trigger regeneration.',
                "invented regeneration path",
            )
            caller_blocked = False
        except CandidateError:
            caller_blocked = True
        check("prompt cannot assign new behavior to its caller", caller_blocked)


def test_full_project_report_is_static_and_bounded() -> None:
    print("\n[6] Full-project report uses static inventory and no live tools")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        pack = root / "skills" / "internal" / "example_ops.py"
        pack.parent.mkdir(parents=True)
        pack.write_text(
            """
StructuredTool.from_function(func=alpha, name="alpha_tool", description="a")
StructuredTool.from_function(beta, description="b")
StructuredTool.from_function(func=dynamic_func, name=dynamic_name, description="c")
""".strip(),
            encoding="utf-8",
        )
        prompt = root / "prompts.py"
        prompt.write_text('SYSTEM_PROMPT = "old rule"\n', encoding="utf-8")

        inventory = discover_tool_inventory(root)
        names = {item.name for item in inventory.tools}
        check("static inventory finds explicit and function-name tools", names == {"alpha_tool", "beta"}, repr(names))
        check("dynamic registrations are reported rather than imported", inventory.dynamic_registrations == 1)

        attribution = SimpleNamespace(
            proposal_eligible=True,
            relation="inferred",
            runtime_state="active-core-path",
        )
        finding = SimpleNamespace(
            signature="TOOL_ERROR / alpha_tool",
            count=1,
            prompt_file="prompts.py",
            prompt_target="SYSTEM_PROMPT",
            attribution=attribution,
        )
        output = write_project_report(
            root,
            [finding],
            5,
            _policy(),
            {"alpha_tool": ("prompts.py", "SYSTEM_PROMPT")},
            root / "agent_output",
            run_units=False,
        )
        report = output.read_text(encoding="utf-8")
        check("one Markdown report is produced", output.is_file() and report.startswith("# Ciel Harness"))
        check("report states live mutations are excluded", "does **not** execute live external or mutating tools" in report)
        check("report records skipped unit status honestly", "Maintained unit regression: **SKIPPED**" in report)


def main() -> int:
    test_private_boundaries_and_sanitizer()
    test_value_only_apply_and_rollback()
    test_stale_and_secret_candidates()
    test_brain_worker_contract()
    test_attribution_and_contract_boundaries()
    test_full_project_report_is_static_and_bounded()
    total = _passed + len(_failed)
    print(f"\nRESULT: {_passed}/{total} passed")
    if _failed:
        print("Failures: " + ", ".join(_failed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
