---
name: ciel-2.0
description: >
  Modular personal AI assistant with a two-model Brain/Router and Worker pipeline,
  deterministic tool safeguards, bounded conversational context, hybrid memory,
  proactive scheduling, and CLI, API/UI, and Telegram interfaces.
---

# Ciel 2.0 — Project Memory Entry Point

## Cold start

A new AI session reads this file before source code, then opens only the topic files
relevant to the current task. `../improve.md` is the living roadmap; `../note.md` is the
dated operational journal. Stable architecture and rules belong in this directory.

Current baseline:

- Level A and Level B pass.
- P0, P1.1, P1.4, and P1.5 pass.
- The bounded Active Subject handoff passes.
- The monthly/weekly planner passes unit, startup, Docker, and live Telegram delivery
  verification.
- P1.2 formal proactive-day observation, P1.3 formal user-model evaluation, Level C,
  P2, and P3 remain roadmap work unless `improve.md` says otherwise.

The standard verification command is:

```bash
python -m backtest.run_all --unit-only
```

Item-specific suites run in addition to that command. A roadmap checkbox changes only
when its stated pass criteria are met.

## What Ciel is

Ciel is a personal multi-tool agent, not an IDE coding-agent clone. The maintained
runtime has two model identities:

- Brain/Router plans, routes, and evaluates.
- Worker converses, generates, synthesizes, and formats.

Router Assistant and Middleware remain optional code paths, disabled by default. When
enabled intentionally, they reuse Brain or Worker model identities rather than silently
creating a third provider role.

Deterministic Python owns safety, plan validation, context budgets, cancellation,
continuation bounds, notification cooldowns, and delivery deduplication. Models plan
or compose within those boundaries.

Entry points:

- `main.py` — CLI and optional voice.
- `main_api.py` — FastAPI, WebSocket, TTS, and the React/Tauri backend.
- `main_telegram.py` — allow-listed Telegram long polling.

All three use `core.agent_loop.AgentLoop` and `core.llm_connector.CielCore`.

## File index

| File | Stable responsibility |
|---|---|
| `SKILL.md` | Entry point, project identity, file index, critical rules |
| `architecture.md` | Module map, dependency graph, runtime and deployment flows |
| `conventions.md` | Code, tool, data, logging, testing, and maintenance conventions |
| `data_pipeline.md` | Context, memory, planner, scheduler, audit, and persistence flows |
| `safety_and_risk.md` | Permission model, unattended ceiling, outbound and sandbox controls |
| `voice_and_interface.md` | Voice engines, CLI/API/Telegram/UI contracts, WebSocket protocol |
| `references/portable_instruction_generator_prompt.md` | Reusable prompt for bootstrapping this documentation convention elsewhere |
| `references/Reamdepromp.md` | Personal README-writing reference; not project ground truth |

Living files outside this directory:

| File | Responsibility |
|---|---|
| `../README.md` | Public overview, setup, operation, and concise project map |
| `../improve.md` | Roadmap, pass criteria, and upgrade journal |
| `../note.md` | Dated provider, deployment, and operational observations |
| `../backtest/run_all.py` | Unified maintained regression runner |

## Critical rules

### Runtime and model roles

1. `.env` is the credential and provider source. Brain uses `BRAIN_MODEL`; Worker uses
   `CODER_MODEL`. An environment variable named `WORKER_MODEL` is ignored.

2. The normal topology is two model identities. `ROUTER_ASSISTANT_ENABLED=false` and
   `MIDDLEWARE_ENABLED=false`. Vision defaults to the Brain model. Provider changes do
   not require orchestration changes.

3. The Router classifies `chat`, `tool`, `code`, or `multi_tool`. For chat, its `task`
   and reasoning are non-binding hints. The Worker always receives the real user input;
   the Router cannot pre-write the final response.

4. Generic framework assumptions never override documented project behavior. A
   deliberate exception is flagged before being “fixed.”

### Plans, tools, and recovery

5. Every multi-tool and continuation plan passes through `core/plan_validation.py`
   before any step runs. Only loaded tools, schema-valid object arguments, and backward
   `{prev}` or `{step_N}` references are valid. Unsafe duplicate removal stops the plan
   instead of renumbering dependencies.

6. Tool packs auto-register from `skills/**/*_ops.py`. Each exposes one `get_*_tools()`
   function returning `tools`, `prompt`, and optional `parallel_safe`. New or unknown
   tools remain sequential unless explicitly marked safe.

7. A tool may be called repeatedly by healing, correction, continuation, or retries.
   Mutations are idempotent or use preview/confirm boundaries. Tools return the standard
   `skills._result.make_result()` envelope and report partial or empty outcomes honestly.

8. Real external data is gathered before composing claims, emails, or reports. Undated
   search results remain undated. “Sent” is stated only after a real provider success.

9. `_self_correct()` replaces a working result only when the corrective attempt is not
   deterministically failed. Guards constrain bad outcomes without disabling recovery
   paths that could select another loaded tool.

10. Cancellation is cooperative at step boundaries. It never interrupts a tool during
    a write, send, or other atomic action.

### Safety and outbound actions

11. `SAFETY_OPEN` controls Brain content filtering. `DISABLE_SAFETY_GATE` controls
    destructive-tool confirmation. They are independent and never re-coupled.

12. High-risk tools and destructive write/code content pass through permissions and the
    confirmation callback. CLI, API, and Telegram each provide that callback; API and
    Telegram absence/timeouts fail closed. Raw `CielCore` retains a legacy warning plus
    fail-open default when no callback exists, so it is never used as a front end
    without wiring one.

13. An unattended run cannot inherit consent from silence, session grants, plan
    approvals, or `DISABLE_SAFETY_GATE`. Risky work becomes `DEFER`, is recorded, and is
    never auto-replayed.

14. One recipient/channel receives at most one successful outbound delivery per turn.
    Deduplication keys on the recipient, records only successful sends, and covers Gmail
    and Telegram text/document paths.

15. Outbound email passes the deterministic sanitizer before optional Middleware review.
    Middleware is email-scoped, bounded, and fail-open; it cannot discard grounded tool
    values because they look unfamiliar.

16. File operations stay inside `ciel_workspace/` and `agent_output/`. Windows paths
    received by Linux containers are remapped only when they contain one of those
    sandbox zones. Telegram uploads are immutable transport inputs unless overwrite is
    explicitly requested.

### Context and memory

17. Context is assembled through `core/context.py` as named, prioritized blocks. Blocks
    drop whole under budget pressure. Data-sized sources are bounded before assembly.

18. Recent raw turns reach only response-side Worker prompts. They never route the next
    request. The Brain instead receives one compact `core/active_subject.py` snapshot:
    grounded topic, bounded entities, and last completed read action.

19. Active Subject is RAM-only and expires after configured time, unrelated turns, or
    restart. It never stores recipients, credentials, URLs, filesystem paths, commands,
    confirmations, or destructive arguments. Explicit current wording always wins.

20. A successful lookup can retain at most three public URLs for a bounded immediate
    read/deepen follow-up. This lookup channel is not general chat memory.

21. `ciel_data/facts.json` is pull-only and may contain sensitive facts; it is never
    injected. `ciel_data/user_model.json` is bounded push context and rejects secret-like
    keys or values. These stores never merge.

22. Durable tasks, conversational memory, and plans are distinct. Monthly goals and
    weekly tasks live in `ciel_data/planner.db`; immediate todos remain in
    `ciel_workspace/todos.json`.

### Proactivity and persistence

23. Proactivity is opt-in through `PROACTIVE_ENABLED` and named
    `PROACTIVE_TRIGGERS`. Every notification has an action, stable identity key, daily
    budget, cooldown, and liveness-aware delivery path.

24. Monthly and weekly planner messages use `monthly_plan:<YYYY-MM>` and
    `weekly_plan:<ISO-Monday>` keys. Successful sends persist in
    `ciel_data/state/notify.json`; restarts do not repeat them, while failed sends remain
    retryable.

25. `thoughts.log` is an immutable-format chronological audit contract. `[LLM_CALL]`
    keeps `model=` first and space-delimited. Readers normalize CRLF; writers do not
    redesign the format for a UI.

26. `core/planner_store.py` is the only planner SQL boundary. SQLite writes are atomic,
    adds are idempotent, and completion retains history.

### Deployment, testing, and documentation

27. One Ciel process writes shared `ciel_data/`, `ciel_workspace/`, and `agent_output/`
    at a time. API and Telegram containers are alternatives until shared-state locking
    is implemented.

28. Docker images copy repository source with `COPY . .`. Source, dependency, or
    Dockerfile changes therefore require `docker compose ... up -d --build
    --force-recreate`. `.env`, credentials, and persistent data stay bind-mounted and
    are never replaced by source deployment.

29. Headless deployments disable `vision_ops` at load time through
    `DISABLED_SKILL_MODULES`; runtime denial lists remain a separate control.

30. Regression lives in `backtest/run_all.py` and maintained `backtest/test_*.py`
    modules. Live smokes use isolated temporary state and clean up after themselves.

31. Windows PowerShell is placed in UTF-8 mode before piping Vietnamese source or
    subjects to Python. If `?` reaches the tool log, corruption occurred before Ciel.

32. Stable facts update the appropriate topic file in place. Dated results, benchmark
    numbers, provider incidents, and deployment events go to `note.md`; roadmap status
    goes to `improve.md`. `SKILL.md` remains the only complete file index.

## Safe next-work rule

The next upgrade starts from the first unchecked item in `../improve.md`. Finished P0
or Level B work is reopened only when a regression demonstrates that its pass criteria
no longer hold.
