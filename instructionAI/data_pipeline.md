# Data Pipeline — Ciel 2.0

## Data ownership

Ciel separates conversation, facts, user preferences, execution state, planning, and
notification state because they have different authority and retention rules.

| Store | Owner | Purpose | Prompt injection |
|---|---|---|---|
| Recent chat history | LangChain file history | Short response continuity | Worker only, bounded |
| `ciel_data/session_compact.json` | `core/session_compact.py` | Bounded factual session continuity | Worker only, bounded |
| ChromaDB vector memory | `core/rag_manager.py` | Semantic long-term recall | Retrieved, filtered, bounded |
| `ciel_data/facts.json` | `skills/internal/memory_ops.py` | Explicit fact vault | Never automatic; pull-only |
| `ciel_data/user_model.json` | `core/user_model.py` | Preferences with provenance | Bounded, secret-rejecting |
| `ciel_data/state/tasks.json` | `core/task_state.py` | Durable job lifecycle | Status path, not routing |
| `ciel_data/runs.db` | `core/harness/store.py` | Runtime runs, steps, bounded artifacts and events; one-time legacy task import | Never injected; status metadata only |
| `ciel_data/planner.db` | `core/planner_store.py` | Monthly goals, weekly actions, and one-time reminders | Through planner/reminder tools and triggers |
| `ciel_workspace/todos.json` | productivity tools | Immediate unscheduled checklist | Through todo tools/triggers |
| `ciel_data/state/daily_brief.json` | `core/daily_brief.py` | Bounded same-day D-reference identity snapshot | Only after deterministic reference validation |
| `ciel_data/state/notify.json` | `core/notifier.py` | Cooldowns, budget, repeat state | Never |
| `ciel_data/state/deferred.json` | `core/permissions.py` | Blocked unattended actions | Summary only; never replayed |
| `ciel_data/logs/thoughts.log` | core logger | Chronological audit and usage | Read by diagnostics/triggers; streamed to API clients only when `CIEL_API_STREAM_THOUGHTS=true` |
| `ciel_data/gmail_token.json` | `skills/external/gmail_ops.py` | Gmail OAuth access/refresh token | Never; replaced by re-authorizing when it expires |

`core/daily_brief.py` does not own task data. It derives a bounded projection over
planner, todo, and run metadata. Its small state file stores only D-reference, source
type/ID, title, generation/expiry time, date, and timezone; it contains no due/status
copy, run argument/artifact, pending confirmation, permission, or consent.

## Per-turn context flow

```text
User message
  ├─> RAG query -> self-match filter -> bounded recall
  ├─> user model -> live, non-secret, budgeted traits
  ├─> active subject -> compact Brain-visible session state
  ├─> session compact -> factual Worker-visible session state
  ├─> cwd/language/runtime facts
  └─> ContextAssembler -> prioritized whole blocks -> Brain

Brain route -> tools/results -> Worker
Worker additionally receives bounded recent raw turns
Final response -> chat persistence + RAG archive + subject/profile updates
```

`ContextAssembler` is the only cross-cutting prompt assembly boundary. Every block has
a name, priority, and estimated size. Under pressure, a lower-priority block is removed
whole; partial context is never presented as a complete fact.

## Short-term conversation

Recent messages are persisted by the existing LangChain history backend. The response
path renders only the newest complete turns within `CONTEXT_RECENT_TURNS_BUDGET`.

Raw history is deliberately absent from Brain routing. Earlier designs let unresolved
requests leak into new turns and bias tool selection. Conversation history therefore
helps Worker interpret “why?” or “do it,” but cannot authorize or route an action.

## Session Compact

`core/session_compact.py` maintains a persisted, Worker-only factual summary of the
current conversation. It refreshes asynchronously after a bounded number of completed
turns, so compaction never delays the reply that created it. The summary keeps active
topics, confirmed facts, decisions, constraints, and unresolved questions; it redacts
secret-like material, email addresses, URLs, and filesystem paths before the summarizer
sees the transcript or any compact is persisted.

The compact enters the response prompt through `ContextAssembler` alongside recent raw
turns. It never enters Router planning and is labeled as background rather than an
instruction or authorization. Daily cleansing clears it with the raw session history;
older information remains available through RAG.

## Active Subject

`core/active_subject.py` supplies the Brain with one structured, RAM-only subject:

- topic extracted from successful read-tool arguments;
- at most `ACTIVE_SUBJECT_MAX_ENTITIES` entities grounded in tool evidence;
- last completed read action;
- age and unrelated-turn count.

Lifecycle:

1. `begin_turn()` expires stale state and renders the current snapshot.
2. Successful read-only tools contribute evidence during the turn.
3. `complete_turn()` commits one new snapshot after the final response, or ages the old
   snapshot when no grounded evidence was produced.
4. State clears after `ACTIVE_SUBJECT_TTL_SECONDS`,
   `ACTIVE_SUBJECT_MAX_IDLE_TURNS`, explicit reset, or process restart.

Recipients, email addresses, URLs, filesystem paths, secrets, commands, confirmations,
and destructive arguments are rejected. Active Subject resolves reference, not consent.

## Active lookup context

Web lookup follow-ups use a separate bounded channel. A successful search retains its
query and at most three public source URLs for a short time. An explicit “open/read this”
or a terse read-only follow-up can select `smart_scrape` deterministically.

One URL can proceed directly. Several URLs require an explicit “all” request or a user
choice. A new topic clears the state. The channel cannot become a general history or
route to mutating tools.

## Long-term RAG

ChromaDB stores semantic conversation memories using the collection's established
sentence-transformer embedding function. Runtime retrieval:

1. creates an embedding for the current request;
2. retrieves nearby memories;
3. removes empty, duplicate, and strong self-matches;
4. compresses or clips recall at its source;
5. submits one bounded `rag_recall` context block.

If PyTorch, sentence-transformers, or the collection cannot initialize, Ciel disables
RAG and continues. Replacing the embedding function does not migrate an existing
collection; it produces incompatibility at query time.

Inspection tools can fall back to memory only when live inspection is unavailable and
the stored material is clearly labeled historical. Memory never upgrades old evidence
into a current filesystem, inbox, market, or deployment claim.

## Fact vault and user model

### Fact vault

`facts.json` is explicit pull-only data. `memory_ops.py` reads and writes it without
importing `core/` or `agent_system/`. The model must intentionally call a fact tool;
vault contents never enter the prompt wholesale.

Lookups are case-insensitive and misses return available keys rather than encouraging a
guess. Because the vault may contain sensitive facts, it is excluded from automatic
context and public source control.

### User model

`user_model.json` contains preferences that improve outputs when injected. Each trait
has provenance and freshness. Explicitly stated preferences outrank inferred ones;
inferred traits decay; rendering is capped by `USER_MODEL_TOKEN_BUDGET`.

The storage boundary rejects secret-like keys and values. Learning is optional and
begins with deterministic `assess_preference()`; the model extracts a candidate only
when the wording indicates a durable preference. Daily limits prevent chat volume from
creating unbounded extraction calls.

The vault and profile never merge: one may contain secrets and is pull-only; the other
is injected and therefore must be safe to expose to a model.

## Durable task state

`TaskStore` records a job ID, goal, steps, completion count, state, and timestamps.
Active records loaded after a crash become `interrupted`. They can be reported or used
to guide a new request, but are not silently resumed and do not feed Brain routing.

Cancellation closes the current job at the next step boundary. Tool completion is
recorded only after the tool returns.

## Planner data

`PlannerStore` owns a SQLite database with three tables:

```text
monthly_goals
  id, month, title, notes, status, created_at, updated_at

weekly_tasks
  id, week_start, title, weekday, local_time,
  monthly_goal_id, notes, status, created_at, updated_at

reminders
  id, title, due_at_utc, timezone, status,
  notified_at, created_at, updated_at
```

Monthly goals are high-level outcomes. Weekly tasks are concrete actions and may link
to a monthly goal. Reminders are one-time timed notifications. Immediate todos remain a
separate lightweight unscheduled checklist.

SQLite settings include foreign keys, a busy timeout, per-operation connections, and
transactional writes. Unique indexes make equivalent retries idempotent. Completion
retains history rather than deleting records.

Planner tools are always available. Planner announcements require named proactive
triggers:

```dotenv
PROACTIVE_ENABLED=true
PROACTIVE_TRIGGERS=daily_brief,monthly_plan,weekly_plan,reminder_due
```

`PLANNER_TIMEZONE`, `PLANNER_MONTHLY_*`, and `PLANNER_WEEKLY_*` define local schedule
boundaries. Monthly day is clamped to 1–28; weekly weekday uses Monday `0` through
Sunday `6`.

## Proactive scheduling

`core/proactive_setup.py` is shared by CLI, API, and Telegram. It creates:

1. available delivery channels;
2. one `Notifier` using `ciel_data/state/notify.json`;
3. general condition triggers from `core/triggers.py`;
4. planner triggers from `core/planner_triggers.py`;
5. reminder triggers from `core/reminder_triggers.py`;
6. Daily Brief trigger and optional external context;
7. one `TriggerEngine` attached to `Scheduler`.

The scheduler polls Python checks. A trigger returns `None` or a `Notification`; it does
not decide delivery policy.

`Notifier` applies, in order:

1. action contract — actionless notifications become digest items;
2. stable-key cooldown;
3. per-key repeat muting;
4. daily interruption budget;
5. first live channel that accepts delivery;
6. digest fallback when no channel succeeds.

## Daily Brief Phase 1

`get_daily_brief` answers on-demand questions about today's work. The collector uses
`PLANNER_TIMEZONE`, then deterministically combines:

- active weekly tasks scheduled for the requested day plus unscheduled tasks in that week;
- pending reminders that are overdue or inside the bounded look-ahead window;
- open immediate todos;
- run goal/state/timestamps only for `interrupted` and `waiting_for_approval` runs;
- up to five active monthly goals as context, never as invented daily actions.

Priority order is overdue reminder, near reminder, scheduled daily action, later
reminder, waiting/interrupted run, unscheduled weekly action, then loose todo. The result
is capped, reports omitted items and unavailable sources, and reaches Worker once for
persona/language presentation. Run arguments and artifacts never enter the projection.
Imported runs whose reason is `legacy_history` are audit-only and never become current
Daily Brief actions. If the Worker presentation call fails, a deterministic prose
renderer preserves the grounded items without exposing the JSON transport schema.
Phase 1 has no snapshot, reference-action resolution, scheduler trigger, Gmail, weather,
or Calendar integration.

## Daily Brief Phase 2

After a successful current-day brief, `DailyBriefReferenceStore` atomically replaces
`ciel_data/state/daily_brief.json` with at most 20 identity records. A brief requested
for another date clears that snapshot. Resolution requires the explicit `D` prefix,
exactly one distinct D-number, the configured local date, and both persisted and
configured TTL validity. Missing, stale, unknown, duplicated, or multi-reference input
stops before Router invocation.

A valid identity enters `ContextAssembler` as a critical reference-data block. The
Brain still interprets the current action, while Python binds selected tools back to the
verified source ID and title: todo completion, weekly completion/update, reminder
creation/cancellation, and weekly scheduling all continue through their existing tool,
validation, permission, and run boundaries. Unsupported source/tool combinations stop
without execution.

An explicit interrupted/waiting run retry uses only the stored public goal as a fresh
run goal. After a new run is actually created, `RunStore` links the two run IDs and
closes the old record with `approval_inherited=false` and
`historical_args_reused=false`. It never reads or copies historical steps, artifacts,
pending confirmations, plan grants, or consent. Phase 2 still has no proactive delivery.

## Daily Brief Phase 3

`daily_brief` is a separately named proactive trigger and remains off unless both
`PROACTIVE_ENABLED=true` and `PROACTIVE_TRIGGERS` include it. It uses
`PLANNER_TIMEZONE` with `DAILY_BRIEF_HOUR` and `DAILY_BRIEF_MINUTE`, and evaluates at or
after that local time so a process started late can catch up on the current day.

The background path invokes no model. It collects the same bounded local projection,
atomically writes the identity-only D-reference snapshot, renders deterministic
Vietnamese prose, and emits `daily_brief:<YYYY-MM-DD>` only when at least one action is
present. The shared Notifier routes it to a live channel (Telegram on the API/Telegram
service), retries after provider failure, removes any temporary digest copy after a
successful retry, and persists cooldown state only after success. Ordinary cooldown
polls remain absent from `thoughts.log`.

## Daily Brief Phase 4

External context runs only for explicitly enabled sources. Gmail contributes at most
five unread Primary records containing From, Subject, and Date—never body or snippet.
Weather contributes one current-condition object with city, condition, Celsius,
feels-like Celsius, and humidity. Calendar contributes at most ten upcoming records
containing only title/start/end/all-day metadata. These fields are presentation context;
they never enter the D-reference identity snapshot.

Each provider call is isolated. Failure adds `gmail`, `weather`, or `calendar` to
`external_unavailable` while preserving the local brief and successful external
sources. An empty local action list remains silent for proactive delivery even if
external context exists.

Calendar uses `skills/external/calendar_ops.py` and a separate
`ciel_data/calendar_token.json`. `list_calendar_events` is a read-only tool but stays
sequential because the pack closures share one Google API transport. Create, update,
and delete pass through normal exact-call confirmation; unattended requests become
DEFER and are never replayed automatically.

Successful delivery updates state only after the channel returns success. A provider
failure therefore remains retryable.

### One-time reminder lifecycle

`add_reminder` accepts either an ISO-8601 deadline or a relative `delay_minutes` value.
The store normalizes the deadline to UTC while retaining the display timezone. If
`reminder_due` is disabled, creation fails explicitly rather than saving an alert that
cannot fire.

The scheduler checks pending due reminders once per minute. Notification identity is
`reminder:<id>`. The reminder becomes `delivered` only after notifier success; a failed
send stays pending. Persisted cooldown also repairs the narrow crash window in which the
provider succeeded but the database status update did not complete.

### Planner notification identity

- Monthly: `monthly_plan:<YYYY-MM>` with a cooldown longer than one month.
- Weekly: `weekly_plan:<ISO-Monday>` with a cooldown longer than one week.

Checks are catch-up aware. If the process was offline at the configured instant, the
current period can still be announced later after startup. Empty plans do not emit a
monthly notification; weekly output may combine structured weekly actions with open
immediate todos.

## Audit and usage

`thoughts.log` is append-only chronological evidence for debugging, trigger checks,
cost reporting, and prompt harnesses. Its CRLF-normalized parser contract is documented
in `SKILL.md` and `conventions.md`.

The optional `capability_loss` trigger snapshots ToolManager's secret-free startup
statuses. Only `unavailable` and `error` packs become notifications; `ready`, `disabled`,
and `not_configured` states stay silent. Stable module/reason keys use Notifier cooldown
and persistence, so repeated scheduler polls or restarts do not spam the same outage.

Provider calls emit model ID and input/output/total token counts when available. Usage
is operational evidence, not durable project knowledge; dated numbers belong in
`note.md`, not this file.

## Persistence and deployment

Container deployments bind-mount:

- `ciel_data/`
- `ciel_workspace/`
- `agent_output/`
- private `.env`
- private `ciel_data/credentials.json`

Rebuilding the image must not replace these paths. One Ciel process writes the shared
state at a time. Live smoke tests use temporary planner/notifier state, verify delivery
and duplicate suppression, and remove artifacts without touching production data.
