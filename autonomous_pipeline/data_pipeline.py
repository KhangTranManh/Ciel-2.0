import os
import sys
import re
import json
import time
import datetime
from pathlib import Path
import requests
from dotenv import load_dotenv

# Set up paths so we can run from anywhere (VPS cron job)
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

# Load environment variables
load_dotenv(BASE_DIR / ".env")

try:
    from PIL import Image, ImageDraw, ImageFont
    PILLOW_AVAILABLE = True
except ImportError:
    PILLOW_AVAILABLE = False


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


def load_state():
    """Load persistent pipeline metrics state from state.json."""
    state_path = Path(__file__).resolve().parent / "state.json"
    default_state = {
        "last_processed_seek": 0,
        "total_processed": 0,
        "total_accepted": 0,
        "total_rejected": 0,
        "cumulative_dataset_size": 0
    }
    if state_path.exists():
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[-] Failed to load state.json, using defaults: {e}")
    return default_state


def save_state(state):
    """Save persistent pipeline metrics state to state.json."""
    state_path = Path(__file__).resolve().parent / "state.json"
    try:
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        print(f"[-] Failed to save state.json: {e}")


def get_new_log_entries(last_seek):
    """Read only the newly appended content from thoughts.log since the last run."""
    log_path = BASE_DIR / "ciel_data" / "logs" / "thoughts.log"
    if not log_path.exists():
        print("[-] thoughts.log does not exist yet.")
        return "", last_seek
        
    try:
        file_size = log_path.stat().st_size
        # Handle log rotation or clearing
        if file_size < last_seek:
            print("[*] Log size is smaller than previous seek. Log was cleared/cleansed. Resetting seek to 0.")
            last_seek = 0
            
        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
            f.seek(last_seek)
            new_content = f.read()
            new_seek = f.tell()
            
        return new_content, new_seek
    except Exception as e:
        print(f"[-] Failed to read new log entries: {e}")
        return "", last_seek


def parse_entries(log_content):
    """Parse thoughts.log structured entries by matching hyphens and headers."""
    entries = []
    # Split by 60 hyphens
    raw_entries = log_content.split("-" * 60)
    for raw in raw_entries:
        raw = raw.strip()
        if not raw:
            continue
            
        lines = raw.splitlines()
        if not lines:
            continue
            
        header = lines[0]
        # Match header format: [2026-05-26 01:14:19] [WORKER] [FORMAT_TASK]
        match = re.match(r"^\[(.*?)\] \[(.*?)\] \[(.*?)\]", header)
        if match:
            timestamp = match.group(1)
            actor = match.group(2).upper()
            action = match.group(3).upper()
            content = "\n".join(lines[1:]).strip()
            entries.append({
                "timestamp": timestamp,
                "actor": actor,
                "action": action,
                "content": content
            })
    return entries


def extract_worker_pairs(entries):
    """
    Extract consecutive user requests and corresponding worker responses from thoughts.log.
    Handles all response types: FORMAT_RESPONSE (tools), CHAT_RESPONSE (chat), and CODE_RESPONSE (code gen).
    """
    pairs = []
    i = 0
    while i < len(entries):
        curr = entries[i]
        
        if curr["actor"] == "USER" and curr["action"] == "REQUEST":
            user_req = curr["content"].strip()
            # Strip RAG context injection so Judge evaluates only the actual task,
            # not the [RECALLED PAST CONTEXT] prefix Ciel injects into the prompt.
            if "[CURRENT USER REQUEST]:" in user_req:
                user_req = user_req.split("[CURRENT USER REQUEST]:")[-1].strip()

            # Look forward for Ciel's final response in this session (max 15 entries look-ahead)
            response_content = None
            tool_name = "chat"
            raw_result = "N/A - Direct or chat response"
            
            for j in range(i + 1, min(i + 15, len(entries))):
                look_ahead = entries[j]
                
                # If we encounter another user request before a response, stop
                if look_ahead["actor"] == "USER" and look_ahead["action"] == "REQUEST":
                    break
                    
                # Track tool name and result if present
                if look_ahead["actor"] == "BRAIN" and look_ahead["action"] == "ROUTE_DECISION":
                    try:
                        import json
                        brain_json = look_ahead["content"]
                        if brain_json.startswith("```json"):
                            brain_json = brain_json[7:]
                        if brain_json.endswith("```"):
                            brain_json = brain_json[:-3]
                        route_data = json.loads(brain_json.strip())
                        if "tool_name" in route_data:
                            tool_name = route_data["tool_name"]
                        elif "action" in route_data:
                            tool_name = route_data["action"]
                    except Exception:
                        pass
                
                if look_ahead["actor"] == "TOOL" and look_ahead["action"] == "RESULT":
                    raw_result = look_ahead["content"].strip()
                
                # Identify the final response block
                if look_ahead["actor"] == "WORKER" and (
                    look_ahead["action"] in ["FORMAT_RESPONSE", "CHAT_RESPONSE", "CODE_RESPONSE"] or 
                    look_ahead["action"].endswith("_RESPONSE")
                ):
                    response_content = look_ahead["content"].strip()
                    break
            
            if response_content:
                pairs.append({
                    "user_request": user_req,
                    "tool_name": tool_name,
                    "raw_result": raw_result,
                    "original_response": response_content
                })
        i += 1
    return pairs


def audit_batch_with_judge(batch_pairs):
    """Call DeepSeek API (The Judge) using a structured evaluation prompt."""
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        print("[!] DEEPSEEK_API_KEY is not defined. Skipping audit.")
        return [], "No API key configured.", "Set DEEPSEEK_API_KEY in .env."
        
    # Standardize endpoint and custom gateway mapping
    url = "https://api.deepseek.com/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    # Construct batch representation for the prompt
    batch_data = []
    for idx, pair in enumerate(batch_pairs):
        # For code tool: do not truncate response — Judge needs full script to evaluate correctly.
        # For non-code tools: truncate at 800 chars to prevent max_tokens overflow.
        worker_resp = pair["original_response"]
        is_code_tool = pair["tool_name"] == "code"
        if not is_code_tool and len(worker_resp) > 800:
            worker_resp = worker_resp[:800] + "\n... [TRUNCATED FOR AUDIT]"
        batch_data.append({
            "index": idx,
            "user_request": pair["user_request"][:500],
            "tool_name": pair["tool_name"],
            "raw_tool_result": pair["raw_result"][:1500],  # Truncated for safety
            "worker_response": worker_resp
        })
        
    batch_json = json.dumps(batch_data, indent=2, ensure_ascii=False)
    
    # Detailed Judge prompt - using raw multi-line f-string (no markdown)
    prompt = f"""You are the Nightly Judge for the Ciel 2.0 AI Agent.
Your task is to audit the performance of Ciel's local Worker model and generate synthetic dataset records for finetuning.

We have extracted a batch of Worker transactions (pairs of User Request + Raw Tool Results, and the Worker's actual response).
You must evaluate each item strictly against four criteria:

1. NO HALLUCINATION: The response must ONLY contain facts present in the Raw Tool Results. If the Worker invented data, actions, or statuses not present in the tool results -> Tag [BAD].
   CRITICAL EXCEPTION (tool_name = 'code'): If tool_name is 'code', the Worker's response is a Python script and Raw Tool Results will always be 'N/A'. DO NOT apply the hallucination check against 'N/A'. Instead, evaluate whether the script logically and correctly solves the user_request. A script that correctly addresses the task is NOT hallucination even when Raw Tool Results = 'N/A'.
   RULE (empty or error results, non-code tools): If Raw Tool Results are empty, 'N/A', or contain an error message, the Worker MUST explicitly state that the data is unavailable. A response that invents data to compensate -> [BAD]. A response that clearly acknowledges unavailability -> passes this criterion.

2. COMPLETENESS: Count the number of distinct tasks or tool calls requested in the user_request. The Worker response MUST address ALL of them. If ANY subtask is entirely missing from the response (not just brief, but completely absent) -> Tag [BAD]. If tool results for a subtask are unavailable, the Worker must explicitly state that subtask's data is unavailable — silence is not acceptable.

3. CONCISENESS: The response must be extremely concise and direct. No filler or conversational fluff. If too verbose -> Tag [WEAK].

4. PERSONA: For standard conversational or format responses (where tool_name is 'chat' or 'format'), the response MUST address the user as 'Master'. If it fails to call them 'Master' -> Tag [WEAK].
   CRITICAL EXCEPTION: If the tool_name is 'code' (meaning the worker's response is a raw Python script), the 'Master' persona rule does NOT apply, and the script code should NOT contain any conversational greetings. Treat pure Python script outputs as persona-compliant by default.

RESPONSE TYPE RUBRIC — apply these type-specific standards consistently:
- tool_name = 'code' (Python script output):
  * Evaluate correctness by whether the script solves the user_request — NOT by comparing against 'N/A' tool results.
  * NO dummy/hardcoded test data or fabricated file paths in __main__ blocks -> [WEAK]. If it fabricates results entirely unrelated to the task -> [BAD].
  * NO excessive docstrings or multi-line comment blocks on every function. Production code only; at most one brief comment per non-obvious step -> [WEAK] if violated.
  * Script must include proper error handling for file/path operations where relevant.
- tool_name = 'chat' (conversational response):
  * Must be direct and extremely concise — no pleasantries, no "Great question!", no padding.
  * Must address user as 'Master'. Failure -> [WEAK].
- All other tool_names (tool/format responses):
  * Must report ONLY what is present in Raw Tool Results.
  * If Raw Tool Results are empty, error, or 'N/A': response MUST explicitly state data is unavailable. Invented data -> [BAD].
  * Must address user as 'Master'. Failure -> [WEAK].

Based on this, classify each item:
- Classification [GOOD]: Meets all four criteria and rubric standards.
  Actions for [GOOD]: You must also generate exactly 2 diverse paraphrased versions of the Worker's response. The paraphrases must keep the exact same concise information and address the user as 'Master', but use synonyms or different sentence structures.
- Classification [WEAK]: The response did NOT hallucinate and IS complete, but has fixable quality issues (too verbose, missing 'Master', excessive code comments, unnecessary demo __main__ block).
  Actions for [WEAK]: You must rewrite the response to meet the [GOOD] standard (direct, extremely concise, rubric-compliant). For code: remove dummy data and trim excessive comments while keeping the logic intact.
- Classification [BAD]: The response contains hallucinations, fabricated data/actions not in tool results, OR is missing one or more required subtasks entirely.
  Actions for [BAD]: Mark as BAD. No rewrites or paraphrases needed.

You must respond with EXACTLY ONE valid JSON object. Do not include markdown fences, preambles, or explanations outside the JSON block.

IMPORTANT — user_request field cleanup: Some user_request values may begin with a [RECALLED PAST CONTEXT] block followed by [CURRENT USER REQUEST]. Always evaluate the Worker response ONLY against the [CURRENT USER REQUEST] section. Completely ignore any [RECALLED PAST CONTEXT] content when assessing hallucination or completeness.

BATCH TO AUDIT:
{batch_json}

Your JSON response format must be an object matching this exact schema:
{{
  "evaluations": [
    {{
      "index": 0,
      "classification": "GOOD" | "WEAK" | "BAD",
      "reasoning": "1-sentence explanation of your evaluation",
      "paraphrase_1": "Paraphrased version 1 (only if GOOD, else null)",
      "paraphrase_2": "Paraphrased version 2 (only if GOOD, else null)",
      "rewrite": "Rewritten concise version addressing 'Master' (only if WEAK, else null)"
    }}
  ],
  "overall_critique": "A brief 2-sentence summary of the worker's performance, accuracy, and persona compliance in this batch.",
  "improvement_recommendation": "1 actionable tip to improve the worker prompt or model response formatting."
}}
"""
    
    # Retry loop: attempt up to MAX_JUDGE_RETRIES times on JSON parse failures.
    # Each retry increases temperature slightly to get a different (hopefully valid) output.
    MAX_JUDGE_RETRIES = 2
    last_exception = None
    
    for retry in range(MAX_JUDGE_RETRIES + 1):
        temperature = 0.2 + (retry * 0.1)  # 0.2 → 0.3 → 0.4
        
        payload = {
            "model": "deepseek-v4-pro",
            "messages": [
                {"role": "system", "content": "You are a precise data auditor. Keep your reasoning extremely brief and direct, then respond only with a valid JSON object."},
                {"role": "user", "content": prompt}
            ],
            "temperature": temperature,
            "max_tokens": 16384,
            "response_format": {"type": "json_object"}
        }
        
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=120)
            if response.status_code != 200:
                # Try a fallback without response_format if not supported by proxy
                if response.status_code == 400:
                    payload.pop("response_format", None)
                    response = requests.post(url, headers=headers, json=payload, timeout=120)
                    
            if response.status_code == 200:
                res_data = response.json()
                raw_text = res_data["choices"][0]["message"]["content"].strip()
                
                # Strip markdown fences if present
                if raw_text.startswith("```"):
                    raw_text = raw_text.split("\n", 1)[-1]
                    if raw_text.endswith("```"):
                        raw_text = raw_text[:-3]
                    raw_text = raw_text.strip()
                    
                log_deepseek_call("JUDGE", "BATCH_AUDIT", prompt, raw_text)
                
                parsed = json.loads(raw_text)
                evaluations = parsed.get("evaluations", [])
                critique = parsed.get("overall_critique", "N/A - Standard runtime performance looks stable.")
                recommendation = parsed.get("improvement_recommendation", "N/A - Continue monitoring current training cycles.")
                return evaluations, critique, recommendation
            else:
                err_msg = f"HTTP {response.status_code} - {response.text}"
                print(f"[-] DeepSeek API call failed: {err_msg}")
                log_deepseek_call("JUDGE", "BATCH_AUDIT_ERROR", prompt, err_msg)
                return [], "Audit connection error.", "Verify API connection."
        except json.JSONDecodeError as e:
            last_exception = e
            if retry < MAX_JUDGE_RETRIES:
                wait = 2 ** (retry + 1)  # 2s, 4s
                print(f"[-] Judge JSON parse failed (attempt {retry+1}/{MAX_JUDGE_RETRIES+1}): {e}. Retrying in {wait}s...")
                log_deepseek_call("JUDGE", "BATCH_AUDIT_RETRY", prompt, f"Retry {retry+1}: JSON parse error: {e}")
                time.sleep(wait)
            else:
                print(f"[-] Judge JSON parse failed after {MAX_JUDGE_RETRIES+1} attempts: {e}")
                log_deepseek_call("JUDGE", "BATCH_AUDIT_EXCEPTION", prompt, f"Exception after {MAX_JUDGE_RETRIES+1} attempts: {e}")
                return [], f"Audit parsing failed: {e}", "Verify JSON format."
        except Exception as e:
            print(f"[-] Exception during DeepSeek audit: {e}")
            log_deepseek_call("JUDGE", "BATCH_AUDIT_EXCEPTION", prompt, f"Exception: {e}")
            return [], f"Audit parsing failed: {e}", "Verify JSON format."
    
    # Should not reach here, but safety fallback
    return [], f"Audit parsing failed: {last_exception}", "Verify JSON format."


def format_chatml(user_req, tool_results, assistant_response):
    """Format prompt data pair to standard ChatML format."""
    prompt_str = f"[USER REQUEST]\n{user_req}\n\n[TOOL RESULTS]\n{tool_results}"
    return {
        "messages": [
            {
                "role": "system",
                "content": "You are Ciel's Worker. Answer the Master's request concisely based only on the tool results."
            },
            {
                "role": "user",
                "content": prompt_str
            },
            {
                "role": "assistant",
                "content": assistant_response
            }
        ]
    }


def save_to_finetune_file(records):
    """Append accepted training records to worker_finetune.jsonl."""
    finetune_path = BASE_DIR / "autonomous_pipeline" / "worker_finetune.jsonl"
    finetune_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(finetune_path, "a", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[-] Failed to write to worker_finetune.jsonl: {e}")


def get_dataset_size():
    """Retrieve cumulative line count of worker_finetune.jsonl."""
    finetune_path = BASE_DIR / "autonomous_pipeline" / "worker_finetune.jsonl"
    if finetune_path.exists():
        try:
            with open(finetune_path, "r", encoding="utf-8") as f:
                return sum(1 for _ in f)
        except Exception:
            return 0
    return 0


def generate_dashboard(processed, accepted, rejected, dataset_size):
    """Render a premium dark-themed visual metrics dashboard in Pillow."""
    if not PILLOW_AVAILABLE:
        print("[-] Pillow is not installed. Dashboard generation skipped.")
        return None
        
    # Dimensions and base setup
    width, height = 800, 500
    img = Image.new("RGB", (width, height), color=(15, 23, 42))  # slate-900
    draw = ImageDraw.Draw(img)
    
    # Border accents
    draw.rectangle([5, 5, width - 5, height - 5], outline=(30, 41, 59), width=2)
    
    # Try load Arial or fallback
    try:
        font_title = ImageFont.truetype("arial.ttf", 26)
        font_subtitle = ImageFont.truetype("arial.ttf", 15)
        font_metric = ImageFont.truetype("arial.ttf", 36)
        font_lbl = ImageFont.truetype("arial.ttf", 13)
        font_normal = ImageFont.truetype("arial.ttf", 15)
    except Exception:
        font_title = ImageFont.load_default()
        font_subtitle = ImageFont.load_default()
        font_metric = ImageFont.load_default()
        font_lbl = ImageFont.load_default()
        font_normal = ImageFont.load_default()
        
    # Header block
    draw.rectangle([10, 10, width - 10, 80], fill=(30, 41, 59))
    draw.text((30, 20), "CIEL 2.0 NIGHTLY PIPELINE SUMMARY", fill=(0, 240, 255), font=font_title)  # Neon Cyan
    draw.text((30, 55), f"Data Audit & Augmentation Pipeline | {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", fill=(148, 163, 184), font=font_subtitle)
    
    # Draw metrics cards (slate-800 boxes)
    cards = [
        {"lbl": "PROCESSED TODAY", "val": str(processed), "color": (168, 85, 247), "x": 40},       # Purple
        {"lbl": "ACCEPTED TODAY", "val": str(accepted), "color": (34, 197, 94), "x": 225},       # Green
        {"lbl": "REJECTED TODAY", "val": str(rejected), "color": (239, 68, 68), "x": 410},       # Red
        {"lbl": "TOTAL DATASET SIZE", "val": str(dataset_size), "color": (14, 165, 233), "x": 595} # Blue
    ]
    
    for c in cards:
        x, y, w, h = c["x"], 120, 165, 140
        # Background card
        draw.rectangle([x, y, x + w, y + h], fill=(30, 41, 59))
        # Top highlight bar
        draw.rectangle([x, y, x + w, y + 6], fill=c["color"])
        # Labels and Values
        draw.text((x + 12, y + 25), c["lbl"], fill=(148, 163, 184), font=font_lbl)
        draw.text((x + 12, y + 65), c["val"], fill=(255, 255, 255), font=font_metric)
        
    # Stacked horizontal bar chart representing breakdown
    chart_y = 300
    chart_h = 35
    chart_w = 720
    chart_x = 40
    
    draw.text((chart_x, chart_y - 25), "ACCÈPTANCE vs. REJECTION RATIO", fill=(255, 255, 255), font=font_normal)
    
    if processed > 0:
        acc_ratio = accepted / processed
        rej_ratio = rejected / processed
        acc_px = int(acc_ratio * chart_w)
        
        # Accepted portion (Green)
        if acc_px > 0:
            draw.rectangle([chart_x, chart_y, chart_x + acc_px, chart_y + chart_h], fill=(34, 197, 94))
        # Rejected portion (Red)
        if (chart_w - acc_px) > 0:
            draw.rectangle([chart_x + acc_px, chart_y, chart_x + chart_w, chart_y + chart_h], fill=(239, 68, 68))
            
        # Text ratios
        draw.text((chart_x, chart_y + chart_h + 10), f"Accepted Ratio: {acc_ratio*100:.1f}%", fill=(34, 197, 94), font=font_subtitle)
        draw.text((chart_x + chart_w - 180, chart_y + chart_h + 10), f"Rejected Ratio: {rej_ratio*100:.1f}%", fill=(239, 68, 68), font=font_subtitle)
    else:
        # Zero processed bar outline
        draw.rectangle([chart_x, chart_y, chart_x + chart_w, chart_y + chart_h], outline=(71, 85, 105), width=2)
        draw.text((chart_x + 10, chart_y + 8), "No new worker transactions logged today.", fill=(148, 163, 184), font=font_subtitle)
        
    # Bottom watermark signature
    draw.text((40, 460), "Ciel 2.0 MLOps Automation Layer", fill=(71, 85, 105), font=font_lbl)
    
    dashboard_path = BASE_DIR / "autonomous_pipeline" / "daily_dashboard.png"
    img.save(dashboard_path)
    return str(dashboard_path)


def _make_audit_line(pair, audit, classification):
    """Build a compact per-task dict for the Telegram audit summary."""
    tool = pair["tool_name"]
    if tool == "code":
        task_type = "CODE"
    elif tool == "chat":
        task_type = "CHAT"
    else:
        task_type = "TOOL"
    reasoning = (audit.get("reasoning", "") if audit else "no evaluation returned")[:100]
    return {
        "type": task_type,
        "master": pair["user_request"][:70].replace("\n", " "),
        "brain": f"routed → {tool}",
        "worker": pair["original_response"][:70].replace("\n", " "),
        "judge": f"{classification} — {reasoning}",
    }


def send_telegram_alert(message):
    """Send plain-text audit summary to Telegram."""
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not bot_token or not chat_id:
        print("[!] Telegram credentials missing from env. Telegram message skipped.")
        return

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": chat_id, "text": message}, timeout=20)
        if r.status_code != 200:
            print(f"[-] Telegram sendMessage failed (HTTP {r.status_code}): {r.text}")
        else:
            print("[+] Telegram MLOps notification delivered.")
    except Exception as e:
        print(f"[-] Telegram alert delivery failed: {e}")


def main():
    print(f"[*] Nightly MLOps Pipeline Booted: {datetime.datetime.now()}")
    
    # 1. Load state
    state = load_state()
    last_seek = state.get("last_processed_seek", 0)
    print(f"[*] Reading log file from seek cursor: {last_seek}")
    
    # 2. Fetch new log differences
    new_content, new_seek = get_new_log_entries(last_seek)
    if not new_content:
        print("[*] No new log data since last audit. Updating seek cursor and generating summary.")
        state["last_processed_seek"] = new_seek
        current_dataset_size = get_dataset_size()
        state["cumulative_dataset_size"] = current_dataset_size
        save_state(state)

        date_str = datetime.datetime.now().strftime("%d/%m %H:%M")
        telegram_msg = (
            f"🧠 MLOps Audit | {date_str}\n\n"
            f"📊 No new transactions recorded.\n"
            f"Cumulative dataset: {current_dataset_size} pairs."
        )
        send_telegram_alert(telegram_msg)
        return
        
    # 3. Parse entries and extract pairs
    entries = parse_entries(new_content)
    pairs = extract_worker_pairs(entries)
    
    processed_today = len(pairs)
    print(f"[+] Found {processed_today} new Worker formatting pairs since last run.")
    
    accepted_today = 0
    rejected_today = 0
    records_to_save = []
    task_audit_lines = []  # Per-task results accumulated for Telegram summary

    # 4. Group pairs in batches of up to 4 items.
    # Smaller batches prevent DeepSeek from hitting max_tokens and truncating JSON mid-response.
    # Previous batch_size=10 caused ~50% Judge parse failures with large code scripts.
    batch_size = 4
    batches = [pairs[i:i + batch_size] for i in range(0, len(pairs), batch_size)]

    overall_critiques = []
    overall_recs = []
    
    for b_idx, batch in enumerate(batches):
        print(f"[*] Auditing batch {b_idx+1}/{len(batches)} (Size: {len(batch)})...")
        evaluations, critique, recommendation = audit_batch_with_judge(batch)
        
        if critique and "Audit parsing failed" not in critique and critique != "N/A":
            overall_critiques.append(critique)
        if recommendation and recommendation != "N/A":
            overall_recs.append(recommendation)
            
        # Map evaluations back
        eval_map = {item["index"]: item for item in evaluations if "index" in item}
        
        for idx, pair in enumerate(batch):
            audit = eval_map.get(idx)
            
            if not audit:
                # Judge returned no evaluation for this item — skip entirely.
                # Auto-accepting unaudited records risks polluting the dataset.
                rejected_today += 1
                print(f"[-] SKIPPED (no Judge evaluation returned for item {idx}): \"{pair['user_request'][:50]}\"")
                task_audit_lines.append(_make_audit_line(pair, None, "SKIP"))
                continue
            else:
                classification = audit.get("classification", "WEAK").upper()
                paraphrase_1 = audit.get("paraphrase_1")
                paraphrase_2 = audit.get("paraphrase_2")
                rewrite = audit.get("rewrite")

            # Perform MLOps Auto-Correction & Augmentation
            if classification == "GOOD":
                accepted_today += 1

                # Append original pair
                records_to_save.append(format_chatml(
                    pair["user_request"], pair["raw_result"], pair["original_response"]
                ))
                # Append Augmentation Paraphrase 1
                if paraphrase_1:
                    records_to_save.append(format_chatml(
                        pair["user_request"], pair["raw_result"], paraphrase_1
                    ))
                # Append Augmentation Paraphrase 2
                if paraphrase_2:
                    records_to_save.append(format_chatml(
                        pair["user_request"], pair["raw_result"], paraphrase_2
                    ))

            elif classification == "WEAK" and rewrite:
                accepted_today += 1
                # Save ONLY rewritten corrected version
                records_to_save.append(format_chatml(
                    pair["user_request"], pair["raw_result"], rewrite
                ))

            else:
                # BAD or rejected
                rejected_today += 1
                print(f"[-] REJECTED [BAD] entry: Request: \"{pair['user_request'][:50]}\"")

            task_audit_lines.append(_make_audit_line(pair, audit, classification))
                
    # 5. Save all records and update metrics
    if records_to_save:
        save_to_finetune_file(records_to_save)
        print(f"[+] Appended {len(records_to_save)} augmented/corrected ChatML records to worker_finetune.jsonl")
        
    current_dataset_size = get_dataset_size()
    
    # 6. Update state
    state["last_processed_seek"] = new_seek
    state["total_processed"] += processed_today
    state["total_accepted"] += accepted_today
    state["total_rejected"] += rejected_today
    state["cumulative_dataset_size"] = current_dataset_size
    save_state(state)
    
    # 7. Send Telegram Notification
    final_critique = " | ".join(overall_critiques) if overall_critiques else "Standard performance."
    final_rec = " | ".join(overall_recs) if overall_recs else "Continue standard training runs."

    date_str = datetime.datetime.now().strftime("%d/%m %H:%M")
    msg_lines = [
        f"MLOps Audit | {date_str}",
        f"Processed: {processed_today} | Accepted: {accepted_today} | Rejected: {rejected_today} | Dataset: {current_dataset_size}",
        "",
    ]
    for i, t in enumerate(task_audit_lines[-4:], 1):
        msg_lines.append(f"[{i}] {t['master'][:80]}")
        msg_lines.append(f"     Worker: {t['worker'][:80]}")
        msg_lines.append(f"     Judge: {t['judge'][:80]}")
    msg_lines.append("")
    msg_lines.append(f"Critique: {final_critique[:200]}")
    msg_lines.append(f"Rec: {final_rec[:150]}")

    send_telegram_alert("\n".join(msg_lines))

    print("[*] Nightly MLOps pipeline execution complete.")


if __name__ == "__main__":
    main()
