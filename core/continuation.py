"""continuation.py — Tier-1 agent loop: decide whether an executed plan deserves
another observe-then-act round, and bound that loop so it can never run away.

WHY THIS MODULE IS PURE PYTHON
------------------------------
Ciel's plan format is a FLAT list of tool calls chosen before anything runs, so a
request like "check git status, and if it's clean, commit" is not merely hard for
the planner — it is *structurally unrepresentable*. Fixing that needs a loop:
execute → observe → re-plan.

The obvious way to build that loop is to ask the model "are we done yet?" after every
step. This module deliberately does NOT do that, for two reasons:

  1. Cost. That judgement call would fire on every single request, including the
     ~90% that are plainly one-shot ("giá BTC?"). Here the decision is free Python,
     so a non-looping request pays exactly zero extra tokens.

  2. Model-independence. This whole codebase has repeatedly been bitten by guards
     that key off an exact string the model must emit — most recently {{step_N}},
     which two entirely different model families both wrote as {step_N}, silently
     breaking dependent steps. So nothing here *requires* the model to say anything
     in particular. A capable model MAY opt a round in via `needs_followup`, but every
     safety property (when to stop, what counts as progress, budget) is decided by
     code. Downgrading or swapping the model changes answer quality and cost — never
     whether the loop terminates.

FAIL-OPEN
---------
Any exception, any unparseable re-plan, any empty follow-up collapses back to the
first round's answer. The loop can improve a response; it can never make the
pipeline worse than it was before this module existed.
"""
import json
import re
import time
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Signals — every one of these is a deterministic property of the request or of
# what actually came back, never a model self-report.
# ---------------------------------------------------------------------------

# Conditional / result-dependent phrasing. A flat plan cannot express "do B only if
# A turns out a certain way", so such a request needs at least one observation round.
# Kept deliberately tight: a false positive costs one re-plan call, so bare "if" is
# not enough — the phrasing must imply a decision that depends on an earlier outcome.
_CONDITIONAL_MARKERS = (
    # Vietnamese
    r"\bnếu\b.{0,80}?\bthì\b", r"\bnếu\b.{0,40}?(?:sạch|ổn|xong|có|không|lỗi|thành công)",
    r"\btrường\s*hợp\b", r"\btùy\b|\btuỳ\b", r"\bphụ\s*thuộc\b",
    r"\bdựa\s*(?:trên|vào)\s*kết\s*quả\b", r"\bsau\s*khi\s*(?:xem|kiểm\s*tra|đọc|check)\b",
    # "…rồi…" alone is plain SEQUENCING, not a condition. An earlier version matched
    # bare "kiểm tra X rồi Y" and looped on "check git status, then summarise" — a
    # wasted planner call on a request with no branch in it at all. Both forms now
    # require a decision verb after "rồi".
    r"\b(?:xem|kiểm\s*tra)\b.{0,40}?\brồi\s*(?:mới\s+)?(?:quyết|chọn|cân\s*nhắc|tuỳ|tùy)",
    # English
    r"\bif\b.{0,80}?\bthen\b", r"\bdepend(?:s|ing)\s+on\b", r"\bbased\s+on\s+the\s+(?:result|output)\b",
    r"\bonce\s+you\s+(?:see|know|have)\b", r"\bafter\s+(?:checking|reading|seeing)\b",
    r"\bunless\b", r"\bin\s+case\b", r"\bonly\s+if\b",
)
_CONDITIONAL_RE = re.compile("|".join(_CONDITIONAL_MARKERS), re.IGNORECASE | re.DOTALL)

# A step argument that STILL looks like a placeholder after substitution ran means the
# plan wanted data it never received. Matches one OR two braces on purpose: the
# router prompt asks for {{step_N}}, but real models routinely emit {step_N}, and
# either way the value that reached the tool is wrong and worth a second look.
_UNRESOLVED_REF_RE = re.compile(r"\{{1,2}\s*(?:prev|step[_ ]?\d+)(?:\.output)?\s*\}{1,2}", re.IGNORECASE)

# A step that plainly did not deliver. Anchored near the start so a result that merely
# *mentions* the word "error" in prose is not mistaken for a failure.
_FAILED_STEP_RE = re.compile(
    r"^\s*(?:\[TOOL_ERROR|\[EXECUTION_ERROR|\[CANCELLED|Error[:\s]|Lỗi[:\s]|"
    r"API Error|Sorry,|(?:File )?not found)", re.IGNORECASE)

_EMPTY_RESULT_RE = re.compile(r"^\s*(?:\[\]|\{\}|none|null|n/?a|không\s+có\s+(?:kết\s+quả|dữ\s+liệu))\s*$",
                              re.IGNORECASE)

# FAN-OUT: "list the files, then read EACH one". The set's size is unknowable when the
# plan is written, so a flat plan can only ever do the listing and stop — which is
# exactly what happened in testing: list_workspace ran, nothing was read, and the
# answer silently omitted every file's contents. Distributive wording is the request
# side of that signal; _enumerated_item_count is the evidence side.
# Only genuinely PER-ITEM words. "tất cả" / "toàn bộ" / "all" were in this set briefly
# and caused a false positive: "tổng hợp TẤT CẢ vào một báo cáo" is AGGREGATION ("combine
# everything into one"), the opposite of fan-out, and it looped a plain 4-step request for
# nothing. Precision matters more than recall here — a miss costs a slightly thin answer,
# a false positive costs a planner call on every request phrased that way.
_DISTRIBUTIVE_RE = re.compile(
    r"\btừng\b|\bmỗi\b|\bmọi\b|\beach\b|\bevery\b|\bfor\s+each\b",
    re.IGNORECASE)

# SCOPE VETO — the user explicitly bounded the work ("chỉ … thôi", "đừng làm gì thêm",
# "only …", "do not …"). Found live: "liệt kê từng file thôi, rồi DỪNG lại" still tripped
# the fan-out signal (S4) below and looped anyway, because every signal in this module is
# a reason to CONTINUE and none of them is a reason a human gave to STOP. This is checked
# BEFORE every other signal and wins unconditionally — deliberately asymmetric with the
# rest of the policy: a missed continuation costs a slightly thinner answer, but ignoring
# an explicit "don't" does work nobody asked for, which is the worse failure in both
# directions (cost, and doing something an outbound send or a file write cannot undo).
_SCOPE_VETO_RE = re.compile(
    r"\bchỉ\b.{0,40}?\bthôi\b"                              # "chỉ X thôi"
    r"|\bthôi\b(?:[,.]|\s*$|\s+(?:là\s+)?(?:được|đủ)\b)"    # "X thôi." / "... thôi là được"
    r"|\bđừng\b|\bkhông\s+cần\b"                            # "đừng ...", "không cần ..."
    r"|\bkhông\s+(?:làm|cần)\s+gì\s+thêm\b"
    r"|\bdừng\s+lại\b"
    r"|\bonly\b.{0,30}?\b(?:list|show|check|read|get)\b"
    r"|\bdo\s+not\b|\bdon'?t\b.{0,20}?\b(?:do|send|write|delete|modify)\b"
    r"|\bnothing\s+(?:else|more)\b",
    re.IGNORECASE)

# A line that reads as one entry of a listing. The bracketed-tag form is not optional
# polish: Ciel's own list_workspace returns "[DIR ] screenshots\n[FILE] todos.json", and
# git_status returns "[M] path", so a matcher that only understood bullets and bare
# filenames scored a real 9-item listing as zero items and the fan-out signal never fired.
_LIST_ENTRY_RE = re.compile(
    r"^\s*(?:"
    r"[-*•·]\s+\S"                  # - bullet
    r"|\d+[.)]\s+\S"                # 1. / 1) numbered
    r"|\[[\w ]{1,10}\]\s*\S"        # [FILE] x, [DIR ] y, [M] path
    r"|[\w .\-/\\]+\.\w{1,5}\s*$"   # a bare filename on its own line
    r")")


def _enumerated_item_count(text: str) -> int:
    """How many discrete entries a result appears to list. Deliberately crude — it only
    has to distinguish "this produced a set" from "this produced a sentence"."""
    if not text:
        return 0
    return sum(1 for line in str(text).splitlines() if _LIST_ENTRY_RE.match(line))


@dataclass(frozen=True)
class StepRecord:
    """One executed step: what was called, with what, and what came back."""
    tool_name: str
    args: dict
    result: str

    def signature(self) -> str:
        """Stable identity used for no-progress detection. Args are canonicalised so
        that re-ordered keys do not read as a different call."""
        try:
            a = json.dumps(self.args, sort_keys=True, ensure_ascii=False, default=str)
        except Exception:
            a = str(self.args)
        return f"{self.tool_name}::{a}"

    def failed(self) -> bool:
        r = self.result or ""
        return bool(_FAILED_STEP_RE.search(r[:120])) or bool(_EMPTY_RESULT_RE.match(r))


@dataclass
class LoopBudget:
    """Hard ceilings. Every one is enforced in code, so no model behaviour — however
    confused — can turn a request into an unbounded spend."""
    max_rounds: int = 2          # extra observe-then-act rounds beyond the first
    max_seconds: float = 120.0   # wall-clock ceiling for the whole loop
    max_replans: int = 2         # planner calls the loop may spend
    # Steps a single round may execute. Without this a planner is free to answer one
    # re-plan with a dozen slow calls: an observed run proposed five smart_scrape hits
    # on redirect URLs and spent 566s inside ONE round, sailing past max_seconds
    # because the ceiling was only ever tested between rounds.
    max_steps_per_round: int = 4
    started_at: float = field(default_factory=time.monotonic)
    rounds_used: int = 0
    replans_used: int = 0

    def out_of_time(self) -> bool:
        """Checked BETWEEN individual steps, not just between rounds — a round that is
        already running must still be able to stop."""
        return (time.monotonic() - self.started_at) >= self.max_seconds

    def exhausted(self) -> tuple[bool, str]:
        if self.rounds_used >= self.max_rounds:
            return True, f"round limit reached ({self.max_rounds})"
        if self.replans_used >= self.max_replans:
            return True, f"re-plan limit reached ({self.max_replans})"
        elapsed = time.monotonic() - self.started_at
        if elapsed >= self.max_seconds:
            return True, f"time budget spent ({elapsed:.0f}s / {self.max_seconds:.0f}s)"
        return False, ""


@dataclass(frozen=True)
class Assessment:
    should_continue: bool
    reason: str

    def __bool__(self) -> bool:
        return self.should_continue


class ContinuationPolicy:
    """Decides IF another round is warranted. Never decides WHAT to do next — that is
    the planner's job, and it is the only part of the loop that costs tokens."""

    @staticmethod
    def request_is_conditional(user_input: str) -> bool:
        """True when the request's own wording makes the work result-dependent."""
        return bool(_CONDITIONAL_RE.search(user_input or ""))

    @staticmethod
    def request_is_distributive(user_input: str) -> bool:
        """True when the request asks for work to be repeated over EVERY item of a set
        ("đọc từng file", "summarise each result")."""
        return bool(_DISTRIBUTIVE_RE.search(user_input or ""))

    @staticmethod
    def request_has_scope_veto(user_input: str) -> bool:
        """True when the user explicitly bounded the work ("chỉ … thôi", "đừng …",
        "only …", "do not …"). See _SCOPE_VETO_RE for why this exists and why it
        outranks every continuation signal below."""
        return bool(_SCOPE_VETO_RE.search(user_input or ""))

    @staticmethod
    def assess(user_input: str, executed: list, budget: LoopBudget,
               model_requested: bool = False) -> Assessment:
        """Should we spend one planner call to look again?

        `model_requested` carries an OPTIONAL hint from the plan (`needs_followup`).
        It can only ever ADD a reason to continue; it is never required, and it can
        never override a budget ceiling or the no-steps guard.
        """
        # VETO — checked before budget and before every signal. An explicit "chỉ … thôi"
        # / "đừng …" / "only …" means the Master bounded the task on purpose; no signal
        # below (including fan-out) may override that by inventing a second round.
        if ContinuationPolicy.request_has_scope_veto(user_input):
            return Assessment(False, "stop: user explicitly limited scope")

        spent, why = budget.exhausted()
        if spent:
            return Assessment(False, f"stop: {why}")

        if not executed:
            # Nothing ran, so there is nothing to observe. A pure chat turn or a plan
            # that produced no tool calls must not trigger a loop.
            return Assessment(False, "stop: no steps executed, nothing to observe")

        # S1 — the request itself is conditional; a flat plan could not express it.
        if ContinuationPolicy.request_is_conditional(user_input):
            return Assessment(True, "request is result-dependent (conditional phrasing)")

        # S2 — a placeholder survived into a real tool call: the step ran on a literal
        # "{step_1}" instead of prior output.
        for s in executed:
            for v in (s.args or {}).values():
                if isinstance(v, str) and _UNRESOLVED_REF_RE.search(v):
                    return Assessment(True, f"unresolved step reference reached {s.tool_name}")

        # S3 — a step failed while later steps still ran, so the tail of the plan was
        # built on missing data.
        for i, s in enumerate(executed[:-1]):
            if s.failed():
                return Assessment(True, f"step {i + 1} ({s.tool_name}) failed but later steps ran")

        # S4 — FAN-OUT over a set whose size was unknowable at planning time. The
        # request asks for something to be done to EACH item, a step has now produced
        # the list, and there are visibly more items than steps that have run — so the
        # per-item work cannot have happened yet.
        if ContinuationPolicy.request_is_distributive(user_input):
            for s in executed:
                items = _enumerated_item_count(s.result)
                if items >= 2 and len(executed) < items:
                    return Assessment(
                        True, f"fan-out: {s.tool_name} listed {items} items but only "
                              f"{len(executed)} step(s) ran")

        # S5 — the planner explicitly asked to see results. Lowest priority on purpose:
        # a model that never sets it loses nothing, because S1–S4 are what actually
        # protect correctness.
        if model_requested:
            return Assessment(True, "planner requested a follow-up round")

        return Assessment(False, "stop: plan looks complete")

    @staticmethod
    def novel_steps(proposed, executed: list) -> list:
        """Drop follow-up steps that merely repeat work already done.

        This is the no-progress guard: a confused planner that keeps re-proposing the
        same call gets an empty list back, the caller stops, and the loop terminates
        even though the model never realised it was going in circles.

        It is also the shape guard. `proposed` comes straight from a model, so it is
        NOT trusted to be a list of dicts — a degraded model emitting `"tools": "none"`
        or `[{...}, "x", 42]` must be filtered out here rather than crashing the caller
        (both were real failures caught by the degradation suite). Anything that is not
        a usable step is silently dropped; `tool_args` that is not a dict becomes {}.
        """
        if not isinstance(proposed, (list, tuple)):
            return []
        seen = {s.signature() for s in executed}
        fresh = []
        for p in proposed:
            if not isinstance(p, dict):
                continue
            name = str(p.get("tool_name") or "").strip()
            if not name:
                continue
            args = p.get("tool_args")
            if not isinstance(args, dict):
                args = {}
            rec = StepRecord(name, args, "")
            if rec.signature() in seen:
                continue
            seen.add(rec.signature())
            fresh.append({"tool_name": name, "tool_args": args})
        return fresh


def build_observation_block(user_input: str, executed: list, max_chars: int = 1200) -> str:
    """Render what has happened so far for the planner's next look.

    Deliberately reuses the EXISTING router contract rather than inventing a second
    plan schema: the model is asked for the same JSON it already produces every turn,
    so nothing new has to be learned and a weaker model is no more likely to fail here
    than it is at ordinary routing. It is told plainly that "nothing further" is a
    valid, expected answer — without that, models pad the plan with busy-work.
    """
    lines = ["[STEPS ALREADY EXECUTED — these results are real, do NOT run them again]"]
    for i, s in enumerate(executed, 1):
        result = (s.result or "").strip().replace("\n", " ")
        if len(result) > max_chars:
            result = result[:max_chars] + " …(truncated)"
        lines.append(f"{i}. {s.tool_name}({json.dumps(s.args, ensure_ascii=False, default=str)[:200]}) → {result}")
    lines.append(
        "\n[CONTINUE OR FINISH]\n"
        "Given the ORIGINAL request and the real results above, decide what still needs doing.\n"
        "- If the request is fully satisfied, reply with action=\"chat\" — that is the normal, "
        "expected outcome and requires no further tools.\n"
        "- If work genuinely remains (for example the request was conditional and the condition "
        "is now known), return ONLY the remaining steps.\n"
        "- NEVER repeat a step listed above; its result is already in hand."
    )
    return "\n".join(lines)
