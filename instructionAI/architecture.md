# Architecture — Ciel 2.0

Ciel uses a modular Brain-Worker architecture with clean separation of concerns.

## File Tree

```
Ciel-2.0/
├── main.py                       # CLI entry point (input loop + safety callback)
├── main_api.py                   # FastAPI/WebSocket backend for Flutter HUD
├── architect.md                  # Detailed project roadmap and changelog
├── credentials.json              # Google OAuth credentials
├── requirements.txt              # Python dependencies
├── .env                          # API keys, provider config (not tracked in git)
│
├── core/                         # Main orchestration layer
│   ├── agent_loop.py             # Thin wrapper → CielCore.process()
│   ├── llm_connector.py          # CielCore: central pipeline orchestrator
│   ├── router.py                 # Brain-based intent classification (4 actions)
│   ├── recovery_manager.py       # Multi-attempt self-healing (code fix + param fix)
│   ├── rag_manager.py            # ChromaDB vector memory (long-term RAG)
│   ├── scheduler.py              # Proactive background task manager
│   └── tool_manager.py           # Auto-discovery tool registry & execution
│
├── agent_system/                 # Brain-Worker LLM subsystem
│   ├── config.py                 # Provider/model/retry configuration
│   ├── main.py                   # Standalone LangGraph runner
│   ├── models/
│   │   ├── brain.py              # Brain LLM (Router + Planner)
│   │   └── worker.py             # Worker LLM (Code/Text generator)
│   ├── graph/
│   │   ├── state.py              # AgentState TypedDict
│   │   ├── nodes.py              # brain_node, worker_node, file_write_node
│   │   ├── edges.py              # Conditional routing edges
│   │   └── builder.py            # LangGraph compilation
│   ├── tools/
│   │   └── buffer_writer.py      # In-memory code buffer → flush to disk
│   └── utils/
│       └── logger.py             # Colored console logger
│
├── skills/                       # Tool packs (auto-discovered by ToolManager)
│   ├── _result.py                # Shared result schema
│   ├── internal/
│   │   ├── memory_ops.py         # Fact vault (standalone JSON, no deps)
│   │   ├── system_ops.py         # Workspace file CRUD + Python runner
│   │   ├── os_ops.py             # Shell, screenshot, app launcher
│   │   └── vision_ops.py         # Gemini Vision + PyAutoGUI grid overlay
│   └── external/
│       ├── gmail_ops.py          # Gmail toolkit + custom ops
│       ├── trading_ops.py        # Crypto, Forex, Metals price + TA
│       ├── telegram_ops.py       # Telegram Bot API notifications
│       ├── github_ops.py         # Git status, diff, commit, push
│       └── web_agent_ops.py      # Web scraping / search
│
├── persona/                      # Personality
│   └── official_ciel_personality.txt  # The only persona file loaded at startup. Legacy fragments
│                                 # (identity/directives/format.txt) merged in + removed July 9, 2026.
│
├── backtest/                     # Test suites
│   ├── test_integration.py       # 21-test full pipeline validation
│   ├── test_brain_worker.py      # Multi-step workflow tests
│   └── test_rag_memory.py        # 45-prompt amnesia stress test
│
├── scripts/                      # Maintenance helpers
│   └── format_thoughts_log.py    # Generate Markdown/JSONL views of thoughts.log
│
├── ciel_data/                    # Runtime data (persisted across restarts)
│   ├── facts.json                # Fact vault (key-value)
│   ├── memory_bank.json          # Short-term chat history (max 20 messages)
│   ├── vector_memory/            # ChromaDB persistent storage (long-term)
│   └── logs/
│       └── thoughts.log          # Raw audit trail (never modify format)
│
├── ciel_workspace/               # Sandbox for user scripts (quarantined)
└── agent_output/                 # Generated code output directory
```

## Internal Dependency Graph

```
main.py → core.agent_loop.AgentLoop
core.agent_loop → core.llm_connector.CielCore

CielCore initializes:
  ├── agent_system.models.brain.Brain        (Router LLM)
  ├── agent_system.models.worker.Worker      (Generator LLM)
  ├── core.router.Router                     (Intent classification)
  ├── core.recovery_manager.RecoveryManager  (Self-healing)
  ├── core.rag_manager                       (Hybrid memory)
  └── core.tool_manager.ToolManager          (Tool registry)
       └── skills.internal.* + skills.external.*  (auto-discovered)

main.py also initializes:
  └── core.scheduler.CielScheduler           (Background tasks)
       └── Directly calls Gmail API + TwelveData API + Telegram API

skills.internal.memory_ops → standalone (reads/writes ciel_data/facts.json directly)
```

## Runtime Flow (Primary Pipeline)

```
User Input → CielCore.process()
  → RAG Recall: search ChromaDB (skipped if query < 15 chars or relevance < 0.65)
  → RAG Compression: Tier-1 regex/structural → Tier-2 Worker (if still > ~1000 tokens)
  → Inject recalled context into user prompt
  → Router (Brain LLM) classifies intent → JSON with hidden_thought + action
  → Based on action:
      "chat"       → Worker generates natural response
      "tool"       → Safety Gate check → ToolManager executes → Worker formats
                     → Self-Correction: Brain evaluates → retries if unsatisfied (max 2)
      "code"       → Worker generates code → buffer_writer → disk
      "multi_tool" → Sequential tool execution → Worker synthesizes report
  → Self-Healing Loop (if error):
      Attempt 1: Fix obvious cause (syntax/import)
      Attempt 2: Rewrite with alternative approach
      Attempt 3: Full rewrite using stdlib only
  → _trim_history(): overflow messages → archived into ChromaDB
  → Response saved → returned to user
```

## Multi-Provider Support

| Provider | Brain Model | Worker Model | Config Key |
|----------|-------------|--------------|------------|
| **Gemini** (default) | `gemini-2.5-pro` | `gemini-2.5-flash` | `GEMINI_API_KEY` |
| **DeepSeek** | configurable | configurable | `DEEPSEEK_API_KEY` |
| **Ollama** (local) | configurable | configurable | `OLLAMA_BASE_URL` |

Provider is set via `BRAIN_PROVIDER` and `WORKER_PROVIDER` in `.env`. No code changes needed to switch.

## Two Entry Points

- **`main.py`** — CLI loop. Blocking `input("Y/N")` for safety confirmations.
- **`main_api.py`** — FastAPI + WebSocket. Streams `thoughts.log` lines and vitals to Flutter HUD. Safety confirmations sent as JSON over WebSocket with 60s timeout.
