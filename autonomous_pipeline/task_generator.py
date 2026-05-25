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
    
    # Determine the time of day and schedule task category
    now = datetime.datetime.now()
    hour = now.hour
    
    if 8 <= hour < 11:
        time_slot = "Morning (08:00-10:00)"
        focus = "Checking unread emails, listing git repository status, crypto/gold price updates, or daily brief summarizations."
    elif 11 <= hour < 15:
        time_slot = "Mid-day (11:00-14:00)"
        focus = "Searching for current technology/AI news, reading files, or writing/running Python scripts in 'autonomous_pipeline/agent_output/'."
    elif 15 <= hour < 18:
        time_slot = "Afternoon (15:00-17:00)"
        focus = "Writing and testing scripts inside 'autonomous_pipeline/agent_output/', performing git commits, or scraping specific websites for structural data."
    else:
        # Evening or Night
        time_slot = "Evening/Night (18:00-22:00)"
        focus = "Summarizing 24h market prices (forex/crypto), reviewing workspace logs/files, or performing automated system status checks."
        
    current_time_str = now.strftime("%Y-%m-%d %H:%M:%S")
    
    # Simulated Master Instruction - Using RAW multi-line f-string (no markdown)
    system_prompt = f"""You are the Simulated Master, the owner and administrator of the Ciel 2.0 AI agent. 
Your goal is to generate exactly ONE realistic, high-value, and actionable request for Ciel to execute right now.

Current VPS Date and Time: {current_time_str}
Current Time Slot: {time_slot}
Current Task Focus: {focus}

Last 3 user requests for context (Lookback mechanism):
{history_str}

RULES:
1. Output ONLY the task description string itself. Do not include markdown formatting, markdown blocks, quotes, prefix labels like 'Task:', or conversational filler.
2. Be context-aware. If the last request was related to a topic (e.g., BTC price), follow up logically (e.g., check BTC technical stats or analyze news).
3. The task must fit the current Time Slot focus but feel like a natural instruction from a demanding master.
4. The task must be completely solvable by Ciel's tool set (email, filesystem CRUD, python script running, market prices, git commands, vision UI desktop commands).
5. DO NOT generate tasks that involve opening GUI text editors (such as Notepad) or manually typing/editing text via graphical interfaces. If the task requires creating or modifying a document/file, instruct Ciel to write or append to it programmatically within the secure workspace instead.
6. If the task involves creating, writing, or updating any Python script, you MUST explicitly command Ciel to BOTH write/save the file inside the dedicated playground directory 'autonomous_pipeline/agent_output/' AND run/execute the script immediately afterwards to verify that it runs successfully and prints/outputs the expected results. Never just ask to create or save a file; always ask to create AND test run it to prove it works!
"""

    payload = {
        "model": "deepseek-v4-flash",
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
