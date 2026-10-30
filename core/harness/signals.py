"""Shared intent, failure, placeholder, and risk signals; no provider dependencies."""
import re

# ==========================================================
# SELF-CORRECTION PROMPT — Brain evaluates tool results
# ==========================================================
SELF_CORRECTION_PROMPT = """You are evaluating whether a tool's result adequately answers the user's original request.

Respond ONLY with valid JSON (no markdown, no prose):

If the result answers the user's request adequately:
{{"satisfied": true}}

If the result is empty, incomplete, or wrong AND you know a better approach:
{{"satisfied": false, "reasoning": "1-sentence explanation", "action": "tool", "tool_name": "alternative_tool", "tool_args": {{"key": "value"}}, "response_hint": "how to present"}}

If the result is insufficient and no tool can help, explain to user:
{{"satisfied": false, "reasoning": "1-sentence explanation", "action": "chat", "task": "instruction for Worker to explain the situation"}}

RULES:
- Return satisfied=true if the result reasonably answers the request, even partially.
- A result is NEVER satisfactory if it is an error message, empty, "not found",
  "does not exist", "không tồn tại", or clearly unrelated junk — in those cases you
  MUST return satisfied=false and provide the best alternative tool, or an "action":"chat"
  that honestly explains the situation to the user.
- Only return satisfied=false if you have a CONCRETE better alternative OR the result is an error/empty/junk.
- NEVER suggest the same tool with identical arguments.
- Keep reasoning to 1 sentence.
"""


RAG_LLM_COMPRESS_CHAR_THRESHOLD = 4000  # Roughly 1000 tokens.
RAG_LLM_COMPRESS_INPUT_LIMIT = 20000
RAG_LLM_COMPRESS_OUTPUT_LIMIT = 5000


# ==========================================================
# MAIN ORCHESTRATOR
# ==========================================================

# Risk descriptions for high-risk tools (shown in confirmation prompt)
_DANGEROUS_CODE_PATTERNS = (
    r"format\s+[a-z]:", r"shutil\.rmtree\(", r"rm\s+-rf\s+/", r"del\s+/f\s*/s\s*/q",
    r"shutdown\s+/[rsf]", r"os\.system\([^)]*\brm\b", r"DROP\s+DATABASE", r"DROP\s+TABLE",
    r"mkfs\.", r"diskutil\s+erasedisk", r":(){ :\|:& };:",
)


# ONE definition of "an email address", used everywhere one is extracted or stripped.
# The `+` in the local part is the reason this is centralised: seven copies of a
# `+`-blind pattern had drifted through the file, and three of them extract the RECIPIENT.
# Against "first.last+tag@sub.domain.com" such a pattern matches only "tag@sub.domain.com",
# i.e. it would have addressed the mail to the wrong person — silently, since the result
# is still a valid-looking address. Gmail's +tag form is common, so this is not exotic.
_EMAIL_RE = re.compile(r"[\w.+\-]+@[\w.\-]+\.\w+")

# ONE definition of "does this message actually ask to SEND something", used by
# both send-step safeguards below (single-tool promotion and multi_tool). Found
# live, 5 turns in a row: a Master repeatedly said "chỉ tìm và đọc email thôi,
# không gửi gì hết" ("just search and read, don't send anything") — the OLD
# check was a bare substring test for "gửi"/"send"/etc. with no negation
# awareness, and it ALSO fired on "người gửi" ("sender" — describing an
# email's metadata, e.g. "search by người gửi john@abc.com"), which contains
# the same substring but has nothing to do with sending. Every one of those
# turns still had a concrete email address on-screen (john@abc.com), so the
# safeguard kept re-appending send_gmail_message and getting DEFERRED for
# approval, on a plan the Master explicitly, repeatedly said was read-only.
_SENDER_NOUN_RE = re.compile(r"người\s*g[uử]i|from\s*:", re.IGNORECASE)
_SEND_VERB_RE = re.compile(r"\b(g[uử]i|g[oở]i|send|forward|chuy[eể]n|mail)\b", re.IGNORECASE)
_SEND_NEGATION_RE = re.compile(
    r"(không|đừng|khỏi|chưa|no\b|don'?t|never)\s+(?:\w+\s+){0,3}?"
    r"(g[uử]i|g[oở]i|send|forward|chuy[eể]n|mail)",
    re.IGNORECASE)


def _has_send_intent(text: str) -> bool:
    """True only for a genuine request to SEND something — see the module note
    above for the two false positives this exists to rule out."""
    scrubbed = _SENDER_NOUN_RE.sub(" ", text or "")
    if not _SEND_VERB_RE.search(scrubbed):
        return False
    return not _SEND_NEGATION_RE.search(text or "")


# ONE definition of "does this message name a write target with a save/write
# verb", shared by the single-tool promotion (new) and the existing multi_tool
# write-step safeguard — was previously two separate inline copies that could
# drift. Found live: "thêm 3 kỹ năng vào CV rồi lưu thành cv_updated.txt" got
# planned as a single action="tool" read_file call three turns in a row —
# read_file is in _SKIP_SELF_CORRECTION, so nothing ever noticed the write the
# Master asked for never happened; Ciel just re-displayed the unmodified file
# each time. The multi_tool version of this safeguard already existed but only
# ever ran for action=="multi_tool" — it never saw a plan that was, itself,
# just one lone read_file call.
_WRITE_TARGET_PATH_RE = re.compile(
    r'((?:[A-Za-z]:[\\/](?:[\w .-]+[\\/])*)?(?:agent_output|ciel_workspace)[\\/][\w.\\/ -]*[\w-]\.\w+'
    # A bare filename with no folder prefix also counts — found live: "lưu lại
    # thành cv_updated.txt" (no ciel_workspace/ prefix at all) is exactly how a
    # real Master names a save target most of the time; requiring the prefix
    # meant this exact wording never matched, and bug A's whole fix depends on it.
    r'|\b[\w-]+\.(?:txt|md|html?|py|json|csv|docx?|xlsx|log|ya?ml)\b)',
    re.IGNORECASE)
_WRITE_VERB_RE = re.compile(
    r"\b(viết|ghi|write|save|lưu|tạo|create|make|generate|note|tao file|tạo file)\b", re.IGNORECASE)


def _strip_telegram_inbound_metadata(text: str) -> str:
    """Remove the transport note prepended to a Telegram file/photo message."""
    return re.sub(
        r"\[(?:File|Ảnh)\s+Master\s+vừa\s+gửi[^\]]*\]",
        " ",
        text or "",
        flags=re.IGNORECASE,
    )


def _has_telegram_send_intent(text: str) -> bool:
    """True when Master asked to deliver a result via Telegram (not mere mention)."""
    # The inbound transport note literally says "gửi qua Telegram". It describes how
    # the file arrived; it is never an instruction to send the result again.
    cleaned = _strip_telegram_inbound_metadata(text)
    return bool(re.search(
        r"(?:gửi|send|báo|notify|thông\s*báo).{0,50}telegram"
        r"|qua\s+telegram"
        r"|telegram\s+(?:cho|to\s+me|cho\s+t)"
        r"|send_telegram",
        cleaned,
        re.IGNORECASE,
    ))


def _has_telegram_delivery_step(tools: list) -> bool:
    """True when a plan already delivers a message or attachment to Telegram."""
    return any(
        isinstance(step, dict)
        and step.get("tool_name") in ("send_telegram", "send_telegram_document")
        for step in (tools or [])
    )


def _has_write_intent(text: str):
    """Returns the matched target path if the text names one AND uses a save/
    write verb, else None. A path alone (e.g. just mentioning a file that
    exists) is not intent; a verb alone with no path is too vague to act on.

    When TWO filenames appear ("mở cv_new.txt ra, ... rồi lưu thành
    cv_updated.txt"), the LAST one is taken — a source-then-destination shape
    is how this is phrased in practice, and the first match alone would have
    picked the file being read FROM, not saved TO, as the write target.

    Live false positive (2026-08-06 Telegram smoke): the inbound note
    "[File Master vừa gửi … đã lưu tại: ciel_workspace/telegram_uploads/X.txt]"
    matches both a path AND the write-verb `lưu` inside "đã lưu tại", so the
    multi_tool write safeguard *appended write_file to the upload* and clobbered
    the inbound file with a summary. Strip delivery phrasing / never auto-write
    into telegram_uploads.
    """
    raw = text or ""
    cleaned = _strip_telegram_inbound_metadata(raw)
    cleaned = re.sub(
        r"đã\s+lưu\s+tại\s*:[^\n\]]*",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"(?:saved|stored)\s+at\s*:[^\n\]]*",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    matches = list(_WRITE_TARGET_PATH_RE.finditer(cleaned))
    if matches and _WRITE_VERB_RE.search(cleaned):
        path = matches[-1].group(1)
        norm = path.replace("\\", "/").lower()
        if "telegram_uploads/" in norm or norm.startswith("telegram_uploads"):
            return None
        return path
    return None


# "Đừng ghi đè, tạo file mới" — explicit no-clobber request. write_file always
# overwrites (that's its whole contract); honoring "make a new one" means
# picking a DIFFERENT filename before the tool ever runs, deterministically,
# rather than hoping the Brain remembers on the next attempt. Found live: a
# Master said this 5 times across 5 turns and every write still landed on the
# exact same filename.
_NO_OVERWRITE_RE = re.compile(
    r"đừng\s*(có\s*)?ghi\s*đè|không\s*(được\s*)?ghi\s*đè|file\s*m[oớ]i|"
    r"\bnew\s+file\b|\bdon'?t\s+overwrite\b|\bdo\s+not\s+overwrite\b",
    re.IGNORECASE)


# --- "I don't have that information" detection (chat path) -------------------------
# Found live: Master asked "đang có chế độ gì mới ko" about LoL; Ciel answered "Mình
# chưa có dữ liệu live để xác nhận chính xác" and STOPPED — the Master then had to
# type "tra cứu đi" by hand. The chat path gives the Worker no tools, so an honest
# "I don't know" is where the turn ends, even for a question stealth_search answers
# trivially. Two independent signals in ONE sentence are required, rather than one
# broad regex: a negation AND a knowledge/data noun. That distinguishes a KNOWLEDGE
# gap (searchable) from a CAPABILITY refusal ("Mình không thể xác định danh tính từ
# ảnh", "không có công cụ chơi Poker") — both contain a negation, neither is fixed by
# searching, and both appear verbatim in the real log.
_KNOWLEDGE_GAP_NEGATION_RE = re.compile(
    r"\b(?:chưa|không|chẳng|no|not|don'?t|doesn'?t|cannot|can'?t|unable|lack)\b",
    re.IGNORECASE)
_KNOWLEDGE_NOUN_RE = re.compile(
    r"(?:dữ\s*liệu|thông\s*tin|kết\s*quả|số\s*liệu|tin\s*tức|cập\s*nhật|tra\s*cứu|"
    r"\blive\b|\bdata\b|\binformation\b|\binfo\b|\bresults?\b|\bupdates?\b|"
    r"\bcurrent\b|\blatest\b)",
    re.IGNORECASE)

# Questions about the Master's OWN stored things — the fact vault, todo store, Gmail
# and workspace own these, never the public web. Searching them would be useless at
# best and would put the Master's private wording into a third-party query at worst.
# Narrow on purpose: a personal-artifact noun sitting next to a first-person pronoun.
# "tôi muốn biết chế độ mới của LoL" contains "tôi" but no artifact noun, so it stays
# searchable — excluding every first-person question would gut the feature.
_OWN_DATA_RE = re.compile(
    r"(?:tên|mật\s*khẩu|email|mail|todo|công\s*việc|lịch|file|tệp|ghi\s*chú|"
    r"tài\s*khoản|số\s*điện\s*thoại|địa\s*chỉ|password|note|account|address|phone)"
    r"[^.?!\n]{0,20}?\b(?:của\s+)?(?:tôi|t|mình|em|my)\b"
    r"|\bmy\s+(?:name|password|email|todo|file|note|account|address|phone|schedule)\b",
    re.IGNORECASE)

_SENTENCE_SPLIT_RE = re.compile(r"[.!?\n]+")


def _admits_missing_knowledge(text: str) -> bool:
    """True when a reply concedes it lacks the DATA to answer (not the ABILITY).
    Scoped per sentence so "I have no poker tool. Here is the latest data..." — a
    negation and a knowledge noun in unrelated sentences — does not false-positive."""
    for sentence in _SENTENCE_SPLIT_RE.split(text or ""):
        if _KNOWLEDGE_GAP_NEGATION_RE.search(sentence) and _KNOWLEDGE_NOUN_RE.search(sentence):
            return True
    return False


def _search_results_need_retry(text: str) -> bool:
    """True only for a completed search with no usable hits, never API failures."""
    normalized = (text or "").strip().lower()
    if not normalized or "search api error:" in normalized or "[tool_error]" in normalized:
        return False
    return (
        "no results found" in normalized
        or "no usable results" in normalized
        or "0 results found" in normalized
    )


def _search_results_unusable(text: str) -> bool:
    """Whether a fallback search result must never be presented as evidence."""
    normalized = (text or "").strip().lower()
    return (
        not normalized
        or _search_results_need_retry(normalized)
        or "search api error:" in normalized
        or normalized.startswith(("[tool_error]", "[execution_error]", "[tool_not_found]"))
    )


def _normalized_search_query(query: str) -> str:
    """Comparison form for bounded retry: whitespace/case changes are not a strategy."""
    return " ".join((query or "").casefold().split())


# A chat-routed Worker has no tool handle.  Some models nevertheless narrate an
# imaginary next step ("I'm running stealth_search" / "wait for the results") when
# the Router deliberately chose chat to ask for a missing subject.  That is worse
# than a normal bad answer: it leaves the Master waiting for work that will never run.
# Match the *claim of current/future execution*, not a harmless explanation of what a
# tool is, so a question such as "what does stealth_search do?" stays answerable.
_UNBACKED_CHAT_TOOL_PROMISE_RE = re.compile(
    r"(?:\b(?:đang|sẽ|will|currently|going\s+to)\b[^.!?\n]{0,56}"
    r"\b(?:chạy|dùng|thực\s+hiện|tìm|tra|search(?:ing)?|look\s*up|run(?:ning)?|"
    r"stealth_search|smart_scrape|search_gmail|get_weather)\b"
    # A first-person verb alone can describe a completed past mistake (for example
    # "tôi loay hoay tìm file nên quên"), not a promise that work is running now.
    # Require an explicit present/future marker before treating it as a fake action.
    r"|\b(?:để\s+)?(?:tôi|mình|em|i|we)\s+(?:đang|sẽ|will|currently|going\s+to)\b[^.!?\n]{0,40}"
    r"\b(?:chạy|dùng|thực\s+hiện|tìm|tra|search(?:ing)?|look\s*up|run(?:ning)?)\b"
    r"|\b(?:chờ|đợi|wait(?:ing)?)\b[^.!?\n]{0,40}"
    r"\b(?:tôi|mình|em|i|we)\b)",
    re.IGNORECASE,
)

# A completed live lookup may ground one immediate deictic follow-up ("scrape it",
# "read that article", "đào sâu vụ này") without exposing general chat history to the
# Router. Only public URLs from a successful lookup are kept, in RAM, for a short TTL.
_PUBLIC_URL_RE = re.compile(r"https?://[^\s<>\"'\]\[()]+", re.IGNORECASE)


def _find_dangerous_code_patterns(text: str) -> list:
    """Deterministic scan for genuinely destructive code/commands (drive format, mkfs,
    rmtree, fork bombs, etc.) — same bar backtest/test_hard_special.py checks against.
    Code generation (execute_code) and write_file/append_file never passed through the
    Safety Gate before (only pre-declared high-risk TOOLS did), so an LLM could write a
    fully wired format_drive()/mkfs call straight to disk with zero confirmation."""
    return [pat for pat in _DANGEROUS_CODE_PATTERNS if re.search(pat, text, re.IGNORECASE)]


# Deferred-synthesis placeholder detector (deterministic, model-agnostic). execute_multi_tool
# defers a write_file/send_gmail_message step whose content is "still waiting on real data"
# and re-executes it after the Worker synthesizes the actual report. This used to be
# recognized only via an exact literal match on the two markers this codebase itself
# generates ("..._TO_BE_SYNTHESIZED]"). Observed gap: a stronger Brain model (Opus) planned
# its OWN write_file step instead of relying on the workflow safeguard, and phrased the
# placeholder differently — "[SYNTHESIZE_FROM_RESULTS: today's date from get_current_time,
# ...]" — which the literal match didn't recognize. The classification silently missed it,
# so that raw placeholder text was written straight to disk as the "report" (a hollow-shell
# bug distinct from data fabrication: no invented facts, just unresolved plan scaffolding
# leaking into a real file). Match on the SYNTHES* root inside brackets instead of one exact
# string, so any Brain phrasing of the same "fill this in after synthesis" intent is caught.
_UNSYNTHESIZED_PLACEHOLDER_RE = re.compile(
    r"\[[^\[\]\n]{0,160}?(?:SYNTHES\w*|TO_BE_FILLED|PLACEHOLDER)[^\[\]\n]{0,160}?\]",
    re.IGNORECASE,
)


def _has_unsynthesized_placeholder(text: str) -> bool:
    """True if `text` still contains a bracketed 'fill this in after synthesis' marker
    instead of real content, regardless of the Brain's exact wording for it."""
    return bool(text) and bool(_UNSYNTHESIZED_PLACEHOLDER_RE.search(text))


# Errors where NO tool_args correction can possibly help — retrying with guessed
# parameters is guaranteed to fail again, it just costs an extra Worker call each time.
# Found via scripts/prompt_harness.py: 220 HEALING_TRIGGER/UnclassifiedError occurrences
# in thoughts.log, most of them this class (missing python library, network/geo issues)
# rather than an actual parameter typo the self-healing "guess a corrected arg" prompt
# (recovery_manager.py's "other tools" branch) could ever fix. Deliberately narrow: genuine
# format issues ("Could not find price for BTCUSDT... Ensure format is correct") are LEFT
# eligible, since a corrected symbol format is a real, observed fix for those.
_RAW_STEP_RESULT_CHARS = 12000

_HEALING_SKIP_PATTERNS = re.compile(
    r"library is not installed|service unavailable from a restricted location|"
    r"max retries exceeded|(?:read|connect|request|operation) timed out|\[winerror|"
    r"forbidden by its|\[not_allowlisted\]|\[(?:tool_)?not_found\]|"
    r"tool.{0,80}(?:not registered|not loaded|not found)|"
    r"invalid_grant|authentication (?:failed|required)|unauthorized|"
    r"permission denied|access denied|certificate verify failed|"
    r"(?:ssl|tls)(?:error| failure|:)|connection (?:reset|aborted|refused)|"
    r"command not found|is not recognized as an internal or external command|"
    r"/(?:bin/)?(?:sh|bash):.{0,120}not found",
    re.IGNORECASE,
)


def _is_unfixable_tool_error(tool_name: str, result_text: str) -> bool:
    """Whether changing tool arguments cannot repair this deterministic failure.

    ``run_python_script`` is intentionally excluded: its recovery path can rewrite
    code, not merely guess arguments, and may legitimately remove a bad dependency.
    """
    return (
        tool_name != "run_python_script"
        and bool(_HEALING_SKIP_PATTERNS.search((result_text or "")[:1000]))
    )


_RISK_DESCRIPTIONS = {
    "delete_file":             "Permanently DELETE a file from your workspace",
    "execute_shell_command":   "Run an OS shell command on your machine",
    "send_gmail_message":      "Send an email from your Gmail account",
    "send_gmail_html_message": "Send an HTML email from your Gmail account",
    "reply_to_email":          "Send a reply to an email thread",
    "trash_email":             "Move an email to Trash in your Gmail",
    "git_confirm_push":        "Commit and PUSH code to the remote repository",
    "vision_act":              "Autonomously control your screen (click, type, scroll)",
}

