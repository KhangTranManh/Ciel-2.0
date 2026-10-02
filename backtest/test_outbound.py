"""Outbound idempotence — one delivery per recipient per turn.

WHY THIS SUITE EXISTS
---------------------
Two independent mechanisms complete a plan that is missing its send step: the
deterministic workflow safeguard in `execute_multi_tool` (which appends one) and the
Tier-1 loop (which re-plans one when the Brain sets `needs_followup`). Neither knew about
the other, so "gửi mail cho X báo cáo Y" delivered the same report TWICE, with two
different subjects. Reproduced live before the fix; this suite is what stops it coming
back — including from some third path added later, since the guard sits at the one choke
point every send goes through.

No LLM calls: the tool layer is stubbed, so what is under test is purely the decision to
deliver or suppress. Requires `.env` only because CielCore constructs its clients at
init (it never calls them here).

Run from the Ciel 2.0 directory:
    ./myenv/Scripts/python.exe -m backtest.test_outbound
"""
import os
import sys
import tempfile
import time
from uuid import uuid4
from pathlib import Path

os.environ["PYTHONIOENCODING"] = "utf-8"
# The unified unit runner disables external packs. This suite needs Gmail tool
# *names* to reach CielCore's outbound guard, but never a real Gmail client.
# Re-enable the module before config loads, then replace its factory below.
_disabled_skills = {s.strip() for s in os.environ.get("DISABLED_SKILL_MODULES", "").split(",") if s.strip()}
_disabled_skills.discard("gmail_ops")
os.environ["DISABLED_SKILL_MODULES"] = ",".join(sorted(_disabled_skills))
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.llm_connector import CielCore     # noqa: E402
from langchain_core.tools import StructuredTool  # noqa: E402
from skills.external import gmail_ops  # noqa: E402


def _stub_gmail_tool():
    """Placeholder only; make_core replaces ToolManager.execute_tool before use."""
    return "Message Id: STUB-1"


def _stub_gmail_factory():
    return {
        "tools": [
            StructuredTool.from_function(_stub_gmail_tool, name=name, description="Unit-test Gmail stub")
            for name in ("send_gmail_message", "send_gmail_html_message", "reply_to_email",
                         "search_gmail", "get_gmail_message")
        ],
        "prompt": "",
    }


gmail_ops.get_gmail_tools = _stub_gmail_factory

_passed, _failed = 0, []


def check(name, cond, detail=""):
    global _passed
    if cond:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed.append(name)
        print(f"  FAIL  {name}" + (f"  -- {detail}" if detail else ""))


def make_core():
    """A core whose tool layer is stubbed: nothing leaves the machine."""
    core = CielCore()
    # Tests must never read, clear, or overwrite a real user's pending action.
    core._pending_state_path = Path(tempfile.gettempdir()) / f"ciel_outbound_{uuid4().hex}.json"
    core._pending_action = None
    core.sent = []          # what the tool layer was actually asked to deliver
    core.asked = []         # what the safety gate prompted about
    core.fail_next = False

    def fake_exec(name, args):
        core.sent.append((name, dict(args or {})))
        if core.fail_next:
            # Phrased to match `_HEALING_SKIP_PATTERNS`, so self-healing correctly
            # declines to retry a network failure — which is also the realistic shape of
            # a failed send, and keeps this suite free of live retry calls.
            return {"success": False,
                    "result": "EXECUTION_ERROR: Max retries exceeded with url: gmail"}
        return {"success": True, "result": "Message Id: STUB-1"}

    core.tool_manager.execute_tool = fake_exec
    core.tool_manager.format_tool_result = lambda r: r.get("result", "")
    core.confirm_callback = lambda n, p, a: (core.asked.append(n), True)[1]
    # The failure path rephrases errors through the Worker, and self-healing can retry
    # through it too. Both are real network calls that have nothing to do with what is
    # under test here, so the Worker is stubbed out entirely.
    core.worker.generate = lambda *a, **k: "the provider refused the message"
    return core


MAIL = {"to": "kxctran@gmail.com", "subject": "Báo cáo vàng", "message": "nội dung"}


# ─────────────────────────────────────────────────────────────────────────────
def test_key_shapes():
    print("\n[1] What counts as 'the same message'")
    c = make_core()
    k = c._outbound_key

    check("plain and HTML sends to one address are DIFFERENT tools but both keyed",
          k("send_gmail_message", MAIL) == "send_gmail_message:kxctran@gmail.com"
          and k("send_gmail_html_message", MAIL) == "send_gmail_html_message:kxctran@gmail.com")
    check("the recipient is normalised, so casing cannot slip a duplicate through",
          k("send_gmail_message", {"to": "  KXCTran@Gmail.COM "})
          == "send_gmail_message:kxctran@gmail.com")
    check("a reply is keyed by the thread it answers, not by an address",
          k("reply_to_email", {"message_id": "abc123"}) == "reply_to_email:abc123")
    check("telegram has one destination, so the tool itself is the key",
          k("send_telegram", {"message": "hi"}) == "send_telegram")
    check("a send with no recipient is not keyed — let the tool report the real error",
          k("send_gmail_message", {"subject": "x"}) is None)
    check("non-outbound tools are untouched",
          k("read_file", {"filename": "a"}) is None and k("git_status", {}) is None)


def test_duplicate_suppressed():
    print("\n[2] The bug — the same report must not go out twice")
    c = make_core()

    out1 = c.execute_tool("send_gmail_message", dict(MAIL), user_input="gửi mail")
    check("the first send goes through", len(c.sent) == 1 and "Message Id" in out1)

    # The real duplicate had a DIFFERENT subject — that is exactly why a signature over
    # all arguments would never have caught it.
    out2 = c.execute_tool("send_gmail_message",
                          dict(MAIL, subject="Báo cáo từ Ciel"), user_input="gửi mail")
    check("the second send to the same recipient is suppressed", len(c.sent) == 1,
          str(c.sent))
    check("...even though its subject differs", "[SKIPPED]" in out2, out2)
    check("the Master is told plainly, not silently ignored",
          "trùng" in out2 or "Đã gửi tới người nhận này" in out2, out2)

    check("suppression happens BEFORE the safety gate — no pointless prompt",
          c.asked == ["send_gmail_message"], str(c.asked))


def test_direct_success_clears_matching_pending_action():
    """Self-correction can complete a preview follow-up without a bare 'yes'."""
    c = make_core()
    c._pending_action = {
        "tool": "send_gmail_message",
        "args": dict(MAIL),
        "ts": time.time(),
        "from": "preview_tool",
    }
    c.execute_tool("send_gmail_message", dict(MAIL), user_input="continue")
    check("direct successful follow-up clears its stale pending action",
          c._pending_action is None, repr(c._pending_action))

    c = make_core()
    c._pending_action = {
        "tool": "send_gmail_message",
        "args": dict(MAIL, subject="different"),
        "ts": time.time(),
        "from": "preview_tool",
    }
    c.execute_tool("send_gmail_message", dict(MAIL), user_input="continue")
    check("a different pending action is never cleared accidentally",
          c._pending_action is not None, repr(c._pending_action))


def test_scope():
    print("\n[3] Scope — per recipient, per turn, and nothing wider")
    c = make_core()
    c.execute_tool("send_gmail_message", dict(MAIL), user_input="x")

    c.execute_tool("send_gmail_message", dict(MAIL, to="someone.else@example.com"),
                   user_input="x")
    check("a DIFFERENT recipient still receives their message", len(c.sent) == 2,
          str([a.get("to") for _, a in c.sent]))

    c.execute_tool("read_file", {"filename": "note.md"}, user_input="x")
    c.execute_tool("read_file", {"filename": "note.md"}, user_input="x")
    check("ordinary tools may still repeat — only DELIVERY is once-per-turn",
          sum(1 for n, _ in c.sent if n == "read_file") == 2)

    # A new request may legitimately mail the same person again.
    c._sent_this_turn = set()       # what process() does at the start of a turn
    c.execute_tool("send_gmail_message", dict(MAIL), user_input="x")
    check("a NEW request can mail the same person again",
          sum(1 for n, a in c.sent if a.get("to") == MAIL["to"]) == 2)


def test_failure_is_retryable():
    print("\n[4] A failed send must stay retryable")
    c = make_core()
    c.fail_next = True
    out = c.execute_tool("send_gmail_message", dict(MAIL), user_input="x")
    check("the failure is reported", "[TOOL_ERROR]" in out or "Sorry" in out, out[:60])
    check("the attempt reached the tool layer", len(c.sent) == 1)

    c.fail_next = False
    c.execute_tool("send_gmail_message", dict(MAIL), user_input="x")
    check("a retry after a failure IS allowed — marking it delivered would lose the mail",
          len(c.sent) == 2, str(len(c.sent)))

    c.execute_tool("send_gmail_message", dict(MAIL), user_input="x")
    check("...and once it finally succeeds, the guard engages", len(c.sent) == 2)


def test_html_and_reply():
    print("\n[5] The other outbound tools")
    c = make_core()
    c.execute_tool("send_gmail_html_message", dict(MAIL), user_input="x")
    c.execute_tool("send_gmail_html_message", dict(MAIL, subject="khác"), user_input="x")
    check("HTML sends are guarded too", len(c.sent) == 1)

    check("a plain send to the same address is a separate tool and still allowed",
          (c.execute_tool("send_gmail_message", dict(MAIL), user_input="x"),
           len(c.sent))[1] == 2)

    c2 = make_core()
    c2.execute_tool("reply_to_email", {"message_id": "m1", "message": "ok"}, user_input="x")
    c2.execute_tool("reply_to_email", {"message_id": "m1", "message": "ok again"}, user_input="x")
    check("replying twice to one thread is suppressed", len(c2.sent) == 1)
    c2.execute_tool("reply_to_email", {"message_id": "m2", "message": "ok"}, user_input="x")
    check("a different thread still gets its reply", len(c2.sent) == 2)


def test_stale_send_status():
    print("\n[6] A report must not deny a send that already succeeded")
    c = make_core()
    s = c._strip_stale_send_status

    # The exact shape observed live, while the mail was in fact delivered.
    live = ("Giá XAU/USD: 4055.92\n"
            "Trạng thái: Chưa gửi email tới kxctran@gmail.com – chưa có kết quả gửi "
            "thực tế từ công cụ. Nội dung báo cáo đã sẵn sàng.")
    out = s(live)
    check("the stale denial is removed", "Chưa gửi email" not in out, out)
    check("...including the second half of the same claim",
          "chưa có kết quả gửi" not in out, out)
    check("the real content survives untouched",
          "4055.92" in out and "Nội dung báo cáo đã sẵn sàng" in out, out)

    for phrase in ["Email chưa được gửi.", "The report has not been sent.",
                   "Chưa xác nhận gửi email.", "No confirmation of the send yet.",
                   "Không thể xác nhận đã gửi mail.", "Report not yet sent to the user."]:
        r = s(f"Dữ liệu: 100.\n{phrase}\nHết.")
        check(f"removed: {phrase!r}", phrase.rstrip(".") not in r, r)

    # The asymmetry that keeps anti-fabrication intact.
    positive = "Đã gửi email tới a@b.com thành công. Message Id: abc123."
    check("a POSITIVE send claim is never touched — it is the true one here",
          s(positive) == positive, s(positive))
    check("an unrelated negation is not collateral damage",
          "chưa có dữ liệu" in s("Thị trường chưa có dữ liệu mới hôm nay."))
    check("text with no status claim is returned identically",
          s("Giá vàng hôm nay là 4055.") == "Giá vàng hôm nay là 4055.")

    check("empty and None are safe", s("") == "" and s(None) in ("", None))
    messy = s("Dòng một.\n\nChưa gửi email.\n\n\nDòng hai.")
    check("no orphaned blank block is left where the sentence was",
          "\n\n\n" not in messy and "Dòng một." in messy and "Dòng hai." in messy,
          repr(messy))


def test_markdown_email_render():
    print("\n[7] Markdown in an email body renders as HTML, not raw pipes")
    from core.outbound import plaintext_to_html as render

    # Shape of the body delivered live on 2026-09-29.
    body = ("Chào chị Nga,\n\nGửi chị bảng giá vàng miếng SJC 999.9 hôm nay:\n\n"
            "| | Giá (nghìn đồng/chỉ) |\n|---|--:|\n| **Mua vào** | 13.950 |\n| **Bán ra** | 14.250 |\n\n"
            "## Yếu tố ảnh hưởng\n\n- Chênh lệch trong nước – thế giới\n- Nhu cầu <mua> tăng\n\n"
            "1. Theo dõi thêm\n2. Cân nhắc thời điểm\n\n---\n\nTrân trọng.")
    out = render(body)
    check("table becomes an HTML table", "<table" in out and out.count("<tr") == 3, out)
    check("no raw separator row survives", "|---" not in out and "--:" not in out, out)
    check("no raw pipes survive", "|" not in out, out)
    check("header cells render as th", "<th" in out and "Giá (nghìn đồng/chỉ)" in out, out)
    check("right-aligned column honours the separator", "text-align:right\">13.950" in out, out)
    check("bold inside a cell renders", "<strong>Mua vào</strong>" in out, out)
    check("heading renders without hashes", "##" not in out and "Yếu tố ảnh hưởng" in out, out)
    check("bullets become a ul", "<ul" in out and out.count("<li") == 4, out)
    check("numbered items become an ol", "<ol" in out, out)
    check("horizontal rule renders", "<hr" in out, out)
    check("user text is still escaped", "&lt;mua&gt;" in out and "<mua>" not in out, out)
    check("plain paragraphs keep line breaks",
          "Chào chị Nga," in render("Chào chị Nga,\nDòng hai.") and
          "<br>" in render("Chào chị Nga,\nDòng hai."))
    check("a single pipe line is not mistaken for a table",
          "<table" not in render("Giá | Mua | Bán"))
    already = "<p>already html</p>"
    check("existing HTML passes through untouched", render(already) == already)
    check("empty body is safe", render("") == "")


def _email_plan_core(verify_ok=True):
    """A core running search → send, whose Worker WOULD claim 'sent' if asked to
    format a step — so any early claim in the output proves per-step formatting ran."""
    c = make_core()
    c.prompts = []

    def fake_exec(name, args):
        c.sent.append((name, dict(args or {})))
        if name == "search_gmail":
            return {"success": True, "result": "[1] message_id: m1  subject: Giá vàng SJC 13.950"}
        if name == "get_gmail_message":
            if not verify_ok:
                return {"success": False, "result": "EXECUTION_ERROR: Max retries exceeded"}
            return {"success": True, "result": '{"id": "%s"}' % args.get("message_id")}
        return {"success": True, "result": "Message sent. Message Id: abc123"}

    def fake_generate(prompt, *a, **k):
        c.prompts.append(prompt)
        if "Raw result:" in prompt:
            return "Đã gửi email tới kxctran@gmail.com rồi nha."
        return "Chào bạn,\n\nGiá vàng SJC hôm nay: 13.950."

    c.tool_manager.execute_tool = fake_exec
    c.worker.generate = fake_generate
    return c


EMAIL_PLAN = [{"tool_name": "search_gmail", "tool_args": {}},
              {"tool_name": "send_gmail_message", "tool_args": dict(MAIL)}]
EMAIL_REQUEST = "gửi email giá vàng tới kxctran@gmail.com"


def test_sent_claim_only_after_verified_send():
    print("\n[8] 'Sent' is claimed only after a real send that Gmail confirms")
    c = _email_plan_core()
    out = c.execute_multi_tool([dict(s) for s in EMAIL_PLAN], "", user_input=EMAIL_REQUEST)
    names = [n for n, _ in c.sent]
    check("no per-step Worker formatting while a send is pending",
          not any("Raw result:" in p for p in c.prompts), str(len(c.prompts)))
    check("the synthesis read the raw evidence", any("13.950" in p for p in c.prompts))
    check("data step ran before the send", names[:2] == ["search_gmail", "send_gmail_message"], str(names))
    check("the sent message is fetched back by its Message Id",
          ("get_gmail_message", {"message_id": "abc123"}) in c.sent, str(c.sent))
    check("the reply opens with a verified confirmation",
          out.startswith("✅ Đã gửi email tới kxctran@gmail.com") and "abc123" in out
          and "kiểm tra lại" in out, out[:200])
    check("no early 'sent' sentence from a step survives", "rồi nha" not in out, out)
    check("the content that was sent is shown after the confirmation",
          out.index("Nội dung đã gửi") < out.index("Giá vàng SJC hôm nay"), out)

    c2 = _email_plan_core()
    c2.confirm_callback = lambda n, p, a: n == "plan"     # approve the plan, decline the send
    out2 = c2.execute_multi_tool([dict(s) for s in EMAIL_PLAN], "", user_input=EMAIL_REQUEST)
    check("a declined send is reported as not sent",
          out2.startswith("❌ Chưa gửi email tới kxctran@gmail.com"), out2[:160])
    check("a declined send is never 're-checked'",
          not any(n == "get_gmail_message" for n, _ in c2.sent), str(c2.sent))

    c3 = _email_plan_core(verify_ok=False)
    out3 = c3.execute_multi_tool([dict(s) for s in EMAIL_PLAN], "", user_input=EMAIL_REQUEST)
    check("a failed re-check is a warning, not a confirmation",
          out3.startswith("⚠️") and "abc123" in out3, out3[:200])

    c4 = _email_plan_core()
    out4 = c4.execute_multi_tool([dict(s) for s in EMAIL_PLAN], "",
                                 user_input="send the gold price email to kxctran@gmail.com")
    check("English request gets an English confirmation", out4.startswith("✅ Email sent to"), out4[:120])


def main():
    print("=" * 72)
    print("OUTBOUND IDEMPOTENCE SUITE (tool layer stubbed — nothing is sent)")
    print("=" * 72)
    for fn in (test_key_shapes, test_duplicate_suppressed,
               test_direct_success_clears_matching_pending_action, test_scope,
               test_failure_is_retryable, test_html_and_reply, test_stale_send_status,
               test_markdown_email_render, test_sent_claim_only_after_verified_send):
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
