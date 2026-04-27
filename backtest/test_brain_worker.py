"""Brain-Worker Workflow Test v2
Tests multi-step routing, multi-file output, retry resilience, and logs all interactions.

Run from Ciel 2.0 directory:
    python -m backtest.test_brain_worker
"""
import sys
import os
import json
import time
from datetime import datetime
from pathlib import Path

# Fix Windows encoding
os.environ["PYTHONIOENCODING"] = "utf-8"
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Add parent to path so we can import agent_system
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from colorama import Fore, Style, init as colorama_init
colorama_init()

from agent_system.models.brain import Brain
from agent_system.models.worker import Worker
from agent_system.tools.buffer_writer import buffer_writer


# ==========================================================
# INTERACTION LOG
# ==========================================================
class InteractionLog:
    """Records all Brain/Worker exchanges for review."""

    def __init__(self):
        self.entries = []
        self.start_time = datetime.now()

    def add(self, actor: str, action: str, input_data: str, output_data: str):
        self.entries.append({
            "timestamp": datetime.now().isoformat(),
            "actor": actor,
            "action": action,
            "input": input_data,
            "output": output_data,
        })

    def save(self, filepath: str):
        os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)

        # --- Readable text log ---
        lines = []
        lines.append("=" * 70)
        lines.append("  BRAIN-WORKER INTERACTION LOG v2")
        lines.append(f"  Started: {self.start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"  Ended:   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"  Total exchanges: {len(self.entries)}")
        lines.append("=" * 70)

        for i, entry in enumerate(self.entries, 1):
            lines.append("")
            lines.append(f"--- Exchange {i} ---")
            lines.append(f"  Actor:  {entry['actor']}")
            lines.append(f"  Action: {entry['action']}")
            lines.append(f"  Time:   {entry['timestamp']}")
            lines.append(f"  Input:")
            for line in entry["input"].split("\n"):
                lines.append(f"    | {line}")
            lines.append(f"  Output:")
            for line in entry["output"].split("\n"):
                lines.append(f"    > {line}")

        lines.append("")
        lines.append("=" * 70)
        lines.append("  END OF LOG")
        lines.append("=" * 70)

        text_content = "\n".join(lines)

        txt_path = filepath.replace(".json", ".txt")
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(text_content)

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump({
                "start_time": self.start_time.isoformat(),
                "end_time": datetime.now().isoformat(),
                "total_exchanges": len(self.entries),
                "entries": self.entries,
            }, f, ensure_ascii=False, indent=2)

        return txt_path, filepath


def header(msg):
    print(Fore.CYAN + f"\n{'='*60}" + Style.RESET_ALL)
    print(Fore.CYAN + f"  {msg}" + Style.RESET_ALL)
    print(Fore.CYAN + f"{'='*60}" + Style.RESET_ALL)


def section(msg):
    print(Fore.WHITE + f"\n  --- {msg} ---" + Style.RESET_ALL)


# ==========================================================
# CORE TEST RUNNER
# ==========================================================
def run_test(test_name: str, user_request: str, ilog: InteractionLog):
    """Run one test case through Brain -> Worker pipeline."""
    header(f"TEST: {test_name}")
    print(Fore.GREEN + f'  Request: "{user_request}"' + Style.RESET_ALL)

    # Reset buffer between tests
    buffer_writer.clear()

    # --- BRAIN PLANS ---
    section("BRAIN: Analyzing & Planning")
    brain = Brain()
    ilog.add("BRAIN", "receive_request", user_request, "(processing...)")

    try:
        plan = brain.plan(user_request)
    except Exception as e:
        print(Fore.RED + f"  [FAIL] Brain error: {e}" + Style.RESET_ALL)
        ilog.add("BRAIN", "error", user_request, str(e))
        return False

    plan_json = json.dumps(plan, indent=2, ensure_ascii=False)
    print(Fore.YELLOW + f"  Brain's Plan:" + Style.RESET_ALL)
    for line in plan_json.split("\n"):
        print(Fore.YELLOW + f"    {line}" + Style.RESET_ALL)
    ilog.add("BRAIN", "plan_created", user_request, plan_json)

    steps = plan.get("plan", [])
    if not steps:
        print(Fore.RED + "  [FAIL] Brain returned empty plan!" + Style.RESET_ALL)
        ilog.add("BRAIN", "empty_plan", user_request, "No steps returned")
        return False

    print(Fore.GREEN + f"  [OK] Plan has {len(steps)} step(s)" + Style.RESET_ALL)

    # Count expected routing transitions
    worker_steps = sum(1 for s in steps if s.get("assign") == "worker")
    tool_steps = sum(1 for s in steps if s.get("assign") == "tool")
    flush_steps = sum(1 for s in steps if s.get("flush_to"))
    print(Fore.WHITE + f"  Expected: {worker_steps} worker, {tool_steps} tool, {flush_steps} flush" + Style.RESET_ALL)

    # --- WORKER EXECUTES EACH STEP ---
    worker = Worker()
    results = []
    files_written = []

    for step in steps:
        step_num = step.get("step", "?")
        task = step.get("task", "")
        assign = step.get("assign", "worker")

        if assign == "tool":
            tool_name = step.get("tool_name", "buffer_write")
            flush_to = step.get("flush_to")

            section(f"TOOL STEP {step_num}: {tool_name}" + (f" -> {flush_to}" if flush_to else ""))

            # Find last real content
            last_content = None
            for r in reversed(results):
                if not r.startswith("[") :
                    last_content = r
                    break

            if last_content:
                buffer_writer.append(last_content)
                ilog.add("TOOL", "buffer_write", f"Step {step_num}", f"Buffered {len(last_content)} chars")

                if flush_to:
                    flush_result = buffer_writer.flush(flush_to)
                    print(Fore.GREEN + f"  [OK] {flush_result}" + Style.RESET_ALL)
                    ilog.add("TOOL", "file_flush", flush_to, flush_result)
                    files_written.append(flush_to)
                    results.append(f"[Flushed -> {flush_to}]")
                else:
                    print(Fore.GREEN + f"  [OK] Buffered {len(last_content)} chars" + Style.RESET_ALL)
                    results.append(f"[Buffered step {step_num}]")
            else:
                print(Fore.YELLOW + f"  [WARN] No content to buffer" + Style.RESET_ALL)
                results.append(f"[Tool: {tool_name} (empty)]")
            continue

        shared_ctx = step.get("shared_context", "")
        ctx_label = f" [ctx: {shared_ctx[:40]}]" if shared_ctx else " [isolated]"
        section(f"WORKER STEP {step_num}/{len(steps)}: {task[:50]}{ctx_label}")
        ilog.add("WORKER", f"step_{step_num}_start", task, f"shared_context: {shared_ctx or '(none)'}")

        try:
            # ISOLATED: only system prompt + task + optional one-line shared_context
            result = worker.generate(task, shared_ctx)
        except Exception as e:
            print(Fore.RED + f"  [FAIL] Worker error: {e}" + Style.RESET_ALL)
            ilog.add("WORKER", f"step_{step_num}_error", task, str(e))
            return False

        results.append(result)

        preview = result[:200].replace("\n", "\n    ")
        print(Fore.CYAN + f"  Output ({len(result)} chars):" + Style.RESET_ALL)
        print(Fore.CYAN + f"    {preview}" + Style.RESET_ALL)
        if len(result) > 200:
            print(Fore.CYAN + f"    ... ({len(result) - 200} more chars)" + Style.RESET_ALL)
        ilog.add("WORKER", f"step_{step_num}_done", task, result)

    # --- FINAL FILE OUTPUT (single-file mode) ---
    filepath = plan.get("output_filepath")
    if filepath and buffer_writer._buffer:
        section(f"FINAL FILE WRITE: {filepath}")
        flush_result = buffer_writer.flush(filepath)
        print(Fore.GREEN + f"  {flush_result}" + Style.RESET_ALL)
        ilog.add("TOOL", "final_file_flush", filepath, flush_result)
        files_written.append(filepath)

    # --- SUMMARY ---
    print(Fore.GREEN + f"\n  [OK] Test '{test_name}' completed!" + Style.RESET_ALL)
    print(Fore.WHITE + f"  Steps executed: {len(steps)}" + Style.RESET_ALL)
    print(Fore.WHITE + f"  Worker generations: {worker_steps}" + Style.RESET_ALL)
    if files_written:
        print(Fore.WHITE + f"  Files written: {', '.join(files_written)}" + Style.RESET_ALL)

    return True


# ==========================================================
# TEST: TENACITY RETRY
# ==========================================================
def test_retry(ilog: InteractionLog):
    """Stress test: 20 rapid Brain calls to trigger Gemini's 15 RPM limit."""
    BURST_COUNT = 20
    header(f"TEST: Tenacity Retry (Burst {BURST_COUNT} calls)")
    print(Fore.WHITE + f"  Firing {BURST_COUNT} rapid Brain calls to exceed 15 RPM..." + Style.RESET_ALL)

    brain = Brain()
    base_prompt = "What is {n}+{n}? Reply as JSON with a single worker step."

    retry_observed = False
    results_ok = 0
    errors_429 = 0
    errors_other = 0

    for i in range(BURST_COUNT):
        prompt = base_prompt.format(n=i + 1)
        call_num = i + 1

        start = time.time()
        try:
            result = brain.plan(prompt)
            elapsed = time.time() - start

            # If any call takes >3s after first few, retry backoff kicked in
            if elapsed > 3.0:
                retry_observed = True
                print(Fore.YELLOW + f"  Call {call_num}/{BURST_COUNT}: {elapsed:.1f}s (RETRY backoff!)" + Style.RESET_ALL)
                ilog.add("RETRY_TEST", f"call_{call_num}_slow", prompt, f"Elapsed: {elapsed:.1f}s (retry)")
            else:
                print(Fore.GREEN + f"  Call {call_num}/{BURST_COUNT}: {elapsed:.1f}s [OK]" + Style.RESET_ALL)

            results_ok += 1
            ilog.add("RETRY_TEST", f"call_{call_num}_ok", prompt, json.dumps(result))

        except Exception as e:
            elapsed = time.time() - start
            error_str = str(e)
            is_429 = any(x in error_str.lower() for x in ["429", "rate limit", "quota", "exhausted"])

            if is_429:
                retry_observed = True
                errors_429 += 1
                print(Fore.YELLOW + f"  Call {call_num}/{BURST_COUNT}: [429] Rate limited ({elapsed:.1f}s)" + Style.RESET_ALL)
                ilog.add("RETRY_TEST", f"call_{call_num}_429", prompt, error_str[:200])
            else:
                errors_other += 1
                print(Fore.RED + f"  Call {call_num}/{BURST_COUNT}: [ERR] {error_str[:80]}" + Style.RESET_ALL)
                ilog.add("RETRY_TEST", f"call_{call_num}_error", prompt, error_str[:200])

    # --- Summary ---
    print()
    print(Fore.WHITE + f"  Results: {results_ok} OK, {errors_429} rate-limited, {errors_other} other errors" + Style.RESET_ALL)
    if retry_observed:
        print(Fore.GREEN + "  [CONFIRMED] Tenacity retry mechanism fired!" + Style.RESET_ALL)
    else:
        print(Fore.WHITE + "  [INFO] No rate limit hit. API had enough capacity for the burst." + Style.RESET_ALL)

    return results_ok > 0


# ==========================================================
# MAIN
# ==========================================================
def main():
    header("BRAIN-WORKER WORKFLOW TEST v2")
    print(Fore.WHITE + "  Tests: multi-step routing, multi-file, retry, full logging" + Style.RESET_ALL)

    ilog = InteractionLog()
    results = {}

    # --- Test 1: Simple single-step (baseline) ---
    results["1_simple_chat"] = run_test(
        "Simple Chat (1 step)",
        "Explain what a Python decorator is in 3 sentences.",
        ilog,
    )

    # --- Test 2: Single file with buffer (2 steps: worker + buffer + flush) ---
    results["2_single_file"] = run_test(
        "Single File Output (worker + buffer + flush)",
        "Write a Python function called fibonacci that takes n and returns the nth fibonacci number. Save it to agent_output/fibonacci.py",
        ilog,
    )

    # --- Test 3: MULTI-STEP MULTI-FILE (the real stress test) ---
    results["3_multi_file"] = run_test(
        "Multi-Step Multi-File (3 worker steps, 3 files)",
        (
            "Write a Python web scraper project with 3 separate files: "
            "1) agent_output/config.py - a Config class with base_url, timeout, headers settings. "
            "2) agent_output/scraper.py - a Scraper class that uses the config to fetch and parse HTML. "
            "3) agent_output/main.py - a main runner that creates a Scraper and runs it. "
            "Save each to its own file."
        ),
        ilog,
    )

    # --- Test 4: Retry resilience ---
    results["4_retry_test"] = test_retry(ilog)

    # --- Save logs ---
    header("SAVING INTERACTION LOG")
    log_dir = Path(__file__).parent / "logs"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    txt_path, json_path = ilog.save(str(log_dir / f"brain_worker_v2_{timestamp}.json"))

    print(Fore.GREEN + f"  Text log:  {txt_path}" + Style.RESET_ALL)
    print(Fore.GREEN + f"  JSON log:  {json_path}" + Style.RESET_ALL)

    # --- Summary ---
    header("TEST SUMMARY")
    for name, passed in results.items():
        status = Fore.GREEN + "[PASS]" if passed else Fore.RED + "[FAIL]"
        print(f"  {status} {name}{Style.RESET_ALL}")

    total = len(results)
    passed = sum(1 for v in results.values() if v)
    print(f"\n  {passed}/{total} tests passed")
    print(Fore.WHITE + f"  Full log: {log_dir}/" + Style.RESET_ALL)


if __name__ == "__main__":
    main()
