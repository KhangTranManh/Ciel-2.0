"""Offline regression checks for PendingSlot and turn_context integration (0 LLM, 0 network)."""
from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

# Ensure UTF-8 output
os.environ["PYTHONIOENCODING"] = "utf-8"
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.pending_slot import PendingSlot, PendingSlotSnapshot, redact_unsafe
from core.active_subject import ActiveSubject
from core.context import ContextAssembler, P_REQUEST, P_CRITICAL


def main() -> int:
    checks: list[tuple[str, bool]] = []

    # 1. Location slot matching
    slot = PendingSlot(ttl_seconds=600, max_turns=2)
    snap = slot.observe_response(
        user_input="Dự báo thời tiết giúp tôi",
        response="Bạn muốn xem thông tin thời tiết ở đâu?",
        now=100.0,
    )
    checks.append(("observe_response captures location slot", bool(snap and snap.slot_kind == "location")))
    checks.append(("observe_response captures read intent", bool(snap and snap.intent == "read")))
    note = slot.consume_if_matches("Hà Nội", now=105.0)
    expected_note = "Ciel vừa hỏi location cho yêu cầu: Dự báo thời tiết giúp tôi; Master trả lời: Hà Nội"
    checks.append(("consume_if_matches matches location answer", note == expected_note))
    checks.append(("consume_if_matches consumes slot on match", slot.snapshot is None))

    # 2. Time slot matching
    slot = PendingSlot(ttl_seconds=600, max_turns=2)
    snap = slot.observe_response(
        user_input="Nhắc tôi gọi điện cho khách hàng",
        response="Bạn muốn nhắc vào lúc mấy giờ?",
        now=200.0,
    )
    checks.append(("observe_response captures time slot", bool(snap and snap.slot_kind == "time")))
    note = slot.consume_if_matches("15h30", now=205.0)
    checks.append(("consume_if_matches matches time answer", "15h30" in note and "time" in note))

    # 3. Choice slot matching with options list
    slot = PendingSlot(ttl_seconds=600, max_turns=2)
    resp_with_options = (
        "Tôi tìm thấy 2 bài viết liên quan:\n"
        "1. Giá vàng tăng vọt phiên cuối tuần\n"
        "2. Biến động tỷ giá USD tại các ngân hàng\n"
        "Bạn chọn số mấy?"
    )
    snap = slot.observe_response(
        user_input="Tin tức tài chính hôm nay",
        response=resp_with_options,
        now=300.0,
    )
    checks.append(("observe_response captures choice slot", bool(snap and snap.slot_kind == "choice")))
    checks.append(("observe_response extracts options", bool(snap and len(snap.options) == 2)))
    note = slot.consume_if_matches("2", now=305.0)
    checks.append(("consume_if_matches matches numeric choice", "Master trả lời: 2" in note and "choice" in note))

    # 4. TTL expiration
    slot = PendingSlot(ttl_seconds=600, max_turns=2)
    slot.observe_response(
        user_input="Xem thời tiết",
        response="Bạn muốn xem thời tiết ở đâu?",
        now=400.0,
    )
    expired_note = slot.consume_if_matches("Đà Nẵng", now=1001.0)  # > 600s elapsed
    checks.append(("TTL expires slot", expired_note == "" and slot.snapshot is None))

    # 5. Word count > 6 does not match and clears slot
    slot = PendingSlot(ttl_seconds=600, max_turns=2)
    slot.observe_response(
        user_input="Xem thời tiết",
        response="Bạn muốn xem thời tiết ở đâu?",
        now=500.0,
    )
    long_msg = "Tôi muốn đi du lịch ở thành phố Đà Nẵng vào cuối tuần này"
    long_note = slot.consume_if_matches(long_msg, now=505.0)
    checks.append(("long message (>6 words) does not match slot", long_note == ""))
    checks.append(("long message clears slot", slot.snapshot is None))

    # 6. Explicit new request breaks slot
    slot = PendingSlot(ttl_seconds=600, max_turns=2)
    slot.observe_response(
        user_input="Xem thời tiết",
        response="Bạn muốn xem thời tiết ở đâu?",
        now=600.0,
    )
    new_req_note = slot.consume_if_matches("Giá vàng hôm nay bao nhiêu?", now=605.0)
    checks.append(("explicit new request breaks slot", new_req_note == "" and slot.snapshot is None))

    # 7. Safety: 'ok/có/được' NEVER gives consent for an act intent
    slot = PendingSlot(ttl_seconds=600, max_turns=2)
    snap = slot.observe_response(
        user_input="Xóa toàn bộ các file tạm trong thư mục",
        response="Bạn có chắc chắn muốn xóa không?",
        now=700.0,
    )
    checks.append(("destructive request is classified as act intent", bool(snap and snap.intent == "act")))
    act_ok_note = slot.consume_if_matches("ok", now=705.0)
    checks.append(("'ok' does not match act intent slot", act_ok_note == ""))
    act_co_note = slot.consume_if_matches("có", now=705.0)
    checks.append(("'có' does not match act intent slot", act_co_note == ""))
    act_duoc_note = slot.consume_if_matches("được", now=705.0)
    checks.append(("'được' does not match act intent slot", act_duoc_note == ""))

    # 8. Pure read confirm_topic allows affirmation
    slot = PendingSlot(ttl_seconds=600, max_turns=2)
    snap = slot.observe_response(
        user_input="Tìm kiếm thông tin máy tính",
        response="Tôi đã tìm thấy 3 bài viết. Bạn có muốn xem thêm không?",
        now=800.0,
    )
    checks.append(("read request is classified as read intent", bool(snap and snap.intent == "read")))
    checks.append(("confirm_topic slot classified", bool(snap and snap.slot_kind == "confirm_topic")))
    read_ok_note = slot.consume_if_matches("có", now=805.0)
    checks.append(("read confirm_topic allows 'có'", "Master trả lời: có" in read_ok_note))

    # 9. No path or email or secrets leak into note (redaction test)
    slot = PendingSlot(ttl_seconds=600, max_turns=2)
    slot.observe_response(
        user_input="Gửi email tới test@example.com kèm file C:/secret/report.pdf với token sk-1234567890abcdefghijk",
        response="Bạn muốn gửi vào lúc mấy giờ?",
        now=900.0,
    )
    safe_note = slot.consume_if_matches("10h sáng", now=905.0)
    checks.append(("note generated for time", bool(safe_note and "10h sáng" in safe_note)))
    checks.append(("email is redacted in note", "test@example.com" not in safe_note and "[email]" in safe_note))
    checks.append(("path is redacted in note", "C:/secret/report.pdf" not in safe_note and "[path]" in safe_note))
    checks.append(("secret token is redacted in note", "sk-1234567890abcdefghijk" not in safe_note and "[token]" in safe_note))

    # 10. User input containing path or email is rejected from slot
    slot = PendingSlot(ttl_seconds=600, max_turns=2)
    slot.observe_response(
        user_input="Gửi thông báo",
        response="Bạn muốn gửi tới địa chỉ nào?",
        now=1000.0,
    )
    unsafe_input_note = slot.consume_if_matches("victim@example.com", now=1005.0)
    checks.append(("user input with email is rejected from slot", unsafe_input_note == "" and slot.snapshot is None))

    # 11. Idle turns expiration
    slot = PendingSlot(ttl_seconds=600, max_turns=2)
    slot.observe_response(
        user_input="Tra cứu giá",
        response="Bạn muốn tra cứu mã nào?",
        now=1100.0,
    )
    # Turn 1: normal response without question
    slot.observe_response("Khác 1", "Đây là câu trả lời 1 bình thường.", now=1110.0)
    checks.append(("idle turns incremented to 1", bool(slot.snapshot and slot.snapshot.idle_turns == 1)))
    # Turn 2: normal response without question
    slot.observe_response("Khác 2", "Đây là câu trả lời 2 bình thường.", now=1120.0)
    checks.append(("slot expired after reaching max_turns", slot.snapshot is None))

    # 12. Integration with prepare_decision in turn_context
    from core.harness.turn_context import prepare_decision

    class DummyCore:
        def __init__(self):
            self.base_dir = str(ROOT)
            self.unattended = False
            self.active_subject = ActiveSubject()
            self.pending_slot = PendingSlot(ttl_seconds=600, max_turns=2)
            self.chat_history = None
            self.router = type("DummyRouter", (), {"route": lambda *a, **kw: {"action": "chat", "message": "ok"}})()
            self._tool_list_str = "[]"
            self._open_thread_called = False
            self._OPEN_THREAD_NUDGE_RE = re.compile(r"^tra\b", re.I)
            self._OPEN_THREAD_FILE_DEICTIC_RE = re.compile(r"file đó", re.I)
            self._OPEN_THREAD_EMAIL_DEICTIC_RE = re.compile(r"email đó", re.I)

        def _get_pending_action(self):
            return None

        def _open_thread_note(self, user_input):
            self._open_thread_called = True
            return "OLD_OPEN_THREAD_HEURISTIC"

        def _recent_entities_note(self):
            return ""

        def _wants_past_recall(self, user_input):
            return False

        def _detect_language(self, user_input):
            return "vi"

        def _apply_active_lookup_route_override(self, decision, open_thread):
            return decision

        def _is_email_send_intent(self, user_input, lowered):
            return False

        def _log_thought(self, *args, **kwargs):
            pass

    # Subtest 12a: Pending slot takes priority over _open_thread_note
    core_with_slot = DummyCore()
    now_test = time.time()
    core_with_slot.pending_slot.observe_response(
        user_input="Dự báo thời tiết",
        response="Bạn muốn xem thời tiết ở đâu?",
        now=now_test,
    )
    decision, _ = prepare_decision(core_with_slot, "Hà Nội", "", "")
    checks.append(("pending slot prioritized: old open_thread not called", not core_with_slot._open_thread_called))
    checks.append(("pending slot consumed after routing", core_with_slot.pending_slot.snapshot is None))

    # Subtest 12b: When no pending slot matches, fall back to _open_thread_note
    core_fallback = DummyCore()
    decision2, _ = prepare_decision(core_fallback, "Tin tức hôm nay", "", "")
    checks.append(("fallback to old open_thread when no pending slot", core_fallback._open_thread_called))

    # 13. Regression sanity: ActiveSubject and ContextAssembler
    subj = ActiveSubject()
    subj.observe_tool("stealth_search", {"query": "test query"}, "test result")
    snap = subj.complete_turn("find info", "Found **info**.", "tool", now=1300.0)
    checks.append(("ActiveSubject sanity check", bool(snap and "test query" in snap.topic)))

    ca = ContextAssembler()
    ca.add("req", "test req", P_REQUEST)
    rendered, _ = ca.render(budget_tokens=100)
    checks.append(("ContextAssembler sanity check", "test req" in rendered))

    # Print results
    for name, passed in checks:
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}")

    passed_count = sum(ok for _, ok in checks)
    total_count = len(checks)
    print(f"\nRESULT: {passed_count}/{total_count} passed")
    return 0 if passed_count == total_count else 1


if __name__ == "__main__":
    raise SystemExit(main())
