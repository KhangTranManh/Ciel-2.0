# Data Pipeline — Ciel 2.0

## Data ownership

Ciel separates conversation, facts, user preferences, execution state, planning, and
notification state because they have different authority and retention rules.

| Store | Owner | Purpose | Prompt injection |
|---|---|---|---|
| Recent chat history | LangChain file history | Short response continuity | Worker only, bounded |
| ChromaDB vector memory | `core/memory.py` | Semantic long-term recall | Retrieved, filtered, bounded |
| `ciel_data/facts.json` | `skills/internal/memory_ops.py` | Explicit fact vault | Never automatic; pull-only |
| `ciel_data/user_model.json` | `core/user_model.py` | Preferences with provenance | Bounded, secret-rejecting |
| `ciel_data/state/tasks.json` | `core/task_state.py` | Durable job lifecycle | Status path, not routing |
| `ciel_data/planner.db` | `core/planner_store.py` | Monthly goals, weekly actions, and one-time reminders | Through planner/reminder tools and triggers |
| `ciel_workspace/todos.json` | productivity tools | Immediate unscheduled checklist | Through todo tools/triggers |
| `ciel_data/state/notify.json` | `core/notifier.py` | Cooldowns, budget, repeat state | Never |
| `ciel_data/state/deferred.json` | `core/permissions.py` | Blocked unattended actions | Summary only; never replayed |
| `ciel_data/logs/thoughts.log` | core logger | Chronological audit and usage | Read by diagnostics/triggers; streamed to API clients only when `CIEL_API_STREAM_THOUGHTS=true` |
| `ciel_data/gmail_token.json` | `skills/external/gmail_ops.py` | Gmail OAuth access/refresh token | Never; replaced by re-authorizing when it expires |
| App chat history (device) | `ciel_app` `ChatHistoryStore` | Last 200 turns for display on that device | Never sent back as context |

## Per-turn context flow

```text
User message
  ├─> RAG query -> self-match filter -> bounded recall
  ├─> user model -> live, non-secret, budgeted traits
  ├─> active subject -> compact Brain-visible session state
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
PROACTIVE_TRIGGERS=monthly_plan,weekly_plan,reminder_due
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
6. one `TriggerEngine` attached to `Scheduler`.

The scheduler polls Python checks. A trigger returns `None` or a `Notification`; it does
not decide delivery policy.

`Notifier` applies, in order:

1. action contract — actionless notifications become digest items;
2. stable-key cooldown;
3. per-key repeat muting;
4. daily interruption budget;
5. first live channel that accepts delivery;
6. digest fallback when no channel succeeds.

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

Provider calls emit model ID and input/output/total token counts when available. Usage
is operational evidence, not durable project knowledge; dated numbers belong in
`note.md`, not this file.

## Persistence and deployment

Container deployments bind-mount:

- `ciel_data/`
- `ciel_workspace/`
- `agent_output/`
- private `.env`
- private `credentials.json`

Rebuilding the image must not replace these paths. One Ciel process writes the shared
state at a time. Live smoke tests use temporary planner/notifier state, verify delivery
and duplicate suppression, and remove artifacts without touching production data.
