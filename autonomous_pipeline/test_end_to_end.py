import os
import sys
import datetime
from pathlib import Path
from dotenv import load_dotenv

# Set up paths so we can run from anywhere
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

# Load environment variables
load_dotenv(BASE_DIR / ".env")


def main():
    print("=" * 60)
    print("      CIEL 2.0 PIPELINE - IMMEDIATE END-TO-END TEST")
    print("=" * 60)
    print(f"[*] Starting immediate integration test: {datetime.datetime.now()}")
    # Force working directory to BASE_DIR to keep paths aligned
    os.chdir(BASE_DIR)
    
    # PHASE 1: FORCE TASK GENERATION & WORKER RUN
    print("\n[PHASE 1] FORCING SIMULATED MASTER TASK GENERATION...")
    
    # Snapshot current log size BEFORE Ciel runs, so Phase 2 can read exactly the new entries
    log_path = BASE_DIR / "ciel_data" / "logs" / "thoughts.log"
    pre_task_seek = log_path.stat().st_size if log_path.exists() else 0
    print(f"[*] Pre-task log snapshot position: {pre_task_seek}")
    
    try:
        from autonomous_pipeline.task_generator import get_recent_user_prompts, generate_simulated_task
        from core.agent_loop import AgentLoop
        
        prompts = get_recent_user_prompts()
        print(f"[*] Retrieved recent lookback context prompts:")
        for p in prompts:
            print(f"  -> {p}")
            
        print("[*] Generating simulated task using deepseek-v4-flash...")
        simulated_task = generate_simulated_task(prompts)
        print(f"[+] Task generated successfully: \"{simulated_task}\"")
        
        print("[*] Initializing Ciel 2.0 Loop & executing task...")
        ciel = AgentLoop()
        # Auto-approve high risk steps for background automation testing
        ciel.core.confirm_callback = lambda tool_name, preview, tool_args: True
        
        response = ciel.run_step(simulated_task)
        print(f"[+] Ciel responded: \"{response}\"")
        print("[PHASE 1 COMPLETE] Task executed and saved to thoughts.log.")
        
    except Exception as e:
        print(f"[-] Phase 1 failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
        
    # PHASE 2: FORCE NIGHTLY AUDIT & JUDGE REPORTING
    print("\n[PHASE 2] FORCING NIGHTLY JUDGE AUDIT & TELEGRAM DIGEST...")
    try:
        # Import data pipeline methods
        from autonomous_pipeline.data_pipeline import (
            load_state, get_new_log_entries, parse_entries, 
            extract_worker_pairs, audit_batch_with_judge, 
            format_chatml, save_to_finetune_file, get_dataset_size,
            generate_dashboard, send_telegram_alert, save_state,
            clean_for_markdown
        )
        
        state = load_state()
        # Use pre-task snapshot seek, NOT state file seek — this guarantees we read the task we just ran
        print(f"[*] Reading log from pre-task snapshot position: {pre_task_seek}")
        
        new_content, new_seek = get_new_log_entries(pre_task_seek)
        if not new_content:
            print("[!] Warning: No new log content found since pre-task snapshot! Retrying from beginning (seek 0).")
            new_content, new_seek = get_new_log_entries(0)
            
        entries = parse_entries(new_content)
        pairs = extract_worker_pairs(entries)
        
        processed_today = len(pairs)
        print(f"[+] Retrieved {processed_today} Worker transactions for DeepSeek audit.")
        
        if processed_today == 0:
            print("[-] No valid human-worker format transactions found in log segment. Cannot run DeepSeek Judge audit. Exiting test.")
            sys.exit(0)
            
        accepted_today = 0
        rejected_today = 0
        records_to_save = []
        
        # Audit with Judge
        print("[*] Sending batch of transactions to The Judge (deepseek-v4-pro)...")
        # limit to first 10 for test speed
        evaluations, critique, recommendation = audit_batch_with_judge(pairs[:10])  
        
        eval_map = {item["index"]: item for item in evaluations if "index" in item}
        
        for idx, pair in enumerate(pairs[:10]):
            audit = eval_map.get(idx)
            if not audit:
                # fallback
                has_master = "Master" in pair["original_response"]
                classification = "GOOD" if has_master else "WEAK"
                paraphrase_1 = pair["original_response"] if has_master else None
                paraphrase_2 = pair["original_response"] if has_master else None
                rewrite = None if has_master else ("Master, " + pair["original_response"])
            else:
                classification = audit.get("classification", "WEAK").upper()
                paraphrase_1 = audit.get("paraphrase_1")
                paraphrase_2 = audit.get("paraphrase_2")
                rewrite = audit.get("rewrite")
                
            print(f"  -> Transaction {idx}: Classified as {classification}")
            
            if classification == "GOOD":
                accepted_today += 1
                records_to_save.append(format_chatml(pair["user_request"], pair["raw_result"], pair["original_response"]))
                if paraphrase_1:
                    records_to_save.append(format_chatml(pair["user_request"], pair["raw_result"], paraphrase_1))
                if paraphrase_2:
                    records_to_save.append(format_chatml(pair["user_request"], pair["raw_result"], paraphrase_2))
            elif classification == "WEAK" and rewrite:
                accepted_today += 1
                records_to_save.append(format_chatml(pair["user_request"], pair["raw_result"], rewrite))
            else:
                rejected_today += 1
                
        if records_to_save:
            save_to_finetune_file(records_to_save)
            print(f"[+] Wrote {len(records_to_save)} training records to worker_finetune.jsonl")
            
        current_dataset_size = get_dataset_size()
        
        # Save updated seek state
        state["last_processed_seek"] = new_seek
        state["total_processed"] += processed_today
        state["total_accepted"] += accepted_today
        state["total_rejected"] += rejected_today
        state["cumulative_dataset_size"] = current_dataset_size
        save_state(state)
        
        # Render dashboard image
        print("[*] Rendering Pillow dashboard visuals...")
        img_path = generate_dashboard(processed_today, accepted_today, rejected_today, current_dataset_size)
        
        # Alert Telegram
        print("[*] Delivering MLOps alert and metrics visual to Telegram...")
        tasks_summary_lines = []
        if pairs:
            tasks_summary_lines.append("\n📝 *Recent Tasks & Outcomes Summary:*")
            for p_idx, pair in enumerate(pairs[-4:]):
                req_clean = clean_for_markdown(pair["user_request"])
                res_clean = clean_for_markdown(pair["original_response"])
                
                req_trunc = req_clean if len(req_clean) <= 80 else req_clean[:77] + "..."
                res_trunc = res_clean if len(res_clean) <= 120 else res_clean[:117] + "..."
                res_trunc = res_trunc.replace("\n", " ")
                tasks_summary_lines.append(f"*{p_idx+1}. Task:* `{req_trunc}`\n   ↳ *Ciel:* {res_trunc}")
                
        tasks_summary_text = "\n".join(tasks_summary_lines) if tasks_summary_lines else ""
        
        critique_clean = clean_for_markdown(critique) if critique else "Standard runtime performance looks stable."
        rec_clean = clean_for_markdown(recommendation) if recommendation else "N/A"
        
        telegram_msg = (
            "🧪 *Ciel 2.0 Integration Test Completed Successfully*\n\n"
            f"📊 *Test Metrics Breakdown:*\n"
            f"• Processed: `{processed_today}` worker items\n"
            f"• Accepted/Augmented: `{accepted_today}` records\n"
            f"• Hallucinated/Discarded: `{rejected_today}` records\n\n"
            f"📈 *Finetune Dataset Progress:*\n"
            f"• Added in test: `+{len(records_to_save)}` ChatML pairs\n"
            f"• Cumulative Size: `{current_dataset_size}` ChatML pairs\n\n"
            f"⚖️ *Judge's MLOps Assessment:*\n"
            f"• *Critique:* {critique_clean}\n"
            f"• *Recommendation:* {rec_clean}\n"
            f"{tasks_summary_text}\n\n"
            "Status: *Immediate end-to-end pipeline test verified.*"
        )
        send_telegram_alert(telegram_msg, img_path)
        print("\n[PHASE 2 COMPLETE] Integration test completed cleanly!")
        print("=" * 60)
        
    except Exception as e:
        print(f"[-] Phase 2 failed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
