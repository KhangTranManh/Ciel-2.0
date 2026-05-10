# Ciel 2.0 Project Roadmap (EN + VI)

This document is a full project map for architecture, dependencies, and functions.
Tài liệu này là bản đồ đầy đủ của dự án: kiến trúc file, phụ thuộc và toàn bộ hàm.

---

## 1) High-Level Architecture | Kiến trúc tổng thể

Ciel 2.0 uses a **Modular Brain-Worker** architecture where responsibilities are cleanly separated:

- **Entry point | Điểm vào:** `main.py` starts CLI loop and delegates each command to `AgentLoop`.
- **Thin agent loop | Vòng lặp agent:** `core/agent_loop.py` is a thin wrapper that passes user input to `CielCore.process()`.
- **Main orchestrator | Bộ điều phối chính:** `core/llm_connector.py` (`CielCore`) is the central pipeline. It initializes Brain, Worker, Router, RecoveryManager, and ToolManager, then coordinates the full `route → execute → respond` flow.
- **Router | Bộ định tuyến:** `core/router.py` uses the Brain LLM to classify user intent into `chat`, `tool`, `code`, or `multi_tool` actions. Includes WINDOWS SYSTEM ARCHITECT and anti-hallucination guardrails. Contains RECALLED CONTEXT rule to prefer RAG data over redundant `get_fact` calls.
- **Self-healing engine | Hệ thống tự sửa lỗi:** `core/recovery_manager.py` implements multi-attempt (up to 3) autonomous error correction with a ROBUST OS DEVELOPER prompt, syntax validation, and escalating fix strategies.
- **Hybrid Memory (RAG) | Bộ nhớ lai:** `core/rag_manager.py` provides long-term semantic memory via ChromaDB + `all-MiniLM-L6-v2` embeddings. Short-term: `memory_bank.json` (max 20 messages). Long-term: `ciel_data/vector_memory/` (ChromaDB). Overflow messages are automatically archived into vector storage via `_trim_history()`. Recall is filtered by `MIN_QUERY_LENGTH=15` and `MIN_RELEVANCE_SCORE=0.65` to eliminate noise.
- **Proactive Scheduler | Lịch trình chủ động:** `core/scheduler.py` runs background tasks on a timer using zero-token standby. Tools are called directly (bypassing Brain) to save API costs. Currently schedules a Morning Digest at 08:00 daily (Gmail + Forex/Metals → Worker summary → Telegram notification).
- **Tool registry/execution | Kho công cụ & thực thi:** `core/tool_manager.py` loads internal + external tool packs, stitches tool manuals, executes by tool name.
- **Agent system | Hệ thống agent:** `agent_system/` contains the Brain and Worker LLM models, provider config, LangGraph pipeline, and buffer writer tool.
- **Tool packs | Các gói kỹ năng:**
  - Internal: workspace/file ops (`skills/internal/system_ops.py`), host OS control (`skills/internal/os_ops.py`), fact tools (`skills/internal/memory_ops.py` — standalone, no external dependency)
  - External: Gmail toolkit/extensions (`skills/external/gmail_ops.py`), crypto/trading toolkit (`skills/external/trading_ops.py`), Telegram notifications (`skills/external/telegram_ops.py`)
- **Testing scripts | Script kiểm thử:** `backtest/test_integration.py` (full 17-test pipeline), `backtest/test_brain_worker.py` (multi-step/multi-file workflow tests), `backtest/test_rag_memory.py` (45-prompt amnesia stress test for hybrid memory).
- **Workspace sandbox | Vùng workspace:** `ciel_workspace/` contains files created/tested by tools.

---

## 2) Runtime Flow | Luồng chạy

### Primary Pipeline (CielCore — used by main.py)

```
User Input → CielCore.process()
  → RAG Recall: search ChromaDB for semantically similar past context
    (skipped if query < 15 chars or relevance < 0.65)
  → Inject recalled context into user prompt (if any)
  → Router (Brain LLM) classifies intent → JSON decision
  → Based on action:
      "chat"       → Worker generates natural response
      "tool"       → ToolManager executes → Worker formats result (if needed)
      "code"       → Worker generates code → buffer_writer flushes to disk
      "multi_tool" → Sequential tool execution → Worker synthesizes combined report
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
├── credentials.json              # Google OAuth credentials
├── main.py                       # CLI entry point
├── requirements.txt              # Python dependencies
│
├── core/                         # Main orchestration layer
│   ├── agent_loop.py             # Thin wrapper → CielCore.process()
│   ├── llm_connector.py          # CielCore: main pipeline orchestrator
│   ├── rag_manager.py            # RAG: ChromaDB vector memory (long-term)
│   ├── router.py                 # Router: Brain-based intent classification
│   ├── recovery_manager.py       # RecoveryManager: multi-attempt self-healing
│   ├── scheduler.py              # Proactive background task scheduler
│   └── tool_manager.py           # ToolManager: tool registry & execution
│
├── agent_system/                 # Brain-Worker LLM subsystem
│   ├── __init__.py
│   ├── config.py                 # Provider/model/retry configuration
│   ├── main.py                   # Standalone LangGraph runner
│   ├── requirements.txt
│   ├── models/
│   │   ├── brain.py              # Brain LLM (Router + Planner)
│   │   └── worker.py             # Worker LLM (Code/Text generator)
│   ├── graph/
│   │   ├── state.py              # AgentState TypedDict
│   │   ├── nodes.py              # brain_node, worker_node, file_write_node
│   │   ├── edges.py              # Conditional routing edges
│   │   └── builder.py            # LangGraph compilation
│   ├── tools/
│   │   └── buffer_writer.py      # In-memory code buffer with flush-to-disk
│   └── utils/
│       └── logger.py             # Colored console logger
│
├── skills/                       # Tool packs
│   ├── internal/
│   │   ├── memory_ops.py         # Fact vault tools (standalone JSON-based)
│   │   ├── os_ops.py             # Shell, screenshot, app launcher
│   │   └── system_ops.py         # Workspace file CRUD + Python runner
│   └── external/
│       ├── github_ops.py         # Git repo manager (status, diff, commit, push)
│       ├── gmail_ops.py          # Gmail toolkit + custom ops
│       ├── telegram_ops.py       # Telegram Bot API notifications
│       └── trading_ops.py        # Crypto price, TA, Forex/Metals
│
├── persona/                      # Personality fragments
│   ├── directives.txt
│   ├── format.txt
│   └── identity.txt
│
├── backtest/                     # Test suites
│   ├── test_integration.py       # 17-test full pipeline validation
│   ├── test_brain_worker.py      # Brain-Worker multi-file workflow tests
│   ├── test_rag_memory.py        # 45-prompt amnesia stress test (hybrid memory)
│   └── logs/                     # Test output logs (.txt + .json)
│
├── ciel_data/                    # Runtime data
│   ├── facts.json                # Fact vault
│   ├── gmail_token.json          # Google OAuth token (auto-generated)
│   ├── memory_bank.json          # Chat history persistence (short-term, max 20)
│   ├── vector_memory/            # ChromaDB persistent storage (long-term RAG)
│   └── logs/
│       └── thoughts.log          # Brain/Worker thought process audit trail
│
├── ciel_workspace/               # Sandbox for user scripts
│   ├── test_healing.py
│   └── loop_test.py
│
└── agent_output/                 # Generated code output directory
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
- **Scheduling**
  - `schedule`

### 4.2 Multi-Provider Support

Ciel supports **three LLM providers**, configurable independently for Brain and Worker:

| Provider   | Brain Model (Router) | Worker Model (Generator) | Config Key        |
|------------|----------------------|--------------------------|-------------------|
| **Gemini** (default) | `gemini-1.5-pro`     | `gemini-1.5-flash`       | `GEMINI_API_KEY`  |
| **DeepSeek** | `deepseek-v4-pro`    | `deepseek-v4-pro`        | `DEEPSEEK_API_KEY`|
| **Ollama** (local) | `qwen2.5:14b`       | `qwen2.5-coder:14b`     | `OLLAMA_BASE_URL` |

Provider is set via `BRAIN_PROVIDER` and `WORKER_PROVIDER` in `.env` or `agent_system/config.py`.

### 4.3 Key runtime services / credentials

- **Gemini API:** `GEMINI_API_KEY` (prepaid credits, monthly spend cap)
- **DeepSeek API:** `DEEPSEEK_API_KEY`
- **Local models via Ollama:** `OLLAMA_BASE_URL` (default: `http://localhost:11434`)
- **Trading (TwelveData):** `TWELVEDATA_API_KEY` (free tier for Forex/Metals/Stocks)
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
- `code` — Code generation + save to `agent_output/`
- `multi_tool` — Sequential multi-tool workflow with synthesized report

**Prompt Roles Embedded:**
- **WINDOWS SYSTEM ARCHITECT:** Forces absolute paths in double quotes, `python -m` prefix, and `taskkill` suggestions for locked files.
- **Anti-hallucination:** Explicit rule: `NEVER output "action": "shell_command"` — must use `"tool"` with `"execute_shell_command"`.

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

**Prompt Roles Embedded:**
- **ROBUST OS DEVELOPER:** Implements `FileNotFoundError`/`PermissionError` handling, `os.path.normpath` for Windows, and `--user` flag suggestions for pip.

### 5.3 Brain (`agent_system/models/brain.py`)

The Brain has two specialized prompts:
- **BRAIN_SYSTEM_PROMPT:** For the LangGraph pipeline — multi-step planner with `shared_context` for isolated Worker steps.
- **CIEL_ROUTER_PROMPT (in router.py):** For the main CielCore pipeline — intent classifier.

Supports three providers: Gemini (`ChatGoogleGenerativeAI`), DeepSeek (`ChatOpenAI`), Ollama (`ChatOllama`).

### 5.4 Worker (`agent_system/models/worker.py`)

Pure text/code generator. No tools, no routing. Features:
- Robust markdown fence stripping (`_strip_markdown_fences`)
- Retry protection via `tenacity` for transient errors
- Supports three providers: Gemini, DeepSeek, Ollama

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
- `execute_chat(self, task)` - Worker generates natural language response.
- `execute_tool(self, tool_name, tool_args, response_hint)` - Execute tool with self-healing loop (up to 3 attempts) and optional Worker formatting.
- `execute_code(self, task, filename)` - Worker generates code → buffer_writer → flush to disk.
- `execute_multi_tool(self, tools, response_hint)` - Sequential tool execution → Worker synthesizes combined report.
- `process(self, user_input)` - Full pipeline: **RAG recall → inject context →** route → execute → respond → save memory.
- `chat_with_tools(self, user_input, use_coder)` - Legacy compatibility wrapper.

### `core/router.py` (`class Router`)

- `__init__(self, brain, log_thought_fn)` - Init with Brain LLM and thought logger.
- `route(self, user_input, tool_list_str, chat_history)` - Classify intent via Brain LLM → return parsed JSON decision. Has tenacity retry protection.

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

- `__init__(self)` - Initialize LLM based on provider (Gemini/DeepSeek/Ollama).
- `generate(self, task, context)` - Generate content for a single task. Strips markdown fences. Has tenacity retry.
- `_strip_markdown_fences(content)` *(module-level)* - Robust markdown fence removal.

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
- `read_file(filename)` *(nested)* - Read file in sandbox.
- `write_file(filename, content)` *(nested)* - Overwrite/create file in sandbox.
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
- `get_market_price(symbol)` *(nested)* - TwelveData real-time price for Forex (EUR/USD), Metals (XAU/USD), Stocks (AAPL). Falls back to Binance for crypto.
- `get_crypto_stats(symbol)` *(nested)* - Binance 24h stats (price, change%, high, low).
- `analyze_crypto_technical(symbol, interval="1h")` *(nested)* - RSI + SMA trend analysis with pandas-ta.

### `skills/external/telegram_ops.py`

- `send_telegram_message(message)` - Send a message via Telegram Bot API. Reads `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` from `.env`. Returns `True`/`False`.

### `core/rag_manager.py`

- `_get_collection()` - Lazy-load ChromaDB client and `all-MiniLM-L6-v2` embedding model. Persistent storage at `ciel_data/vector_memory/`.
- `add_memory(text)` - Archive a conversation snippet into ChromaDB with timestamp metadata.
- `search_similar(query, n_results=3)` - Semantic search. Returns empty list if query < `MIN_QUERY_LENGTH` (15) or all results below `MIN_RELEVANCE_SCORE` (0.65).
- `count_memories()` - Return total number of stored memories.

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

The `backtest/test_integration.py` suite covers 17 test cases:

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

---

## 8) Changelog | Nhật ký thay đổi

### GitHub Manager (May 2026)

- **Added:** `skills/external/github_ops.py` — Git version control tool pack with 5 tools: `git_list_repos`, `git_status`, `git_diff`, `git_commit_and_push` (preview), `git_confirm_push` (execute after confirmation).
- **Modified:** `core/tool_manager.py` — Registered git tools with arg schemas.
- **Safety:** Auto-excludes sensitive files (.env, credentials, tokens) from commits. Push requires explicit Master confirmation via 2-step flow.

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

---

## 9) Roadmap Suggestions | Gợi ý roadmap nâng cấp

- **Finish RAG layer | Hoàn thiện RAG:** ~~implement document ingestion and vector search for knowledge queries.~~ ✅ **DONE** — ChromaDB hybrid memory with automatic archival and semantic recall.
- **Add automated tests | Bổ sung test tự động:** convert smoke scripts to pytest with mocks for API/network calls.
- **Streaming responses | Phản hồi streaming:** implement token-by-token streaming for better UX with cloud providers.
- **Tool confirmation | Xác nhận tool:** add user confirmation step before executing destructive tools (delete, shell).
- **Cost monitoring | Giám sát chi phí:** track API token usage per request and surface cumulative cost.
- **RAG Re-ranking | Xếp hạng lại RAG:** Add a local cross-encoder (e.g., `bge-reranker-base`) to re-score RAG results before sending to Brain. Deferred until memory noise becomes a measurable problem.
- **GitHub Manager | Quản lý GitHub:** ~~Add `skills/external/github_ops.py` for `git_status`, `git_diff`, `git_commit_and_push` with mandatory user approval before push.~~ ✅ **DONE** — 5 tools with 2-step commit safety and deep repo scanner.
- **11 PM Brain Cleanse | Dọn não 23h:** Add nightly scheduled task to flush all short-term memory into RAG and generate a Daily Summary.

---

## 10) Notes | Ghi chú

- This map reflects the current repository contents as of May 2026.
- Bản đồ này phản ánh trạng thái hiện tại của repository tại tháng 5/2026.
- Default provider is Gemini (prepaid credits with monthly spend cap).
- The system supports hot-swapping providers via `.env` without code changes.