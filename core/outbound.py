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


_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_SEP_CELL_RE = re.compile(r"^\s*:?-+:?\s*$")
_HEADING_RE = re.compile(r"^\s*(#{1,6})\s+(.+?)\s*#*\s*$")
_BULLET_RE = re.compile(r"^\s*[-*•]\s+(.+)$")
_NUMBERED_RE = re.compile(r"^\s*\d+[.)]\s+(.+)$")
_RULE_RE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")

_CELL_STYLE = "border:1px solid #d0d0d0;padding:6px 10px"
_HEADING_SIZES = {1: 20, 2: 18, 3: 16, 4: 15, 5: 14, 6: 14}


def _inline(text: str) -> str:
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", text)


def _split_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _is_separator_row(line: str) -> bool:
    cells = _split_row(line)
    return bool(cells) and all(_TABLE_SEP_CELL_RE.match(c) for c in cells)


def _render_table(lines: list[str]) -> str:
    header = None
    aligns: list[str] = []
    body_lines = lines
    if len(lines) >= 2 and _is_separator_row(lines[1]):
        header = _split_row(lines[0])
        for cell in _split_row(lines[1]):
            c = cell.strip()
            if c.startswith(":") and c.endswith(":"):
                aligns.append("center")
            elif c.endswith(":"):
                aligns.append("right")
            else:
                aligns.append("left")
        body_lines = lines[2:]

    def cell_html(tag: str, value: str, idx: int) -> str:
        align = aligns[idx] if idx < len(aligns) else "left"
        return f'<{tag} style="{_CELL_STYLE};text-align:{align}">{_inline(value)}</{tag}>'

    parts = ['<table style="border-collapse:collapse;margin:0 0 12px">']
    if header is not None:
        cells = "".join(cell_html("th", v, i) for i, v in enumerate(header))
        parts.append(f'<thead><tr style="background:#f3f3f3">{cells}</tr></thead>')
    parts.append("<tbody>")
    for line in body_lines:
        if _is_separator_row(line):
            continue
        cells = "".join(cell_html("td", v, i) for i, v in enumerate(_split_row(line)))
        parts.append(f"<tr>{cells}</tr>")
    parts.append("</tbody></table>")
    return "".join(parts)


def _render_list(items: list[str], ordered: bool) -> str:
    tag = "ol" if ordered else "ul"
    lis = "".join(f'<li style="margin:0 0 4px">{_inline(i)}</li>' for i in items)
    return f'<{tag} style="margin:0 0 12px;padding-left:22px">{lis}</{tag}>'


def plaintext_to_html(text: str) -> str:
    """Render a plain/Markdown email body as simple inline-styled HTML.

    Gmail delivery is text/html, so Markdown the Worker writes (tables, headings,
    lists) would otherwise reach the recipient as raw pipes and dashes.
    """
    if not text or re.search(r"<(?:p|br|div|table|h[1-6]|ul|ol)\b", text, re.IGNORECASE):
        return text
    lines = html.escape(text).strip().split("\n")
    blocks: list[str] = []
    paragraph: list[str] = []

    def flush_paragraph():
        if paragraph:
            blocks.append(f'<p style="margin:0 0 12px">{"<br>".join(_inline(l.strip()) for l in paragraph)}</p>')
            paragraph.clear()

    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            flush_paragraph()
            i += 1
            continue

        if _TABLE_ROW_RE.match(line) and i + 1 < len(lines) and _TABLE_ROW_RE.match(lines[i + 1]):
            flush_paragraph()
            j = i
            while j < len(lines) and _TABLE_ROW_RE.match(lines[j]):
                j += 1
            blocks.append(_render_table(lines[i:j]))
            i = j
            continue

        heading = _HEADING_RE.match(line)
        if heading:
            flush_paragraph()
            size = _HEADING_SIZES[len(heading.group(1))]
            blocks.append(f'<p style="margin:16px 0 8px;font-size:{size}px;font-weight:bold">'
                          f'{_inline(heading.group(2))}</p>')
            i += 1
            continue

        if _RULE_RE.match(line):
            flush_paragraph()
            blocks.append('<hr style="border:none;border-top:1px solid #ddd;margin:12px 0">')
            i += 1
            continue

        for pattern, ordered in ((_BULLET_RE, False), (_NUMBERED_RE, True)):
            if pattern.match(line):
                flush_paragraph()
                items = []
                while i < len(lines) and pattern.match(lines[i]):
                    items.append(pattern.match(lines[i]).group(1))
                    i += 1
                blocks.append(_render_list(items, ordered))
                break
        else:
            paragraph.append(line)
            i += 1

    flush_paragraph()
    inner = "\n".join(blocks)
    return (
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;'
        f'line-height:1.55;color:#222">{inner}</div>'
    )
