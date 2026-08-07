"""Deterministic validation for a proposed multi-tool plan.

The Brain may propose steps, but it does not decide whether those steps form an
executable plan.  This module is deliberately pure: it neither imports tools nor
executes them.  ``CielCore`` supplies the currently-loaded tool names and their
schema validator at the boundary.

Keep rules here structural and model-agnostic.  Permission decisions belong in
``core.permissions``; tool execution and result formatting belong in
``core.llm_connector``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Callable, Iterable

from .outbound import delivery_key


# Accept the one- and two-brace forms Ciel has always supported in plans.
_STEP_REF_RE = re.compile(
    r"\{{1,2}\s*(prev|step[_ ]?(\d+)(?:\.output)?)\s*\}{1,2}",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PlanIssue:
    """One deterministic diagnosis, expressed against a one-based step number."""

    code: str
    step: int
    message: str


@dataclass
class PlanValidation:
    """Validated plan plus explicit errors and safe, deterministic repairs."""

    steps: list[dict] = field(default_factory=list)
    errors: list[PlanIssue] = field(default_factory=list)
    repairs: list[PlanIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def error_text(self) -> str:
        """Compact user-safe text; detailed diagnostics remain available in logs."""
        if not self.errors:
            return ""
        details = "; ".join(
            f"step {issue.step}: {issue.message}" for issue in self.errors
        )
        return f"[PLAN_INVALID] Nothing was run because the tool plan is invalid: {details}"


def _references(value) -> list[tuple[str, int | None]]:
    """Return every ``prev`` / ``step_N`` reference anywhere in a value."""
    if isinstance(value, str):
        return [
            (match.group(1).lower(), int(match.group(2)) if match.group(2) else None)
            for match in _STEP_REF_RE.finditer(value)
        ]
    if isinstance(value, dict):
        found = []
        for item in value.values():
            found.extend(_references(item))
        return found
    if isinstance(value, (list, tuple)):
        found = []
        for item in value:
            found.extend(_references(item))
        return found
    return []


def validate_plan(
    proposed_steps,
    *,
    known_tools: Iterable[str],
    validate_args: Callable[[str, dict], str | None] | None = None,
) -> PlanValidation:
    """Validate and normalize an LLM-proposed plan without executing anything.

    A duplicate delivery is the only automatic repair.  It is removed only when
    no later step has a reference that would be renumbered by the removal; otherwise
    the plan is rejected instead of guessing at a dependency rewrite.
    """
    result = PlanValidation()
    available = {str(name).strip().lower() for name in known_tools if str(name).strip()}

    if not isinstance(proposed_steps, list) or not proposed_steps:
        result.errors.append(PlanIssue("EMPTY_PLAN", 0, "no executable steps were provided"))
        return result

    normalized: list[dict] = []
    for index, raw in enumerate(proposed_steps, 1):
        if not isinstance(raw, dict):
            result.errors.append(PlanIssue("INVALID_STEP", index, "step must be an object"))
            continue

        name = raw.get("tool_name")
        if not isinstance(name, str) or not name.strip():
            result.errors.append(PlanIssue("MISSING_TOOL", index, "tool_name is required"))
            continue
        name = name.strip().lower()

        args = raw.get("tool_args", {})
        if args is None:
            args = {}
        if not isinstance(args, dict):
            result.errors.append(PlanIssue("INVALID_ARGS", index, "tool_args must be an object"))
            continue
        if name not in available:
            result.errors.append(PlanIssue("UNKNOWN_TOOL", index, f"'{name}' is not loaded"))
            continue

        # Preserve optional metadata added by future planners, while normalizing the
        # only two execution fields Ciel owns.
        step = dict(raw)
        step["tool_name"] = name
        step["tool_args"] = dict(args)
        normalized.append(step)

        if validate_args:
            try:
                arg_error = validate_args(name, args)
            except Exception as exc:  # A schema bug must fail closed for the plan.
                arg_error = f"could not validate arguments ({type(exc).__name__})"
            if arg_error:
                result.errors.append(PlanIssue("SCHEMA_ERROR", index, str(arg_error)))

    if result.errors:
        return result

    for index, step in enumerate(normalized, 1):
        for ref_name, ref_index in _references(step["tool_args"]):
            if ref_name == "prev" and index == 1:
                result.errors.append(PlanIssue(
                    "UNRESOLVED_REFERENCE", index, "{prev} has no preceding step",
                ))
            elif ref_index is not None and not 1 <= ref_index < index:
                result.errors.append(PlanIssue(
                    "INVALID_REFERENCE", index,
                    f"{{step_{ref_index}}} must reference an earlier step",
                ))

    if result.errors:
        return result

    # Delivery idempotency remains enforced again at execute_tool(), because a plan can
    # be extended by the agent loop.  Catching it here gives a clearer plan-level audit.
    first_delivery: dict[str, int] = {}
    drop_indexes: set[int] = set()
    for index, step in enumerate(normalized, 1):
        key = delivery_key(step["tool_name"], step["tool_args"])
        if not key:
            continue
        if key not in first_delivery:
            first_delivery[key] = index
            continue

        # Removing this step is safe only if nothing after it refers to numbered steps.
        # Otherwise a deletion would silently change the meaning of {step_N}.
        later_references = any(
            _references(candidate["tool_args"])
            for candidate in normalized[index:]
        )
        if later_references:
            result.errors.append(PlanIssue(
                "DUPLICATE_DELIVERY", index,
                f"duplicates delivery from step {first_delivery[key]} and later dependencies prevent a safe repair",
            ))
        else:
            drop_indexes.add(index)
            result.repairs.append(PlanIssue(
                "DUPLICATE_DELIVERY_REMOVED", index,
                f"duplicate delivery from step {first_delivery[key]} was removed",
            ))

    if result.errors:
        return result
    result.steps = [step for index, step in enumerate(normalized, 1) if index not in drop_indexes]
    return result
