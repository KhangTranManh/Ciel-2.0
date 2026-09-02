"""Generate one bounded project-wide harness report without live tool mutations."""
from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from .policy import HarnessPolicy
from .prompt_value import read_prompt_value


@dataclass(frozen=True)
class ToolRegistration:
    name: str
    module: str


@dataclass(frozen=True)
class ToolInventory:
    tools: tuple[ToolRegistration, ...]
    dynamic_registrations: int
    parse_errors: tuple[str, ...]


@dataclass(frozen=True)
class UnitResult:
    status: str
    summary: str
    duration_seconds: float


def discover_tool_inventory(root: Path) -> ToolInventory:
    """Read tool-pack ASTs without importing providers or touching credentials."""

    registrations: set[tuple[str, str]] = set()
    dynamic = 0
    errors: list[str] = []
    skills_root = root / "skills"
    for path in sorted(skills_root.rglob("*_ops.py")):
        relative = path.relative_to(root).as_posix()
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source)
        except (OSError, SyntaxError) as exc:
            errors.append(f"{relative}: {type(exc).__name__}")
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or func.attr != "from_function":
                continue

            tool_name = None
            function_name = None
            has_name_keyword = False
            for keyword in node.keywords:
                if keyword.arg == "name":
                    has_name_keyword = True
                    if isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
                        tool_name = keyword.value.value
                if keyword.arg == "func" and isinstance(keyword.value, ast.Name):
                    function_name = keyword.value.id
            if function_name is None and node.args and isinstance(node.args[0], ast.Name):
                function_name = node.args[0].id

            resolved = tool_name if has_name_keyword else function_name
            if resolved:
                registrations.add((resolved, relative))
            else:
                dynamic += 1

    tools = tuple(ToolRegistration(name=name, module=module) for name, module in sorted(registrations))
    return ToolInventory(tools=tools, dynamic_registrations=dynamic, parse_errors=tuple(errors))


def run_unit_suite(root: Path, timeout_seconds: int = 300) -> UnitResult:
    started = datetime.now()
    child_env = os.environ.copy()
    child_env["PYTHONUTF8"] = "1"
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "backtest.run_all", "--unit-only"],
            cwd=str(root),
            env=child_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return UnitResult("TIMEOUT", f"Timed out after {timeout_seconds}s", (datetime.now() - started).total_seconds())
    except OSError as exc:
        return UnitResult("ERROR", f"{type(exc).__name__}: {exc}", (datetime.now() - started).total_seconds())

    output = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    match = re.search(r"TOTAL:\s*(\d+)\s+PASS\s*/\s*(\d+)\s+FAIL\s+out of\s+(\d+)\s+suites", output)
    if match:
        summary = f"{match.group(1)} PASS / {match.group(2)} FAIL out of {match.group(3)} suites"
    else:
        summary = f"exit code {proc.returncode}; unified summary was not found"
    status = "PASS" if proc.returncode == 0 else "FAIL"
    return UnitResult(status, summary, (datetime.now() - started).total_seconds())


def write_project_report(
    root: Path,
    findings: list[Any],
    total_entries: int,
    policy: HarnessPolicy,
    tool_to_prompt: Mapping[str, tuple[str, str]],
    output_dir: Path,
    *,
    run_units: bool = True,
) -> Path:
    """Write a single report covering logs, targets, static tools, and unit regression."""

    inventory = discover_tool_inventory(root)
    unit = run_unit_suite(root) if run_units else UnitResult("SKIPPED", "Skipped by operator", 0.0)

    valid_targets: list[str] = []
    invalid_targets: list[str] = []
    for target in policy.targets:
        label = f"{target.file}::{target.symbol}"
        try:
            path = policy.resolve_target(root, target)
            read_prompt_value(path, target.symbol)
            valid_targets.append(label)
        except Exception as exc:  # report validation failure without leaking source values
            invalid_targets.append(f"{label} ({type(exc).__name__})")

    blocked = sum(1 for finding in findings if not finding.attribution.proposal_eligible)
    eligible = len(findings) - blocked
    mapped = [item for item in inventory.tools if item.name in tool_to_prompt]
    unmapped = [item for item in inventory.tools if item.name not in tool_to_prompt]
    generated = datetime.now()

    lines = [
        "# Ciel Harness — Full Project Report",
        "",
        f"Generated: `{generated.strftime('%Y-%m-%d %H:%M:%S')}`",
        "",
        "## Scope and safety",
        "",
        "This report covers the complete available `thoughts.log` history, every recurring signature at count >= 1,",
        "all allow-listed prompt literals, a static AST inventory of tool registrations, and the maintained unit suite.",
        "It does **not** execute live external or mutating tools: no email is sent, no file is deleted, no Git push occurs,",
        "and no deployment action runs. Static inventory is coverage evidence, not proof that every provider works live.",
        "",
        "## Executive summary",
        "",
        f"- Parsed log entries: **{total_entries}**",
        f"- Findings: **{len(findings)}** total; **{eligible}** eligible for Brain review; **{blocked}** blocked before model",
        f"- Prompt targets: **{len(valid_targets)}** valid; **{len(invalid_targets)}** invalid",
        f"- Static tool registrations: **{len(inventory.tools)}** resolved; **{inventory.dynamic_registrations}** dynamic/unresolved",
        f"- Tools with a prompt-owner mapping: **{len(mapped)}**; without a dedicated mapping: **{len(unmapped)}**",
        f"- Maintained unit regression: **{unit.status}** — {unit.summary} ({unit.duration_seconds:.1f}s)",
        "",
        "## Logged findings",
        "",
        "| Signature | Count | Target | Attribution | Runtime | Proposal |",
        "|---|---:|---|---|---|---|",
    ]

    if findings:
        for finding in findings:
            proposal = "Brain review" if finding.attribution.proposal_eligible else "Blocked"
            lines.append(
                "| "
                + " | ".join([
                    _cell(finding.signature),
                    str(finding.count),
                    _cell(f"{finding.prompt_file}::{finding.prompt_target}"),
                    _cell(finding.attribution.relation),
                    _cell(finding.attribution.runtime_state),
                    proposal,
                ])
                + " |"
            )
    else:
        lines.append("| No mined failure signature | 0 | — | — | — | — |")

    lines.extend([
        "",
        "## Prompt target integrity",
        "",
    ])
    for label in valid_targets:
        lines.append(f"- PASS `{label}`")
    for label in invalid_targets:
        lines.append(f"- FAIL `{label}`")

    lines.extend([
        "",
        "## Static tool inventory",
        "",
        "| Tool | Pack | Prompt-owner mapping |",
        "|---|---|---|",
    ])
    for item in inventory.tools:
        target = tool_to_prompt.get(item.name)
        owner = f"{target[0]}::{target[1]}" if target else "No dedicated mapping"
        lines.append(f"| `{_cell(item.name)}` | `{_cell(item.module)}` | `{_cell(owner)}` |")
    if inventory.dynamic_registrations:
        lines.append(f"\nDynamic `StructuredTool.from_function(...)` registrations unresolved statically: **{inventory.dynamic_registrations}**.")
    if inventory.parse_errors:
        lines.append("\nAST parse errors:")
        for error in inventory.parse_errors:
            lines.append(f"- `{_cell(error)}`")

    lines.extend([
        "",
        "## Live coverage deliberately excluded",
        "",
        "The following need separate, attended and capability-specific smoke tests:",
        "",
        "- Gmail search/draft/send/reply/trash and Message ID verification",
        "- Telegram text/document delivery and deduplication",
        "- Web search/scrape provider quality and current-data completeness",
        "- Git commit/push confirmation against an operator-selected repository",
        "- Shell, deletion, desktop vision, and other high-risk operations",
        "- Live Brain/Worker integration, long conversation, and RAG stress suites",
        "",
        "These are not safe to combine into one unattended 'run everything' command because their permissions, test data,",
        "external recipients, cost, and cleanup requirements differ.",
        "",
        "## Recommended review order",
        "",
        "1. Resolve invalid prompt targets or unit failures first.",
        "2. Review blocked findings as code/config/provider work; do not force prompt candidates.",
        "3. Run Brain diagnosis only for eligible findings, one exact signature at a time.",
        "4. Review one generated candidate and run its focused smoke before apply/commit.",
        "5. Run live tools separately with explicit recipients, isolated fixtures, and confirmation enabled.",
        "",
    ])

    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"ciel_harness_project_report_{generated.strftime('%Y%m%d_%H%M%S')}.md"
    output.write_text("\n".join(lines), encoding="utf-8")
    return output


def _cell(value: str) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").strip()
