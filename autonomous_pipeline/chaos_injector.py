"""
Chaos Injector — synthetic adversarial scenarios for Worker fine-tuning.

Writes hand-crafted [USER][REQUEST] + [BRAIN][ROUTE_DECISION] +
[BRAIN][CHAIN_OF_THOUGHT] + [TOOL][RESULT] + [WORKER][FORMAT_RESPONSE]
sequences directly into ciel_data/logs/thoughts.log in the exact format
Ciel's runtime emits. The existing data_pipeline parser picks them up and
routes them through the Judge unchanged.

Purpose: force the dataset to cover edge cases the live pipeline rarely
produces — tool timeouts, missing files, API errors, empty results — so the
Worker learns to acknowledge unavailability instead of fabricating data.

Scenarios are rotated sequentially (not random) via state.chaos_scenario_index
so every scenario is exercised once per full pass before any repeats.

Side effects: appends to thoughts.log and updates state.json. No Gemini API
calls, no Ciel core imports, no network activity.
"""
import datetime
import json
from pathlib import Path

from autonomous_pipeline.data_pipeline import load_state, save_state

BASE_DIR = Path(__file__).resolve().parent.parent
LOG_PATH = BASE_DIR / "ciel_data" / "logs" / "thoughts.log"
SEPARATOR = "-" * 60


# Library of adversarial scenarios. Each entry models a realistic failure mode
# paired with the Worker response we want the model to learn.
#
# Schema:
#   user_request:    what the Master typed
#   tool_name:       routed tool (must match a real tool name so Judge applies
#                    the right rubric — non-code tools require "Master" persona)
#   tool_args:       arguments shown in BRAIN ROUTE_DECISION (cosmetic; helps
#                    the Judge see plausible context)
#   tool_result:     raw output the tool returned — the "chaos" payload
#   ideal_response:  hand-crafted Worker response that handles it correctly
#                    (concise, addresses Master, acknowledges unavailability
#                    rather than inventing data)
CHAOS_SCENARIOS = [
    # ─── File system errors ───────────────────────────────────────────────
    {
        "user_request": "Read the contents of quarterly_report_q4.txt",
        "tool_name": "read_file",
        "tool_args": {"filename": "quarterly_report_q4.txt"},
        "tool_result": "read_file: Error: File 'quarterly_report_q4.txt' not found in workspace.",
        "ideal_response": "Master, the file 'quarterly_report_q4.txt' does not exist in the workspace.",
    },
    {
        "user_request": "Delete temp_backup.zip from my workspace.",
        "tool_name": "delete_file",
        "tool_args": {"filename": "temp_backup.zip"},
        "tool_result": "delete_file: Error: Permission denied. File is locked by another process.",
        "ideal_response": "Master, deletion failed — 'temp_backup.zip' is locked by another process.",
    },
    {
        "user_request": "Get info on the file system_log.txt",
        "tool_name": "get_file_info",
        "tool_args": {"filename": "system_log.txt"},
        "tool_result": "get_file_info: Error: Path does not exist.",
        "ideal_response": "Master, 'system_log.txt' does not exist in the workspace.",
    },
    {
        "user_request": "List everything in my workspace folder",
        "tool_name": "list_workspace",
        "tool_args": {},
        "tool_result": "list_workspace: Danh sách Vùng Cách Ly:\n(workspace is empty)",
        "ideal_response": "Master, the workspace is currently empty.",
    },
    {
        "user_request": "Write the daily summary into notes/today.md",
        "tool_name": "write_file",
        "tool_args": {"filename": "notes/today.md", "content": "..."},
        "tool_result": "write_file: Error: Directory 'notes/' does not exist and could not be created.",
        "ideal_response": "Master, write failed — the directory 'notes/' does not exist.",
    },

    # ─── Network / API errors ─────────────────────────────────────────────
    {
        "user_request": "What is the current price of XAU/USD?",
        "tool_name": "get_market_price",
        "tool_args": {"symbol": "XAU/USD"},
        "tool_result": "get_market_price: Error: Connection timeout after 30 seconds. Provider API unreachable.",
        "ideal_response": "Master, the market data provider is unreachable — XAU/USD price unavailable.",
    },
    {
        "user_request": "Show me Bitcoin's 24h stats",
        "tool_name": "get_crypto_stats",
        "tool_args": {"symbol": "BTC"},
        "tool_result": "get_crypto_stats: Error: HTTP 429 - Rate limit exceeded. Retry after 60 seconds.",
        "ideal_response": "Master, the crypto API rate limit was hit — please retry in 60 seconds.",
    },
    {
        "user_request": "Run a technical analysis on ETH",
        "tool_name": "analyze_crypto_technical",
        "tool_args": {"symbol": "ETH"},
        "tool_result": "analyze_crypto_technical: Error: HTTP 503 - Service temporarily unavailable.",
        "ideal_response": "Master, the technical analysis service is temporarily unavailable.",
    },
    {
        "user_request": "Check the EUR/JPY price",
        "tool_name": "get_market_price",
        "tool_args": {"symbol": "EUR/JPY"},
        "tool_result": "get_market_price: Error: Symbol 'EUR/JPY' not supported by current data provider.",
        "ideal_response": "Master, EUR/JPY is not supported by the current data provider.",
    },

    # ─── Empty / no-result responses ──────────────────────────────────────
    {
        "user_request": "Find emails from notifications@github.com this week",
        "tool_name": "search_gmail",
        "tool_args": {"query": "from:notifications@github.com newer_than:7d"},
        "tool_result": "search_gmail: []",
        "ideal_response": "Master, no emails from notifications@github.com were found this week.",
    },
    {
        "user_request": "Search my inbox for invoices from Stripe",
        "tool_name": "search_gmail",
        "tool_args": {"query": "from:stripe invoice"},
        "tool_result": "search_gmail: []",
        "ideal_response": "Master, no Stripe invoice emails were found in the inbox.",
    },
    {
        "user_request": "What fact do you have stored under 'api_key_rotation_schedule'?",
        "tool_name": "get_fact",
        "tool_args": {"key": "api_key_rotation_schedule"},
        "tool_result": "get_fact: Error: No fact stored under key 'api_key_rotation_schedule'.",
        "ideal_response": "Master, no fact is stored under 'api_key_rotation_schedule'.",
    },

    # ─── OS / shell failures ──────────────────────────────────────────────
    {
        "user_request": "Take a screenshot of the current display",
        "tool_name": "take_screenshot",
        "tool_args": {},
        "tool_result": "take_screenshot: Error: No display detected. Headless environment.",
        "ideal_response": "Master, screenshot failed — no display is attached to this environment.",
    },
    {
        "user_request": "Open the Notepad application",
        "tool_name": "open_application",
        "tool_args": {"name": "notepad"},
        "tool_result": "open_application: Error: Application 'notepad' not found on PATH.",
        "ideal_response": "Master, 'notepad' could not be opened — application not found on PATH.",
    },
    {
        "user_request": "Run 'git status' in my home directory",
        "tool_name": "execute_shell_command",
        "tool_args": {"command": "git status"},
        "tool_result": "execute_shell_command: Error: fatal: not a git repository (or any of the parent directories): .git",
        "ideal_response": "Master, that directory is not a git repository.",
    },

    # ─── Vision tool failures ─────────────────────────────────────────────
    {
        "user_request": "Describe what you see on screen right now",
        "tool_name": "vision_describe",
        "tool_args": {},
        "tool_result": "vision_describe: Error: Screen capture returned blank/black image. No content to analyze.",
        "ideal_response": "Master, the screen capture is blank — nothing to describe.",
    },
    {
        "user_request": "Click the 'Submit' button on screen",
        "tool_name": "vision_act",
        "tool_args": {"target": "Submit button"},
        "tool_result": "vision_act: Error: Target 'Submit button' not detected on current screen.",
        "ideal_response": "Master, no 'Submit' button is visible on the current screen.",
    },

    # ─── Git failures ─────────────────────────────────────────────────────
    {
        "user_request": "Commit and push my changes with message 'fix typo'",
        "tool_name": "git_commit_and_push",
        "tool_args": {"message": "fix typo"},
        "tool_result": "git_commit_and_push: Error: nothing to commit, working tree clean.",
        "ideal_response": "Master, nothing to commit — the working tree is clean.",
    },
    {
        "user_request": "Show me the git diff",
        "tool_name": "git_diff",
        "tool_args": {},
        "tool_result": "git_diff: (empty diff — no changes detected)",
        "ideal_response": "Master, there are no changes — the diff is empty.",
    },

    # ─── Mail tool failures ───────────────────────────────────────────────
    {
        "user_request": "Send a reply to the latest email from boss@company.com",
        "tool_name": "reply_to_email",
        "tool_args": {"to": "boss@company.com"},
        "tool_result": "reply_to_email: Error: No prior message from 'boss@company.com' found in thread context.",
        "ideal_response": "Master, no prior email from boss@company.com was found to reply to.",
    },
    {
        "user_request": "Trash the email with subject 'Weekly newsletter'",
        "tool_name": "trash_email",
        "tool_args": {"subject": "Weekly newsletter"},
        "tool_result": "trash_email: Error: No email matching subject 'Weekly newsletter' found.",
        "ideal_response": "Master, no email matching 'Weekly newsletter' was found to trash.",
    },

    # ─── Self-healing exhausted ───────────────────────────────────────────
    {
        "user_request": "Run my data_export.py script",
        "tool_name": "run_python_script",
        "tool_args": {"filename": "data_export.py"},
        "tool_result": "run_python_script: [Self-Healing Exhausted] All 3 repair attempts failed. Final error: ModuleNotFoundError: No module named 'pandas'.",
        "ideal_response": "Master, the script failed — 'pandas' is not installed and self-healing could not recover.",
    },
    {
        "user_request": "Execute the backup routine in cleanup.py",
        "tool_name": "run_python_script",
        "tool_args": {"filename": "cleanup.py"},
        "tool_result": "run_python_script: Error: File 'cleanup.py' not found in workspace.",
        "ideal_response": "Master, 'cleanup.py' does not exist in the workspace.",
    },
]


def _format_log_entry(timestamp_str, actor, action, content):
    """Render one structured entry in the exact format Ciel's logger emits."""
    return f"[{timestamp_str}] [{actor}] [{action}]\n{content}\n{SEPARATOR}\n"


def _build_route_decision(tool_name, tool_args):
    """Construct a plausible BRAIN ROUTE_DECISION block so the parser extracts the right tool_name."""
    route = {
        "hidden_thought": {
            "observation": f"User requested a task requiring the '{tool_name}' tool.",
            "reasoning": f"Routed to '{tool_name}' as the most direct match for the request.",
            "risk": "none",
        },
        "action": "tool",
        "tool_name": tool_name,
        "tool_args": tool_args,
        "response_hint": "Reporting back to Master.",
    }
    return "```json\n" + json.dumps(route, indent=2, ensure_ascii=False) + "\n```"


def _build_chain_of_thought(tool_name):
    """Construct a BRAIN CHAIN_OF_THOUGHT block matching the real log emoji pattern."""
    return (
        f"🔍 Observation: Routed to {tool_name}\n"
        f"🧠 Reasoning:   Direct tool match for the request.\n"
        f"⚠️ Risk:        none"
    )


def inject_scenario(scenario):
    """Append one chaos scenario as 5 sequential log entries into thoughts.log."""
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    tool_name = scenario["tool_name"]

    blocks = [
        _format_log_entry(ts, "USER", "REQUEST", scenario["user_request"]),
        _format_log_entry(
            ts, "BRAIN", "ROUTE_DECISION",
            _build_route_decision(tool_name, scenario["tool_args"]),
        ),
        _format_log_entry(
            ts, "BRAIN", "CHAIN_OF_THOUGHT",
            _build_chain_of_thought(tool_name),
        ),
        _format_log_entry(ts, "TOOL", "RESULT", scenario["tool_result"]),
        _format_log_entry(ts, "WORKER", "FORMAT_RESPONSE", scenario["ideal_response"]),
    ]

    # Lead and trail with a newline so injected entries are visually separated
    # from preceding/following log content the same way real runtime entries are.
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write("\n" + "\n".join(blocks) + "\n")


def run_chaos_cycle():
    """Inject the next chaos scenario in sequential rotation.

    Reads chaos_scenario_index from state.json, picks that scenario, then
    advances the index (wrapping at len(CHAOS_SCENARIOS)) and persists state.
    Guarantees every scenario is exercised once per full pass before repeats.

    Returns the injected scenario dict (for logging / telemetry).
    """
    state = load_state()
    idx = state.get("chaos_scenario_index", 0) % len(CHAOS_SCENARIOS)
    scenario = CHAOS_SCENARIOS[idx]

    inject_scenario(scenario)

    state["chaos_scenario_index"] = (idx + 1) % len(CHAOS_SCENARIOS)
    save_state(state)

    print(
        f"[CHAOS] Injected scenario #{idx + 1}/{len(CHAOS_SCENARIOS)}: "
        f"{scenario['tool_name']} → \"{scenario['user_request'][:60]}\""
    )
    return scenario


if __name__ == "__main__":
    run_chaos_cycle()
