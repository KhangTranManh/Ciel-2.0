# Architecture — Ciel 2.0

Ciel uses a modular **Brain → Middleware → Worker** architecture with clean separation of concerns.

## File Tree

```
Ciel-2.0/
├── main.py                       # CLI entry point (input loop, safety callback, --voice/--speak)
├── main_api.py                   # FastAPI/WebSocket backend: WS /ws, GET /skills /health, POST /tts
├── architect.md                  # Detailed project roadmap and changelog (dated)
├── note.md                       # Live status / rolling changelog (dated)
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
│       └── web_agent_ops.py      # stealth_search (Google News RSS → ddgs fallback) + smart_scrape

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
                     → dependent steps: {{prev}} / {{step_N}} in a later step's args are
                       replaced with an earlier step's raw output (deterministic, no LLM)
                     → TIER-1 AGENT LOOP (core/continuation.py) — see below
                     → deferred write/send body filled with the synthesized report; any
                       leftover synthesis placeholder is blocked (see safety_and_risk.md)
                     → explicit "subject exactly '...'" is enforced onto the send step
  → Self-Healing Loop (if error, and not on the unfixable skip-list):
      Attempt 1: Fix obvious cause (syntax/import)
      Attempt 2: Rewrite with alternative approach
      Attempt 3: Full rewrite using stdlib only
  → _trim_history(): overflow messages → archived into ChromaDB
  → Response saved → returned to user → optionally spoken aloud (TTS)
```

## Tier-1 Agent Loop (`core/continuation.py`, added July 2026)

A plan is a **flat list of tool calls chosen before anything runs**, so a request like
*"check git status, and if it's clean, commit"* is not merely hard to plan — it is
**structurally unrepresentable**. The loop closes that gap: execute → observe → re-plan.

```
execute round-0 steps  →  records = [(tool, args, result), …]
        ↓
ContinuationPolicy.assess()          ← pure Python, ZERO tokens
        ↓ only if a signal fires
Router.route(request + observations) ← 1 planner call, reuses the SAME plan schema
        ↓
novel_steps()  → drop anything already executed   ← no-progress guard
        ↓
execute fresh steps  → append to records → loop (bounded)
        ↓
Worker synthesizes over ALL rounds, then the deferred send/write runs ONCE
```

**Five signals may open a round.** All but the last are properties of the request or of
what actually came back — never a model self-report:

| | Signal | Example |
|---|---|---|
| S1 | Request is conditional | "nếu sạch **thì** commit", "only if the tests pass" |
| S2 | An unresolved `{step_N}` / `{{step_N}}` reached a real tool | the single-brace bug — the tool ran on literal `{step_1}` |
| S3 | A step failed while **later** steps still ran | read_file 404s, the summary step then invents content |
| S4 | Fan-out over a set of unknown size | "liệt kê file rồi đọc **từng** file" — 4 listed, 1 step ran |
| S5 | Planner set the optional `"needs_followup": true` | strictly opt-in; omitting it breaks nothing |

**Bounds are enforced in code, not by model good behaviour** (`LoopBudget`): extra rounds
(`AGENT_LOOP_MAX_ROUNDS`, default 2), planner calls, wall-clock
(`AGENT_LOOP_MAX_SECONDS`, default 120 — tested **before every step**, not merely between
rounds), and `max_steps_per_round` (4). It is **fail-open**: any exception, unparseable
re-plan, or empty follow-up quietly keeps the first round's answer.

Off-switch: `AGENT_LOOP_ENABLED=false` restores the exact pre-loop behaviour.

**Cost on ordinary requests is exactly zero extra calls** — `assess()` is free Python, and
a non-conditional request returns `False` before any LLM is touched. Verified: an 11-case
regression spent 8 Brain calls with the loop on, the same 8 it spent with it off.

## Tier-3 Permissions (`core/permissions.py`, added July 2026)

Approval used to be binary and per-call: a tool was either in `_HIGH_RISK_TOOLS` (asked
every single time) or free. Worse, the prompt arrived **mid-execution** — declining at
step 3 of 4 left steps 1-2 already carried out, so the Master was approving fragments.

Every `(tool, args)` now resolves to one of three decisions:

| | Meaning |
|---|---|
| `AUTO` | Read-only. Never interrupts. Explicit list + `CIEL_AUTO_TOOLS`. |
| `ASK` | Has an effect. Needs confirmation. Source of truth stays `_HIGH_RISK_TOOLS`. |
| `DENY` | Refused outright, not even offered as a prompt. `CIEL_DENY_TOOLS`. |

**Plan-level approval** (`_review_plan_permissions`, called before the first step of a
multi_tool plan): one prompt listing every step, answered once. A "no" means **nothing
ran**. A deny-listed step aborts the plan without asking at all.

**Two grant scopes, deliberately different:**
- *session* — by tool name, opt-in via `A` at the CLI prompt, never written to disk.
- *plan* — by **`(tool + exact args)` signature**, cleared when the plan ends. Keyed on
  the whole call on purpose: approving `delete_file` for the reviewed plan must not
  auto-approve a *different* `delete_file` that the Tier-1 loop invents two rounds later
  and the Master never saw. It also makes a stale grant harmless — it can only ever
  re-approve the identical action.

`DENY` is checked first and cannot be overridden by a session grant, a plan approval, or
`DISABLE_SAFETY_GATE`. The `AUTO` set is kept in agreement with `core/parallel.py`'s
parallel-safe set (a test asserts no parallel-safe tool needs approval).

## Tier-2 Task State (`core/task_state.py`, added July 2026)

Before this the only cross-turn state was ONE slot holding ONE action awaiting
confirmation, so a job interrupted by a crash, a restart or a closed terminal simply
vanished, and "what were you doing?" could only be answered by the model guessing —
the Router never sees `chat_history`, and such short inputs fall below RAG's
`MIN_QUERY_LENGTH`.

```
TaskRecord: id · goal · status · steps[] · note · created/updated
status:     active → done | blocked | failed | interrupted | cancelled
file:       ciel_data/state/tasks.json   (rolling, newest 20)
```

- **Opened** in `process()` only for `tool` / `multi_tool` / `code` — pure chat can leave
  nothing half-finished, so it gets no record.
- **Steps recorded** where they already happen (`_run_steps`, and the single-tool path).
  `records` (the loop's `StepRecord` list) held this data already; Tier 2 gives it an
  owner and a home rather than duplicating it into a second list that can drift.
- **Closed** at every exit: `done`, `failed` on exception, or **`blocked`** when a
  confirmation was staged — waiting on the Master is not completion.
- **Resumption.** Any record still `active` at load time must belong to a process that no
  longer exists (the constructor only runs at start-up), so it is reclassified
  `interrupted`. `main.py` prints it on launch; `describe_unfinished()` exposes it.
- **`đang làm gì` / `status` / `what were you doing`** is matched by `_STATUS_QUERY_RE`
  before routing and answered from the store — **0 LLM calls, ~0.1s** (measured).

**The Brain never reads these records.** Feeding them into routing would re-open the
cross-request contamination this codebase deliberately closed, and cost tokens every
turn. They exist for the Master and for resumption; every decision is plain Python.

Thread-safe: `_run_steps` appends from parallel worker threads, so every mutation holds a
lock (verified with 6 threads × 25 steps — 150/150 kept, no duplicate ordinals).

Deliberately **not** a scheduler, queue or priority system: one active task at a time,
matching the pending-confirmation slot and how conversation actually works.

## Parallel Tool Execution (`core/parallel.py`, added July 2026)

Steps in one plan that are **provably independent** run concurrently instead of one
after another. `plan_batches()` walks the steps in order and only ever *groups* them —
it never reorders or drops any — so results are collected back in the original order and
`{prev}` / `{step_N}` keep meaning exactly what they meant sequentially.

A step may share a batch only if **all three** hold:
1. its tool is declared parallel-safe, 2. its args carry no `{step_N}`/`{prev}`
reference, 3. it is not high-risk (those block on a Y/N prompt — several threads racing
for one stdin is a deadlock, not a speed-up).

**Opt-in, never a blacklist.** Skills auto-register, so "parallelise everything except
writes/sends" would silently parallelise a new mutating tool the day someone adds one.
Built-ins live in `_DEFAULT_PARALLEL_SAFE`; a skill adds its own:

```python
def get_my_tools():
    return {"tools": [...], "prompt": "...", "parallel_safe": ["my_search"]}
```

`_log_thought` takes a lock: it appends to the shared `thoughts.log` **and** accumulates
the per-tier token/cost dicts, so concurrent callers would otherwise interleave mid-entry
(breaking the format `scripts/cost_report.py` parses) and lose counter increments.

**Measured, tool time only** (LLM excluded — it dominates wall-clock and hides this):
7 file reads 9.3×, 4 mixed fast tools 1.9×, 4 web scrapes 2.3×, 3 searches 1.05× (the
search source rate-limits, so concurrency cannot help there). In absolute terms that is
0.15–2.8s against requests that take 15–55s, i.e. **invisible on ordinary requests** and
worth real minutes only on slow-network fan-out. Off-switch: `AGENT_PARALLEL_ENABLED=false`.

## Multi-Provider Support

Each tier (Brain / Worker / Middleware) selects its provider and model independently via
`.env` — no code changes to switch. Current recommended setup:

| Tier | Provider | Model | Config Key |
|------|----------|-------|------------|
| **Brain (Router)** | Vilao | `ccf/claude-opus-4-8` | `BRAIN_PROVIDER`, `BRAIN_MODEL` |
| **Worker (Generator)** | Vilao | `op/deepseek/deepseek-v4-pro` (via `CODER_MODEL`, not `WORKER_MODEL`) | `WORKER_PROVIDER`, `CODER_MODEL` |
| **Middleware (Verifier, optional)** | Vilao | `op/deepseek/deepseek-v4-pro` | `MIDDLEWARE_PROVIDER`, `MIDDLEWARE_ENABLED` |

> Provider model names drift — DeepSeek retired `deepseek-chat` (now `deepseek-v4-pro` /
> `deepseek-v4-flash`); Vilao model aliases (e.g. `awkr/…` → `ccf/…`) change too. If a tier returns
> empty output or a 4xx, check the alias is still live before suspecting the code.

Also supported per tier: Gemini, Ollama (fully offline), and Vilao. **All three tiers can run on
Vilao** — the Worker gained a `WORKER_PROVIDER=vilao` branch (`agent_system/models/worker.py`,
mirroring the Brain's; previously provider=vilao silently fell through to the Ollama-localhost
branch and failed with a connection-refused). A verified pure-Vilao setup: Brain
`ccf/claude-opus-4-8`, Worker + Middleware `op/deepseek/deepseek-v4-pro`. **Tested fallback:** if
Vilao is unavailable, set `BRAIN_PROVIDER=deepseek` + a valid DeepSeek model — zero code changes.

> **Gotcha (unchanged):** the Worker's MODEL is read from `CODER_MODEL`, never `WORKER_MODEL`
> (`agent_system/config.py`); its PROVIDER is `WORKER_PROVIDER` (`CODER_PROVIDER` is not read).

## Web Search Source Chain (`skills/external/web_agent_ops.py`)

`stealth_search` is layered, because the original single DuckDuckGo `text()` call returned
no publication dates, surfaced section landing pages instead of articles, and honored its
own `timelimit` unreliably (a "past day" window still returned 8–16-day-old hits):

```
news-intent query
  → 1. Google News RSS          PRIMARY — free, no API key, no extra library
       ├─ generic "what's the news" query  → TOP STORIES feed (no q=)
       └─ query names a topic              → keyword search (q=)
       locale (hl/gl/ceid) picked from the QUERY's language: vi/VN, en-US/US, ja/JP, …
  → 2. ddgs.news()              fallback if RSS fails/empty (also dated)
  → 3. ddgs.text()              non-news queries, or to top up
then: recency filter on the REAL pubDate → landing-page filter → trim to max_results
```

Key points for anyone changing this:
- **Top stories vs keyword search matters.** Searching "top news headlines today" matches
  articles *titled* that (roundups: "School Assembly News Headlines"); the top-stories feed
  returns the actual lead stories. `_is_generic_news_query()` decides between them.
- **Language drives the locale.** A Vietnamese query must hit Vietnamese outlets — see the
  `[USER LANGUAGE: X]` note the Router receives (`CielCore._detect_language`).
- Every result prints `Published` + `Source`; undated web results are explicitly labeled
  `UNKNOWN … do NOT state a date` so the model cannot invent one.
- Google News RSS is an UNOFFICIAL endpoint (like edge-tts) — keep the ddgs fallback.

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
