import os
import sys
import json
import datetime
from pathlib import Path
from dotenv import load_dotenv

# Set up paths so we can run from anywhere (VPS cron job)
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

# Load environment variables
load_dotenv(BASE_DIR / ".env")

from core.agent_loop import AgentLoop
from autonomous_pipeline.data_pipeline import load_state, save_state


# Diverse single-step fallback tasks used when DeepSeek task generation fails.
# Rotated SEQUENTIALLY (not random) via state.fallback_task_index so every entry
# is exercised once per full pass before any repeats — guarantees dataset variety
# even during extended DeepSeek outages. Covers all 21 capability categories.
_FALLBACK_TASKS = [
    # WORKSPACE
    "List all files and folders in ciel_workspace/ recursively and print the full tree to console.",
    "Write 'Daily checkpoint' followed by the current timestamp to ciel_workspace/checkpoint.txt.",
    "Append a timestamped line 'fallback heartbeat' to ciel_workspace/heartbeat.log.",
    "Read the file ciel_workspace/heartbeat.log and print its last line to console.",
    # MEMORY
    "Save a fact with key 'last_health_check' and value set to the current date to the memory vault.",
    "Retrieve the fact with key 'last_health_check' from the memory vault and print its value.",
    "Delete the memory fact with key 'last_health_check' using delete_fact and print 'cleared'.",
    # TRADING
    "Get the current closing price of BTC/USDT and report only the closing price value.",
    "Get the current closing price of ETH/USDT and report only the closing price value.",
    "Get the current XAU/USD gold price and print only the price value to console.",
    "Get 24h stats (price change %, high, low) for ETH and print the result.",
    "Run RSI + SMA technical analysis for BTC on the 1h interval and report the trend signal.",
    # MAIL
    "Check my unread emails and list the subjects and senders of the most recent 5.",
    # GIT
    "Check the git status of the Ciel 2.0 repository and report uncommitted changes.",
    "Run git diff on the repository and report the first meaningful changed lines.",
    # OS / SHELL
    "Execute a shell command to check free disk space and print the result.",
    "Execute a safe shell command to list running processes and print the first 10 lines.",
    "Execute a shell command to print current CPU and memory usage, then report the values.",
    # VISION
    "Take a screenshot of the current screen and describe what is visible.",
    # CODE_GEN
    "Write a Python script to autonomous_pipeline/agent_output/disk_report.py that prints free disk space.",
    "Write a Python script to autonomous_pipeline/agent_output/uptime_report.py that prints system uptime.",
    # SELF_CORRECTION / EDGE
    "Read the file ciel_workspace/daily_summary.txt and print its content (the file may not exist).",
    # CHAT
    "Briefly explain what RAG (retrieval-augmented generation) is in two sentences.",
]


def _pick_fallback_task(recent_prompts):
    """Pick the next fallback task in sequential rotation, skipping entries in recent_prompts.

    Uses state.fallback_task_index for persistent round-robin across cycles.
    If an index lands on a task that's in recent_prompts, advances forward until
    finding a non-recent one — guarantees no immediate repeats while still
    making progress through the pool.
    """
    state = load_state()
    start_idx = state.get("fallback_task_index", 0) % len(_FALLBACK_TASKS)
    recent = {p.strip() for p in (recent_prompts or [])}

    chosen_idx = start_idx
    for offset in range(len(_FALLBACK_TASKS)):
        candidate_idx = (start_idx + offset) % len(_FALLBACK_TASKS)
        if _FALLBACK_TASKS[candidate_idx] not in recent:
            chosen_idx = candidate_idx
            break

    state["fallback_task_index"] = (chosen_idx + 1) % len(_FALLBACK_TASKS)
    save_state(state)
    return _FALLBACK_TASKS[chosen_idx]


# Maximum length of a valid Master-generated task. Real single-scope tasks fit
# easily under 400 chars; anything longer is almost always leaked reasoning
# prose (e.g. "We need to generate exactly ONE task...") that the model emitted
# instead of obeying the OUTPUT RULES.
_MAX_TASK_CHARS = 400

# Phrases that strongly indicate the model returned its chain-of-thought instead
# of the final task string. Checked case-insensitively at the start of the text.
_REASONING_LEAK_PREFIXES = (
    "we need to", "we should", "let me", "let's", "first,", "first ",
    "okay,", "alright,", "i need to", "i'll", "i will",
    "looking at", "analyzing", "to generate", "based on the",
    "given the", "since the", "thinking about", "considering",
)


def _looks_like_task(text):
    """Reject reasoning-style blobs that leaked into the response.

    A real task is a single concise imperative sentence. Multi-line analysis
    prose or anything starting with planning language is the model's internal
    reasoning — which, if passed through to Ciel, crashes the Brain router with
    JSON decode errors (the input is too far out of distribution).
    """
    if not text:
        return False
    if len(text) > _MAX_TASK_CHARS:
        return False
    # Real tasks are one line, occasionally two. Anything with 3+ newlines is
    # multi-paragraph reasoning.
    if text.count("\n") >= 3:
        return False
    lowered = text.lstrip().lower()
    if any(lowered.startswith(p) for p in _REASONING_LEAK_PREFIXES):
        return False
    return True


def log_deepseek_call(actor, action, prompt, response):
    """Log DeepSeek prompt inputs and model responses inside autonomous_pipeline/thoughts.log."""
    log_path = BASE_DIR / "autonomous_pipeline" / "thoughts.log"
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_content = (
        f"\n" + "-" * 60 + "\n"
        f"[{now_str}] [{actor}] [{action}]\n"
        f"Prompt:\n{prompt}\n\n"
        f"Response:\n{response}\n"
        + "-" * 60 + "\n"
    )
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(log_content)
    except Exception as e:
        print(f"[-] Failed to write to autonomous thoughts log: {e}")


def get_recent_user_prompts():
    """Extract the last 3 UNIQUE user requests from memory_bank.json or thoughts.log."""
    prompts = []
    
    # 1. Try reading from memory_bank.json (primary source)
    memory_bank_path = BASE_DIR / "ciel_data" / "memory_bank.json"
    if memory_bank_path.exists():
        try:
            with open(memory_bank_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                human_messages = [msg["content"] for msg in data if msg.get("type") == "human"]
                # Walk backwards to collect last 3 UNIQUE prompts (avoid duplicates
                # from repeated pipeline cycles eating all lookback slots).
                seen = set()
                for msg in reversed(human_messages):
                    normalized = msg.strip().lower()
                    if normalized not in seen:
                        seen.add(normalized)
                        prompts.append(msg)
                    if len(prompts) >= 3:
                        break
                prompts.reverse()  # Restore chronological order
        except Exception as e:
            print(f"[-] Failed to read memory bank: {e}")
            
    # 2. Fallback to thoughts.log if memory bank yields less than 3 prompts
    if len(prompts) < 3:
        log_path = BASE_DIR / "ciel_data" / "logs" / "thoughts.log"
        if log_path.exists():
            try:
                # Read the last 20KB of the log file to parse quickly
                with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                    f.seek(0, os.SEEK_END)
                    size = f.tell()
                    block_size = min(size, 20480)
                    f.seek(size - block_size)
                    content = f.read()
                
                # Split entries by 60 hyphens
                entries = content.split("-" * 60)
                log_prompts = []
                for entry in reversed(entries):
                    if "User's request:" in entry:
                        for line in entry.splitlines():
                            if "User's request:" in line:
                                req = line.replace("User's request:", "").strip()
                                if req and req not in log_prompts:
                                    log_prompts.append(req)
                    if len(log_prompts) >= 3:
                        break
                
                # Merge lists (deduplicated)
                existing = {p.strip().lower() for p in prompts}
                for p in log_prompts:
                    if p.strip().lower() not in existing:
                        existing.add(p.strip().lower())
                        prompts.append(p)
                prompts = prompts[-3:]
            except Exception as e:
                print(f"[-] Failed to parse thoughts.log fallback: {e}")
                
    # If still empty, provide a default starting list
    if not prompts:
        prompts = [
            "Hello Ciel, give me a status update.",
            "Check BTC crypto price.",
            "Show me recent files in the workspace."
        ]
        
    return prompts


def generate_simulated_task(recent_prompts):
    """Call DeepSeek API (Simulated Master) to generate a dynamic, context-aware task based on time schedule."""
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise ValueError("DEEPSEEK_API_KEY is not defined in the environment or .env file.")
        
    import requests
    url = "https://api.deepseek.com/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    # Format the recent history
    history_str = "\n".join([f"- {p[:80]}..." if len(p) > 80 else f"- {p}" 
                          for p in recent_prompts])
    
    now = datetime.datetime.now()
    current_time_str = now.strftime("%Y-%m-%d %H:%M:%S")

    # Simulated Master Instruction - Using RAW multi-line f-string (no markdown)
    system_prompt = f"""You are the Simulated Master, the owner and administrator of the Ciel 2.0 AI agent.
Your goal is to generate exactly ONE realistic, high-value, and actionable request for Ciel to execute right now.

Current VPS Date and Time: {current_time_str}

CIEL'S FULL CAPABILITY MAP — you must rotate through ALL 21 categories across cycles. Never repeat the same category twice in a row.
[CAT-01] CHAT            : Ask a factual or knowledge question. Ciel answers from memory. No tools needed.
[CAT-02] WORKSPACE_LIST  : List all files and folders in ciel_workspace/ recursively. Print the full tree.
[CAT-03] WORKSPACE_READ  : Read a specific existing file from ciel_workspace/ and print its content.
[CAT-04] WORKSPACE_WRITE : Write a new file to ciel_workspace/ with specific, concrete content.
[CAT-05] WORKSPACE_APPEND: Append a timestamped log line to an existing file in ciel_workspace/.
[CAT-06] MEMORY_SAVE     : Save a specific key-value fact to Ciel's memory vault using save_fact.
[CAT-07] MEMORY_GET      : Retrieve a specific fact by key from the memory vault using get_fact.
[CAT-08] MEMORY_DELETE   : Delete a specific fact key from the memory vault using delete_fact.
[CAT-09] OS_SHELL        : Execute a safe read-only shell command (dir, tasklist, systeminfo, ping localhost).
[CAT-10] OS_SCREENSHOT   : Take a screenshot of the current screen and describe what is visible.
[CAT-11] CODE_GEN        : Write a Python script to autonomous_pipeline/agent_output/<name>.py (write only, no run).
[CAT-12] TRADING_PRICE   : Get the current closing price of BTC/USDT, ETH/USDT, XAU/USD, or EUR/USD.
[CAT-13] TRADING_STATS   : Get 24h stats (price change %, 24h high, 24h low) for BTC or ETH.
[CAT-14] TRADING_TA      : Get RSI + SMA technical analysis for BTC or ETH on 1h interval, report trend signal.
[CAT-15] GMAIL_SEARCH    : Search unread or recent emails by keyword or sender, list subjects and senders.
[CAT-16] GIT_STATUS      : Check the git status of the Ciel 2.0 repository, report uncommitted changes.
[CAT-17] GIT_DIFF        : Run git diff on the repository and report the first meaningful changed lines.
[CAT-18] SELF_HEALING    : Run ciel_workspace/test_healing.py via run_python_script (it may have errors — tests Ciel's self-repair).
[CAT-19] SELF_CORRECTION : Request a file that likely does NOT exist (e.g., ciel_workspace/daily_summary.txt). Tests Brain retry logic.
[CAT-20] EDGE_CASE       : Send a deliberately vague or ambiguous single-sentence request. Tests routing robustness.
[CAT-21] HEALTH_CHECK    : Execute a shell command to check CPU load, memory usage, or free disk space. Print the result.

Last 3 tasks Ciel executed (determine which categories were already covered — do NOT repeat them):
{history_str}

SELECTION RULES:
1. Identify the categories of the last 3 tasks from the lookback above.
2. Pick ONE category from the map above that has NOT appeared in the last 3 tasks.
3. If no cleanup/delete task (CAT-08, CAT-08-related) has run in several cycles, prefer it after any write/save task.
4. Balance variety across the 21 categories over time — do not cluster around Trading or Chat.

OUTPUT RULES:
1. Output ONLY the task description string itself. No markdown, no quotes, no 'Task:' label, no conversational filler.
2. The task must be completely solvable by Ciel's tool set in ONE step.
3. DO NOT generate tasks that involve opening GUI text editors. Use programmatic file writes instead.
4. For CAT-11 (CODE_GEN): write to 'autonomous_pipeline/agent_output/<name>.py' only. Do NOT ask Ciel to run it in the same task.
5. SINGLE-SCOPE TASKS ONLY: ONE clear primary objective. Never combine multiple tools or data sources.
6. SPECIFY EXPECTED OUTPUT EXPLICITLY: Always state what Ciel must output. Example: 'print the result to console', 'report only the closing price value', 'save to ciel_workspace/output.txt'. Vague output specs cause hallucination.
"""

    payload = {
        "model": "deepseek-v4-pro",
        "messages": [
            {"role": "system", "content": "You are the Simulated Master, a demanding and precise administrator."},
            {"role": "user", "content": system_prompt}
        ],
        "temperature": 0.7,
        "max_tokens": 1024
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=60)
        if response.status_code == 200:
            res_data = response.json()
            choice = res_data["choices"][0]
            message = choice.get("message", {})
            finish_reason = choice.get("finish_reason", "unknown")
            task_text = (message.get("content") or "").strip()

            # Strip wrapping quotes / markdown the model sometimes adds despite the OUTPUT RULES.
            task_text = task_text.replace("```", "").strip()
            if task_text.startswith('"') and task_text.endswith('"'):
                task_text = task_text[1:-1].strip()

            # Validate: must be a real task, not a reasoning blob.
            # We deliberately do NOT fall back to `reasoning_content` — that field
            # is literally the model's chain-of-thought, and passing it to Ciel
            # crashes the Brain router (JSONDecodeError × 5 retries observed in
            # test_end_to_end). If `content` is empty or reasoning-shaped, raise
            # to trigger the sequential fallback pool instead.
            if not _looks_like_task(task_text):
                preview = (task_text[:150] + "...") if task_text else "(empty)"
                raise RuntimeError(
                    f"DeepSeek returned non-task content "
                    f"(finish_reason={finish_reason}, len={len(task_text)}, preview={preview!r})."
                )

            # Log the successful DeepSeek call
            log_deepseek_call("SIMULATED_MASTER", "TASK_GENERATION", system_prompt, task_text)
            return task_text
        else:
            raise RuntimeError(f"DeepSeek API error: HTTP {response.status_code} - {response.text[:300]}")
    except Exception as e:
        # Graceful fallback task if DeepSeek API fails to avoid crashing cron.
        # Rotate through a diverse pool (avoiding recent prompts) so the pipeline
        # keeps category variety even while task generation is broken.
        print(f"[-] DeepSeek API call failed: {e}. Falling back to a rotated default task.")
        fallback_task = _pick_fallback_task(recent_prompts)

        # Log the fallback for complete audit trail
        log_deepseek_call("SIMULATED_MASTER", "TASK_GENERATION_FALLBACK", system_prompt, f"Fallback Activated: {fallback_task} (Error: {e})")
        return fallback_task


def main():
    print(f"[*] Autonomous Task Generator Booted: {datetime.datetime.now()}")
    # Force working directory to BASE_DIR to keep paths aligned
    os.chdir(BASE_DIR)
    try:
        prompts = get_recent_user_prompts()
        print(f"[*] Lookback Context:\n" + "\n".join([f"  -> {p}" for p in prompts]))
        
        simulated_task = generate_simulated_task(prompts)
        print(f"\n[+] Simulated Master generated task:\n\"{simulated_task}\"")

        # Execute Ciel's agent loop with the generated task
        
        print("\n[*] Initializing Ciel 2.0 loop & executing task...")
        ciel = AgentLoop()
        
        # In cron job background mode, auto-approve high risk tasks
        ciel.core.confirm_callback = lambda tool_name, preview, tool_args: True
        
        response = ciel.run_step(simulated_task)
        
        print("\n[+] Ciel Execution Response:")
        print(response)
        print("[*] Task Generator completed successfully.")
        
    except Exception as e:
        print(f"[!] Critical Error in Task Generator: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()