# Ciel 2.0 Project Roadmap (EN + VI)

This document is a full project map for architecture, dependencies, and functions.
Tài liệu này là bản đồ đầy đủ của dự án: kiến trúc file, phụ thuộc và toàn bộ hàm.

---

## 1) High-Level Architecture | Kiến trúc tổng thể

- **Entry point | Điểm vào:** `main.py` starts CLI loop and delegates each command to `AgentLoop`.
- **Core orchestration | Điều phối lõi:** `core/agent_loop.py` routes command intent (`CHAT` vs `CODE`), executes tools, then reflects on tool output.
- **LLM bridge | Cầu nối LLM:** `core/llm_connector.py` initializes local Router/Coder models (Ollama), optional Gemini fallback, prompt assembly, and chat memory.
- **Tool registry/execution | Kho công cụ & thực thi:** `core/tool_manager.py` loads internal + external tool packs, stitches tool manuals, executes by tool name.
- **Memory layer | Tầng bộ nhớ:** `core/memory_manager.py` stores/retrieves fact vault (`facts.json`) and provides context string for prompts.
- **Tool packs | Các gói kỹ năng:**
  - Internal: workspace/file ops (`skills/internal/system_ops.py`), host OS control (`skills/internal/os_ops.py`), fact tools (`skills/internal/memory_ops.py`)
  - External: Gmail toolkit/extensions (`skills/external/gmail_ops.py`), crypto/trading toolkit (`skills/external/trading_ops.py`)
- **Testing scripts | Script kiểm thử:** `backtest/` and `test.py` validate APIs and end-to-end tool behavior.
- **Workspace sandbox | Vùng workspace:** `ciel_workspace/` contains files created/tested by tools.

---

## 2) Runtime Flow | Luồng chạy

1. User enters command in `main.py`.
2. `AgentLoop.run_step()` stores user message, asks `_is_coding_task()` router.
3. `CielCore.chat_with_tools(..., use_coder=...)` invokes selected LLM with bound tools.
4. If tool calls exist (native or extracted from text), `ToolManager.execute_tool()` runs them.
5. Tool outputs are injected into a reflection prompt for final answer generation.
6. Final response is parsed from `<RESPONSE>...</RESPONSE>`, logged (if DEBUG), and returned.

---

## 3) File Architecture (Current) | Cấu trúc file hiện tại

```text
Ciel 2.0/
├── .gitignore
├── architect.md
├── credentials.json
├── main.py
├── requirements.txt
├── test.py
├── core/
│   ├── agent_loop.py
│   ├── llm_connector.py
│   ├── memory_manager.py
│   └── tool_manager.py
├── skills/
│   ├── external/
│   │   ├── gmail_ops.py
│   │   └── trading_ops.py
│   └── internal/
│       ├── memory_ops.py
│       ├── os_ops.py
│       └── system_ops.py
├── persona/
│   ├── directives.txt
│   ├── format.txt
│   └── identity.txt
├── backtest/
│   ├── test_google.py
│   ├── test_indicators.py
│   ├── test_os_ops_integration.py
│   ├── test_trading_api.py
│   └── testapi.py
└── ciel_workspace/
    ├── calculate_sum.py
    ├── chào.txt
    ├── code.txt
    ├── loop_test.py
    ├── sum_1_to_100.py
    └── test_hermes.txt
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
  - `langgraph`
- **Data / TA**
  - `pandas`
  - `pandas-ta`
- **Memory / Vector**
  - `chromadb`
  - `sentence-transformers`
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

### 4.2 Key runtime services / credentials

- **Local models via Ollama:** `LOCAL_MODEL`, `CODER_MODEL`
- **Gemini fallback:** `GEMINI_API_KEY`, `GEMINI_MODEL`
- **Trading (MEXC):** `MEXC_API_KEY`, `MEXC_API_SECRET`
- **Google OAuth:** `credentials.json`, token files under `ciel_data/`

### 4.3 Internal dependency graph

- `main.py` -> `core.agent_loop.AgentLoop`
- `core.agent_loop` -> `core.llm_connector.CielCore`
- `core.llm_connector` -> `core.memory_manager.MemoryManager`, `core.tool_manager.ToolManager`
- `core.tool_manager` -> internal/external skill modules
- `skills.internal.memory_ops` -> `core.memory_manager.MemoryManager`

---

## 5) Complete Function Inventory | Danh mục toàn bộ hàm

> Includes top-level functions, class methods, and nested tool functions.
> Bao gồm hàm top-level, method trong class, và hàm tool lồng bên trong.

### `main.py`

- `main()` - Boot CLI app, create `AgentLoop`, read user input loop, execute `run_step()`.

### `core/agent_loop.py` (`class AgentLoop`)

- `__init__(self)` - Init core connector, debug flag, thought log path.
- `_log_interaction(self, user_input, thought, response, tool_used=None)` - Write debug interaction log.
- `_is_coding_task(self, user_input)` - Intent router (`CODE` vs `CHAT`) using router model + heuristic fallback.
- `_extract_tool_calls_from_text(self, raw_content, available_tools)` - Parse fallback tool calls from `<ACTION>` or `Action:`.
- `_safe_parse_tool_args(self, args_str, target_tool)` - Safely parse tool args via `ast`, JSON, scalar mapping.
- `_looks_like_tool_intent(self, user_input)` - Heuristic keyword detector for likely tool requests.
- `_contains_refusal_phrase(self, text)` - Detect refusal patterns.
- `run_step(self, user_input)` - Full cycle: route model, tool execution loop, reflection prompt, response parse, memory save.

### `core/llm_connector.py` (`class CielCore`)

- `__init__(self)` - Build tool manager, initialize Router/Coder LLMs, optional Gemini fallback, prompt and memory.
- `_trim_history(self)` - Keep chat history within max length.
- `_build_prompt(self)` - Compose system prompt from persona fragments + tool manuals + fact vault.
- `load_fragment(filename, default_text="")` *(nested)* - Read persona fragment file.
- `_load_chat_memory(self)` - Load saved messages, filter toxic/refusal patterns.
- `_save_chat_memory(self)` - Persist chat history JSON.
- `chat_with_tools(self, user_input, use_coder=False)` - Invoke selected LLM chain with memory + facts.

### `core/memory_manager.py` (`class MemoryManager`)

- `__init__(self)` - Init memory directory and fact file path.
- `_init_vault(self)` - Create empty `facts.json` when missing.
- `save_fact(self, key, value)` - Upsert fact.
- `get_fact(self, key)` - Return fact or `NOT_FOUND`.
- `delete_fact(self, key)` - Remove fact key.
- `get_all_facts_context(self)` - Build formatted fact context string for prompts.
- `ingest_file(self, file_path)` - Placeholder for future RAG ingestion.
- `query_file_knowledge(self, query)` - Placeholder for future RAG query.

### `core/tool_manager.py` (`class ToolManager`)

- `__init__(self)` - Initialize tool registries and load tool zones.
- `_load_internal_tools(self)` - Load memory/system/os internal tool packs and prompts.
- `_load_external_tools(self)` - Load Gmail + trading tool packs and prompts.
- `get_tools(self)` - Return tool list and refresh name->tool map.
- `get_dynamic_prompt(self)` - Concatenate all tool manuals for system prompt.
- `execute_tool(self, name, args)` - Invoke tool by name with error handling.

### `skills/internal/memory_ops.py`

- `save_fact(key, value)` - Tool wrapper for saving facts.
- `delete_fact(key)` - Tool wrapper for deleting facts.

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

- `get_contract_info(symbol)` - Fetch MEXC contract size + fair price.
- `get_trading_tools()` - Build trading tools and return with system prompt.
- `get_crypto_price(symbol)` *(nested)* - Binance realtime symbol price.
- `get_24h_stats(symbol)` *(nested)* - Binance 24h stats.
- `analyze_technical_indicators(symbol, interval="1h")` *(nested)* - RSI + SMA trend analysis with pandas-ta.
- `get_mexc_portfolio()` *(nested)* - Query MEXC spot/futures balances and open positions with computed PnL.

### `test.py`

- `run_backtest()` - Execute scripted end-to-end prompts across memory/system/gmail/trading toolsets and log results.

### `backtest/test_google.py`

- `test_google_connection()` - OAuth + Calendar API connectivity smoke test.

### `backtest/test_indicators.py`

- `run_technical_backtest(symbol="SOLUSDT", interval="1h", limit=100)` - Pull klines and compute RSI/EMA diagnostics.

### `backtest/test_os_ops_integration.py`

- `main()` - Verify OS tools load + smoke test shell and screenshot operations.

### `backtest/test_trading_api.py`

- `get_contract_info(symbol)` - Fetch MEXC contract size/fair price.
- `test_mexc_full_portfolio()` - Spot/futures scan and PnL breakdown from MEXC APIs.

### `backtest/testapi.py`

- `test_ciel_connection()` - Direct Gemini API connection test (non-LangChain).

### Script-only files in `ciel_workspace/`

- `calculate_sum.py` - arithmetic print script (no function).
- `loop_test.py` - loop print script (no function).
- `sum_1_to_100.py` - aggregate sum script (no function).

---

## 6) Roadmap Suggestions | Gợi ý roadmap nâng cấp

- **Unify duplicated logic | Gộp logic trùng:** `get_contract_info()` appears in both trading module and test module.
- **Strengthen security | Tăng bảo mật:** avoid storing secrets in plain files; add secret scanning and stricter command allowlist.
- **Stabilize tool contracts | Ổn định contract tool:** define typed schemas for nested tool functions and central error model.
- **Finish RAG layer | Hoàn thiện RAG:** implement `ingest_file()` and `query_file_knowledge()`.
- **Add automated tests | Bổ sung test tự động:** convert smoke scripts to pytest with mocks for API/network calls.

---

## 7) Notes | Ghi chú

- This map reflects the current repository contents at scan time.
- Bản đồ này phản ánh trạng thái hiện tại của repository tại thời điểm quét.