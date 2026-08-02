"""Tầng 6-adjacent — one-shot CI job: ask Ciel (through real Brain → tool routing,
not hand-rolled calls) to summarize today's important email and news, then report
the result to Telegram. Runs once daily via cron (.github/workflows/health_check.yml),
inside the same Docker image that workflow already builds for the health check.

Deliberately goes through core.process() rather than calling search_gmail/
stealth_search directly — this request gets the same routing, self-correction, and
safety gate any live Master request gets, instead of a hand-rolled path that bit-rots
independently of the real pipeline.

core.unattended = True before the call: nobody is watching a scheduled CI run, so if
Brain's routing ever reached for something in the high-risk list, the Tier-6 rule
applies — DEFER, never auto-approve on silence (core/permissions.py). Neither
search_gmail nor stealth_search is high-risk, so this is defense-in-depth, not
something expected to trigger today.

Run manually:  python -m scripts.daily_digest
Run in CI:      see .github/workflows/health_check.yml (runs after the health check)
Exit code: 0 on success, 1 on failure. Independent of scripts/health_check.py's own
exit code — a broken digest should never report Ciel itself as unhealthy, and a
provider outage that already failed the health check should skip this step entirely
(see the workflow: this step only runs if the health check step succeeded).
"""
from __future__ import annotations

import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skills.external.telegram_ops import send_telegram_message

# Master is Vietnam-local; CI runners are UTC. Anchor "hôm nay" to Asia/Ho_Chi_Minh
# so a 00:00 UTC cron (07:00 VN) and a late-evening local run agree on the calendar
# day the digest is *for*. Fall back to system local if zoneinfo data is missing.
_MASTER_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def _now_master() -> datetime:
    try:
        return datetime.now(_MASTER_TZ)
    except Exception:
        return datetime.now().astimezone()


def build_digest_request(now: datetime | None = None) -> str:
    """Build the digest prompt with a *real* wall-clock date injected.

    The model must not invent "today" from training weights (wrong year/day is a
    recurring failure mode on news queries). Python's datetime is the source of
    truth; get_current_time remains available as an optional cross-check tool.
    """
    now = now or _now_master()
    today = now.strftime("%Y-%m-%d")
    today_vn = now.strftime("%d/%m/%Y")
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    yesterday_vn = (now - timedelta(days=1)).strftime("%d/%m/%Y")
    clock_line = now.strftime("%Y-%m-%d %H:%M %Z")

    # Found live: category:primary alone doesn't exclude job-alert/marketing senders
    # — curation lives in presentation (two buckets), not the Gmail query.
    #
    # News half (found live 2026-08): vague "tin nổi bật hôm nay" + SerpApi shells
    # like "Tin thế giới nổi bật trong ngày 15/5" with no body. Require scrape +
    # honest empty state; pin "hôm nay" to the system clock below.
    return (
        f"[SYSTEM CLOCK — source of truth for this request]\n"
        f"Now: {clock_line}\n"
        f"Hôm nay (today) = {today} ({today_vn}, Asia/Ho_Chi_Minh).\n"
        f"Hôm qua (yesterday) = {yesterday} ({yesterday_vn}).\n"
        f"Do NOT use the model's trained idea of the current date. "
        f"If you need a second check, call get_current_time — but this block wins "
        f"on any conflict.\n"
        f"\n"
        f"Lập BẢN TIN NGẮN cho Master (tiếng Việt), 2 phần. PHẢI gọi tool thật; "
        f"không bịa số liệu hay headline.\n"
        f"\n"
        f"## (1) Gmail gần đây\n"
        f"Dùng search_gmail (email gần đây / chưa đọc; ưu tiên category:primary nếu tool hỗ trợ). "
        f"Chia 2 nhóm:\n"
        f"- 'Cần chú ý' — cần Master biết hoặc hành động (đăng nhập lạ, giao dịch ngân hàng/ví, "
        f"ticket support, ứng tuyển/phỏng vấn, xác minh tài khoản, mail fail delivery quan trọng). "
        f"Tóm tắt đầy đủ từng cái (ai gửi, việc gì, cần làm gì).\n"
        f"- 'Tự động/định kỳ' — job-alert, marketing, newsletter hàng loạt: CHỈ gộp theo "
        f"người gửi + số lượng, không tóm từng cái.\n"
        f"\n"
        f"## (2) Tin tức nổi bật hôm nay ({today} / {today_vn})\n"
        f"Bắt buộc pipeline:\n"
        f"1) stealth_search với timelimit='d' (past day). Gắn ngày thật vào query khi hữu ích "
        f"(ví dụ gồm '{today}' hoặc '{today_vn}'). 1–2 query (thế giới + Việt Nam). "
        f"Ưu tiên bài có Snippet + Published = {today} hoặc {yesterday} từ báo thật.\n"
        f"2) smart_scrape ÍT NHẤT 2 URL bài viết cụ thể (không scrape trang chủ / trang mục). "
        f"Ưu tiên VnExpress, Tuổi Trẻ, Thanh Niên, Reuters, BBC, AP, Nikkei khi có trong kết quả.\n"
        f"3) Mỗi tin trong bản tin: 1–2 câu nội dung + nguồn + ngày. CẤM liệt kê chỉ title không body.\n"
        f"4) BỎ qua hit kiểu 'Tin thế giới nổi bật trong ngày 15/5' nếu ngày trong title "
        f"không phải {today_vn} hoặc {yesterday_vn} — shell tổng hợp cũ, không phải tin hôm nay.\n"
        f"5) Nếu sau search+scrape vẫn không có nội dung dùng được: viết một dòng "
        f"'Chưa lấy được nội dung tin ngày {today}.' — KHÔNG dump danh sách title rỗng.\n"
        f"\n"
        f"Trình bày súc tích, có heading ## 1 / ## 2 rõ ràng. "
        f"Ghi dòng ngày bản tin: {today_vn}."
    )


# Back-compat for anything that imported the old constant (tests / notebooks).
DIGEST_REQUEST = build_digest_request()


def main() -> int:
    try:
        from core.llm_connector import CielCore
        core = CielCore()
        core.unattended = True

        # Rebuild at run time so a long-lived process cannot reuse a stale module-level date.
        request = build_digest_request()
        print(f"[daily_digest] clock: {_now_master().isoformat()}")
        digest = core.process(request)
        print(digest)

        today_vn = _now_master().strftime("%d/%m/%Y")
        sent = send_telegram_message(f"📰 *Bản tin {today_vn}*\n\n{digest}")
        if not sent:
            print("[daily_digest] Telegram not configured or send failed — "
                  "see console output above for the result.")
        return 0

    except Exception as e:
        print(f"[daily_digest] Failed: {e}")
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
