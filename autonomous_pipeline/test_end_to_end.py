import os
import sys
import datetime
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
load_dotenv(BASE_DIR / ".env")


def main():
    print("=" * 60)
    print("      CIEL 2.0 PIPELINE - IMMEDIATE END-TO-END TEST")
    print("=" * 60)
    print(f"[*] Starting: {datetime.datetime.now()}")
    os.chdir(BASE_DIR)

    # PHASE 1
    print("\n[PHASE 1] TASK GENERATION & WORKER RUN...")
    log_path = BASE_DIR / "ciel_data" / "logs" / "thoughts.log"
    pre_task_seek = log_path.stat().st_size if log_path.exists() else 0
    print(f"[*] Log snapshot position: {pre_task_seek}")

    try:
        from autonomous_pipeline.task_generator import get_recent_user_prompts, generate_simulated_task
        from core.agent_loop import AgentLoop

        prompts = get_recent_user_prompts()
        for p in prompts:
            print(f"  -> {p}")

        simulated_task = generate_simulated_task(prompts)
        print(f"[+] Task: \"{simulated_task}\"")

        ciel = AgentLoop()
        ciel.core.confirm_callback = lambda tool_name, preview, tool_args: True
        response = ciel.run_step(simulated_task)
        print(f"[+] Ciel responded: \"{response}\"")
        print("[PHASE 1 COMPLETE]")

    except Exception as e:
        print(f"[-] Phase 1 failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    # PHASE 2
    print("\n[PHASE 2] JUDGE AUDIT & TELEGRAM...")
    try:
        from autonomous_pipeline.data_pipeline import (
            load_state, save_state, get_new_log_entries, parse_entries,
            extract_worker_pairs, audit_batch_with_judge,
            format_chatml, save_to_finetune_file, get_dataset_size,
            send_telegram_alert, _classify_task
        )

        state = load_state()
        new_content, new_seek = get_new_log_entries(pre_task_seek)
        if not new_content:
            print("[!] No new log content. Retrying from 0.")
            new_content, new_seek = get_new_log_entries(0)

        entries = parse_entries(new_content)
        pairs = extract_worker_pairs(entries)
        processed_today = len(pairs)
        print(f"[+] {processed_today} Worker transactions found.")

        if processed_today == 0:
            # Fact tools (delete_fact / save_fact / get_fact) and a few other
            # paths in llm_connector skip the Worker formatting step entirely,
            # so the extractor finds no [WORKER][*_RESPONSE] pair. Still send
            # Telegram so Master sees the run completed.
            date_str = datetime.datetime.now().strftime("%d/%m %H:%M")
            send_telegram_alert(
                f"E2E Test | {date_str}\n"
                f"Task ran successfully, but produced no auditable Worker pair "
                f"(likely a fact/memory tool that bypasses Worker formatting).\n"
                f"Ciel response: {str(response)[:300]}"
            )
            print("[-] No auditable pairs. Telegram sent. Exiting.")
            sys.exit(0)

        accepted_today = 0
        rejected_today = 0
        records_to_save = []

        print("[*] Sending to Judge...")
        evaluations, critique, recommendation = audit_batch_with_judge(pairs[:4])
        eval_map = {item["index"]: item for item in evaluations if "index" in item}

        for idx, pair in enumerate(pairs[:4]):
            audit = eval_map.get(idx)
            if not audit:
                rejected_today += 1
                print(f"  -> [{idx}] SKIP")
                continue

            classification = audit.get("classification", "WEAK").upper()
            print(f"  -> [{idx}] {classification}")

            if classification == "GOOD":
                accepted_today += 1
                records_to_save.append(format_chatml(pair["user_request"], pair["raw_result"], pair["original_response"]))
                if audit.get("paraphrase_1"):
                    records_to_save.append(format_chatml(pair["user_request"], pair["raw_result"], audit["paraphrase_1"]))
                if audit.get("paraphrase_2"):
                    records_to_save.append(format_chatml(pair["user_request"], pair["raw_result"], audit["paraphrase_2"]))
            elif classification == "WEAK" and audit.get("rewrite"):
                accepted_today += 1
                records_to_save.append(format_chatml(pair["user_request"], pair["raw_result"], audit["rewrite"]))
            else:
                rejected_today += 1

        if records_to_save:
            save_to_finetune_file(records_to_save)
            print(f"[+] Wrote {len(records_to_save)} training records.")

        current_dataset_size = get_dataset_size()
        state["last_processed_seek"] = new_seek
        state["total_processed"] += processed_today
        state["total_accepted"] += accepted_today
        state["total_rejected"] += rejected_today
        state["cumulative_dataset_size"] = current_dataset_size
        save_state(state)

        # Build simple plain-text message
        date_str = datetime.datetime.now().strftime("%d/%m %H:%M")
        lines = [
            f"E2E Test | {date_str}",
            f"Processed: {processed_today} | Accepted: {accepted_today} | Rejected: {rejected_today} | Dataset: {current_dataset_size}",
            "",
        ]
        for idx, pair in enumerate(pairs[:4]):
            audit = eval_map.get(idx)
            verdict = audit.get("classification", "SKIP").upper() if audit else "SKIP"
            category = _classify_task(pair)
            lines.append(f"[{idx+1}] ({category}) {pair['user_request'][:70]}")
            lines.append(f"     Worker: {pair['original_response'][:80]}")
            lines.append(f"     Judge: {verdict}")
        lines.append("")
        lines.append(f"Critique: {(critique or 'N/A')[:200]}")
        lines.append(f"Rec: {(recommendation or 'N/A')[:150]}")

        send_telegram_alert("\n".join(lines))
        print("\n[PHASE 2 COMPLETE]")
        print("=" * 60)

    except Exception as e:
        print(f"[-] Phase 2 failed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
