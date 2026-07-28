"""run_test_samples.py — replays backtest/logs/test_samples.json (built by
generate_test_samples.py) against the REAL CielCore agent, one fresh conversation
per sample, and writes a report for human review. There is no automated pass/fail
here — an open-ended generated conversation cannot be graded by a fixed assertion
the way backtest/test_conversation_bugs.py can. This is a triage tool: it flags
the samples worth a human actually reading, out of however many you generated.

SAFETY
------
Every sample runs with `core.unattended = True`. This is not a special sandbox
built for this script — it is the SAME Tier-6 mechanism the agent already uses for
unattended/background operation (see core/permissions.py, core/notifier.py): any
tool that would normally ASK for confirmation is instead DEFERRED (recorded, never
executed) when nobody is at the keyboard. A generated sample asking to "send an
email" or "delete a file" will route and reach the safety gate exactly like a real
request would — it just cannot get a live human Y/N, so it stops there instead of
running. Read-only tools (search, price lookups, file reads) still execute for
real, since that is what unattended mode has always allowed.

If you want zero real network calls at all (not even read-only), see
`--tools-file` to point ToolManager at a stub tool set instead — not built here
since most bugs so far were in the ROUTING/CHAINING layer, which needs real tool
shapes to exercise.

ISOLATION
---------
Each sample gets a throwaway sandbox directory for chat history, pending
confirmations, task records, deferred approvals, the facts vault, and the todo
list (see `_isolate()`) — none of that touches the Master's real ciel_data/
ciel_workspace files, and none of it leaks between samples either. A prior run
(before this existed) left a REAL unresolved git_confirm_push pending against
this actual repo and silently contaminated ~30 later samples with the same
stale reply. take_screenshot/open_application/vision_act still act on the real
desktop regardless — that's not fixable by path redirection, see the note above
`_isolate()`.

Run:
    python -m backtest.run_test_samples
    python -m backtest.run_test_samples --samples backtest/logs/test_samples.json --limit 20
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.llm_connector import CielCore  # noqa: E402
from backtest._sandbox import isolate, new_sandbox  # noqa: E402

DEFAULT_SAMPLES = ROOT / "backtest" / "logs" / "test_samples.json"
DEFAULT_REPORT_DIR = ROOT / "backtest" / "logs"

# Deterministic red flags in a response — same failure vocabulary the rest of this
# codebase already uses (core/continuation.py's _FAILED_STEP_RE, task_state.py),
# reused here instead of inventing a third copy of the same check.
_FLAG_RE = re.compile(
    r"^\s*(?:\[TOOL_ERROR|\[EXECUTION_ERROR|\[CANCELLED|Error[:\s]|Lỗi[:\s]|"
    r"API Error|Sorry,|(?:File )?not found)", re.IGNORECASE)
_EXCEPTION_MARKERS = ("Traceback (most recent call last)", "AttributeError",
                      "KeyError", "TypeError", "ValueError")


def _flag_reason(response: str) -> str:
    if any(m in response for m in _EXCEPTION_MARKERS):
        return "raw exception leaked into the response"
    if _FLAG_RE.match(response or ""):
        return "response matches known failure pattern"
    if len(response or "") < 3:
        return "empty/near-empty response"
    return ""


def run_one(sample: dict) -> dict:
    core = CielCore()
    core.unattended = True  # Tier 6 — risky steps DEFER instead of running unattended
    sandbox = new_sandbox(f"ciel_test_{sample.get('id', 'x')}_")
    try:
        isolate(core, sandbox)
        turns_out = []
        flagged = False
        for turn in sample["turns"]:
            t0 = time.time()
            try:
                response = core.process(turn)
            except Exception as e:
                response = f"[HARNESS_EXCEPTION] {type(e).__name__}: {e}\n{traceback.format_exc()[-800:]}"
            elapsed = time.time() - t0
            reason = _flag_reason(response)
            if reason:
                flagged = True
            turns_out.append({
                "user": turn, "response": response,
                "elapsed_s": round(elapsed, 2), "flag": reason,
            })
        return {
            "id": sample.get("id"), "tool_hint": sample.get("tool_hint"),
            "type": sample.get("type"), "note": sample.get("note", ""),
            "flagged": flagged, "turns": turns_out,
        }
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


def render_report(results: list[dict]) -> str:
    flagged = [r for r in results if r["flagged"]]
    lines = [
        "# Test Sample Run Report",
        "",
        f"{len(results)} sample(s) run, {len(flagged)} flagged for review.",
        "",
        "## Flagged (review these first)",
        "",
    ]
    if not flagged:
        lines.append("None — nothing matched a known failure pattern. Still worth")
        lines.append("spot-checking a few unflagged ones by hand; this is triage, not proof.")
    for r in flagged:
        lines.append(f"### {r['id']} — tool_hint={r['tool_hint']} ({r['type']}) — {r['note']}")
        for i, t in enumerate(r["turns"], 1):
            mark = f" ⚠ {t['flag']}" if t["flag"] else ""
            lines.append(f"{i}. **User:** {t['user']}")
            lines.append(f"   **Ciel:** {t['response'][:400]}{mark}")
        lines.append("")

    lines.append("## Everything else (summary only)")
    lines.append("")
    for r in results:
        if r["flagged"]:
            continue
        # Bug found live: pairing turn[0]'s user text with turn[-1]'s response made
        # correctly-handled multi-turn samples look like non-sequiturs — e.g. turn 1
        # asked to open a file, turn 2 asked to open Calculator (answered correctly),
        # and the summary showed "open file" -> "opened calc.exe" as if turn 1 had
        # gone wrong. Show every turn's own user->response pairing instead of
        # cherry-picking mismatched ends for anything beyond a single turn.
        if len(r["turns"]) == 1:
            resp = r["turns"][0]["response"][:120].replace("\n", " ")
            lines.append(f"- {r['id']} ({r['tool_hint']}): \"{r['turns'][0]['user'][:60]}\" -> {resp}")
        else:
            lines.append(f"- {r['id']} ({r['tool_hint']}), {len(r['turns'])} turns:")
            for i, t in enumerate(r["turns"], 1):
                resp = t["response"][:100].replace("\n", " ")
                lines.append(f"    {i}. \"{t['user'][:60]}\" -> {resp}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", default=str(DEFAULT_SAMPLES))
    ap.add_argument("--limit", type=int, default=0, help="0 = run all samples")
    args = ap.parse_args()

    samples_path = Path(args.samples)
    if not samples_path.exists():
        raise SystemExit(f"No samples file at {samples_path} — run generate_test_samples.py first.")
    samples = json.loads(samples_path.read_text(encoding="utf-8"))
    if args.limit:
        samples = samples[:args.limit]

    results = []
    for i, s in enumerate(samples, 1):
        print(f"[{i}/{len(samples)}] {s.get('id')} ({s.get('tool_hint')}) ...")
        results.append(run_one(s))

    report = render_report(results)
    ts = time.strftime("%Y%m%d_%H%M%S")
    out_path = DEFAULT_REPORT_DIR / f"test_samples_report_{ts}.md"
    out_path.write_text(report, encoding="utf-8")
    print(f"\n{sum(r['flagged'] for r in results)}/{len(results)} flagged.")
    print(f"Report: {out_path}")


if __name__ == "__main__":
    main()
