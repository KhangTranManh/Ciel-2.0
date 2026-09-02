"""Conservative compatibility checks for model-proposed prompt values."""
from __future__ import annotations

import re


class PromptContractError(ValueError):
    """A proposal attempts to change behavior owned by runtime code."""


_JSON_KEY_RE = re.compile(r'["\'](?P<key>[A-Za-z_][A-Za-z0-9_]*)["\']\s*:')
_RUNTIME_AUTHORITY_PATTERNS = (
    re.compile(r"\bcaller\s+must\b", re.IGNORECASE),
    re.compile(r"\btrigger\s+(?:a\s+)?regeneration\b", re.IGNORECASE),
    re.compile(r"\bhard\s+stop\b", re.IGNORECASE),
    re.compile(r"\bmust\s+never\s+(?:forward|send|execute)\b", re.IGNORECASE),
)


def validate_prompt_contract(current_value: str, new_value: str) -> None:
    """Reject schema or caller obligations that a value-only edit cannot provide.

    The harness cannot prove arbitrary callers implement a newly invented response
    field. A reviewed code change may extend a contract; an automated prompt-only
    candidate may not.
    """

    old_keys = {match.group("key") for match in _JSON_KEY_RE.finditer(current_value)}
    new_keys = {match.group("key") for match in _JSON_KEY_RE.finditer(new_value)}
    introduced = sorted(new_keys - old_keys)
    if introduced:
        raise PromptContractError(
            "Prompt-only candidate introduces unsupported JSON key(s): "
            + ", ".join(introduced)
            + ". Change and test the caller contract first."
        )

    for pattern in _RUNTIME_AUTHORITY_PATTERNS:
        if pattern.search(new_value) and not pattern.search(current_value):
            raise PromptContractError(
                "Prompt-only candidate assigns new blocking/regeneration behavior to runtime code. "
                "Use a reviewed code change with focused tests instead."
            )
