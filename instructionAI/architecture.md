# Architecture — Ciel 2.0

Ciel uses a **Brain → Router → Middleware → Worker** core, extended by seven
agent-capability tiers. One rule runs through every tier: **the decision is
deterministic Python; the model only plans or composes.** That is what lets each tier
survive a change — or a downgrade — of model.

## File Tree

```
Ciel-2.0/
├── main.py                       # CLI entry point (input loop, safety callback, --voice/--speak)
├── main_api.py                   # FastAPI/WebSocket backend: WS /ws, GET /skills /health, POST /tts
├── main_telegram.py               # Telegram bot entry point — thin wrapper around core.telegram_interface
├── architect.md                  # Detailed project roadmap and changelog (dated)
├── note.md                       # Live status / rolling changelog (dated)
├── credentials.json              # Google OAuth credentials (not tracked in git)
├── requirements.txt               # Python dependencies
├── .env                           # API keys, provider config (not tracked in git)
├── docker/                        # Container deployment — see "Docker Deployment" below
│   ├── Dockerfile                  # Shared image for both docker-compose files below
│   ├── docker-compose.api.yml      # ciel-api service (main_api.py, Vercel-facing)
│   ├── docker-compose.telegram.yml # ciel-telegram service (main_telegram.py)
│   ├── requirements-docker.txt     # Slimmed deps — no pyautogui/pyperclip/voice-input libs; CPU-only
│   │                                torch (keeps sentence-transformers, the real vector_memory/'s
│   │                                embedding fn — ChromaDB won't accept swapping it at runtime)
│   └── README.md                   # Build/run instructions, shared-state caveat
│
├── core/                          # Main orchestration layer — every request goes through this
│   ├── agent_loop.py              # Thin wrapper → CielCore.process()
│   ├── llm_connector.py           # CielCore: central pipeline orchestrator
│   ├── router.py                  # Brain-based intent classification (4 actions)
│   ├── middleware.py              # Middleware tier hookup (agent_system/models/middleware.py does the work)
│   ├── recovery_manager.py        # Multi-attempt self-healing (code fix + param fix + skip-list)
│   ├── rag_manager.py             # ChromaDB vector memory (long-term RAG) — embedding via
│   │                                SentenceTransformerEmbeddingFunction (all-MiniLM-L6-v2). A
│   │                                switch to ChromaDB's ONNX DefaultEmbeddingFunction was tried
│   │                                and reverted: ChromaDB persists the embedding fn choice IN the
│   │                                collection itself, so passing a different one at runtime
│   │                                doesn't migrate an EXISTING collection — it just fails at
│   │                                query/add time. torch stays a real dependency as a result.
│   ├── tool_manager.py            # Auto-discovery tool registry & execution — skips any module
│   │                                named in config.DISABLED_SKILL_MODULES before even importing it
│   ├── telegram_interface.py      # TelegramInterface — third front-end consumer of CielCore
│   │                                (long-polls the Bot API directly; chat_id allow-list; confirm
│   │                                gate via inline Yes/No keyboard, same confirm_callback contract
│   │                                as main.py's CLI prompt and main_api.py's WS dialog)
│   ├── cost.py                    # LLM pricing table + estimate_cost() (overridable via ciel_data/model_pricing.json)
│   ├── voice_input.py             # CLI speech-to-text (sounddevice + whisper/google/gemini backends)
│   ├── speech_output.py           # CLI/UI text-to-speech (to_speech() normalizer + edge/pyttsx3/space backends)
│   │
│   │  # --- the seven agent-capability tiers, in tier order ---
│   ├── continuation.py            # T1  observe → re-plan → act; every bound enforced in code
│   ├── task_state.py              # T2  durable job records; interrupted work survives a crash
│   ├── permissions.py             # T3  AUTO/ASK/DENY(+DEFER), plan-level approval, deferred store
│   ├── context.py                 # T4  the single prompt assembler + token budget
│   ├── notifier.py                # T6  routes a proactive message, and decides whether it goes at all
│   ├── triggers.py                # T6  the nine condition triggers + polling engine
│   ├── user_model.py              # T7  the Master's profile — authority, decay, unprompted learning
│   ├── parallel.py                # (T1-adjacent) independent read-only steps run concurrently
│   └── scheduler.py                # background thread: legacy clock tasks + the Tier-6 trigger engine
│
├── agent_system/                  # Brain-Worker-Middleware LLM subsystem
│   ├── config.py                  # Provider/model/retry config, per tier, plus every tier's .env knobs
│   ├── main.py                    # Standalone LangGraph runner
│   ├── models/
│   │   ├── brain.py                # Brain LLM (Router + Planner)
│   │   ├── worker.py                # Worker LLM (Code/Text generator)
│   │   └── middleware.py            # Middleware LLM (semantic verifier/finalizer, email-scoped)
│   ├── graph/                      # state.py, nodes.py, edges.py, builder.py — LangGraph code-gen pipeline
│   └── utils/
│       ├── logger.py                # Colored console logger
│       └── usage.py                 # extract_usage()/format_usage() — provider token counts for cost tracking
│
├── skills/                        # Tool packs (auto-discovered by ToolManager)
│   ├── _result.py                  # Shared result schema (make_result, confirm= pairing)
│   ├── internal/
│   │   ├── memory_ops.py            # Fact vault (standalone JSON, no deps) — pull-only, never injected
│   │   ├── system_ops.py            # Workspace file CRUD + Python runner + PDF/DOCX reader
│   │   ├── os_ops.py                # Shell, screenshot, app launcher
│   │   ├── productivity_ops.py      # Todos, time, weather, calculate, grep_in_workspace
│   │   ├── report_ops.py            # build_analysis_report_html → email_template/analysis_report.html
│   │   │                              (prefer output_path=agent_output/*.html; never telegram_uploads/)
│   │   └── vision_ops.py            # Gemini Vision + PyAutoGUI grid overlay, PLUS describe_image_file
│   │                                  (looks at an existing image FILE, e.g. one Telegram-uploaded —
│   │                                  not the live screen; shares _call_vision_llm as an implementation
│   │                                  detail only). All three tools disabled together on headless
│   │                                  deployments via .env `DISABLED_SKILL_MODULES=vision_ops`
│   │                                  (ToolManager skips the module before import) — a known trade-off:
│   │                                  describe_image_file itself needs no display, but is bundled with
│   │                                  the two that do.
│   └── external/
│       ├── gmail_ops.py             # Gmail toolkit + custom ops; search_gmail formatted with message_id
│       ├── trading_ops.py           # Crypto (Binance-first for BTC/USDT), Forex/Metals, build_market_report_html
│       ├── telegram_ops.py          # send_telegram (plain text) + send_telegram_document (HTML/PDF file)
│       ├── github_ops.py            # Git status, diff, commit, push
│       └── web_agent_ops.py         # stealth_search (SerpApi Google → RSS → ddgs) + smart_scrape
│
├── persona/
│   └── official_ciel_personality.txt   # The only persona file loaded at startup
│
├── ui/                             # React + Tauri v2 frontend (optional) — see ui/README.md
│   └── src/
│       ├── orb.ts                   # Three.js audio-reactive particle orb (framework-agnostic, currently unmounted — see voice_and_interface.md)
│       ├── components/Orb.tsx       # React wrapper for the orb
│       ├── core/                    # transport+protocol (ws, bus, types, http) — modality-agnostic
│       └── io/                      # modality layer: input/ (text + voice mic), output/ (transcript, speaker+analyser)
│
├── autonomous_pipeline/            # Self-running MLOps daemon (optional)
│   ├── orchestrator.py              # Background scheduler
│   ├── task_generator.py            # Simulated Master
│   ├── data_pipeline.py             # Judge audit + dataset builder
│   └── chaos_injector.py            # Adversarial edge-case injection
│
├── backtest/                       # Test suites via `python -m backtest.run_all` (not pytest)
│   ├── run_all.py                   # Unified runner: --unit-only | --skip-exploratory | full
│   ├── test_context.py              # T4 — context budget/priority
│   ├── test_outbound.py             # duplicate-send guard + stale-status strip (stub tools)
│   ├── test_proactive.py            # T6 — notifier + triggers (simulated clock)
│   ├── test_user_model.py           # T7 — profile authority/decay/secrets
│   ├── test_conversation_bugs.py    # scope veto, recent-turns, referential email, open thread
│   │                                  (constructs real CielCore — may append to thoughts.log)
│   ├── test_quality_guards.py       # P1 guards: sanitize, write-intent, gmail digest, HTML
│   │                                  builder, telegram_uploads write-block (maintained unit)
│   ├── test_integration.py          # Full pipeline (live LLM)
│   ├── test_brain_worker.py         # Multi-step workflow (live LLM)
│   ├── test_rag_memory.py           # RAG amnesia stress (live LLM)
│   ├── test_hard_special.py         # Hard/special + market/email (live LLM)
│   └── live_conversation_test.py    # Exploratory multi-turn sim (expensive; --skip-exploratory)
│
├── scripts/                        # format_thoughts_log.py, prompt_harness.py, cost_report.py,
│                                    # health_check.py (CI liveness probe), daily_digest.py (CI
│                                    # email+news → Telegram, see "Docker Deployment" below)
├── email_template/                 # market_report.html · analysis_report.html · health_report.html
├── instructionAI/                  # AI-assistant instruction files (start with SKILL.md)
├── improve.md                      # Upgrade roadmap Level A/B/C + P0–P3 (Level B as of 2026-08-06)
├── ciel_workspace/                 # Sandbox (+ telegram_uploads/ for inbound bot files)
└── agent_output/                   # Generated code + analysis HTML reports
```

**Unit suites** (`run_all --unit-only`): context, user_model, proactive, outbound,
conversation_bugs, **quality_guards**. Prefer these first on any change to `core/` or
outbound/Telegram paths. Live suites need API keys and cost real tokens.

**Telegram inbound path (runtime):** download → `ciel_workspace/telegram_uploads/` →
inbox note with path → Brain routes `read_file` / `read_document` / `describe_image_file`
→ optional `build_analysis_report_html` + `send_telegram_document`.

## Internal Dependency Graph

```
main.py → core.agent_loop.AgentLoop → core.llm_connector.CielCore

CielCore initializes:
  ├── agent_system.models.brain.Brain              (Router LLM)
  ├── agent_system.models.worker.Worker            (Generator LLM)
  ├── agent_system.models.middleware.Middleware     (Semantic verifier — optional, lazy)
  ├── core.router.Router                           (Intent classification)
  ├── core.recovery_manager.RecoveryManager         (Self-healing)
  ├── core.rag_manager                             (Hybrid memory)
  ├── core.tool_manager.ToolManager                 (Tool registry)
  │    └── skills.internal.* + skills.external.*   (auto-discovered)
  ├── core.task_state.TaskStore                    (Tier 2)
  ├── core.permissions.PermissionPolicy             (Tier 3)
  ├── core.user_model.UserModel                     (Tier 7)
  └── core.permissions.DeferredStore                (Tier 6 safety ceiling)

main.py also initializes:
  └── core.scheduler.CielScheduler                  (clock tasks + Tier-6 TriggerEngine)
       ├── (legacy) directly calls Gmail/TwelveData/Telegram APIs
       └── (Tier 6, opt-in) core.notifier.Notifier + core.triggers.build_triggers()

main.py (voice mode) also touches:
  └── core.voice_input / core.speech_output         (STT/TTS — lazy imports, fail-open)

main_telegram.py → core.agent_loop.AgentLoop → core.llm_connector.CielCore
  └── core.telegram_interface.TelegramInterface     (long-poll loop + confirm gate;
                                                      sets ciel.core.confirm_callback,
                                                      same signature as main.py/main_api.py's)
  A separate process/entry point from main.py and main_api.py — its own CielCore
  instance, not shared state, unless deliberately pointed at the same ciel_data/
  (see docker/README.md's shared-memory note).

skills.internal.memory_ops → standalone (reads/writes ciel_data/facts.json directly)
```

## Runtime Flow

```
User input (text or voice transcript) → CielCore.process()
  → clear this turn's cancel flag and outbound-send record (Tier 5 / duplicate-send guard)
  → Tier 7b: assess_preference(text) — free; only durable wording spends one extraction call
  → RAG recall: search ChromaDB (skipped if query < 15 chars or relevance < 0.65;
    a result whose archived question normalises identically to THIS one is filtered —
    otherwise a repeated question recalls its own prior failure as "context")
  → ContextAssembler bounds recall, then assembles [request, language, cwd] with a budget
  → Router (Brain LLM) classifies intent → JSON with hidden_thought + action
      action == "chat": the router's `task` is a HINT only — the Worker always gets the
      Master's real words; `task` is appended labelled "for reference ONLY", and Brain's
      real `hidden_thought.reasoning` (when present) is appended alongside it — also
      labelled non-binding — so the Worker explains a "why not" from the real reason
      instead of inventing a plausible-sounding but false one (caught live: a false
      "no permission to control the browser" excuse for a decision Brain made for an
      unrelated reason)
  → Based on action:
      "chat"       → Worker generates a response, with recent-turns context injected
                     → if that reply CONCEDES a knowledge gap ("chưa có dữ liệu…"), a
                       free Python gate runs stealth_search and re-answers from the
                       results (_search_fallback_for_chat) — a capability refusal, a
                       non-question, or a question about the Master's own stored data
                       is left alone, and a search that finds nothing keeps the honest
                       "I don't know" rather than replacing it with a guess
      "tool"       → dangerous-code / high-risk gate → Safety Gate (Y/N, or DEFER if
                     unattended) → ToolManager executes → outbound send is deduped by
                     recipient → Worker formats non-email results
                     → Self-Correction: Brain evaluates → retries if unsatisfied (max 2)
      "code"       → Worker generates code → dangerous-code gate → buffer_writer → disk
      "multi_tool" → sequential/parallel tool execution → workflow safeguards auto-append
                     a missing send/write step → Worker synthesizes a report → TIER-1
                     AGENT LOOP may re-plan with the real results in view → deferred
                     send/write runs ONCE with the synthesized body → duplicate-send
                     guard, subject enforcement, placeholder guard all apply
  → Self-Healing (on error, unless the error is on the unfixable skip-list): fix obvious
    cause → rewrite with an alternative approach → full rewrite, stdlib only
  → chat_history updated; overflow archived into ChromaDB
  → Tier-2 task record closed: done / failed / blocked (a staged confirmation) / cancelled
  → response returned, optionally spoken aloud (TTS)
```

---

## Tier 1 — The Agent Loop (`core/continuation.py`)

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

**A scope veto is checked before anything else.** Found live: "liệt kê từng file thôi,
rồi DỪNG lại" ("just list the files, then STOP") still tripped the fan-out signal and
looped anyway — every signal below is a reason to *continue*, none was a reason a human
gave to *stop*. `request_has_scope_veto()` matches explicit bounding language ("chỉ …
thôi", "đừng …", "only …", "do not …") and, if present, overrides every signal below —
deliberately asymmetric with the rest of the policy: a missed continuation costs a
thinner answer, overriding an explicit "don't" does work nobody asked for.

**Five signals may open a round**, checked after the veto. All but the last are
properties of the request or of what actually came back — never a model self-report:

| | Signal | Example |
|---|---|---|
| S1 | Request is conditional | "nếu sạch **thì** commit", "only if the tests pass" |
| S2 | An unresolved `{step_N}` / `{{step_N}}` reached a real tool | the single-brace bug — the tool ran on literal `{step_1}` |
| S3 | A step failed while **later** steps still ran | read_file 404s, the summary step then invents content |
| S4 | Fan-out over a set of unknown size | "liệt kê file rồi đọc **từng** file" — 4 listed, 1 step ran |
| S5 | Planner set the optional `"needs_followup": true` | strictly opt-in; omitting it breaks nothing |

**Bounds are enforced in code, not by model good behaviour** (`LoopBudget`): extra
rounds (`AGENT_LOOP_MAX_ROUNDS`, default 2), planner calls, wall-clock
(`AGENT_LOOP_MAX_SECONDS`, default 120 — tested **before every step**, not merely
between rounds), and `max_steps_per_round` (4). Fail-open: any exception, unparseable
re-plan, or empty follow-up quietly keeps the first round's answer.

Off-switch: `AGENT_LOOP_ENABLED=false` restores the exact pre-loop behaviour. Cost on an
ordinary request is exactly zero extra calls — `assess()` returns `False` before any LLM
is touched; an 11-case regression spent the same 8 Brain calls loop-on vs loop-off.

### Parallel tool execution (`core/parallel.py`)

Steps in one plan that are **provably independent** run concurrently instead of one
after another. `plan_batches()` walks the steps in order and only ever *groups* them —
never reorders or drops any — so results collect back in original order and
`{prev}`/`{step_N}` keep their sequential meaning.

A step joins a batch only if all three hold: its tool is declared parallel-safe, its
args carry no `{step_N}`/`{prev}` reference, and it is not high-risk (several threads
racing one stdin Y/N prompt is a deadlock, not a speed-up).

**Opt-in, never a blacklist** — skills auto-register, so "parallelise everything except
writes/sends" would silently parallelise a new mutating tool the day one is added:

```python
def get_my_tools():
    return {"tools": [...], "prompt": "...", "parallel_safe": ["my_search"]}
```

`_log_thought` takes a lock: it appends to `thoughts.log` **and** accumulates the
per-tier token/cost dicts, so concurrent callers would otherwise interleave mid-entry
and lose counter increments.

**Measured, tool time only** (LLM excluded — it dominates wall-clock): 7 file reads
9.3×, 4 mixed fast tools 1.9×, 4 web scrapes 2.3×, 3 searches 1.05× (the search source
rate-limits, so concurrency can't help). In absolute terms 0.15–2.8s against 15–55s
requests — invisible on ordinary requests, real minutes saved only on slow fan-out.
Off-switch: `AGENT_PARALLEL_ENABLED=false`.

## Tier 2 — Durable Task State (`core/task_state.py`)

Before this, the only cross-turn state was one slot holding one pending confirmation:
a job interrupted by a crash, restart, or closed terminal simply vanished, and "what
were you doing?" could only be answered by the model guessing — the Router never sees
`chat_history`, and such short questions fall below RAG's `MIN_QUERY_LENGTH`.

```
TaskRecord: id · goal · status · steps[] · note · created/updated
status:     active → done | blocked | failed | interrupted | cancelled
file:       ciel_data/state/tasks.json   (rolling, newest 20)
```

- **Opened** only for `tool` / `multi_tool` / `code` — pure chat leaves nothing
  half-finished, so it gets no record.
- **Closed** at every exit: `done`, `failed` on exception, `cancelled` (Tier 5), or
  **`blocked`** when a confirmation was staged — waiting on the Master is not
  completion.
- **Resumption.** A record still `active` at load time must belong to a dead process
  (the constructor only runs at start-up), so it is reclassified `interrupted`.
  `main.py` prints it on launch; `describe_unfinished()` exposes it.
- **`đang làm gì` / `status`** is matched before routing and answered from the store —
  **0 LLM calls, ~0.1s** (measured).

**The Brain never reads these records.** Feeding them into routing would reopen the
cross-request contamination this codebase deliberately closed. Thread-safe: verified
with 6 threads × 25 steps — 150/150 kept, no duplicate ordinals. Deliberately **not** a
scheduler, queue, or priority system — one active task at a time, matching how a
pending-confirmation slot and a real conversation both already work.

## Tier 3 — Permissions (`core/permissions.py`)

Approval used to be binary and per-call: a tool was either always-ask or free, and the
prompt arrived **mid-execution** — declining at step 3 of 4 left steps 1-2 already run.

Every `(tool, args)` resolves to one of:

| | Meaning |
|---|---|
| `AUTO` | Read-only. Never interrupts. Explicit list + `CIEL_AUTO_TOOLS`. |
| `ASK` | Has an effect. Needs confirmation. Source of truth: `_HIGH_RISK_TOOLS`. |
| `DENY` | Refused outright, not even offered. `CIEL_DENY_TOOLS`. |
| `DEFER` | `ASK`, but unattended (Tier 6) — see below. |

**Plan-level approval** (`_review_plan_permissions`, before the first step of a
multi_tool plan): one prompt listing every step, answered once. "No" means **nothing
ran**. A deny-listed step aborts without asking at all.

**Two grant scopes, deliberately different:**
- *session* — by tool name, opt-in via `A` at the CLI prompt, never written to disk.
- *plan* — by **`(tool + exact args)` signature**, cleared when the plan ends. Keyed on
  the whole call so approving `delete_file` for the reviewed plan cannot auto-approve a
  *different* `delete_file` the Tier-1 loop invents two rounds later; a stale grant can
  only ever re-approve the identical action.

`DENY` is checked first and cannot be overridden by any grant or `DISABLE_SAFETY_GATE`.
The `AUTO` set stays in agreement with `core/parallel.py`'s parallel-safe set (a test
asserts no parallel-safe tool needs approval).

### The unattended ceiling (Tier 6 safety mechanism, lives in this module)

A trigger firing at 03:00 has nobody to ask, and "nobody answered" must never resolve
to "yes". `PermissionPolicy.decide(..., attended=False)` returns `DEFER` for a risky
tool: recorded in `DeferredStore`, raised at the next interaction. Session grants, plan
approvals, and `DISABLE_SAFETY_GATE` are **all ignored** in that context — each is
evidence a human agreed *while present*. The only escape hatch is per-tool
(`CIEL_UNATTENDED_AUTO_TOOLS`), which cannot reach past the deny list.

`CielCore.unattended` is **thread-local**, propagated explicitly into parallel workers.
A plain attribute would let the scheduler thread flip it mid-flight on a foreground
request, silently turning that user's confirmations into deferrals.

`DeferredStore` (`ciel_data/state/deferred.json`) **deliberately never replays**. A
mutating action decided against 03:00's world is not the same action at 09:00; the
Master re-issues it as a fresh request. Known-open: `main_api.py` still auto-approves
when no WebSocket is attached — that predates this ceiling and should route through it.

## Tier 4 — Context Discipline (`core/context.py`)

Context used to be assembled by appending to a string — six `enriched_input +=` lines,
each reasonable alone, together with no budget, no record of what a call carried, and an
order that was just the order features were added in.

`ContextAssembler` collects named blocks and renders them once:

```
ctx.add("request",  enriched_input, P_REQUEST)    # 100 — never dropped
ctx.add("cwd",      "[WORKING DIRECTORY: …]", P_CRITICAL)   # 80
ctx.add("language", "[USER LANGUAGE: …]", P_IMPORTANT)      # 60
text, report = ctx.render(budget_tokens=CONTEXT_INPUT_BUDGET)
```

**Priority decides what is dropped; insertion order decides layout.** Kept separate
deliberately — emitting highest-priority-first is also defensible, but changing
ordering *and* adding a budget at once would make any A/B uninterpretable. Blocks drop
**whole, never truncated**: half a `[WORKING DIRECTORY: …]` note still reads as a fact
while being wrong.

RAG recall is bounded **at its source** (`CONTEXT_RECALL_BUDGET`), before it fuses with
the request — it is the only block whose size depends on retrieved data rather than
code, so bounding it after the merge would be too late to drop separately. Every drop
is logged (`[CONTEXT] assembled`).

**Recent-conversation context** (`CielCore._recent_turns_block()`) is also assembled
this way, injected into `execute_chat` and the tool-result format path — **never** the
Router (see Tier 1's July-2026 decision below; a test asserts this).

### Router persona — measured, not assumed

The router emits JSON and nothing else, yet carried the full 1,205-token persona: 28%
of every Brain call spent on voice, for a component that never speaks. A/B over 11
routing cases:

| mode | persona tok | Brain input tok | decisions |
|---|---|---|---|
| `full` | 1,205 | 46,728 | baseline |
| `slim` | 46 | **34,221 (−27%)** | 10/11 identical |

The one disagreement (an email plan missing its send step) was re-run 3× per arm:
`full` produced it 2/3, `slim` 2/3 — **sampling noise, not a persona effect**.
`ROUTER_PERSONA_MODE` still ships `full`: 11 cases at one repetition is not enough
evidence to flip a default silently. Set `slim` to take the saving.

### The conversation-memory bugs (found live, fixed together)

Reading a real session (not a failing test) surfaced three related bugs:

1. **No memory of the turn just answered.** `chat_history` was stored, persisted, and
   archived into RAG — and never read back into a prompt. "giá vàng bao nhiêu" → an
   answer → "tại sao lại thế" got a reply with zero reference to the price just given.
   Fixed by `_recent_turns_block()` above.
2. **RAG recalling the question's own prior failure.** A repeated/rephrased follow-up
   is, by construction, the single most similar thing in the store — so it recalled its
   own earlier unhelpful reply as "context", teaching the model to repeat it. Filtered
   in `rag_manager.search_similar()` via `_normalize_for_selfmatch()`: drops a result
   whose archived question normalises identically to the current one (a paraphrase is
   NOT filtered — that is genuine, useful recall).
3. **The router pre-writing the final reply into `task`.** A strong Brain routinely
   overstepped `{"action": "chat", "task": "what the Worker should do"}` and wrote the
   literal answer into `task` — once caught writing `"task": "Reply: \"Novices guess,
   Master...\""` in English while the Master's real message was a Vietnamese tease.
   Since the Router never sees `chat_history`, this could ALSO silently override
   context the Worker actually had (a football score resolved one turn back was
   ignored because `task` pre-decided "ask which match"). Fixed in `process()`'s chat
   branch: `execute_chat` always receives the Master's real `user_input`; `task`
   survives only as a labelled, non-binding hint. `action == "code"`'s `task` is a
   different contract (a spec, not a pre-written answer) and is untouched.

Verified live, model-for-model, on both reproductions — see `note.md` for the exact
before/after replies. Tests: `backtest/test_conversation_bugs.py` (44 assertions).

## Tier 5 — Interruptibility (`CielCore.request_cancel`)

`main.py` blocks in `input()`, so a long request could only be escaped by killing the
process — destroying the Tier-2 record of what had been done along with it. One
observed run sat at 566s.

- **Ctrl+C during a request cancels the request**; at the prompt it still exits.
- The job closes as **`cancelled`**, so `status` tells it apart from a crash.
- Cancellation is **cooperative, checked at step boundaries only** — `_run_steps`
  before each batch, `_continue_until_done` before each planner call. Never mid-tool.
- The flag clears at the **start** of a turn, not when it fires, so a Ctrl+C landing
  between turns cannot silently kill the next request.
- Thread-safe (`threading.Event`) — the UI now cancels the same way over the WebSocket
  (`{"type": "cancel"}`, wired in `main_api.py`).

## Tier 6 — Proactivity (`core/notifier.py` + `core/triggers.py`)

`scheduler.py` could only fire on a wall-clock time and held one hardcoded task, so
Ciel could say "good morning" but never "that job you started is still stuck". Nine
triggers in three groups close that, at zero token cost while idle:

```
TriggerEngine.tick(now)          rides the scheduler's existing daemon thread
  └─ Trigger.check(now)          plain Python over data already on disk
       └─ Notification           key · title · detail · action · urgency
            └─ Notifier.deliver(now)
                 contract → cooldown → repeat-mute → budget → first LIVE channel
```

| Group | Trigger | Watches | Source |
|---|---|---|---|
| A | `unfinished_task` | a job abandoned past `PROACTIVE_UNFINISHED_MIN_AGE` | Tier-2 `TaskStore` |
| A | `daily_cost` | today's tokens/USD over a ceiling you set | `thoughts.log` + `core/cost.py` |
| A | `repeated_failure` | one tool failing N times in a window | `thoughts.log` |
| A | `deferred_approval` | background actions blocked pending the Master | `DeferredStore` |
| B | `digest` | everything held back, read out once a day | the Notifier's own queue |
| B | `morning_digest` | the 08:00 brief, now a declared trigger | Gmail + markets + Worker |
| C | `price_alert` | a price **crossing** a threshold you set | `fetch_market_price` |
| C | `important_email` | unread mail from senders you listed | Gmail |
| C | `stale_todo` | todos open past N days (**age** — the store has no due date) | `todos.json` |

Group C exists only behind thresholds set explicitly; a trigger with a missing
threshold or dependency is dropped at build time, not crashed on
(`is not None`, never a truthiness test — `DeferredStore` defines `__len__`, so an
*empty* store is falsy and would otherwise silently drop the one trigger meant to
report on it). Opt-in **by name** via `PROACTIVE_TRIGGERS` (empty = nothing runs), for
the same reason skills are opt-in. Everything is off by default.

**Four rules stop it becoming noise:**

1. **The message contract is enforced in code.** A `Notification` with no `action` is
   demoted to the digest and can never interrupt (`effective_urgency()`).
2. **Edge, not level.** Each `key` is built from the identity of the underlying thing —
   a task id, a tool name, a date bucket — never from message text, and the Notifier
   enforces a per-key cooldown, so a condition that merely *stays* true is announced
   once.
3. **Routing by liveness.** A running process is not a present human: `Presence` counts
   the CLI/app as a channel only while the last interaction is inside
   `PROACTIVE_IDLE_SECONDS`; past that, delivery falls to Telegram. `main.py` blocks in
   `input()`, so `NOTIFY` queues and flushes between prompts (never into a half-typed
   line) while `ASK` prints immediately. An unanswered `ASK` escalates to the fallback
   channel; any keystroke calls `ack_seen()`.
4. **Budget + feedback.** `PROACTIVE_DAILY_BUDGET` caps interruptions per day in
   Python; over budget, findings drop to the digest instead of vanishing. After
   `PROACTIVE_REPEAT_LIMIT` unheeded repeats of the *same* finding, it goes quiet —
   muted per key, not per trigger, so a long silence re-arms it since a condition that
   went away and came back is news again.

`daily_at()` expresses a clock task as an ordinary trigger, firing *at or after* its
time so a machine asleep at 08:00 still gets its brief on waking. When the engine owns
`morning_digest`, the scheduler skips registering the 08:00 clock task — two mechanisms
delivering one brief is a double-send the Notifier cannot dedupe.

**Gotcha found the hard way.** `_log_thought` writes the log in text mode, so on
Windows `thoughts.log` is 100% CRLF, while the tail is read in binary to bound its
size — miss that and every log-reading check silently reports "nothing found",
indistinguishable from a healthy system. Any new check must go through `_iter_entries`.

Tests: `backtest/test_proactive.py` (148 assertions, no LLM, no network — every
decision takes `now` as a parameter, so a full day simulates in milliseconds).

## Tier 7 — A Model of You (`core/user_model.py`)

A fact vault already existed (`skills/internal/memory_ops.py` + `ciel_data/facts.json`)
and was still `{}` after months. That is the design, not neglect: the vault is
**pull-only** — recall needs the model to guess an exact key *and* choose to call
`get_fact`, writing needs the Master to say "remember this" out loud. This module is the
**push** side — a bounded profile that enters the prompt by itself.

**Two stores, split by what happens to them, not by what they hold:**

| Store | Holds | Read path |
|---|---|---|
| `ciel_data/facts.json` | secrets, credentials | pull-only, **never injected** |
| `ciel_data/user_model.json` | preferences, profile | **injected**, refuses credentials |

`looks_like_secret()` refuses on both key and value — the value test catches an
innocently-named key holding a real token. Separator normalisation matters: `_`/`-` are
word characters, so `\bcvv\b` does not match `card_cvv` without it (both `card_cvv` and
`bank_pin` were being accepted before the fix).

**Three rules keep a profile from rotting:**

1. **Authority** — `stated` (3) > `inferred` (2) > `observed` (1). A lower-authority
   write can never overwrite a higher one; the Master can always change their own mind.
2. **Decay** — `stated` never fades; inferred/observed lose confidence on a half-life
   unless re-observed, so "I'm busy today" cannot harden into a permanent trait.
3. **A hard token ceiling** — `render(budget_tokens=…)` truncates strongest-trait-first
   and returns `""` on an empty profile, so the feature costs **exactly zero** until it
   has learned something.

Injected via `CielCore._profile_block()` at the Worker chat path and both tool-result
format paths — **deliberately not in the Router**, same reasoning as the persona A/B.
The block states its own authority: *"Đây là nền, KHÔNG phải mệnh lệnh"* — a stale
profile must never override what the Master is asking for right now.

### Learning without being told (7b)

`save_fact` never fires organically because it needs the Master to *ask*. Asking the
model "was there a preference in that?" every turn would fix that and cost a call
forever. So the same shape as `ContinuationPolicy.assess()`:

```
assess_preference(text)     free Python. None on an ordinary turn → nothing happens
  ├─ one-off marker ("hôm nay", "lần này")  → None, outright
  ├─ durable marker ("từ giờ", "luôn")      → STATED
  └─ leaning ("thích", "muốn", "prefer")    → INFERRED
        └─ ONE extraction call → parse_extraction → remember()
```

**Python decides the kind, the model only proposes key/value** — letting the model
self-report authority would make the authority rule meaningless. Runs on a **daemon
thread** from the top of `process()`: the reply returns in 0.8ms while a real
extraction takes 5–8s. Excluded on unattended runs — a trigger's text is Ciel's own
words, and learning from itself drifts the profile away from the person it describes.
`USER_MODEL_LEARN_DAILY_LIMIT` caps extraction calls per day, persisted.

Tests: `backtest/test_user_model.py` (108 assertions, no LLM).

---

## Multi-Provider Support

Each tier (Brain / Worker / Middleware) selects its provider and model independently
via `.env` — no code changes to switch.

**Current (since 2026-07-27):** all three tiers run on a single `custom`
OpenAI-compatible endpoint (`API_KEY` + `BASE_URL`), switched from Vilao for balance
reasons. Vilao lines are deliberately left unused in `.env` for rollback — see `note.md`
for the exact date and reasoning; that file is the live source of truth for which
provider/model is actually running, not this table.

| Tier | Provider | Model (live) | Config Key |
|------|----------|-------|------------|
| **Brain (Router)** | `custom` | `gpt-5.6-sol` | `BRAIN_PROVIDER`, `BRAIN_MODEL` |
| **Worker (Generator)** | `custom` | `gpt-5.6-luna` (via `CODER_MODEL`, not `WORKER_MODEL`) | `WORKER_PROVIDER`, `CODER_MODEL` |
| **Middleware (Verifier, optional)** | `custom` | `gpt-5.5` | `MIDDLEWARE_PROVIDER`, `MIDDLEWARE_ENABLED` |

> Provider model names/aliases drift — check the alias is still live (a trivial request,
> compare reported `input_tokens` against what you actually sent) before suspecting the
> code on empty output or a 4xx. See rule 11 in `SKILL.md`.

Also supported per tier: Vilao, DeepSeek, Gemini, Ollama (fully offline) — swap by
changing `*_PROVIDER` + the matching `*_API_KEY`/model name, no code change. **Tested
fallback:** if the primary endpoint is unavailable, `BRAIN_PROVIDER=deepseek` + a valid
DeepSeek model works with zero code changes.

**Measure a new alias before adopting it.** On the same endpoint/key, one Brain alias
(`ccf/claude-opus-4-8`) added **~6,500 unsuppressable tokens per call**, while
`nt/cx/gpt-5.6-sol` added ~10 — switching cost one `.env` line and cut Brain tokens 52%,
latency 51%, at identical 11/11 correctness. ~79% of the old cost was text nobody sent.
To check: issue one trivial request and compare the provider's reported `input_tokens`
against what you actually sent.

> **Gotcha:** the Worker's MODEL is read from `CODER_MODEL`, never `WORKER_MODEL`; its
> PROVIDER is `WORKER_PROVIDER` (`CODER_PROVIDER` is not read).

## Web Search Source Chain (`skills/external/web_agent_ops.py`)

`stealth_search` is layered, because a single DuckDuckGo `text()` call returned no
publication dates, surfaced section landing pages instead of articles, and honored its
own `timelimit` unreliably (a "past day" window still returned 8–16-day-old hits):

```
any query
  → 0. SerpApi (real Google)    PRIMARY, unconditional — the same index/ranking a human
       typing into google.com/search gets. Needs SEARCH_API_KEY (+ API_ENDPOINT).
       `tbs=qdr:X` is Google's own recency filter, mapped from our `timelimit` letters.
       Dates arrive as RELATIVE strings ("12 hours ago" / "2 ngày trước"), parsed by
       _parse_relative_age() — plain web results carry no absolute timestamp.
       Returns [] (never raises) with no key → everything below runs unchanged.
  ↓ (only if the above produced nothing)
news-intent query
  → 1. Google News RSS          free, no API key, no extra library
       ├─ generic "what's the news" query  → TOP STORIES feed (no q=)
       └─ query names a topic              → keyword search (q=)
       locale (hl/gl/ceid) picked from the QUERY's language: vi/VN, en-US/US, ja/JP, …
  → 2. ddgs.news()              fallback if RSS fails/empty (also dated)
  → 3. ddgs.text()              non-news queries, or to top up
then: recency filter on the REAL pubDate → landing-page filter → trim to max_results
```

Two traps this chain has already sprung, both fixed:

- **The recency filter used to skip itself.** It only applied when ≥3 fresh hits
  survived, so a low-coverage query (Vietnamese "world news roundup") with 1–2 genuinely
  fresh results fell through to the FULL unfiltered pool — a real `timelimit="d"` request
  returned a 216-day-old article, honestly dated and completely unwanted. Now any
  non-empty fresh set wins; only a totally empty one falls back.
- **A missing `SEARCH_API_KEY` is invisible.** Tier 0 no-ops rather than erroring, so the
  whole chain still "works", just worse — see rule 29 in `SKILL.md`, and note the key must
  be set in `.env` **and** in `.github/workflows/health_check.yml`'s `docker run -e` list.

- **Top stories vs keyword search matters.** "Top news headlines today" would otherwise
  match articles *titled* that (roundups); the top-stories feed returns the actual lead
  stories — `_is_generic_news_query()` decides between them.
- **Language drives the locale.** A Vietnamese query must hit Vietnamese outlets — see
  the `[USER LANGUAGE: X]` note (`CielCore._detect_language`).
- Every result prints `Published` + `Source`; undated results are labeled
  `UNKNOWN … do NOT state a date` so the model cannot invent one.
- Google News RSS is an UNOFFICIAL endpoint (like edge-tts) — keep the ddgs fallback.

## Three Entry Points

- **`main.py`** — CLI loop. Blocking `input("Y/N")` for safety confirmations. `--voice`
  (speak requests) and `--speak` (hear replies); `:v` for a one-off voice capture.
  Ctrl+C mid-request cancels the request (Tier 5); at the prompt it exits.
- **`main_api.py`** — FastAPI + WebSocket. Streams `thoughts.log` lines and vitals
  (incl. live token/cost) to the React/Tauri UI. Safety confirmations sent as JSON over
  WebSocket with a 60s timeout; `{"type": "cancel"}` wires Tier 5. `POST /tts` powers
  the UI's read-aloud toggle with the same voice engine as the CLI.
- **`main_telegram.py`** — thin wrapper around `core/telegram_interface.py`
  (`TelegramInterface`). Long-polls the Bot API on one thread, processes messages one at
  a time on a worker thread (a queue, not concurrent — CielCore's JSON/SQLite-backed
  state is built for one writer). **Every inbound message is checked against a single
  allow-listed `TELEGRAM_CHAT_ID`** before it ever reaches `core.process()` — this
  front-end can run real tools (shell, email, file writes), so an unauthorized sender
  must never reach routing. Safety confirmations become an inline Yes/No keyboard
  (`confirm_callback`, same contract as the other two front-ends), auto-declining after
  60s. `/cancel` wires Tier 5 (handled on the poll thread, not queued, so it interrupts
  whatever the worker thread is currently doing instead of waiting behind it).

See `voice_and_interface.md` for the UI's current layout, the full WebSocket protocol,
and what a UI rebuild still needs to wire.

## Docker Deployment (`docker/`)

Two front-ends, one shared image (`docker/Dockerfile`, built from repo root so it can
`COPY . .`), split into **separate compose files** so each can be built/started/stopped
independently: `docker-compose.api.yml` (`ciel-api` → `main_api.py`, the Vercel-facing
service) and `docker-compose.telegram.yml` (`ciel-telegram` → `main_telegram.py`).

- **`docker/requirements-docker.txt`** drops what a headless container can't use:
  `pyautogui`/`pyperclip` (no display — pair with `.env`'s
  `DISABLED_SKILL_MODULES=vision_ops`) and `sounddevice`/`faster-whisper`/
  `SpeechRecognition` (no mic — `core/voice_input.py` stays CLI-only, lazily imported).
  `edge-tts` is kept (cloud TTS, no hardware). `sentence-transformers` is KEPT too —
  see the `rag_manager.py` note in the File Tree above (ChromaDB persists the embedding
  fn choice in the collection, so the real `vector_memory/` needs the same library it
  was created with). The Dockerfile installs a CPU-only `torch` wheel first so this
  stays ~3.5GB rather than the ~10GB a default GPU build would pull in.
- **Both compose files mount the SAME `ciel_data/`/`agent_output/`/`ciel_workspace/`**
  on purpose, so whichever front-end the Master used, the other sees the same facts,
  chat history, and long-term memory. That is also why the two services should **not**
  run at the same time yet: two independent `CielCore` processes writing the same
  JSON/SQLite-backed state concurrently is a real race condition, not a theoretical
  one. Treat them as alternatives to pick one from until that's addressed with a proper
  shared store or a lock.
- Full build/run commands and the shared-state caveat: `docker/README.md`.

**`.github/workflows/health_check.yml`** builds this SAME image (not a bare `pip
install` on the runner) and runs two one-shot containers from it daily: `scripts/
health_check.py` (boots CielCore, one real Brain call, reports PASS/FAIL to Telegram —
also doubles as a daily proof the image still builds), then, only if that succeeded,
`scripts/daily_digest.py` (asks Ciel — via `core.process()`, real Brain routing, not a
hand-rolled call — to summarize Gmail + news, reports to Telegram). Needed GitHub repo
secrets: `API_KEY`, `BASE_URL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`; optional
`GOOGLE_CREDENTIALS_B64`/`GOOGLE_TOKEN_B64` (base64 of `credentials.json`/
`ciel_data/gmail_token.json`) matter now that the digest genuinely calls `search_gmail`
— previously (health-check-only) Gmail was truly optional.
