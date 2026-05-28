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
    """Extract the last 3 user requests from memory_bank.json or thoughts.log."""
    prompts = []
    
    # 1. Try reading from memory_bank.json (primary source)
    memory_bank_path = BASE_DIR / "ciel_data" / "memory_bank.json"
    if memory_bank_path.exists():
        try:
            with open(memory_bank_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                human_messages = [msg["content"] for msg in data if msg.get("type") == "human"]
                prompts = human_messages[-3:]
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
                
                # Merge lists
                for p in log_prompts:
                    if p not in prompts:
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
    history_str = "\n".join([f"- {p}" for p in recent_prompts])
    
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
            task_text = res_data["choices"][0]["message"]["content"].strip()
            
            # Strip any potential wrapping quotes or markdown blocks that the model hallucinated
            task_text = task_text.replace("```", "").strip()
            if task_text.startswith('"') and task_text.endswith('"'):
                task_text = task_text[1:-1].strip()
                
            # Log the successful DeepSeek call
            log_deepseek_call("SIMULATED_MASTER", "TASK_GENERATION", system_prompt, task_text)
            return task_text
        else:
            raise RuntimeError(f"DeepSeek API error: HTTP {response.status_code} - {response.text}")
    except Exception as e:
        # Graceful fallback task if DeepSeek API fails to avoid crashing cron
        print(f"[-] DeepSeek API call failed: {e}. Falling back to default scheduled task.")
        fallback_task = ""
        if 8 <= hour < 11:
            fallback_task = "Check my unread emails and crypto prices today."
        elif 11 <= hour < 15:
            fallback_task = "Search Google for today's high-level AI tech news."
        elif 15 <= hour < 18:
            fallback_task = "Check workspace git repository status and list recent commits."
        else:
            fallback_task = "Retrieve crypto and gold 24h market price summaries."
            
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

        # Guard: empty string from DeepSeek causes AgentLoop crash ("contents are required")
        if not simulated_task or not simulated_task.strip():
            hour = datetime.datetime.now().hour
            if 8 <= hour < 11:
                simulated_task = "Check the current BTC price and print only the closing price value to console."
            elif 11 <= hour < 15:
                simulated_task = "List all files in the autonomous_pipeline/agent_output/ directory and print the results to console."
            elif 15 <= hour < 18:
                simulated_task = "Get the current EUR/USD exchange rate and print only the price value to console."
            else:
                simulated_task = "Retrieve the XAU/USD gold price and print only the closing price value to console."
            print(f"[!] Task generation returned empty string. Using time-based fallback: \"{simulated_task}\"")
            log_deepseek_call("SIMULATED_MASTER", "TASK_GENERATION_EMPTY_FALLBACK", "N/A", f"Empty string returned — fallback used: {simulated_task}")

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