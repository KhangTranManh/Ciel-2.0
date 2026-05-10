"""scheduler.py — Proactive background task manager for Ciel.

Design principles:
  - Zero-Token Standby: Python watches the clock, LLM sleeps until needed.
  - Direct Execution: Tools are called as raw Python/APIs, skipping the Brain.
  - Ghost Mode: Scheduled tasks do NOT pollute memory_bank.json or RAG.
  - Single API Call: Only the Worker is invoked once to format the digest.
"""
import time
import threading
import requests
from datetime import datetime
from pathlib import Path

# Lazy imports — only loaded when a task actually fires
_worker = None

def _get_worker():
    """Lazy-load the Worker so startup stays fast."""
    global _worker
    if _worker is None:
        from agent_system.models.worker import Worker
        _worker = Worker()
    return _worker


# ──────────────────────────────────────────────
#  RAW DATA GATHERERS (0 LLM tokens)
# ──────────────────────────────────────────────

def _fetch_unread_emails(max_results: int = 5) -> str:
    """Directly call Gmail API using existing credentials."""
    try:
        import inspect
        from langchain_google_community.gmail.utils import (
            build_resource_service,
            get_gmail_credentials,
        )
        base_dir = Path(__file__).resolve().parent.parent
        credentials_file = base_dir / "credentials.json"
        token_file = base_dir / "ciel_data" / "gmail_token.json"

        if not credentials_file.exists():
            return "[Gmail] Missing credentials.json"

        params = inspect.signature(get_gmail_credentials).parameters
        common_kwargs = {"token_file": str(token_file), "scopes": ["https://mail.google.com/"]}
        if "client_secrets_file" in params:
            creds = get_gmail_credentials(**common_kwargs, client_secrets_file=str(credentials_file))
        elif "client_sercret_file" in params:
            creds = get_gmail_credentials(**common_kwargs, client_sercret_file=str(credentials_file))
        else:
            return "[Gmail] Unsupported credential signature"

        service = build_resource_service(credentials=creds)
        results = service.users().messages().list(
            userId="me", q="is:unread", maxResults=max_results
        ).execute()

        messages = results.get("messages", [])
        if not messages:
            return "No unread emails."

        summaries = []
        for msg_meta in messages:
            msg = service.users().messages().get(
                userId="me", id=msg_meta["id"], format="metadata",
                metadataHeaders=["From", "Subject"]
            ).execute()
            headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
            sender = headers.get("From", "Unknown")
            subject = headers.get("Subject", "(no subject)")
            summaries.append(f"- From: {sender} | Subject: {subject}")

        return "\n".join(summaries)
    except Exception as e:
        return f"[Gmail Error] {e}"


def _fetch_market_price(symbol: str) -> str:
    """Directly call TwelveData API for Forex/Metals (same logic as trading_ops.py)."""
    import os
    api_key = os.getenv("TWELVEDATA_API_KEY")
    if not api_key:
        return f"{symbol}: Missing TWELVEDATA_API_KEY in .env"
    try:
        url = f"https://api.twelvedata.com/price?symbol={symbol}&apikey={api_key}"
        data = requests.get(url, timeout=10).json()
        if "price" in data:
            return f"{symbol}: {data['price']}"
        return f"{symbol}: No data available."
    except Exception as e:
        return f"{symbol}: API Error - {e}"


# ──────────────────────────────────────────────
#  TELEGRAM NOTIFIER
# ──────────────────────────────────────────────

def _send_telegram(message: str) -> bool:
    """Send message via Telegram Bot API."""
    import os
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not bot_token or not chat_id:
        return False

    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        resp = requests.post(url, json={
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "Markdown",
        }, timeout=10)
        return resp.status_code == 200
    except Exception as e:
        print(f"[Telegram Error] {e}")
        return False


# ──────────────────────────────────────────────
#  SCHEDULED TASKS
# ──────────────────────────────────────────────

def _morning_digest():
    """Run at 08:00 — gather data, format once, notify."""
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"\n[{ts}] [SCHEDULER] Running Morning Digest...")

    # 1. Gather raw data (0 tokens)
    emails = _fetch_unread_emails(max_results=5)
    eurusd = _fetch_market_price("EUR/USD")
    xauusd = _fetch_market_price("XAU/USD")
    gbpusd = _fetch_market_price("GBP/USD")

    # 2. Format with Worker (1 API call)
    worker = _get_worker()
    prompt = (
        "You are Ciel. Write a concise Morning Digest for the Master.\n"
        "Include: date, top unread emails summary, forex/metals prices.\n"
        "Use markdown formatting. Be professional and brief.\n\n"
        f"Date: {datetime.now().strftime('%A, %B %d, %Y')}\n\n"
        f"Unread Emails:\n{emails}\n\n"
        f"Market Prices:\n{eurusd}\n{xauusd}\n{gbpusd}"
    )
    digest = worker.generate(prompt)

    # 3. Save to workspace
    workspace = Path(__file__).resolve().parent.parent / "ciel_workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    brief_path = workspace / "daily_brief.md"
    brief_path.write_text(digest, encoding="utf-8")

    # 4. Notify via Telegram
    sent = _send_telegram(digest)
    status = "sent to Telegram ✓" if sent else "saved to daily_brief.md (Telegram not configured)"
    print(f"[{datetime.now().strftime('%H:%M:%S')}] [SCHEDULER] Morning Digest {status}")


# ──────────────────────────────────────────────
#  SCHEDULER ENGINE
# ──────────────────────────────────────────────

class CielScheduler:
    """Lightweight background scheduler using the `schedule` library."""

    def __init__(self):
        try:
            import schedule
            self._schedule = schedule
        except ImportError:
            print("[SCHEDULER] Warning: 'schedule' library not installed. Run: pip install schedule")
            self._schedule = None

    def start_background(self):
        """Register tasks and start daemon thread."""
        if self._schedule is None:
            print("[SCHEDULER] Skipping — schedule library missing.")
            return

        # Register daily tasks
        self._schedule.every().day.at("08:00").do(_morning_digest)

        # Start daemon thread (dies when main process exits)
        thread = threading.Thread(target=self._run_loop, daemon=True)
        thread.start()
        print("[System] CielScheduler started. Morning Digest scheduled at 08:00 daily.")

    def _run_loop(self):
        """Check for pending tasks every 60 seconds."""
        while True:
            self._schedule.run_pending()
            time.sleep(60)

    def run_now(self, task_name: str = "morning_digest"):
        """Manually trigger a task for testing."""
        if task_name == "morning_digest":
            _morning_digest()
        else:
            print(f"[SCHEDULER] Unknown task: {task_name}")
