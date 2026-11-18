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
import shutil
import threading
import subprocess
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
    def __init__(self, ciel_agent, bot_token: str = None, chat_id: str = None, user_id: str = None):
        self.ciel = ciel_agent
        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = str(chat_id or os.getenv("TELEGRAM_CHAT_ID", "")).strip()
        self.user_id = str(user_id or os.getenv("TELEGRAM_USER_ID", "")).strip()
        if not self.bot_token or not self.chat_id:
            raise RuntimeError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing in .env")

        self._offset = 0
        self._inbox: "queue.Queue[str]" = queue.Queue()
        self._pending_confirm = {}   # token -> (threading.Event, result dict)
        self._confirm_lock = threading.Lock()
        self._handled_reminder_cbs: set[str] = set()
        self._stop = threading.Event()

    def _is_authorized_user(self, sender: dict | None) -> bool:
        if not self.user_id:
            return True
        return str((sender or {}).get("id", "")) == self.user_id

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
            params = {"callback_query_id": callback_query_id}
            if text:
                params["text"] = text
            self._call("answerCallbackQuery", **params)
        except Exception:
            pass

    def _set_bot_commands(self):
        commands = [
            {"command": "today", "description": "Lịch trình & việc cần làm hôm nay"},
            {"command": "reminders", "description": "Danh sách nhắc việc đang chờ"},
            {"command": "market", "description": "Giá vàng (XAU/USD) & Bitcoin tức thì"},
            {"command": "cancel", "description": "Hủy tác vụ đang chạy dở"},
            {"command": "help", "description": "Hướng dẫn sử dụng Ciel"},
            {"command": "start", "description": "Bắt đầu làm việc với Ciel"},
        ]
        try:
            self._call("setMyCommands", commands=commands)
        except Exception as e:
            print(Fore.YELLOW + f"[Telegram] setMyCommands failed: {redact_secrets(e)}" + Style.RESET_ALL)

    def _send_help(self):
        help_text = (
            "🤖 *Ciel — Trợ lý AI cá nhân*\n\n"
            "📌 *Các lệnh tra cứu nhanh (không tốn token):*\n"
            "• /today — Xem lịch trình và việc cần làm hôm nay\n"
            "• /reminders — Xem danh sách nhắc việc đang chờ\n"
            "• /market — Xem giá thị trường (vàng XAU/USD & Bitcoin)\n"
            "• /cancel — Hủy tác vụ Ciel đang thực hiện dở\n"
            "• /help — Xem lại hướng dẫn này\n\n"
            "💡 *Cách tương tác khác:*\n"
            "• Nhắn tin tự nhiên để giao việc hoặc hỏi đáp\n"
            "• Gửi ảnh hoặc tài liệu đính kèm\n"
            "• Gửi tin nhắn thoại để giao việc bằng giọng nói\n"
            "• Bấm nút '✅ Xong' hoặc '⏰ Hoãn 15p' trên thông báo nhắc việc"
        )
        self._send_message(help_text)

    def _handle_cmd_today(self):
        try:
            from datetime import datetime, timedelta
            from zoneinfo import ZoneInfo
            from agent_system import config
            from core.planner_store import PlannerStore

            store = PlannerStore(config.PLANNER_DB_PATH)
            tz = ZoneInfo(config.PLANNER_TIMEZONE)
            now_local = datetime.now(tz)
            date_str = now_local.strftime("%d/%m/%Y")

            # 1. Events today
            start_of_day = datetime(now_local.year, now_local.month, now_local.day, 0, 0, 0, tzinfo=tz)
            end_of_day = start_of_day + timedelta(days=1)
            events = store.list_calendar_events(
                start_of_day.isoformat(),
                end_of_day.isoformat(),
                timezone_name=config.PLANNER_TIMEZONE,
            )

            # 2. Reminders today
            reminders = store.list_reminders(include_closed=False)
            today_reminders = []
            for rem in reminders:
                try:
                    due_utc = datetime.fromisoformat(str(rem["due_at_utc"]).replace("Z", "+00:00"))
                    due_local = due_utc.astimezone(tz)
                    if due_local.date() == now_local.date():
                        today_reminders.append((due_local, rem))
                except Exception:
                    continue
            today_reminders.sort(key=lambda x: x[0])

            # 3. Weekly tasks for this week
            week_start = (now_local.date() - timedelta(days=now_local.weekday())).isoformat()
            tasks = store.list_weekly_tasks(week_start, include_closed=False)
            today_weekday = now_local.weekday()
            today_tasks = [t for t in tasks if t.get("weekday") == today_weekday or t.get("weekday") is None]

            parts = [f"📅 Lịch trình & Việc hôm nay ({date_str}):"]
            has_content = False

            if events:
                has_content = True
                parts.append("\n🗓️ Sự kiện lịch:")
                for ev in events:
                    title = ev.get("title", "")
                    if ev.get("all_day"):
                        time_label = "Cả ngày"
                    else:
                        try:
                            s = datetime.fromisoformat(str(ev["start_at_utc"]).replace("Z", "+00:00")).astimezone(tz).strftime("%H:%M")
                            e = datetime.fromisoformat(str(ev["end_at_utc"]).replace("Z", "+00:00")).astimezone(tz).strftime("%H:%M")
                            time_label = f"{s} - {e}"
                        except Exception:
                            time_label = "Trong ngày"
                    parts.append(f"• {time_label}: {title}")

            if today_reminders:
                has_content = True
                parts.append("\n⏰ Nhắc việc hôm nay:")
                for due_local, rem in today_reminders:
                    t_str = due_local.strftime("%H:%M")
                    parts.append(f"• {t_str} [#{rem['id']}]: {rem['title']}")

            if today_tasks:
                has_content = True
                parts.append("\n📋 Nhiệm vụ tuần:")
                for t in today_tasks:
                    lt = f" ({t['local_time']})" if t.get("local_time") else ""
                    parts.append(f"• {t['title']}{lt}")

            if not has_content:
                parts.append("\nKhông có lịch hẹn hay việc cần làm nào trong ngày.")

            self._send_message("\n".join(parts))
        except Exception as e:
            self._send_message(f"⚠️ Không thể tải thông tin hôm nay: {redact_secrets(e)}")

    def _handle_cmd_reminders(self):
        try:
            from datetime import datetime
            from zoneinfo import ZoneInfo
            from agent_system import config
            from core.planner_store import PlannerStore

            store = PlannerStore(config.PLANNER_DB_PATH)
            tz = ZoneInfo(config.PLANNER_TIMEZONE)
            reminders = store.list_reminders(include_closed=False)
            if not reminders:
                self._send_message("⏰ Hiện không có nhắc việc nào đang chờ.")
                return

            parts = ["⏰ Danh sách nhắc việc đang chờ:"]
            for rem in reminders:
                rem_id = rem["id"]
                title = rem["title"]
                try:
                    due_utc = datetime.fromisoformat(str(rem["due_at_utc"]).replace("Z", "+00:00"))
                    due_local = due_utc.astimezone(tz).strftime("%H:%M %d/%m/%Y")
                except Exception:
                    due_local = rem.get("due_at_utc", "")
                parts.append(f"• [#{rem_id}] {due_local}: {title}")

            self._send_message("\n".join(parts))
        except Exception as e:
            self._send_message(f"⚠️ Không thể tải danh sách nhắc việc: {redact_secrets(e)}")

    def _handle_cmd_market(self):
        try:
            from skills.external.trading_ops import fetch_market_price
            gold = fetch_market_price("XAU/USD")
            btc = fetch_market_price("BTC/USDT")
            parts = [
                "📊 Giá thị trường tức thì:",
                f"• Vàng (XAU/USD): {gold}",
                f"• Bitcoin (BTC/USDT): {btc}",
            ]
            self._send_message("\n".join(parts))
        except Exception as e:
            self._send_message(f"⚠️ Không thể lấy dữ liệu thị trường: {redact_secrets(e)}")

    def _remove_message_buttons(self, cq: dict, note_status: str = ""):
        msg = cq.get("message") or {}
        msg_id = msg.get("message_id")
        orig_text = msg.get("text", "")
        chat_id = str((msg.get("chat") or {}).get("id") or self.chat_id)
        if not msg_id:
            return
        new_text = orig_text
        if note_status and note_status not in orig_text:
            new_text = f"{orig_text}\n\n{note_status}"
        try:
            self._call(
                "editMessageText",
                chat_id=chat_id,
                message_id=msg_id,
                text=new_text,
                reply_markup={"inline_keyboard": []},
            )
        except Exception:
            try:
                self._call(
                    "editMessageReplyMarkup",
                    chat_id=chat_id,
                    message_id=msg_id,
                    reply_markup={"inline_keyboard": []},
                )
            except Exception:
                pass

    def _reset_reminder_cooldown(self, reminder_id: int):
        key = f"reminder:{reminder_id}"
        try:
            scheduler = getattr(self.ciel, "scheduler", None)
            engine = getattr(scheduler, "trigger_engine", None)
            notifier = getattr(engine, "notifier", None)
            if notifier and hasattr(notifier, "reset_cooldown"):
                notifier.reset_cooldown(key)
        except Exception:
            pass
        try:
            base_dir = getattr(getattr(self.ciel, "core", None), "base_dir", None) or Path.cwd()
            state_path = base_dir / "ciel_data" / "state" / "notify.json"
            if state_path.exists():
                raw = json.loads(state_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict) and "last_sent" in raw and key in raw["last_sent"]:
                    raw["last_sent"].pop(key, None)
                    state_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def _handle_reminder_callback(self, cq: dict, data: str):
        cq_id = str(cq.get("id", ""))
        msg = cq.get("message") or {}
        msg_id = msg.get("message_id")
        orig_text = msg.get("text", "")

        parts = data.split(":")
        if len(parts) < 3:
            self._answer_callback(cq_id, text="Thao tác không hợp lệ.")
            return

        action = parts[1]
        try:
            reminder_id = int(parts[2])
        except ValueError:
            self._answer_callback(cq_id, text="ID nhắc việc không hợp lệ.")
            return

        cb_key = f"{msg_id}:{action}:{reminder_id}"
        if cb_key in self._handled_reminder_cbs or "✅ Đã hoàn thành" in orig_text or "⏰ Đã hoãn" in orig_text:
            self._answer_callback(cq_id, text="Nhắc việc này đã được xử lý.")
            self._remove_message_buttons(cq)
            return

        from agent_system import config
        from core.planner_store import PlannerStore
        try:
            store = PlannerStore(config.PLANNER_DB_PATH)
            rem = store.get_reminder(reminder_id)
        except Exception as e:
            self._answer_callback(cq_id, text="Không thể truy cập dữ liệu.")
            return

        if rem is None:
            self._answer_callback(cq_id, text="Nhắc việc không tồn tại.")
            self._remove_message_buttons(cq, note_status="[Không tìm thấy nhắc việc]")
            return

        if rem.get("status") == "cancelled":
            self._answer_callback(cq_id, text="Nhắc việc này đã bị hủy.")
            self._remove_message_buttons(cq, note_status="[Nhắc việc đã bị hủy]")
            return

        self._handled_reminder_cbs.add(cb_key)

        if action == "done":
            try:
                if rem.get("status") == "pending":
                    store.mark_reminder_delivered(reminder_id)
            except Exception:
                pass
            self._answer_callback(cq_id, text="✅ Đã hoàn thành nhắc việc!")
            self._remove_message_buttons(cq, note_status="✅ Đã hoàn thành.")

        elif action == "snooze":
            try:
                snooze_min = int(parts[3]) if len(parts) > 3 else 15
            except ValueError:
                snooze_min = 15

            try:
                store.snooze_reminder(reminder_id, delay_minutes=snooze_min)
                self._reset_reminder_cooldown(reminder_id)
            except Exception as e:
                self._answer_callback(cq_id, text=f"Lỗi khi hoãn: {redact_secrets(e)}")
                return

            self._answer_callback(cq_id, text=f"⏰ Đã hoãn {snooze_min} phút.")
            self._remove_message_buttons(cq, note_status=f"⏰ Đã hoãn {snooze_min} phút.")

    def _handle_voice_message(self, msg: dict, voice: dict):
        duration = voice.get("duration") or 0
        if duration > 120:
            self._send_message("⚠️ Tin nhắn thoại quá dài (tối đa 2 phút). Master vui lòng gửi đoạn ngắn hơn giúp Ciel.")
            return

        if not shutil.which("ffmpeg"):
            self._send_message("⚠️ Hệ thống chưa cài đặt ffmpeg để xử lý âm thanh từ Telegram. Vui lòng cài đặt ffmpeg trên máy chủ.")
            return

        file_id = voice.get("file_id")
        if not file_id:
            self._send_message("⚠️ Không tìm thấy file âm thanh trong tin nhắn.")
            return

        saved = self._download_file(file_id, f"voice_{file_id[-10:]}.ogg")
        if saved is None:
            self._send_message("⚠️ Không tải được tin nhắn thoại từ Telegram — Master thử gửi lại giúp Ciel.")
            return

        wav_path = saved.with_suffix(".wav")
        try:
            proc = subprocess.run(
                ["ffmpeg", "-y", "-i", str(saved), "-ar", "16000", "-ac", "1", str(wav_path)],
                capture_output=True,
                timeout=30,
            )
            if proc.returncode != 0 or not wav_path.exists():
                self._send_message("⚠️ Không thể chuyển đổi định dạng âm thanh. Master thử gửi lại giúp Ciel.")
                return

            try:
                import speech_recognition as sr_lib
            except ImportError:
                self._send_message("⚠️ Thư viện nhận dạng giọng nói (SpeechRecognition) chưa được cài đặt trên máy chủ.")
                return

            rec = sr_lib.Recognizer()
            with sr_lib.AudioFile(str(wav_path)) as source:
                audio_data = rec.record(source)
            try:
                text = rec.recognize_google(audio_data, language="vi-VN").strip()
            except getattr(sr_lib, "UnknownValueError", Exception):
                self._send_message("⚠️ Ciel không nghe rõ nội dung trong tin nhắn thoại. Master nói lại hoặc nhắn chữ giúp Ciel nhé.")
                return
            except Exception as e:
                self._send_message(f"⚠️ Lỗi kết nối dịch vụ nhận dạng giọng nói: {redact_secrets(e)}")
                return

            if not text:
                self._send_message("⚠️ Ciel không nghe rõ nội dung trong tin nhắn thoại. Master nói lại hoặc nhắn chữ giúp Ciel nhé.")
                return

            caption = (msg.get("caption") or "").strip()
            full_note = f'[Tin nhắn thoại]: "{text}"'
            if caption:
                full_note += f"\n{caption}"

            self._send_message(f'🎙️ Đã nhận tin nhắn thoại: "{text}"')
            self._inbox.put(full_note)

        finally:
            saved.unlink(missing_ok=True)
            wav_path.unlink(missing_ok=True)

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
            if (not self.user_id and self.chat_id.startswith("-")) or not self._is_authorized_user(cq.get("from")):
                self._answer_callback(cq["id"], text="⚠️ Bạn không có quyền thực hiện thao tác này.")
                print(Fore.YELLOW + "[Telegram] Ignored confirmation from unauthorized user" + Style.RESET_ALL)
                return
            self._answer_callback(cq["id"])
            data = cq.get("data", "")
            if data.startswith("rmd:"):
                self._handle_reminder_callback(cq, data)
                return
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
        if not self._is_authorized_user(msg.get("from")):
            print(Fore.YELLOW + "[Telegram] Ignored message from unauthorized user" + Style.RESET_ALL)
            return

        # Voice / Audio message
        voice = msg.get("voice") or msg.get("audio")
        if voice:
            self._handle_voice_message(msg, voice)
            return

        # Photos / document
        photos = msg.get("photo")
        document = msg.get("document")
        if photos or document:
            if photos:
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

        if text.startswith("/"):
            cmd = text.split()[0].lower().split("@")[0]
            if cmd == "/cancel":
                self.ciel.core.request_cancel("cancelled from Telegram")
                return
            if cmd in ("/start", "/help"):
                self._send_help()
                return
            if cmd == "/today":
                self._handle_cmd_today()
                return
            if cmd == "/reminders":
                self._handle_cmd_reminders()
                return
            if cmd == "/market":
                self._handle_cmd_market()
                return

        self._inbox.put(text)

    # ---------- worker: processes one message at a time through CielCore ----------
    def _worker_loop(self):
        while not self._stop.is_set():
            try:
                text = self._inbox.get(timeout=1)
            except queue.Empty:
                continue
            if text.lower().split("@")[0] in ("/start", "/help"):
                self._send_help()
                continue
            self._send_typing()
            try:
                response = self.ciel.run_step(text, confirm_callback=self.confirm_callback)
            except Exception as e:
                response = f"[Ciel Fatal] {redact_secrets(e)}"
            # The attachment and its caption are already visible in this chat. Do not
            # repost the synthesized report as a second message below it.
            if getattr(self.ciel.core, "_telegram_delivery_sent_this_turn", False):
                continue
            self._send_message(response)

    def start(self):
        self._set_bot_commands()
        threading.Thread(target=self._poll_updates, daemon=True, name="telegram-poll").start()
        threading.Thread(target=self._worker_loop, daemon=True, name="telegram-worker").start()
        print(Fore.BLUE + f"[Telegram] Bot online — listening for chat_id={self.chat_id} only."
              + Style.RESET_ALL)

    def stop(self):
        self._stop.set()
