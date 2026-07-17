# Architecture — Ciel 2.0

Ciel uses a modular **Brain → Middleware → Worker** architecture with clean separation of concerns.

## File Tree

```
Ciel-2.0/
├── main.py                       # CLI entry point (input loop, safety callback, --voice/--speak)
├── main_api.py                   # FastAPI/WebSocket backend: WS /ws, GET /skills /health, POST /tts
├── architect.md                  # Detailed project roadmap and changelog (dated)
├── note.txt                      # Live status / rolling changelog (dated)
├── credentials.json              # Google OAuth credentials (not tracked in git)
├── requirements.txt              # Python dependencies
├── .env                          # API keys, provider config (not tracked in git)
│
├── core/                         # Main orchestration layer
│   ├── agent_loop.py             # Thin wrapper → CielCore.process()
│   ├── llm_connector.py          # CielCore: central pipeline orchestrator
│   ├── router.py                 # Brain-based intent classification (4 actions)
│   ├── middleware.py             # (wiring) Middleware tier hookup — see agent_system/models/middleware.py
│   ├── recovery_manager.py       # Multi-attempt self-healing (code fix + param fix + skip-list)
│   ├── rag_manager.py            # ChromaDB vector memory (long-term RAG)
│   ├── scheduler.py              # Proactive background task manager (Ghost Mode)
│   ├── tool_manager.py           # Auto-discovery tool registry & execution
│   ├── cost.py                   # LLM pricing table + estimate_cost() (overridable via ciel_data/model_pricing.json)
│   ├── voice_input.py            # CLI speech-to-text (sounddevice + google/whisper/gemini backends)
│   └── speech_output.py          # CLI/UI text-to-speech (to_speech() normalizer + edge/pyttsx3/space backends)
│
├── agent_system/                 # Brain-Worker-Middleware LLM subsystem
│   ├── config.py                 # Provider/model/retry configuration (per tier)
│   ├── main.py                   # Standalone LangGraph runner
│   ├── models/
│   │   ├── brain.py              # Brain LLM (Router + Planner)
│   │   ├── worker.py             # Worker LLM (Code/Text generator)
│   │   └── middleware.py         # Middleware LLM (semantic verifier/finalizer, email-scoped)
│   ├── graph/
│   │   ├── state.py              # AgentState TypedDict
│   │   ├── nodes.py              # brain_node, worker_node, file_write_node
│   │   ├── edges.py              # Conditional routing edges
│   │   └── builder.py            # LangGraph compilation
│   ├── tools/
│   │   └── buffer_writer.py      # In-memory code buffer → flush to disk
│   └── utils/
│       ├── logger.py             # Colored console logger
│       └── usage.py              # extract_usage()/format_usage() — provider token counts for cost tracking
│
├── skills/                       # Tool packs (auto-discovered by ToolManager)
│   ├── _result.py                # Shared result schema
│   ├── internal/
│   │   ├── memory_ops.py         # Fact vault (standalone JSON, no deps)
│   │   ├── system_ops.py         # Workspace file CRUD + Python runner + PDF/DOCX reader
│   │   ├── os_ops.py             # Shell, screenshot, app launcher
│   │   ├── productivity_ops.py   # Todos, time, weather, calculate, grep_in_workspace
│   │   └── vision_ops.py         # Gemini Vision + PyAutoGUI grid overlay
│   └── external/
│       ├── gmail_ops.py          # Gmail toolkit + custom ops (send/html/reply/draft/trash/search)
│       ├── trading_ops.py        # Crypto, Forex, Metals price + TA + build_market_report_html
│       ├── telegram_ops.py       # Telegram Bot API notifications
│       ├── github_ops.py         # Git status, diff, commit, push
│       └── web_agent_ops.py      # stealth_search (recency-aware) + smart_scrape

├── persona/                      # Personality
│   └── official_ciel_personality.txt  # The only persona file loaded at startup
│
├── ui/                            # React + Tauri v2 frontend (optional, browser-first)
│   └── src/
│       ├── orb.ts                #   Three.js audio-reactive particle orb (framework-agnostic)
│       ├── components/Orb.tsx    #   React wrapper driving the orb from conversation state
│       ├── core/                 #   transport+protocol (ws, bus, types, http) — modality-agnostic
│       ├── io/                   #   MODALITY LAYER: input/ (text + voice mic), output/ (transcript, speaker+analyser)
│       └── hooks/useCiel.ts      #   bus <-> React bridge
│
├── backtest/                     # Test suites (script-based, not pytest — see conventions.md)
│   ├── test_integration.py       # Full pipeline validation
│   ├── test_brain_worker.py      # Multi-step workflow tests
│   ├── test_rag_memory.py        # Amnesia stress test
│   └── test_hard_special.py      # Hard/special cases + BUG DASHBOARD
│
├── scripts/                      # Maintenance helpers
│   ├── format_thoughts_log.py    # Generate Markdown/JSONL views of thoughts.log
│   ├── prompt_harness.py         # Mine thoughts.log for recurring failure patterns
│   └── cost_report.py            # Cumulative LLM cost/usage report (by tier/model/day)
│
├── ciel_data/                    # Runtime data (persisted across restarts)
│   ├── facts.json                # Fact vault (key-value)
│   ├── memory_bank.json          # Short-term chat history (max 20 messages)
│   ├── model_pricing.json        # Optional cost-tracking price overrides (no code change)
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
  ├── agent_system.models.brain.Brain             (Router LLM)
  ├── agent_system.models.worker.Worker           (Generator LLM)
  ├── agent_system.models.middleware.Middleware    (Semantic verifier — optional, lazy)
  ├── core.router.Router                          (Intent classification)
  ├── core.recovery_manager.RecoveryManager        (Self-healing)
  ├── core.rag_manager                            (Hybrid memory)
  └── core.tool_manager.ToolManager                (Tool registry)
       └── skills.internal.* + skills.external.*  (auto-discovered)

main.py also initializes:
  └── core.scheduler.CielScheduler                (Background tasks, Ghost Mode)
       └── Directly calls Gmail API + TwelveData API + Telegram API

main.py (voice mode) also touches:
  └── core.voice_input / core.speech_output        (STT/TTS — lazy imports, fail-open)

skills.internal.memory_ops → standalone (reads/writes ciel_data/facts.json directly)
```

## Runtime Flow (Primary Pipeline)

```
User Input (text or voice transcript) → CielCore.process()
  → RAG Recall: search ChromaDB (skipped if query < 15 chars or relevance < 0.65)
  → RAG Compression: Tier-1 regex/structural → Tier-2 Worker (if still > ~1000 tokens)
  → Inject recalled context into user prompt
  → Router (Brain LLM) classifies intent → JSON with hidden_thought + action
  → Based on action:
      "chat"       → Worker generates natural response
      "tool"       → Dangerous-code / high-risk gate → Safety Gate (Y/N) → ToolManager executes
                     → outbound email body: sanitize → Middleware review (email-scoped, fail-open) → send
                     → Worker formats non-email results
                     → Self-Correction: Brain evaluates → retries if unsatisfied (max 2)
      "code"       → Worker generates code → dangerous-code gate → buffer_writer → disk
      "multi_tool" → Sequential tool execution → Worker synthesizes report (before final send)
  → Self-Healing Loop (if error, and not on the unfixable skip-list):
      Attempt 1: Fix obvious cause (syntax/import)
      Attempt 2: Rewrite with alternative approach
      Attempt 3: Full rewrite using stdlib only
  → _trim_history(): overflow messages → archived into ChromaDB
  → Response saved → returned to user → optionally spoken aloud (TTS)
```

## Multi-Provider Support

Each tier (Brain / Worker / Middleware) selects its provider and model independently via
`.env` — no code changes to switch. Current recommended setup:

| Tier | Provider | Model | Config Key |
|------|----------|-------|------------|
| **Brain (Router)** | Vilao | `alic/qwen3.7-max` | `BRAIN_PROVIDER`, `BRAIN_MODEL` |
| **Worker (Generator)** | DeepSeek | `deepseek-chat` (via `CODER_MODEL`, not `WORKER_MODEL`) | `WORKER_PROVIDER`, `CODER_MODEL` |
| **Middleware (Verifier, optional)** | mirrors Brain's branching | disabled by default | `MIDDLEWARE_PROVIDER`, `MIDDLEWARE_ENABLED` |

Also supported per tier: Gemini, Ollama (fully offline). **Tested fallback:** if Vilao (Brain)
is unavailable, set `BRAIN_PROVIDER=deepseek` + `BRAIN_MODEL=deepseek-chat` — verified live, zero
code changes, since `DEEPSEEK_API_KEY` is already configured for the Worker.

## Two Entry Points

- **`main.py`** — CLI loop. Blocking `input("Y/N")` for safety confirmations. `--voice` (speak
  requests) and `--speak` (hear replies) flags; `:v` for a one-off voice capture.
- **`main_api.py`** — FastAPI + WebSocket. Streams `thoughts.log` lines and vitals (incl. live
  token/cost) to the React/Tauri UI. Safety confirmations sent as JSON over WebSocket with 60s
  timeout. `POST /tts` powers the UI's read-aloud toggle using the same voice engine as the CLI.

## Interface Layer (optional)

The React + Tauri v2 UI (`ui/`) is browser-first and desktop-wrappable with no code changes
between the two. Layout: skills panel (left) · an audio-reactive Three.js particle orb (center,
the centerpiece — reflects idle/listening/thinking/speaking and pulses to the real TTS voice) ·
conversation (right). The skill list is 100% backend-driven (`GET /skills`) so adding a tool pack
needs zero frontend edits. See `voice_and_interface.md` for the voice/orb design.
