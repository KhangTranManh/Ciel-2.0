#!/usr/bin/env python3
"""Read-only structural validator for repository context used by coding agents."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import deque
from pathlib import Path


ENTRY_CANDIDATES = (
    Path("AGENTS.md"),
    Path("instructionAI/SKILL.md"),
    Path("docs/agent-context/index.md"),
    Path("docs/agent-context/SKILL.md"),
    Path("CLAUDE.md"),
    Path(".github/copilot-instructions.md"),
)
FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL)
FIELD_RE = re.compile(r"^(name|description)\s*:\s*(.+?)\s*$", re.MULTILINE)
LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
SECRET_PATTERNS = {
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "credential assignment": re.compile(
        r"(?i)\b(?:api[_-]?key|token|password|client[_-]?secret)\b\s*[:=]\s*[\"']?[A-Za-z0-9_./+\-=]{16,}"
    ),
    "authorization header": re.compile(
        r"(?i)authorization\s*:\s*bearer\s+[A-Za-z0-9._~+/=-]{16,}"
    ),
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate repository context used by AI coding agents."
    )
    parser.add_argument("root", type=Path, help="Repository root to inspect")
    parser.add_argument(
        "--entry",
        type=Path,
        help="Canonical entry path relative to root. Common paths are auto-detected.",
    )
    parser.add_argument(
        "--memory-dir",
        type=Path,
        help="Directory of context files relative to root when the entry is elsewhere.",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    return parser.parse_args()


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _resolve_under(root: Path, relative: Path, label: str) -> tuple[Path | None, str | None]:
    resolved = (root / relative).resolve()
    if not _inside(resolved, root):
        return None, f"{label} path escapes the repository root."
    return resolved, None


def _detect_entry(root: Path) -> tuple[Path | None, list[str]]:
    matches = [candidate for candidate in ENTRY_CANDIDATES if (root / candidate).is_file()]
    if not matches:
        return None, []
    warnings: list[str] = []
    if len(matches) > 1:
        try:
            selected_text = (root / matches[0]).read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            selected_text = ""
        unlinked = [
            path
            for path in matches[1:]
            if path.as_posix() not in selected_text and path.name not in selected_text
        ]
        if unlinked:
            names = ", ".join(path.as_posix() for path in matches)
            warnings.append(
                f"Detected multiple entry candidates ({names}); selected {matches[0].as_posix()}. "
                "Use --entry to choose another."
            )
    return (root / matches[0]).resolve(), warnings


def _local_target(source: Path, target: str, root: Path) -> Path | None:
    clean = target.strip().split("#", 1)[0]
    if not clean or clean.startswith(("http://", "https://", "mailto:")):
        return None
    return (source.parent / clean).resolve()


def _linked_markdown(entry: Path, root: Path) -> set[Path]:
    found: set[Path] = {entry}
    queue: deque[Path] = deque([entry])
    while queue and len(found) < 200:
        source = queue.popleft()
        try:
            text = source.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        for target in LINK_RE.findall(text):
            resolved = _local_target(source, target, root)
            if (
                resolved is not None
                and _inside(resolved, root)
                and resolved.suffix.lower() == ".md"
                and resolved.is_file()
                and resolved not in found
            ):
                found.add(resolved)
                queue.append(resolved)
    return found


def _result(entry: str, checked: list[str], errors: list[str], warnings: list[str]) -> dict[str, object]:
    return {
        "ok": not errors,
        "entry": entry,
        "checked_files": checked,
        "errors": errors,
        "warnings": warnings,
    }


def validate(
    root_arg: Path,
    entry_arg: Path | None = None,
    memory_dir_arg: Path | None = None,
) -> dict[str, object]:
    root = root_arg.expanduser().resolve()
    errors: list[str] = []
    warnings: list[str] = []
    checked: list[str] = []

    if not root.is_dir():
        return _result(str(entry_arg or "auto"), checked, ["Repository root is not a directory."], warnings)

    if entry_arg is None:
        entry, detection_warnings = _detect_entry(root)
        warnings.extend(detection_warnings)
        if entry is None:
            candidates = ", ".join(path.as_posix() for path in ENTRY_CANDIDATES)
            return _result(
                "auto",
                checked,
                [f"No canonical entry was detected. Checked: {candidates}. Use --entry for another path."],
                warnings,
            )
    else:
        entry, error = _resolve_under(root, entry_arg, "Entry")
        if error:
            return _result(entry_arg.as_posix(), checked, [error], warnings)

    assert entry is not None
    entry_label = entry.relative_to(root).as_posix()
    if not entry.is_file():
        return _result(entry_label, checked, [f"Canonical entry does not exist: {entry_label}"], warnings)

    memory_root: Path | None = None
    if memory_dir_arg is not None:
        memory_root, error = _resolve_under(root, memory_dir_arg, "Memory directory")
        if error:
            return _result(entry_label, checked, [error], warnings)
        if not memory_root.is_dir():
            return _result(
                entry_label,
                checked,
                [f"Memory directory does not exist: {memory_dir_arg.as_posix()}"],
                warnings,
            )

    if memory_root is not None:
        markdown_files = set(memory_root.rglob("*.md")) | {entry}
    elif entry.parent != root and entry.parent.name not in {".github", ".claude"}:
        memory_root = entry.parent.resolve()
        markdown_files = set(memory_root.rglob("*.md"))
    else:
        markdown_files = _linked_markdown(entry, root)

    if not markdown_files:
        errors.append("No Markdown context files were found.")

    texts: dict[Path, str] = {}
    for path in sorted(markdown_files):
        if not _inside(path.resolve(), root):
            errors.append(f"Context file escapes the repository root: {path}")
            continue
        if path.is_symlink():
            warnings.append(f"Skipped symbolic link: {path.relative_to(root).as_posix()}")
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            errors.append(
                f"Cannot read {path.relative_to(root).as_posix()} as UTF-8: {type(exc).__name__}"
            )
            continue
        texts[path] = text
        checked.append(path.relative_to(root).as_posix())
        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(text):
                errors.append(
                    f"Possible {label} in {path.relative_to(root).as_posix()}; inspect without copying the value."
                )

    entry_text = texts.get(entry, "")
    if not entry_text.strip():
        errors.append("Canonical entry is empty.")
    frontmatter = FRONTMATTER_RE.search(entry_text)
    if entry.name == "SKILL.md":
        if not frontmatter:
            errors.append("A SKILL.md entry needs YAML frontmatter delimited by ---." )
        else:
            fields = {
                key: value.strip().strip("\"'")
                for key, value in FIELD_RE.findall(frontmatter.group(1))
            }
            for required in ("name", "description"):
                if not fields.get(required):
                    errors.append(
                        f"The SKILL.md frontmatter needs a non-empty {required} field."
                    )

    for path, text in texts.items():
        for target in LINK_RE.findall(text):
            resolved = _local_target(path, target, root)
            if resolved is None:
                continue
            if not _inside(resolved, root):
                errors.append(
                    f"Local link escapes the repository root in {path.relative_to(root).as_posix()}: {target}"
                )
            elif not resolved.exists():
                errors.append(
                    f"Broken local link in {path.relative_to(root).as_posix()}: {target}"
                )

    if memory_root is not None:
        context_index = "\n".join(texts.values())
        for path in sorted(memory_root.rglob("*.md")):
            if path == entry or path not in texts:
                continue
            relative_name = path.relative_to(memory_root).as_posix()
            if relative_name not in context_index and path.name not in context_index:
                warnings.append(f"Context file is not named by another context file: {relative_name}")

    for marker in ("TODO", "TBD", "FIXME"):
        if re.search(rf"\b{marker}\b", entry_text):
            warnings.append(f"Canonical entry contains a volatile marker: {marker}")

    return _result(entry_label, checked, errors, warnings)


def main() -> int:
    args = _parse_args()
    result = validate(args.root, args.entry, args.memory_dir)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        status = "PASS" if result["ok"] else "FAIL"
        print(f"Repository context validation: {status}")
        print(f"Entry: {result['entry']}")
        print(f"Checked files: {len(result['checked_files'])}")
        for error in result["errors"]:
            print(f"ERROR: {error}")
        for warning in result["warnings"]:
            print(f"WARNING: {warning}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
