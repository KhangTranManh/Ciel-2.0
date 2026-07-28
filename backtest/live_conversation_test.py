"""live_conversation_test.py — a LIVE two-agent conversation, instead of the
pre-scripted multi-turn samples in generate_test_samples.py.

WHY THIS EXISTS
---------------
A pre-written multi-turn sample guesses the ENTIRE conversation before turn 1
ever runs — turn 2's wording is fixed in the JSON file no matter how Ciel
actually answers turn 1. A real user does not work that way: if Ciel asks a
clarifying question, a real user answers THAT question. This script replaces
the fixed script with a second LLM (TEACHER_MODEL) that plays the user, reads
Ciel's ACTUAL reply each turn, and reacts to it — genuinely adaptive, at the
cost of extra API calls and less reproducibility run-to-run (see the tradeoff
table this was proposed with).

STRICT SEPARATION OF WHAT EACH SIDE SEES (the two "thinking contexts")
------------------------------------------------------------------------
- THE USER-SIM (TEACHER_MODEL) sees: its own system prompt (persona + goal +
  instructions to react naturally) and the growing transcript of
  (its own prior messages, Ciel's actual replies) — nothing else. It never
  sees Ciel's tool calls, Router decisions, or thoughts.log — only the same
  final text a real human would see. This is enforced simply by construction:
  `core.process(user_msg)` returns exactly that text, and that return value is
  the ONLY thing fed back into the User-Sim's next prompt.
- CIEL sees: only `user_msg`, the plain string the User-Sim produced — fed to
  `core.process()` exactly like any real request. It has NO visibility into
  the User-Sim's system prompt, persona, or the fact this is a simulation at
  all. This is enforced by construction too: the User-Sim's system prompt is
  never passed anywhere near `core.process()`.
Neither side is told about the other's internal setup. Conflating the two
(e.g. letting Ciel see the User-Sim's goal, or letting the User-Sim see
Ciel's tool calls) would make the transcript unrepresentative of a real
conversation — the entire point of this script is realism.

LOOP CONTROL (the "xoáy" / runaway-loop question)
--------------------------------------------------
Three deterministic guards, none of them trusting the LLM to self-regulate:
1. Hard `--max-turns` ceiling (default 8) — a turn count, not a token budget
   the model could argue its way around.
2. The User-Sim must reply with strict JSON including "done": bool (same
   pattern as vision_ops.py's own step loop) — it can end the conversation
   early once its goal is met, but can never make it run longer than the cap.
3. Repeat detection: if the User-Sim's last 2 messages are near-identical
   (normalized, whitespace/case-insensitive prefix match), the conversation is
   cut and marked "stuck" — same spirit as vision_ops.py's own
   click-same-cell-3-times loop breaker.

MULTI_TOOL TRANSPARENCY
------------------------
The User-Sim needs no special handling for turns where Ciel internally ran a
multi_tool plan — it only ever sees the one final text response, exactly like
a real user. For a human reviewing the transcript afterward, this script also
tails thoughts.log per turn to note which real tool(s) fired underneath, purely
as a debugging annotation the User-Sim itself never sees.

Run:
    python -m backtest.live_conversation_test --sessions 10 --max-turns 6
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
from langchain_openai import ChatOpenAI  # noqa: E402

from core.llm_connector import CielCore  # noqa: E402
from core.tool_manager import ToolManager  # noqa: E402
from backtest._sandbox import isolate, new_sandbox  # noqa: E402
from backtest.generate_test_samples import get_tool_catalog  # noqa: E402

DEFAULT_REPORT_DIR = ROOT / "backtest" / "logs"
THOUGHTS_LOG = ROOT / "ciel_data" / "logs" / "thoughts.log"


def _build_teacher_call() -> Callable[[str, str], str]:
    """TEACHER_MODEL plays the user — a SEPARATE model from BRAIN_MODEL on
    purpose: a model rarely writes the input that confuses itself, and the
    User-Sim's whole job is to probe Ciel's blind spots. Same endpoint
    (API_KEY/BASE_URL) as everything else in this project; only the model name
    differs, exactly like the BRAIN/WORKER/MIDDLEWARE tiers already do."""
    load_dotenv(ROOT / ".env")
    api_key = os.getenv("API_KEY")
    base_url = os.getenv("BASE_URL")
    model = os.getenv("TEACHER_MODEL")
    if not (api_key and base_url and model):
        raise SystemExit("Missing API_KEY / BASE_URL / TEACHER_MODEL in .env.")

    client = ChatOpenAI(model=model, api_key=api_key, base_url=base_url, temperature=0.8)

    def teacher_call(system_prompt: str, user_prompt: str) -> str:
        from langchain_core.messages import SystemMessage, HumanMessage
        resp = client.invoke([SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)])
        content = resp.content
        if isinstance(content, list):
            content = "".join(c.get("text", "") if isinstance(c, dict) else str(c) for c in content)
        return content

    return teacher_call


_USER_SIM_SYSTEM_PROMPT = """You are role-playing as a REAL HUMAN USER of an AI assistant
named Ciel. You are NOT Ciel and must never answer on Ciel's behalf — you only ever
write what the USER would type next.

YOUR PERSONA AND GOAL FOR THIS SESSION:
{goal}

HOW TO PLAY THIS ROLE:
- React to what Ciel ACTUALLY just said, not to what you expected it to say. If Ciel
  asked a clarifying question, answer it. If Ciel gave an unexpected or wrong answer,
  react the way a real person would (confused, correcting it, annoyed, or just moving on).
- Use natural, sometimes messy phrasing — referential wording ("that file", "cái đó",
  "làm lại như trên nhưng..."), typos, short replies, mixing Vietnamese and English if
  natural for your persona. Real users are not careful writers.
- Stay in character and on-goal. Do not break the fourth wall, do not mention that you
  are an AI or that this is a test.
- This session should run AT LEAST {min_turns} turns before you consider yourself done.
  You are currently on turn {turn_n} of up to {max_turns}. If turn_n is below {min_turns},
  do NOT set "done": true yet — dig deeper into the topic: ask a natural follow-up, request
  a related check, double-check something Ciel said, or add a related sub-task a real user
  in this persona would naturally think of next. Only set "done" early (before {min_turns})
  if the conversation has genuinely reached a dead end (Ciel is stuck/broken and repeating
  itself) — not just because your first request was answered.
- Once past turn {min_turns}, decide normally when your goal is actually satisfied.

Return ONLY this JSON (no markdown fences, no prose outside it):
{{"message": "<what the user types next>", "done": <true|false>, "reason": "<short note on why done or not>"}}
"""


def _build_user_sim_prompt(transcript: list[dict]) -> str:
    lines = []
    for turn in transcript:
        lines.append(f"You: {turn['user']}")
        lines.append(f"Ciel: {turn['response']}")
    lines.append("\nWrite your NEXT message now (as JSON).")
    return "\n".join(lines) if transcript else "This is the start of the conversation. Write your OPENING message now (as JSON)."


def _extract_json_obj(text: str) -> dict:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        text = text.rsplit("```", 1)[0]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("No JSON object found in the User-Sim's reply.")
    return json.loads(text[start:end + 1])


_NORMALIZE_RE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    return _NORMALIZE_RE.sub(" ", (text or "").strip().lower())[:80]


def _recent_tools_fired(before_byte_pos: int) -> list[str]:
    """Debugging annotation ONLY — never shown to the User-Sim. Tails
    thoughts.log for [TOOL] entries logged since the turn started, purely so a
    human reviewing the transcript can see what actually ran underneath a
    turn that (from the User-Sim's view) was just one plain text reply."""
    try:
        if not THOUGHTS_LOG.exists():
            return []
        with open(THOUGHTS_LOG, "r", encoding="utf-8", errors="ignore") as f:
            f.seek(before_byte_pos)
            chunk = f.read()
        return re.findall(r"\[TOOL\] \[(?:RESULT\w*|PARALLEL_BATCH)\]\n([a-z_]+)", chunk)
    except Exception:
        return []


def _recent_brain_thinking(before_byte_pos: int) -> dict:
    """Debugging annotation ONLY — never shown to the User-Sim (same rule as
    _recent_tools_fired). Pulls the Brain's own hidden_thought (observation +
    reasoning) for this turn out of thoughts.log, purely so a human reviewing
    the transcript afterward can see WHY Ciel routed the way it did — the
    User-Sim only ever sees the final text, exactly like a real user."""
    try:
        if not THOUGHTS_LOG.exists():
            return {}
        with open(THOUGHTS_LOG, "r", encoding="utf-8", errors="ignore") as f:
            f.seek(before_byte_pos)
            chunk = f.read()
        decisions = re.findall(r"\[BRAIN\] \[ROUTE_DECISION\]\n(\{.*\})\n", chunk)
        if not decisions:
            return {}
        try:
            last = json.loads(decisions[-1])
        except Exception:
            return {}
        ht = last.get("hidden_thought", {})
        return {
            "action": last.get("action", ""),
            "observation": ht.get("observation", ""),
            "reasoning": ht.get("reasoning", ""),
        }
    except Exception:
        return {}


_CONFIRM_PROMPT = """You are the SAME persona/user from this session, with this goal:
{goal}

RECENT CONVERSATION (what you just said to Ciel, and what it just replied):
{recent}

You just sent this message: {current_msg}

Ciel now needs your approval before performing a real action — very likely the exact
thing you just asked for above:
---
{preview}
---

Decide exactly as this persona would in the moment. If this action matches what you
JUST asked for in your last message (even if described in Ciel's own technical terms —
e.g. "send_gmail_message to X" IS "gửi mail cho X"), APPROVE it — a real user who just
asked for something does not then refuse the very thing they asked for. Only decline if
the action is genuinely NOT what you asked (wrong recipient/target, something you never
requested, or something that would surprise you).

Reply with ONLY one word: YES or NO."""


def _make_confirm_callback(teacher_call: Callable[[str, str], str], goal: str,
                           approvals_out: list, ctx: dict) -> Callable[[str, str, dict], bool]:
    """Wires Ciel's REAL confirmation gate (core.confirm_callback — the same hook
    main_api.py uses for the live UI, see core/llm_connector.py:303) to the
    User-Sim instead of auto-deferring everything. Found live (round 1): with
    core.unattended=True, every risky tool silently DEFERs — no prompt is ever
    shown, so transcripts filled up with repetitive "[CANCELLED]...needs
    approval" turns the User-Sim could only react to with frustration, never
    resolve.

    Found live (round 2, after fixing round 1): even personas who explicitly
    said "tôi đồng ý với mọi kế hoạch, đừng hỏi lại nữa" kept getting
    "[CANCELLED] Master declined the plan" — because THIS callback only saw
    the isolated (tool_name, preview, tool_args), never the conversation that
    led to it. Asked in a vacuum, the Teacher had no way to recognize "this
    preview IS the exact thing I just asked for" and defaulted to caution.
    `ctx` is a small mutable dict the caller updates with the current message
    and recent transcript right before each `core.process()` call, so the
    approval prompt is grounded in what was actually just asked — same fix
    shape as _recent_entities_note() in core/llm_connector.py: ground the
    decision in the real recent turns instead of judging in isolation."""
    def confirm_callback(tool_name: str, preview: str, tool_args: dict) -> bool:
        recent_lines = []
        for t in ctx.get("transcript", [])[-3:]:
            recent_lines.append(f"You: {t['user']}")
            recent_lines.append(f"Ciel: {t['response'][:300]}")
        recent = "\n".join(recent_lines) if recent_lines else "(this is the first message)"
        prompt = _CONFIRM_PROMPT.format(
            goal=goal, recent=recent, current_msg=ctx.get("user_msg", ""), preview=preview)
        try:
            raw = teacher_call("You are answering a real approve/deny prompt as the user, in character.", prompt)
            approved = raw.strip().upper().startswith("YES")
        except Exception:
            approved = False
        approvals_out.append({"tool": tool_name, "approved": approved})
        return approved
    return confirm_callback


def run_live_conversation(goal: str, teacher_call: Callable[[str, str], str],
                          max_turns: int = 10, min_turns: int = 10) -> dict:
    core = CielCore()
    # Attended (default) — NOT unattended — so risky tools actually reach the
    # confirm_callback below instead of auto-DEFERing. See _make_confirm_callback.
    approvals: list = []
    transcript: list[dict] = []
    confirm_ctx = {"transcript": transcript, "user_msg": ""}
    core.confirm_callback = _make_confirm_callback(teacher_call, goal, approvals, confirm_ctx)
    sandbox = new_sandbox(f"ciel_live_{abs(hash(goal)) % 100000}_")
    stopped_reason = "max_turns_reached"

    try:
        isolate(core, sandbox)
        for turn_n in range(1, max_turns + 1):
            system_prompt = _USER_SIM_SYSTEM_PROMPT.format(
                goal=goal, min_turns=min_turns, max_turns=max_turns, turn_n=turn_n)
            user_prompt = _build_user_sim_prompt(transcript)
            try:
                sim_raw = teacher_call(system_prompt, user_prompt)
                sim = _extract_json_obj(sim_raw)
            except Exception as e:
                stopped_reason = f"user_sim_error: {type(e).__name__}: {e}"
                break

            user_msg = sim.get("message", "").strip()
            if not user_msg:
                stopped_reason = "user_sim_produced_no_message"
                break

            # Repeat guard: same spirit as vision_ops.py's stuck-loop breaker.
            if len(transcript) >= 1 and _normalize(user_msg) == _normalize(transcript[-1]["user"]):
                stopped_reason = "repeat_detected"
                break

            log_pos = THOUGHTS_LOG.stat().st_size if THOUGHTS_LOG.exists() else 0
            approvals.clear()
            confirm_ctx["user_msg"] = user_msg  # ground any confirm_callback fired during this turn
            t0 = time.time()
            try:
                response = core.process(user_msg)
            except Exception as e:
                response = f"[HARNESS_EXCEPTION] {type(e).__name__}: {e}"
            elapsed = time.time() - t0
            tools_fired = _recent_tools_fired(log_pos)
            thinking = _recent_brain_thinking(log_pos)

            transcript.append({
                "turn": turn_n, "user": user_msg, "response": response,
                "elapsed_s": round(elapsed, 2), "tools_fired": tools_fired,
                "thinking": thinking, "approvals": list(approvals),
            })

            if sim.get("done"):
                stopped_reason = f"user_sim_done: {sim.get('reason', '')}"
                break
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)

    return {"goal": goal, "stopped_reason": stopped_reason, "turns": transcript}


_GOAL_SCHEMA_NOTE = """
Return ONLY a JSON array of strings (no markdown fences, no prose). Each string is
ONE short persona+goal description (1-2 sentences), e.g.:
"A busy Vietnamese office worker who wants Ciel to check unread emails and, if
anything urgent comes up, draft (not send) a reply. Gets slightly impatient with
long-winded answers."
"""


def generate_goals(count: int, teacher_call: Callable[[str, str], str]) -> list[str]:
    catalog = get_tool_catalog()
    tool_lines = "\n".join(f"- {t['name']}: {t['description']}" for t in catalog)
    prompt = f"""Write {count} short persona+goal seeds for testing an AI agent named Ciel
that has real tools: {tool_lines}

Cover a range of tools across the {count} seeds. Favor goals that need multiple turns,
involve changing your mind, or reference something ambiguously ("that file", "the one
from before"), since those are the shapes that find real bugs in this agent.

EMAIL ADDRESSES: whenever a goal involves sending/receiving/searching an actual email,
use ONE of these three REAL addresses (the Master's own accounts) instead of a fictional
one like name@example.com — a fake domain never has real mail to find:
  kxctran@gmail.com, kxcpro123@gmail.com, prokxcpro@gmail.com
{_GOAL_SCHEMA_NOTE}"""
    raw = teacher_call("You write concise test-persona seeds for an AI agent test harness.", prompt)
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
    start, end = text.find("["), text.rfind("]")
    return json.loads(text[start:end + 1])


# Default categories mirror how the Master has actually been using Ciel this
# session (per thoughts.log): checking/replying to Gmail, market/gold-price
# reports emailed out, news lookups, and file/CV-style document work — real
# usage patterns find real bugs better than an evenly-spread tool catalog does.
DEFAULT_CATEGORIES = ["email", "CV / hồ sơ xin việc", "tin tức", "tình hình thị trường (giá vàng / crypto)"]


def generate_goals_by_category(categories: list[str], per_category: int,
                                teacher_call: Callable[[str, str], str], min_turns: int) -> list[dict]:
    """One persona+goal seed per category, tagged so the report can group by
    category. Explicitly asks for goals that need >= min_turns to play out
    fully (multiple sub-tasks, a correction, a related follow-up) rather than
    a single-shot request the User-Sim would mark 'done' after turn 1."""
    catalog = get_tool_catalog()
    tool_lines = "\n".join(f"- {t['name']}: {t['description']}" for t in catalog)
    goals = []
    for category in categories:
        prompt = f"""Write {per_category} short persona+goal seed(s) for testing an AI agent
named Ciel, FOCUSED ON THIS CATEGORY OF REAL USAGE: "{category}".

Ciel's real tools: {tool_lines}

The goal must require AT LEAST {min_turns} realistic back-and-forth turns to play out —
build in multiple related sub-tasks within the category, at least one change-of-mind or
correction, and at least one ambiguous/referential follow-up ("that email", "cái báo cáo
lúc nãy"). This should read like a real Vietnamese user's actual multi-step session, not
a single clean request.

EMAIL ADDRESSES: whenever the goal involves sending/receiving/searching an actual
email, use ONE of these three REAL addresses (the Master's own accounts) instead of a
fictional one like name@example.com or name@company.com — a fake domain never has real
mail to find, so search/reply/thread scenarios test nothing real:
  kxctran@gmail.com, kxcpro123@gmail.com, prokxcpro@gmail.com
{_GOAL_SCHEMA_NOTE}"""
        raw = teacher_call("You write concise test-persona seeds for an AI agent test harness.", prompt)
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
        start, end = text.find("["), text.rfind("]")
        for g in json.loads(text[start:end + 1]):
            goals.append({"category": category, "goal": g})
    return goals


def render_report(sessions: list[dict]) -> str:
    lines = ["# Live Conversation Test Report", "", f"{len(sessions)} session(s) run.", ""]
    by_category: dict[str, list[dict]] = {}
    for s in sessions:
        by_category.setdefault(s.get("category", "(uncategorized)"), []).append(s)

    for category, sessions_in_cat in by_category.items():
        lines.append(f"# Category: {category}")
        lines.append("")
        for i, s in enumerate(sessions_in_cat, 1):
            lines.append(f"## Session {i} — {len(s['turns'])} turn(s) — {s['stopped_reason']}")
            lines.append(f"**Goal:** {s['goal']}")
            lines.append("")
            for t in s["turns"]:
                tag = f" (tools: {', '.join(t['tools_fired'])})" if t["tools_fired"] else ""
                approvals = t.get("approvals") or []
                if approvals:
                    votes = ", ".join(f"{a['tool']}={'✅' if a['approved'] else '❌'}" for a in approvals)
                    tag += f" [approvals: {votes}]"
                lines.append(f"{t['turn']}. **User:** {t['user']}")
                lines.append(f"   **Ciel:** {t['response'][:400]}{tag}")
            lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sessions", type=int, default=10, help="Used only without --categories.")
    ap.add_argument("--categories", default="",
                     help="Comma-separated categories, e.g. 'email,CV,tin tức'. Empty = DEFAULT_CATEGORIES.")
    ap.add_argument("--per-category", type=int, default=1)
    ap.add_argument("--max-turns", type=int, default=10)
    ap.add_argument("--min-turns", type=int, default=10)
    ap.add_argument("--goals-file", default="")
    args = ap.parse_args()

    teacher_call = _build_teacher_call()

    if args.goals_file:
        raw_goals = json.loads(Path(args.goals_file).read_text(encoding="utf-8"))
        goals = [{"category": "(from file)", "goal": g} for g in raw_goals] if raw_goals and isinstance(raw_goals[0], str) else raw_goals
    else:
        categories = [c.strip() for c in args.categories.split(",") if c.strip()] or DEFAULT_CATEGORIES
        print(f"Generating goals for categories: {categories} ({args.per_category} each)...")
        goals = generate_goals_by_category(categories, args.per_category, teacher_call, args.min_turns)

    results = []
    for i, g in enumerate(goals, 1):
        print(f"[{i}/{len(goals)}] ({g['category']}) {g['goal'][:70]}...")
        session = run_live_conversation(g["goal"], teacher_call, max_turns=args.max_turns, min_turns=args.min_turns)
        session["category"] = g["category"]
        results.append(session)

    report = render_report(results)
    ts = time.strftime("%Y%m%d_%H%M%S")
    out_path = DEFAULT_REPORT_DIR / f"live_conversation_report_{ts}.md"
    out_path.write_text(report, encoding="utf-8")
    json_path = DEFAULT_REPORT_DIR / f"live_conversation_report_{ts}.json"
    json_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nReport: {out_path}\nJSON:   {json_path}")


if __name__ == "__main__":
    main()
