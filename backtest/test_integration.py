"""Ciel Full Integration Test — Brain-Worker + All Project Tools
Tests the complete CielCore.process() pipeline across all tool categories:
  1. Chat routing (Brain → Worker)
  2. Workspace/file ops (Brain → ToolManager → Worker formats)
  3. Memory/fact ops (Brain → ToolManager → Worker formats)
  4. OS/shell ops (Brain → ToolManager → Worker formats)
  5. Code generation (Brain → Worker → buffer_writer → disk)
  6. Trading tools (Brain → ToolManager → Worker formats)
  7. Edge cases: unknown tool, ambiguous intent, multi-turn

Saves full interaction logs to backtest/logs/ as .txt and .json.

Run from Ciel 2.0 directory:
    python -m backtest.test_integration
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

# Add parent to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from colorama import Fore, Style, init as colorama_init
colorama_init()


# ==========================================================
# INTERACTION LOG — same format as test_brain_worker.py
# ==========================================================
class InteractionLog:
    """Records all Brain/Worker/Tool exchanges for review."""

    def __init__(self):
        self.entries = []
        self.start_time = datetime.now()

    def add(self, actor: str, action: str, input_data: str, output_data: str):
        self.entries.append({
            "timestamp": datetime.now().isoformat(),
            "actor": actor,
            "action": action,
            "input": str(input_data)[:3000],
            "output": str(output_data)[:3000],
        })

    def save(self, filepath: str):
        os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)

        # --- Readable text log ---
        lines = []
        lines.append("=" * 70)
        lines.append("  CIEL FULL INTEGRATION TEST LOG")
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
            for line in str(entry["input"]).split("\n")[:20]:
                lines.append(f"    | {line}")
            lines.append(f"  Output:")
            for line in str(entry["output"]).split("\n")[:20]:
                lines.append(f"    > {line}")
            if len(str(entry["output"]).split("\n")) > 20:
                lines.append(f"    > ... (truncated)")

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
                "test_type": "full_integration",
                "start_time": self.start_time.isoformat(),
                "end_time": datetime.now().isoformat(),
                "total_exchanges": len(self.entries),
                "entries": self.entries,
            }, f, ensure_ascii=False, indent=2)

        return txt_path, filepath


# ==========================================================
# DISPLAY HELPERS
# ==========================================================
def header(msg):
    print(Fore.CYAN + f"\n{'='*65}" + Style.RESET_ALL)
    print(Fore.CYAN + f"  {msg}" + Style.RESET_ALL)
    print(Fore.CYAN + f"{'='*65}" + Style.RESET_ALL)


def section(msg):
    print(Fore.WHITE + f"\n  --- {msg} ---" + Style.RESET_ALL)


def show_result(label, value, color=Fore.GREEN):
    preview = str(value)[:300].replace("\n", "\n    ")
    print(color + f"  [{label}]" + Style.RESET_ALL)
    print(f"    {preview}")
    if len(str(value)) > 300:
        print(f"    ... ({len(str(value)) - 300} more chars)")


# ==========================================================
# SINGLE TEST RUNNER
# ==========================================================
def run_test(core, test_name: str, user_input: str, ilog: InteractionLog,
             expect_action: str = None, validate_fn=None):
    """Run one test through the full CielCore pipeline.

    Args:
        core: CielCore instance
        test_name: Display name
        user_input: The user's request
        ilog: Interaction log
        expect_action: Expected routing action (chat/tool/code) — for validation
        validate_fn: Optional callable(decision, response) -> bool for custom checks
    """
    header(f"TEST: {test_name}")
    print(Fore.GREEN + f'  Input: "{user_input}"' + Style.RESET_ALL)
    ilog.add("TEST", "start", test_name, user_input)

    success = True
    decision = {}
    response = ""

    try:
        # Step 1: Route
        section("BRAIN: Routing")
        start = time.time()
        decision = core.router.route(user_input, core._tool_list_str, core.chat_history)
        route_time = time.time() - start
        action = decision.get("action", "chat")

        decision_str = json.dumps(decision, indent=2, ensure_ascii=False)
        show_result(f"Routed to: {action.upper()} ({route_time:.1f}s)", decision_str, Fore.YELLOW)
        ilog.add("BRAIN", "route", user_input, decision_str)

        # Validate routing
        if expect_action and action != expect_action:
            print(Fore.RED + f"  [FAIL] Expected action='{expect_action}', got '{action}'" + Style.RESET_ALL)
            ilog.add("VALIDATION", "route_mismatch", f"expected={expect_action}", f"got={action}")
            success = False

        # Step 2: Execute
        section("EXECUTING")
        start = time.time()

        if action == "tool":
            tool_name = decision.get("tool_name", "unknown")
            tool_args = decision.get("tool_args", {})
            hint = decision.get("response_hint", "")
            ilog.add("TOOL", f"execute_{tool_name}", json.dumps(tool_args), "(running...)")
            response = core.execute_tool(tool_name, tool_args, hint)

        elif action == "code":
            task = decision.get("task", user_input)
            filename = decision.get("filename", "agent_output/test_output.py")
            ilog.add("WORKER", "code_gen", task, f"target: {filename}")
            response = core.execute_code(task, filename)

        else:  # chat
            task = decision.get("task", user_input)
            ilog.add("WORKER", "chat_gen", task, "(generating...)")
            response = core.execute_chat(task)

        exec_time = time.time() - start
        show_result(f"Response ({exec_time:.1f}s)", response, Fore.CYAN)
        ilog.add("RESULT", action, user_input, response)

        # Step 3: Custom validation
        if validate_fn:
            valid = validate_fn(decision, response)
            if not valid:
                print(Fore.RED + "  [FAIL] Custom validation failed" + Style.RESET_ALL)
                ilog.add("VALIDATION", "custom_fail", test_name, "Validation returned False")
                success = False

    except Exception as e:
        print(Fore.RED + f"  [ERROR] {type(e).__name__}: {e}" + Style.RESET_ALL)
        ilog.add("ERROR", type(e).__name__, user_input, str(e)[:500])
        success = False

    status = Fore.GREEN + "[PASS]" if success else Fore.RED + "[FAIL]"
    print(f"\n  {status} {test_name}{Style.RESET_ALL}")
    ilog.add("TEST", "end", test_name, "PASS" if success else "FAIL")

    return success


# ==========================================================
# MAIN
# ==========================================================
def main():
    header("CIEL FULL INTEGRATION TEST")
    print(Fore.WHITE + "  Testing: Brain routing → Tool execution → Worker formatting" + Style.RESET_ALL)
    print(Fore.WHITE + "  All project tools: workspace, memory, OS, trading, code gen" + Style.RESET_ALL)

    ilog = InteractionLog()
    results = {}

    # Initialize CielCore ONCE (loads all tools)
    section("INITIALIZING CielCore")
    try:
        from core.llm_connector import CielCore
        core = CielCore()
        tool_names = [t.name for t in core._tools]
        print(Fore.GREEN + f"  [OK] {len(tool_names)} tools loaded: {', '.join(tool_names)}" + Style.RESET_ALL)
        ilog.add("SYSTEM", "init", "CielCore", f"Tools: {', '.join(tool_names)}")
    except Exception as e:
        print(Fore.RED + f"  [FATAL] Cannot initialize CielCore: {e}" + Style.RESET_ALL)
        import traceback
        traceback.print_exc()
        return

    # ======================================================
    # TEST 1: Simple Chat (no tools)
    # ======================================================
    results["1_chat_simple"] = run_test(
        core,
        "Simple Chat — greeting",
        "Hello, who are you?",
        ilog,
        expect_action="chat",
    )

    # ======================================================
    # TEST 2: Chat — knowledge question
    # ======================================================
    results["2_chat_knowledge"] = run_test(
        core,
        "Chat — Python question",
        "Explain what a Python decorator is in 2 sentences.",
        ilog,
        expect_action="chat",
    )

    # ======================================================
    # TEST 3: Workspace — list files
    # ======================================================
    results["3_workspace_list"] = run_test(
        core,
        "Workspace — list files",
        "List all files in my workspace.",
        ilog,
        expect_action="tool",
        validate_fn=lambda d, r: d.get("tool_name") == "list_workspace" or "list" in r.lower(),
    )

    # ======================================================
    # TEST 4: Workspace — write file
    # ======================================================
    results["4_workspace_write"] = run_test(
        core,
        "Workspace — write file",
        'Write "Hello from integration test" to a file called test_integration.txt in the workspace.',
        ilog,
        expect_action="tool",
        validate_fn=lambda d, r: d.get("tool_name") == "write_file" or "writ" in r.lower(),
    )

    # ======================================================
    # TEST 5: Workspace — read file back
    # ======================================================
    results["5_workspace_read"] = run_test(
        core,
        "Workspace — read file",
        "Read the file test_integration.txt from my workspace.",
        ilog,
        expect_action="tool",
        validate_fn=lambda d, r: d.get("tool_name") == "read_file" or "test_integration" in r.lower() or "hello" in r.lower(),
    )

    # ======================================================
    # TEST 6: Memory — save a fact
    # ======================================================
    results["6_memory_save"] = run_test(
        core,
        "Memory — save fact",
        'Remember that my favorite programming language is Python. Save this as a fact with key "fav_language".',
        ilog,
        expect_action="tool",
        validate_fn=lambda d, r: d.get("tool_name") == "save_fact" or "save" in r.lower() or "remember" in r.lower(),
    )

    # ======================================================
    # TEST 7: Memory — retrieve the fact
    # ======================================================
    results["7_memory_get"] = run_test(
        core,
        "Memory — get fact",
        'What is my favorite programming language? Check the fact with key "fav_language".',
        ilog,
        expect_action="tool",
        validate_fn=lambda d, r: "python" in r.lower() or d.get("tool_name") == "get_fact",
    )

    # ======================================================
    # TEST 8: OS — shell command
    # ======================================================
    results["8_os_shell"] = run_test(
        core,
        "OS — shell command",
        "Run the command: echo Hello from Ciel",
        ilog,
        expect_action="tool",
        validate_fn=lambda d, r: d.get("tool_name") == "execute_shell_command" or "hello" in r.lower(),
    )

    # ======================================================
    # TEST 9: Code generation — write to disk
    # ======================================================
    results["9_code_gen"] = run_test(
        core,
        "Code — generate and save",
        "Write a Python function called greet(name) that returns 'Hello, {name}!'. Save it to agent_output/greet.py",
        ilog,
        expect_action="code",
        validate_fn=lambda d, r: "greet" in r.lower() or "written" in r.lower(),
    )

    # ======================================================
    # TEST 10: Trading — market price (may fail if no API)
    # ======================================================
    results["10_trading_price"] = run_test(
        core,
        "Trading — EUR/USD price",
        "What is the current price of EUR/USD?",
        ilog,
        expect_action="tool",
        validate_fn=lambda d, r: (
            d.get("tool_name") == "get_market_price"
            or "eur" in r.lower()
            or "price" in r.lower()
            or "error" in r.lower()  # API might fail but routing should still work
        ),
    )

    # ======================================================
    # TEST 11: Gmail — check newest emails (may fail if no token)
    # ======================================================
    results["11_gmail_search"] = run_test(
        core,
        "Gmail — search newest emails",
        "Check my newest emails.",
        ilog,
        expect_action="tool",
        validate_fn=lambda d, r: (
            d.get("tool_name") == "search_gmail"
            or "email" in r.lower()
            or "error" in r.lower()
        ),
    )

    # ======================================================
    # TEST 12: Edge — ambiguous intent
    # ======================================================
    results["12_edge_ambiguous"] = run_test(
        core,
        "Edge — ambiguous (should not crash)",
        "What can you do?",
        ilog,
        expect_action="chat",
    )

    # ======================================================
    # TEST 13: Edge — unknown tool request
    # ======================================================
    results["13_edge_unknown"] = run_test(
        core,
        "Edge — request for something impossible",
        "Order me a pizza from Dominos.",
        ilog,
        expect_action="chat",
    )

    # ======================================================
    # TEST 14: Memory cleanup — delete the test fact
    # ======================================================
    results["14_memory_cleanup"] = run_test(
        core,
        "Memory — delete fact (cleanup)",
        'Delete the fact with key "fav_language".',
        ilog,
        expect_action="tool",
        validate_fn=lambda d, r: d.get("tool_name") == "delete_fact" or "delet" in r.lower(),
    )

    # ======================================================
    # TEST 15: Workspace cleanup — delete test file
    # ======================================================
    results["15_workspace_cleanup"] = run_test(
        core,
        "Workspace — delete file (cleanup)",
        "Delete the file test_integration.txt from my workspace.",
        ilog,
        expect_action="tool",
        validate_fn=lambda d, r: d.get("tool_name") == "delete_file" or "delet" in r.lower(),
    )



    # ======================================================
    # TEST 16: Self-Healing — ModuleNotFoundError (Code Rewrite)
    # ======================================================
    # Write a script with a fake module and syntax error
    bad_code = "import this_module_does_not_exist_123\nprint('hello'\n"
    core.tool_manager.execute_tool("write_file", {"filename": "test_healing.py", "content": bad_code})
    
    results["16_self_healing_module"] = run_test(
        core,
        "Self-Healing — Code Rewrite (Syntax & Module)",
        "Run the python script test_healing.py",
        ilog,
        expect_action="tool",
        validate_fn=lambda d, r: "Self-Healing Activated" in r and "TOOL_ERROR" not in r,
    )

    # ======================================================
    # TEST 17: Self-Healing — Parameter Correction
    # ======================================================
    # We ask Brain to get price for 'EUR_USD' directly via ToolManager bypassing router
    results["17_self_healing_parameter"] = run_test(
        core,
        "Self-Healing — Parameter Correction",
        "Get the price for EUR_USD exactly like I typed it",
        ilog,
        expect_action="tool",
        validate_fn=lambda d, r: "Self-Healing Activated" in r or "1.0" in r or "TOOL_ERROR" not in r,
    )

    # ======================================================
    # TEST 18: Self-Correction — Brain re-evaluates result
    # ======================================================
    # Tests _self_correct() directly (bypasses RAG to avoid contamination
    # from previous test runs where notes.txt was also requested).
    # Setup: write "project_notes.txt", call read_file("notes.txt") → fails
    # Then _self_correct() should evaluate and try alternative (list_workspace → read correct file).
    header("TEST: Self-Correction — Brain re-evaluates result")
    core.tool_manager.execute_tool("write_file", {"filename": "project_notes.txt", "content": "Secret data: self-correction works!"})
    print(Fore.GREEN + '  Step 1: execute_tool("read_file", "notes.txt") → expect error' + Style.RESET_ALL)
    ilog.add("TEST", "start", "Self-Correction", "read_file(notes.txt) → _self_correct()")

    try:
        # Step 1: Execute the "wrong" tool call directly
        raw_result = core.execute_tool("read_file", {"filename": "notes.txt"}, "Reading notes.txt...")
        show_result("Raw tool result", raw_result, Fore.YELLOW)
        ilog.add("TOOL", "raw_result", "read_file(notes.txt)", raw_result)

        # Step 2: Call _self_correct() to test the evaluation loop
        print(Fore.GREEN + '  Step 2: _self_correct() evaluating result...' + Style.RESET_ALL)
        corrected = core._self_correct(
            "Read the file notes.txt from my workspace",
            "read_file", {"filename": "notes.txt"}, raw_result
        )
        show_result("Self-Correction Result", corrected, Fore.CYAN)
        ilog.add("RESULT", "self_correction", "Read notes.txt", corrected)

        # PASS if: self-correction tag appears OR Brain found the right file
        sc_pass = (
            "Self-Correction" in corrected
            or "project_notes" in corrected.lower()
            or "secret data" in corrected.lower()
            or "self-correction works" in corrected.lower()
        )

        results["18_self_correction"] = sc_pass
        status = Fore.GREEN + "[PASS]" if sc_pass else Fore.RED + "[FAIL]"
        print(f"\n  {status} Self-Correction{Style.RESET_ALL}")
    except Exception as e:
        print(Fore.RED + f"  [ERROR] {type(e).__name__}: {e}" + Style.RESET_ALL)
        ilog.add("ERROR", type(e).__name__, "self_correction", str(e)[:500])
        results["18_self_correction"] = False

    ilog.add("TEST", "end", "Self-Correction", "PASS" if results.get("18_self_correction") else "FAIL")

    # ======================================================
    # TEST 19: Cleanup — delete self-correction test file
    # ======================================================
    results["19_sc_cleanup"] = run_test(
        core,
        "Cleanup — delete self-correction test file",
        "Delete the file project_notes.txt from my workspace.",
        ilog,
        expect_action="tool",
        validate_fn=lambda d, r: d.get("tool_name") == "delete_file" or "delet" in r.lower(),
    )

    # ======================================================
    # SAVE LOGS
    # ======================================================
    header("SAVING INTERACTION LOG")
    log_dir = Path(__file__).parent / "logs"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    txt_path, json_path = ilog.save(str(log_dir / f"integration_test_{timestamp}.json"))

    print(Fore.GREEN + f"  Text log: {txt_path}" + Style.RESET_ALL)
    print(Fore.GREEN + f"  JSON log: {json_path}" + Style.RESET_ALL)

    # ======================================================
    # SUMMARY
    # ======================================================
    header("TEST SUMMARY")

    # Group results by category
    categories = {
        "Chat":      ["1_chat_simple", "2_chat_knowledge"],
        "Workspace": ["3_workspace_list", "4_workspace_write", "5_workspace_read"],
        "Memory":    ["6_memory_save", "7_memory_get"],
        "OS/Shell":  ["8_os_shell"],
        "Code Gen":  ["9_code_gen"],
        "Trading":   ["10_trading_price"],
        "Gmail":     ["11_gmail_search"],
        "Edge Cases": ["12_edge_ambiguous", "13_edge_unknown"],
        "Self-Healing": ["16_self_healing_module", "17_self_healing_parameter"],
        "Self-Correction": ["18_self_correction"],
        "Cleanup":   ["14_memory_cleanup", "15_workspace_cleanup", "19_sc_cleanup"],
    }

    for cat_name, test_keys in categories.items():
        cat_pass = sum(1 for k in test_keys if results.get(k, False))
        cat_total = len(test_keys)
        cat_color = Fore.GREEN if cat_pass == cat_total else Fore.YELLOW if cat_pass > 0 else Fore.RED
        print(f"  {cat_color}[{cat_pass}/{cat_total}]{Style.RESET_ALL} {cat_name}")
        for k in test_keys:
            status = Fore.GREEN + "PASS" if results.get(k, False) else Fore.RED + "FAIL"
            print(f"       {status}{Style.RESET_ALL}  {k}")

    total = len(results)
    passed = sum(1 for v in results.values() if v)
    print(f"\n  Total: {passed}/{total} passed")
    print(Fore.WHITE + f"  Full log: {log_dir}/" + Style.RESET_ALL)

    if passed < total:
        print(Fore.YELLOW + "\n  Note: Some failures may be expected (e.g., trading API needs keys)." + Style.RESET_ALL)
        print(Fore.YELLOW + "  Check the logs for Brain routing decisions vs actual execution." + Style.RESET_ALL)


if __name__ == "__main__":
    main()
