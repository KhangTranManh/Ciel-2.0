"""Read and replace exactly one module-level string value using AST boundaries."""
from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from .contract import PromptContractError, validate_prompt_contract
from .policy import HarnessPolicy, PromptTarget, SAFE_TEST_COMMAND
from .sanitizer import assert_no_secret_material


class CandidateError(ValueError):
    """A prompt candidate is invalid or stale."""


@dataclass(frozen=True)
class PromptCandidate:
    schema_version: int
    file: str
    symbol: str
    source_sha256: str
    old_value_sha256: str
    new_value: str
    diagnosis: str

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "PromptCandidate":
        raw = json.loads(path.read_text(encoding="utf-8"))
        expected = {field for field in cls.__dataclass_fields__}
        if set(raw) != expected:
            raise CandidateError("Candidate schema has missing or unknown fields")
        return cls(**raw)


@dataclass(frozen=True)
class ApplyResult:
    changed: bool
    tests_passed: bool
    test_output: str


def _sha(value: str | bytes) -> str:
    data = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(data).hexdigest()


def _find_value_node(source: str, symbol: str) -> ast.Constant:
    tree = ast.parse(source[1:] if source.startswith("\ufeff") else source)
    found: list[ast.Constant] = []
    for node in tree.body:
        names: list[str] = []
        value = None
        if isinstance(node, ast.Assign):
            names = [target.id for target in node.targets if isinstance(target, ast.Name)]
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names = [node.target.id]
            value = node.value
        if symbol in names:
            if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
                raise CandidateError(f"{symbol} must be a literal module-level string")
            found.append(value)
    if len(found) != 1:
        raise CandidateError(f"Expected exactly one assignment for {symbol}; found {len(found)}")
    return found[0]


def read_prompt_value(path: Path, symbol: str) -> tuple[str, str]:
    # Decode bytes directly so Windows CRLF is not normalized behind the hash or
    # rollback logic. A failed apply can therefore restore the exact original bytes.
    source = path.read_bytes().decode("utf-8")
    node = _find_value_node(source, symbol)
    return source, str(node.value)


def _line_byte_column_to_char(line: str, byte_column: int) -> int:
    return len(line.encode("utf-8")[:byte_column].decode("utf-8"))


def _offset(source: str, line_number: int, byte_column: int) -> int:
    lines = source.splitlines(keepends=True)
    line = lines[line_number - 1]
    prefix = sum(len(item) for item in lines[:line_number - 1])
    if line_number == 1 and source.startswith("\ufeff"):
        # AST positions came from the BOM-stripped parsing view.
        prefix += 1
        line = line[1:]
    return prefix + _line_byte_column_to_char(line, byte_column)


def replace_prompt_value(source: str, symbol: str, new_value: str) -> str:
    node = _find_value_node(source, symbol)
    start = _offset(source, node.lineno, node.col_offset)
    end = _offset(source, node.end_lineno, node.end_col_offset)
    # A triple-quoted literal keeps prompts reviewable while remaining data-only.
    # Backslashes and the delimiter are escaped so the runtime value stays exact.
    body = new_value.replace("\\", "\\\\").replace('"""', '\\"""')
    body = body.replace("\r", "\\r").replace("\t", "\\t")
    body = "".join(char if char == "\n" or ord(char) >= 32 else f"\\x{ord(char):02x}" for char in body)
    if "\r\n" in source:
        body = body.replace("\n", "\r\n")
    literal = f'"""{body}"""'
    updated = source[:start] + literal + source[end:]
    ast.parse(updated[1:] if updated.startswith("\ufeff") else updated)
    _, verified = _read_prompt_from_source(updated, symbol)
    if verified != new_value:
        raise CandidateError("Replacement did not preserve the proposed runtime value")
    return updated


def _read_prompt_from_source(source: str, symbol: str) -> tuple[ast.Constant, str]:
    node = _find_value_node(source, symbol)
    return node, str(node.value)


def build_candidate(
    root: Path,
    policy: HarnessPolicy,
    target: PromptTarget,
    new_value: str,
    diagnosis: str,
) -> PromptCandidate:
    path = policy.resolve_target(root, policy.target(target.file, target.symbol))
    source, old_value = read_prompt_value(path, target.symbol)
    if not new_value.strip() or new_value == old_value:
        raise CandidateError("Candidate must be a non-empty change")
    if len(new_value) > policy.max_value_chars:
        raise CandidateError("Candidate exceeds max_value_chars")
    if len(new_value) - len(old_value) > policy.max_growth_chars:
        raise CandidateError("Candidate grows the prompt beyond policy")
    try:
        assert_no_secret_material(new_value)
    except ValueError as exc:
        raise CandidateError(str(exc)) from exc
    try:
        validate_prompt_contract(old_value, new_value)
    except PromptContractError as exc:
        raise CandidateError(str(exc)) from exc
    replace_prompt_value(source, target.symbol, new_value)
    return PromptCandidate(
        schema_version=1,
        file=target.file,
        symbol=target.symbol,
        source_sha256=_sha(source),
        old_value_sha256=_sha(old_value),
        new_value=new_value,
        diagnosis=diagnosis[:2000],
    )


def apply_candidate(root: Path, policy: HarnessPolicy, candidate: PromptCandidate) -> ApplyResult:
    if candidate.schema_version != 1:
        raise CandidateError("Unsupported candidate schema")
    if tuple(policy.test_command) != SAFE_TEST_COMMAND:
        raise CandidateError("Only the maintained unit runner may validate an apply")
    target = policy.target(candidate.file, candidate.symbol)
    path = policy.resolve_target(root, target)
    source, old_value = read_prompt_value(path, target.symbol)
    if _sha(source) != candidate.source_sha256 or _sha(old_value) != candidate.old_value_sha256:
        raise CandidateError("Candidate is stale; source or prompt value changed")
    # Rebuild through the same policy boundary immediately before the write.
    build_candidate(root, policy, target, candidate.new_value, candidate.diagnosis)
    updated = replace_prompt_value(source, target.symbol, candidate.new_value)
    path.write_bytes(updated.encode("utf-8"))

    command = [sys.executable if item == "{python}" else item for item in policy.test_command]
    try:
        proc = subprocess.run(
            command,
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        path.write_bytes(source.encode("utf-8"))
        return ApplyResult(changed=False, tests_passed=False, test_output=f"Unit runner failed: {exc}")
    output = ((proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else ""))[-12000:]
    if proc.returncode != 0:
        path.write_bytes(source.encode("utf-8"))
        return ApplyResult(changed=False, tests_passed=False, test_output=output)
    return ApplyResult(changed=True, tests_passed=True, test_output=output)
