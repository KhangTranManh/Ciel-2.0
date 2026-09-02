"""Remove private material before evidence reaches either model."""
from __future__ import annotations

import re


_REPLACEMENTS = (
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "<REDACTED_PRIVATE_KEY>"),
    (re.compile(r"\b(?:sk|ghp|github_pat)-?[A-Za-z0-9_\-]{20,}\b"), "<REDACTED_TOKEN>"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"), "<REDACTED_API_KEY>"),
    (re.compile(r"(?i)\b(?:api[_-]?key|token|password|secret|authorization)\s*[:=]\s*[^\s,;]+"), "<REDACTED_SECRET_ASSIGNMENT>"),
    (re.compile(r"\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b", re.I), "<REDACTED_EMAIL>"),
    (re.compile(r"https?://[^\s)>\]]+", re.I), "<REDACTED_URL>"),
    (re.compile(r"(?i)(?:[A-Z]:\\|/(?:home|srv|root|users)/)[^\s\"']+"), "<REDACTED_PATH>"),
)

_SECRET_MARKERS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----", re.I),
    re.compile(r"\b(?:sk|ghp|github_pat)-?[A-Za-z0-9_\-]{20,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"),
    re.compile(r"(?i)\b(?:api[_-]?key|token|password|secret|authorization)\s*[:=]\s*[^\s,;]+"),
)


def sanitize_text(value: str, max_chars: int = 1200) -> str:
    """Return bounded evidence with common private values removed."""
    text = str(value).replace("\x00", "")
    for pattern, replacement in _REPLACEMENTS:
        text = pattern.sub(replacement, text)
    text = "\n".join(line[:500] for line in text.splitlines()[:20])
    return text[:max_chars]


def assert_no_secret_material(value: str) -> None:
    """Reject model output that appears to contain a secret value."""
    for pattern in _SECRET_MARKERS:
        if pattern.search(value):
            raise ValueError("Candidate contains secret-like material")
