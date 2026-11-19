"""Bounded, RAM-only clarifying slot handoff for short follow-up answers."""
from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any

# Unsafe content patterns that must never leak into Router prompt
_SK_RE = re.compile(r"sk-[a-zA-Z0-9_\-]{16,}")
_HEX_TOKEN_RE = re.compile(r"\b[a-fA-F0-9]{32,64}\b")
_SECRET_RE = re.compile(
    r"\b(?:api[_-]?key|token|password|secret|authorization|bearer|ssh|cookie)[\s:=]+"
    r"(?!\[(?:token|secret|path|email|cmd)\])([^\s,;]+)",
    re.IGNORECASE,
)
_PATH_RE = re.compile(
    r"(?:[A-Za-z]:[\\/]|/(?:home|srv|etc|var|root|usr)[\\/]|\\\\|[\\w\-./\\]+\.(?:txt|py|json|csv|pdf|docx?|html?|md|log|ya?ml))\b",
    re.IGNORECASE,
)
_EMAIL_RE = re.compile(r"[\w.+%-]+@[\w.-]+\.\w+")
_COMMAND_RE = re.compile(
    r"\b(?:sudo|docker|git|ssh|scp|powershell|cmd(?:\.exe)?|bash|sh|rm\s+-rf|chmod)\b|&&|\|\||`",
    re.IGNORECASE,
)

# Intent classification regex for original request
_ACT_INTENT_RE = re.compile(
    r"\b(?:gửi|send|tạo|create|viết|write|xóa|delete|remove|sửa|edit|update|modify|thay\s*đổi|"
    r"đặt\s*lịch|schedule|thêm|add|append|chạy|run|exec|execute|hủy|cancel|mua|bán|post|push|commit)\b",
    re.IGNORECASE,
)

# Question detection at end of Ciel's response
_QUESTION_END_RE = re.compile(r"[?？][\s\"'’\)]*$", re.MULTILINE)

# Option extraction (numbered or bullet list)
_OPTION_LINE_RE = re.compile(r"(?:^|\n)\s*(?:[1-9]\d*[\.\)]|[-•*])\s*([^\n]+)")

# Slot kind classification regexes
_LOCATION_RE = re.compile(
    r"ở\s*đâu|thành\s*phố\s*nào|địa\s*điểm(?:\s*nào)?|nơi\s*nào|tỉnh\s*nào|quận\s*nào|huyện\s*nào|chỗ\s*nào|"
    r"which\s+(?:city|location)|where\b",
    re.IGNORECASE,
)
_CHOICE_RE = re.compile(
    r"chọn(?:\s+phương\s+án|\s+số|\s+gì|\s+nào)?|lựa\s*chọn|phương\s*án\s*nào|số\s*mấy|"
    r"\b1\s+hay\s+2\b|which\s+(?:option|one)\b",
    re.IGNORECASE,
)
_DATE_RE = re.compile(
    r"ngày\s*(?:nào|bao\s*nhiêu)|thứ\s*mấy|hôm\s*nào|which\s+date|what\s+day\b",
    re.IGNORECASE,
)
_TIME_RE = re.compile(
    r"mấy\s*giờ|khi\s*nào|lúc\s*mấy\s*giờ|lúc\s*nào|bao\s*giờ|khoảng\s*mấy\s*giờ|thời\s*gian\s*nào|"
    r"what\s+time|when\b",
    re.IGNORECASE,
)
_QUANTITY_RE = re.compile(
    r"bao\s*nhiêu|số\s*lượng|mấy\s*(?:cái|người|phần|bản)|how\s+many|how\s+much\b",
    re.IGNORECASE,
)
_NAME_RE = re.compile(
    r"tên\s*(?:gì|là\s*gì|người\s*nào)|ai\b|người\s*nào|what\s+name|who\b",
    re.IGNORECASE,
)
_CONFIRM_TOPIC_RE = re.compile(
    r"xác\s*nhận|muốn\s+.*?không|có\s*muốn\s+.*?không|chắc\s*chắn\s*không|đồng\s*ý\s*không|bạn\s*có\s*muốn|confirm\b",
    re.IGNORECASE,
)

# Explicit new request patterns that override and discard a pending slot
_EXPLICIT_NEW_REQUEST_RE = re.compile(
    r"^(?:hãy\b|giúp\b|vui\s*lòng\b|tìm\s+kiếm\b|search\b|tra\s+cứu\b|xem\s+(?:thời\s+tiết|giá|tin)|"
    r"thời\s*tiết\b|weather\b|giá\s+(?:vàng|btc|crypto)|tin\s+tức\b|gửi\s+(?:mail|email)|tạo\s+file|"
    r"viết\s+code|chạy\s+lệnh|bật\b|tắt\b)",
    re.IGNORECASE,
)

# Common affirmation words
_AFFIRMATION_WORDS = {"có", "được", "đồng ý", "ok", "yes", "chắc chắn", "tiếp tục"}
_DENIAL_WORDS = {"không", "hủy", "thôi", "no", "cancel"}


def redact_unsafe(text: str) -> str:
    """Sanitize secrets, paths, emails, and shell commands from text."""
    if not text:
        return ""
    text = _SK_RE.sub("[token]", text)
    text = _HEX_TOKEN_RE.sub("[token]", text)
    text = _SECRET_RE.sub("[secret]", text)
    text = _EMAIL_RE.sub("[email]", text)
    text = _PATH_RE.sub("[path]", text)
    text = _COMMAND_RE.sub("[cmd]", text)
    return " ".join(text.split()).strip()


@dataclass(frozen=True)
class PendingSlotSnapshot:
    slot_kind: str  # location | time | date | choice | quantity | name | confirm_topic | free
    original_request: str  # redacted, <= 200 chars
    intent: str  # read | act
    options: tuple[str, ...] = field(default_factory=tuple)
    asked_at: float = 0.0
    turn_index: int = 0
    idle_turns: int = 0


class PendingSlot:
    """Maintain one RAM-only pending clarifying question for short follow-ups."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        ttl_seconds: int = 600,
        max_turns: int = 2,
    ):
        self.enabled = bool(enabled)
        self.ttl_seconds = max(1, int(ttl_seconds))
        self.max_turns = max(1, int(max_turns))
        self._snapshot: PendingSlotSnapshot | None = None
        self._lock = threading.RLock()

    @property
    def snapshot(self) -> PendingSlotSnapshot | None:
        with self._lock:
            return self._snapshot

    def clear(self) -> None:
        with self._lock:
            self._snapshot = None

    def observe_response(
        self,
        user_input: str,
        response: str,
        action: str = "",
        now: float | None = None,
        turn_index: int = 0,
    ) -> PendingSlotSnapshot | None:
        """Capture clarifying slot if Ciel's response ends with a clarifying question."""
        if not self.enabled:
            return None
        now = time.time() if now is None else float(now)
        resp_clean = (response or "").strip()

        with self._lock:
            # Check if current response ends with a question
            if not resp_clean or not _QUESTION_END_RE.search(resp_clean):
                # Age or clear existing slot
                if self._snapshot is not None:
                    idle = self._snapshot.idle_turns + 1
                    if idle >= self.max_turns or (now - self._snapshot.asked_at) > self.ttl_seconds:
                        self._snapshot = None
                    else:
                        self._snapshot = PendingSlotSnapshot(
                            slot_kind=self._snapshot.slot_kind,
                            original_request=self._snapshot.original_request,
                            intent=self._snapshot.intent,
                            options=self._snapshot.options,
                            asked_at=self._snapshot.asked_at,
                            turn_index=self._snapshot.turn_index,
                            idle_turns=idle,
                        )
                return self._snapshot

            # Extract the question segment (last sentence or line ending with ?)
            lines = [line.strip() for line in resp_clean.splitlines() if line.strip()]
            q_line = lines[-1] if lines else resp_clean

            # Options extraction
            options: list[str] = []
            for opt_match in _OPTION_LINE_RE.findall(resp_clean):
                clean_opt = redact_unsafe(opt_match.strip())
                if clean_opt and len(clean_opt) <= 100:
                    options.append(clean_opt)

            # Classify slot kind
            slot_kind = self._classify_slot_kind(q_line, options)

            # Sanitize original request: redact secrets, paths, emails, commands
            redacted_req = redact_unsafe(user_input or "")
            if len(redacted_req) > 200:
                redacted_req = redacted_req[:199].rstrip() + "…"

            # Intent classification
            intent = "act" if _ACT_INTENT_RE.search(user_input or "") else "read"

            self._snapshot = PendingSlotSnapshot(
                slot_kind=slot_kind,
                original_request=redacted_req,
                intent=intent,
                options=tuple(options[:10]),
                asked_at=now,
                turn_index=turn_index,
                idle_turns=0,
            )
            return self._snapshot

    def has_active_slot(self, user_input: str = "", now: float | None = None) -> bool:
        """Check if an unexpired slot is waiting and candidate matches."""
        if not self.enabled:
            return False
        now = time.time() if now is None else float(now)
        with self._lock:
            snap = self._snapshot
            if snap is None:
                return False
            if (now - snap.asked_at) > self.ttl_seconds or snap.idle_turns >= self.max_turns:
                self._snapshot = None
                return False
            if user_input:
                return self._is_short_compatible_answer(user_input, snap)
            return True

    def consume_if_matches(self, user_input: str, now: float | None = None) -> str:
        """Match and consume slot for a short answer; return Router note or empty string."""
        if not self.enabled:
            return ""
        now = time.time() if now is None else float(now)
        text = (user_input or "").strip()
        if not text:
            return ""

        with self._lock:
            snap = self._snapshot
            if snap is None:
                return ""

            # Check expiration
            if (now - snap.asked_at) > self.ttl_seconds or snap.idle_turns >= self.max_turns:
                self._snapshot = None
                return ""

            # Explicit new question / new request breaks slot
            if "?" in text or "？" in text or _EXPLICIT_NEW_REQUEST_RE.search(text):
                self._snapshot = None
                return ""

            # Safety check: input must not contain raw paths, emails, secrets, or commands
            if (_SK_RE.search(text) or _HEX_TOKEN_RE.search(text) or _SECRET_RE.search(text)
                    or _PATH_RE.search(text) or _EMAIL_RE.search(text) or _COMMAND_RE.search(text)):
                self._snapshot = None
                return ""

            # Word count check: short follow-up only (<= 6 words, <= 80 chars)
            words = re.findall(r"\S+", text)
            if len(words) > 6 or len(text) > 80:
                self._snapshot = None
                return ""

            # Type compatibility check
            if not self._is_short_compatible_answer(text, snap):
                self._snapshot = None
                return ""

            # Matched! Consume single-use slot
            self._snapshot = None
            clean_answer = redact_unsafe(text)
            return (
                f"Ciel vừa hỏi {snap.slot_kind} cho yêu cầu: {snap.original_request}; "
                f"Master trả lời: {clean_answer}"
            )

    def _classify_slot_kind(self, q_text: str, options: list[str]) -> str:
        """Deterministic slot kind classification based on Vietnamese clarifying patterns."""
        if _CHOICE_RE.search(q_text) or (len(options) >= 2 and re.search(r"nào|chọn|which", q_text, re.I)):
            return "choice"
        if _LOCATION_RE.search(q_text):
            return "location"
        if _DATE_RE.search(q_text):
            return "date"
        if _TIME_RE.search(q_text):
            return "time"
        if _QUANTITY_RE.search(q_text):
            return "quantity"
        if _NAME_RE.search(q_text):
            return "name"
        if _CONFIRM_TOPIC_RE.search(q_text):
            return "confirm_topic"
        return "free"

    def _is_short_compatible_answer(self, text: str, snap: PendingSlotSnapshot) -> bool:
        """Verify that the short answer matches the expected slot kind and passes safety."""
        clean = text.strip()
        folded = clean.casefold()

        # CRITICAL SAFETY INVARIANT:
        # "ok/có/được" KHÔNG được biến thành đồng ý một hành động nguy hiểm qua slot.
        if snap.intent == "act" and folded in _AFFIRMATION_WORDS:
            return False

        kind = snap.slot_kind

        if kind == "choice":
            # Direct numeric choice or option label
            if clean.isdigit():
                return True
            if re.match(r"^(?:số|cái|phương\s*án|option|lựa\s*chọn)?\s*(?:[0-9]+|[a-dA-D])$", clean, re.I):
                return True
            # Matches one of the presented options
            if snap.options:
                for opt in snap.options:
                    opt_fold = opt.casefold()
                    if folded in opt_fold or opt_fold.startswith(folded):
                        return True
            return False

        if kind == "location":
            # Location: 1 to 4 words, no action verbs
            words = re.findall(r"\S+", clean)
            if not (1 <= len(words) <= 4):
                return False
            if _ACT_INTENT_RE.search(clean):
                return False
            # Exclude standalone affirmations/denials
            if folded in _AFFIRMATION_WORDS or folded in _DENIAL_WORDS:
                return False
            return True

        if kind in ("time", "date"):
            # Time / date formats or relative expressions
            time_pat = re.compile(
                r"\b(?:\d{1,2}[:h]\d{0,2}|\d{1,2}\s*(?:h|giờ|am|pm)|"
                r"sáng\s*mai|chiều\s*mai|tối\s*nay|trưa\s*nay|bây\s*giờ|lát\s*nữa|\d+\s*phút\s*nữa|"
                r"hôm\s*nay|ngày\s*mai|ngày\s*kia|hôm\s*qua|thứ\s*[2-7]|thứ\s*hai|thứ\s*ba|thứ\s*tư|"
                r"thứ\s*năm|thứ\s*sáu|thứ\s*bảy|chủ\s*nhật|tuần\s*sau|today|tomorrow|now)\b",
                re.IGNORECASE,
            )
            if time_pat.search(clean) or clean.isdigit():
                return True
            return False

        if kind == "quantity":
            qty_pat = re.compile(
                r"^\d+(?:[.,]\d+)?(?:\s*(?:cái|người|chiếc|bản|phần|kg|g|triệu|k|usd|vnd|đ))?$|"
                r"^(?:vài\s*cái|tất\s*cả|all)$",
                re.IGNORECASE,
            )
            return bool(qty_pat.match(clean))

        if kind == "name":
            words = re.findall(r"\S+", clean)
            if not (1 <= len(words) <= 4):
                return False
            if _ACT_INTENT_RE.search(clean):
                return False
            if folded in _AFFIRMATION_WORDS or folded in _DENIAL_WORDS:
                return False
            return True

        if kind == "confirm_topic":
            if snap.intent == "act":
                # Intent act never accepts affirmation via slot
                return False
            return folded in _AFFIRMATION_WORDS or folded in _DENIAL_WORDS

        if kind == "free":
            # Generic short answer: 1-4 words, not action verb
            words = re.findall(r"\S+", clean)
            if 1 <= len(words) <= 4 and not _ACT_INTENT_RE.search(clean):
                return True
            return False

        return False
