import inspect
import base64
from email.message import EmailMessage
from pathlib import Path
from langchain_core.tools import StructuredTool
from langchain_google_community import GmailToolkit
from langchain_google_community.gmail.utils import (
    build_resource_service,
    get_gmail_credentials,
)

GMAIL_SYSTEM_PROMPT = """
[GMAIL ARMORY & COMMUNICATIONS PROTOCOL]
You possess tools to manage the Master's emails.
ANTI-REFUSAL DIRECTIVE: YOU HAVE FULL AUTHORIZATION TO READ AND SEND EMAILS. NEVER SAY YOU CANNOT DO THIS.

1. `search_gmail`: To find emails. ALWAYS provide a `query` parameter and optionally `max_results`.
2. `get_gmail_message`: To read a specific email by message_id (full body).
3. `get_gmail_thread`: To read an email chain by thread_id.
4. `create_draft`: To save an email in Drafts.
5. `send_gmail_message`: To send a BRAND NEW email.
6. `trash_email`: To delete an email.
7. `mark_email_read`: To mark an email as read.
8. `reply_to_email`: To reply to a thread.
9. `send_gmail_html_message`: To send a rich HTML email (styled dashboard reports like the Market Report).

[MANDATORY QUERY EXAMPLES - USE THESE EXACT PATTERNS]
- "check my newest emails"  -> search_gmail(query="category:primary", max_results=5)
- "check 10 newest emails"  -> search_gmail(query="category:primary", max_results=10)
- "unread emails"            -> search_gmail(query="is:unread category:primary", max_results=10)
- "important/unread emails from the last week" -> search_gmail(query="newer_than:7d category:primary {is:unread is:important}", max_results=10)
- "emails from Google"       -> search_gmail(query="from:google", max_results=5)
- "emails about security"    -> search_gmail(query="subject:security", max_results=5)
CRITICAL RULES:
- ALWAYS use `query` and `max_results` as parameter names. NEVER use `count`.
- Use "category:primary" to exclude promotions/ads. NEVER use "label:new".
- THIS APPLIES EVEN WHEN COMBINING OPERATORS (newer_than, is:unread, is:important,
  {a b} for OR, etc.) — a query like "newer_than:7d {is:unread is:important}" with NO
  category:primary WILL surface marketing/job-board mail Gmail auto-marks unread or
  important (found live: LinkedIn, job boards, and promo mail all leaked into a
  "check important emails" digest this way). Always keep category:primary in the
  query unless the Master explicitly asked for promotions/all mail/every category.
- NEVER skip the tool call. NEVER pretend you already fetched the emails.

[LIST + DIG DEEP — MANDATORY]
- When listing emails, EVERY item MUST include the tool field `message_id: <id>`
  (and thread_id if present). Never drop IDs from the summary — follow-ups need them.
- "đọc kỹ / đào sâu / dig / open / full content / email đó / quan trọng nhất vừa liệt kê":
  DO NOT ask the Master for message_id first. DO NOT answer from memory/snippet alone.
  DO call tools:
  1) If a message_id appeared in a recent search/list tool result or summary → get_gmail_message(message_id=...).
  2) Else re-run search_gmail with subject/from from context (e.g. subject:"…", from:…),
     then get_gmail_message on the top match id.
  Never use web search (stealth_search) as a substitute for reading Gmail content.

[RULES OF ENGAGEMENT - STRICT]
1. NEVER AUTO-SEND: If sending, verify Recipient, Subject, and Body.
2. PREFER DRAFTS: Suggest `create_draft` first unless ordered to send now.
3. REPLIES: Use `search_gmail` to get `message_id`, then `reply_to_email`.
4. SUMMARIZE (list view) — Vietnamese format, KEEP message_id:
[Number]. [Sender] — [Subject]
* **message_id:** [id from tool]
* **Tóm tắt:** [short Vietnamese summary]

EMAIL TEMPLATES FOR SENDING (USE THESE FOR PREPARING EMAIL CONTENT):
When the request involves preparing and sending via email, first gather real data from tools. Then create a professional email body that directly answers the user's request using only the collected facts.
- Default for any send email: Produce a clean, professional email (clear structure, polite tone, useful and direct). Base the content on the user's exact question + real tool data. Do not force any fixed dashboard template or specific HTML layout unless the user explicitly requests "visual", "dashboard", or "HTML style".
- Use send_gmail_message (or send_gmail_html_message internally for better readability when it improves the professional email). Never send unfilled placeholders or invented data.
- For market reports: Gather prices/stats/technicals first, then turn them into a professional email summary/evaluation.
- For todo/productivity: Use appropriate structure from note.txt as light guidance.
- Always: professional tone matching the request language, no internal paths, no meta tags. Gather data first, then fill the email.
- When sending, use the synthesized professional content as the body. Only claim sent on real Message Id.
"""


def _decode_mime_header(value: str) -> str:
    """Decode =?utf-8?Q?...?= subjects when email.header is available."""
    if not value or "=?" not in value:
        return value or ""
    try:
        from email.header import decode_header, make_header
        return str(make_header(decode_header(value)))
    except Exception:
        return value or ""


def _format_gmail_search_results(raw) -> str:
    """Make message_id impossible to miss for Brain/Worker/history.

    Live failure (2026-08-06 P0.3): search_gmail returned id+body, but the list
    reply dropped IDs, so dig-follow-up CHAT-routed and asked Master for an id.

    Live failure (same day, list): full 2.5k bodies × N emails → _compact_email_result
    fell back to text[:2000] (JSON-only path) and the Worker only saw *one* message.
    Digest mode uses short bodies; dig still uses get_gmail_message for full text.
    """
    if raw is None:
        return "No emails found."
    if isinstance(raw, str):
        return raw
    items = raw if isinstance(raw, list) else [raw]
    if not items:
        return "No emails found."

    # Multi-hit list → short body; single hit (or dig re-search) → longer excerpt
    body_cap = 6000 if len(items) <= 1 else 180

    blocks = []
    for i, item in enumerate(items, 1):
        if not isinstance(item, dict):
            blocks.append(f"[{i}] {item}")
            continue
        mid = item.get("id") or item.get("message_id") or item.get("messageId") or ""
        tid = item.get("threadId") or item.get("thread_id") or ""
        snippet = (item.get("snippet") or "").strip()
        body = (item.get("body") or item.get("text") or "").strip()
        subject = _decode_mime_header(item.get("subject") or "")
        sender = item.get("from") or item.get("sender") or ""
        if not subject and isinstance(item.get("payload"), dict):
            headers = {
                h.get("name", "").lower(): h.get("value", "")
                for h in (item["payload"].get("headers") or [])
                if isinstance(h, dict)
            }
            subject = _decode_mime_header(headers.get("subject", ""))
            sender = sender or headers.get("from", "")

        body_excerpt = body[:body_cap] if body else ""
        lines = [
            f"[{i}] message_id: {mid}",
            f"    thread_id: {tid}" if tid else None,
            f"    from: {sender}" if sender else None,
            f"    subject: {subject}" if subject else None,
            f"    snippet: {snippet[:300]}" if snippet else None,
            f"    body: {body_excerpt}" if body_excerpt else None,
        ]
        blocks.append("\n".join(x for x in lines if x))
    return "\n\n".join(blocks)

def _get_gmail_credentials_compat(token_file: Path, credentials_file: Path):
    params = inspect.signature(get_gmail_credentials).parameters
    common_kwargs = {"token_file": str(token_file), "scopes": ["https://mail.google.com/"]}
    if "client_secrets_file" in params: return get_gmail_credentials(**common_kwargs, client_secrets_file=str(credentials_file))
    if "client_sercret_file" in params: return get_gmail_credentials(**common_kwargs, client_sercret_file=str(credentials_file))
    raise TypeError("Unsupported get_gmail_credentials signature.")

def get_gmail_tools() -> dict:
    try:
        base_dir = Path(__file__).resolve().parent.parent.parent
        credentials_file = base_dir / "credentials.json"
        token_file = base_dir / "ciel_data" / "gmail_token.json"

        if not credentials_file.exists():
            print("[Ciel Fatal] Missing credentials.json. Gmail armory locked.")
            return {"tools": [], "prompt": ""}

        token_file.parent.mkdir(parents=True, exist_ok=True)
        credentials = _get_gmail_credentials_compat(token_file, credentials_file)
        api_resource = build_resource_service(credentials=credentials)
        toolkit = GmailToolkit(api_resource=api_resource)
        tools = toolkit.get_tools()

        # Wrap search_gmail so list/dig always see explicit message_id lines.
        wrapped = []
        for t in tools:
            if getattr(t, "name", "") != "search_gmail":
                wrapped.append(t)
                continue
            _orig = t

            def _search_gmail_formatted(
                query: str,
                resource: str = "messages",
                max_results: int = 5,
                _orig=_orig,
            ) -> str:
                try:
                    # LangChain tool schemas vary; pass only supported kwargs.
                    try:
                        raw = _orig.invoke(
                            {
                                "query": query,
                                "resource": resource,
                                "max_results": max_results,
                            }
                        )
                    except Exception:
                        raw = _orig.invoke({"query": query, "max_results": max_results})
                    return _format_gmail_search_results(raw)
                except Exception as e:
                    return f"search_gmail error: {e}"

            wrapped.append(
                StructuredTool.from_function(
                    func=_search_gmail_formatted,
                    name="search_gmail",
                    description=(
                        "Search emails. Args: query (required), resource='messages' "
                        "(preferred), max_results (optional, default 5). "
                        "Returns a text block with message_id, thread_id, snippet/body "
                        "for each hit — always keep message_id when summarizing."
                    ),
                )
            )
        tools = wrapped

        def trash_email(message_id: str) -> str:
            try:
                api_resource.users().messages().trash(userId='me', id=message_id).execute()
                return f"Message {message_id} successfully moved to Trash."
            except Exception as e: return f"Failed to trash message: {e}"
        trash_tool = StructuredTool.from_function(func=trash_email, name="trash_email", description="Delete or trash an email. YOU MUST USE THIS TOOL when asked to delete or remove an email. Requires exact message_id.")

        def mark_as_read(message_id: str) -> str:
            try:
                api_resource.users().messages().modify(userId='me', id=message_id, body={'removeLabelIds': ['UNREAD']}).execute()
                return f"Message {message_id} marked as read."
            except Exception as e: return f"Failed to mark as read: {e}"
        mark_read_tool = StructuredTool.from_function(func=mark_as_read, name="mark_email_read", description="Mark an email as read. YOU MUST USE THIS TOOL when asked to mark an email as read. Requires exact message_id.")

        def reply_to_email(message_id: str, reply_text: str) -> str:
            try:
                orig_msg = api_resource.users().messages().get(userId='me', id=message_id, format='metadata').execute()
                headers = {h['name'].lower(): h['value'] for h in orig_msg['payload']['headers']}
                orig_msg_id, orig_subject, orig_sender, orig_references, thread_id = headers.get('message-id', ''), headers.get('subject', ''), headers.get('from', ''), headers.get('references', ''), orig_msg['threadId']
                new_subject = orig_subject if orig_subject.lower().startswith('re:') else f"Re: {orig_subject}"
                new_references = f"{orig_references} {orig_msg_id}".strip()

                msg = EmailMessage()
                msg.set_content(reply_text)
                msg['To'] = orig_sender
                msg['Subject'] = new_subject
                msg['In-Reply-To'] = orig_msg_id
                msg['References'] = new_references

                raw_message = base64.urlsafe_b64encode(msg.as_bytes()).decode('utf-8')
                api_resource.users().messages().send(userId='me', body={'raw': raw_message, 'threadId': thread_id}).execute()
                return f"Successfully replied to {orig_sender} (Thread ID: {thread_id})."
            except Exception as e: return f"Failed to send reply: {e}"
        reply_tool = StructuredTool.from_function(func=reply_to_email, name="reply_to_email", description="Reply to an email thread. YOU MUST USE THIS TOOL when asked to reply or respond to an email. Requires message_id and reply_text.")

        def send_html_email(to: str, subject: str, html_body: str) -> str:
            """Send an HTML email (rich layout). Use for styled dashboard reports (e.g. Market Report)."""
            try:
                msg = EmailMessage()
                msg["To"] = to
                msg["Subject"] = subject
                msg.set_content("This report requires an HTML-capable email client.")
                msg.add_alternative(html_body, subtype="html")
                raw_message = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
                sent = api_resource.users().messages().send(userId="me", body={"raw": raw_message}).execute()
                return f"HTML email sent to {to}. Message Id: {sent.get('id', 'UNKNOWN')}"
            except Exception as e:
                return f"Failed to send HTML email: {e}"
        send_html_tool = StructuredTool.from_function(
            func=send_html_email,
            name="send_gmail_html_message",
            description="Send a rich HTML email. USE THIS for styled dashboard reports such as the Market Performance Report (BTC/XAUUSD). Requires to, subject, and html_body (full HTML string). Claim success only if a Message Id is returned.",
        )

        tools.extend([trash_tool, mark_read_tool, reply_tool, send_html_tool])
        return {"tools": tools, "prompt": GMAIL_SYSTEM_PROMPT}

    except Exception as e:
        print(f"[Ciel System Error] Failed to arm Gmail toolkit: {e}")
        return {"tools": [], "prompt": ""}