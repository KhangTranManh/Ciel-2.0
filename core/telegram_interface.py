"""Telegram bot interface — a third front-end consumer of CielCore, alongside
the CLI (main.py) and the WebSocket API (main_api.py). Long-polls the Bot API
directly (no python-telegram-bot dependency — consistent with the raw-requests
style already used in skills/external/telegram_ops.py) and restricts every
inbound message to a single authorized chat: this bot can run real tools
(shell commands, email, file writes), so anyone else able to message it must
never reach core.process().
"""
import os
import time
import json
import queue
import threading
import requests
from pathlib import Path
from colorama import Fore, Style

from core.redact import redact_secrets
from skills.internal.system_ops import WORKSPACE_DIR

API_ROOT = "https://api.telegram.org/bot{token}/{method}"
FILE_API_ROOT = "https://api.telegram.org/file/bot{token}/{file_path}"
MAX_MESSAGE_CHARS = 3500          # stay under Telegram's 4096 hard limit with headroom
LONG_POLL_TIMEOUT = 30            # seconds Telegram holds the getUpdates connection open
CONFIRM_TIMEOUT = 60              # seconds to wait for a Yes/No tap before auto-declining

# Inbound photos/documents land here — already inside the sandbox (a subdir of
# ciel_workspace/), so every existing file/vision tool can reach them with no
# special-casing: read_document, describe_image_file, read_file, etc. all resolve
# through the same _is_safe_path() as anything else in ciel_workspace/.
UPLOAD_DIR = WORKSPACE_DIR / "telegram_uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


class TelegramInterface:
    def __init__(self, ciel_agent, bot_token: str = None, chat_id: str = None):
        self.ciel = ciel_agent
        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = str(chat_id or os.getenv("TELEGRAM_CHAT_ID", "")).strip()
        if not self.bot_token or not self.chat_id:
            raise RuntimeError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing in .env")

        self._offset = 0
        self._inbox: "queue.Queue[str]" = queue.Queue()
        self._pending_confirm = {}   # token -> (threading.Event, result dict)
        self._confirm_lock = threading.Lock()
        self._stop = threading.Event()

    # ---------- low-level Bot API ----------
    def _call(self, method: str, **params) -> dict:
        url = API_ROOT.format(token=self.bot_token, method=method)
        resp = requests.post(url, json=params, timeout=LONG_POLL_TIMEOUT + 10)
        resp.raise_for_status()
        return resp.json()

    def _send_message(self, text: str, reply_markup: dict = None):
        # Telegram caps a single message at 4096 chars; split long replies rather
        # than silently truncating a real answer (a full email draft, a report...).
        text = text or "(empty response)"
        chunks = [text[i:i + MAX_MESSAGE_CHARS] for i in range(0, len(text), MAX_MESSAGE_CHARS)]
        for i, chunk in enumerate(chunks):
            params = {"chat_id": self.chat_id, "text": chunk}
            if reply_markup and i == len(chunks) - 1:
                params["reply_markup"] = json.dumps(reply_markup)
            try:
                self._call("sendMessage", **params)
            except Exception as e:
                print(Fore.RED + f"[Telegram] send failed: {redact_secrets(e)}" + Style.RESET_ALL)

    def _send_typing(self):
        try:
            self._call("sendChatAction", chat_id=self.chat_id, action="typing")
        except Exception:
            pass

    def _answer_callback(self, callback_query_id: str, text: str = ""):
        try:
            self._call("answerCallbackQuery", callback_query_id=callback_query_id, text=text)
        except Exception:
            pass

    def _download_file(self, file_id: str, suggested_name: str) -> Path | None:
        """Resolve a Telegram file_id to real bytes and save it under UPLOAD_DIR.
        Timestamp-prefixed so two uploads with the same original name never collide.
        Returns the saved path, or None on any failure (network, bad file_id, ...)."""
        try:
            info = self._call("getFile", file_id=file_id)
            file_path = info["result"]["file_path"]
            url = FILE_API_ROOT.format(token=self.bot_token, file_path=file_path)
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()

            ts = time.strftime("%Y%m%d_%H%M%S")
            safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in suggested_name)
            dest = UPLOAD_DIR / f"{ts}_{safe_name}"
            dest.write_bytes(resp.content)
            return dest
        except Exception as e:
            print(Fore.RED + f"[Telegram] file download failed: {redact_secrets(e)}" + Style.RESET_ALL)
            return None

    # ---------- confirm_callback wired into CielCore ----------
    # Same signature as main.py's _cli_confirm and main_api.py's _ws_confirm:
    # confirm_callback(tool_name, preview, tool_args) -> bool.
    def confirm_callback(self, tool_name: str, preview: str, tool_args: dict) -> bool:
        token = f"cf{int(time.time() * 1000)}"
        event = threading.Event()
        result = {"approved": False}
        with self._confirm_lock:
            self._pending_confirm[token] = (event, result)

        header = "PLAN APPROVAL" if tool_name == "plan" else f"SAFETY CHECK — {tool_name}"
        text = f"⚠️ {header}\n{preview}"
        keyboard = {"inline_keyboard": [[
            {"text": "✅ Yes", "callback_data": f"{token}:yes"},
            {"text": "❌ No", "callback_data": f"{token}:no"},
        ]]}
        self._send_message(text, reply_markup=keyboard)

        got_answer = event.wait(timeout=CONFIRM_TIMEOUT)
        with self._confirm_lock:
            self._pending_confirm.pop(token, None)
        if not got_answer:
            self._send_message("⏳ Timed out waiting for approval — action cancelled.")
            return False
        return result["approved"]

    # ---------- polling loop (runs on its own thread) ----------
    def _poll_updates(self):
        while not self._stop.is_set():
            try:
                data = self._call("getUpdates", offset=self._offset, timeout=LONG_POLL_TIMEOUT)
            except Exception as e:
                print(Fore.RED + f"[Telegram] poll error: {redact_secrets(e)}" + Style.RESET_ALL)
                time.sleep(3)
                continue

            for update in data.get("result", []):
                self._offset = update["update_id"] + 1
                try:
                    self._handle_update(update)
                except Exception as e:
                    print(Fore.RED + f"[Telegram] update handling error: {redact_secrets(e)}" + Style.RESET_ALL)

    def _handle_update(self, update: dict):
        if "callback_query" in update:
            cq = update["callback_query"]
            self._answer_callback(cq["id"])
            data = cq.get("data", "")
            if ":" not in data:
                return
            token, choice = data.split(":", 1)
            with self._confirm_lock:
                pending = self._pending_confirm.get(token)
            if pending:
                event, result = pending
                result["approved"] = (choice == "yes")
                event.set()
            return

        msg = update.get("message") or update.get("edited_message")
        if not msg:
            return
        sender_chat_id = str(msg.get("chat", {}).get("id", ""))
        if sender_chat_id != self.chat_id:
            # Never process a command from anyone but the authorized Master — this
            # bot can run real tools (shell, email, file writes).
            print(Fore.YELLOW + f"[Telegram] Ignored message from unauthorized "
                  f"chat_id={sender_chat_id}" + Style.RESET_ALL)
            return

        # Telegram puts a photo/document's accompanying text in `caption`, not
        # `text` — only a plain message uses `text`.
        photos = msg.get("photo")
        document = msg.get("document")
        if photos or document:
            if photos:
                # `photo` is a list of the SAME image at increasing resolutions —
                # the last entry is the largest.
                file_id = photos[-1]["file_id"]
                suggested_name = f"photo_{file_id[-10:]}.jpg"
                kind_label = "Ảnh"
            else:
                file_id = document["file_id"]
                suggested_name = document.get("file_name") or f"file_{file_id[-10:]}"
                kind_label = "File"

            saved = self._download_file(file_id, suggested_name)
            if saved is None:
                self._send_message(f"⚠️ Không tải được {kind_label.lower()} từ Telegram — Master thử gửi lại giúp Ciel.")
                return

            # Relative to ciel_workspace/, matching the shape every file/vision tool
            # already expects (read_document, describe_image_file, read_file, ...).
            rel_path = f"telegram_uploads/{saved.name}"
            caption = (msg.get("caption") or "").strip()
            note = f"[{kind_label} Master vừa gửi qua Telegram, đã lưu tại: ciel_workspace/{rel_path}]"
            if caption:
                note += f"\n{caption}"
            self._inbox.put(note)
            return

        text = (msg.get("text") or "").strip()
        if not text:
            return
        if text == "/cancel":
            # Tier 5 cooperative cancel — same request_cancel() the WS "cancel" frame
            # and CLI Ctrl+C use. Handled here (poll thread), not via the inbox queue,
            # so it interrupts the job currently running on the worker thread instead
            # of queueing behind it.
            self.ciel.core.request_cancel("cancelled from Telegram")
            return
        self._inbox.put(text)

    # ---------- worker: processes one message at a time through CielCore ----------
    def _worker_loop(self):
        while not self._stop.is_set():
            try:
                text = self._inbox.get(timeout=1)
            except queue.Empty:
                continue
            if text.lower() in ("/start", "/help"):
                self._send_message("Ciel is online. Just send a message as you normally would. "
                                    "/cancel interrupts whatever is currently running.")
                continue
            self._send_typing()
            try:
                response = self.ciel.run_step(text)
            except Exception as e:
                response = f"[Ciel Fatal] {redact_secrets(e)}"
            # The attachment and its caption are already visible in this chat. Do not
            # repost the synthesized report as a second message below it.
            if getattr(self.ciel.core, "_telegram_delivery_sent_this_turn", False):
                continue
            self._send_message(response)

    def start(self):
        self.ciel.core.confirm_callback = self.confirm_callback
        threading.Thread(target=self._poll_updates, daemon=True, name="telegram-poll").start()
        threading.Thread(target=self._worker_loop, daemon=True, name="telegram-worker").start()
        print(Fore.BLUE + f"[Telegram] Bot online — listening for chat_id={self.chat_id} only."
              + Style.RESET_ALL)

    def stop(self):
        self._stop.set()
