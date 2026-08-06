"""HTML analysis report builder (generic file/text analysis → email_template)."""
from __future__ import annotations

import html as html_lib
import os
from datetime import date
from pathlib import Path

from langchain_core.tools import StructuredTool

BASE_DIR = Path(__file__).resolve().parent.parent.parent
TEMPLATE = BASE_DIR / "email_template" / "analysis_report.html"

REPORT_PROMPT = """
[REPORT ARMORY]
1. `build_analysis_report_html`: Fill the analysis HTML template from REAL source facts only.
   Returns full HTML string — write it with write_file (agent_output/…html) then
   send_telegram_document or send_gmail_html_message.
Never invent quotes/markers not present in the source tool output.
"""


def _esc(s: str) -> str:
    return html_lib.escape(s or "", quote=True)


def _bullets_to_html(text: str) -> str:
    """Turn plain bullets / newlines into simple HTML list items."""
    t = (text or "").strip()
    if not t:
        return "<p>N/A</p>"
    if "<li" in t.lower() or "<p" in t.lower() or "<ul" in t.lower():
        return t  # already HTML from Worker
    lines = [ln.strip().lstrip("•-*").strip() for ln in t.splitlines() if ln.strip()]
    if not lines:
        return f"<p>{_esc(t)}</p>"
    items = "".join(f"<li style='margin:0 0 6px'>{_esc(ln)}</li>" for ln in lines)
    return f"<ul style='margin:0;padding-left:18px'>{items}</ul>"


def build_analysis_report_html(
    title: str,
    source_path: str,
    source_type: str,
    summary: str,
    key_points: str,
    risks: str = "N/A",
    actions: str = "N/A",
    quotes: str = "",
    output_path: str = "",
) -> str:
    """Build styled analysis HTML from template. Values must come from real file content.

    If output_path is set (prefer agent_output/*.html), writes the file and returns
    a short confirmation with the path — so send_telegram_document can run next without
    a fragile shell/copy step (live: Brain built HTML then failed to persist it).
    """
    if not TEMPLATE.exists():
        return f"Error: template missing at {TEMPLATE}"

    # Never put full internal path in outward-facing label — basename only for display
    label = os.path.basename((source_path or "").replace("\\", "/")) or "source"
    src_type = (source_type or "text").strip().lower()
    if src_type not in ("text", "pdf", "image", "docx", "html", "other"):
        src_type = "other"

    raw = TEMPLATE.read_text(encoding="utf-8")
    tokens = {
        "TITLE": _esc(title or "Analysis report"),
        "DATE": _esc(str(date.today())),
        "SOURCE_TYPE": _esc(src_type),
        "SOURCE_LABEL": _esc(label),
        "SUMMARY": _esc(summary or "N/A").replace("\n", "<br>"),
        "KEY_POINTS": _bullets_to_html(key_points),
        "RISKS": _bullets_to_html(risks),
        "ACTIONS": _bullets_to_html(actions),
        "QUOTES": _esc(quotes or "N/A"),
    }
    html = raw
    for k, v in tokens.items():
        html = html.replace("{{" + k + "}}", v)

    out = (output_path or "").strip().replace("\\", "/")
    if out:
        if "telegram_uploads" in out.lower():
            return (
                "Error: refuse to write analysis HTML into telegram_uploads/. "
                "Use agent_output/your_report.html"
            )
        if not out.startswith("agent_output/") and not out.startswith("ciel_workspace/"):
            out = f"agent_output/{os.path.basename(out)}"
        if not out.endswith(".html"):
            out = out + ".html"
        dest = BASE_DIR / out
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(html, encoding="utf-8")
        return (
            f"HTML report written to {out} ({len(html)} chars). "
            f"Next: send_telegram_document(filepath=\"{out}\") "
            f"or send_gmail_html_message with this file's content."
        )
    return html


def get_report_tools() -> dict:
    tool = StructuredTool.from_function(
        func=build_analysis_report_html,
        name="build_analysis_report_html",
        description=(
            "Build a dark-card HTML analysis report (email_template/analysis_report.html). "
            "Args: title, source_path, source_type (text|pdf|image|docx), summary, key_points, "
            "risks, actions, quotes — fill ONLY from read_file/read_document/describe_image_file. "
            "ALWAYS pass output_path='agent_output/<name>.html' so the file is written. "
            "Then call send_telegram_document(filepath=that path) or email the HTML. "
            "Never use telegram_uploads/ as output_path."
        ),
    )
    return {"tools": [tool], "prompt": REPORT_PROMPT}
