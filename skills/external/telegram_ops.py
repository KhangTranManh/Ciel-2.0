import os
import requests
from langchain_core.tools import StructuredTool

TELEGRAM_SYSTEM_PROMPT = """
[TELEGRAM NOTIFICATION ARMORY]
You possess a tool to send messages to the Master via Telegram.

1. `send_telegram`: Send a notification message to the Master's Telegram.

[RULES]
1. Use this when the Master asks you to notify, alert, or send a message via Telegram.
2. Keep messages concise and informative.
3. Use Markdown formatting for readability.
"""


def send_telegram_message(message: str) -> bool:
    """Send a message via Telegram bot. Raw function usable by scheduler directly."""
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    
    if not bot_token or not chat_id:
        return False
        
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown"
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.status_code == 200
    except Exception as e:
        print(f"[Telegram Error] {e}")
        return False


def get_telegram_tools() -> dict:
    """Factory function following the standard get_*_tools() pattern."""
    tools = []

    def send_telegram(message: str) -> str:
        """Send a notification message to the Master via Telegram."""
        success = send_telegram_message(message)
        if success:
            return "Message sent to Telegram successfully."
        return "Failed to send Telegram message. Check TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env."

    tools.append(StructuredTool.from_function(
        func=send_telegram,
        name="send_telegram",
        description="Send a notification message to the Master's Telegram. "
                    "YOU MUST USE THIS TOOL when asked to notify, alert, or send a message via Telegram."
    ))

    return {"tools": tools, "prompt": TELEGRAM_SYSTEM_PROMPT}
