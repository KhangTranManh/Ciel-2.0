"""Quality guards folded from one-off smokes (P1.1/P1.4/Telegram/HTML).

Pure / low-LLM unit checks — run via:
    python -m backtest.test_quality_guards
    python -m backtest.run_all --unit-only
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
# This module is a no-network unit suite. CielCore normally auto-loads every
# skill, and external integrations may contact OAuth/provider services during
# that load. These guards test only core/internal behavior, so exclude external
# packs before agent_system.config is first imported below.
_disabled_skills = {s.strip() for s in os.environ.get("DISABLED_SKILL_MODULES", "").split(",") if s.strip()}
_disabled_skills.update({"github_ops", "gmail_ops", "telegram_ops", "trading_ops", "web_agent_ops"})
os.environ["DISABLED_SKILL_MODULES"] = ",".join(sorted(_disabled_skills))
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_passed, _failed = 0, []


def check(name, cond, detail=""):
    global _passed
    if cond:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed.append(name)
        print(f"  FAIL  {name}" + (f"  -- {detail}" if detail else ""))


def test_write_intent_telegram_note():
    print("\n[1] Write-intent must not fire on Telegram inbound notes")
    from core.llm_connector import _has_write_intent, _has_telegram_send_intent

    note = (
        "[File Master vừa gửi qua Telegram, đã lưu tại: "
        "ciel_workspace/telegram_uploads/20260806_x.txt]\n"
        "Tóm tắt file này và gửi qua Telegram cho t."
    )
    check("inbound 'đã lưu tại' is not write intent", _has_write_intent(note) is None)
    check(
        "explicit save path still is write intent",
        _has_write_intent("viết báo cáo vào agent_output/report.txt")
        == "agent_output/report.txt",
    )
    check("telegram send intent detected", _has_telegram_send_intent(note))
    metadata_only = (
        "[Ảnh Master vừa gửi qua Telegram, đã lưu tại: "
        "ciel_workspace/telegram_uploads/20260806_x.jpg]\n"
        "Phân tích ảnh này dùm t."
    )
    check(
        "inbound Telegram metadata alone is not send intent",
        not _has_telegram_send_intent(metadata_only),
    )
    check(
        "plain chat is not telegram-send intent",
        not _has_telegram_send_intent("xin chào Ciel"),
    )
    from core.llm_connector import _has_telegram_delivery_step
    check(
        "HTML document counts as an existing Telegram delivery",
        _has_telegram_delivery_step([{"tool_name": "send_telegram_document", "tool_args": {}}]),
    )
    check(
        "ordinary tools do not count as Telegram delivery",
        not _has_telegram_delivery_step([{"tool_name": "read_document", "tool_args": {}}]),
    )


def test_scrape_url_extraction():
    print("\n[1b] Smart scrape URL extraction")
    from skills.external.web_agent_ops import _article_url_from_scrape_input

    search_block = (
        "Search Results for 'news':\n"
        "1. Title: First\nURL: https://example.com/first-story\n"
        "2. Title: Second\nURL: https://example.com/second-story"
    )
    check("extracts first URL from a search block",
          _article_url_from_scrape_input(search_block) == "https://example.com/first-story")
    check("keeps a direct article URL",
          _article_url_from_scrape_input("https://example.com/a") == "https://example.com/a")
    check("rejects non-URL scrape input", _article_url_from_scrape_input("Search Results only") == "")


def test_sanitize_outbound():
    print("\n[2] Outbound sanitize — path leak vs keep numbers")
    from core.llm_connector import CielCore

    core = CielCore()
    dirty = (
        "Chi tiết tại ciel_workspace/secret.txt và agent_output/r.md.\n"
        "Giá BTC: 64891.99 USDT.\n"
    )
    clean = core._sanitize_outbound_email(dirty, keep_paths=False)
    check("strips ciel_workspace/", "ciel_workspace/" not in clean, clean[:80])
    check("strips agent_output/", "agent_output/" not in clean, clean[:80])
    check("keeps BTC number", "64891.99" in clean, clean[:80])
    keep = core._sanitize_outbound_email(dirty, keep_paths=True)
    check("keep_paths retains workspace paths", "ciel_workspace/" in keep, keep[:80])
    meta = "[COGNITION] x\nGiá XAU: 4050.12\nMessage Id: abc\n"
    c2 = core._sanitize_outbound_email(meta, keep_paths=False)
    check("strips COGNITION / Message Id", "[COGNITION]" not in c2 and "Message Id" not in c2)
    check("keeps XAU", "4050.12" in c2)


def test_gmail_format_digest():
    print("\n[3] Gmail list format keeps multi message_ids")
    from skills.external.gmail_ops import _format_gmail_search_results
    from core.llm_connector import CielCore

    items = [
        {
            "id": f"id{i}",
            "threadId": f"t{i}",
            "from": f"a{i}@x.com",
            "subject": f"S{i}",
            "snippet": "snip",
            "body": "BODY " * 100,
        }
        for i in range(1, 6)
    ]
    fmt = _format_gmail_search_results(items)
    check("5 message_id lines", fmt.count("message_id:") == 5, str(fmt.count("message_id:")))
    check("digest stays compact", len(fmt) < 4000, str(len(fmt)))
    core = CielCore()
    compact = core._compact_email_result(fmt)
    check("compact keeps ≥5 ids", compact.count("message_id:") >= 5, str(compact.count("message_id:")))


def test_analysis_html_builder():
    print("\n[4] analysis HTML builder writes agent_output only")
    from skills.internal.report_ops import build_analysis_report_html
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    out = "agent_output/_quality_guard_report.html"
    msg = build_analysis_report_html(
        title="Quality guard",
        source_path="ciel_workspace/x.txt",
        source_type="text",
        summary="Summary line",
        key_points="- a\n- b",
        risks="N/A",
        actions="None",
        quotes="Q",
        output_path=out,
    )
    path = root / out
    check("writes file", path.exists(), str(path))
    html = path.read_text(encoding="utf-8") if path.exists() else ""
    check("filled template no {{ leftover", "{{" not in html and "Quality guard" in html)
    default_msg = build_analysis_report_html(
        title="Default path",
        source_path="ciel_workspace/telegram_uploads/Tran_Manh_Khang_dev_CV.pdf",
        source_type="pdf",
        summary="Summary line",
        key_points="- a",
    )
    default_path = root / "agent_output" / "Tran_Manh_Khang_dev_CV_summary.html"
    check("missing output_path uses agent_output default", default_path.exists(), default_msg)
    refuse = build_analysis_report_html(
        title="x",
        source_path="a",
        source_type="text",
        summary="s",
        key_points="k",
        output_path="telegram_uploads/bad.html",
    )
    check("refuses telegram_uploads output", "refuse" in refuse.lower() or "Error" in refuse)
    if path.exists():
        path.unlink()
    if default_path.exists():
        default_path.unlink()


def test_telegram_upload_write_block():
    print("\n[5] execute_tool blocks write into telegram_uploads")
    from core.llm_connector import CielCore

    core = CielCore()
    core.confirm_callback = lambda *a, **k: True
    out = core.execute_tool(
        "write_file",
        {
            "filename": "ciel_workspace/telegram_uploads/should_not_write.txt",
            "content": "nope",
        },
        user_input="tóm tắt file",
    )
    check("write blocked with SKIPPED", "SKIPPED" in (out or "") or "không ghi đè" in (out or "").lower(), (out or "")[:120])


def test_shell_allowlist_matches_host_os():
    print("\n[7] Shell allowlist follows the host OS (Linux container ≠ Windows)")
    from skills.internal import os_ops

    original = os_ops.SAFE_COMMANDS
    try:
        os_ops.SAFE_COMMANDS = os_ops._POSIX_COMMANDS
        v = os_ops._validate_shell_command
        check("linux: which/cat/ls/git are allowed",
              all(v(c) is None for c in ("which git", "cat /repo/README.md", "ls -la", "git -C /repo log --oneline -10")))
        check("linux: a pipe between allowed commands is fine", v("git log --oneline | head -5") is None)
        check("linux: windows-only commands are not offered",
              v("where git") and v("where git")[0] == "NOT_ALLOWLISTED")
        check("linux: package installs stay blocked",
              v("apt-get install -y git")[0] == "NOT_ALLOWLISTED" and v("sudo ls")[0] == "NOT_ALLOWLISTED")
        check("linux: chaining is still rejected", v("git status && ls")[0] == "UNSAFE_OPERATOR")
        check("linux: find cannot delete or exec",
              v("find / -name x -delete")[0] == "BLOCKED_COMMAND"
              and v("find . -exec ls {} +")[0] == "BLOCKED_COMMAND")
        os_ops.SAFE_COMMANDS = os_ops._WINDOWS_COMMANDS
        check("windows: the original list is unchanged",
              v("where git") is None and v("ipconfig") is None and v("cat x")[0] == "NOT_ALLOWLISTED")
    finally:
        os_ops.SAFE_COMMANDS = original

    prompt = os_ops.get_os_tools()["prompt"]
    check("prompt tells the model which shell it is on", os_ops.HOST_SHELL in prompt, prompt[-300:])


def test_git_list_repos_bounded():
    print("\n[8] git_list_repos finds repos without walking into link loops")
    import tempfile
    from skills.external.github_ops import get_github_tools

    tool = next(t for t in get_github_tools()["tools"] if t.name == "git_list_repos")
    with tempfile.TemporaryDirectory() as root:
        base = Path(root)
        (base / "proj" / ".git").mkdir(parents=True)
        (base / "deep" / "a").mkdir(parents=True)
        try:
            os.symlink(base, base / "deep" / "a" / "loop", target_is_directory=True)
        except (OSError, NotImplementedError):
            pass
        result = tool.func(str(base))
        repos = (result.get("data") or {}).get("repos") or []
        check("finds the repository", any(r["path"].endswith("proj") for r in repos), str(result)[:200])
        check("does not report the repo again through the link loop", len(repos) == 1, str(repos))


def main():
    print("=" * 72)
    print("QUALITY GUARDS (unit — former smoke coverage)")
    print("=" * 72)
    test_write_intent_telegram_note()
    test_scrape_url_extraction()
    test_sanitize_outbound()
    test_gmail_format_digest()
    test_analysis_html_builder()
    test_telegram_upload_write_block()
    test_shell_allowlist_matches_host_os()
    test_git_list_repos_bounded()
    total = _passed + len(_failed)
    print("\n" + "=" * 72)
    print(f"RESULT: {_passed}/{total} passed")
    if _failed:
        print("Failed:", ", ".join(_failed))
    print("=" * 72)
    return 0 if not _failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
