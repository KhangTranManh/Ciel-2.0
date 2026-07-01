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
2. `get_message`: To read a specific email by message_id.
3. `get_thread`: To read an email chain by thread_id.
4. `create_draft`: To save an email in Drafts.
5. `send_message`: To send a BRAND NEW email.
6. `trash_email`: To delete an email.
7. `mark_email_read`: To mark an email as read.
8. `reply_to_email`: To reply to a thread.

[MANDATORY QUERY EXAMPLES - USE THESE EXACT PATTERNS]
- "check my newest emails"  -> search_gmail(query="category:primary", max_results=5)
- "check 10 newest emails"  -> search_gmail(query="category:primary", max_results=10)
- "unread emails"            -> search_gmail(query="is:unread category:primary", max_results=10)
- "emails from Google"       -> search_gmail(query="from:google", max_results=5)
- "emails about security"    -> search_gmail(query="subject:security", max_results=5)
CRITICAL RULES:
- ALWAYS use `query` and `max_results` as parameter names. NEVER use `count`.
- Use "category:primary" to exclude promotions/ads. NEVER use "label:new".
- NEVER skip the tool call. NEVER pretend you already fetched the emails.

[RULES OF ENGAGEMENT - STRICT]
1. NEVER AUTO-SEND: If sending, verify Recipient, Subject, and Body.
2. PREFER DRAFTS: Suggest `create_draft` first unless ordered to send now.
3. REPLIES: Use `search_gmail` to get `message_id`, then `reply_to_email`.
4. SUMMARIZE: Analyze emails and present a clean summary EXACTLY in this Vietnamese format:
[Number]. [Sender Name] ([Sender Email])
* **Tiêu đề:** [Subject]
* **Tóm tắt:** [A short, concise, and analytical summary in Vietnamese]

EMAIL TEMPLATES FOR SENDING (USE THESE FOR PREPARING EMAIL CONTENT):
When the request involves preparing and sending a report or evaluation via email (e.g. market analysis, status, todos, alerts), first determine the type of content.
- For market data + evaluation + send email (XAUUSD, BTC, crypto, forex, gold, prices, technicals, risk): Use the Market / Asset Report structure defined in note.txt (the filled version under Recommended Email Templates). Gather prices + stats + technicals from tools FIRST, then fill ONLY with real current values. Never send a body containing unfilled [] placeholders or hallucinated prices. Never leak internal paths.
- For todo/productivity related + email: Use Todo / Productivity Summary template (see note.txt for structure).
- For general task/status + email: Use General Task / Status Report template.
- For alerts/digests: Use Alert / Warning / Digest template.
- For replies or gmail summaries: Use Gmail-related template.
- Otherwise: Use General / Custom Content template.
Always follow the chosen template's exact sections, order, and tone. Gather data with tools first, then populate only with actual results. When sending, use send_message or send_gmail_message with the filled template content as the message. Never leak internal paths in the email.
"""

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

        tools.extend([trash_tool, mark_read_tool, reply_tool])
        return {"tools": tools, "prompt": GMAIL_SYSTEM_PROMPT}

    except Exception as e:
        print(f"[Ciel System Error] Failed to arm Gmail toolkit: {e}")
        return {"tools": [], "prompt": ""}