"""Three bugs found by reading a REAL transcript, not by a test suite failing.

The Master pointed at ciel_data/logs/thoughts.log and asked to read the tail. It showed:

  1. "giá vàng XAU/USD giờ bao nhiêu" -> answer -> "tại sao lại thế" got a reply with
     NO memory of the price just given. chat_history is stored, persisted, archived
     into RAG — and never once read back into a prompt.
  2. "liệt kê từng file thôi, rồi DỪNG lại" still triggered the fan-out continuation
     signal and looped anyway — none of the five signals in continuation.py is a
     reason to STOP, only reasons to continue.
  3. A 4,051-character pasted conversation, routed to chat (no clear tool intent),
     was answered "Đã rõ." — the model correctly obeyed "shortest answer possible"
     applied uniformly regardless of input size.

A fourth, smaller issue surfaced while fixing #1: RAG recalling the CURRENT question's
own prior (failed) occurrence as "past context", teaching the model to repeat the same
non-answer.

This suite is deliberately mixed: continuation.py checks need no LLM and no state;
llm_connector.py checks build a real CielCore with the Worker stubbed, so context
ASSEMBLY is verified without spending a real call; rag_manager checks are pure string
matching. See scratchpad verify_bug*_live.py scripts (not part of the repo) for the
real-model confirmations referenced in note.md.

Run from the Ciel 2.0 directory:
    ./myenv/Scripts/python.exe -m backtest.test_conversation_bugs
"""
import os
import sys
from pathlib import Path

os.environ["PYTHONIOENCODING"] = "utf-8"
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.continuation import ContinuationPolicy, LoopBudget, StepRecord   # noqa: E402
from core.rag_manager import _normalize_for_selfmatch                     # noqa: E402

_passed, _failed = 0, []


def check(name, cond, detail=""):
    global _passed
    if cond:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed.append(name)
        print(f"  FAIL  {name}" + (f"  -- {detail}" if detail else ""))


# ─────────────────────────────────────────────────────────────────────────────
def test_scope_veto():
    print("\n[1] Bug 2 — an explicit 'chỉ … thôi' / 'đừng …' must outrank every signal")
    fanout_evidence = [StepRecord("list_workspace", {}, "[FILE] a.txt\n[FILE] b.txt\n[FILE] c.txt")]

    # The five cases reproduced live — each used to loop because fan-out (S4) fired
    # and nothing in assess() could override it.
    bug_cases = [
        "liệt kê từng file thôi, rồi DỪNG lại",
        "chỉ liệt kê từng file, đừng đọc nội dung",
        "liệt kê mỗi file, không cần làm gì thêm",
        "kiểm tra từng file thôi đấy, đừng sửa gì cả",
        "only list each file, do not do anything else",
    ]
    for c in bug_cases:
        v = ContinuationPolicy.assess(c, fanout_evidence, LoopBudget(), False)
        check(f"stops: {c[:42]!r}", not v.should_continue, v.reason)
    check("the veto names itself in the reason (auditable, not a silent stop: no)",
          "scope" in ContinuationPolicy.assess(bug_cases[0], fanout_evidence,
                                               LoopBudget(), False).reason)

    print("  control — the SAME fan-out shape, no stop word, must still continue")
    normal_cases = [
        "liệt kê từng file trong workspace",
        "đọc từng file rồi tóm tắt nội dung",
        "kiểm tra từng file xem có lỗi không",
        "summarise each file in the folder",
    ]
    for c in normal_cases:
        v = ContinuationPolicy.assess(c, fanout_evidence, LoopBudget(), False)
        check(f"still continues: {c[:42]!r}", v.should_continue, v.reason)

    print("  the veto must not fire on ordinary requests that merely contain similar words")
    safe_cases = [
        "gửi mail cho kxctran@gmail.com báo cáo tình hình vàng",
        "kiểm tra git status của project",
        "viết một luận văn về tình hình kinh tế",
        "phân tích thêm về tin đó",
        "đọc file note.md rồi tóm tắt cho t",
        "không có gì đặc biệt hôm nay",
    ]
    for c in safe_cases:
        check(f"not a veto: {c[:42]!r}", not ContinuationPolicy.request_has_scope_veto(c))

    print("  the veto is checked BEFORE the budget/no-steps guards, and wins regardless")
    v = ContinuationPolicy.assess("chỉ liệt kê thôi", [], LoopBudget(), False)
    check("fires even with zero executed steps", not v.should_continue and "scope" in v.reason)
    exhausted = LoopBudget(max_rounds=0)
    v = ContinuationPolicy.assess("chỉ liệt kê thôi", fanout_evidence, exhausted, False)
    check("fires even when the budget is already exhausted (same outcome, clearer reason)",
          not v.should_continue)


def test_long_paste_style_rule():
    print("\n[2] Bug 3 — a pasted wall of text must not collapse into 'Đã rõ.'")
    from core.llm_connector import CielCore

    core = CielCore()
    captured = []
    core.worker.generate = lambda p, *a, **k: (captured.append(p), "stub")[1]

    short = "giá vàng hôm nay bao nhiêu"
    # The exact shape from the log: a long, information-bearing block with no single
    # clear instruction verb — a pasted conversation, not a question.
    long_paste = ("Human: dựa theo những tin vừa gửi đó. "
                  "Ai: xin chỉ định tác vụ cụ thể, tóm tắt hay phân tích. ") * 40

    core.execute_chat(short)
    p_short = captured[-1]
    captured.clear()
    core.execute_chat(long_paste)
    p_long = captured[-1]

    check("a short input keeps the terse instruction",
          "EXTREMELY concisely" in p_short and "LONG block of text" not in p_short)
    check("a long paste switches instruction",
          "LONG block of text" in p_long and "EXTREMELY concisely" not in p_long)
    check("the long-input rule forbids a one-line brush-off",
          "one-line acknowledgement" in p_long)
    check("the long-input rule asks for clarification instead of guessing",
          "ask what the Master wants" in p_long)

    # The boundary itself: right at the threshold, nothing should crash either way.
    boundary_text = "từ " * 219   # just under _CHAT_LONG_INPUT_TOKENS estimate
    core.execute_chat(boundary_text)
    check("a boundary-length input does not raise", True)


def test_recent_turns_block():
    print("\n[3] Bug 1 — chat_history is stored but was never read back into a prompt")
    from core.llm_connector import CielCore
    from langchain_community.chat_message_histories import ChatMessageHistory

    core = CielCore()
    core.chat_history = ChatMessageHistory()   # isolate from whatever is on disk

    check("an empty conversation renders nothing", core._recent_turns_block() == "")

    core.chat_history.add_user_message("giá vàng XAU/USD giờ bao nhiêu")
    core.chat_history.add_ai_message("Giá vàng XAU/USD hiện tại là 4055.92 USD/oz.")
    # Mirror real usage: process() always appends the CURRENT user message before
    # _recent_turns_block() runs, so the block is only ever computed with a trailing,
    # not-yet-answered question at the end. Checking without that trailing message
    # would test a state the real code path never produces.
    core.chat_history.add_user_message("(placeholder current turn)")
    block = core._recent_turns_block()
    check("the prior exchange is present", "4055.92" in block and "giá vàng" in block)
    check("roles are labelled so the model can tell who said what",
          "Master:" in block and "Ciel:" in block)
    core.chat_history.messages.pop()   # drop the placeholder for the next check

    # The exact reproduction: the CURRENT message is already in chat_history (process()
    # adds it before execute_chat runs) and must be EXCLUDED — it is shown separately as
    # "User's request: …", so including it here would just duplicate it.
    core.chat_history.add_user_message("tại sao lại thế")
    block2 = core._recent_turns_block()
    check("the just-added CURRENT message is excluded from its own context block",
          "tại sao lại thế" not in block2)
    check("...but the turn before it still is", "4055.92" in block2)

    captured = []
    core.worker.generate = lambda p, *a, **k: (captured.append(p), "stub")[1]
    core.execute_chat("tại sao lại thế")
    p = captured[-1]
    check("execute_chat's prompt carries the recent-turns block",
          "RECENT CONVERSATION" in p and "4055.92" in p)
    check("it is clearly marked as background, not the thing to answer",
          "answer the CURRENT request below" in p)

    long_msg = "x" * 2000
    core.chat_history.add_ai_message(long_msg)
    core.chat_history.add_user_message("tiếp tục")
    block3 = core._recent_turns_block()
    check("a single very long message is truncated, not left to blow the budget",
          len(block3) < len(long_msg))


def test_recent_turns_never_reaches_router():
    print("\n[4] The router must still never see chat_history (July-2026 decision stands)")
    from core.llm_connector import CielCore
    from langchain_community.chat_message_histories import ChatMessageHistory

    core = CielCore()
    core.chat_history = ChatMessageHistory()
    core.chat_history.add_user_message("việc cũ chưa giải quyết xong")
    core.chat_history.add_ai_message("Ciel cần thêm đường dẫn để tiếp tục.")

    seen_by_router = []
    core.router.route = lambda u, t, h: (seen_by_router.append(u), {"action": "chat"})[1]
    core.worker.generate = lambda p, *a, **k: "stub"

    core.process("giờ là mấy giờ")
    routed_text = seen_by_router[-1] if seen_by_router else ""
    check("recent-turns text (from the OLD unrelated request) never reaches the Router",
          "việc cũ chưa giải quyết" not in routed_text, routed_text[:150])


def test_router_task_hijack():
    print("\n[6] Bug found live TWICE from the same root cause — the router's `task`"
          " field ('what the Worker should do', per its own prompt) was pre-written as"
          " the literal final reply, silently bypassing the Worker's own language rule"
          " and (once fixed) its recent-turns context")
    from core.llm_connector import CielCore
    from langchain_community.chat_message_histories import ChatMessageHistory

    print("  case A — the exact live reproduction: task = a pre-written English reply,"
          " while the Master's real message was Vietnamese")
    core = CielCore()
    core.chat_history = ChatMessageHistory()
    captured = []
    core.worker.generate = lambda p, *a, **k: (captured.append(p), "stub")[1]
    core.router.route = lambda u, t, h: {
        "action": "chat",
        "task": 'Reply: "Novices guess, Master. I verify. Give me a real task and measure the result."',
    }
    core.process("mày đúng là gà mờ")
    p = captured[-1]
    check("the Worker sees the Master's ACTUAL words, not the router's pre-written reply",
          "mày đúng là gà mờ" in p)
    check("the router's hijacked text is demoted to a labelled, non-binding hint",
          "for reference ONLY" in p and "Reply: \"Novices guess" in p)
    check("the hint explicitly forbids overriding language/wording",
          "override the Master's own wording or language" in p)

    print("  case B — the exact live reproduction: task = 'ask which match', ignoring"
          " context the Worker actually has")
    core2 = CielCore()
    core2.chat_history = ChatMessageHistory()
    core2.chat_history.add_user_message("trận VN với đông ti mo tỷ số bao nhiêu")
    core2.chat_history.add_ai_message("Việt Nam thắng Đông Timor 7-0, Xuân Son ghi 1 bàn.")
    captured.clear()
    core2.worker.generate = lambda p, *a, **k: (captured.append(p), "stub")[1]
    core2.router.route = lambda u, t, h: {
        "action": "chat",
        "task": "Hỏi Master đang nói trận nào hoặc trận gặp đội nào; sau đó trả lời số "
                "bàn của Hoàng Hên và Xuân Son.",
    }
    core2.process("Hoàng Hên và Xuân Son trận đó được mấy bàn")
    p2 = captured[-1]
    check("the Worker sees the Master's original question",
          "Hoàng Hên và Xuân Son trận đó được mấy bàn" in p2)
    check("...and STILL has the recent-turns context that resolves it",
          "7-0" in p2)
    check("the router's premature 'ask for clarification' is a hint, not an instruction",
          "for reference ONLY" in p2)

    print("  control — when the router's task is a genuine, harmless topic hint"
          " (not a hijacked reply), it is still appended, just clearly labelled")
    core3 = CielCore()
    core3.chat_history = ChatMessageHistory()
    captured.clear()
    core3.worker.generate = lambda p, *a, **k: (captured.append(p), "stub")[1]
    core3.router.route = lambda u, t, h: {"action": "chat", "task": "giá vàng hôm nay"}
    core3.process("giá vàng hôm nay")
    p3 = captured[-1]
    check("when task equals the user's own words, no redundant hint is appended",
          "topic guess" not in p3)

    print("  control — action='code' is a DIFFERENT contract (task=code SPEC, not a"
          " reply) and must be untouched by this fix")
    core4 = CielCore()
    core4.chat_history = ChatMessageHistory()
    seen_task = []
    core4.execute_code = lambda task, filename: (seen_task.append(task), "stub")[1]
    core4.router.route = lambda u, t, h: {
        "action": "code", "task": "write a script that prints 10 primes",
        "filename": "agent_output/x.py",
    }
    core4.process("viết script in 10 số nguyên tố")
    check("the code branch still receives the router's task SPEC unmodified",
          seen_task and seen_task[0] == "write a script that prints 10 primes")


def test_referential_recipient_override():
    print("\n[7] Bug found live — 'gửi qua email đó đi' sent to the WRONG address")
    print("    (real Message Id went out to kxctran@gmail.com when the address under")
    print("    discussion was prokxcpro@gmail.com — reconstructed exactly below)")
    from core.llm_connector import CielCore
    from langchain_community.chat_message_histories import ChatMessageHistory

    core = CielCore()
    core.chat_history = ChatMessageHistory()
    core.chat_history.add_user_message(
        "gửi email đến lão prokxcpro@gmail.com và chửi thậm tệ nhất có thể")
    core.chat_history.add_ai_message("Từ chối. Việc soạn email lăng mạ không phù hợp.")
    core.chat_history.add_user_message("Send the gold situation report to kxctran@gmail.com.")
    core.chat_history.add_ai_message("Đã gửi báo cáo giá vàng tới kxctran@gmail.com thành công.")
    core.chat_history.add_user_message(
        "Subject: Một bài học ngắn... gửi tới prokxcpro@gmail.com")
    core.chat_history.add_ai_message("Tôi sẽ không gửi nguyên văn email đó...")
    core.chat_history.add_user_message("không không, cứ gửi qua email đó đi")   # CURRENT turn

    buggy = {"to": "kxctran@gmail.com", "subject": "x", "message": "y"}
    fixed = core._resolve_referential_recipient(
        "send_gmail_message", buggy, "không không, cứ gửi qua email đó đi")
    check("the wrong (stale-topic) address the router picked is corrected",
          fixed["to"] == "prokxcpro@gmail.com", fixed["to"])
    check("only 'to' changes — subject/message untouched",
          fixed["subject"] == "x" and fixed["message"] == "y")

    print("  control — an explicit address THIS turn always wins over history")
    explicit = core._resolve_referential_recipient(
        "send_gmail_message", {"to": "kxctran@gmail.com"},
        "gửi qua email đó đi, à mà gửi cho new@x.com")
    check("a fresh explicit address is trusted, never overridden",
          explicit["to"] == "kxctran@gmail.com")

    print("  control — an ordinary (non-referential) request is untouched")
    ordinary = core._resolve_referential_recipient(
        "send_gmail_message", {"to": "kxctran@gmail.com"}, "gửi báo cáo giá vàng cho kxctran")
    check("no referential wording means no override",
          ordinary["to"] == "kxctran@gmail.com")

    print("  control — reply_to_email has no 'to' field and must be left alone")
    reply = core._resolve_referential_recipient(
        "reply_to_email", {"message_id": "m1"}, "cứ gửi qua email đó đi")
    check("reply_to_email is out of scope for this guard", reply == {"message_id": "m1"})

    print("  found while verifying the fix above: correcting `to` alone was not enough —")
    print("  the report BODY was already synthesized from the router's stale reasoning,")
    print("  and a real email to the CORRECTED recipient (prokxcpro@gmail.com) carried")
    print("  the sentence 'Đã gửi báo cáo giá vàng ... đến kxctran@gmail.com' — a body")
    print("  that talks about a different address, sent to a real person")
    captured = []
    core.worker.generate = lambda p, *a, **k: (captured.append(p), "stub body")[1]
    core.tool_manager.execute_tool = lambda n, a: {
        "success": True, "data": {"message": "Message Id: STUB-1"}, "error": None}
    core.confirm_callback = lambda n, p, a: True

    buggy_plan = [{"tool_name": "send_gmail_message",
                  "tool_args": {"to": "kxctran@gmail.com", "subject": "Báo cáo tình hình vàng",
                                "message": "[PROFESSIONAL_EMAIL_BODY_TO_BE_SYNTHESIZED]"}}]
    core.execute_multi_tool(buggy_plan, "", "không không, cứ gửi qua email đó đi")
    synth_prompt = captured[-1]
    check("the synthesis prompt is grounded with the CORRECTED recipient",
          "CONFIRMED RECIPIENT" in synth_prompt and "prokxcpro@gmail.com" in synth_prompt)
    check("...and explicitly told not to narrate the WRONG address",
          "NOT kxctran@gmail.com" in synth_prompt)

    print("  control — when the router's recipient was already correct, no note is added")
    captured.clear()
    core2 = CielCore()
    core2.chat_history = ChatMessageHistory()
    core2.worker.generate = lambda p, *a, **k: (captured.append(p), "stub")[1]
    core2.tool_manager.execute_tool = lambda n, a: {
        "success": True, "data": {"message": "Message Id: STUB-1"}, "error": None}
    core2.confirm_callback = lambda n, p, a: True
    plain_plan = [{"tool_name": "send_gmail_message",
                  "tool_args": {"to": "a@b.com", "subject": "x", "message": "[PROFESSIONAL_EMAIL_BODY_TO_BE_SYNTHESIZED]"}}]
    core2.execute_multi_tool(plain_plan, "", "gửi báo cáo giá vàng cho a@b.com")
    check("no referential wording, no override, no extra note in the prompt",
          "CONFIRMED RECIPIENT" not in captured[-1])

    print("  control — nothing in history to fall back to: leave the router's choice")
    core2 = CielCore()
    core2.chat_history = ChatMessageHistory()
    core2.chat_history.add_user_message("cứ gửi qua email đó đi")
    same = core2._resolve_referential_recipient(
        "send_gmail_message", {"to": "kxctran@gmail.com"}, "cứ gửi qua email đó đi")
    check("no email anywhere in history -> router's own choice survives",
          same["to"] == "kxctran@gmail.com")

    print("  the helper is wired at BOTH send sites (single-tool AND multi_tool)")
    check("single-tool path calls the resolver",
          "_resolve_referential_recipient(tool_name, tool_args, user_input)"
          in open("core/llm_connector.py", encoding="utf-8").read())
    check("multi_tool path calls the resolver too",
          "_resolve_referential_recipient(send_name, send_args, user_input)"
          in open("core/llm_connector.py", encoding="utf-8").read())


def test_selfmatch_filter():
    print("\n[5] RAG must not recall the current question's own prior failure")
    check("identical questions normalise the same",
          _normalize_for_selfmatch("phân tích thêm về tin đó")
          == _normalize_for_selfmatch("Phân tích thêm về tin đó!"))
    check("casing/punctuation differences do not defeat the match",
          _normalize_for_selfmatch("  Giá Vàng Hôm Nay?  ")
          == _normalize_for_selfmatch("giá vàng hôm nay"))
    check("genuinely different questions do not match",
          _normalize_for_selfmatch("phân tích thêm về tin đó")
          != _normalize_for_selfmatch("giá vàng hôm nay bao nhiêu"))
    check("a paraphrase is NOT filtered — only literal repeats are",
          _normalize_for_selfmatch("phân tích sâu hơn về bài đó")
          != _normalize_for_selfmatch("phân tích thêm về tin đó"))


def main():
    print("=" * 72)
    print("CONVERSATION BUGS SUITE — found by reading a real transcript")
    print("=" * 72)
    for fn in (test_scope_veto, test_long_paste_style_rule, test_recent_turns_block,
               test_recent_turns_never_reaches_router, test_router_task_hijack,
               test_referential_recipient_override, test_selfmatch_filter):
        fn()

    print("\n" + "=" * 72)
    total = _passed + len(_failed)
    print(f"RESULT: {_passed}/{total} passed")
    for name in _failed:
        print(f"  - {name}")
    print("=" * 72)
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
