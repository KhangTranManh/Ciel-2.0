# Ciel Harness — Full Project Report

Generated: `2026-09-01 14:30:59`

## Scope and safety

This report covers the complete available `thoughts.log` history, every recurring signature at count >= 1,
all allow-listed prompt literals, a static AST inventory of tool registrations, and the maintained unit suite.
It does **not** execute live external or mutating tools: no email is sent, no file is deleted, no Git push occurs,
and no deployment action runs. Static inventory is coverage evidence, not proof that every provider works live.

## Executive summary

- Parsed log entries: **4405**
- Findings: **13** total; **8** eligible for Brain review; **5** blocked before model
- Prompt targets: **14** valid; **0** invalid
- Static tool registrations: **51** resolved; **0** dynamic/unresolved
- Tools with a prompt-owner mapping: **20**; without a dedicated mapping: **31**
- Maintained unit regression: **PASS** — 10 PASS / 0 FAIL out of 10 suites (37.5s)

## Logged findings

| Signature | Count | Target | Attribution | Runtime | Proposal |
|---|---:|---|---|---|---|
| HOLLOW_CONTENT / send_gmail_message | 31 | agent_system/models/middleware.py::MIDDLEWARE_SYSTEM_PROMPT | direct-event | disabled (MIDDLEWARE_ENABLED=false) | Blocked |
| HEALING_TRIGGER / UnclassifiedError | 16 | core/recovery_manager.py::ROBUST OS DEVELOPER prompt | direct-event/non-allow-listed-target | not-applicable | Blocked |
| TOOL_ERROR / send_gmail_message | 12 | skills/external/gmail_ops.py::GMAIL_SYSTEM_PROMPT | inferred | unknown-from-log | Brain review |
| SELF_CORRECTION:OTHER / stealth_search | 5 | skills/external/web_agent_ops.py::WEB_AGENT_SYSTEM_PROMPT | inferred | active-core-path | Brain review |
| SELF_CORRECTION:OTHER / git_commit_and_push | 2 | skills/external/github_ops.py::GIT_SYSTEM_PROMPT | inferred | active-core-path | Brain review |
| SELF_CORRECTION:INCOMPLETE_MULTISTEP | 2 | core/router.py::CIEL_ROUTER_PROMPT | inferred | active-core-path | Brain review |
| LANGUAGE_MISMATCH / send_gmail_message | 1 | agent_system/models/middleware.py::MIDDLEWARE_SYSTEM_PROMPT | direct-event | disabled (MIDDLEWARE_ENABLED=false) | Blocked |
| HEALING_TRIGGER / SyntaxError | 1 | core/recovery_manager.py::ROBUST OS DEVELOPER prompt | direct-event/non-allow-listed-target | not-applicable | Blocked |
| HEALING_TRIGGER / ModuleNotFoundError | 1 | core/recovery_manager.py::ROBUST OS DEVELOPER prompt | direct-event/non-allow-listed-target | not-applicable | Blocked |
| SELF_CORRECTION:HALLUCINATED_ACTION | 1 | core/router.py::CIEL_ROUTER_PROMPT | inferred | active-core-path | Brain review |
| SELF_CORRECTION:OTHER / read_file | 1 | skills/internal/system_ops.py::SYSTEM_OPS_PROMPT | inferred | active-core-path | Brain review |
| SELF_CORRECTION:OTHER / execute_shell_command | 1 | skills/internal/os_ops.py::OS_OPS_PROMPT | inferred | active-core-path | Brain review |
| SELF_CORRECTION:OTHER / search_gmail | 1 | skills/external/gmail_ops.py::GMAIL_SYSTEM_PROMPT | inferred | active-core-path | Brain review |

## Prompt target integrity

- PASS `core/router.py::CIEL_ROUTER_PROMPT`
- PASS `agent_system/models/brain.py::BRAIN_SYSTEM_PROMPT`
- PASS `agent_system/models/worker.py::WORKER_SYSTEM_PROMPT`
- PASS `agent_system/models/middleware.py::MIDDLEWARE_SYSTEM_PROMPT`
- PASS `skills/external/gmail_ops.py::GMAIL_SYSTEM_PROMPT`
- PASS `skills/external/github_ops.py::GIT_SYSTEM_PROMPT`
- PASS `skills/external/trading_ops.py::TRADING_SYSTEM_PROMPT`
- PASS `skills/external/web_agent_ops.py::WEB_AGENT_SYSTEM_PROMPT`
- PASS `skills/internal/os_ops.py::OS_OPS_PROMPT`
- PASS `skills/internal/system_ops.py::SYSTEM_OPS_PROMPT`
- PASS `skills/internal/productivity_ops.py::PRODUCTIVITY_PROMPT`
- PASS `skills/internal/report_ops.py::REPORT_PROMPT`
- PASS `skills/internal/monthly_plan_ops.py::MONTHLY_PLAN_PROMPT`
- PASS `skills/internal/weekly_plan_ops.py::WEEKLY_PLAN_PROMPT`

## Static tool inventory

| Tool | Pack | Prompt-owner mapping |
|---|---|---|
| `add_monthly_goal` | `skills/internal/monthly_plan_ops.py` | `No dedicated mapping` |
| `add_todo` | `skills/internal/productivity_ops.py` | `No dedicated mapping` |
| `add_weekly_task` | `skills/internal/weekly_plan_ops.py` | `No dedicated mapping` |
| `analyze_crypto_technical` | `skills/external/trading_ops.py` | `skills/external/trading_ops.py::TRADING_SYSTEM_PROMPT` |
| `append_file` | `skills/internal/system_ops.py` | `skills/internal/system_ops.py::SYSTEM_OPS_PROMPT` |
| `build_analysis_report_html` | `skills/internal/report_ops.py` | `No dedicated mapping` |
| `build_market_report_html` | `skills/external/trading_ops.py` | `skills/external/trading_ops.py::TRADING_SYSTEM_PROMPT` |
| `calculate` | `skills/internal/productivity_ops.py` | `No dedicated mapping` |
| `complete_monthly_goal` | `skills/internal/monthly_plan_ops.py` | `No dedicated mapping` |
| `complete_todo` | `skills/internal/productivity_ops.py` | `No dedicated mapping` |
| `complete_weekly_task` | `skills/internal/weekly_plan_ops.py` | `No dedicated mapping` |
| `delete_fact` | `skills/internal/memory_ops.py` | `No dedicated mapping` |
| `delete_file` | `skills/internal/system_ops.py` | `skills/internal/system_ops.py::SYSTEM_OPS_PROMPT` |
| `describe_image_file` | `skills/internal/vision_ops.py` | `No dedicated mapping` |
| `execute_shell_command` | `skills/internal/os_ops.py` | `skills/internal/os_ops.py::OS_OPS_PROMPT` |
| `get_crypto_stats` | `skills/external/trading_ops.py` | `skills/external/trading_ops.py::TRADING_SYSTEM_PROMPT` |
| `get_current_time` | `skills/internal/productivity_ops.py` | `No dedicated mapping` |
| `get_fact` | `skills/internal/memory_ops.py` | `No dedicated mapping` |
| `get_file_info` | `skills/internal/system_ops.py` | `No dedicated mapping` |
| `get_market_price` | `skills/external/trading_ops.py` | `skills/external/trading_ops.py::TRADING_SYSTEM_PROMPT` |
| `get_weather` | `skills/internal/productivity_ops.py` | `No dedicated mapping` |
| `git_commit_and_push` | `skills/external/github_ops.py` | `skills/external/github_ops.py::GIT_SYSTEM_PROMPT` |
| `git_confirm_push` | `skills/external/github_ops.py` | `skills/external/github_ops.py::GIT_SYSTEM_PROMPT` |
| `git_diff` | `skills/external/github_ops.py` | `No dedicated mapping` |
| `git_list_repos` | `skills/external/github_ops.py` | `No dedicated mapping` |
| `git_status` | `skills/external/github_ops.py` | `No dedicated mapping` |
| `grep_in_workspace` | `skills/internal/productivity_ops.py` | `No dedicated mapping` |
| `list_monthly_goals` | `skills/internal/monthly_plan_ops.py` | `No dedicated mapping` |
| `list_todos` | `skills/internal/productivity_ops.py` | `No dedicated mapping` |
| `list_weekly_plan` | `skills/internal/weekly_plan_ops.py` | `No dedicated mapping` |
| `list_workspace` | `skills/internal/system_ops.py` | `No dedicated mapping` |
| `mark_email_read` | `skills/external/gmail_ops.py` | `skills/external/gmail_ops.py::GMAIL_SYSTEM_PROMPT` |
| `open_application` | `skills/internal/os_ops.py` | `No dedicated mapping` |
| `read_document` | `skills/internal/system_ops.py` | `skills/internal/system_ops.py::SYSTEM_OPS_PROMPT` |
| `read_file` | `skills/internal/system_ops.py` | `skills/internal/system_ops.py::SYSTEM_OPS_PROMPT` |
| `reply_to_email` | `skills/external/gmail_ops.py` | `skills/external/gmail_ops.py::GMAIL_SYSTEM_PROMPT` |
| `run_python_script` | `skills/internal/system_ops.py` | `skills/internal/system_ops.py::SYSTEM_OPS_PROMPT` |
| `save_fact` | `skills/internal/memory_ops.py` | `No dedicated mapping` |
| `search_gmail` | `skills/external/gmail_ops.py` | `skills/external/gmail_ops.py::GMAIL_SYSTEM_PROMPT` |
| `send_gmail_html_message` | `skills/external/gmail_ops.py` | `skills/external/gmail_ops.py::GMAIL_SYSTEM_PROMPT` |
| `send_telegram` | `skills/external/telegram_ops.py` | `No dedicated mapping` |
| `send_telegram_document` | `skills/external/telegram_ops.py` | `No dedicated mapping` |
| `smart_scrape` | `skills/external/web_agent_ops.py` | `skills/external/web_agent_ops.py::WEB_AGENT_SYSTEM_PROMPT` |
| `stealth_search` | `skills/external/web_agent_ops.py` | `skills/external/web_agent_ops.py::WEB_AGENT_SYSTEM_PROMPT` |
| `take_screenshot` | `skills/internal/os_ops.py` | `No dedicated mapping` |
| `trash_email` | `skills/external/gmail_ops.py` | `skills/external/gmail_ops.py::GMAIL_SYSTEM_PROMPT` |
| `update_monthly_goal` | `skills/internal/monthly_plan_ops.py` | `No dedicated mapping` |
| `update_weekly_task` | `skills/internal/weekly_plan_ops.py` | `No dedicated mapping` |
| `vision_act` | `skills/internal/vision_ops.py` | `No dedicated mapping` |
| `vision_describe` | `skills/internal/vision_ops.py` | `No dedicated mapping` |
| `write_file` | `skills/internal/system_ops.py` | `skills/internal/system_ops.py::SYSTEM_OPS_PROMPT` |

## Live coverage deliberately excluded

The following need separate, attended and capability-specific smoke tests:

- Gmail search/draft/send/reply/trash and Message ID verification
- Telegram text/document delivery and deduplication
- Web search/scrape provider quality and current-data completeness
- Git commit/push confirmation against an operator-selected repository
- Shell, deletion, desktop vision, and other high-risk operations
- Live Brain/Worker integration, long conversation, and RAG stress suites

These are not safe to combine into one unattended 'run everything' command because their permissions, test data,
external recipients, cost, and cleanup requirements differ.

## Recommended review order

1. Resolve invalid prompt targets or unit failures first.
2. Review blocked findings as code/config/provider work; do not force prompt candidates.
3. Run Brain diagnosis only for eligible findings, one exact signature at a time.
4. Review one generated candidate and run its focused smoke before apply/commit.
5. Run live tools separately with explicit recipients, isolated fixtures, and confirmation enabled.
