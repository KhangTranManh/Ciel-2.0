"""Create readable and machine-friendly views of ciel_data/logs/thoughts.log.

The raw thoughts.log stays untouched as the chronological audit trail. This
script generates:
  - thoughts_view.md: compact turn-by-turn debug timeline for humans
  - thoughts_view.jsonl: parsed entries for future tooling/RAG ingestion
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = ROOT / "ciel_data" / "logs" / "thoughts.log"
DEFAULT_MD = ROOT / "ciel_data" / "logs" / "thoughts_view.md"
DEFAULT_JSONL = ROOT / "ciel_data" / "logs" / "thoughts_view.jsonl"

ENTRY_RE = re.compile(
    r"^\[(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]\s+"
    r"\[(?P<actor>[^\]]+)\]\s+\[(?P<action>[^\]]+)\]\s*$"
)
SEPARATOR_RE = re.compile(r"^-{20,}\s*$")
CURRENT_REQUEST_RE = re.compile(
    r"\[CURRENT USER REQUEST\]:\s*(?P<request>.*?)(?:\n-{20,}|\Z)",
    re.DOTALL,
)
ROUTE_ACTION_RE = re.compile(r'"action"\s*:\s*"([^"]+)"')
ROUTE_TOOL_RE = re.compile(r'"tool_name"\s*:\s*"([^"]+)"')


@dataclass
class LogEntry:
    index: int
    timestamp: str
    actor: str
    action: str
    content: str


def parse_log(text: str) -> list[LogEntry]:
    entries: list[LogEntry] = []
    current_header: tuple[str, str, str] | None = None
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_header, current_lines
        if not current_header:
            current_lines = []
            return
        timestamp, actor, action = current_header
        content = "\n".join(current_lines).strip()
        entries.append(
            LogEntry(
                index=len(entries) + 1,
                timestamp=timestamp,
                actor=actor,
                action=action,
                content=content,
            )
        )
        current_header = None
        current_lines = []

    for line in text.splitlines():
        header = ENTRY_RE.match(line)
        if header:
            flush()
            current_header = (
                header.group("timestamp"),
                header.group("actor"),
                header.group("action"),
            )
            continue

        if SEPARATOR_RE.match(line):
            flush()
            continue

        if current_header:
            current_lines.append(line)

    flush()
    return entries


def group_turns(entries: list[LogEntry]) -> list[dict]:
    turns: list[dict] = []
    pending_prelude: list[LogEntry] = []
    current: dict | None = None

    for entry in entries:
        is_user_request = entry.actor == "USER" and entry.action == "REQUEST"
        if is_user_request:
            if current:
                turns.append(current)
            current = {
                "timestamp": entry.timestamp,
                "request": extract_request(entry.content),
                "entries": pending_prelude + [entry],
            }
            pending_prelude = []
            continue

        if current:
            current["entries"].append(entry)
        elif entry.actor == "RAG" and entry.action in {"RECALLED", "COMPRESSED", "COMPRESS_TASK"}:
            pending_prelude.append(entry)

    if current:
        turns.append(current)

    return turns


def extract_request(content: str) -> str:
    match = CURRENT_REQUEST_RE.search(content)
    if match:
        return compact(match.group("request"), 700)
    return compact(content, 700)


def compact(text: str, limit: int = 500) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def route_summary(content: str) -> str:
    action = ROUTE_ACTION_RE.search(content)
    tool = ROUTE_TOOL_RE.search(content)
    if action and tool:
        return f"{action.group(1)} -> {tool.group(1)}"
    if action:
        return action.group(1)
    return compact(content, 300)


def render_turn(turn: dict, include_full: bool = False) -> str:
    entries: list[LogEntry] = turn["entries"]
    lines = [
        f"## {turn['timestamp']} - User Request",
        "",
        f"> {turn['request']}",
        "",
    ]

    rag_reads = [
        e for e in entries
        if e.actor == "RAG" and e.action in {"RECALLED", "COMPRESS_TASK", "COMPRESSED", "COMPRESS_ERROR"}
    ]
    rag_writes = [
        e for e in entries
        if e.actor == "RAG" and e.action.startswith("ARCHIVED")
    ]
    routes = [e for e in entries if e.actor == "BRAIN" and e.action == "ROUTE_DECISION"]
    tools = [e for e in entries if e.actor == "TOOL"]
    healing = [e for e in entries if e.actor == "HEALING"]
    worker_responses = [
        e for e in entries
        if e.actor == "WORKER" and e.action in {
            "CHAT_RESPONSE",
            "FORMAT_RESPONSE",
            "MULTI_TOOL_FORMAT_RESPONSE",
            "ERROR_REPHRASE",
        }
    ]
    errors = [
        e for e in entries
        if "ERROR" in e.action or "[TOOL_ERROR]" in e.content or "Traceback" in e.content
    ]

    if rag_reads:
        lines.append("**RAG Recall / Compression**")
        for entry in rag_reads:
            lines.append(f"- `{entry.action}` {compact(entry.content, 350)}")
        lines.append("")

    if routes:
        lines.append("**Route**")
        for entry in routes:
            lines.append(f"- `{route_summary(entry.content)}`")
        lines.append("")

    if tools:
        lines.append("**Tools**")
        for entry in tools:
            lines.append(f"- `{entry.action}` {compact(entry.content, 350)}")
        lines.append("")

    if healing:
        lines.append("**Healing**")
        for entry in healing:
            lines.append(f"- `{entry.action}` {compact(entry.content, 350)}")
        lines.append("")

    if worker_responses:
        lines.append("**Final / Worker Output**")
        for entry in worker_responses[-2:]:
            lines.append(f"- {compact(entry.content, 500)}")
        lines.append("")

    if errors:
        lines.append("**Errors To Inspect**")
        for entry in errors:
            lines.append(f"- `{entry.actor}:{entry.action}` {compact(entry.content, 500)}")
        lines.append("")

    if rag_writes:
        lines.append("**Memory Writes**")
        for entry in rag_writes:
            lines.append(f"- `{entry.action}` {compact(entry.content, 300)}")
        lines.append("")

    if include_full:
        lines.append("<details>")
        lines.append("<summary>Full entries</summary>")
        lines.append("")
        for entry in entries:
            lines.append(f"### {entry.timestamp} [{entry.actor}] [{entry.action}]")
            lines.append("")
            lines.append("```text")
            lines.append(entry.content)
            lines.append("```")
            lines.append("")
        lines.append("</details>")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_jsonl(entries: list[LogEntry], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")


def write_markdown(turns: list[dict], output_path: Path, limit: int, include_full: bool) -> None:
    selected = turns[-limit:] if limit > 0 else turns
    selected = list(reversed(selected))

    total_entries = sum(len(turn["entries"]) for turn in turns)
    lines = [
        "# Ciel Thoughts Log View",
        "",
        "Generated from `ciel_data/logs/thoughts.log`.",
        "The raw log is not modified.",
        "",
        f"- Total turns: {len(turns)}",
        f"- Total parsed entries: {total_entries}",
        f"- Showing newest turns: {len(selected)}",
        "",
    ]

    for turn in selected:
        lines.append(render_turn(turn, include_full=include_full))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Format Ciel thoughts.log for easier debugging.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--out-md", type=Path, default=DEFAULT_MD)
    parser.add_argument("--out-jsonl", type=Path, default=DEFAULT_JSONL)
    parser.add_argument("--limit", type=int, default=40, help="Newest turns to include in Markdown. Use 0 for all.")
    parser.add_argument("--include-full", action="store_true", help="Include full entry bodies in Markdown details.")
    args = parser.parse_args()

    if not args.input.exists():
        print(f"Input log not found: {args.input}")
        return 1

    text = args.input.read_text(encoding="utf-8", errors="replace")
    entries = parse_log(text)
    turns = group_turns(entries)

    write_jsonl(entries, args.out_jsonl)
    write_markdown(turns, args.out_md, args.limit, args.include_full)

    print(f"Parsed entries: {len(entries)}")
    print(f"Parsed turns: {len(turns)}")
    print(f"Markdown view: {args.out_md}")
    print(f"JSONL view: {args.out_jsonl}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
