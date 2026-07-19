"""Tầng 1 — lightweight daily health check, reported via Telegram.

Deliberately NOT the full backtest suite (test_integration.py / test_hard_special.py):
those send REAL emails and burn 40-60 LLM calls per run — fine for an occasional/manual
regression check, wrong for something that fires automatically every day. This script
answers a narrower question — "is the system still alive?" — for near-zero cost:

  1. CielCore boots (persona loads, all skill packs load, Brain/Worker/Middleware
     clients construct) — normally free, no network call yet. EXCEPTION: CielCore
     also runs `_check_bootup_cleanse()` in __init__, which fires a real Worker call
     (Daily Summary) if `ciel_data/memory_bank.json`'s mtime is before today. That
     file is gitignored, so on a FRESH GitHub Actions checkout it won't exist and
     this never fires there — it only shows up on a local run with stale state.
  2. Exactly ONE guaranteed real LLM call: Router.route() on a trivial chat message,
     proving the configured Brain provider is reachable and still routes correctly.
  3. Reports which skill modules loaded (so a silent Gmail/trading credential failure
     shows up as a number dropping, not as an exception days later).

No tool is executed, no email is sent, no Worker/Middleware call is made. Reports via
Telegram using `send_telegram_message()` directly (skills/external/telegram_ops.py) —
bypasses ToolManager entirely since this script IS the trigger, not a conversation.

Run manually:      python -m scripts.health_check
Run in CI:          see .github/workflows/health_check.yml
Exit code: 0 on success, 1 on failure (drives CI pass/fail).
"""
from __future__ import annotations

import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skills.external.telegram_ops import send_telegram_message


def _report(lines: list[str], ok: bool) -> None:
    """Send the check result to Telegram (if configured) and print it either way —
    CI logs still show the result even without TELEGRAM_BOT_TOKEN/CHAT_ID set."""
    header = "🟢 *Ciel Health Check*" if ok else "🔴 *Ciel Health Check — FAILED*"
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    message = f"{header} — {stamp}\n" + "\n".join(lines)
    print(message.replace("*", ""))
    sent = send_telegram_message(message)
    if not sent:
        print("[health_check] Telegram not configured or send failed — "
              "see console output above for the result.")


def main() -> int:
    lines: list[str] = []
    try:
        t0 = time.time()
        from core.llm_connector import CielCore
        core = CielCore()
        init_s = time.time() - t0
        lines.append(f"✅ CielCore initialized ({init_s:.1f}s)")

        manifest = core.tool_manager.get_skills_manifest()
        total_tools = sum(m.get("tool_count", 0) for m in manifest)
        lines.append(f"🛠 {total_tools} tools across {len(manifest)} skill modules")
        # Flag modules that loaded 0 tools — usually a missing credential/dependency,
        # not necessarily a failure (e.g. gmail_ops without credentials.json is
        # EXPECTED on a fresh CI runner unless Gmail secrets were configured).
        empty = [m["module"] for m in manifest if m.get("tool_count", 0) == 0]
        if empty:
            lines.append(f"⚠️ 0 tools loaded: {', '.join(empty)} (check credentials/deps if unexpected)")

        t1 = time.time()
        decision = core.router.route("Hello, are you online?", core._tool_list_str, core.chat_history)
        route_s = time.time() - t1
        action = decision.get("action", "?")
        if action not in ("chat", "tool", "code", "multi_tool"):
            raise RuntimeError(f"Router returned an unrecognized action: {action!r}")
        from agent_system.config import BRAIN_MODEL
        lines.append(f"✅ Brain routed correctly ({route_s:.1f}s) — action={action}, model={BRAIN_MODEL}")

        _report(lines, ok=True)
        return 0

    except Exception as e:
        lines.append(f"❌ {type(e).__name__}: {e}")
        print(traceback.format_exc())
        _report(lines, ok=False)
        return 1


if __name__ == "__main__":
    sys.exit(main())
