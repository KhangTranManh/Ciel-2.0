"""Mask credentials in text that leaves the process.

Exception messages from `requests` include the full request URL, and the Telegram
Bot API carries the bot token inside that URL. Anything printed to the container
log, returned as a tool result (which reaches thoughts.log and the models), or sent
to a front end passes through `redact_secrets` first.
"""
from __future__ import annotations

import os
import re

_TELEGRAM_URL_TOKEN_RE = re.compile(r"(/(?:file/)?bot)\d+:[A-Za-z0-9_-]+")
_BARE_TELEGRAM_TOKEN_RE = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{30,}\b")
_SECRET_ENV_NAME_RE = re.compile(r"(?:KEY|TOKEN|SECRET|PASSWORD|PASS)$|SECRET|PASSWORD", re.IGNORECASE)
_MIN_SECRET_LEN = 8
MASK = "***"


def _secret_env_values() -> list[str]:
    values = {
        value for name, value in os.environ.items()
        if value and len(value) >= _MIN_SECRET_LEN and _SECRET_ENV_NAME_RE.search(name)
    }
    return sorted(values, key=len, reverse=True)


def redact_secrets(text) -> str:
    s = "" if text is None else str(text)
    if not s:
        return s
    for value in _secret_env_values():
        s = s.replace(value, MASK)
    s = _TELEGRAM_URL_TOKEN_RE.sub(r"\1" + MASK, s)
    return _BARE_TELEGRAM_TOKEN_RE.sub(MASK, s)
