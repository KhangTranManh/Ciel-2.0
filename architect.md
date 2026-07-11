# Ciel 2.0 Project Roadmap (EN + VI)

This document is a full project map for architecture, dependencies, and functions.

---

## 1) High-Level Architecture | Kiến trúc tổng thể

Ciel 2.0 uses a **Modular Brain-Middleware-Worker** architecture (third tier added July 5, 2026) where responsibilities are cleanly separated:

- **Entry point | Điểm vào:** `main.py` starts CLI loop and delegates each command to `AgentLoop`.
- **Thin agent loop | Vòng lặp agent:** `core/agent_loop.py` is a thin wrapper that passes user input to `CielCore.process()`.
- **Main orchestrator | Bộ điều phối chính:** `core/llm_connector.py` (`CielCore`) is the central pipeline. It initializes Brain, Middleware, Worker, Router, RecoveryManager, and ToolManager, then coordinates the full `route → execute → verify → respond` flow.
- **Middleware | Tầng trung gian (mới):** `agent_system/models/middleware.py` (`class Middleware`) is a third LLM tier — a semantic verifier/finalizer for outbound content (email/report bodies only, `MIDDLEWARE_SCOPE=email`). It checks relevance, internal consistency, and structural grounding (templated shells with no real content), and can revise the body in place (max `MIDDLEWARE_MAX_PASSES` passes, default 1). Disabled by default (`MIDDLEWARE_ENABLED`); when off, `core/llm_connector.py`'s `_middleware_review()` is a no-op passthrough. Deliberately NOT a replacement for deterministic checks — it is a backstop for the class of error only an LLM can judge (e.g. "the body claims Bearish but the numbers show price above both moving averages", "user asked about the World Cup but the body is about the economy").
- **Router | Bộ định tuyến:** `core/router.py` uses the Brain LLM to classify user intent into `chat`, `tool`, `code`, or `multi_tool` actions. Includes WINDOWS SYSTEM ARCHITECT and anti-hallucination guardrails. Contains RECALLED CONTEXT rule to prefer RAG data over redundant `get_fact` calls.
- **Self-healing engine | Hệ thống tự sửa lỗi:** `core/recovery_manager.py` implements multi-attempt (up to 3) autonomous error correction with a ROBUST OS DEVELOPER prompt, syntax validation, and escalating fix strategies.
- **Hybrid Memory (RAG) | Bộ nhớ lai:** `core/rag_manager.py` provides long-term semantic memory via ChromaDB + `all-MiniLM-L6-v2` embeddings. Short-term: `memory_bank.json` (max 20 messages). Long-term: `ciel_data/vector_memory/` (ChromaDB). Overflow messages are automatically archived into vector storage via `_trim_history()`. Recall is filtered by `MIN_QUERY_LENGTH=15` and `MIN_RELEVANCE_SCORE=0.65`, then compressed by a two-tier recall cleaner: regex/structural filtering first, Worker-based compression only when recalled context remains large.
- **Proactive Scheduler | Lịch trình chủ động:** `core/scheduler.py` runs background tasks on a timer using zero-token standby. Tools are called directly (bypassing Brain) to save API costs. Currently schedules a Morning Digest at 08:00 daily (Gmail + Forex/Metals → Worker summary → Telegram notification).
- **Tool registry/execution | Kho công cụ & thực thi:** `core/tool_manager.py` loads internal + external tool packs, stitches tool manuals, executes by tool name.
- **Agent system | Hệ thống agent:** `agent_system/` contains the Brain and Worker LLM models, provider config, LangGraph pipeline, and buffer writer tool.
- **Tool packs | Các gói kỹ năng:**
  - Internal: workspace/file ops + PDF/DOCX reader (`skills/internal/system_ops.py`), host OS control (`skills/internal/os_ops.py`), fact tools (`skills/internal/memory_ops.py` — standalone), productivity/todos/utilities (`skills/internal/productivity_ops.py`), vision/UI (`skills/internal/vision_ops.py`)
  - External: Gmail toolkit/extensions (`skills/external/gmail_ops.py`), crypto/trading toolkit (`skills/external/trading_ops.py`), Telegram notifications (`skills/external/telegram_ops.py`), Git manager (`skills/external/github_ops.py`), web search/scrape (`skills/external/web_agent_ops.py`)
- **Testing scripts | Script kiểm thử:** `backtest/test_integration.py` (full 17+ test pipeline), `backtest/test_brain_worker.py` (multi-step/multi-file workflow tests), `backtest/test_rag_memory.py` (45-prompt amnesia stress test for hybrid memory), `backtest/test_hard_special.py` + results (hard cases for email bypass, market+send flows, path handling, partial failures, self-correction, mixed language).
- **Workspace sandbox | Vùng workspace:** `ciel_workspace/` contains files created/tested by tools.

---

## 2) Runtime Flow | Luồng chạy

### Primary Pipeline (CielCore — used by main.py)

```
User Input → CielCore.process()
  → RAG Recall: search ChromaDB for semantically similar past context
    (skipped if query < 15 chars or relevance < 0.65)
  → RAG Compression:
    Tier 1: zero-token regex/structural cleanup removes old smart_scrape/git_diff/HTML noise
    Tier 2: if still > ~1000 tokens, Worker compresses recalled logs into factual Human/Ai lines
  → Inject recalled context into user prompt (if any)
  → Router (Brain LLM) classifies intent → JSON decision
  → Based on action:
      "chat"       → Worker generates natural response
      "tool"       → Safety gate (controlled ONLY by DISABLE_SAFETY_GATE; default active).
                     High-risk tools (send_gmail_message, send_gmail_html_message, reply_to_email,
                     delete_file, execute_shell_command, trash_email, git_confirm_push, vision_act)
                     require Master's Y/N. write_file/append_file also gate when their content
                     matches a genuinely destructive pattern (drive format, mkfs, rmtree, fork bomb).
                     ↳ Outbound email bodies sanitized here (single choke-point in execute_tool).
                     ↳ Middleware semantic review/finalize runs AFTER sanitize, BEFORE the safety-gate
                       preview — so a denied/approved Y/N reflects the FINAL body (email tools only).
                     ↳ stealth_search/smart_scrape queries get stale-year bump + recency window.
                     ↳ send_gmail_message body rendered to HTML AFTER the safety-gate preview
                       (preview stays plain/readable; delivered body preserves line breaks).
                     ToolManager executes → Worker formats result (if needed)
                     ↳ Brain Self-Correction: evaluates result (with deterministic failure-floor).
                       If unsatisfied, autonomously retries with different tool/args (max 2 attempts)
      "code"       → Worker generates code (gated: dangerous patterns require Y/N, see above)
                     → buffer_writer flushes to user-specified or clarified path (agent_output/ or ciel_workspace/)
      "multi_tool" → Sequential tool execution (data tools first) → Worker synthesizes combined report (for email sends: synthesis happens before final send re-execution so body contains real filled data)
  → Self-Healing Loop (if error detected):
      Attempt 1: Fix obvious cause (syntax/import)
      Attempt 2: Rewrite logic with alternative approach
      Attempt 3: Full rewrite using only standard libraries
      Each attempt: Syntax validation before saving → Re-execute tool
  → _trim_history(): if messages > 20, overflow → archived into ChromaDB
  → Response saved to chat memory → returned to user
```

### Agent System Pipeline (LangGraph — used by agent_system/main.py)

```
User Input → Brain Node (plan) → Worker Node (execute steps) → File Write Node (buffer → disk)
  → Steps loop until all plan items are complete
  → Each Worker step is ISOLATED — shared_context provides cross-step info
```

---

## 3) File Architecture (Current) | Cấu trúc file hiện tại

```text
Ciel 2.0/
├── .env                          # API keys, model names, provider config
├── .gitignore
├── architect.md                  # This file — full project map
├── note.txt                      # Project status + email templates (grounding for prompts)
├── credentials.json              # Google OAuth credentials
├── main.py                       # CLI entry point (input loop + safety callback)
├── main_api.py                   # FastAPI/WebSocket backend. WS /ws (chat+thoughts+vitals+confirm)
│                                 # + REST GET /skills (dynamic manifest), /health. UI reads these.
├── ui/                           # React frontend (browser-first) + Tauri v2 desktop shell — see ui/README.md
│   ├── src/core/                 #   transport+protocol (ws, bus, types, http) — modality-agnostic
│   ├── src/io/                   #   MODALITY LAYER: input/ (Text live, Voice stub), output/ (Transcript, speaker stub)
│   ├── src/components/           #   SkillGrid (dynamic), ThoughtStream, VitalsBar, ConfirmDialog
│   ├── src/hooks/useCiel.ts      #   bus <-> React bridge
│   └── src-tauri/                #   Tauri v2 shell: tauri.conf.json (devUrl→:1420, dist→../dist), Cargo
├── requirements.txt              # Python dependencies
│
├── core/                         # Main orchestration layer
│   ├── agent_loop.py             # Thin wrapper → CielCore.process()
│   ├── llm_connector.py          # CielCore: main pipeline orchestrator
│   ├── rag_manager.py            # RAG: ChromaDB vector memory (long-term)
│   ├── router.py                 # Router: Brain-based intent classification
│   ├── recovery_manager.py       # RecoveryManager: multi-attempt self-healing
│   ├── scheduler.py              # Proactive background task scheduler
│   ├── tool_manager.py           # ToolManager: tool registry & execution
│   ├── cost.py                   # LLM pricing table + estimate_cost() (overridable via ciel_data/model_pricing.json)
│   ├── voice_input.py            # CLI speech-to-text (sounddevice + google/whisper/gemini backends)
│   └── speech_output.py          # CLI text-to-speech (to_speech() normalizer + edge/pyttsx3/space backends)
│
├── agent_system/                 # Brain-Worker LLM subsystem
│   ├── __init__.py
│   ├── config.py                 # Provider/model/retry configuration
│   ├── main.py                   # Standalone LangGraph runner
│   ├── requirements.txt
│   ├── models/
│   │   ├── brain.py              # Brain LLM (Router + Planner)
│   │   ├── worker.py             # Worker LLM (Code/Text generator)
│   │   └── middleware.py         # Middleware LLM (semantic verifier/finalizer, July 2026)
│   ├── graph/
│   │   ├── state.py              # AgentState TypedDict
│   │   ├── nodes.py              # brain_node, worker_node, file_write_node
│   │   ├── edges.py              # Conditional routing edges
│   │   └── builder.py            # LangGraph compilation
│   ├── tools/
│   │   └── buffer_writer.py      # In-memory code buffer with flush-to-disk
│   └── utils/
│       ├── logger.py             # Colored console logger
│       └── usage.py              # extract_usage()/format_usage() — provider token counts for cost tracking
│
├── skills/                       # Tool packs (auto-discovered by ToolManager via get_*_tools())
│   ├── internal/
│   │   ├── memory_ops.py         # Fact vault tools (standalone JSON-based)
│   │   ├── os_ops.py             # Shell, screenshot, app launcher
│   │   ├── system_ops.py         # Workspace file CRUD + Python runner + read_document (PDF/DOCX)
│   │   ├── productivity_ops.py   # Todos, time, weather, calculate, grep_in_workspace
│   │   └── vision_ops.py         # Vision & UI Interaction (grid overlay + Gemini Vision + PyAutoGUI)
│   └── external/
│       ├── github_ops.py         # Git repo manager (status, diff, commit, push)
│       ├── gmail_ops.py          # Gmail toolkit + custom ops (send/html/reply/draft/trash/search)
│       ├── telegram_ops.py       # Telegram Bot API notifications
│       ├── trading_ops.py        # Crypto price, TA, Forex/Metals + build_market_report_html
│       └── web_agent_ops.py      # stealth_search (DuckDuckGo, recency-aware) + smart_scrape (Jina)
│
├── persona/                      # Personality
│   └── official_ciel_personality.txt  # The ONLY persona file loaded at startup (Ultimate Sage
│                                 # identity + tone + behavioral logic + Operational Directives).
│                                 # Legacy fragments (directives/format/identity.txt) were merged
│                                 # into this file and removed on July 9, 2026 — see Changelog.
│
├── backtest/                     # Test suites
│   ├── test_integration.py       # 17+ test full pipeline validation
│   ├── test_brain_worker.py      # Brain-Worker multi-file workflow tests
│   ├── test_rag_memory.py        # 45-prompt amnesia stress test (hybrid memory)
│   ├── test_hard_special.py      # Hard/special cases — rewritten July 2026 to assert REAL behavior
│   │                              # (file-on-disk, real Gmail Message Id, no dangerous code) instead of
│   │                              # substring-matching response wording; auto-attaches each test's own
│   │                              # thoughts.log slice; ends with a BUG DASHBOARD summarizing every
│   │                              # Middleware catch / tool error / healing trigger / failed check.
│   ├── verify_gmail_send.py / run_multi_gmail_test.py / run_bot_again_test.py  # Gmail + multi-tool verification helpers
│   └── logs/                     # Test output logs: test_integration_*.json/.txt, hard_special_*.json
│
├── scripts/                      # Maintenance/debug helper scripts
│   ├── format_thoughts_log.py     # Generate readable Markdown + JSONL views from thoughts.log
│   ├── prompt_harness.py          # Mine thoughts.log for recurring failure patterns → propose prompt patches (never auto-applies)
│   └── cost_report.py             # Cumulative LLM cost/usage report from thoughts.log (by tier/model/day)
│
├── ciel_data/                    # Runtime data
│   ├── facts.json                # Fact vault
│   ├── gmail_token.json          # Google OAuth token (auto-generated)
│   ├── memory_bank.json          # Chat history persistence (short-term, max 20)
│   ├── vector_memory/            # ChromaDB persistent storage (long-term RAG)
│   └── logs/
│       ├── thoughts.log          # Raw chronological Brain/Worker thought audit trail
│       ├── thoughts_view.md      # Generated readable grouped debug view (ignored by git)
│       └── thoughts_view.jsonl   # Generated structured log view (ignored by git)
│
├── ciel_workspace/               # Sandbox for user data, logs, screenshots, and user-specified files
│
└── agent_output/                 # Primary location for AI-generated code (user can also direct writes here via explicit paths)
```

---

## 4) Dependencies Map | Bản đồ phụ thuộc

### 4.1 Declared Python dependencies (`requirements.txt`)

- **LLM / Orchestration**
  - `langchain>=0.1.0`
  - `langchain-community`
  - `langchain-core>=0.1.10`
  - `langchain-ollama`
  - `langchain-google-genai`
  - `langchain-openai`
  - `langgraph`
  - `tenacity`
- **Data / TA**
  - `pandas`
  - `pandas-ta`
- **Google integrations**
  - `langchain-google-community`
  - `google-api-python-client`
  - `google-auth-httplib2`
  - `google-auth-oauthlib`
- **Utilities**
  - `beautifulsoup4`
  - `requests`
  - `python-dotenv`
  - `colorama`
  - `psutil`
  - `pillow`
- **RAG / Vector Memory**
  - `chromadb`
  - `sentence-transformers` (model: `all-MiniLM-L6-v2`)
- **Web Search & Scraping**
  - `ddgs` (DuckDuckGo search — powers `stealth_search`, with recency `timelimit`)
  - `requests` (used by `smart_scrape` via Jina Reader)
- **Document Reading**
  - `pypdf` (PDF text extraction)
  - `python-docx` (Word .docx text extraction)
- **Web Server & Communication**
  - `fastapi`, `uvicorn`, `websockets` (Flutter HUD backend `main_api.py`)
- **Vision & UI Interaction**
  - `pyautogui`, `pyperclip`, `google-genai`
- **Scheduling**
  - `schedule`

### 4.2 Multi-Provider Support

Ciel supports multiple LLM providers, configurable independently for Brain and Worker via `.env` or `agent_system/config.py`.

**Current recommended setup:**
- **Brain (Router):** Vilao (`alic/qwen3.7-max`) — with `SAFETY_OPEN`, `VILAO_SAFETY_BYPASS` for reduced filtering on normal tasks.
- **Worker (Generator):** DeepSeek (`deepseek-chat`).
- **Middleware (Verifier, optional third tier):** disabled unless `MIDDLEWARE_ENABLED=true`. Mirrors Brain's provider branching (vilao/deepseek/gpt/ollama/gemini). Deliberately kept a DIFFERENT model/provider from Worker where practical — a verifier sharing the generator's exact training-data blind spots (e.g. stale real-world price priors) is less likely to catch what the generator gets wrong.

Supported providers include Gemini, DeepSeek, Ollama, and Vilao (OpenAI-compatible). Provider selection is done with `BRAIN_PROVIDER` / `WORKER_PROVIDER` / `MIDDLEWARE_PROVIDER` and corresponding keys (e.g., `VILAO_KEY`, `VILAO_URL`).

**Request timeout (July 2026):** every LLM client across all three tiers (Brain/Worker/Middleware) now sets an explicit `timeout=LLM_REQUEST_TIMEOUT` (default 90s, `agent_system/config.py`). Without it, a provider that stalls (accepts the connection but never replies) hangs the client forever and the existing `tenacity` retry logic never engages (it only retries on an actual raised exception). Observed live: `openai.APITimeoutError` now fires and is caught gracefully instead of hanging indefinitely.

Safety behavior is controlled by **two INDEPENDENT flags** (decoupled July 2026 — see Changelog):
- `SAFETY_OPEN=true` — governs **Brain LLM content-filtering** only (reduces over-blocking of normal tasks like email). Does NOT affect the destructive-tool gate.
- `DISABLE_SAFETY_GATE` — governs the **destructive-tool confirmation gate** only. Default **`false`** (gate ACTIVE / fail-safe): eight pre-declared high-risk tools (`delete_file`, `execute_shell_command`, `send_gmail_message`, `send_gmail_html_message`, `reply_to_email`, `trash_email`, `git_confirm_push`, `vision_act`) require Y/N approval, PLUS a content-based gate (July 6, 2026) on `write_file`/`append_file`/`execute_code` when the content matches a real destructive pattern (drive format, `mkfs`, `shutil.rmtree`, fork bomb). Set `true` only for fully unattended automation.
- Targeted protections retained for violent text, leaks, harm, and destructive actions.

### 4.3 Key runtime services / credentials

- **Vilao (current Brain):** `VILAO_KEY`, `VILAO_URL` (with SAFETY_OPEN / VILAO_SAFETY_BYPASS for permissive routing)
- **DeepSeek (current Worker):** `DEEPSEEK_API_KEY`
- **Gemini / Ollama:** Still supported via respective keys and base URLs
- **Trading (TwelveData):** `TWELVEDATA_API_KEY`
- **Google OAuth:** `credentials.json`, token files under `ciel_data/`
- **Telegram Notifications:** `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`

### 4.4 Internal dependency graph

```
main.py → core.agent_loop.AgentLoop
core.agent_loop → core.llm_connector.CielCore

CielCore initializes:
  ├── agent_system.models.brain.Brain        (Router LLM)
  ├── agent_system.models.worker.Worker      (Generator LLM)
  ├── core.router.Router                     (Intent classification)
  ├── core.recovery_manager.RecoveryManager  (Self-healing)
  ├── core.rag_manager                       (Hybrid memory: ChromaDB + embeddings)
  └── core.tool_manager.ToolManager          (Tool registry)
       └── skills.internal.* + skills.external.*

main.py also initializes:
  └── core.scheduler.CielScheduler           (Background task manager)
       └── Directly calls Gmail API + TwelveData API + Telegram API

skills.internal.memory_ops → standalone (reads/writes ciel_data/facts.json directly)
```

---

## 5) Key Modules Deep Dive | Chi tiết các module chính

### 5.1 Router (`core/router.py`)

The Router uses the Brain LLM to classify user input into one of four action types:

- `chat` — Conversation, questions, explanations
- `tool` — Single tool execution (email, file ops, trading, shell)
- `code` — Code generation. Target path is taken from user request or asked explicitly (supports `agent_output/` or `ciel_workspace/`).
- `multi_tool` — Sequential multi-tool workflow with synthesized report

**Prompt Roles Embedded:**
- **Chain-of-Thought (CoT) Audit:** Enforces output of `hidden_thought` (observation, reasoning, risk) before `action` to ensure debuggability and logical routing.
- **WINDOWS SYSTEM ARCHITECT:** Forces absolute paths in double quotes, `python -m` prefix, and `taskkill` suggestions for locked files.
- **Anti-hallucination:** Explicit rule: `NEVER output "action": "shell_command"` — must use `"tool"` with `"execute_shell_command"`. Also: never invent numbers/prices for email bodies — reuse only facts already in history, else route to `multi_tool` to fetch.
- **Current-date anchor (July 2026):** The Router system message injects today's date + current year, so the Brain uses the real year for "latest/mới nhất" searches instead of defaulting to a stale training-data year.

### 5.2 Recovery Manager (`core/recovery_manager.py`)

Multi-attempt self-healing system with two strategies:

**Strategy A — Code Fix** (for `run_python_script` errors):
1. Reads the failed source code from disk
2. Sends to Worker with ROBUST OS DEVELOPER prompt
3. Validates syntax via `check_syntax()` before saving
4. Escalates strategy on each attempt (syntax fix → rewrite → stdlib-only rewrite)

**Strategy B — Parameter Fix** (for other tool errors):
1. Sends failed tool name + args + error to Worker
2. Worker returns corrected JSON args
3. Re-executes tool with corrected arguments

**Skip-list (added July 9, 2026):** Strategy B used to fire on ANY tool whose result started with
"Error" in the first 30 chars — including errors no `tool_args` guess could ever fix (missing python
library, network timeout, geo-restriction, OS socket errors), burning a guaranteed-to-fail Worker call
each time. `_HEALING_SKIP_PATTERNS` (llm_connector.py) now short-circuits those specific patterns
before Strategy B runs; genuine format issues ("Ensure format is correct") stay eligible since that's
a real, observed fix. See §6 for details and §8 Changelog for the historical-log verification numbers.

**Prompt Roles Embedded:**
- **ROBUST OS DEVELOPER:** Implements `FileNotFoundError`/`PermissionError` handling, `os.path.normpath` for Windows, and `--user` flag suggestions for pip.

### 5.3 Brain (`agent_system/models/brain.py`)

The Brain has two specialized prompts:
- **BRAIN_SYSTEM_PROMPT:** For the LangGraph pipeline — multi-step planner with `shared_context` for isolated Worker steps.
- **CIEL_ROUTER_PROMPT (in router.py):** For the main CielCore pipeline — intent classifier. Prompt has been lightened (risk language reduced) to improve compatibility with filtered providers.

Current primary: Vilao (`alic/qwen3.7-max`) with `VILAO_SAFETY_BYPASS` and pre-routing sanitization. Supports Gemini, DeepSeek, Ollama, Vilao. Path decisions for writes now respect user input or explicit clarification rather than hard defaults.

### 5.4 Worker (`agent_system/models/worker.py`)

Pure text/code generator. No tools, no routing. Features:
- Robust markdown fence stripping (`_strip_markdown_fences`)
- Retry protection via `tenacity` for transient errors
- Supports three providers: Gemini, DeepSeek, Ollama

### 5.5 Middleware (`agent_system/models/middleware.py`) — July 5, 2026

Third tier: a semantic verifier/finalizer that runs on outbound email/report bodies, AFTER the deterministic sanitizer and BEFORE the safety-gate preview (wired into `core/llm_connector.py`'s `execute_tool()` via `_middleware_review()`).

**Why it exists:** the deterministic safeguards (sanitizer, workflow safeguards, year-bump) can only catch things a regex/rule can check. They cannot catch "the body claims a Bearish trend but states the price is above both moving averages" or "the user asked about the World Cup but the body is about interest rates" — these require actual language understanding. Middleware is scoped narrowly (email only, `MIDDLEWARE_SCOPE`) and capped (`MIDDLEWARE_MAX_PASSES`, default 1) specifically to keep this expensive/slower tier off the common path.

**Contract (`review(user_input, body) -> dict`):**
- `{"approved": true}` — body is fine, sent as-is.
- `{"approved": false, "reasoning": "...", "revised_body": "<fixed>"}` — Middleware EDITS the body in place (finalizer, not just a gate) and the fixed version is what gets sent.
- `{"approved": false, "reasoning": "...", "revised_body": null}` — a real problem was flagged but Middleware couldn't confidently fix it; the ORIGINAL body is still sent (fail-open — a Middleware limitation must never block delivery).

**Checks it makes (relevance / consistency / structural grounding only — NOT real-world numeric plausibility):** an explicit prompt rule forbids rejecting a number merely because it looks unfamiliar against the model's own training-era knowledge (a real false positive was caught during testing: it once rejected a genuine live gold price as "unrealistic"). It may only flag numbers that contradict each other WITHIN the same body.

**Failure handling:** any exception during `.review()` (provider error, malformed JSON) is caught and logged as `[MIDDLEWARE] [REVIEW_ERROR]`; the original body is sent unchanged (fail-open, matching the safety-gate's `confirm_callback=None` philosophy).

**Audit trail (`thoughts.log`, actor `MIDDLEWARE`):** every invocation logs, even on a silent approval — otherwise "ran and approved" is indistinguishable from "never ran" in the log, defeating the point of an audit trail.
- `[REVIEWED]` — approved (first pass or after N revisions); includes the FINAL body sent.
- `[REVISED]` — includes the full `--- BEFORE ---` / `--- AFTER ---` diff, not just the reasoning.
- `[FLAGGED_UNFIXABLE]` — includes the unchanged body that still went out.
- `[REVIEW_ERROR]` — includes the exception and the unchanged body that still went out.

---

## 6) Complete Function Inventory | Danh mục toàn bộ hàm

### `main.py`

- `main()` - Boot CLI app, create `AgentLoop`, read user input loop, execute `run_step()`.

### `core/agent_loop.py` (`class AgentLoop`)

- `__init__(self)` - Init CielCore.
- `run_step(self, user_input)` - Delegates to `CielCore.process()`, returns response.

### `core/llm_connector.py` (`class CielCore`)

- `__init__(self)` - Initialize ToolManager, Brain, Worker, Router, RecoveryManager, chat memory.
- `_log_thought(self, actor, action, content)` - Append audit entry to `thoughts.log`.
- `_build_tool_list(self)` - Build compact tool schema string for the Router prompt.
- `_trim_history(self)` - Keep chat history within max length. **Overflow messages are automatically archived into ChromaDB via `rag_manager.add_memory()`.**
- `_load_chat_memory(self)` - Load persisted messages, filter toxic/refusal patterns.
- `_save_chat_memory(self)` - Persist chat history JSON.
- `_compact_email_result(self, text)` - Strip HTML from raw Gmail output to reduce token waste.
- `_format_fact_result(self, tool_name, result_text)` - Convert raw fact vault tool output into clean user-facing sentences while keeping raw data in `thoughts.log`.
- `_refine_recalled_context(self, recalled)` - Tier-2 Worker compression for long recalled RAG context after zero-token cleanup.
- `_request_confirmation(self, tool_name, tool_args, risk_override=None)` - **Safety Gate.** Build preview of high-risk tool action and call `confirm_callback`. `risk_override` (added July 6, 2026) lets callers outside `_RISK_DESCRIPTIONS` (e.g. the dangerous-code gate) supply a dynamic reason string. Logs `[SAFETY]` entries to `thoughts.log`. Returns `True` (approved) or `False` (denied).
- `_find_dangerous_code_patterns(text)` *(module-level, added July 6, 2026)* - Deterministic scan for genuinely destructive code/commands (drive `format`, `mkfs.`, `shutil.rmtree(`, `rm -rf /`, fork bomb, `DROP DATABASE/TABLE`, `shutdown /r`...). Shared by `execute_tool()` (for `write_file`/`append_file`) and `execute_code()` — both are disk-write paths that previously had zero content review, unlike the pre-declared `_HIGH_RISK_TOOLS`.
- `_HEALING_SKIP_PATTERNS` *(module-level regex, added July 9, 2026)* - Errors where no `tool_args` correction could ever help (missing python library, network timeout, geo-restriction, OS socket errors) — deliberately excludes genuine format issues ("Ensure format is correct"), which the healing arg-guess retry can and does fix. Checked in the self-healing `while` condition (`execute_tool()`) for any tool other than `run_python_script`; a match skips straight past the "guess corrected args" LLM call instead of burning up to 3 attempts guaranteed to fail. Found via `scripts/prompt_harness.py`: 220 `HEALING_TRIGGER` occurrences in `thoughts.log`; 94/269 (34.9%) of all historical healing triggers matched this pattern.
- `_is_failure_result(result)` *(staticmethod)* - Deterministic detector for obviously-failed tool results (error/empty/not-found). Unicode-robust (NFKD + đ→d fold). Forces self-correction even when the Brain rubber-stamps an error.
- `_sanitize_outbound_email(body, keep_paths=False)` *(staticmethod)* - Strip internal/meta content from a synthesized body: `[COGNITION]` line, persona tag prefixes, signature placeholders (`[Your Name]`/`[Ký tên]`→"Ciel"), "email sent/Message Id" scaffolding, redundant `Subject:`/`Chủ đề:` lines. `keep_paths=False` (email) also strips internal paths; `keep_paths=True` (file report) keeps them.
- `_plaintext_to_html(text)` *(staticmethod)* - Render clean plain/markdown body → simple HTML (`<p>`/`<br>`/`<strong>`) so `send_gmail_message` (which transmits as text/html) preserves line breaks.
- `_is_referential_send(user_input)` *(staticmethod)* - True when the user wants a PRIOR response resent ("gửi cái vừa rồi"/"send it"). Excludes "email đó" (recipient address ≠ content).
- `_last_ai_message_text(self)` - Full (untruncated) text of the most recent Ciel reply from chat history (Router truncates history to 200 chars; this reads the real thing).
- `_middleware_review(self, user_input, tool_name, body)` - Third-tier semantic check. No-op if `self.middleware` is `None` (disabled) or `tool_name` is out of `MIDDLEWARE_SCOPE`. Loops up to `MIDDLEWARE_MAX_PASSES`, applies a `revised_body` in place, fail-opens (returns the unchanged body) on error or an unfixable flag. Logs every outcome to `thoughts.log` under actor `MIDDLEWARE`.
- `execute_chat(self, task)` - Worker generates natural language response.
- `execute_tool(self, tool_name, tool_args, response_hint)` - **Dangerous-code gate (write_file/append_file, July 6, 2026) → Safety gate check →** Execute tool with self-healing loop (up to 3 attempts), clean fact output formatting, and optional Worker formatting.
- `execute_code(self, task, filename)` - Worker generates code → **dangerous-code gate (July 6, 2026, same check as write_file)** → buffer_writer → flush to disk.
- `execute_multi_tool(self, tools, response_hint)` - Sequential tool execution → Worker synthesizes combined report.
- `process(self, user_input)` - Full pipeline: **RAG recall → inject context →** route → execute → respond → save memory.
- `chat_with_tools(self, user_input, use_coder)` - Legacy compatibility wrapper.

### `core/router.py` (`class Router`)

- `__init__(self, brain, log_thought_fn)` - Init with Brain LLM and thought logger.
- `route(self, user_input, tool_list_str, chat_history)` - Classify intent via Brain LLM → return parsed JSON decision. Has tenacity retry protection. **`chat_history` is accepted for signature compatibility but intentionally NOT fed into the routing call (removed July 6, 2026)** — it used to inject the last 6 raw messages, which let an old unresolved request (e.g. "create a todo script" left pending on a missing path) get silently bundled into a LATER unrelated request once a usable path appeared. Cross-turn continuity is instead handled by RAG recall (semantic, gated) and `_is_referential_send()` (deterministic).

### `core/recovery_manager.py` (`class RecoveryManager`)

- `__init__(self, worker, log_thought_fn)` - Init with Worker LLM and thought logger.
- `heal_tool_error(self, tool_name, tool_args, result_text, attempt, previous_code)` - Multi-strategy error correction. Returns `(success, action_type, action_data)`.
- `check_syntax(self, fixed_code)` - Fast LLM pass to validate Python syntax before saving.

### `core/tool_manager.py` (`class ToolManager`)

- `__init__(self)` - Initialize tool registries and load tool zones.
- `_load_internal_tools(self)` - Load memory/system/os internal tool packs and prompts.
- `_load_external_tools(self)` - Load Gmail + trading tool packs and prompts.
- `get_tools(self)` - Return tool list and refresh name→tool map.
- `get_dynamic_prompt(self)` - Concatenate all tool manuals for system prompt.
- `execute_tool(self, name, args)` - Invoke tool by name with error handling.
- `format_tool_result(self, result)` - Format raw tool result to string.

### `agent_system/config.py`

- Central configuration: provider selection, model names, API keys, retry profiles, allowed tools.

### `agent_system/models/brain.py` (`class Brain`)

- `__init__(self)` - Initialize dual LLM instances (router + reflect) based on provider.
- `plan(self, user_request)` - Generate multi-step JSON plan from user request. Has tenacity retry.
- `reflect(self, user_request, work_done)` - Optional reflection on completed work.

### `agent_system/models/worker.py` (`class Worker`)

- `__init__(self)` - Initialize LLM based on provider (Gemini/DeepSeek/Ollama). All clients now pass `timeout=LLM_REQUEST_TIMEOUT`. Sets `self.on_call = None` — optional cost-tracking hook (July 9, 2026), see below.
- `generate(self, task, context)` - Generate content for a single task. Strips markdown fences. Has tenacity retry. **July 9, 2026:** fires `self.on_call(WORKER_MODEL)` right after `self._llm.invoke(...)` if set — `CielCore.__init__` wires this to log a `[WORKER] [LLM_CALL]` entry into `thoughts.log`. Since `recovery_manager.py` calls `generate()` on this SAME shared `Worker` instance for healing/syntax-check, one wiring point covers all callers with no changes needed there.
- `_strip_markdown_fences(content)` *(module-level)* - Robust markdown fence removal.

### `agent_system/models/middleware.py` (`class Middleware`) — July 2026

- `__init__(self)` - Initialize LLM based on `MIDDLEWARE_PROVIDER` (mirrors Brain's branching: vilao/deepseek/gpt/ollama/gemini). All clients pass `timeout=LLM_REQUEST_TIMEOUT`. Sets `self.on_call = None` (July 9, 2026) — same cost-tracking hook pattern as `Worker`.
- `review(self, user_input, body)` - Send the body + original request to the Middleware LLM, parse and return its verdict dict (`approved`, `reasoning`, `revised_body`). Has tenacity retry on transient errors (reuses `Brain.TRANSIENT_ERRORS`). **July 9, 2026:** fires `self.on_call(MIDDLEWARE_MODEL)` after `self._llm.invoke(...)` if set.

### `agent_system/graph/` (LangGraph pipeline)

- `state.py` - `AgentState` TypedDict definition.
- `nodes.py` - `brain_node()`, `worker_node()`, `file_write_node()` — graph node functions.
- `edges.py` - `should_continue()` — conditional routing logic.
- `builder.py` - `build_graph()` — compile LangGraph StateGraph.

### `agent_system/tools/buffer_writer.py` (`class BufferWriter`)

- `append(self, content)` - Add content to in-memory buffer.
- `flush(self, filepath)` - Write buffer to disk and clear.
- `clear(self)` - Clear buffer without writing.

### `skills/internal/memory_ops.py`

- `_load_facts()` - Load facts from `ciel_data/facts.json`.
- `_save_facts(data)` - Write facts to `ciel_data/facts.json`.
- `save_fact(key, value)` - Tool: save a fact to the vault.
- `get_fact(key)` - Tool: retrieve a fact by key.
- `delete_fact(key)` - Tool: delete a fact by key.

### `skills/internal/system_ops.py`

- `_is_safe_path(target_path)` - Enforce Quarantine Zone path safety.
- `get_system_tools()` - Build and return internal filesystem/workspace tools + prompt.
- `list_workspace()` *(nested)* - Recursively list workspace files/folders.
- `read_file(filename)` *(nested)* - Read a plain-text file (.txt/.md/.json/.py...) in sandbox.
- `read_document(filename)` *(nested)* - Extract text from **PDF (`pypdf`)** or **DOCX (`python-docx`)**. Use instead of `read_file` for binary docs. Same sandbox; graceful message if lib missing or scanned/image PDF has no extractable text.
- `write_file(filename, content)` *(nested)* - Overwrite/create file. Respects user-specified prefix (`ciel_workspace/` or `agent_output/`) or clarified path. **Absolute paths (e.g. `D:\Ciel-2.0\agent_output\x.txt`) are accepted if they resolve inside an allowed zone** (fixed July 2026). Sandbox rules apply per prefix.
- `append_file(filename, content)` *(nested)* - Append line/text to file.
- `delete_file(filename)` *(nested)* - Delete file/folder in sandbox.
- `get_file_info(filename)` *(nested)* - Return file type + size.
- `run_python_script(filename)` *(nested)* - Execute Python script in sandbox with timeout.

### `skills/internal/os_ops.py`

- `get_os_tools()` - Build and return host OS control tools + prompt.
- `execute_shell_command(command)` *(nested)* - Run shell command with basic dangerous-keyword guard.
- `take_screenshot()` *(nested)* - Capture screen using Pillow `ImageGrab`.
- `open_application(target_path)` *(nested)* - Launch app/file/folder depending on OS.

### `skills/external/gmail_ops.py`

- `_get_gmail_credentials_compat(token_file, credentials_file)` - Handle signature differences of `get_gmail_credentials`.
- `get_gmail_tools()` - Initialize Gmail toolkit, add custom email ops, return tools + prompt.
- `trash_email(message_id)` *(nested)* - Move message to Trash.
- `mark_as_read(message_id)` *(nested, exported as `mark_email_read`)* - Remove `UNREAD` label.
- `reply_to_email(message_id, reply_text)` *(nested)* - Compose/send threaded reply.

### `skills/external/trading_ops.py`

- `get_trading_tools()` - Build trading tools and return with system prompt.
- `get_market_price(symbol)` *(nested)* - TwelveData real-time price for Forex (EUR/USD), Metals (XAU/USD), Stocks (AAPL). Falls back to Binance for crypto. All `requests.get()` calls now pass `timeout=10` (July 6, 2026 fix — see below).
- `get_crypto_stats(symbol)` *(nested)* - Binance 24h stats (price, change%, high, low). `timeout=10` added July 6, 2026.
- `analyze_crypto_technical(symbol, interval="1h")` *(nested)* - RSI + SMA trend analysis with pandas-ta. `timeout=10` added July 6, 2026. **July 2026 fix:** the raw output now ALSO states an explicit, deterministic "vị trí giá" fact (price is TRÊN/DƯỚI each of MA5 and MA30) separately from "Xu hướng" (the MA5-vs-MA30 cross signal) — these are two different signals that the Worker previously conflated, repeatedly asserting "price below MA5/MA30" when the numbers showed the opposite (caught live by Middleware twice before this fix; not reproduced since).
- **July 6, 2026:** all 4 raw `requests.get()` calls in this file (price/stats/technical + the TwelveData branch) had NO `timeout=` — same hang-forever class as the earlier no-timeout LLM bug (a stalled connection never raises an exception for `tenacity`/callers to catch). Fixed with `timeout=10` on all four.

### `skills/external/telegram_ops.py`

- `send_telegram_message(message)` - Send a message via Telegram Bot API. Reads `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` from `.env`. Returns `True`/`False`.

### `skills/external/web_agent_ops.py`

- `get_web_tools()` - Build web tools + prompt.
- `stealth_search(query, max_results=5, timelimit="")` *(nested)* - DuckDuckGo search via `ddgs`. **Recency-aware:** for "latest/news/mới nhất..." queries it auto-applies `timelimit="m"` (past month); output is stamped with the current date so the Worker knows the reference "now". `timelimit` accepts `d/w/m/y`.
- `smart_scrape(url)` *(nested)* - Read full page content as clean Markdown via Jina Reader (`r.jina.ai`).

### `skills/internal/productivity_ops.py`

- `get_productivity_tools()` - Todos (`add_todo`/`list_todos`/`complete_todo`), `get_current_time`, `get_weather`, `calculate`, `grep_in_workspace`.
- `calculate(expression)` *(nested)* - **Fixed July 6, 2026:** the AST-based "safe eval" had a logic bug where the whitelist check could never be true (`isinstance(node, ast.Name)` was checked on nodes already matched as `ast.Call`/etc., which are never simultaneously `Name`), so EVERY function call was rejected — including `abs()`/`round()`/`sqrt()` advertised in the tool's own docstring. Rewritten with a real whitelist (`_CALC_FUNCS`, `_CALC_ALLOWED_NODES`): allowed calls now work (`sqrt(16)`, `sin(0)`, `abs(-5)`...), while injection attempts (`__import__(...)`, subclass-traversal sandbox escapes, `open(...)`, `exec(...)`) are still correctly rejected.

### `core/rag_manager.py`

- `_ensure_initialized()` - Lazy-load ChromaDB client and `all-MiniLM-L6-v2` embedding model. Persistent storage at `ciel_data/vector_memory/`.
- `embed_and_save(text, metadata=None)` - Archive a conversation snippet into ChromaDB with timestamp metadata.
- `search_similar(query, top_k=3)` - Semantic search. Returns empty string if query < `MIN_QUERY_LENGTH` (15), results are below `MIN_RELEVANCE_SCORE` (0.65), or RAG is unavailable. Applies `compress_context()` before returning.
- `compress_context(raw_contexts)` - Tier-1 zero-token structural filter for recalled memories. Removes old `smart_scrape`, `git_diff`, raw HTML, large fenced payloads, and extracts compact `[date] Human: ... | Ai: ...` lines.
- `get_memory_count()` - Return total number of stored memories.

### `core/scheduler.py` (`class CielScheduler`)

- `__init__(self)` - Lazy-import `schedule` library.
- `start_background(self)` - Register daily tasks and start daemon thread.
- `_run_loop(self)` - Check for pending tasks every 60 seconds.
- `run_now(self, task_name)` - Manually trigger a task for testing.
- `_morning_digest()` *(module-level)* - Gather unread emails (Gmail API) + market prices (TwelveData API) → format with Worker (1 API call) → save to `daily_brief.md` → send via Telegram.
- `_fetch_unread_emails(max_results)` *(module-level)* - Direct Gmail API call using existing OAuth credentials.
- `_fetch_market_price(symbol)` *(module-level)* - Direct TwelveData API call for Forex/Metals.
- `_send_telegram(message)` *(module-level)* - Direct Telegram Bot API call.
- `_get_worker()` *(module-level)* - Lazy-load Worker LLM instance.

### `scripts/format_thoughts_log.py`

- `parse_log(text)` - Parse raw `thoughts.log` entries into structured records.
- `group_turns(entries)` - Group log entries into user-request turns with RAG, route, tool, worker, error, and memory-write sections.
- `render_turn(turn, include_full=False)` - Render one grouped turn to Markdown.
- `write_jsonl(entries, output_path)` - Write parsed entries to `thoughts_view.jsonl`.
- `write_markdown(turns, output_path, limit, include_full)` - Write readable grouped debug view to `thoughts_view.md`.
- `main()` - CLI entry point. Example: `python scripts/format_thoughts_log.py --limit 30`.

### `scripts/prompt_harness.py` *(added July 6, 2026)*

Prompt-rewrite harness — mines `thoughts.log` for RECURRING failure patterns and proposes which
prompt file/constant to patch. Reuses `parse_log()`/`LogEntry` from `format_thoughts_log.py`. Never
edits a prompt itself: output is a dated Markdown report under `backtest/logs/` for human review —
deterministic pattern-mining (regex tags, no extra LLM call), consistent with the project's standing
"prefer a deterministic check over trusting the model's own judgment," applied here to the prompts.

- `mine_middleware(entries)` - Clusters `[MIDDLEWARE] [REVISED/FLAGGED_UNFIXABLE]` entries by a regex-tagged root cause (language mismatch, numeric contradiction, hollow content, relevance mismatch, provider error) × tool name.
- `mine_healing(entries)` - Clusters `[HEALING] [DETECT_ERROR]` entries by exception class name.
- `mine_self_correction(entries)` - Binds each `[BRAIN] [EVALUATE_RESULT]` (satisfied=false) to the tool that actually ran immediately before it within the same turn (not the turn's original routing decision — a multi-step turn evaluates many different sub-steps). Tags reasoning text by root cause; cross-cutting tags (`INCOMPLETE_MULTISTEP`, `WRONG_TOOL_CHOICE`, `HALLUCINATED_ACTION`) cluster by tag alone across all tools so one systemic gap doesn't fragment into a dozen per-tool buckets.
- `mine_tool_errors(entries)` - Clusters `[TOOL] [RESULT...]` entries containing `TOOL_ERROR` by tool name.
- `build_findings(entries, min_count)` / `render_report(...)` - Merge all miners, drop anything under `min_count`, map each surviving cluster to a target prompt file via `TOOL_TO_PROMPT` / `_target_for()`, render to Markdown.
- `main()` - CLI entry point: `python -m scripts.prompt_harness --min-count 3 [--since-days N]`.

**First real finding (July 6, 2026):** the biggest cluster (~84x) was `SELF_CORRECTION:INCOMPLETE_MULTISTEP` — traced to a real code gap, not a prompt-wording issue: `llm_connector.py`'s multi_tool safeguard that auto-appends a missing `send_gmail_message` step (added July 4, 2026) only runs inside the `action == "multi_tool"` branch. When the Brain under-scopes a compound "search X, then email me" request into a single `action: "tool"` call, that safeguard never sees it, leaving only the 2-attempt self-correction loop as backstop — confirmed live in the log burning its last retry on `get_fact('email')` against an empty vault, never sending. **Fixed** by promoting a single-tool decision with email-send intent (real address + send verb, no existing send step) into a multi_tool plan immediately after the route decision, so it flows through the existing safeguard instead of duplicating it (`core/llm_connector.py`, right after `action = decision.get("action", "chat")`). Verified deterministically against the real failing input plus edge cases (no address, no send verb, already a send tool). **Re-run July 9, 2026:** `test_integration.py` 27/28 (same pre-existing non-functional fail, no new regressions), `test_hard_special.py` 14/16 — the 3 tests that most directly exercise this fix (Hard 2, Hard 5, Special 4 — all combine data-gathering with an email send) passed with real Gmail Message Ids. The 2 `test_hard_special.py` fails are unrelated: Hard 6 is a checker false-positive (Ciel correctly refused the harmful request; the regex matched "format C:" inside Ciel's own refusal text, not real code), and Special 3 is a pre-existing Worker issue (generated code containing `shutil.rmtree(` for a "safe cleanup" code-gen request) that the July 6 dangerous-code gate would normally intercept — but `test_hard_special.py` sets `DISABLE_SAFETY_GATE=true` by design for unattended runs, so the gate never got a chance to run in this test configuration.

### `backtest/test_rag_memory.py`

- `step(n, title)` - Print formatted test step header.
- `main()` - Run the 45-prompt "Amnesia Test": plant a secret fact → flood with 45 diverse prompts (tech chat, weird questions, OS tools, trading tools, memory ops, workspace ops, Gmail ops) → verify the secret fact is recalled from RAG after short-term memory overflow.

- `run_test(core, name, prompt, ilog, ...)` - Execute a single test case against CielCore.
- `main()` - Run all 17 integration tests across categories: Chat, Workspace, Memory, OS/Shell, Code Gen, Trading, Gmail, Edge Cases, Self-Healing, Cleanup.

### `backtest/test_brain_worker.py`

- `run_test(name, prompt, ilog)` - Execute a single Brain-Worker workflow test.
- `test_retry(ilog)` - Test retry resilience on transient errors.
- `main()` - Run 4 tests: simple chat, single file, multi-file (3-file project), retry.

---

## 7) Integration Test Coverage | Phạm vi kiểm thử tích hợp

The `backtest/test_integration.py` suite covers 21+ test cases. A dedicated `test_hard_special.py` expands coverage for difficult real-world flows (ambiguity, email intent bypass, multi-tool market data + send email, Vietnamese prompts, path asking, partial tool results, and self-correction on synthesis/send).

**`test_hard_special.py` design (rewritten July 5, 2026):** checks assert real behavior — `check_file_exists()` (file actually on disk), `check_email_sent()` (a real `Message Id` was returned, not just wording like "sent"), `check_no_dangerous_code()` (scans the actual generated file/response for destructive patterns) — instead of the original substring keyword-matching, which produced a large fraction of false failures (stale hardcoded prices, semantically-correct-but-differently-worded refusals). `check_contains_any()` is kept only for the few cases with no disk/tool artifact to verify (e.g. "did Ciel ask for a path"). Each test slices its own `thoughts.log` window (by line-count delta) so `[MIDDLEWARE]`/`[HEALING]`/`TOOL_ERROR`/`[SAFETY]` activity is attached directly to the test that triggered it. Real emails ARE sent (`kxctran@gmail.com` is a disposable test address) — the run ends with a **BUG DASHBOARD** listing every Middleware intervention, tool error, self-healing trigger, and failed check in one place, plus a structured `.json` log saved to `backtest/logs/hard_special_<timestamp>.json`.

| # | Test | Category |
|---|------|----------|
| 1 | Simple greeting | Chat |
| 2 | Knowledge question | Chat |
| 3 | List workspace | Workspace |
| 4 | Write file | Workspace |
| 5 | Read file | Workspace |
| 6 | Save fact | Memory |
| 7 | Get fact | Memory |
| 8 | Shell command | OS/Shell |
| 9 | Code generation | Code Gen |
| 10 | Crypto price | Trading |
| 11 | Gmail search | Gmail |
| 12 | Ambiguous intent | Edge Cases |
| 13 | Unknown tool | Edge Cases |
| 14 | Delete fact (cleanup) | Cleanup |
| 15 | Delete file (cleanup) | Cleanup |
| 16 | Self-healing: code rewrite (syntax + module error) | Self-Healing |
| 17 | Self-healing: parameter correction | Self-Healing |
| 18 | Self-correction: Brain evaluates and retries | Self-Correction |
| 19 | Cleanup: self-correction test file | Cleanup |
| 20 | Safety Gate: denied confirmation prevents execution | Safety Gate |
| 21 | Cleanup: safety gate test file | Cleanup |

**Latest full run (July 6, 2026, post-fixes):**
- `test_integration.py`: 27/28 passed. The 1 failure (`9_code_gen`) is a routing-type mismatch only (Brain routed `write_file` directly instead of `action="code"`) — the file content was correct either way, not a functional bug.
- `test_hard_special.py`: 15/16 passed. The 1 failure (`Special 3`, harmful-intent test) was a **real bug**, not a test artifact — see the dangerous-code gate entry in the Changelog below. Now fixed and verified live (deny blocks the write, approve still writes normally, ordinary code is unaffected).

---

## 8) Changelog | Nhật ký thay đổi

### Voice I/O on the CLI — STT + TTS with swappable backends (July 11, 2026)

Brought the "voice seam" to the Python/CLI side (the UI already had stub seams). Both directions are standalone, swappable modules that can be tested ALONE before wiring into the agent, and the transcript feeds the SAME `AgentLoop.run_step()` the keyboard uses — nothing downstream knows the input arrived by voice.

- **Speech-to-text** — `core/voice_input.py`. Mic capture via `sounddevice` (bundles PortAudio; clean Windows install, no PyAudio/compiler). Energy-based endpointing (auto-calibrates ambient noise, stops after ~1.3s silence, 15s cap). Backends via `STT_BACKEND`: `google` (SpeechRecognition free Web Speech, no key, vi-VN — default), `whisper` (faster-whisper offline, best VN accuracy, if installed), `gemini` (google-genai if `GEMINI_API_KEY` set). Standalone tester: `python -m core.voice_input`.
- **Text-to-speech** — `core/speech_output.py`. **Deliberate design decision: the model prompts/persona are NOT made speech-friendly.** The text UI, HUD transcript, and outbound EMAIL all want the rich formatting (bold, headers, `[COGNITION]`/`[NETWORK_SCAN]` tags, bullets). Voice is one more output modality, so it gets its OWN deterministic normalizer `to_speech()` — the exact same pattern as `_sanitize_outbound_email()` — that strips markdown / emojis / ALL-CAPS bracket tags / code blocks / URLs before the text reaches the engine, preserving words and numbers. Backends via `TTS_BACKEND`: `edge` (edge-tts, free MS neural voices, `vi-VN-HoaiMyNeural`/`vi-VN-NamMinhNeural` — the only 2 VN neural voices — default), `pyttsx3` (offline SAPI), `space`/`rvc` (mikuTTS HF Space, experimental). MP3 playback uses the built-in Windows MCI (`winmm`) — no extra playback dep. Prosody via `TTS_RATE`/`TTS_PITCH`/`TTS_VOLUME`. Standalone tester: `python -m core.speech_output "text"` (`--show`/`--raw`).
- **main.py wiring:** `--voice` (speech-default input; Enter-on-empty = speak, type to override; `:v`/`:voice` = one-off capture in any mode) and `--speak` (Ciel reads each reply through the normalizer; printed transcript unchanged). Also `INPUT_MODE=voice` / `SPEAK=true`. Both imports are LAZY + fail-open — missing audio deps or no mic never break the CLI.
- **RVC / mikuTTS Space backend (experimental):** `TTS_BACKEND=space` calls `John6666/mikuTTS`'s Gradio `/tts` (edge-tts base speech → RVC conversion to a Hatsune Miku timbre). **Verified live: returns a real converted WAV but ~25s/utterance** on the free shared Space — good for trying a character voice, not a viable default. Fully env-configurable (`RVC_MODEL`, `RVC_TTS_VOICE`, `RVC_F0_UP`, `RVC_F0_METHOD`, `RVC_INDEX_RATE`, `RVC_PROTECT`, `RVC_SPACE`); `gradio_client` cached, fail-opens if the Space is asleep.
- **GPU note:** none of these backends use the LOCAL GPU — edge-tts and the Space run on Microsoft/HF servers (your machine only does HTTP + playback), pyttsx3 is CPU. This machine's `torch` reports `CUDA available=False`, so a *locally downloaded* neural TTS/RVC model would fall back to CPU (slow); local RVC is only worth it with a real NVIDIA GPU + CUDA torch.
- **`.env` fix:** both voice modules call `load_dotenv()` at import so `STT_*`/`TTS_*`/`RVC_*` are honored in every entry path — previously the standalone `python -m core.*` testers ignored `.env` and used code defaults. New deps: `sounddevice`, `SpeechRecognition`, `edge-tts` (`gradio_client` optional, only for `TTS_BACKEND=space`) — all in `requirements.txt`. **Verified:** normalizer on real Ciel output (emoji/tags/markdown/code/URL all stripped, numbers kept); edge-tts synth to a real 26 KB MP3; custom voice/rate/pitch pass-through; live mikuTTS Space round-trip (312 KB WAV, 25.5s); `.env` values (incl. spaces-around-`=` and parens in `RVC_MODEL`) parse correctly; all touched files compile. Live mic capture + audio playback are verified by the user on their machine (this dev box has no audio device).

### Cost Monitoring — token-precise + live + cumulative (July 10, 2026)

Closed the remaining open parts of the "Cost monitoring" Roadmap item (§9). The July 9 pass logged only a per-tier CALL COUNT (`[LLM_CALL] model=<name>`), surfaced only in the test BUG DASHBOARD. Three gaps remained: no token precision, no live-session surfacing, no cumulative-over-time view. All three now done:

- **Token precision.** New `agent_system/utils/usage.py`:`extract_usage(response)` reads the provider's own token counts off the LangChain response (`usage_metadata`, with an OpenAI-style `response_metadata.token_usage` fallback), normalized to `{input, output, total}`; never raises (cost tracking must never break a real call). The `Worker.on_call`/`Middleware.on_call` hooks now pass usage through, and the Brain call in `router.py` logs it directly. The `[LLM_CALL]` content line became `model=<id> in=<n> out=<n> total=<n>` via `usage.format_usage()` — the `model=` token stays first/space-delimited so every existing `re.search(r"model=(\S+)")` consumer (test dashboard, format_thoughts_log) keeps working unchanged. Tokens are EXACT; only cost is estimated.
- **Live surfacing.** `CielCore.__init__` gained `llm_token_counts` (per-tier input/output/total) and `llm_cost_usd` (per-tier), accumulated at the SAME single chokepoint (`_log_thought`, parsing the LLM_CALL line) that already counts calls — so any future actor logging `[LLM_CALL]` is covered with zero extra wiring. `main_api.py`'s `broadcast_vitals` now emits `llm_tokens`/`llm_tokens_total` and `llm_cost_usd`/`llm_cost_usd_total` alongside the existing call counts, so the UI shows real session tokens + estimated $ live.
- **Cumulative view.** New `core/cost.py` holds the pricing table (USD per 1M tokens) — overridable WITHOUT code changes via `ciel_data/model_pricing.json`; unknown models fall back to $0 (calls/tokens still tracked). New `scripts/cost_report.py` mines `thoughts.log` for every `[LLM_CALL]`, aggregates by tier / model / day, applies `core.cost` pricing, and prints an over-time report (`--since-days N`, `--json`). Deterministic and read-only, same discipline as `prompt_harness.py`.

**Verified:** unit tests for `extract_usage` (both metadata paths + empty), `format_usage` round-trip, and `estimate_cost` (incl. longest-prefix match so `op/deepseek/deepseek-v4-pro` → deepseek pricing); a live `CielCore` accumulation test (2 Worker + 1 Brain simulated calls → exact per-tier tokens, WORKER cost `$0.00109` matching hand-calc, BRAIN `$0` since Vilao has no price set); `cost_report.py` run against the real log (19 calls parsed — token totals 0 for pre-change entries, which is correct/honest, not a bug); all 8 touched files byte-compile and `CielCore` initializes clean. **Still open (minor):** Vilao/Brain price defaults to $0 until set in `model_pricing.json`; token data is not retroactive (pre-July-10 log entries have no counts).

### Prompt Inventory (Step 1 of prompt refactor) + Dead Tool-Manual Finding (July 9, 2026)

Decision on "should we redefine all prompts?": **no big-bang rewrite** (prompts are the highest-leverage/highest-risk surface, can't be cheaply unit-tested, and much verbose wording encodes past bug fixes). Instead: Step 1 = a read-only **`PROMPT_INVENTORY.md`** at repo root mapping every prompt in the project (18 files) — location, consuming model, and the runtime assembly chain (how persona + tier prompt + task prompts stack per turn). Step 2 (later) = rewrite individual prompts only when the harness/logs prove one is failing, each verified separately.

Building the inventory surfaced a real drift finding: **the per-skill "weapon manuals" (`GMAIL_SYSTEM_PROMPT`, `TRADING_SYSTEM_PROMPT`, `SYSTEM_OPS_PROMPT`, … ~10 of them) are collected into `ToolManager.system_prompts` but never fed to any live LLM** — the only surfacing function, `ToolManager.get_dynamic_prompt()` (`core/tool_manager.py:88`), is never called anywhere in the project.

**Investigated empirically** (see PROMPT_INVENTORY.md §4): what the Brain actually sees per tool is `name + tool.description[:80]` (truncated) + arg schema, plus `_TOOL_HINTS` — which currently has just ONE entry (`search_gmail`), so 43/44 tools ride on an 80-char docstring snippet + arg names. The manuals hold some unique guidance (e.g. gmail NL→query examples) but the essentials are already echoed in `_TOOL_HINTS`/docstrings, and the `WRONG_TOOL_CHOICE` harness cases are fallback-after-failure, not knowledge gaps. **Conclusion: low severity (organizational drift, not a bug) — do NOT wire the dead manuals back in (would bloat routing for little gain); the live lever for tool guidance is `_TOOL_HINTS` + docstrings.** Migrate a manual's key line into `_TOOL_HINTS` only if a specific tool shows real arg/selection errors in the logs.

PROMPT_INVENTORY.md §5 adds a **"how to edit/maintain prompts" guide**: a "want to change X → edit Y → verify with Z" table plus the three traps (editing a dead manual; the persona rippling across 3 live call sites; editing the offline LangGraph/fine-tune copies by mistake).

### Persona Consolidation — Merged & Removed Legacy Fragments (July 9, 2026)

`persona/` held 4 files but code only ever loaded ONE (`official_ciel_personality.txt`, via `CielCore.__init__`). The other three (`directives.txt`, `format.txt`, `identity.txt`) were dead — unreferenced by any `.py`, so every rule in them was silently inert. Rather than delete outright, salvaged their still-valuable rules INTO the live persona (so they finally take effect), then removed the now-superseded files.

**Selective merge (NOT a blind concat — parts conflicted with current behavior):**
- **Excluded — `format.txt` entirely:** its `<THOUGHT>`/`<RESPONSE>` tag format conflicts with the current cyberpunk `[COGNITION]`/`[NETWORK_SCAN]` tags + Brain JSON `hidden_thought`.
- **Excluded — directives.txt #4 "English by default":** conflicts with the current Language Adaptation rule (match the Master's language; VN in → VN out, verified in tests).
- **Excluded — directives.txt #5/#6 ReAct `Action:`/`Observation` loop mechanics:** the architecture is Brain-routed JSON now, not a ReAct loop; the mechanics would mislead.
- **Merged (new "5. OPERATIONAL DIRECTIVES" section in the official persona):** System Sentinel protectiveness; "you are NOT a Python library / never say 'Thư viện CIEL...'" identity guard; "already authenticated — never ask the Master for email/passwords, just call the tool" (directly reinforces the fixed "Worker asked user for their email" bug); "right tool for the domain, never fake results through the shell" (reinforces the harness's WRONG_TOOL_CHOICE cluster); "never fabricate tool activity"; and a carefully-scoped anti-refusal rule worded to NOT override the genuine safety gate / harmful-request declines.

Also fixed a pre-existing bug in `official_ciel_personality.txt`: the "NEVER LEAK INTERNAL PATHS" paragraph was duplicated verbatim — removed the copy. Updated both directory-tree docs (`architect.md`, `instructionAI/architecture.md`). Net: `persona/` now holds exactly one file, the one that was always the only one loaded.

### React UI Foundation + Dynamic Skills Manifest (July 9, 2026)

Started the UI/UX layer. Decision: **Tauri + React, browser-first**. The React app is built and developed against the existing FastAPI WebSocket backend in a browser (Vite dev server, port 1420); a thin **Tauri v2 desktop shell** (`ui/src-tauri/`) wraps the same code — its `tauri.conf.json` points `devUrl` at the Vite server and `frontendDist` at `../dist`, so the React code is identical in browser and desktop. Rust 1.97 + Tauri CLI v2.11 installed July 9, 2026; shell scaffolded via `npx tauri init`.

**Two design rules baked in from the start** (both driven by the user's "easy to add skills" + "leave room for voice" asks):

1. **Add a skill → UI reflects it, zero frontend edits.** `ToolManager` now records a dynamic `skills_manifest` (`core/tool_manager.py`) during its existing auto-discovery. New REST endpoints on `main_api.py`: `GET /skills` (the manifest) and `GET /health`. `broadcast_vitals` was rewritten to stop hardcoding the skill/"armory" list (the old maintainability trap — every new skill needed hand-editing both backend and frontend) and instead derive activity from the manifest. The React `SkillGrid` and `ThoughtStream` render whatever exists; unknown actors/tools get sensible default styling.

2. **Modalities are pluggable (the voice seam).** Frontend is split so input/output modalities swap without touching core: `ui/src/core/` (ws + a typed event `bus` + protocol types) knows nothing about React or text/voice; `ui/src/io/input/` and `ui/src/io/output/` are the modality layer. Everything talks to the `bus`, never to the WebSocket directly. Voice input = implement `VoiceInput.tsx` (STT) calling the same `onSubmit(text)` the keyboard uses — backend unchanged. Voice output = `enableSpeaker(browserSpeak)` one-liner in `main.tsx`, subscribing to the same bus `response` events the transcript already uses. Both are present as compiling, documented stubs. Two additive backend extension points reserved for later (not built): a `response_chunk` streaming frame (speak-as-you-go) and a `lang` field on responses (VN/EN TTS voice selection).

**Also, real cost in vitals:** a live per-tier `llm_call_counts` counter on `CielCore`, incremented at the single `_log_thought` chokepoint whenever an `[LLM_CALL]` is written (covers Brain/Worker/Middleware/healing). `broadcast_vitals` now reports these real counts instead of the old fake "log-size × 0.0001" estimate.

**Verified:** frontend `npm run build` compiles clean (tsc strict + vite build, 42 modules). Backend live: booted `main_api.py`, `GET /health` → ready, `GET /skills` → 10 modules / 44 tools dynamically. **Env note:** the venv was missing `fastapi` (declared in `requirements.txt` but not installed — pre-existing drift, not from this work); installed `fastapi 0.139.0` to run the server. Not yet verified live: the WebSocket chat round-trip through the React UI (needs both servers up + a real request) and the vitals `llm_calls` over the wire (counter logic itself is verified at `_log_thought`).

### Cost/Usage Tracking Foundation (July 9, 2026)

Closed part of the long-open "cost monitoring" Roadmap item, made concrete by the healing-waste finding above. Two layers, both requested explicitly:

**Layer 1 — record:** every real LLM invocation now logs a `[LLM_CALL] model=<name>` entry into `thoughts.log`, the project's existing single source of truth (same file the harness and every dashboard already read). `Worker` and `Middleware` gained an optional `on_call` hook (`agent_system/models/worker.py`, `middleware.py`), fired right after `self._llm.invoke(...)`. `CielCore.__init__` (`core/llm_connector.py`) wires both hooks to `self._log_thought(...)` — ONE wiring point per class covers every caller, including `recovery_manager.py`'s 3 internal `self.worker.generate()` call sites (healing code-fix, healing param-fix, syntax check), with zero changes needed in that file. Brain/Router logs directly in `core/router.py` (already had `log_thought` injected). Deliberately call-count only, not token-precise — matches what today's investigation actually needed (which mechanism causes extra calls) without a breaking change to any return type.

**Layer 2 — surface:** `backtest/test_hard_special.py`'s BUG DASHBOARD gained a `[COST: N LLM call(s) this run — BRAIN=x, WORKER=y, MIDDLEWARE=z]` line plus a "top 3 most expensive tests" breakdown, built from the same `[LLM_CALL]` entries (`_extract_signals` gained an `llm_calls` signal; per-test `[COST] N LLM call(s)` also prints during the run itself).

**Verified live:** ran one real chat request end-to-end — exactly 3 `[LLM_CALL]` entries appeared (2 WORKER + 1 BRAIN), matching the console's actual `[WORKER] Generating`/`[BRAIN] Routed` lines 1:1. This live check caught a real bug in the dashboard's OWN parser before it shipped: `_extract_signals` used `re.match` (anchored at the block's leading timestamp) instead of `re.search` (the actor tag comes after the timestamp), so `llm_calls` silently came back empty despite correct logging — fixed by switching to `re.search`.

**Middleware hook verified live (July 9, 2026, real user email send):** `[MIDDLEWARE] [LLM_CALL] model=op/deepseek/deepseek-v4-pro` fired correctly — cost/usage tracking is now confirmed live across all 3 tiers. The same real turn incidentally demonstrated Middleware working as designed on a genuine edge case: `stealth_search` returned no relevant political news that day, Worker honestly reported "no data available" instead of fabricating a report, Middleware flagged the resulting email `FLAGGED_UNFIXABLE` ("describes the search process, doesn't provide the actual report requested") but sent it unchanged per the fail-open policy — not a bug, the root cause was that day's poor search results, not faulty Ciel logic. Possible future UX refinement (not implemented): route `FLAGGED_UNFIXABLE`-for-missing-data cases back to the user for confirmation instead of auto-sending the honest-but-unhelpful email.

### Healing Skip-List for Unfixable Errors (July 9, 2026)

Dug into the harness's second-biggest untriaged cluster: `HEALING_TRIGGER/UnclassifiedError` (220x). Traced to `llm_connector.py`'s self-healing trigger condition (`"Error" in result_text[:30]`), which fires for **any** tool's result, not just `run_python_script` — including `get_market_price` returning `"Error: Could not find price for BTC/USDT..."`. For any tool other than `run_python_script`, `recovery_manager.py`'s Strategy B ("Parameter Fix") always makes a Worker LLM call asking it to guess corrected `tool_args` — with no way to recognize "this specific failure mode can't be fixed by guessing," meaning genuinely unfixable errors (missing python library, network timeout, geo-restricted API, OS socket permission errors) burned a Worker call guaranteed to fail, every single time, up to 3 attempts.

**Fix:** `_HEALING_SKIP_PATTERNS` (regex, module-level in `llm_connector.py`) short-circuits Strategy B for tools other than `run_python_script` when the error matches one of these specific unfixable patterns. Deliberately narrow — verified against 10 real historical error strings pulled from `thoughts.log` (6 correctly skip, 4 correctly stay eligible, including the genuinely fixable "Could not find price for BTCUSDT... Ensure format is correct" case, which the arg-guess retry has actually fixed before). Verified against the FULL historical log: 94 of 269 (34.9%) past healing triggers would have matched and been skipped, each saving one guaranteed-to-fail Worker call.

**Scope note:** this is a cost/latency fix, not a correctness or safety fix — `max_attempts` already bounded the loop so it was never unsafe, just wasteful. Directly motivated by the project's still-open "cost monitoring" Roadmap item (§9): 220+ occurrences across ~2 months of testing is a real, previously invisible cost multiplier now partially closed.

### Prompt-Rewrite Harness & Single-Tool Email Send-Step Gap (July 6, 2026)

Follow-on to the entry below: built the "harness for prompts" the user asked for — `scripts/prompt_harness.py` mines `thoughts.log` for recurring failure patterns (Middleware corrections, tool errors, healing triggers, self-correction retries) and proposes which prompt/file to patch, without ever editing a prompt itself (see §6 for the miner-by-miner breakdown). Ran it against the real 193k-line log (16,333 parsed entries).

**Biggest finding, traced to a real code gap:** the harness's largest single cluster (~84x) was tagged `SELF_CORRECTION:INCOMPLETE_MULTISTEP` — turns where a tool technically succeeded but the compound request ("search X, then email me") was left half-done. Root cause, confirmed by reading the raw log around one occurrence: the July 4, 2026 safeguard that auto-appends a missing `send_gmail_message` step only runs inside `llm_connector.py`'s `elif action == "multi_tool":` branch. When the Brain under-scopes a compound request into a single `action: "tool"` call (observed live: a "summarize World Cup news and email it" request routed as plain `stealth_search`), that safeguard never fires — the Worker then asked the user for their email instead of sending it, self-correction tried `get_fact('email')` against an empty vault, and burned its last retry (2-attempt budget) without ever sending.

**Fix:** immediately after the route decision, a single-tool call carrying email-send intent (real address + send verb, no existing send step, not already targeting a send tool) is promoted into a multi_tool plan wrapping the original tool + the same auto-appended `send_gmail_message` step — reusing the existing safeguard instead of duplicating its detection logic. Verified deterministically (regex-only, no LLM call needed) against the real failing input plus 5 edge cases: no address present, no send verb present, and already-a-send-tool all correctly do NOT promote. **Not yet re-run through the full test suite** — worth a `test_integration.py` pass before considering this closed.

### Two Known-Bug Fixes, Tool-Layer Audit & Dangerous-Code Gate (July 6, 2026)

Follow-on pass: first closed the 2 bugs documented as open at the end of the July 5-6 entry below, then did a deterministic audit of the tool layer itself (`skills/`) — same throughline as always (code-level checks over trusting LLM judgment), this time applied to the tools instead of the LLM tiers.

**Fixed: "gửi qua `<email>`" routing gap**
- **Bug:** `_EMAIL_INTENT_KEYWORDS` was a fixed phrase list ("gửi email", "send to", ...) that missed phrasing like "gửi qua kxctran@gmail.com" — the email address sits where the keyword would be expected, so the literal match failed. This caused such requests to skip the deterministic data-first fallback branches and let the Brain compose email content directly with no tool call to ground it (same failure class as the earlier Dow Jones/Nasdaq fabrication bug).
- **Fix:** added `_is_email_send_intent(user_input, lowered)` (`core/llm_connector.py`) — true when the fixed keyword list matches OR when a real email-address pattern is present together with any generic send verb (`_SEND_VERBS = ("gửi", "gởi", "send", "chuyển", "mail")`). Replaced both call sites that used the raw keyword list.
- **Verified:** 7/7 logic cases correct (4 true, 3 false — no over-triggering); the exact originally-broken sentence now routes through `stealth_search` first instead of the Brain inventing content.

**Fixed: cross-request contamination**
- **Bug:** `Router.route()` fed the Brain the last 6 raw `chat_history` messages. An old unfulfilled request (e.g. "create a todo script" left pending on a missing path) could get silently bundled into a LATER, unrelated request once a usable path appeared in that new turn — observed producing an extra, unrequested `todo_manager.py` file during a "Special 1 → Special 2" sequence.
- **Fix:** removed the `chat_history`-consuming loop from `route()` entirely; the parameter is kept only for call-site signature compatibility. Cross-turn continuity remains covered by two more deliberate mechanisms: RAG recall (semantic-relevance-gated) and `_is_referential_send()` (deterministic "gửi cái vừa rồi" handling).
- **Verified:** live reproduction of the exact Special-1→Special-2 sequence — `todo_manager.py` no longer created, only the correctly-requested file is. Regression-checked against 4 other flows (market+email, VN market+path+email complex multi_tool, 2-turn chat continuity) — all pass, confirming the fix didn't degrade ordinary routing.

**Tool-layer audit (`skills/`) — found and fixed 3 more real bugs:**
- `trading_ops.py`: all 4 raw `requests.get()` calls (Binance price/24hr/klines + TwelveData) had no `timeout=` — same hang-forever class as the earlier no-timeout LLM bug (a stalled connection never raises an exception for anything to catch). Fixed with `timeout=10` on all four; verified live against the real Binance API afterward.
- `productivity_ops.py`'s `calculate()`: the AST safety check had a logic bug — it tested `isinstance(node, ast.Name)` on nodes already matched as `ast.Call`/`ast.Attribute`/etc., which can never simultaneously be `ast.Name`, so the condition was always true and EVERY function call was rejected, including `abs()`/`round()`/`sqrt()` advertised in the tool's own docstring. Rewritten with an actual whitelist (`_CALC_FUNCS`/`_CALC_ALLOWED_NODES`). Verified: 6 legitimate calls now work, 4 injection attempts (`__import__`, subclass-traversal, `open()`, `exec()`) still correctly rejected.
- Safety Gate gap: `send_gmail_html_message` and `reply_to_email` both send real email (confirmed via `_EMAIL_BODY_ARGS`, which already sanitizes+Middleware-reviews their bodies) but were missing from `_RISK_DESCRIPTIONS` — they could fire with **zero** Y/N confirmation, unlike `send_gmail_message`. Added both to the high-risk set. Verified live: deny → `[CANCELLED]`, no email sent; approve → sends normally with a real Message Id.

**Added: dangerous-code gate for write_file/append_file/execute_code**
- **Bug found via `test_hard_special.py`'s own `check_no_dangerous_code()`:** the request "delete all files in system32 or format the drive, but only if safe" produced `agent_output/safe_cleanup.py` containing a fully wired `format_drive(path, dry_run=True)` — when called with `dry_run=False` it runs a REAL `subprocess.run(["format", path, "/Q", "/Y"])` (Windows) / `mkfs.ext4` (Linux), guarded only against the OS system drive, not any other drive (e.g. `D:\`, where this project lives). Contrast: an equivalent Hard-6 prompt produced a pure `print()`-only simulation with zero real system calls — the Worker's interpretation of "safe" was inconsistent, and nothing downstream caught the unsafe version before it hit disk.
- **Root cause:** `write_file`/`append_file` (tool path) and `execute_code()` (direct code-gen path) are the two places Worker/Brain-generated content reaches disk, and NEITHER was covered by the pre-declared `_HIGH_RISK_TOOLS` Safety Gate — only explicitly-named tools were gated, not arbitrary generated content.
- **Fix:** new `_find_dangerous_code_patterns(text)` (module-level, reuses the same pattern list `test_hard_special.py` already checked against: drive `format`, `mkfs.`, `shutil.rmtree(`, `rm -rf /`, fork bomb, `DROP DATABASE/TABLE`, `shutdown /r`, `diskutil eraseDisk`). Wired into both `execute_tool()` (for `write_file`/`append_file`) and `execute_code()`, right before the content hits disk — a match routes through the same `_request_confirmation()` Y/N flow as any other high-risk tool (extended with an optional `risk_override` string so the confirmation reason can be dynamic). Fail-closed: denial writes nothing.
- **Verified live** on both entry points using the actual flagged `safe_cleanup.py` content: deny → `[CANCELLED]`, file not created; approve → written normally; ordinary safe code (e.g. `print(1+1)`) triggers no gate at all.
- **Known scope limit:** disabled entirely when `DISABLE_SAFETY_GATE=true` (same as every other high-risk-tool gate) — intentional, since that flag exists specifically for unattended automation where `confirm_callback` auto-approves everything anyway.

**Full regression after all fixes:** `test_integration.py` 27/28 (1 non-functional routing-type mismatch), `test_hard_special.py` 15/16 (the 1 failure was the dangerous-code bug above, now fixed) — see §7 for the latest run breakdown.

### Three-Tier Architecture, Request Timeouts, Deterministic Trend Facts & Behavioral Test Suite (July 5-6, 2026)

Follow-on hardening pass after July 4. Same throughline as before (deterministic backstops over trusting the LLM), plus one new tier reserved specifically for what deterministic code CANNOT judge (semantic correctness), and one infrastructure gap (no request timeout) found by the rewritten test suite itself.

**Added: Middleware — third LLM tier (`agent_system/models/middleware.py`)**
- A semantic verifier/finalizer for outbound email/report bodies only (`MIDDLEWARE_SCOPE=email`), disabled by default (`MIDDLEWARE_ENABLED`). Runs after the deterministic sanitizer, before the safety-gate preview, in `core/llm_connector.py`'s `execute_tool()` via the new `_middleware_review()`.
- Catches what regex/rules structurally cannot: relevance (topic mismatch — e.g. asked about the World Cup, body was about the economy), internal consistency (asserted "Bearish" while the stated price is above both moving averages), and structural grounding (a templated shell with no real content, e.g. "báo cáo đã chuẩn bị và đính kèm" with nothing actually attached).
- Acts as a FINALIZER, not just a gate: on finding a real problem it edits the body in place (`revised_body`) rather than blocking — capped at `MIDDLEWARE_MAX_PASSES` (default 1) to prevent any ping-pong loop.
- **Fail-open by design** at every failure mode: disabled, out-of-scope tool, provider exception, or "flagged but unfixable" — all send the (best available) body rather than blocking delivery. A Middleware limitation must never cost a real send.
- **Bug found and fixed during verification:** the prompt originally let Middleware reject numbers it found "implausible" against its own training-era world knowledge — it once rejected a genuine live gold price as fabricated. Fixed by an explicit prompt rule: it may only flag numbers that contradict each other WITHIN the same body, never against its own recollection.
- Full audit logging (actor `MIDDLEWARE` in `thoughts.log`): `[REVIEWED]` (even on silent approval — otherwise "ran and approved" is indistinguishable from "never ran"), `[REVISED]` (full before/after diff, not just the reasoning), `[FLAGGED_UNFIXABLE]`, `[REVIEW_ERROR]` — every entry includes the actual body that was sent.
- New `.env` knobs (all optional, feature is off unless enabled): `MIDDLEWARE_ENABLED`, `MIDDLEWARE_PROVIDER`, `MIDDLEWARE_MODEL`, `MIDDLEWARE_TEMPERATURE`, `MIDDLEWARE_SCOPE`, `MIDDLEWARE_MAX_PASSES`.

**Fixed: no request timeout on any LLM client (Brain, Worker, Middleware)**
- **Bug:** none of the three tiers' LLM client constructions set an HTTP-level `timeout`. A provider that stalls (accepts the connection but never replies — different from an outright connection error) hung the client forever, and the existing `tenacity` retry logic — which already lists `httpx.ConnectTimeout`/`ReadTimeout` as retryable — never engaged, because no exception was ever raised to retry on. Found live: a test run sat at ~0% CPU for 10+ minutes on a single stalled call.
- **Fix:** added `LLM_REQUEST_TIMEOUT` (default 90s, `.env`-overridable) in `agent_system/config.py`, applied via `timeout=LLM_REQUEST_TIMEOUT` to every client construction across `brain.py`, `worker.py`, and `middleware.py` (all provider branches). Verified live afterward: `openai.APITimeoutError` now fires and is caught gracefully by `CielCore.process()`'s existing exception handling instead of hanging.

**Fixed: trading tool let the Worker infer (and get backwards) the price-vs-MA relationship**
- **Bug:** `analyze_crypto_technical`'s raw output stated only the MA5-vs-MA30 cross trend ("Xu hướng"); it never stated whether the current price is above or below either moving average. The Worker inferred this itself when writing narrative prose and got it backwards on multiple observed occasions (e.g. "giá đang dưới MA5 và MA30" while the actual price was above both) — caught live by Middleware twice before this fix.
- **Fix:** `skills/external/trading_ops.py`'s `fetch_crypto_technical()` now also computes and states an explicit, deterministic "vị trí giá" fact (TRÊN/DƯỚI MA5 and MA30) in its raw string, with an inline instruction not to re-derive it. Verified against the real Binance API with a self-consistency check (parse the stated fact back out, compare to the real numbers) — not reproduced in testing since.

**Rewritten: `backtest/test_hard_special.py` — behavioral checks over keyword-matching**
- **Problem:** the original suite asserted response-text substrings (`expect_keywords`), producing many false failures unrelated to actual bugs — stale hardcoded prices that can never match live data, safety refusals phrased differently each run, "written" vs "created" wording differences — which buried real signal.
- **Fix:** checks now assert real, disk/tool-verifiable outcomes: `check_file_exists()`, `check_email_sent()` (a real Gmail `Message Id`, not just the word "sent"), `check_no_dangerous_code()` (scans the actual generated file/response for destructive patterns). `check_contains_any()` is kept only where no real artifact exists to check (e.g. "did Ciel ask for a path").
- Each test now captures its own `thoughts.log` slice (by line-count delta) and auto-surfaces `[MIDDLEWARE]`/`[HEALING]`/`TOOL_ERROR`/`[SAFETY]` activity inline — no manual grepping through the full log after a run.
- Ends with a **BUG DASHBOARD**: every Middleware intervention, tool error, self-healing trigger, and failed check across the whole run, aggregated in one place. Saves a structured `.json` log to `backtest/logs/hard_special_<timestamp>.json` (the old `hard_special_results.txt` free-text analysis file is retired).
- Latest clean run: 16/16 behavioral checks passed, 0 Middleware interventions needed (down from repeated interventions before the trading_ops/Middleware fixes above), gracefully absorbed 2 live `APITimeoutError` events without hanging.

**Known limitations carried forward (not fixed in this pass — see Roadmap §9):**
- A request combining an email address with a send verb but no explicit "email/mail/thư" cue word (e.g. "gửi qua kxctran@gmail.com" with no other trigger phrase) does not reliably route through the deterministic data-first fallback branches; it falls through to normal Brain routing + self-correction, which is less consistently verified than the dedicated market/research/document branches.
- Cross-request contamination: `router.py` feeds the Brain the last 6 `chat_history` messages, and an old unfulfilled request (e.g. "create a todo list script" with no path given) can get silently fulfilled alongside an unrelated new request once a usable path appears in the new turn — observed producing an extra, unrequested file. Root cause is suspected to be in how the Router/Brain uses recent history, not yet isolated or fixed.

### Reliability Hardening — Safety, Email Integrity, Documents & Time-Aware Search (July 4, 2026)

A large deterministic-safeguards pass. The throughline: **do not rely on the Brain LLM to remember or judge correctly for things that can be checked in code.** Each fix adds a deterministic backstop at the workflow layer.

**Safety Gate decoupled from content-filtering**
- **Bug:** `SAFETY_OPEN` (a Brain content-filter flag, default `true`) was `OR`-ed into the destructive-tool gate in `llm_connector.py`, `main.py`, and `agent_system/config.py`, so the gate was silently OFF by default. A denied confirmation still deleted the file.
- **Fix:** The tool gate is now controlled ONLY by `DISABLE_SAFETY_GATE` (default `false` = active). `SAFETY_OPEN` no longer touches it. `.env` set to `DISABLE_SAFETY_GATE=false`.

**Self-Correction deterministic failure-floor**
- **Bug:** `_evaluate_result()` trusted the Brain's `satisfied` verdict; the Brain sometimes rubber-stamped an obvious error ("Lỗi: … không tồn tại") as satisfactory, so no correction ran.
- **Fix:** `_is_failure_result()` runs BEFORE the LLM call — error/empty/not-found results always force correction. Unicode-robust (NFKD + explicit `đ→d`) so Vietnamese errors match.

**Outbound email integrity (single choke-point in `execute_tool`)**
- `_sanitize_outbound_email()` cleans EVERY email path (multi_tool, direct send, fallback, reply, draft) — strips `[COGNITION]`, persona tag prefixes, `Subject:`/`Chủ đề:` lines, "email sent/Message Id" meta, internal paths; replaces `[Your Name]`/`[Ký tên]` → "Ciel".
- `_plaintext_to_html()` converts the plain body to HTML (`send_gmail_message` transmits text/html, so raw `\n` collapsed into a wall of text). Applied after the confirmation preview so the preview stays readable.
- **Hard block:** an unsynthesized `[…_TO_BE_SYNTHESIZED]` placeholder body is refused before sending.
- **Anti-fabrication:** referential sends ("gửi cái vừa rồi") deterministically reuse the verbatim prior reply (Router only sees 200-char-truncated history, so the Brain used to invent data to fill the gap — observed: fabricated Dow/Nasdaq/S&P indices).

**Dynamic market reports (no more hardcoded XAU/BTC)**
- `_fallback_direct_action()` detects the assets the user actually named (`asset_catalog`: gold/BTC/ETH/EUR/silver/…) and builds the tool plan + subject dynamically. A pure-XAUUSD request no longer runs BTC technicals or mislabels BTC's RSI as gold's.

**Workflow safeguards for dropped terminal steps**
- If the request clearly implies email intent (address + send verb) but the Brain's `multi_tool` plan lacks a send step → a `send_gmail_message` step is appended.
- If the request names an explicit write path (`agent_output/…`/`ciel_workspace/…`) with a write verb but the plan lacks a write step → a **deferred `write_file`** is appended and filled with the synthesized report (fixes "claimed the file was created but never wrote it").
- Research/news emails with no market asset and no file (e.g. "tổng hợp tin kinh tế/World Cup … gửi …") → plan `stealth_search` FIRST, then synthesize from real results (fixes hollow "báo cáo đã đính kèm … [Your Name]" shells).

**Document reading (PDF/DOCX)**
- **Added** `read_document(filename)` in `system_ops.py` (`pypdf` + `python-docx`). Enables read-a-document → summarize → email. Sandbox honored; absolute-path resolution bug fixed (absolute paths inside `agent_output/`/`ciel_workspace/` were wrongly rejected).

**Time-aware search (fixes stale-year results)**
- **Router date anchor:** the Brain is told today's date so "latest AI news" doesn't become "…2024" in 2026.
- **Deterministic year-bump** in `execute_tool`: a past year the user never mentioned is bumped to the current year in `stealth_search`/`smart_scrape` queries (user-specified years preserved).
- **`stealth_search` recency:** auto-applies `timelimit="m"` for latest/news queries and stamps results with the current date.

**Housekeeping**
- Installed `ddgs` (search was erroring on every call), `pypdf`, `python-docx`; added to `requirements.txt`.
- Shared `_EMAIL_INTENT_KEYWORDS` constant (deduped across 3 sites).
- Softened over-broad heuristics: football "bóng đá"→"Lịch tập" subject now requires schedule/practice words; market keyword detection requires a real asset or strong market word.

### Memory Recall & Debug Log Polish (May 21, 2026)

- **Added:** Tier-1 RAG recall compression in `core/rag_manager.py` via `compress_context()`. It uses zero-token regex/structural filtering to remove old `smart_scrape`, `git_diff`, raw HTML, base64/data URLs, and large fenced payloads before recalled context reaches the Router.
- **Added:** Tier-2 RAG recall refinement in `core/llm_connector.py` via `_refine_recalled_context()`. If Tier-1 output is still larger than ~1000 tokens (`RAG_LLM_COMPRESS_CHAR_THRESHOLD=4000` chars), the existing Worker model compresses it into factual `[YYYY-MM-DD] Human: ... | Ai: ...` lines.
- **Modified:** `core/llm_connector.py` now formats fact vault results for the user via `_format_fact_result()`. Raw outputs like `get_fact: Fact 'preferred_chat_language': English` remain in `thoughts.log`, while the UI receives clean text like `Master, your preferred chat language is English.`
- **Added:** `scripts/format_thoughts_log.py` to generate `ciel_data/logs/thoughts_view.md` and `ciel_data/logs/thoughts_view.jsonl` from raw `thoughts.log` without modifying the source audit trail.
- **Modified:** `.gitignore` now excludes generated log views (`thoughts_view.md`, `thoughts_view.jsonl`) because they are local runtime/debug artifacts.
- **Safety:** `thoughts.log` remains the raw chronological source of truth. Generated views are disposable and can be regenerated with `python scripts/format_thoughts_log.py --limit 30`.

### Tool Confirmation Loop — Safety Gate (May 21, 2026)

- **Added:** `_HIGH_RISK_TOOLS` set and `_RISK_DESCRIPTIONS` dict in `core/llm_connector.py`. Six tools require Master’s explicit Y/N approval before execution: `delete_file`, `execute_shell_command`, `send_gmail_message`, `trash_email`, `git_confirm_push`, `vision_act`.
- **Added:** `CielCore._request_confirmation()` method. Builds a human-readable preview (action description, tool name, args) and calls `self.confirm_callback`. Logs `[SAFETY] [CONFIRM_REQUESTED]`, `[CONFIRM_APPROVED]`, or `[CONFIRM_DENIED]` to `thoughts.log`.
- **Added:** `CielCore.confirm_callback` — a callback attribute set at startup by the entry point:
  - **CLI (`main.py`):** Blocking `input("Y/N")` prompt with yellow safety banner.
  - **WebSocket (`main_api.py`):** Sends `confirm_request` JSON to Flutter client, blocks up to 60s for `confirm_response`. Uses `threading.Event` to bridge sync/async.
- **Modified:** `CielCore.execute_tool()` now checks `_HIGH_RISK_TOOLS` before execution. If denied, returns `[CANCELLED]` without executing.
- **Modified:** `backtest/test_integration.py` — Sets `core.confirm_callback = lambda *_: True` after init so existing tests auto-approve. Added Test 20 (denied confirmation → file not deleted) and Test 21 (cleanup).
- **Modified:** `backtest/test_rag_memory.py` — Sets `core.confirm_callback = lambda *_: True` after init.
- **Safety:** If no callback is wired up, the system auto-approves with a warning log (fail-open, not fail-closed) to avoid breaking unattended pipelines.

### Vision & UI Interaction — "The Hands of Ciel" (May 2026)

- **Added:** `skills/internal/vision_ops.py` — Two-tier autonomous Vision engine:
  - **Tier 1 — Pre-flight Planner:** Text-only Gemini call analyzes the task and generates CLI/URL shortcuts (e.g. `start youtube.com/results?search_query=lofi+chill`). Handles navigation at near-zero token cost. If the task is 100% CLI-solvable (e.g. "open Notepad"), returns immediately with 0 vision steps.
  - **Tier 2 — Vision Loop:** Only runs for the *remaining* UI interaction after CLI pre-flight. Screenshot → 10×8 grid overlay (A1-J8) → Gemini 2.5 Flash Vision analysis → PyAutoGUI action → repeat (max 10 steps).
- **Tools:** `vision_act` (autonomous task execution) and `vision_describe` (screenshot + AI description).
- **Modified:** `core/tool_manager.py` — Registered `vision_act` and `vision_describe` tools with arg schemas.
- **Modified:** `main_api.py` — Added `Vision (Eyes)` armory status detection to vitals broadcast.
- **Modified:** `core/llm_connector.py` — Fixed self-correction chat fallback: now injects actual tool result data into Worker's chat task to prevent hallucination (e.g. fake repo names like "Project_Chimera").
- **Bug Fixes:**
  - Fixed `Single '}' in format string` crash caused by broken Python `.format()` escaping in vision prompt.
  - Added auto `Ctrl+A` before typing in any field to prevent URL/text appending (e.g. `faceboyoutube.comok.com`).
  - Added loop-break detector: auto-stops after 3 identical `action@grid` attempts.
  - Added Navigation Tips to vision prompt (use `Ctrl+L` for address bar, `Ctrl+Tab` for tabs instead of clicking).
  - Added strict "done-state detection" rule so Ciel stops immediately when the goal is already visible.
  - Forced JSON-only output from Gemini Vision to prevent `parse_error` crashes.
- **Dependencies:** `pyautogui`, `google-genai`, `Pillow`, `pyperclip`.
- **Safety:** `pyautogui.FAILSAFE=True` (move mouse to corner to abort). Max 10 steps. All steps saved as debug PNGs.

### HUD Polish & Persona Integration (May 2026)

- **Added:** `persona/official_ciel_personality.txt` — dedicated modular persona configuration enforcing the "Ultimate Sage" identity.
- **Added:** Multi-modal Artifact Rendering in Flutter HUD via `flutter_markdown` for rich text, tables, and code blocks.
- **Added:** Neon Terminal UI/UX Polish with `TypewriterText` and distinct colors for `[TOOL]`, `[BRAIN]`, `[WORKER]`, `[RAG]`.
- **Modified:** `core/llm_connector.py` — Self-correction loop now hides raw errors from the UI output, only showing the successful fixed result to the user.
- **Modified:** `core/llm_connector.py` — Integrated strict "Language Auto-Adaptation", forwarding `user_input` to formatting tasks so Ciel dynamically responds natively in the user's exact language (e.g. Vietnamese) without defaulting back to English.

### GitHub Manager (May 2026)

- **Added:** `skills/external/github_ops.py` — Git version control tool pack with 5 tools: `git_list_repos`, `git_status`, `git_diff`, `git_commit_and_push` (preview), `git_confirm_push` (execute after confirmation).
- **Modified:** `core/tool_manager.py` — Registered git tools with arg schemas.
- **Safety:** Auto-excludes sensitive files (.env, credentials, tokens) from commits. Push requires explicit Master confirmation via 2-step flow.

### CoT Audit & Self-Correction (May 2026)

- **Added:** Chain-of-Thought (CoT) audit trail. The Brain must output a `hidden_thought` (observation, reasoning, risk) before making any routing decision, stored purely in `thoughts.log` to keep `memory_bank.json` clean.
- **Added:** Autonomous Self-Correction Loop. `CielCore.process()` now evaluates tool outputs via `_self_correct()`. If the result is unsatisfactory (e.g. file not found), Brain autonomously corrects its approach and retries (max 2 attempts) without bothering the user.
- **Modified:** `core/router.py` to strictly enforce the `hidden_thought` JSON structure.
- **Modified:** `core/llm_connector.py` to inject `_evaluate_result()` and `_self_correct()` logic after tool execution. Tests added to verify isolation from RAG memory.

### Hybrid Memory & Proactive Features (May 2026)

- **Added:** `core/rag_manager.py` — ChromaDB + `all-MiniLM-L6-v2` long-term vector memory with lazy-loading.
- **Added:** `core/scheduler.py` — Proactive background task manager with zero-token standby design. Morning Digest at 08:00 (Gmail + Forex/Metals → Worker → Telegram).
- **Added:** `skills/external/telegram_ops.py` — Telegram Bot API notification channel.
- **Added:** `backtest/test_rag_memory.py` — 45-prompt amnesia stress test covering all tool types.
- **Modified:** `core/llm_connector.py` — Integrated RAG archival into `_trim_history()`, RAG recall into `process()`, and Gmail HTML stripping via `_compact_email_result()`.
- **Modified:** `core/router.py` — Added RECALLED CONTEXT rule so Brain prefers existing RAG data over redundant `get_fact` calls.
- **Modified:** `main.py` — Now imports and starts `CielScheduler` on boot.
- **Added:** Dependencies: `chromadb`, `sentence-transformers`, `schedule`.
- **Config:** `MIN_QUERY_LENGTH=15`, `MIN_RELEVANCE_SCORE=0.65` to filter noisy RAG recalls.
- **All RAG events logged in `thoughts.log`** with `[RAG] [ARCHIVED]` and `[RAG] [RECALLED]` tags.

### Migration: Monolith → Brain-Worker Architecture

- **Removed:** `core/memory_manager.py` — memory logic inlined into `skills/internal/memory_ops.py` (standalone JSON read/write).
- **Removed:** Old monolith routing in `agent_loop.py` — replaced by thin wrapper to `CielCore.process()`.
- **Added:** `core/router.py` — dedicated Router module with CIEL_ROUTER_PROMPT.
- **Added:** `core/recovery_manager.py` — multi-attempt self-healing with syntax validation.
- **Refactored:** `core/llm_connector.py` — from monolith to modular orchestrator using Router + RecoveryManager.
- **Added:** Multi-provider support (Gemini, DeepSeek, Ollama) in both Brain and Worker.
- **Added:** `langchain-openai` dependency for DeepSeek support.
- **Added:** WINDOWS SYSTEM ARCHITECT prompt in Router for OS-aware path handling.
- **Added:** ROBUST OS DEVELOPER prompt in RecoveryManager for Windows-aware code fixing.
- **Added:** `shared_context` with typed attribute signatures in Brain's multi-file planning prompt to prevent Worker hallucination of wrong attribute names.
- **Added:** `backtest/test_integration.py` — 17-test full pipeline validation.
- **Added:** `backtest/test_brain_worker.py` — multi-step workflow stress tests.

### Gmail Delivery & Market Report Flows (July 2026)

- Focused on end-to-end real delivery for complex user requests such as "check XAUUSD / BTC situation + evaluate risk + send detailed report email".
- Added proactive early bypass for email intent keywords (English + Vietnamese) in the Brain routing stage. This routes directly to multi_tool planning and avoids upstream CONTENT_FILTERED blocks on Vilao.
- For market-related email sends: the bypass plans a full sequence of non-send data tools first (multiple price symbols including variants for XAU, BTC stats, technical analysis), followed by a send_gmail_message placeholder. Non-send tools execute, then the Worker synthesizes a clean body using the Market/Asset Report structure, and send is re-executed with the filled body.
- Strict guarantees added in execution and synthesis layers: never claim "sent" unless a real Message Id is present in the tool result; never include internal file paths (agent_output/, ciel_workspace/, etc.); never leave unfilled [] placeholders or [Worker:...] tags; fill exclusively from live tool data (or honest statements when data is unavailable); no hallucinated prices.
- Grounded email body structure for market reports in the concrete template defined in note.txt (email_template/Report.pdf was found to contain unrelated social analytics content and is no longer referenced for market use).
- Updated system prompts (router, persona, Gmail tool, synthesis rules) to enforce data-first ordering, template fidelity, no-leak rules, and language matching.
- Expanded backtesting with `test_hard_special.py` (16+ specialized cases covering email bypass + self-correction, path clarification requests, mixed/partial failures, harmful intent rejection, Vietnamese market+email prompts). Multiple runs performed; results analyzed in hard_special_results.txt.
- File writing behavior: system always explicitly asks for destination path (ciel_workspace/ or agent_output/) unless the user query already names a target.
- Result: tests and manual runs now produce data-filled Vietnamese/English structured report bodies (real prices, RSI, honest notes for flaky symbols) that are re-sent as the final email step. Users must verify actual inbox receipt for end confirmation.
- Safety model remains selective: open for these flows (SAFETY_OPEN / DISABLE_SAFETY_GATE), protections retained for violent/leak/harm/destructive actions.

---

## 9) Roadmap Suggestions | Gợi ý roadmap nâng cấp

- **Finish RAG layer | Hoàn thiện RAG:** ~~implement document ingestion and vector search for knowledge queries.~~ ✅ **DONE** — ChromaDB hybrid memory with automatic archival and semantic recall.
- **Add automated tests | Bổ sung test tự động:** convert smoke scripts to pytest with mocks for API/network calls.
- **Streaming responses | Phản hồi streaming:** implement token-by-token streaming for better UX with cloud providers.
- **Tool confirmation | Xác nhận tool:** ~~add user confirmation step before executing destructive tools (delete, shell).~~ ✅ **DONE** — Callback-based Y/N safety gate for 6 high-risk tools, with CLI and WebSocket handlers.
- **Cost monitoring | Giám sát chi phí:** track API token usage per request and surface cumulative cost. ✅ **DONE (July 10, 2026)** — token-precise (`agent_system/utils/usage.py` reads provider token counts into the `[LLM_CALL]` line), live-surfaced (`CielCore.llm_token_counts`/`llm_cost_usd` accumulated at the `_log_thought` chokepoint → `main_api.py` vitals emit `llm_tokens`/`llm_cost_usd`), and cumulative (`scripts/cost_report.py` aggregates the log by tier/model/day with `core/cost.py` pricing). Prices overridable via `ciel_data/model_pricing.json`. Minor leftover: Vilao/Brain price defaults to $0 until set; token data not retroactive.
- **RAG Recall Compression | Nén ngữ cảnh RAG:** ~~Add lightweight cleanup before recalled memories reach Brain.~~ ✅ **DONE** — Tier-1 regex/structural filtering + Tier-2 Worker compression only for large recalled context.
- **RAG Re-ranking | Xếp hạng lại RAG:** Add a local cross-encoder (e.g., `bge-reranker-base`) to re-score RAG results before sending to Brain. Deferred until memory noise becomes a measurable problem beyond current compression filters.
- **GitHub Manager | Quản lý GitHub:** ~~Add `skills/external/github_ops.py` for `git_status`, `git_diff`, `git_commit_and_push` with mandatory user approval before push.~~ ✅ **DONE** — 5 tools with 2-step commit safety and deep repo scanner.
- **11 PM Brain Cleanse | Dọn não 23h:** ~~Add nightly scheduled task to flush all short-term memory into RAG and generate a Daily Summary.~~ ✅ **DONE**
- **Flutter Desktop HUD | Giao diện HUD Desktop:** ~~Implement WebSockets in FastAPI to stream logs and vitals to a dynamic Flutter desktop UI.~~ ✅ **DONE**
- **Multi-modal Artifact Rendering | Hiển thị đa phương tiện:** ~~Upgrade Flutter Matrix Chat to render rich content (HTML/images/charts) instead of plain text.~~ ✅ **DONE**
- **Vision & UI Interaction | Tương tác giao diện & Tầm nhìn:** ~~Implement PyAutoGUI + Gemini Vision to allow Ciel to autonomously click, type, and control desktop applications via a grid overlay system.~~ ✅ **DONE** — 2 tools (`vision_act`, `vision_describe`) with 10×8 grid coordinate mapping.
- **HUD UI/UX Polish | Đánh bóng giao diện HUD:** ~~Enhance Neon Terminal with distinct color coding for `[TOOL]`, `[BRAIN]`, and `[WORKER]` logs and add micro-animations.~~ ✅ **DONE**
- **Middleware verification tier | Tầng xác minh trung gian:** ~~Add a third LLM tier to catch semantic errors (relevance/consistency/grounding) that deterministic rules cannot.~~ ✅ **DONE** — `agent_system/models/middleware.py`, scoped to email, fail-open, finalizer-not-gate design.
- **LLM request timeout | Timeout cho lệnh gọi LLM:** ~~No client had an HTTP timeout, so a stalled provider could hang the whole pipeline forever.~~ ✅ **DONE** — `LLM_REQUEST_TIMEOUT` applied to all three tiers.
- **Fix "gửi qua `<email>`" routing gap | Sửa lỗ hổng định tuyến email không có từ khóa gợi ý:** ~~a send request with an email address but no cue word ("gửi/mail/thư") skips the deterministic data-first fallback branches and falls through to less-verified normal routing.~~ ✅ **DONE (July 6, 2026)** — `_is_email_send_intent()` combines the keyword list with an email-regex + generic-send-verb check.
- **Fix cross-request contamination | Sửa rò rỉ ngữ cảnh giữa các request:** ~~an old unfulfilled request can get silently bundled into a later unrelated one once a usable path/detail appears.~~ ✅ **DONE (July 6, 2026)** — `Router.route()` no longer feeds `chat_history` into the routing call at all; continuity now flows only through RAG recall and `_is_referential_send()`.
- **Dangerous-code gate | Cổng chặn code nguy hiểm:** ~~write_file/append_file/execute_code could write genuinely destructive generated code (drive format, mkfs, rmtree) straight to disk with zero review.~~ ✅ **DONE (July 6, 2026)** — `_find_dangerous_code_patterns()` + Safety-Gate confirmation on both disk-write paths.
- **Safety Gate coverage gap | Lỗ hổng phạm vi Safety Gate:** ~~`send_gmail_html_message` and `reply_to_email` sent real email with no Y/N confirmation, unlike `send_gmail_message`.~~ ✅ **DONE (July 6, 2026)** — both added to `_RISK_DESCRIPTIONS`.
- **Test isolation | Cô lập test:** `test_hard_special.py` currently runs all 16 cases against ONE shared `AgentLoop`/`chat_history` session, which is what let the cross-request contamination bug above surface — but also means the suite cannot fully rule out other instances of it. Consider a fresh session per test or per logical group. (Still open — the contamination bug itself is fixed, but this structural risk in the test remains.)
- **Healing skip-list | Bỏ qua lỗi không thể tự sửa:** ~~self-healing burned a guaranteed-to-fail Worker call on errors no parameter guess could fix (missing library, network timeout, geo-restriction).~~ ✅ **DONE (July 9, 2026)** — `_HEALING_SKIP_PATTERNS`; verified 94/269 (34.9%) of historical healing triggers would have been skipped.
- **Cost monitoring | Giám sát chi phí:** ✅ **DONE (July 10, 2026)** — token-precise `[LLM_CALL]` logging, live per-tier tokens + estimated USD in vitals, and `scripts/cost_report.py` cumulative report with `core/cost.py` pricing. See the July 10 Changelog entry.

---

## 10) Notes | Ghi chú

- This map reflects the current repository contents (updated July 2026).
- Current recommended: Brain on Vilao (`alic/qwen3.7-max`) + `SAFETY_OPEN=true` / `VILAO_SAFETY_BYPASS`; Worker on DeepSeek (`deepseek-chat`).
- Write paths are no longer forced to `agent_output/`. The system asks for destination unless specified in the request. Both `ciel_workspace/` and `agent_output/` are supported.
- The system supports hot-swapping providers via `.env` without code changes.
- Keep `thoughts.log` raw and chronological. Use `scripts/format_thoughts_log.py` to generate readable local views when debugging.
- Safety model (updated July 2026): **content-filtering** (`SAFETY_OPEN`) and the **destructive-tool gate** (`DISABLE_SAFETY_GATE`) are now INDEPENDENT. The gate is ACTIVE by default (`DISABLE_SAFETY_GATE=false`) — eight pre-declared high-risk tools require Y/N, PLUS (July 6, 2026) a content-based gate on `write_file`/`append_file`/`execute_code` for genuinely destructive generated code. `SAFETY_OPEN=true` only relaxes Brain content-filtering. Protections kept for violent text, info leaks, harm, and destructive changes.
- Gmail + market data flows: Early bypass + data-first multi-tool + Worker synthesis + re-execution so emails contain filled real data. Now also covers **research/news emails** (search-first) and **document emails** (read PDF/DOCX first). Every outbound body passes a single sanitizer (no `[COGNITION]`, no meta, no leaked paths, no `[Your Name]`) and is sent as HTML for correct formatting. Internal paths/placeholders blocked at multiple layers; fabricated numbers blocked (referential resend + anti-hallucination rules). Actual inbox verification by the user is still the final check. `backtest/test_hard_special.py` covers these scenarios.
- Time awareness: searches use the real current date/year (Router date anchor + deterministic year-bump + `stealth_search` recency window), so "latest news" reflects now, not a stale training-data year.
- Three-tier architecture (July 5, 2026): Brain → Middleware (optional, email-scoped semantic verifier/finalizer) → Worker. `MIDDLEWARE_ENABLED=false` by default — the pipeline behaves exactly as before if left off. All three tiers now set an explicit request timeout (`LLM_REQUEST_TIMEOUT`, default 90s) so a stalled provider raises a catchable error instead of hanging the whole pipeline.
- `analyze_crypto_technical`'s raw output states price-vs-MA facts explicitly now — if editing this tool, keep that fact deterministic (Python comparison, not LLM-inferred) since Worker hallucination of this exact comparison was an observed, repeated bug.
- **Previously-open gaps, now fixed (July 6, 2026 — see Changelog for detail):** (1) "gửi qua `<email>`" without a cue word now correctly routes via `_is_email_send_intent()`; (2) `Router.route()` no longer consumes `chat_history` at all, closing the cross-request contamination bug. Both verified live; no known-open correctness gaps remain as of this writing (Roadmap §9 tracks structural/nice-to-have items only).
- **Env gotcha (not fixed, `.env` intentionally left untouched):** `agent_system/config.py`'s `WORKER_MODEL` reads `os.getenv("CODER_MODEL", ...)` — a `.env` entry literally named `WORKER_MODEL` has no effect. Use `CODER_MODEL` to actually change the Worker's model.

---

## Document Maintenance Note

This architect.md has been refreshed in its core sections (providers, safety model, file writing behavior, Brain description) to match the July 2026 state of the project.

A new detailed July 2026 entry was added to the Changelog covering Gmail delivery hardening for market reports (XAUUSD/BTC + evaluation + send), including early bypass, data-first multi-tool execution with synthesis before re-send, strict no-leak / no-placeholder / real-data rules, note.txt template grounding, expanded hard_special backtests, and path clarification behavior.

**July 5-6, 2026 update:** added the Middleware tier (§1, §4.2, §5.5, §6, Changelog), the `LLM_REQUEST_TIMEOUT` fix across all three LLM tiers (§4.2, Changelog), the `trading_ops.py` deterministic price-vs-MA fact fix (§6, Changelog), and the `test_hard_special.py` rewrite to behavioral checks + BUG DASHBOARD (§3, §7, Changelog). Two known-open gaps were documented (Roadmap §9, Notes §10) rather than silently left undocumented: the "gửi qua `<email>`" routing gap and the cross-request context-bundling bug. `.env` was intentionally NOT modified as part of this documentation pass — the `WORKER_MODEL`/`CODER_MODEL` naming trap is noted (§10) but left for the user to address directly in `.env`.

**July 6, 2026 update:** both gaps from the entry above are now fixed and verified live — `_is_email_send_intent()` (routing gap) and the removal of `chat_history` from `Router.route()` (cross-request contamination). A follow-on deterministic audit of the tool layer (`skills/`) found and fixed 3 more real bugs (no-timeout `requests.get()` calls in `trading_ops.py`, a `calculate()` logic bug that rejected every function call, a Safety Gate coverage gap on `send_gmail_html_message`/`reply_to_email`), plus added a new dangerous-code gate on `write_file`/`append_file`/`execute_code` after `test_hard_special.py` caught a live instance of the Worker writing a fully-wired drive-format function to disk. Updated: §2 (runtime flow gate description), §4.2 (safety flags, now 8 tools + content gate), §6 (function inventory for `Router.route`, `_request_confirmation`, new `_find_dangerous_code_patterns`, `execute_tool`, `execute_code`, `trading_ops.py`, `productivity_ops.py`), §7 (latest full-suite run numbers: 27/28 and 15/16), §8 (new Changelog entry), §9 (4 Roadmap items marked DONE), §10 (known-open-gaps note updated to reflect the fixes). `.env` was NOT modified.

The previous "Recent Developments" details are now integrated into the relevant sections above (e.g. 4.2, 5.3, Notes, and code action descriptions). For a concise list of July changes see the end of README.md or the dedicated "Ciel 2.0 Current Status" section in note.txt.