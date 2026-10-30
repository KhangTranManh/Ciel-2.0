# Runtime harness

The runtime harness in `core/harness/` executes user work. The maintenance tool in
`scripts/harness/` still audits/proposes prompt changes; it does not run user jobs.

## Configuration and rollback

```dotenv
CIEL_RUN_HARNESS_ENABLED=true
CIEL_RUN_HARNESS_MAX_STEPS=24
CIEL_RUN_HARNESS_MAX_SECONDS=180
```

The source default is disabled so existing deployments do not switch behavior on
pull. Restart the local process after changing the flag. Set the flag to false and
restart to use the compatibility path. Turning it off preserves the private run
database. Deployment and container recreation are separate operator actions.

## Execution lanes

| Lane | Behavior | Additional model classification/verification calls |
|---|---|---|
| `fast_chat` | Worker conversation; exact standalone greetings bypass Brain | None |
| `single_step` | One known read-only tool with complete routed arguments | No evaluator for successful output |
| `harness_run` | Multi-step/code/mutating/background work, dependencies, recovery | Existing bounded planner/recovery calls only when needed |

Brain keeps the existing `chat/tool/code/multi_tool` plan format and may include an
optional `lane`. Python enforces the final lane after existing route safeguards.
Unknown capabilities and mutations cannot use the single-step whitelist. Pending
confirmations and explicit deferred reissues are resolved before ordinary routing.
Ambiguous requests still go through Brain; arguments are never guessed by regex.

A failed single read or a chat knowledge-gap search can escalate within the same
run, retaining evidence. Clear greetings use only one Worker call. Other ordinary
chat still uses the existing Brain routing and response-side context services.

## Module ownership

| Module | Responsibility |
|---|---|
| `models.py` | Lane/state contracts and positive execution limits |
| `classifier.py` | Pure lane policy, narrow greeting preflight, explicit read-only whitelist |
| `runtime.py` | Feature flag, store construction, startup recovery and historical import |
| `store.py` | Only SQL boundary for `ciel_data/runs.db` |
| `manager.py` | Run identity, shared step/time limits, escalation and outcome tracking |
| `turn.py` | Pending actions, turn bookkeeping, history/subject commit and error handling |
| `turn_context.py` | Bounded context and one routing decision |
| `plan_normalization.py` | Existing missing-send/write and conditional-plan safeguards |
| `dispatch.py` | Chat/single/multi/code execution dispatch |
| `runner.py` | Dependency substitution and explicitly safe parallel batches |
| `planner.py` | Bounded observe/replan using existing continuation policy |
| `verifier.py` | Deterministic step outcome classification |
| `finalizer.py` | One synthesis from raw facts, then permitted terminal delivery/write |
| `tool_execution.py` | Shared tool safety, recovery, evidence and formatting boundary |
| `recovery.py` | Bounded correction and chat search fallback |
| `repair.py` | Parameter/script repair; Python syntax validation with `ast.parse` |
| `code_generation.py` | Code generation; harness writes through the shared tool boundary |
| `signals.py` | Intent, failure, placeholder and risk signals; no model dependencies |

`CielCore` retains compatible public methods delegating to these services. Existing
signal imports are re-exported from the connector, and dynamic configuration exports
remain compatible with older integrations/tests. `core/recovery_manager.py` is a
compatibility import. Context, RAG, user model, permissions, ToolManager, scheduling,
planner/reminder storage and front-end modules remain shared services.

## Persistence and authority

Runs, steps, bounded result artifacts and append-only events live in SQLite.
The private database contains user goals/arguments/evidence; it is not an additional
prompt source. Router never reads historical run actions or raw conversation history.
Model roles and ContextAssembler budgets remain unchanged.

Startup changes abandoned planned/running/waiting records to `interrupted` and
never replays them. Old `tasks.json` records are copied once as history without
modifying the source or importing consent. TaskStore remains a compatibility mirror
for existing notification/status consumers; its final state reflects the run outcome.

A live confirmation callback exposes `waiting_for_approval`, then returns to
running. A preview awaiting a later user message stays waiting. An explicit reissue
creates a fresh reviewed run and links/closes the earlier wait. No persisted event
acts as a permission grant.

Runs remain serialized by `AgentLoop.run_step()` because chat, pending confirmation,
cancellation and outbound deduplication are shared per-turn state. The run store
keeps multiple histories; it is not a concurrent multi-job scheduler or an automatic
resume queue. Arbitrary interrupted work must be explicitly reissued, with current
data and fresh permission checks.

## Limits and completion

The step ceiling counts logical steps entering the shared boundary, including
dependency skips; existing per-step healing/continuation caps remain in force.
Time is checked at execution/planner boundaries. A running atomic tool or provider
request is allowed to finish; its own existing timeout applies. The deadline is not
a hard process kill and does not retroactively cancel an outbound action.

Cancellation, budget exhaustion, waiting, invalid plans and failed source steps
cannot be recorded as completed merely because a final paragraph was generated.
Dependent outbound delivery/write is suppressed after a failed source, pending
approval or exhausted run. Successful correction records a recovery event while
preserving the original failed step/artifact. Failed external/destructive mutations
do not enter automatic argument-healing replay in harness mode.

Generated `.py` files have fences removed and pass deterministic syntax validation
before their write. Syntax validity is not an execution test or a proof of program
correctness.

`AgentLoop.run_status()` and token-protected `GET /runs` expose run IDs, lane, state
and timestamps only. Tool arguments, goals and artifacts are excluded from that API.
The normal `status` conversation command can describe private goals to the user.

## Staged verification

```powershell
.\myenv\Scripts\python.exe -m backtest.test_run_harness --stage storage
.\myenv\Scripts\python.exe -m backtest.test_run_harness --stage lanes
.\myenv\Scripts\python.exe -m backtest.test_run_harness --stage runtime
.\myenv\Scripts\python.exe -m backtest.test_run_harness --stage replay
.\myenv\Scripts\python.exe -m backtest.run_all --unit-only
```

The new tests use temporary stores, fake providers and fake external tools. Replay
tests invoke real CielCore methods without constructing provider clients. They cover
fast/single/multi paths, raw Gmail IDs, approval denial, unattended ceilings, recovery,
search escalation, dependency failures, cancellation, code writes and syntax rejection.
The unified regression keeps existing suites on the compatibility path and exercises
the enabled harness in the dedicated suite. Live providers/mail delivery are outside
this offline regression and are not evidence implied by a passing test result.
