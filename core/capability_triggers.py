"""Proactive, secret-safe alerts for tool packs that failed during startup."""
from __future__ import annotations

from typing import Iterable

from .notifier import Notification, NOTIFY
from .triggers import Trigger


_FAILED_STATES = frozenset({"unavailable", "error"})


def _safe_statuses(statuses: Iterable[dict]) -> list[dict[str, str]]:
    failures = []
    for raw in statuses or ():
        if not isinstance(raw, dict):
            continue
        state = str(raw.get("state") or "").strip().lower()
        if state not in _FAILED_STATES:
            continue
        module = str(raw.get("module") or "unknown_pack").strip()[:80]
        reason = str(raw.get("reason") or "unavailable").strip()[:80]
        failures.append({"module": module, "state": state, "reason": reason})
    return failures


def _action_for(module: str, reason: str) -> str:
    if module == "gmail_ops" and reason == "oauth_or_credentials_error":
        return "Authorize Gmail again, then restart Ciel."
    return "Check this capability's configuration or dependency, then restart Ciel."


def make_capability_loss_check(status: dict[str, str]):
    """Return one stable startup finding without retaining provider exception text."""
    module = status["module"]
    state = status["state"]
    reason = status["reason"]
    label = module.removesuffix("_ops").replace("_", " ").title()

    def check(now: float):
        return Notification(
            key=f"capability_loss:{module}:{reason}",
            title=f"Capability unavailable: {label}",
            detail=f"Startup state: {state}; reason: {reason}.",
            action=_action_for(module, reason),
            urgency=NOTIFY,
            trigger="capability_loss",
            created_at=now,
        )

    return check


def build_capability_loss_triggers(*, enabled_names=(), statuses=()) -> list[Trigger]:
    """Build opt-in startup alerts for packs that unexpectedly failed to load."""
    if "capability_loss" not in set(enabled_names or ()):
        return []
    return [
        Trigger(
            name=f"capability_loss:{status['module']}",
            check=make_capability_loss_check(status),
            every_seconds=300.0,
            cooldown_seconds=24 * 3600.0,
        )
        for status in _safe_statuses(statuses)
    ]
