"""Create readable and machine-friendly views of ciel_data/logs/thoughts.log.

The raw thoughts.log stays untouched as the chronological audit trail. This
script generates:
  - thoughts_view.md: compact turn-by-turn debug timeline for humans
  - thoughts_view.jsonl: parsed entries for future tooling/RAG ingestion
  - router_finetune.jsonl: Full-pipeline ChatML dataset for Qwen2.5-7B LoRA
    fine-tuning. Each sample captures the complete reasoning chain:
    USER → BRAIN (why) → TOOL (what happened) → WORKER (final answer)
"""

from __future__ import annotations

import argparse
import json
import random
import re
from dataclasses import dataclass, asdict
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = ROOT / "ciel_data" / "logs" / "thoughts.log"
DEFAULT_MD = ROOT / "ciel_data" / "logs" / "thoughts_view.md"
DEFAULT_JSONL = ROOT / "ciel_data" / "logs" / "thoughts_view.jsonl"
DEFAULT_TRAIN = ROOT / "ciel_data" / "router_finetune.jsonl"

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

# ── Training Dataset Constants ──────────────────────────────────────────────

FAKE_PATHS = [
    r"C:\Projects\AlphaWebApp",
    r"E:\Dev\ReactFrontEnd",
    r"D:\DataAnalysis\PythonServer",
    r"C:\workspace\django_backend",
    r"D:\TradingDesk\mql5_scripts",
    r"C:\Users\Master\Documents\automation_tool",
    r"E:\Workspace\ciel_helper",
    r"D:\GitRepositories\finance_dashboard",
]

TARGET_PATH_PATTERNS = [
    r"D:\\Program Files\\Ciel 2\.0\\Ciel 2\.0",
    r"D:\\Ciel-2\.0",
    r"D:/Ciel-2\.0",
    r"D:\\\\Ciel-2\.0",
]

# Allowed tool names — samples using tools outside this set are dropped
ALLOWED_TOOLS = {
    "save_fact", "get_fact", "delete_fact",
    "list_workspace", "read_file", "write_file", "append_file",
    "delete_file", "get_file_info", "run_python_script",
    "execute_shell_command", "take_screenshot", "open_application",
    "create_gmail_draft", "send_gmail_message", "search_gmail",
    "get_gmail_message", "get_gmail_thread",
    "trash_email", "mark_email_read", "reply_to_email",
    "get_market_price", "get_crypto_stats", "analyze_crypto_technical",
    "git_list_repos", "git_status", "git_diff",
    "git_commit_and_push", "git_confirm_push",
    "vision_act", "vision_describe",
}

CIEL_SYSTEM_PROMPT = (
    "You are Ciel, an autonomous AI assistant operating on a Windows desktop. "
    "You have two modes of thinking:\n\n"
    "1. REASONING: When given a user request, first analyze the intent, assess risk, "
    "and decide the correct action (chat / tool / code / multi_tool). Output your "
    "reasoning as a JSON with 'hidden_thought', 'action', and tool details if needed.\n\n"
    "2. EXECUTING: After the action is decided, generate the appropriate response — "
    "either a direct chat answer, formatted tool output, or generated code.\n\n"
    "AVAILABLE TOOLS:\n"
    "- save_fact / get_fact / delete_fact: Local fact vault\n"
    "- list_workspace / read_file / write_file / append_file / delete_file / get_file_info: File ops\n"
    "- run_python_script: Execute Python in sandbox\n"
    "- execute_shell_command: Run OS commands\n"
    "- open_application: Launch programs\n"
    "- search_gmail / create_gmail_draft / send_gmail_message / reply_to_email / trash_email: Email\n"
    "- get_market_price / get_crypto_stats / analyze_crypto_technical: Trading data\n"
    "- git_status / git_diff / git_commit_and_push / git_confirm_push: Git ops\n\n"
    "RULES:\n"
    "- Always reason before acting. Output 'hidden_thought' with observation, reasoning, risk.\n"
    "- Use ONLY tools from the list above. Never invent tool names.\n"
    "- Be extremely concise in final responses. No filler."
)


@dataclass
class LogEntry:
    index: int
    timestamp: str
    actor: str
    action: str
    content: str


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 1: Log Parsing (shared by all output formats)
# ══════════════════════════════════════════════════════════════════════════════

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


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 2: Debug Markdown + JSONL output (unchanged from original)
# ══════════════════════════════════════════════════════════════════════════════

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


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 3: Full-Pipeline Training Dataset (ChatML for Qwen2.5-7B)
#
#  Each sample is a MULTI-TURN conversation that teaches the model
#  the complete reasoning chain:
#
#    [system] Ciel persona + tool list
#    [user]   Raw user request
#    [assistant] Brain reasoning (hidden_thought + action decision)
#    [user]   Tool result (if tool action)  — or skip this turn for chat
#    [assistant] Worker's final response to the user
#
#  This teaches the model WHY (reasoning) not just WHAT (output).
# ══════════════════════════════════════════════════════════════════════════════

def _clean_user_request(text: str) -> str:
    """Extract the clean core prompt, stripping any RAG recalled context."""
    if "[CURRENT USER REQUEST]:" in text:
        text = text.split("[CURRENT USER REQUEST]:")[-1]
    return text.strip()


def _clean_route_json(text: str) -> str:
    """Strip markdown code fences from route decision JSON."""
    cleaned = re.sub(r"```json\s*", "", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"```\s*$", "", cleaned)
    return cleaned.strip()


def _randomize_paths_str(text: str) -> str:
    """Replace hardcoded project paths with a random fake path."""
    random_path = random.choice(FAKE_PATHS)
    for pattern in TARGET_PATH_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            swapped = random_path
            if "/" in pattern:
                swapped = random_path.replace("\\", "/")
            elif "\\\\\\\\" in pattern:
                swapped = random_path.replace("\\", "\\\\")
            repl = lambda _m: swapped
            text = re.sub(pattern, repl, text, flags=re.IGNORECASE)
    return text


def _extract_tools_from_decision(decision_obj: dict) -> list[str]:
    """Get all tool_name values from a route decision."""
    names = []
    if "tool_name" in decision_obj:
        names.append(decision_obj["tool_name"])
    for t in decision_obj.get("tools", []):
        if isinstance(t, dict) and "tool_name" in t:
            names.append(t["tool_name"])
    return names


def _truncate_content(text: str, max_chars: int = 1500) -> str:
    """Truncate very long content (e.g. email bodies) to keep training
    samples within a reasonable token budget for the 2048 seq_len."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "\n[... truncated]"


def build_training_dataset(entries: list[LogEntry]) -> tuple[list[dict], dict]:
    """Walk through parsed log entries and build multi-turn ChatML samples
    that capture the full reasoning pipeline.

    Returns (dataset, stats_dict).
    """
    dataset: list[dict] = []
    stats = {
        "total_turns": 0,
        "kept": 0,
        "dropped_invalid_json": 0,
        "dropped_bad_tool": 0,
        "dropped_no_response": 0,
        "by_action": {},
    }

    # Group entries into turns (USER REQUEST → next USER REQUEST)
    turns: list[list[LogEntry]] = []
    current_turn: list[LogEntry] = []

    for entry in entries:
        if entry.actor == "USER" and entry.action == "REQUEST":
            if current_turn:
                turns.append(current_turn)
            current_turn = [entry]
        elif current_turn:
            current_turn.append(entry)
    if current_turn:
        turns.append(current_turn)

    stats["total_turns"] = len(turns)

    for turn_entries in turns:
        # Find key components
        user_entry = turn_entries[0]
        user_request = _clean_user_request(user_entry.content)
        if not user_request:
            continue

        # Find ROUTE_DECISION
        route_entry = None
        for e in turn_entries:
            if e.actor == "BRAIN" and e.action == "ROUTE_DECISION":
                route_entry = e
                break
        if not route_entry:
            continue

        # Parse route JSON
        decision_str = _clean_route_json(route_entry.content)
        try:
            decision_obj = json.loads(decision_str)
        except json.JSONDecodeError:
            stats["dropped_invalid_json"] += 1
            continue

        # Check for hallucinated tools
        tool_names = _extract_tools_from_decision(decision_obj)
        bad_tools = [t for t in tool_names if t not in ALLOWED_TOOLS]
        if bad_tools:
            stats["dropped_bad_tool"] += 1
            continue

        action_type = decision_obj.get("action", "chat")
        stats["by_action"][action_type] = stats["by_action"].get(action_type, 0) + 1

        # Build the multi-turn message sequence
        messages: list[dict] = [
            {"role": "system", "content": CIEL_SYSTEM_PROMPT},
            {"role": "user", "content": _randomize_paths_str(user_request)},
        ]

        # Turn 1 — Brain's reasoning + action decision (the "WHY")
        clean_decision = json.dumps(decision_obj, ensure_ascii=False)
        clean_decision = _randomize_paths_str(clean_decision)
        messages.append({"role": "assistant", "content": clean_decision})

        # Turn 2+ — depends on action type
        if action_type == "chat":
            # Find CHAT_RESPONSE
            chat_resp = None
            for e in turn_entries:
                if e.actor == "WORKER" and e.action == "CHAT_RESPONSE":
                    chat_resp = e
                    break
            if chat_resp:
                # Add the chat task as context, then the response
                messages.append({"role": "user", "content": "Now generate the response."})
                messages.append({"role": "assistant", "content": _truncate_content(chat_resp.content)})
            else:
                stats["dropped_no_response"] += 1
                continue

        elif action_type == "tool":
            # Find TOOL RESULT and WORKER FORMAT_RESPONSE
            tool_result = None
            worker_resp = None
            for e in turn_entries:
                if e.actor == "TOOL" and e.action == "RESULT":
                    tool_result = e
                if e.actor == "WORKER" and e.action == "FORMAT_RESPONSE":
                    worker_resp = e

            if tool_result:
                messages.append({
                    "role": "user",
                    "content": f"Tool returned:\n{_truncate_content(tool_result.content)}"
                })
                if worker_resp:
                    messages.append({"role": "assistant", "content": _truncate_content(worker_resp.content)})
                else:
                    # Some tool results go directly to user without Worker formatting
                    messages.append({"role": "assistant", "content": _truncate_content(tool_result.content)})
            # If no tool result at all, just keep the reasoning turn

        elif action_type == "code":
            # Find CODE_RESPONSE
            code_resp = None
            for e in turn_entries:
                if e.actor == "WORKER" and e.action == "CODE_RESPONSE":
                    code_resp = e
                    break
            if code_resp:
                messages.append({"role": "user", "content": "Generate the code now."})
                messages.append({"role": "assistant", "content": _truncate_content(code_resp.content)})

        elif action_type == "multi_tool":
            # Find all TOOL RESULTs and final MULTI_TOOL_FORMAT_RESPONSE
            tool_results = []
            multi_resp = None
            for e in turn_entries:
                if e.actor == "TOOL" and e.action.startswith("RESULT"):
                    tool_results.append(e)
                if e.actor == "WORKER" and e.action == "MULTI_TOOL_FORMAT_RESPONSE":
                    multi_resp = e

            if tool_results:
                combined = "\n---\n".join(
                    f"[{e.action}] {_truncate_content(e.content, 800)}"
                    for e in tool_results
                )
                messages.append({"role": "user", "content": f"Tool results:\n{combined}"})
                if multi_resp:
                    messages.append({"role": "assistant", "content": _truncate_content(multi_resp.content)})

        dataset.append({"messages": messages})
        stats["kept"] += 1

    return dataset, stats


def write_training_jsonl(dataset: list[dict], output_path: Path) -> None:
    """Write the ChatML training dataset to a JSONL file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        for sample in dataset:
            f.write(json.dumps(sample, ensure_ascii=False) + "\n")


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 4: CLI Entry Point
# ══════════════════════════════════════════════════════════════════════════════

def main() -> int:
    parser = argparse.ArgumentParser(description="Format Ciel thoughts.log for debugging and fine-tuning.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--out-md", type=Path, default=DEFAULT_MD)
    parser.add_argument("--out-jsonl", type=Path, default=DEFAULT_JSONL)
    parser.add_argument("--out-train", type=Path, default=DEFAULT_TRAIN,
                        help="Output path for ChatML training dataset.")
    parser.add_argument("--limit", type=int, default=40, help="Newest turns to include in Markdown. Use 0 for all.")
    parser.add_argument("--include-full", action="store_true", help="Include full entry bodies in Markdown details.")
    parser.add_argument("--no-train", action="store_true", help="Skip generating the training dataset.")
    args = parser.parse_args()

    if not args.input.exists():
        print(f"Input log not found: {args.input}")
        return 1

    text = args.input.read_text(encoding="utf-8", errors="replace")
    entries = parse_log(text)
    turns = group_turns(entries)

    # ── Output 1: Debug JSONL ────────────────────────────────────────────
    write_jsonl(entries, args.out_jsonl)

    # ── Output 2: Debug Markdown ─────────────────────────────────────────
    write_markdown(turns, args.out_md, args.limit, args.include_full)

    # ── Output 3: Full-Pipeline Training Dataset ─────────────────────────
    if not args.no_train:
        dataset, stats = build_training_dataset(entries)
        write_training_jsonl(dataset, args.out_train)

    # ── Summary ──────────────────────────────────────────────────────────
    print(f"Parsed entries: {len(entries)}")
    print(f"Parsed turns: {len(turns)}")
    print(f"Markdown view: {args.out_md}")
    print(f"JSONL view: {args.out_jsonl}")
    if not args.no_train:
        size_kb = args.out_train.stat().st_size / 1024
        print(f"\n{'='*55}")
        print(f"  TRAINING DATASET STATS")
        print(f"{'='*55}")
        print(f"  Total turns scanned:    {stats['total_turns']}")
        print(f"  Kept (clean):           {stats['kept']}")
        print(f"  Dropped (invalid JSON): {stats['dropped_invalid_json']}")
        print(f"  Dropped (bad tool):     {stats['dropped_bad_tool']}")
        print(f"  Dropped (no response):  {stats['dropped_no_response']}")
        print(f"  Action breakdown:       {json.dumps(stats['by_action'])}")
        print(f"  Output: {args.out_train}  ({size_kb:.1f} KB)")
        print(f"{'='*55}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
