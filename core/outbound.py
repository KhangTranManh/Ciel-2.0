"""Deterministic outbound-delivery helpers.

This module deliberately contains no LLM, tool, or persistence dependency.  It
owns the stable transformations used by ``CielCore`` before a message can leave
the process: delivery identity, recipient normalization, and plain-text email
rendering.  Safety/permission decisions remain in ``llm_connector.CielCore``.
"""
from __future__ import annotations

import html
import re


OUTBOUND_KEYS = {
    "send_gmail_message": "to",
    "send_gmail_html_message": "to",
    "reply_to_email": "message_id",
    "send_telegram": None,
    "send_telegram_document": None,
}


def delivery_key(tool_name: str, tool_args: dict | None) -> str | None:
    """Return the per-turn idempotency identity for an outbound delivery."""
    if tool_name not in OUTBOUND_KEYS:
        return None
    arg = OUTBOUND_KEYS[tool_name]
    if arg is None:
        return tool_name
    target = str((tool_args or {}).get(arg) or "").strip().lower()
    return f"{tool_name}:{target}" if target else None


def recipients_lowered(value) -> list[str]:
    """Normalize Gmail's scalar-or-list ``to`` field for membership checks."""
    if isinstance(value, list):
        return [str(item).strip().lower() for item in value if str(item).strip()]
    return [str(value).strip().lower()] if str(value or "").strip() else []


def plaintext_to_html(text: str) -> str:
    """Preserve paragraphs in text/html Gmail delivery without rewriting HTML."""
    if not text or re.search(r"<(?:p|br|div|table|h[1-6]|ul|ol)\b", text, re.IGNORECASE):
        return text
    escaped = html.escape(text)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    paragraphs = [
        f'<p style="margin:0 0 12px">{block.strip().replace(chr(10), "<br>")}</p>'
        for block in re.split(r"\n\s*\n", escaped.strip())
        if block.strip()
    ]
    inner = "\n".join(paragraphs)
    return (
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;'
        f'line-height:1.55;color:#222">{inner}</div>'
    )
