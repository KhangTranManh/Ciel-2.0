"""Regression checks for deterministic multi-tool plan validation (0 LLM / no I/O)."""
from __future__ import annotations

from core.plan_validation import validate_plan


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


KNOWN = {"read_file", "write_file", "send_gmail_message", "send_telegram"}


def test_valid_dependency_and_schema():
    print("\n[1] Valid plans retain their declared dependency order")
    seen = []

    def schema(name, args):
        seen.append((name, args))
        return None

    plan = [
        {"tool_name": "READ_FILE", "tool_args": {"filename": "ciel_workspace/a.txt"}},
        {"tool_name": "write_file", "tool_args": {
            "filename": "agent_output/out.txt", "content": "{{step_1}}",
        }},
    ]
    result = validate_plan(plan, known_tools=KNOWN, validate_args=schema)
    check("valid dependent plan is accepted", result.ok, result.error_text())
    check("tool names are normalized once", result.steps[0]["tool_name"] == "read_file")
    check("schema is checked before any execution", len(seen) == 2, repr(seen))


def test_invalid_structure_and_dependencies():
    print("\n[2] Invalid plans fail before the first tool can run")
    forward = validate_plan(
        [{"tool_name": "read_file", "tool_args": {"filename": "{step_2}"}}],
        known_tools=KNOWN,
    )
    check("future step reference is rejected", not forward.ok and forward.errors[0].code == "INVALID_REFERENCE",
          forward.error_text())

    previous = validate_plan(
        [{"tool_name": "read_file", "tool_args": {"filename": "{prev}"}}],
        known_tools=KNOWN,
    )
    check("first-step {prev} is rejected", not previous.ok and previous.errors[0].code == "UNRESOLVED_REFERENCE",
          previous.error_text())

    unknown = validate_plan(
        [{"tool_name": "invent_tool", "tool_args": {}}], known_tools=KNOWN,
    )
    check("unloaded tool is rejected", not unknown.ok and unknown.errors[0].code == "UNKNOWN_TOOL",
          unknown.error_text())

    malformed = validate_plan(
        [{"tool_name": "read_file", "tool_args": "not-an-object"}], known_tools=KNOWN,
    )
    check("non-object arguments are rejected", not malformed.ok and malformed.errors[0].code == "INVALID_ARGS",
          malformed.error_text())


def test_duplicate_delivery_repairs_without_renumbering():
    print("\n[3] Delivery duplicates are repaired only when that is dependency-safe")
    duplicate = validate_plan(
        [
            {"tool_name": "send_gmail_message", "tool_args": {"to": "a@example.com", "message": "one"}},
            {"tool_name": "send_gmail_message", "tool_args": {"to": "a@example.com", "message": "two"}},
        ],
        known_tools=KNOWN,
    )
    check("same-recipient delivery plan remains valid", duplicate.ok, duplicate.error_text())
    check("later duplicate is removed", len(duplicate.steps) == 1 and len(duplicate.repairs) == 1,
          repr(duplicate.repairs))

    referenced_after_duplicate = validate_plan(
        [
            {"tool_name": "send_gmail_message", "tool_args": {"to": "a@example.com", "message": "one"}},
            {"tool_name": "send_gmail_message", "tool_args": {"to": "a@example.com", "message": "two"}},
            {"tool_name": "write_file", "tool_args": {
                "filename": "agent_output/out.txt", "content": "{step_2}",
            }},
        ],
        known_tools=KNOWN,
    )
    check("duplicate is rejected when removal would renumber a reference",
          not referenced_after_duplicate.ok and referenced_after_duplicate.errors[-1].code == "DUPLICATE_DELIVERY",
          referenced_after_duplicate.error_text())


def main() -> int:
    test_valid_dependency_and_schema()
    test_invalid_structure_and_dependencies()
    test_duplicate_delivery_repairs_without_renumbering()
    total = _passed + len(_failed)
    print(f"\nRESULT: {_passed}/{total} passed")
    if _failed:
        print("Failures: " + ", ".join(_failed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
