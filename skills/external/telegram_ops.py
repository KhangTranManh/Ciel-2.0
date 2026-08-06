import os
from pathlib import Path
import requests
from langchain_core.tools import StructuredTool

TELEGRAM_SYSTEM_PROMPT = """
[TELEGRAM NOTIFICATION ARMORY]
You possess tools to message the Master via Telegram.

1. `send_telegram`: Plain-text notification (short summary).
2. `send_telegram_document`: Send a FILE (e.g. .html report under agent_output/ or ciel_workspace/).
   USE THIS when Master asks for HTML report / visual file via Telegram.

[RULES]
1. Text for short notes; document for HTML/PDF/file deliveries.
2. Never claim sent without a successful tool result (message_id / document ok).
3. Prefer agent_output/*.html for reports — do NOT write into telegram_uploads/.
"""

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def send_telegram_message(message: str, *, return_detail: bool = False):
    """Send plain text via Telegram bot. Returns bool, or (ok, detail) if return_detail."""
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not bot_token or not chat_id:
        detail = "Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID in .env"
        return (False, detail) if return_detail else False

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    text = (message or "").strip()
    if not text:
        detail = "Empty message"
        return (False, detail) if return_detail else False
    if len(text) > 4000:
        text = text[:3990] + "…"

    def _post(payload: dict):
        try:
            response = requests.post(url, json=payload, timeout=15)
            try:
                data = response.json()
            except Exception:
                data = {}
            if response.status_code == 200 and data.get("ok"):
                mid = (data.get("result") or {}).get("message_id", "?")
                return True, f"ok message_id={mid}"
            desc = data.get("description") or response.text[:200]
            return False, f"HTTP {response.status_code}: {desc}"
        except Exception as e:
            return False, f"exception: {e}"

    ok, detail = _post({"chat_id": chat_id, "text": text})
    if not ok:
        ok2, detail2 = _post({"chat_id": chat_id, "text": text, "parse_mode": "HTML"})
        if ok2:
            ok, detail = True, detail2 + " (html parse_mode)"
        else:
            detail = f"plain=[{detail}]; html=[{detail2}]"

    if return_detail:
        return ok, detail
    return ok


def send_telegram_document(filepath: str, caption: str = "", *, return_detail: bool = False):
    """Send a local file as Telegram document (for HTML reports, PDFs, etc.)."""
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        detail = "Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID in .env"
        return (False, detail) if return_detail else False

    path = Path(filepath)
    if not path.is_absolute():
        # Resolve relative to project root, then workspace-safe paths
        cand = BASE_DIR / filepath
        if not cand.exists():
            cand = BASE_DIR / "ciel_workspace" / filepath.replace("ciel_workspace/", "")
        path = cand

    # Safety: only under project, block escaping
    try:
        resolved = path.resolve()
        root = BASE_DIR.resolve()
        if root not in resolved.parents and resolved != root:
            detail = f"Path outside project root: {filepath}"
            return (False, detail) if return_detail else False
    except Exception as e:
        detail = f"Bad path: {e}"
        return (False, detail) if return_detail else False

    if not resolved.is_file():
        detail = f"File not found: {filepath}"
        return (False, detail) if return_detail else False

    # Block sending raw inbound uploads as "reports" by accident is OK; allow any project file
    url = f"https://api.telegram.org/bot{bot_token}/sendDocument"
    cap = (caption or "").strip()
    if len(cap) > 1000:
        cap = cap[:990] + "…"

    try:
        with open(resolved, "rb") as f:
            files = {"document": (resolved.name, f)}
            data = {"chat_id": chat_id}
            if cap:
                data["caption"] = cap
            response = requests.post(url, data=data, files=files, timeout=60)
        try:
            body = response.json()
        except Exception:
            body = {}
        if response.status_code == 200 and body.get("ok"):
            mid = (body.get("result") or {}).get("message_id", "?")
            return (True, f"ok document message_id={mid} file={resolved.name}") if return_detail else True
        desc = body.get("description") or response.text[:200]
        detail = f"HTTP {response.status_code}: {desc}"
        return (False, detail) if return_detail else False
    except Exception as e:
        detail = f"exception: {e}"
        return (False, detail) if return_detail else False


def get_telegram_tools() -> dict:
    tools = []

    def send_telegram(message: str) -> str:
        success, detail = send_telegram_message(message, return_detail=True)
        if success:
            return f"Message sent to Telegram successfully. ({detail})"
        return f"Failed to send Telegram message: {detail}"

    def send_telegram_document_tool(filepath: str, caption: str = "") -> str:
        """Send a file (HTML report, PDF, etc.) to Master via Telegram."""
        success, detail = send_telegram_document(filepath, caption=caption, return_detail=True)
        if success:
            return f"Document sent to Telegram successfully. ({detail})"
        return f"Failed to send Telegram document: {detail}"

    tools.append(StructuredTool.from_function(
        func=send_telegram,
        name="send_telegram",
        description="Send a short plain-text message to Master's Telegram.",
    ))
    tools.append(StructuredTool.from_function(
        func=send_telegram_document_tool,
        name="send_telegram_document",
        description=(
            "Send a FILE to Master's Telegram (HTML report, PDF, txt). "
            "Args: filepath (required, e.g. agent_output/analysis.html), caption (optional). "
            "USE THIS for HTML visual reports after write_file. "
            "Do not write reports into telegram_uploads/."
        ),
    ))

    return {"tools": tools, "prompt": TELEGRAM_SYSTEM_PROMPT}
