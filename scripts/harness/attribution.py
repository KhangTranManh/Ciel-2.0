"""Deterministic provenance checks for prompt-harness findings.

Log correlation can show that a component emitted an event, but that is not proof
that changing its prompt will fix the failure. This module keeps those two claims
separate and prevents model stages from running for inactive or non-prompt targets.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


_MIDDLEWARE_SIGNALS = {
    "LANGUAGE_MISMATCH",
    "NUMERIC_CONTRADICTION",
    "HOLLOW_CONTENT",
    "RELEVANCE_MISMATCH",
    "PROVIDER_ERROR",
}
_MIDDLEWARE_TARGET = (
    "agent_system/models/middleware.py",
    "MIDDLEWARE_SYSTEM_PROMPT",
)


@dataclass(frozen=True)
class TargetAttribution:
    """Why a finding points at a target, and whether proposal may continue."""

    relation: str
    evidence_source: str
    runtime_state: str
    proposal_eligible: bool
    reason: str


def _runtime_controls(overrides: Mapping[str, bool] | None) -> dict[str, bool]:
    if overrides is not None:
        return {str(key): bool(value) for key, value in overrides.items()}

    # Config may read private environment values, but this boundary exposes only a
    # named boolean feature state. No value or credential reaches either model.
    try:
        from agent_system import config

        return {"MIDDLEWARE_ENABLED": bool(config.MIDDLEWARE_ENABLED)}
    except Exception:
        return {}


def trace_target(
    signal_type: str,
    key: str,
    target: tuple[str, str],
    *,
    runtime_controls: Mapping[str, bool] | None = None,
) -> TargetAttribution:
    """Describe deterministic evidence behind one target assignment.

    ``direct-event`` means the named component emitted the source event. It still
    does not prove the prompt caused it. ``inferred`` means a maintained ownership
    map linked a tool or cross-cutting signal to the target.
    """

    controls = _runtime_controls(runtime_controls)

    if signal_type == "PROVIDER_ERROR":
        return TargetAttribution(
            relation="direct-event/non-prompt-cause",
            evidence_source="[MIDDLEWARE] review event",
            runtime_state=_middleware_state(controls),
            proposal_eligible=False,
            reason="Provider, timeout, and API failures require configuration or code diagnosis, not a prompt rewrite.",
        )

    if signal_type in _MIDDLEWARE_SIGNALS:
        state = _middleware_state(controls)
        enabled = controls.get("MIDDLEWARE_ENABLED")
        return TargetAttribution(
            relation="direct-event",
            evidence_source="[MIDDLEWARE] REVISED/FLAGGED_UNFIXABLE event",
            runtime_state=state,
            proposal_eligible=enabled is not False and target == _MIDDLEWARE_TARGET,
            reason=(
                "The log proves Middleware executed for the historical event, but the target is disabled in the current runtime."
                if enabled is False
                else "The log proves Middleware executed; Brain must still distinguish prompt wording from caller/code behavior."
            ),
        )

    if signal_type == "HEALING_TRIGGER":
        return TargetAttribution(
            relation="direct-event/non-allow-listed-target",
            evidence_source="[HEALING] DETECT_ERROR event",
            runtime_state="not-applicable",
            proposal_eligible=False,
            reason="A healing exception is code/environment evidence; its recovery prompt is not an allow-listed literal target.",
        )

    if signal_type == "TOOL_ERROR":
        return TargetAttribution(
            relation="inferred",
            evidence_source=f"[TOOL] error correlated with {key}",
            runtime_state="unknown-from-log",
            proposal_eligible=True,
            reason="The tool-to-prompt map suggests ownership, but Brain must classify prompt vs code/config/external cause.",
        )

    if signal_type == "SELF_CORRECTION:EXTERNAL_DEPENDENCY_MISSING":
        return TargetAttribution(
            relation="inferred/non-prompt-cause",
            evidence_source="[BRAIN] EVALUATE_RESULT event",
            runtime_state="not-applicable",
            proposal_eligible=False,
            reason="A missing dependency belongs to requirements or environment configuration, not a prompt literal.",
        )

    if signal_type.startswith("SELF_CORRECTION:"):
        return TargetAttribution(
            relation="inferred",
            evidence_source="[BRAIN] EVALUATE_RESULT after the last executed tool",
            runtime_state="active-core-path",
            proposal_eligible=True,
            reason="The ownership map is plausible but not causal proof; Brain must confirm that wording alone can fix it.",
        )

    return TargetAttribution(
        relation="inferred",
        evidence_source="tool-to-prompt ownership map",
        runtime_state="unknown-from-log",
        proposal_eligible=True,
        reason="No direct component event exists; Brain must reject the target if evidence is insufficient.",
    )


def _middleware_state(controls: Mapping[str, bool]) -> str:
    enabled = controls.get("MIDDLEWARE_ENABLED")
    if enabled is True:
        return "enabled (MIDDLEWARE_ENABLED=true)"
    if enabled is False:
        return "disabled (MIDDLEWARE_ENABLED=false)"
    return "unknown (MIDDLEWARE_ENABLED unavailable)"
