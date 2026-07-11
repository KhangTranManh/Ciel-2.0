"""Cumulative LLM cost/usage report — the offline, over-time counterpart to the live
vitals feed. Mines ciel_data/logs/thoughts.log for every ``[LLM_CALL]`` entry, sums
exact token counts (reported by the provider) per tier / per model / per day, and
applies core/cost.py pricing to estimate USD spend.

Deterministic and read-only (no LLM call, never edits the log) — same discipline as
scripts/prompt_harness.py. Token totals are exact; the dollar figure is an ESTIMATE
that depends on the prices in core/cost.py (override via ciel_data/model_pricing.json).

Run from the Ciel 2.0 directory:
    python -m scripts.cost_report [--since-days N] [--by-day] [--json]
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from scripts.format_thoughts_log import parse_log
from core.cost import estimate_cost, price_for

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOG = ROOT / "ciel_data" / "logs" / "thoughts.log"
DEFAULT_OUT = ROOT / "backtest" / "logs"

# Matches the format written by agent_system.utils.usage.format_usage:
#   "model=<id> in=<n> out=<n> total=<n>"
_MODEL_RE = re.compile(r"model=(\S+)")
_IN_RE = re.compile(r"\bin=(\d+)")
_OUT_RE = re.compile(r"\bout=(\d+)")


def _parse_call(content: str):
    """Return (model, input_tokens, output_tokens) from an LLM_CALL content line, or
    None if it isn't a parseable call entry."""
    m = _MODEL_RE.search(content)
    if not m:
        return None
    inp = _IN_RE.search(content)
    out = _OUT_RE.search(content)
    return m.group(1), int(inp.group(1)) if inp else 0, int(out.group(1)) if out else 0


def collect(log_path: Path, since_days: int | None):
    """Walk the log once, aggregating usage by tier, model, and day."""
    if not log_path.exists():
        raise SystemExit(f"[cost_report] Log not found: {log_path}")

    cutoff = None
    if since_days is not None:
        cutoff = datetime.now() - timedelta(days=since_days)

    entries = parse_log(log_path.read_text(encoding="utf-8", errors="replace"))

    def _mk():
        return {"calls": 0, "input": 0, "output": 0, "total": 0, "cost": 0.0}

    by_tier = defaultdict(_mk)
    by_model = defaultdict(_mk)
    by_day = defaultdict(_mk)
    overall = _mk()

    for e in entries:
        if e.action.upper() != "LLM_CALL":
            continue
        parsed = _parse_call(e.content)
        if not parsed:
            continue
        if cutoff is not None:
            try:
                ts = datetime.strptime(e.timestamp, "%Y-%m-%d %H:%M:%S")
                if ts < cutoff:
                    continue
            except ValueError:
                pass  # keep un-timestamped/odd entries rather than silently dropping
        model, inp, out = parsed
        cost = estimate_cost(model, inp, out)
        day = e.timestamp[:10] if e.timestamp else "unknown"
        for bucket in (by_tier[e.actor], by_model[model], by_day[day], overall):
            bucket["calls"] += 1
            bucket["input"] += inp
            bucket["output"] += out
            bucket["total"] += inp + out
            bucket["cost"] = round(bucket["cost"] + cost, 6)

    return {
        "by_tier": dict(by_tier),
        "by_model": dict(by_model),
        "by_day": dict(by_day),
        "overall": overall,
    }


def _fmt_row(label: str, b: dict, width: int = 22) -> str:
    return (f"  {label:<{width}} {b['calls']:>6} calls  "
            f"in={b['input']:>9,}  out={b['output']:>9,}  "
            f"total={b['total']:>10,}  ${b['cost']:.4f}")


def render(data: dict, since_days: int | None) -> str:
    lines = []
    lines.append("=" * 78)
    scope = f"last {since_days} day(s)" if since_days is not None else "all time"
    lines.append(f"  CIEL LLM COST / USAGE REPORT  -  {scope}")
    lines.append(f"  generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("=" * 78)

    ov = data["overall"]
    if ov["calls"] == 0:
        lines.append("\n  No [LLM_CALL] entries found in range. Nothing to report yet.")
        lines.append("  (Older log entries logged only 'model=<name>' with no token counts;")
        lines.append("   token/cost data starts once the July 2026 usage change is live.)")
        lines.append("=" * 78)
        return "\n".join(lines)

    lines.append("\n-- By tier --")
    for actor in sorted(data["by_tier"], key=lambda a: -data["by_tier"][a]["cost"]):
        lines.append(_fmt_row(actor, data["by_tier"][actor]))

    lines.append("\n-- By model --")
    for model in sorted(data["by_model"], key=lambda m: -data["by_model"][m]["cost"]):
        p = price_for(model)
        priced = "" if (p["input"] or p["output"]) else "  (no price set -> $0)"
        lines.append(_fmt_row(model, data["by_model"][model], width=28) + priced)

    lines.append("\n-- By day --")
    for day in sorted(data["by_day"]):
        lines.append(_fmt_row(day, data["by_day"][day]))

    lines.append("\n-- TOTAL --")
    lines.append(_fmt_row("ALL", ov))
    lines.append("=" * 78)
    lines.append("  Tokens are exact (provider-reported). Cost is an ESTIMATE from "
                 "core/cost.py;")
    lines.append("  set real prices in ciel_data/model_pricing.json to sharpen it.")
    lines.append("=" * 78)
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="Cumulative LLM cost/usage report from thoughts.log")
    ap.add_argument("--since-days", type=int, default=None,
                    help="Only count calls from the last N days (default: all time).")
    ap.add_argument("--log", type=Path, default=DEFAULT_LOG, help="Path to thoughts.log")
    ap.add_argument("--json", action="store_true",
                    help="Also write a machine-readable JSON report to backtest/logs/.")
    args = ap.parse_args()

    data = collect(args.log, args.since_days)
    report = render(data, args.since_days)
    print(report)

    if args.json:
        DEFAULT_OUT.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_file = DEFAULT_OUT / f"cost_report_{stamp}.json"
        out_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[cost_report] JSON written to {out_file}")


if __name__ == "__main__":
    main()
