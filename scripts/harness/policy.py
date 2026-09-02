"""Deny-by-default policy for the prompt harness.

The JSON policy is intentionally data-only.  It can widen the harness only to an
exact Python module and an exact module-level string variable.  Runtime validation
still rejects private paths, symlinks escaping the repository, and non-string values.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


class PolicyError(ValueError):
    """The requested harness operation violates policy."""


# These names are code-level invariants. A custom JSON policy cannot turn them off.
_IMMUTABLE_BLOCKED_PARTS = {
    ".git", "ciel_data", "credentials.json", "gmail_token.json", "secrets", "runtime",
}
_PRIVATE_NAME_FRAGMENTS = ("credential", "secret", "token", "private_key")
SAFE_TEST_COMMAND = ("{python}", "-m", "backtest.run_all", "--unit-only")


@dataclass(frozen=True)
class PromptTarget:
    file: str
    symbol: str


@dataclass(frozen=True)
class HarnessPolicy:
    targets: tuple[PromptTarget, ...]
    blocked_parts: tuple[str, ...]
    max_value_chars: int
    max_growth_chars: int
    test_command: tuple[str, ...]

    def target(self, file: str, symbol: str) -> PromptTarget:
        normalized = _normalize_relative(file)
        for item in self.targets:
            if item.file == normalized and item.symbol == symbol:
                return item
        raise PolicyError(f"Target is not allow-listed: {normalized}::{symbol}")

    def resolve_target(self, root: Path, target: PromptTarget) -> Path:
        relative = _normalize_relative(target.file)
        lowered_parts = {part.lower() for part in PurePosixPath(relative).parts}
        filename = PurePosixPath(relative).name.lower()
        blocked = set(self.blocked_parts).union(_IMMUTABLE_BLOCKED_PARTS)
        if (
            lowered_parts.intersection(blocked)
            or filename == ".env"
            or filename.startswith(".env.")
            or any(fragment in filename for fragment in _PRIVATE_NAME_FRAGMENTS)
        ):
            raise PolicyError(f"Private path is blocked: {relative}")
        path = (root / relative).resolve()
        resolved_root = root.resolve()
        try:
            path.relative_to(resolved_root)
        except ValueError as exc:
            raise PolicyError("Target escapes the repository") from exc
        if path.suffix.lower() != ".py":
            raise PolicyError("Only Python prompt modules are writable")
        return path


def _normalize_relative(value: str) -> str:
    normalized = value.replace("\\", "/").strip()
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or ".." in path.parts:
        raise PolicyError(f"Path must be repository-relative: {value!r}")
    return path.as_posix()


def load_policy(path: Path) -> HarnessPolicy:
    raw = json.loads(path.read_text(encoding="utf-8"))
    allowed_keys = {
        "allowed_targets", "blocked_path_parts", "max_value_chars",
        "max_growth_chars", "test_command",
    }
    unknown = set(raw) - allowed_keys
    if unknown:
        raise PolicyError(f"Unknown policy keys: {sorted(unknown)}")

    targets = []
    for item in raw.get("allowed_targets", []):
        if set(item) != {"file", "symbol"}:
            raise PolicyError("Each target must contain only file and symbol")
        file = _normalize_relative(str(item["file"]))
        symbol = str(item["symbol"])
        if not symbol.isidentifier() or not symbol.isupper():
            raise PolicyError(f"Unsafe prompt symbol: {symbol!r}")
        targets.append(PromptTarget(file=file, symbol=symbol))
    if not targets:
        raise PolicyError("Policy has no allowed targets")

    blocked = tuple(str(v).lower() for v in raw.get("blocked_path_parts", []))
    command = tuple(str(v) for v in raw.get("test_command", []))
    if command != SAFE_TEST_COMMAND:
        raise PolicyError("test_command is fixed to the maintained unit runner")
    return HarnessPolicy(
        targets=tuple(targets),
        blocked_parts=blocked,
        max_value_chars=int(raw.get("max_value_chars", 30000)),
        max_growth_chars=int(raw.get("max_growth_chars", 3000)),
        test_command=command,
    )
