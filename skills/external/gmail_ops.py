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

# ==========================================
# THE WEAPON'S SOUL (TOOL-SPECIFIC PROMPT)
# ==========================================
GMAIL_SYSTEM_PROMPT = """
[GMAIL ARMORY & COMMUNICATIONS PROTOCOL]
You possess the following tools:
1. `search_gmail`: To find emails (You can use standard Gmail operators like "from:", "subject:", "newer_than:1d").
2. `get_message`: To read the specific content of a single email.
3. `get_thread`: To read an entire back-and-forth email chain.
4. `create_draft`: To write an email and save it in the Master's Drafts folder for their review.
5. `send_message`: To instantly send a BRAND NEW email (Do NOT use this for replying).
6. `trash_email`: To delete an annoying or promotional email (Requires the exact message_id).
7. `mark_email_read`: To mark an email as read.
8. `reply_to_email`: To precisely reply to an existing email conversation (Requires the original message_id and the reply text).

[RULES OF ENGAGEMENT - STRICT]
1. NEVER AUTO-SEND: If the Master asks you to "send an email", you MUST verify you have the Recipient Address, the Subject, and the Body. If any are missing, ask the Master. 
2. PREFER DRAFTS: If the email is highly important or complex, suggest using `create_draft` first so the Master can review it safely, unless they explicitly say "send it now".
3. REPLIES: To reply, you MUST first search for the email to get its `message_id`, then use `reply_to_email`.
4. SUMMARIZE INTELLIGENCE: When the Master asks you to read or search emails, do not just dump raw text. Read the emails using your tools, analyze them, and present a clean, tactical summary EXACTLY in this Vietnamese format:

[Number]. [Sender Name] ([Sender Email])
* **Tiêu đề:** [Subject]
* **Tóm tắt:** [A short, concise, and analytical summary of the email content in Vietnamese]
"""

def _get_gmail_credentials_compat(token_file: Path, credentials_file: Path):
    """Armor against LangChain's typo bugs."""
    params = inspect.signature(get_gmail_credentials).parameters
    common_kwargs = {"token_file": str(token_file), "scopes": ["https://mail.google.com/"]}

    if "client_secrets_file" in params:
        return get_gmail_credentials(**common_kwargs, client_secrets_file=str(credentials_file))
    if "client_sercret_file" in params:
        return get_gmail_credentials(**common_kwargs, client_sercret_file=str(credentials_file))
    
    raise TypeError("Unsupported get_gmail_credentials signature.")

def get_gmail_tools() -> dict:
    """Initialize and return the Gmail toolkit AND its specific instructions."""
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

        # Custom Tool 1: Trash
        def trash_email(message_id: str) -> str:
            try:
                api_resource.users().messages().trash(userId='me', id=message_id).execute()
                return f"Message {message_id} successfully moved to Trash."
            except Exception as e:
                return f"Failed to trash message: {e}"

        trash_tool = StructuredTool.from_function(
            func=trash_email,
            name="trash_email",
            description="Delete/trash an email. Requires exact message_id."
        )

        # Custom Tool 2: Mark as Read
        def mark_as_read(message_id: str) -> str:
            try:
                api_resource.users().messages().modify(
                    userId='me', id=message_id, body={'removeLabelIds': ['UNREAD']}
                ).execute()
                return f"Message {message_id} marked as read."
            except Exception as e:
                return f"Failed to mark as read: {e}"

        mark_read_tool = StructuredTool.from_function(
            func=mark_as_read,
            name="mark_email_read",
            description="Mark an email as read by removing the UNREAD label. Requires exact message_id."
        )

        # Custom Tool 3: True Reply
        def reply_to_email(message_id: str, reply_text: str) -> str:
            try:
                # 1. Fetch original message headers to maintain the thread
                orig_msg = api_resource.users().messages().get(userId='me', id=message_id, format='metadata').execute()
                headers = {h['name'].lower(): h['value'] for h in orig_msg['payload']['headers']}
                
                orig_msg_id = headers.get('message-id', '')
                orig_subject = headers.get('subject', '')
                orig_sender = headers.get('from', '')
                orig_references = headers.get('references', '')
                thread_id = orig_msg['threadId']

                # 2. Build new headers
                new_subject = orig_subject if orig_subject.lower().startswith('re:') else f"Re: {orig_subject}"
                new_references = f"{orig_references} {orig_msg_id}".strip()

                # 3. Create the MIME message
                msg = EmailMessage()
                msg.set_content(reply_text)
                msg['To'] = orig_sender
                msg['Subject'] = new_subject
                msg['In-Reply-To'] = orig_msg_id
                msg['References'] = new_references

                # 4. Encode and Send
                raw_message = base64.urlsafe_b64encode(msg.as_bytes()).decode('utf-8')
                send_body = {'raw': raw_message, 'threadId': thread_id}
                
                api_resource.users().messages().send(userId='me', body=send_body).execute()
                return f"Successfully replied to {orig_sender} (Thread ID: {thread_id})."
            except Exception as e:
                return f"Failed to send reply: {e}"

        reply_tool = StructuredTool.from_function(
            func=reply_to_email,
            name="reply_to_email",
            description="Reply to an email thread. You MUST provide the message_id of the email you are replying to, and the reply_text."
        )

        # Inject all custom weapons
        tools.extend([trash_tool, mark_read_tool, reply_tool])
        
        return {
            "tools": tools,
            "prompt": GMAIL_SYSTEM_PROMPT
        }

    except Exception as e:
        print(f"[Ciel System Error] Failed to arm Gmail toolkit: {e}")
        return {"tools": [], "prompt": ""}