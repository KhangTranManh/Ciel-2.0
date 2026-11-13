"""email_support.py — outbound/inbound email helpers extracted from CielCore.

These were methods on the CielCore god-object; they are now module functions that
take the core instance as ``core`` (same pattern as core/harness/turn.py). CielCore
keeps thin delegating wrappers so every existing ``self._x(...)`` call site is
unchanged, and behaviour is identical. Class-level constants
(_EMAIL_BODY_CAP_*, _MESSAGE_ID_RE, _MIDDLEWARE_EMAIL_TOOLS) stay on the class and
are reached through ``core``.
"""
from __future__ import annotations

import json
import re
import unicodedata

from agent_system.config import MIDDLEWARE_SCOPE, MIDDLEWARE_MAX_PASSES
from agent_system.utils.logger import log
from core.harness.signals import _EMAIL_RE

def compact_email_result(core, text: str) -> str:
    """Parse email JSON or formatted search_gmail blocks; keep ALL hits visible.

    After gmail_ops wraps search as prose with `message_id:` lines, the old
    JSON-only path fell through to text[:2000] and silently dropped emails 2–N
    (live P0.3: list max_results=5 but Worker only saw Render).
    """
    if not text:
        return text
    try:
        emails = json.loads(text)
        if not isinstance(emails, list):
            return core._strip_html(text)[:8000]

        per_body_cap = core._EMAIL_BODY_CAP_SINGLE if len(emails) <= 1 else core._EMAIL_BODY_CAP_DIGEST
        compact = []
        for em in emails:
            body_raw = em.get("body", "")
            body_clean = core._strip_html(body_raw)[:per_body_cap]
            compact.append({
                "id": em.get("id") or em.get("message_id") or "",
                "sender": em.get("sender") or em.get("from") or "",
                "subject": em.get("subject", ""),
                "body": body_clean,
            })
        return json.dumps(compact, ensure_ascii=False, indent=1)
    except (json.JSONDecodeError, TypeError):
        pass

    # Formatted multi-block from skills.external.gmail_ops._format_gmail_search_results
    if re.search(r"\[\d+\]\s*message_id:", text):
        parts = re.split(r"\n(?=\[\d+\]\s*message_id:)", text.strip())
        n = len(parts)
        per_cap = core._EMAIL_BODY_CAP_SINGLE if n <= 1 else max(core._EMAIL_BODY_CAP_DIGEST, 220)
        out = []
        for p in parts:
            p = p.strip()
            if not p:
                continue
            # Soft-cap body line only; keep header lines (id/from/subject)
            lines = p.splitlines()
            kept = []
            body_budget = per_cap
            for ln in lines:
                if re.match(r"\s*body\s*:", ln, re.I):
                    prefix, _, rest = ln.partition(":")
                    rest = rest.strip()
                    if len(rest) > body_budget:
                        rest = rest[:body_budget] + "…"
                    kept.append(f"{prefix}: {rest}")
                else:
                    kept.append(ln)
            out.append("\n".join(kept))
        joined = "\n\n".join(out)
        # Hard ceiling so Worker still sees many short items
        return joined[:10000] if len(joined) > 10000 else joined

    return core._strip_html(text)[:8000]

def email_delivery_note(core, send_args: dict, send_res: str, user_input: str) -> str:
    """The only place the Master is told an email went out — built from the real
    send result and a Gmail re-check, never from model prose."""
    to = send_args.get("to")
    to = ", ".join(map(str, to)) if isinstance(to, list) else str(to or "").strip()
    subject = str(send_args.get("subject") or "").strip()
    vi = core._detect_language(user_input) == "Vietnamese"
    send_res = send_res or ""

    match = core._MESSAGE_ID_RE.search(send_res)
    if not match:
        if send_res.lstrip().startswith("[CANCELLED]"):
            return (f"❌ Chưa gửi email tới {to} — Master đã từ chối." if vi
                    else f"❌ Email to {to} was not sent — you declined it.")
        reason = send_res.strip().splitlines()[0][:200] if send_res.strip() else "no Message Id"
        return (f"❌ Email tới {to} CHƯA được gửi: {reason}" if vi
                else f"❌ Email to {to} was NOT sent: {reason}")

    mid = match.group(1)
    verified = core._verify_sent_email(mid)
    core._log_thought("TOOL", "email_send_verified", f"{mid}: {verified}")
    subj = ((f" — tiêu đề “{subject}”" if vi else f' — subject "{subject}"') if subject else "")
    label = "Nội dung đã gửi:" if vi else "Content sent:"
    if verified:
        note = (f"✅ Đã gửi email tới {to}{subj}. Đã kiểm tra lại trong Gmail (Message Id: {mid})."
                if vi else f"✅ Email sent to {to}{subj}. Re-checked in Gmail (Message Id: {mid}).")
    elif verified is None:
        note = (f"✅ Gmail đã nhận email tới {to}{subj} (Message Id: {mid}); chưa kiểm tra lại được "
                f"vì công cụ đọc Gmail không khả dụng." if vi else
                f"✅ Gmail accepted the email to {to}{subj} (Message Id: {mid}); it could not be "
                f"re-checked because the Gmail reader is unavailable.")
    else:
        note = (f"⚠️ Gmail báo đã gửi email tới {to}{subj} (Message Id: {mid}), nhưng kiểm tra lại "
                f"không thấy thư. Master nên xem lại mục Đã gửi." if vi else
                f"⚠️ Gmail reported the email to {to}{subj} as sent (Message Id: {mid}), but the "
                f"re-check could not find it. Please check your Sent folder.")
    return f"{note}\n\n{label}"

def sanitize_outbound_email(body: str, keep_paths: bool = False) -> str:
    """Strip internal/meta content from a synthesized body.

    Removes chain-of-thought (`[COGNITION]`), persona structural tag prefixes,
    signature placeholders, and self-referential "email sent / Message Id /
    email body:" scaffolding. Operates line-by-line so the real report content
    (prices, evaluation, greeting) is preserved.

    keep_paths=False (default, for outbound EMAIL): also strips internal file
    paths — they must never leak to external recipients.
    keep_paths=True (for synthesized FILE reports written into the workspace):
    internal paths are harmless inside a local file, so they are left intact.
    """
    if not body:
        return body

    # 1a) Remove the [COGNITION] LINE only (tag + any same-line reasoning). We do
    #     NOT consume following lines: the Worker often uses [COGNITION] as a bare
    #     header immediately followed by real report data (prices, dates), and the
    #     old paragraph-wide removal ate that data. Rule 8 of the synthesis prompt
    #     already discourages multi-line reasoning here.
    text = re.sub(r"(?m)^[^\n]*\[COGNITION\][^\n]*\n?", "", body)

    # 1b) Remove remaining persona structural tag prefixes: "📊 [MARKET_DATA]", "📈 [TECHNICAL]", etc.
    text = re.sub(r"[\U0001F300-\U0001FAFF☀-➿]*\s*\[[A-Z_]+\]\s*", "", text)

    # 1c) Replace signature placeholders the Worker/Brain sometimes leaves in
    #     ("[Your Name]", "[Ký tên]") with the actual sender name.
    text = re.sub(
        r"\[\s*(?:your name|k[yý] t[eê]n|ch[uữ] k[yý]|t[eê]n c[uủ]a b[aạ]n|name|signature|sender)\s*\]",
        "Ciel", text, flags=re.IGNORECASE)

    # Diacritic-insensitive helper for matching Vietnamese meta phrases.
    # NOTE: "đ"/"Đ" are distinct letters, not composed diacritics, so NFKD does
    # NOT reduce them to "d" — map them explicitly, otherwise "Chủ đề" folds to
    # "chu đe" and never matches the "chu de" marker (leaving a "Subject:" line).
    def _fold(s: str) -> str:
        s = s.replace("đ", "d").replace("Đ", "D")
        nfkd = unicodedata.normalize("NFKD", s)
        return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()

    # Internal file paths: strip just the path TOKEN (keep the rest of the line),
    # so a legitimate sentence that merely mentions a path is not lost entirely.
    # Skipped entirely for file reports (keep_paths=True).
    path_re = None if keep_paths else re.compile(r"\b(?:agent_output|ciel_workspace)/[\w./\-]+")

    # 2) Drop lines that are purely internal meta / self-reference.
    drop_markers = (
        "message id", "email sent", "email da duoc", "da gui den", "email da gui",
        "da duoc soan va gui", "chua duoc gui", "khong co tool", "no tool available",
        "noi dung email la",
    )
    # Label / scaffold lines to drop: nested-email labels, internal notes, and a
    # redundant "Subject:" line (the real subject is a separate header field).
    label_re = re.compile(r"^\**\s*(email body|email tom tat|noi dung email|luu y|subject|chu de)\b\s*:?", re.IGNORECASE)

    kept = []
    for line in text.splitlines():
        if path_re is not None:
            line = path_re.sub("", line)             # remove internal path tokens
            line = re.sub(r"[ \t]{2,}", " ", line)   # tidy gaps left behind
        folded = _fold(line)
        if any(m in folded for m in drop_markers):
            continue
        if label_re.match(folded):
            continue
        kept.append(line)

    # 3) Collapse leftover blank runs and leading separators/blank lines.
    cleaned = "\n".join(kept)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = re.sub(r"^\s*(-{3,}\s*\n)+", "", cleaned)
    return cleaned.strip()

def middleware_review(core, user_input: str, tool_name: str, body: str) -> str:
    """Third-tier semantic check on outbound content (see agent_system/models/middleware.py).

    Runs AFTER the deterministic sanitizer, BEFORE the safety-gate preview —
    so if Middleware revises the body, the Master's Y/N prompt shows the final
    version that will actually be sent, not a stale draft.

    Fail-open by design: disabled, out-of-scope, or erroring Middleware all
    return the body unchanged. This is a backstop ON TOP OF the deterministic
    rules, never a replacement — a Middleware hiccup must not block delivery.
    """
    if not core.middleware or not body:
        return body
    if MIDDLEWARE_SCOPE in ("email", "external", "all") and tool_name not in core._MIDDLEWARE_EMAIL_TOOLS:
        return body

    max_passes = max(1, MIDDLEWARE_MAX_PASSES)
    current = body
    for attempt in range(max_passes):
        pass_tag = f"{tool_name} (pass {attempt+1}/{max_passes})"
        try:
            verdict = core.middleware.review(user_input, current)
        except Exception as e:
            core._log_thought("MIDDLEWARE", "review_error", f"{pass_tag}: {e} — approving as-is (fail-open).\nBODY SENT UNCHANGED:\n{current}")
            break

        if verdict.get("approved", True):
            # Always log, even on silent approval — otherwise "middleware ran and
            # approved" is indistinguishable in thoughts.log from "middleware never
            # ran" (disabled/out-of-scope), which defeats the point of an audit trail.
            note = "approved, no changes." if attempt == 0 else f"approved after {attempt} revision(s)."
            core._log_thought("MIDDLEWARE", "reviewed", f"{pass_tag}: {note}\nFINAL BODY SENT:\n{current}")
            break

        reasoning = verdict.get("reasoning", "no reason given")
        revised = verdict.get("revised_body")
        if revised:
            # Full before/after diff so the audit trail can reconstruct exactly
            # what Middleware changed and why — not just the one-line reasoning.
            core._log_thought("MIDDLEWARE", "revised",
                              f"{pass_tag}: {reasoning}\n"
                              f"--- BEFORE ---\n{current}\n"
                              f"--- AFTER ---\n{revised}")
            log.middleware(f"Revised {tool_name} body — {reasoning[:80]}")
            current = revised
        else:
            core._log_thought("MIDDLEWARE", "flagged_unfixable",
                              f"{pass_tag}: {reasoning} — sending original (fail-open).\nBODY SENT UNCHANGED:\n{current}")
            log.middleware(f"Flagged but could not auto-fix {tool_name} — sending as-is: {reasoning[:80]}")
            break

    return current

def resolve_referential_recipient(core, tool_name: str, tool_args: dict,
                                   user_input: str) -> dict:
    """Ground `to` in the real conversation when the Master says "that email"
    instead of naming one. The Router never sees chat_history, so a deictic
    recipient reference is exactly the case it has to guess at — and a wrong
    guess here does not corrupt a reply, it sends a real email to the wrong
    person. Deterministic and narrow: only fires when (a) the tool actually
    sends somewhere, (b) THIS turn's own wording is referential, and (c) THIS
    turn names no address of its own to override — a fresh, explicit address
    always wins over any history lookup.

    Candidate priority: the address already sitting in THIS turn's own message/
    html_body (already synthesized against the real request) is a closer, more
    specific ground truth than reaching back into chat_history — history is only
    consulted when the body itself names nothing. Reduces the odds of "most
    recent mention in history wins" picking up an unrelated address (e.g. the
    Master's own email, casually mentioned in an unrelated aside) over the one
    the body was actually written for.
    """
    if tool_name not in ("send_gmail_message", "send_gmail_html_message"):
        return tool_args
    if not core._is_recipient_referential(user_input):
        return tool_args
    if _EMAIL_RE.search(user_input or ""):
        return tool_args   # this turn names its own address — trust it, not history
    current_to = core._to_as_lower_list(tool_args.get("to"))
    body_text = str(tool_args.get("message") or tool_args.get("html_body") or "")
    body_match = _EMAIL_RE.search(body_text)
    if body_match and body_match.group(0).lower() not in current_to:
        candidate, source = body_match.group(0), "this turn's own message body"
    else:
        candidate, source = core._last_mentioned_email(), "chat history"
    if not candidate or candidate.lower() in current_to:
        return tool_args
    core._log_thought(
        "SAFETY", "recipient_referential_override",
        f"{tool_name}: Master's wording referred to a recipient already discussed "
        f"('{user_input[:60]}'), but the Router filled 'to' with "
        f"'{tool_args.get('to')}' — overriding with the address found in "
        f"{source}, '{candidate}'.")
    tool_args = dict(tool_args)
    tool_args["to"] = candidate
    return tool_args
