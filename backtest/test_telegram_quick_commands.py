"""backtest/test_telegram_quick_commands.py — Offline test suite for Telegram quick commands,
reminder action buttons (Done/Snooze), and voice note ingestion.
No network, no live LLM calls, temporary PlannerStore.
"""
from __future__ import annotations

import os
import sys
import shutil
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from agent_system import config
from core.planner_store import PlannerStore
from core.telegram_interface import TelegramInterface

passed_count = 0
failed_count = 0


def check(description: str, condition: bool) -> None:
    global passed_count, failed_count
    if condition:
        passed_count += 1
        print(f"  [PASS] {description}")
    else:
        failed_count += 1
        print(f"  [FAIL] {description}")


class MockCielCore:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.cancelled = False

    def request_cancel(self, reason: str = ""):
        self.cancelled = True


class MockCielAgent:
    def __init__(self, base_dir: Path):
        self.core = MockCielCore(base_dir)
        self.scheduler = MagicMock()


class FakeBot(TelegramInterface):
    """Subclass of TelegramInterface that stubs out network calls."""
    def __init__(self, ciel_agent, chat_id: str = "100", user_id: str = "42"):
        self.sent_messages: list[dict] = []
        self.answered_callbacks: list[dict] = []
        self.edited_messages: list[dict] = []
        self.api_calls: list[tuple[str, dict]] = []
        super().__init__(ciel_agent, bot_token="fake:token", chat_id=chat_id, user_id=user_id)

    def _call(self, method: str, **params) -> dict:
        self.api_calls.append((method, params))
        if method == "sendMessage":
            self.sent_messages.append(params)
            return {"ok": True, "result": {"message_id": len(self.sent_messages)}}
        elif method == "answerCallbackQuery":
            self.answered_callbacks.append(params)
            return {"ok": True, "result": True}
        elif method in ("editMessageText", "editMessageReplyMarkup"):
            self.edited_messages.append(params)
            return {"ok": True, "result": True}
        elif method == "setMyCommands":
            return {"ok": True, "result": True}
        elif method == "getFile":
            return {"ok": True, "result": {"file_path": "mock/voice.oga"}}
        return {"ok": True, "result": {}}

    def _download_file(self, file_id: str, suggested_name: str) -> Path | None:
        upload_dir = self.ciel.core.base_dir / "ciel_workspace" / "telegram_uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        dest = upload_dir / f"test_{suggested_name}"
        dest.write_bytes(b"FAKE_AUDIO_BYTES")
        return dest


def test_quick_commands(tmp_path: Path):
    print("\n[1] Telegram quick commands (/today, /reminders, /market, /help, /start, /cancel)")
    db_path = tmp_path / "planner_test.db"
    orig_db = config.PLANNER_DB_PATH
    config.PLANNER_DB_PATH = db_path
    try:
        store = PlannerStore(db_path)
        ciel = MockCielAgent(tmp_path)
        bot = FakeBot(ciel, chat_id="100", user_id="42")

        # 1. /help
        bot._handle_update({
            "message": {
                "chat": {"id": 100},
                "from": {"id": 42},
                "text": "/help",
            }
        })
        check("/help produces response without queuing into inbox",
              bot._inbox.empty() and len(bot.sent_messages) == 1 and "/today" in bot.sent_messages[0]["text"])

        # 2. /start
        bot.sent_messages.clear()
        bot._handle_update({
            "message": {
                "chat": {"id": 100},
                "from": {"id": 42},
                "text": "/start",
            }
        })
        check("/start produces help menu",
              bot._inbox.empty() and len(bot.sent_messages) == 1 and "Ciel" in bot.sent_messages[0]["text"])

        # 3. /cancel
        bot._handle_update({
            "message": {
                "chat": {"id": 100},
                "from": {"id": 42},
                "text": "/cancel",
            }
        })
        check("/cancel requests cancellation directly", ciel.core.cancelled)

        # 4. /today when empty
        bot.sent_messages.clear()
        bot._handle_update({
            "message": {
                "chat": {"id": 100},
                "from": {"id": 42},
                "text": "/today",
            }
        })
        check("/today when empty sends 'Không có lịch hẹn hay việc cần làm'",
              bot._inbox.empty() and len(bot.sent_messages) == 1 and "Không có lịch hẹn" in bot.sent_messages[0]["text"])

        # 5. Populate planner and test /today and /reminders
        now_dt = datetime.now(timezone.utc)
        today_iso = now_dt.strftime("%Y-%m-%dT%H:%M:%S")
        rem1, _ = store.add_reminder("Uống thuốc buổi tối", today_iso, timezone_name="UTC")
        store.add_weekly_task(
            week_start=(now_dt.date() - timedelta(days=now_dt.weekday())).isoformat(),
            title="Kiểm tra hệ thống VPS",
            weekday=now_dt.weekday(),
        )

        bot.sent_messages.clear()
        bot._handle_update({
            "message": {
                "chat": {"id": 100},
                "from": {"id": 42},
                "text": "/today",
            }
        })
        check("/today with items lists reminders and weekly tasks",
              bot._inbox.empty() and "Uống thuốc buổi tối" in bot.sent_messages[0]["text"] and "Kiểm tra hệ thống VPS" in bot.sent_messages[0]["text"])

        # 6. /reminders
        bot.sent_messages.clear()
        bot._handle_update({
            "message": {
                "chat": {"id": 100},
                "from": {"id": 42},
                "text": "/reminders",
            }
        })
        check("/reminders lists pending reminders",
              bot._inbox.empty() and "Uống thuốc buổi tối" in bot.sent_messages[0]["text"])

        # 7. /market (mock fetch_market_price)
        bot.sent_messages.clear()
        with patch("skills.external.trading_ops.fetch_market_price", side_effect=lambda s: f"Giá {s}: 2650.5" if "XAU" in s else f"Giá {s}: 91000"):
            bot._handle_update({
                "message": {
                    "chat": {"id": 100},
                    "from": {"id": 42},
                    "text": "/market",
                }
            })
        check("/market returns gold and BTC prices",
              bot._inbox.empty() and len(bot.sent_messages) == 1 and "XAU/USD" in bot.sent_messages[0]["text"] and "BTC/USDT" in bot.sent_messages[0]["text"])

    finally:
        config.PLANNER_DB_PATH = orig_db


def test_unauthorized_blocking(tmp_path: Path):
    print("\n[2] Unauthorized chat/user blocking")
    ciel = MockCielAgent(tmp_path)
    bot = FakeBot(ciel, chat_id="100", user_id="42")

    # 1. Message from wrong chat_id
    bot._handle_update({
        "message": {
            "chat": {"id": 999},
            "from": {"id": 42},
            "text": "/today",
        }
    })
    check("message from unauthorized chat is ignored", bot._inbox.empty() and len(bot.sent_messages) == 0)

    # 2. Message from wrong user_id
    bot._handle_update({
        "message": {
            "chat": {"id": 100},
            "from": {"id": 999},
            "text": "/today",
        }
    })
    check("message from unauthorized user is ignored", bot._inbox.empty() and len(bot.sent_messages) == 0)

    # 3. Callback from unauthorized user
    bot._handle_update({
        "callback_query": {
            "id": "cb_unauth",
            "from": {"id": 999},
            "data": "rmd:done:1",
        }
    })
    check("callback from unauthorized user is rejected",
          any(call.get("callback_query_id") == "cb_unauth" and "không có quyền" in call.get("text", "") for call in bot.answered_callbacks))


def test_reminder_callbacks(tmp_path: Path):
    print("\n[3] Reminder callbacks (Done, Snooze, Idempotency, Invalid IDs)")
    db_path = tmp_path / "planner_rem.db"
    orig_db = config.PLANNER_DB_PATH
    config.PLANNER_DB_PATH = db_path
    try:
        store = PlannerStore(db_path)
        ciel = MockCielAgent(tmp_path)
        bot = FakeBot(ciel, chat_id="100", user_id="42")

        now_dt = datetime.now(timezone.utc)
        due_iso = now_dt.strftime("%Y-%m-%dT%H:%M:%S")
        rem1, _ = store.add_reminder("Nhắc việc 1", due_iso, timezone_name="UTC")
        rem_id = rem1["id"]

        # 1. Click "✅ Xong"
        bot._handle_update({
            "callback_query": {
                "id": "cb1",
                "from": {"id": 42},
                "data": f"rmd:done:{rem_id}",
                "message": {"message_id": 10, "text": "Nhắc việc\nNhắc việc 1"},
            }
        })
        updated = store.get_reminder(rem_id)
        check("done callback marks pending reminder delivered", updated["status"] == "delivered")
        check("done callback answers callback query", any(call.get("callback_query_id") == "cb1" and "hoàn thành" in call.get("text", "") for call in bot.answered_callbacks))
        check("done callback edits message and removes buttons", any(edit.get("message_id") == 10 and "✅ Đã hoàn thành" in edit.get("text", "") for edit in bot.edited_messages))

        # 2. Idempotent second click on Done
        bot.answered_callbacks.clear()
        bot._handle_update({
            "callback_query": {
                "id": "cb1_repeat",
                "from": {"id": 42},
                "data": f"rmd:done:{rem_id}",
                "message": {"message_id": 10, "text": "Nhắc việc\nNhắc việc 1\n\n✅ Đã hoàn thành."},
            }
        })
        check("repeat done callback is idempotent and confirms already processed",
              any(call.get("callback_query_id") == "cb1_repeat" and "đã được xử lý" in call.get("text", "") for call in bot.answered_callbacks))

        # 3. Snooze callback
        rem2, _ = store.add_reminder("Nhắc việc 2", due_iso, timezone_name="UTC")
        rem2_id = rem2["id"]
        # simulate delivery before user taps snooze
        store.mark_reminder_delivered(rem2_id)
        check("rem2 is initially delivered", store.get_reminder(rem2_id)["status"] == "delivered")

        bot.answered_callbacks.clear()
        bot.edited_messages.clear()
        bot._handle_update({
            "callback_query": {
                "id": "cb2_snooze",
                "from": {"id": 42},
                "data": f"rmd:snooze:{rem2_id}:15",
                "message": {"message_id": 20, "text": "Nhắc việc\nNhắc việc 2"},
            }
        })
        snoozed = store.get_reminder(rem2_id)
        check("snooze resets status to pending", snoozed["status"] == "pending")
        check("snooze advances due_at_utc by ~15 minutes",
              datetime.fromisoformat(snoozed["due_at_utc"]) > now_dt + timedelta(minutes=14))
        check("snooze answers callback query",
              any(call.get("callback_query_id") == "cb2_snooze" and "hoãn 15 phút" in call.get("text", "") for call in bot.answered_callbacks))
        check("snooze edits message to reflect hoãn 15 phút",
              any(edit.get("message_id") == 20 and "⏰ Đã hoãn 15 phút" in edit.get("text", "") for edit in bot.edited_messages))

        # 4. Invalid reminder ID
        bot.answered_callbacks.clear()
        bot._handle_update({
            "callback_query": {
                "id": "cb_invalid",
                "from": {"id": 42},
                "data": "rmd:done:99999",
                "message": {"message_id": 30, "text": "Nhắc việc cũ"},
            }
        })
        check("invalid reminder ID answers with error",
              any(call.get("callback_query_id") == "cb_invalid" and "không tồn tại" in call.get("text", "") for call in bot.answered_callbacks))

        # 5. Cancelled reminder
        rem3, _ = store.add_reminder("Nhắc việc 3", due_iso, timezone_name="UTC")
        store.cancel_reminder(rem3["id"])
        bot.answered_callbacks.clear()
        bot._handle_update({
            "callback_query": {
                "id": "cb_cancelled",
                "from": {"id": 42},
                "data": f"rmd:done:{rem3['id']}",
                "message": {"message_id": 40, "text": "Nhắc việc đã hủy"},
            }
        })
        check("cancelled reminder answers with already cancelled",
              any(call.get("callback_query_id") == "cb_cancelled" and "đã bị hủy" in call.get("text", "") for call in bot.answered_callbacks))

    finally:
        config.PLANNER_DB_PATH = orig_db


def test_existing_confirmation_callbacks(tmp_path: Path):
    print("\n[4] Existing Yes/No confirmation callback compatibility")
    ciel = MockCielAgent(tmp_path)
    bot = FakeBot(ciel, chat_id="100", user_id="42")

    # Yes callback
    event_yes = threading.Event()
    res_yes = {"approved": False}
    bot._pending_confirm["cf_test_yes"] = (event_yes, res_yes)
    bot._handle_update({
        "callback_query": {
            "id": "cb_cf_yes",
            "from": {"id": 42},
            "data": "cf_test_yes:yes",
        }
    })
    check("Yes confirmation callback still works and approves", event_yes.is_set() and res_yes["approved"] is True)

    # No callback
    event_no = threading.Event()
    res_no = {"approved": True}
    bot._pending_confirm["cf_test_no"] = (event_no, res_no)
    bot._handle_update({
        "callback_query": {
            "id": "cb_cf_no",
            "from": {"id": 42},
            "data": "cf_test_no:no",
        }
    })
    check("No confirmation callback still works and denies", event_no.is_set() and res_no["approved"] is False)


def test_voice_notes(tmp_path: Path):
    print("\n[5] Voice note handling (Duration limit, ffmpeg check, transcription, queueing)")
    ciel = MockCielAgent(tmp_path)
    bot = FakeBot(ciel, chat_id="100", user_id="42")

    # 1. Voice note too long (> 120s)
    bot.sent_messages.clear()
    bot._handle_update({
        "message": {
            "chat": {"id": 100},
            "from": {"id": 42},
            "voice": {"file_id": "v_long", "duration": 130},
        }
    })
    check("voice note > 120s is rejected with clear Vietnamese message",
          len(bot.sent_messages) == 1 and "quá dài" in bot.sent_messages[0]["text"] and bot._inbox.empty())

    # 2. Missing ffmpeg check
    bot.sent_messages.clear()
    with patch("shutil.which", return_value=None):
        bot._handle_update({
            "message": {
                "chat": {"id": 100},
                "from": {"id": 42},
                "voice": {"file_id": "v_valid", "duration": 30},
            }
        })
    check("missing ffmpeg is detected and reported with helpful message",
          len(bot.sent_messages) == 1 and "chưa cài đặt ffmpeg" in bot.sent_messages[0]["text"] and bot._inbox.empty())

    # 3. Successful voice transcription
    bot.sent_messages.clear()
    with patch("shutil.which", return_value="/usr/bin/ffmpeg"):
        with patch("subprocess.run") as mock_proc:
            mock_proc.return_value = MagicMock(returncode=0)
            with patch("pathlib.Path.exists", return_value=True):
                # Mock speech_recognition
                mock_sr = MagicMock()
                mock_recognizer = MagicMock()
                mock_recognizer.recognize_google.return_value = "nhắc tôi họp lúc 3 giờ chiều"
                mock_sr.Recognizer.return_value = mock_recognizer
                mock_sr.AudioFile = MagicMock()

                with patch.dict(sys.modules, {"speech_recognition": mock_sr}):
                    bot._handle_update({
                        "message": {
                            "chat": {"id": 100},
                            "from": {"id": 42},
                            "voice": {"file_id": "v_ok", "duration": 10},
                        }
                    })

    check("successful voice note acknowledges heard text to user",
          any("Đã nhận tin nhắn thoại" in msg["text"] and "nhắc tôi họp lúc 3 giờ chiều" in msg["text"] for msg in bot.sent_messages))
    check("successful voice note puts formatted note into inbox",
          not bot._inbox.empty() and '[Tin nhắn thoại]: "nhắc tôi họp lúc 3 giờ chiều"' in bot._inbox.get())

    # 4. Voice note unrecognized (UnknownValueError)
    bot.sent_messages.clear()
    class MockUnknownValueError(Exception):
        pass

    with patch("shutil.which", return_value="/usr/bin/ffmpeg"):
        with patch("subprocess.run") as mock_proc:
            mock_proc.return_value = MagicMock(returncode=0)
            with patch("pathlib.Path.exists", return_value=True):
                mock_sr = MagicMock()
                mock_recognizer = MagicMock()
                mock_recognizer.recognize_google.side_effect = MockUnknownValueError("noisy")
                mock_sr.Recognizer.return_value = mock_recognizer
                mock_sr.UnknownValueError = MockUnknownValueError
                mock_sr.AudioFile = MagicMock()

                with patch.dict(sys.modules, {"speech_recognition": mock_sr}):
                    bot._handle_update({
                        "message": {
                            "chat": {"id": 100},
                            "from": {"id": 42},
                            "voice": {"file_id": "v_noisy", "duration": 5},
                        }
                    })

    check("unrecognized voice note asks user clearly without crash",
          any("không nghe rõ" in msg["text"] for msg in bot.sent_messages) and bot._inbox.empty())


def main():
    tmp_dir = Path(tempfile.mkdtemp(prefix="ciel_tg_test_"))
    try:
        test_quick_commands(tmp_dir)
        test_unauthorized_blocking(tmp_dir)
        test_reminder_callbacks(tmp_dir)
        test_existing_confirmation_callbacks(tmp_dir)
        test_voice_notes(tmp_dir)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    total = passed_count + failed_count
    print(f"\nRESULT: {passed_count}/{total} passed")
    if failed_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
