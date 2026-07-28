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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skills.external.telegram_ops import send_telegram_message

# Found live: category:primary alone doesn't exclude job-alert/marketing senders
# (ITviec, VietnamWorks, bank/wallet transaction notices, ...) — Gmail's own Primary
# tab already includes them for this account, verified against the real inbox. That's
# a curation problem, not a data-fetch bug, so the fix lives in how the digest is
# ASKED to present results, not in the Gmail query: split into "cần hành động" (a
# real reply owed, an application status change, a payment needing verification) vs.
# routine automated noise (job-board digests, marketing, standing subscriptions) —
# still list the second group, just compressed to sender+count, not summarized.
DIGEST_REQUEST = (
    "Tóm tắt ngắn gọn cho Master: (1) các email quan trọng/chưa đọc gần đây trong Gmail, "
    "(2) tin tức nổi bật hôm nay . "
    "Với phần (1), chia rõ 2 nhóm: "
    "'Cần chú ý' — email thực sự cần Master hành động hoặc biết (phản hồi cá nhân, "
    "kết quả ứng tuyển/phỏng vấn, giao dịch tài khoản/ngân hàng, xác minh tài khoản) — "
    "tóm tắt đầy đủ từng cái; "
    "'Tự động/định kỳ' — bản tin job-alert, quảng cáo, thông báo hàng loạt không cần "
    "hành động ngay — chỉ liệt kê gộp theo người gửi và số lượng, không tóm tắt từng cái. "
    "Trình bày súc tích, có mục rõ ràng cho từng phần."
)


def main() -> int:
    try:
        from core.llm_connector import CielCore
        core = CielCore()
        core.unattended = True

        digest = core.process(DIGEST_REQUEST)
        print(digest)

        sent = send_telegram_message(f"📰 *Bản tin hôm nay*\n\n{digest}")
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
