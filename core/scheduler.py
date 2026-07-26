"""scheduler.py — Proactive background task manager for Ciel.

Design principles:
  - Zero-Token Standby: Python watches the clock, LLM sleeps until needed.
  - Direct Execution: Tools are called as raw Python/APIs, skipping the Brain.
  - Ghost Mode: Scheduled tasks do NOT pollute memory_bank.json or RAG.
  - Single API Call: Only the Worker is invoked once to format the digest.

Data gatherers are imported from their respective skill modules
instead of being duplicated here. This ensures any API logic changes
(e.g. new fallback providers, credential handling) stay in one place.
"""
import time
import threading
from datetime import datetime
from pathlib import Path

# Import raw functions from skill modules (no duplication)
from skills.external.telegram_ops import send_telegram_message as _send_telegram
from skills.external.trading_ops import fetch_market_price
from core.notifier import Notification, NOTIFY

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
    """Directly call Gmail API using existing credentials.
    
    Note: Gmail credential logic is complex and tightly coupled to
    langchain_google_community internals, so this remains here rather
    than being extracted into gmail_ops.py (which wraps the LangChain
    GmailToolkit and doesn't expose a raw fetch function).
    """
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


# ──────────────────────────────────────────────
#  SCHEDULED TASKS
# ──────────────────────────────────────────────

def _morning_digest():
    """Run at 08:00 — gather data, format once, notify."""
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"\n[{ts}] [SCHEDULER] Running Morning Digest...")

    # 1. Gather raw data (0 tokens)
    emails = _fetch_unread_emails(max_results=5)
    eurusd = fetch_market_price("EUR/USD")
    xauusd = fetch_market_price("XAU/USD")
    gbpusd = fetch_market_price("GBP/USD")

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


def _build_digest_text() -> str:
    """Gather + format the brief, and write it to the workspace. Returns the text.

    Split out of `_morning_digest` so the same work can be delivered two ways without
    being written twice: the legacy clock task keeps pushing straight to Telegram, while
    the Tier-6 trigger below hands the text to the Notifier and lets IT choose a channel.
    """
    emails = _fetch_unread_emails(max_results=5)
    prices = [fetch_market_price(s) for s in ("EUR/USD", "XAU/USD", "GBP/USD")]
    worker = _get_worker()
    digest = worker.generate(
        "You are Ciel. Write a concise Morning Digest for the Master.\n"
        "Include: date, top unread emails summary, forex/metals prices.\n"
        "Use markdown formatting. Be professional and brief.\n\n"
        f"Date: {datetime.now().strftime('%A, %B %d, %Y')}\n\n"
        f"Unread Emails:\n{emails}\n\n"
        f"Market Prices:\n" + "\n".join(prices))
    try:
        workspace = Path(__file__).resolve().parent.parent / "ciel_workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "daily_brief.md").write_text(digest, encoding="utf-8")
    except Exception:
        pass                        # the brief is still worth delivering if the file fails
    return digest


def make_morning_digest_check():
    """The 08:00 brief, expressed as a Tier-6 trigger like everything else.

    This is the one check that spends tokens — one Worker call, once a day, exactly as
    before. The gain is not cost, it is that the brief now goes through the same
    Notifier as every other finding: it obeys the daily budget, it routes to whichever
    channel the Master is actually watching instead of always Telegram, and it is
    subject to the same cooldown rather than being able to double-send.
    """
    def check(now: float):
        text = _build_digest_text()
        if not text or not text.strip():
            return None
        return Notification(
            key=f"morning_digest:{datetime.fromtimestamp(now).strftime('%Y-%m-%d')}",
            title="Bản tin sáng",
            detail=text.strip()[:1500],
            action="Đọc lướt; bảo Ciel nếu muốn đào sâu mục nào.",
            urgency=NOTIFY,
            trigger="morning_digest",
            created_at=now,
        )
    return check


# ──────────────────────────────────────────────
#  SCHEDULER ENGINE
# ──────────────────────────────────────────────

class CielScheduler:
    """Lightweight background scheduler using the `schedule` library."""

    def __init__(self):
        self.cleanse_callback = None
        # TIER 6: condition-based proactivity. The engine rides this existing daemon
        # thread rather than starting its own — one background thread is easier to
        # reason about, and every trigger already throttles itself via `every_seconds`.
        self.trigger_engine = None
        # Called once inside the daemon thread to flag it as an unattended context.
        self.mark_unattended = None
        try:
            import schedule
            self._schedule = schedule
        except ImportError:
            print("[SCHEDULER] Warning: 'schedule' library not installed. Run: pip install schedule")
            self._schedule = None

    def start_background(self):
        """Register clock tasks and start the daemon thread.

        A missing `schedule` library disables the clock tasks only. Tier-6 triggers are
        plain Python and must still run — tying them to an optional dependency would
        make proactivity silently vanish on a fresh install.
        """
        # When the Tier-6 engine owns the digest, the clock task must NOT also register
        # it — two mechanisms delivering the same brief is a double-send, and the
        # Notifier cannot dedupe what never passes through it.
        engine_owns_digest = any(
            t.name == "morning_digest" for t in getattr(self.trigger_engine, "triggers", []))
        if self._schedule is not None:
            if not engine_owns_digest:
                self._schedule.every().day.at("08:00").do(_morning_digest)
            self._schedule.every().day.at("23:00").do(self._trigger_cleanse)
        else:
            print("[SCHEDULER] 'schedule' library missing — clock tasks disabled.")

        if self._schedule is None and self.trigger_engine is None:
            return                              # nothing to run; don't spawn an idle thread

        # Start daemon thread (dies when main process exits)
        thread = threading.Thread(target=self._run_loop, daemon=True)
        thread.start()
        bits = []
        if self._schedule is not None:
            bits.append("Morning Digest at 08:00")
        if self.trigger_engine is not None:
            names = ", ".join(t.name for t in self.trigger_engine.triggers) or "none"
            bits.append(f"triggers: {names}")
        print(f"[System] CielScheduler started ({'; '.join(bits)}).")

    def _run_loop(self):
        """Check for pending work every 60 seconds."""
        # Mark THIS thread as unsupervised, once. Everything the scheduler runs from
        # here on inherits it, so any risky tool reached from a trigger defers instead
        # of prompting an empty room. Set inside the thread (not before starting it)
        # because the flag is thread-local — see CielCore.unattended.
        if self.mark_unattended:
            try:
                self.mark_unattended()
            except Exception:
                pass
        while True:
            if self._schedule is not None:
                try:
                    self._schedule.run_pending()
                except Exception as e:
                    print(f"[SCHEDULER] clock task error: {e}")
            if self.trigger_engine is not None:
                try:
                    # The engine absorbs per-trigger errors itself; this guard only
                    # covers the engine failing wholesale, which must not kill the
                    # thread and take the clock tasks down with it.
                    self.trigger_engine.tick(time.time())
                except Exception as e:
                    print(f"[SCHEDULER] trigger engine error: {e}")
            time.sleep(60)

    def _trigger_cleanse(self):
        if self.cleanse_callback:
            self.cleanse_callback(reason="nightly")

    def run_now(self, task_name: str = "morning_digest"):
        """Manually trigger a task for testing."""
        if task_name == "morning_digest":
            _morning_digest()
        else:
            print(f"[SCHEDULER] Unknown task: {task_name}")
