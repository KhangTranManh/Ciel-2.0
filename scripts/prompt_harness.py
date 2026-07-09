"""Prompt-rewrite harness — mines ciel_data/logs/thoughts.log for RECURRING failure
patterns (Middleware corrections, tool errors, self-healing triggers, self-correction
retries) and proposes which system prompt to patch and why.

This does NOT touch any prompt file. It is a deterministic pattern-miner: it groups
raw log evidence into named failure signatures, counts occurrences, and points at a
concrete prompt constant to edit — a human (or a follow-up conversation) still writes
and reviews the actual prompt wording. Consistent with the project's standing rule:
prefer a deterministic check over trusting an LLM's own judgment, here applied to the
*prompts themselves* instead of to a single request.

Run from Ciel 2.0 directory:
    python -m scripts.prompt_harness [--min-count 2] [--since-days N]
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from scripts.format_thoughts_log import LogEntry, parse_log

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOG = ROOT / "ciel_data" / "logs" / "thoughts.log"
DEFAULT_OUT = ROOT / "backtest" / "logs"

# ── Where a given tool's prompt actually lives ──────────────────────────────
TOOL_TO_PROMPT = {
    "send_gmail_message": ("skills/external/gmail_ops.py", "GMAIL_SYSTEM_PROMPT"),
    "send_gmail_html_message": ("skills/external/gmail_ops.py", "GMAIL_SYSTEM_PROMPT"),
    "reply_to_email": ("skills/external/gmail_ops.py", "GMAIL_SYSTEM_PROMPT"),
    "create_gmail_draft": ("skills/external/gmail_ops.py", "GMAIL_SYSTEM_PROMPT"),
    "search_gmail": ("skills/external/gmail_ops.py", "GMAIL_SYSTEM_PROMPT"),
    "trash_email": ("skills/external/gmail_ops.py", "GMAIL_SYSTEM_PROMPT"),
    "mark_email_read": ("skills/external/gmail_ops.py", "GMAIL_SYSTEM_PROMPT"),
    "get_market_price": ("skills/external/trading_ops.py", "TRADING_SYSTEM_PROMPT"),
    "get_crypto_stats": ("skills/external/trading_ops.py", "TRADING_SYSTEM_PROMPT"),
    "analyze_crypto_technical": ("skills/external/trading_ops.py", "TRADING_SYSTEM_PROMPT"),
    "build_market_report_html": ("skills/external/trading_ops.py", "TRADING_SYSTEM_PROMPT"),
    "stealth_search": ("skills/external/web_agent_ops.py", "WEB_AGENT_SYSTEM_PROMPT"),
    "smart_scrape": ("skills/external/web_agent_ops.py", "WEB_AGENT_SYSTEM_PROMPT"),
    "write_file": ("skills/internal/system_ops.py", "SYSTEM_OPS_PROMPT"),
    "append_file": ("skills/internal/system_ops.py", "SYSTEM_OPS_PROMPT"),
    "read_file": ("skills/internal/system_ops.py", "SYSTEM_OPS_PROMPT"),
    "read_document": ("skills/internal/system_ops.py", "SYSTEM_OPS_PROMPT"),
    "delete_file": ("skills/internal/system_ops.py", "SYSTEM_OPS_PROMPT"),
    "run_python_script": ("skills/internal/system_ops.py", "SYSTEM_OPS_PROMPT"),
    "execute_shell_command": ("skills/internal/os_ops.py", "OS_OPS_PROMPT"),
    "git_confirm_push": ("skills/external/github_ops.py", "GIT_SYSTEM_PROMPT"),
    "git_commit_and_push": ("skills/external/github_ops.py", "GIT_SYSTEM_PROMPT"),
}
DEFAULT_PROMPT_TARGET = ("core/router.py", "CIEL_ROUTER_PROMPT")
MIDDLEWARE_PROMPT_TARGET = ("agent_system/models/middleware.py", "MIDDLEWARE_SYSTEM_PROMPT")
HEALING_PROMPT_TARGET = ("core/recovery_manager.py", "ROBUST OS DEVELOPER prompt")

# ── Keyword tags for Middleware reasoning text (deterministic, no LLM call) ──
MIDDLEWARE_TAGS = [
    ("LANGUAGE_MISMATCH", r"vietnamese|tiếng việt|ngôn ngữ|wrong language|in english"),
    ("NUMERIC_CONTRADICTION", r"contradict|bearish|bullish|does not match|mismatch.*(price|ma5|ma30|trend|rsi)"),
    ("HOLLOW_CONTENT", r"no real content|placeholder|hollow|not synthesized|missing.*data|template"),
    ("RELEVANCE_MISMATCH", r"unrelated|does not address|original request|off.?topic|missing/empty"),
    ("PROVIDER_ERROR", r"provider down|review_error|fail-open|timeout|api error"),
]

# ── Keyword tags for self-correction reasoning text — the SAME root cause
# (e.g. "gathered data but never sent the email") recurs across many different
# tool_names, so tagging by root cause instead of by tool avoids fragmenting
# one systemic router/planning gap into a dozen tiny per-tool buckets. ──
SELF_CORRECTION_TAGS = [
    ("INCOMPLETE_MULTISTEP", r"only (fetched|retrieved|listed|gathered|found|installed|located|identified|verified|checked|provides?)|but (did not|failed to|does not|not perform)|instead of (actually\s*)?(reading|sending|performing|checking)"),
    ("WRONG_TOOL_CHOICE", r"another .*tool might work|is a more (robust|better) alternative|is specifically designed for|better alternative"),
    ("HALLUCINATED_ACTION", r"hallucinat"),
    ("EXTERNAL_DEPENDENCY_MISSING", r"not installed|library is not|missing (dependency|package|library)"),
    ("INVALID_OR_FICTIONAL_INPUT", r"fictional|does not exist|invalid symbol|could not find"),
]

TOOL_NAME_RE = re.compile(r"^(?P<tool>[a-z_]+)(?:\s*\(pass[^)]*\))?\s*:\s*(?P<rest>.*)$", re.IGNORECASE)
EXC_CLASS_RE = re.compile(r"\b([A-Z][A-Za-z]*(?:Error|Exception))\b")


@dataclass
class Finding:
    signature: str
    signal_type: str
    count: int
    prompt_file: str
    prompt_target: str
    examples: list


def _tag_middleware_reasoning(text: str) -> str:
    low = text.lower()
    for tag, pattern in MIDDLEWARE_TAGS:
        if re.search(pattern, low):
            return tag
    return "OTHER"


def _tag_self_correction_reasoning(text: str) -> str:
    low = text.lower()
    for tag, pattern in SELF_CORRECTION_TAGS:
        if re.search(pattern, low):
            return tag
    return "OTHER"


def _turns(entries: list[LogEntry]) -> list[list[LogEntry]]:
    turns, current = [], []
    for e in entries:
        if e.actor == "USER" and e.action == "REQUEST":
            if current:
                turns.append(current)
            current = [e]
        elif current:
            current.append(e)
    if current:
        turns.append(current)
    return turns


def _turn_tool_names(turn: list[LogEntry]) -> list[str]:
    """Best-effort: pull tool_name(s) out of this turn's BRAIN ROUTE_DECISION JSON."""
    for e in turn:
        if e.actor == "BRAIN" and e.action == "ROUTE_DECISION":
            try:
                raw = e.content.strip()
                if raw.startswith("```"):
                    raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0]
                decision = json.loads(raw)
            except Exception:
                return []
            names = []
            if decision.get("tool_name"):
                names.append(decision["tool_name"])
            for t in decision.get("tools", []) or []:
                if isinstance(t, dict) and t.get("tool_name"):
                    names.append(t["tool_name"])
            return names
    return []


def mine_middleware(entries: list[LogEntry]) -> dict:
    clusters = defaultdict(list)
    for e in entries:
        if e.actor != "MIDDLEWARE" or e.action not in ("REVISED", "FLAGGED_UNFIXABLE"):
            continue
        first_line = e.content.split("\n", 1)[0]
        m = TOOL_NAME_RE.match(first_line)
        tool = m.group("tool") if m else "unknown_tool"
        reasoning = m.group("rest") if m else first_line
        tag = _tag_middleware_reasoning(reasoning)
        clusters[(tag, tool)].append((e.timestamp, reasoning.strip()[:220]))
    return clusters


def mine_healing(entries: list[LogEntry]) -> dict:
    clusters = defaultdict(list)
    for e in entries:
        if e.actor != "HEALING" or e.action != "DETECT_ERROR":
            continue
        m = EXC_CLASS_RE.search(e.content)
        exc = m.group(1) if m else "UnclassifiedError"
        clusters[("HEALING_TRIGGER", exc)].append((e.timestamp, e.content.strip().splitlines()[0][:220] if e.content.strip() else ""))
    return clusters


# Tags that are cross-cutting router/planning gaps — cluster by tag ALONE,
# ignoring which specific tool was involved, so the same root cause across
# many tools shows up as ONE finding instead of fragmenting into many.
_SELF_CORRECTION_CROSS_CUTTING = {"INCOMPLETE_MULTISTEP", "WRONG_TOOL_CHOICE", "HALLUCINATED_ACTION"}


def mine_self_correction(entries: list[LogEntry]) -> dict:
    """Bind each EVALUATE_RESULT to the tool that actually ran immediately
    before it (not the turn's original routing decision) — a single turn can
    evaluate several different sub-steps in a multi_tool/self-correction loop,
    so the turn-level tool_name is frequently the wrong attribution."""
    clusters = defaultdict(list)
    for turn in _turns(entries):
        initial = _turn_tool_names(turn)
        last_tool = initial[0] if initial else None
        for e in turn:
            if e.actor == "TOOL":
                m = re.match(r"RESULT_([A-Za-z_]+)", e.action)
                if m:
                    last_tool = m.group(1).lower()
                else:
                    m2 = TOOL_NAME_RE.match(e.content.split("\n", 1)[0])
                    if m2:
                        last_tool = m2.group("tool").lower()
            if e.actor == "BRAIN" and e.action == "EVALUATE_RESULT":
                low = e.content.lower().replace(" ", "")
                if '"satisfied":false' in low:
                    key_tool = last_tool or "unknown_tool"
                    tag = _tag_self_correction_reasoning(e.content)
                    example = (e.timestamp, f"[{key_tool}] {e.content.strip()[:220]}")
                    if tag in _SELF_CORRECTION_CROSS_CUTTING:
                        clusters[(f"SELF_CORRECTION:{tag}", "*")].append(example)
                    else:
                        clusters[(f"SELF_CORRECTION:{tag}", key_tool)].append(example)
    return clusters


def mine_tool_errors(entries: list[LogEntry]) -> dict:
    clusters = defaultdict(list)
    for turn in _turns(entries):
        tool_names = _turn_tool_names(turn)
        for e in turn:
            if e.actor != "TOOL":
                continue
            if "TOOL_ERROR" not in e.content and "Lỗi chạy script" not in e.content:
                continue
            m = re.match(r"RESULT_([A-Za-z_]+)", e.action)
            tool = m.group(1).lower() if m else (tool_names[0] if tool_names else "unknown_tool")
            snippet = e.content.strip().splitlines()[0][:220] if e.content.strip() else ""
            clusters[("TOOL_ERROR", tool)].append((e.timestamp, snippet))
    return clusters


def _target_for(signal_type: str, key: str) -> tuple:
    if signal_type in ("LANGUAGE_MISMATCH", "NUMERIC_CONTRADICTION", "HOLLOW_CONTENT", "RELEVANCE_MISMATCH", "PROVIDER_ERROR"):
        return MIDDLEWARE_PROMPT_TARGET
    if signal_type == "HEALING_TRIGGER":
        return HEALING_PROMPT_TARGET
    if signal_type.startswith("SELF_CORRECTION:"):
        tag = signal_type.split(":", 1)[1]
        if tag in _SELF_CORRECTION_CROSS_CUTTING:
            return DEFAULT_PROMPT_TARGET  # router-level planning gap
        if tag == "EXTERNAL_DEPENDENCY_MISSING":
            return ("requirements.txt / environment", "N/A — not a prompt fix")
        return TOOL_TO_PROMPT.get(key, DEFAULT_PROMPT_TARGET)
    return TOOL_TO_PROMPT.get(key, DEFAULT_PROMPT_TARGET)


def build_findings(entries: list[LogEntry], min_count: int) -> list:
    all_clusters = {}
    for miner in (mine_middleware, mine_healing, mine_self_correction, mine_tool_errors):
        for k, v in miner(entries).items():
            all_clusters.setdefault(k, []).extend(v)

    findings = []
    for (signal_type, key), occurrences in all_clusters.items():
        if len(occurrences) < min_count:
            continue
        prompt_file, prompt_target = _target_for(signal_type, key)
        occurrences.sort(key=lambda x: x[0])
        label = signal_type if key == "*" else f"{signal_type} / {key}"
        findings.append(Finding(
            signature=label,
            signal_type=signal_type,
            count=len(occurrences),
            prompt_file=prompt_file,
            prompt_target=prompt_target,
            examples=occurrences[-3:],
        ))
    findings.sort(key=lambda f: f.count, reverse=True)
    return findings


def render_report(findings: list, total_entries: int, since: str) -> str:
    lines = [
        "# Prompt Rewrite Proposals",
        "",
        f"Mined from `ciel_data/logs/thoughts.log` ({total_entries} entries{since}).",
        "Each item below is a RECURRING pattern (not a one-off) — review before editing any prompt.",
        "",
    ]
    if not findings:
        lines.append("No pattern crossed the minimum occurrence threshold. Nothing to propose.")
        return "\n".join(lines)

    for f in findings:
        lines.append(f"## {f.signature} — {f.count}x")
        lines.append(f"**Target:** `{f.prompt_file}` → `{f.prompt_target}`")
        lines.append("")
        lines.append("Recent examples:")
        for ts, text in f.examples:
            lines.append(f"- `{ts}` — {text}")
        lines.append("")
        lines.append("Suggested next step: read the examples above, then add ONE explicit rule to the")
        lines.append(f"target prompt that would have prevented this specific recurring mistake.")
        lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-count", type=int, default=2, help="Minimum occurrences for a pattern to be reported.")
    ap.add_argument("--since-days", type=int, default=0, help="Only consider entries from the last N days (0 = all history).")
    ap.add_argument("--log", default=str(DEFAULT_LOG))
    args = ap.parse_args()

    text = Path(args.log).read_text(encoding="utf-8", errors="ignore")
    entries = parse_log(text)

    since_label = ""
    if args.since_days > 0:
        cutoff = datetime.now() - timedelta(days=args.since_days)
        entries = [e for e in entries if _safe_parse_ts(e.timestamp) >= cutoff]
        since_label = f", last {args.since_days}d"

    findings = build_findings(entries, args.min_count)
    report = render_report(findings, len(entries), since_label)

    DEFAULT_OUT.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = DEFAULT_OUT / f"prompt_rewrite_proposals_{ts}.md"
    out_path.write_text(report, encoding="utf-8")

    print(report)
    print(f"\nSaved: {out_path}")


def _safe_parse_ts(ts: str) -> datetime:
    try:
        return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return datetime.min


if __name__ == "__main__":
    main()
