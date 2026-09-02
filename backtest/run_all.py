"""Ciel 2.0 — unified backtest runner.

Runs the maintained regression suites in order (fast unit first, then live
LLM suites). Writes a single summary under backtest/logs/run_all_*.txt.

Usage (from project root):
    python -m backtest.run_all
    python -m backtest.run_all --unit-only
    python -m backtest.run_all --live-only
    python -m backtest.run_all --skip-exploratory   # skip live multi-turn sim

Exit code 0 only if every suite that was run reported success.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = Path(__file__).resolve().parent / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class Suite:
    name: str
    module: str                 # python -m <module>
    tier: str                   # unit | live | exploratory
    description: str = ""
    # If the suite always exits 0 even on failure, we parse stdout for a RESULT line.
    result_pattern: str | None = r"RESULT:\s*(\d+)/(\d+)\s*passed"
    # Alternate patterns used by older scripts
    alt_patterns: list[str] = field(default_factory=lambda: [
        r"(\d+)/(\d+)\s*tests?\s*passed",
        r"Total:\s*(\d+)/(\d+)\s*passed",
        r"Behavioral checks:\s*(\d+)/(\d+)\s*passed",
    ])


# Maintained suites only (generators / one-off tools are NOT in this list).
SUITES: list[Suite] = [
    Suite(
        "context",
        "backtest.test_context",
        "unit",
        "Context assembler budget/priority (0 LLM)",
    ),
    Suite(
        "active_subject",
        "backtest.test_active_subject",
        "unit",
        "Bounded Brain subject handoff, expiry, and unsafe-field rejection (0 LLM)",
    ),
    Suite(
        "user_model",
        "backtest.test_user_model",
        "unit",
        "User profile authority/decay/secrets (0 LLM)",
    ),
    Suite(
        "proactive",
        "backtest.test_proactive",
        "unit",
        "Notifier + TriggerEngine (0 LLM)",
    ),
    Suite(
        "outbound",
        "backtest.test_outbound",
        "unit",
        "Email double-send guard (stub tools)",
    ),
    Suite(
        "conversation_bugs",
        "backtest.test_conversation_bugs",
        "unit",
        "Continuation, recent turns, email recipient, open thread, RAG self-match",
    ),
    Suite(
        "quality_guards",
        "backtest.test_quality_guards",
        "unit",
        "P1 guards: sanitize, write-intent, gmail digest, HTML builder, TG upload protect",
    ),
    Suite(
        "plan_validation",
        "backtest.test_plan_validation",
        "unit",
        "Deterministic multi-tool structure, dependency, schema, and duplicate-delivery checks",
    ),
    Suite(
        "prompt_harness",
        "backtest.test_prompt_harness",
        "unit",
        "Prompt value allow-list, private-data sanitize, stale hash, test rollback (0 LLM)",
    ),
    Suite(
        "planner",
        "backtest.test_planner",
        "unit",
        "SQLite monthly/weekly plans, separate tools, catch-up triggers, and dedupe",
    ),
    Suite(
        "integration",
        "backtest.test_integration",
        "live",
        "Full CielCore pipeline across tools (live Brain/Worker)",
    ),
    Suite(
        "hard_special",
        "backtest.test_hard_special",
        "live",
        "Ambiguity, path ask, market+email, harmful, self-correct (live)",
    ),
    Suite(
        "brain_worker",
        "backtest.test_brain_worker",
        "live",
        "agent_system multi-step plan + multi-file + retry (live)",
    ),
    Suite(
        "rag_memory",
        "backtest.test_rag_memory",
        "live",
        "RAG amnesia stress after short-term overflow (live)",
    ),
    Suite(
        "live_conversation",
        "backtest.live_conversation_test",
        "exploratory",
        "Adaptive multi-turn User-Sim vs real Ciel (expensive)",
    ),
]


def _parse_pass_total(text: str, suite: Suite) -> tuple[int | None, int | None]:
    patterns = []
    if suite.result_pattern:
        patterns.append(suite.result_pattern)
    patterns.extend(suite.alt_patterns)
    for pat in patterns:
        matches = list(re.finditer(pat, text, re.IGNORECASE))
        if matches:
            m = matches[-1]
            return int(m.group(1)), int(m.group(2))
    # hard_special style: count PASS/FAIL lines if present
    pass_n = len(re.findall(r"\[PASS\]|PASS\s{2}", text))
    fail_n = len(re.findall(r"\[FAIL\]|FAIL\s{2}", text))
    if pass_n or fail_n:
        return pass_n, pass_n + fail_n
    return None, None


def _run_suite(suite: Suite, timeout: int) -> dict:
    started = time.time()
    cmd = [sys.executable, "-m", suite.module]
    print("\n" + "=" * 72)
    print(f"  RUN  [{suite.tier}] {suite.name}")
    print(f"       {suite.description}")
    print(f"       $ {' '.join(cmd)}")
    print("=" * 72)
    try:
        child_env = os.environ.copy()
        if suite.tier == "unit":
            # Unit suites stub their tool calls and must stay offline. Loading an
            # external pack can otherwise contact OAuth/provider services before
            # a test has installed its stub, turning a deterministic guard into a
            # network timeout. Live suites intentionally retain the real catalog.
            disabled = {
                item.strip()
                for item in child_env.get("DISABLED_SKILL_MODULES", "").split(",")
                if item.strip()
            }
            disabled.update({"github_ops", "gmail_ops", "telegram_ops", "trading_ops", "web_agent_ops"})
            child_env["DISABLED_SKILL_MODULES"] = ",".join(sorted(disabled))
            # RAG tests may exercise the lazy sentence-transformer loader. Unit
            # regression must use an already cached model or the graceful fallback,
            # never spend minutes retrying a Hugging Face download.
            child_env["HF_HUB_OFFLINE"] = "1"
            child_env["TRANSFORMERS_OFFLINE"] = "1"
        proc = subprocess.run(
            cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=child_env,
        )
        out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
        # Stream a short tail so the user sees progress without drowning
        tail = "\n".join(out.strip().splitlines()[-40:])
        if tail:
            print(tail)
        passed, total = _parse_pass_total(out, suite)
        elapsed = time.time() - started
        # A suite can print a partial RESULT line and then crash.  Its process exit
        # status is authoritative in that case; otherwise a regression runner can
        # report green while a test never reached its assertions.
        if proc.returncode != 0:
            ok = False
            status = "FAIL"
        # Prefer parsed result when available; else exit code.
        elif passed is not None and total is not None:
            ok = passed >= total and total > 0
            # integration treats TRANSIENT as soft-pass in its own summary;
            # if pattern matched Total: x/y we trust that.
            status = "PASS" if ok else "FAIL"
        else:
            ok = True
            status = "PASS" if ok else "FAIL"
        return {
            "name": suite.name,
            "tier": suite.tier,
            "module": suite.module,
            "status": status,
            "exit_code": proc.returncode,
            "passed": passed,
            "total": total,
            "elapsed_s": round(elapsed, 1),
            "output": out,
        }
    except subprocess.TimeoutExpired as e:
        out = ""
        if e.stdout:
            out += e.stdout if isinstance(e.stdout, str) else e.stdout.decode("utf-8", "replace")
        if e.stderr:
            out += "\n" + (e.stderr if isinstance(e.stderr, str) else e.stderr.decode("utf-8", "replace"))
        print(f"  [TIMEOUT] after {timeout}s")
        return {
            "name": suite.name,
            "tier": suite.tier,
            "module": suite.module,
            "status": "TIMEOUT",
            "exit_code": -1,
            "passed": None,
            "total": None,
            "elapsed_s": float(timeout),
            "output": out,
        }
    except Exception as e:
        print(f"  [ERROR] {e}")
        return {
            "name": suite.name,
            "tier": suite.tier,
            "module": suite.module,
            "status": "ERROR",
            "exit_code": -1,
            "passed": None,
            "total": None,
            "elapsed_s": round(time.time() - started, 1),
            "output": str(e),
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run all Ciel backtest suites")
    parser.add_argument("--unit-only", action="store_true", help="Only 0-LLM / stub suites")
    parser.add_argument("--live-only", action="store_true", help="Only live LLM suites")
    parser.add_argument(
        "--skip-exploratory",
        action="store_true",
        help="Skip expensive multi-turn live_conversation_test",
    )
    parser.add_argument(
        "--unit-timeout",
        type=int,
        default=180,
        help="Timeout seconds per unit suite (default 180)",
    )
    parser.add_argument(
        "--live-timeout",
        type=int,
        default=1800,
        help="Timeout seconds per live suite (default 1800 = 30 min)",
    )
    parser.add_argument(
        "--exploratory-timeout",
        type=int,
        default=2400,
        help="Timeout for live_conversation (default 40 min)",
    )
    args = parser.parse_args(argv)

    suites = list(SUITES)
    if args.unit_only:
        suites = [s for s in suites if s.tier == "unit"]
    elif args.live_only:
        suites = [s for s in suites if s.tier in ("live", "exploratory")]
    if args.skip_exploratory:
        suites = [s for s in suites if s.tier != "exploratory"]

    started = datetime.now()
    print("=" * 72)
    print("  CIEL 2.0 — UNIFIED BACKTEST")
    print(f"  Started: {started.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Suites:  {', '.join(s.name for s in suites)}")
    print("=" * 72)

    results: list[dict] = []
    for suite in suites:
        if suite.tier == "unit":
            to = args.unit_timeout
        elif suite.tier == "exploratory":
            to = args.exploratory_timeout
        else:
            to = args.live_timeout
        results.append(_run_suite(suite, to))

    ended = datetime.now()
    # ---- summary report ----
    lines = []
    lines.append("=" * 72)
    lines.append("  CIEL 2.0 — UNIFIED BACKTEST REPORT")
    lines.append(f"  Started: {started.isoformat(timespec='seconds')}")
    lines.append(f"  Ended:   {ended.isoformat(timespec='seconds')}")
    lines.append(f"  Duration: {(ended - started).total_seconds():.0f}s")
    lines.append("=" * 72)
    lines.append("")
    lines.append(f"{'Suite':<22} {'Tier':<12} {'Status':<10} {'Score':<12} {'Time'}")
    lines.append("-" * 72)

    n_pass = n_fail = 0
    for r in results:
        score = (
            f"{r['passed']}/{r['total']}"
            if r["passed"] is not None and r["total"] is not None
            else f"exit={r['exit_code']}"
        )
        lines.append(
            f"{r['name']:<22} {r['tier']:<12} {r['status']:<10} {score:<12} {r['elapsed_s']}s"
        )
        if r["status"] == "PASS":
            n_pass += 1
        else:
            n_fail += 1

    lines.append("-" * 72)
    lines.append(f"TOTAL: {n_pass} PASS / {n_fail} FAIL out of {len(results)} suites")
    lines.append("")
    if n_fail:
        lines.append("Failed / timed-out suites:")
        for r in results:
            if r["status"] != "PASS":
                lines.append(f"  - {r['name']} ({r['status']})")
    lines.append("=" * 72)

    report = "\n".join(lines)
    print("\n" + report)

    stamp = started.strftime("%Y%m%d_%H%M%S")
    report_path = LOG_DIR / f"run_all_{stamp}.txt"
    # Also dump per-suite raw tails for debugging
    detail_path = LOG_DIR / f"run_all_{stamp}_detail.txt"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report + "\n")
    with open(detail_path, "w", encoding="utf-8") as f:
        for r in results:
            f.write("\n" + "=" * 72 + "\n")
            f.write(f"SUITE: {r['name']}  STATUS: {r['status']}\n")
            f.write("=" * 72 + "\n")
            f.write(r.get("output") or "")
            f.write("\n")

    print(f"\n  Summary: {report_path}")
    print(f"  Detail:  {detail_path}")

    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
