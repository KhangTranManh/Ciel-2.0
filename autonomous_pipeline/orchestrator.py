import os
import sys
import time
import datetime
from pathlib import Path
from dotenv import load_dotenv

# Set up paths so we can run from anywhere
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

# Load environment variables
load_dotenv(BASE_DIR / ".env")

from autonomous_pipeline.data_pipeline import load_state, save_state


def run_task_generator():
    """Import and execute Simulated Master generator in-memory."""
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[+] [{now_str}] Triggering Simulated Master Task Generator...")
    try:
        from autonomous_pipeline.task_generator import main as run_gen
        run_gen()
    except Exception as e:
        print(f"[-] Error running task generator: {e}")


def run_chaos_injector():
    """Inject a synthetic adversarial scenario into thoughts.log (no Gemini, no Ciel)."""
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[+] [{now_str}] Triggering Chaos Injector (adversarial dataset diversity)...")
    try:
        from autonomous_pipeline.chaos_injector import run_chaos_cycle
        run_chaos_cycle()
    except Exception as e:
        print(f"[-] Error running chaos injector: {e}")


def run_data_pipeline():
    """Import and execute Nightly MLOps Auditor in-memory."""
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[+] [{now_str}] Triggering Nightly Audit & Judge Pipeline...")
    try:
        from autonomous_pipeline.data_pipeline import main as run_audit
        run_audit()
    except Exception as e:
        print(f"[-] Error running data pipeline: {e}")


def main():
    print("=" * 60)
    print("      CIEL 2.0 AUTONOMOUS PIPELINE DAEMON SCHEDULER")
    print("=" * 60)
    print(f"[*] Scheduler Boot Time: {datetime.datetime.now()}")
    print("[*] Background daemon loop started. Monitoring time schedules...")
    # Force working directory to BASE_DIR to keep paths aligned
    os.chdir(BASE_DIR)
    
    # Notify Master via Telegram that Ciel's background brain clock is online
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if bot_token and chat_id:
        try:
            msg = "⚙️ *Ciel 2.0 Autonomous Scheduler Daemon* is now *ONLINE* in the background on your VPS."
            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            requests = sys.modules.get('requests')
            if not requests:
                import requests
            requests.post(url, json={"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"}, timeout=10)
        except Exception as e:
            print(f"[-] Telegram boot-up notification failed: {e}")
            
    while True:
        try:
            state = load_state()
            now = datetime.datetime.now()
            current_timestamp = time.time()
            today_date = now.strftime("%Y-%m-%d")
            
            # 1. Simulated Master Task Generation Schedule (Every 30 minutes)
            # If never run, run once immediately on startup to verify.
            time_since_last_task = current_timestamp - state["last_task_gen_timestamp"]

            # 1800 seconds = 30 Minutes
            if state["last_task_gen_timestamp"] == 0.0 or time_since_last_task >= 1800:
                cycle_count = state.get("cycle_count", 0) + 1
                # Every 10th cycle, inject adversarial scenarios instead of running a real task.
                # Forces the dataset to cover error/empty/timeout edge cases the live pipeline
                # rarely produces on its own.
                is_chaos_cycle = (cycle_count % 10 == 0)

                print(
                    f"[*] Schedule Match: Cycle #{cycle_count} "
                    f"({'CHAOS' if is_chaos_cycle else 'NORMAL'}) — "
                    f"Elapsed: {time_since_last_task/60:.1f} min"
                )

                # Snapshot log position BEFORE Ciel (or Chaos) writes new entries
                log_path = BASE_DIR / "ciel_data" / "logs" / "thoughts.log"
                pre_task_seek = log_path.stat().st_size if log_path.exists() else 0

                if is_chaos_cycle:
                    run_chaos_injector()
                else:
                    run_task_generator()

                # Set seek cursor to pre-task position so Judge reads exactly the fresh entries
                state = load_state()
                state["last_processed_seek"] = pre_task_seek
                state["cycle_count"] = cycle_count
                save_state(state)

                # Immediately audit what Ciel/Chaos wrote and deliver Telegram assessment
                print("[*] Task complete. Running immediate Judge audit and Telegram alert...")
                run_data_pipeline()

                # Reload state in case pipeline updated metrics, then update timestamp
                state = load_state()
                state["last_task_gen_timestamp"] = time.time()
                save_state(state)
                
            # 2. Nightly Judge Audit & Augmentation Schedule (Runs daily at 23:00)
            if now.hour == 23 and state["last_audit_date"] != today_date:
                print(f"[*] Schedule Match: Triggering Nightly Audit (Hour: {now.hour})")
                run_data_pipeline()
                # Reload state to preserve updates made by the audit pipeline, then update date
                state = load_state()
                state["last_audit_date"] = today_date
                save_state(state)
                
            # Sleep for 60 seconds (zero CPU load)
            time.sleep(60)
            
        except KeyboardInterrupt:
            print("\n[-] Daemon stopped by Master.")
            if bot_token and chat_id:
                try:
                    msg = "⚠️ *Ciel 2.0 Autonomous Scheduler Daemon* has been *STOPPED* by Master."
                    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
                    import requests
                    requests.post(url, json={"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"}, timeout=10)
                except Exception:
                    pass
            break
        except Exception as e:
            print(f"[!] Error in daemon orchestrator loop: {e}")
            time.sleep(60)


if __name__ == "__main__":
    main()
