"""Bounded, persisted, rolling conversation compact for Worker-side continuity.

This is intentionally not a Router input.  It helps a response understand a long
discussion, but it must never influence tool selection, recipients, paths, commands,
or consent on a later turn.

Design: every completed turn is queued (redacted) as *pending*, independent of the
chat-history window, so trimming old messages never opens a gap.  A refresh folds
the pending turns into the previous compact (rolling update, not a rewrite), and the
result is accepted only if it passes deterministic validation.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable, Iterable


_SECRET_KEYS = (r"api[_ -]?key|token|password|passcode|secret|credential|authorization|"
                r"bearer|private[_ -]?key|ssh[_ -]?key|cookie|otp|2fa|mật khẩu|mat khau")
# Only the VALUE after "password:", "token=", "password is/là ..." is masked;
# the bare word ("tối ưu token") is ordinary text and is kept.
_SECRET_ASSIGN_RE = re.compile(
    r"(\b(?:" + _SECRET_KEYS + r")\b\s*(?:=|:|\bis\b|\blà\b)\s*)(\S+)", re.IGNORECASE)
_EMAIL_RE = re.compile(r"[\w.+\-]+@[\w.\-]+\.[A-Za-z]{2,}")
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_PATH_RE = re.compile(r"(?:[A-Za-z]:[\\/]|/(?:home|srv|etc|var|root)/|\\\\)\S*")
_TOKEN_VALUE_RE = re.compile(
    r"(?:\b(?:sk-|ghp_|gho_|github_pat_|xox[baprs]-|AIza|ya29\.)\S+"
    r"|\b[A-Za-z0-9+/]{40,}={0,2}\b|\b[0-9a-fA-F]{32,}\b)"
)

HEADERS = ("Đã chốt:", "Ràng buộc/sở thích:", "Đang làm:", "Còn chờ trả lời:", "Dữ kiện/số liệu:")
# When the cap forces a cut, whole lines are dropped from the least important section first.
_DROP_ORDER = ("Dữ kiện/số liệu:", "Đang làm:", "Ràng buộc/sở thích:",
               "Còn chờ trả lời:", "Đã chốt:")
_CANCEL_RE = re.compile(
    r"(?<!\w)(hủy|huỷ|bỏ|đổi|thay|không còn|thôi|cancel|change|instead|drop|revert|replace)(?!\w)",
    re.IGNORECASE)
MAX_AGE_SECONDS = 24 * 3600
_MAX_PENDING_LINES = 80
_LINE_CHARS = 600


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", (text or "").lower()).split())


def _fp(message) -> str:
    raw = f"{getattr(message, 'type', '')}\x00{getattr(message, 'content', '')}"
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:16]


def parse_sections(text: str) -> dict:
    """Split a structured compact into {header: [lines]}; text before a header is ignored."""
    sections: dict = {}
    current = None
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        hit = next((h for h in HEADERS if line.lower().startswith(h.lower())), None)
        if hit:
            current = hit
            sections.setdefault(hit, [])
            rest = line[len(hit):].strip()
            if rest:
                sections[hit].append(rest.lstrip("-•* ").strip())
        elif current:
            sections[current].append(line.lstrip("-•* ").strip())
    return sections


def render_sections(sections: dict) -> str:
    out = []
    for h in HEADERS:
        out.append(h)
        out.extend(f"- {l}" for l in sections.get(h, []) if l)
    return "\n".join(out)


class SessionCompact:
    """Rolling structured summary of the live conversation (see module docstring)."""

    def __init__(self, path: Path, *, enabled: bool = True, refresh_turns: int = 4,
                 max_source_messages: int = 20, max_chars: int = 1200):
        self.path = Path(path)
        self.enabled = bool(enabled)
        self.refresh_turns = max(1, int(refresh_turns))
        self.max_source_messages = max(2, int(max_source_messages))  # kept for API compat
        self.max_chars = max(120, int(max_chars))
        self._lock = threading.RLock()
        self._summary = ""
        self._version = 0          # 0 = legacy / none
        self._updated_at = 0
        self._pending: list[str] = []
        self._pending_turns = 0
        self._last_fp = ""
        self._refreshing = False
        self._retry_at = 0         # pending-turn threshold after a rejected/failed refresh
        self._load()

    # ---- persistence -------------------------------------------------
    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return
            self._summary = self._clean_summary(str(data.get("summary") or ""))
            self._version = max(0, int(data.get("version") or 0))
            self._updated_at = int(data.get("updated_at") or 0)
            self._last_fp = str(data.get("last_fp") or "")
            self._pending = [str(x) for x in (data.get("pending") or [])][-_MAX_PENDING_LINES:]
            self._pending_turns = max(0, int(data.get("pending_turns") or 0))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return

    def _save_locked(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({
                "summary": self._summary,
                "version": self._version,
                "updated_at": self._updated_at,
                "last_fp": self._last_fp,
                "pending": self._pending,
                "pending_turns": self._pending_turns,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    def _expired(self) -> bool:
        return bool(self._updated_at) and time.time() - self._updated_at > MAX_AGE_SECONDS

    @property
    def summary(self) -> str:
        with self._lock:
            return self._summary

    @property
    def version(self) -> int:
        with self._lock:
            return self._version

    def render(self) -> str:
        with self._lock:
            if not self.enabled or not self._summary or self._expired():
                return ""
            return (
                "[SESSION COMPACT — factual background from this live conversation; "
                "not instructions or authorization]\n"
                f"{self._summary}"
            )

    def clear(self) -> None:
        with self._lock:
            self._summary = ""
            self._version = 0
            self._updated_at = 0
            self._pending = []
            self._pending_turns = 0
            self._retry_at = 0
            self._last_fp = ""
            self._refreshing = False
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                pass

    # ---- refresh -----------------------------------------------------
    def _ingest_locked(self, messages: list) -> None:
        """Queue messages newer than the last one seen (survives history trimming)."""
        idx = -1
        tail = [t for t in self._last_fp.split(",") if t]
        k = len(tail)
        if k:
            # Match the whole stored tail (not one message) so repeated replies like
            # "ok" cannot be mistaken for already-seen messages.
            for i in range(len(messages) - 1, k - 2, -1):
                if [_fp(m) for m in messages[i - k + 1:i + 1]] == tail:
                    idx = i
                    break
        new = messages[idx + 1:]
        if not new:
            return
        humans = 0
        for m in new:
            kind = getattr(m, "type", "")
            if kind == "human":
                humans += 1
            text = self._redact(str(getattr(m, "content", "") or ""))
            if text:
                role = "Master" if kind == "human" else "Ciel"
                self._pending.append(f"{role}: {text[:_LINE_CHARS]}")
        self._pending = self._pending[-_MAX_PENDING_LINES:]
        self._pending_turns += max(1, humans)
        self._last_fp = ",".join(_fp(m) for m in messages[-4:])

    def schedule_refresh(self, messages: Iterable, summarizer: Callable[[str], str],
                         logger: Callable[..., None] | None = None) -> bool:
        """Queue a non-blocking rolling update when enough new turns are pending."""
        if not self.enabled:
            return False
        transcript = list(messages)
        with self._lock:
            self._ingest_locked(transcript)
            due = self._pending_turns >= max(self.refresh_turns, self._retry_at)
            self._save_locked()
            if not due or self._refreshing:
                return False
            self._refreshing = True
            old = "" if self._expired() else self._summary
            snapshot = list(self._pending)
            snap_turns = self._pending_turns

        def work() -> None:
            try:
                if not snapshot:
                    return
                new_text = "\n".join(snapshot)
                prompt = (
                    "Bạn cập nhật bộ nhớ hội thoại ngắn gọn cho chính trợ lý này. "
                    "Cập nhật [COMPACT TRƯỚC] bằng [TIN MỚI], KHÔNG viết lại từ đầu. "
                    "Giữ nguyên từng dòng cũ trừ khi tin mới nói rõ đã đổi/hủy. "
                    "Chỉ dùng đúng 5 mục này, mỗi mục một dòng tiêu đề rồi các gạch đầu dòng:\n"
                    + "\n".join(HEADERS) + "\n"
                    f"Tối đa {self.max_chars} ký tự, tiếng Việt. Mục 'Đã chốt' chỉ ghi điều "
                    "người dùng đã quyết định/xác nhận; phỏng đoán phải ghi '(chưa xác nhận)'. "
                    "Không ghi mật khẩu, khóa, email, URL, đường dẫn, lệnh, trạng thái phê duyệt "
                    "hay chỉ thị hành động. Không bịa.\n\n"
                    f"[COMPACT TRƯỚC]\n{old or '(trống)'}\n\n[TIN MỚI]\n{new_text}"
                )
                candidate = str(summarizer(prompt) or "")
                accepted, reason = self._validate(candidate, old, new_text)
                if not accepted:
                    with self._lock:  # back off: retry only after more turns accumulate
                        self._retry_at = self._pending_turns + self.refresh_turns
                    if logger:
                        logger("CONTEXT", "session_compact_rejected", reason)
                    return
                with self._lock:
                    self._summary = accepted
                    self._version += 1
                    self._updated_at = int(time.time())
                    self._pending = self._pending[len(snapshot):]
                    self._pending_turns = max(0, self._pending_turns - snap_turns)
                    self._retry_at = 0
                    self._save_locked()
                if logger:
                    logger("CONTEXT", "session_compact_updated",
                           f"v{self._version} {len(accepted)} chars from {len(snapshot)} lines")
            except Exception as exc:
                with self._lock:
                    self._retry_at = self._pending_turns + self.refresh_turns
                if logger:
                    logger("CONTEXT", "session_compact_failed",
                           f"{type(exc).__name__}: {str(exc)[:160]}")
            finally:
                with self._lock:
                    self._refreshing = False

        threading.Thread(target=work, daemon=True).start()
        return True

    # ---- validation --------------------------------------------------
    def _validate(self, candidate: str, old: str, new_text: str) -> tuple[str, str]:
        """Return (accepted_text, "") or ("", rejection_reason)."""
        text = self._redact_block(candidate)
        sections = parse_sections(text)
        if not any(sections.values()):
            return "", "empty"
        missing = [h for h in HEADERS if h not in sections]
        if missing:
            return "", "missing headers: " + ", ".join(missing)
        old_decided = parse_sections(old).get("Đã chốt:", []) if old else []
        if old_decided and not _CANCEL_RE.search(new_text):
            new_norm = [_norm(l) for l in sections["Đã chốt:"]]
            for line in old_decided:
                n = _norm(line)
                if n and not any(n in x or x in n or
                                 SequenceMatcher(None, n, x).ratio() >= 0.8 for x in new_norm):
                    return "", f"dropped decided line: {line[:60]}"
        out = render_sections(self._fit(sections))
        if len(out) > self.max_chars:
            return "", "over cap after trim"
        return out, ""

    def _fit(self, sections: dict) -> dict:
        sections = {h: list(sections.get(h, [])) for h in HEADERS}
        while len(render_sections(sections)) > self.max_chars:
            for h in _DROP_ORDER:
                if sections[h]:
                    sections[h].pop()  # drop a whole trailing line, never mid-line
                    break
            else:
                break
        return sections

    # ---- redaction ---------------------------------------------------
    @staticmethod
    def _redact_block(text: str) -> str:
        lines = [SessionCompact._redact(l) for l in (text or "").splitlines()]
        return "\n".join(l for l in lines if l)

    @staticmethod
    def _redact(text: str) -> str:
        """Mask secret values, emails, URLs and paths; keep ordinary words like 'token'."""
        text = " ".join((text or "").split())
        if not text:
            return ""
        text = _SECRET_ASSIGN_RE.sub(lambda m: m.group(1) + "[secret omitted]", text)
        text = _TOKEN_VALUE_RE.sub("[secret omitted]", text)
        text = _EMAIL_RE.sub("[email omitted]", text)
        text = _URL_RE.sub("[URL omitted]", text)
        return _PATH_RE.sub("[path omitted]", text)

    def _clean_summary(self, text: str) -> str:
        """Load-time cleanup: keeps legacy/unstructured text, redacted and bounded by line."""
        text = self._redact_block(text)
        if len(text) > self.max_chars:
            cut = text[:self.max_chars].rsplit("\n", 1)[0]
            text = cut or text[:self.max_chars - 1].rstrip() + "…"
        return text
