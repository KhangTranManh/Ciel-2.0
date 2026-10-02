# Conventions — Ciel 2.0

## General style

- Python modules use `snake_case.py`; classes use `PascalCase`; functions, variables,
  tool names, and environment keys use established `snake_case` or `UPPER_SNAKE_CASE`.
- Type hints are used on boundaries and persistent data structures.
- Deterministic policies remain pure or independently testable; model clients do not
  own safety decisions.
- Optional features fail open for assistant availability and fail closed for authority.
  A broken memory lookup may disappear; a missing confirmation may not become consent.
- Comments explain why a non-obvious constraint exists, especially when it prevents a
  previously observed failure.

## Model-role convention

The standard model topology is:

```text
BRAIN_PROVIDER + BRAIN_MODEL  -> routing, planning, evaluation
WORKER_PROVIDER + CODER_MODEL -> conversation, generation, synthesis
```

`WORKER_MODEL` is the Python constant derived from `.env` key `CODER_MODEL`. Do not add
an environment variable named `WORKER_MODEL` or a third model role for ordinary work.

Router Assistant and Middleware are optional experiments. If enabled, their model IDs
reuse Brain or Worker identities unless the operator deliberately chooses otherwise.

## Tool-pack registration

ToolManager discovers `skills/**/*_ops.py`. A pack exposes exactly one public factory:

```python
def get_example_tools() -> dict:
    return {
        "tools": [tool_a, tool_b],
        "prompt": "[EXAMPLE]\nUse tool_a when ...",
        "parallel_safe": ["tool_a"],  # optional, explicit allow-list
    }
```

Rules:

- Tool names are globally unique and stable.
- Descriptions state when to use the tool and what evidence it returns.
- Unknown/new tools are sequential by default.
- `parallel_safe` contains read-only, independent calls only.
- Load-time capability removal uses `DISABLED_SKILL_MODULES=<module-stem>`.
- Runtime allow/deny policy remains in permissions; it is not replaced by disabling a
  module.

## Tool contract

Every tool:

1. Validates required inputs and rejects ambiguity it cannot safely normalize.
2. Returns `skills._result.make_result()` with `ok`, `status`, `data`, and structured
   error metadata instead of inventing success prose.
3. Makes repeated equivalent calls safe. Reads are naturally idempotent; writes use a
   stable identity or a preview/confirm split.
4. Reports partial results and missing data explicitly.
5. Returns facts needed by the response: source, date status, path, message ID, page
   count, or other provenance as applicable.
6. Does not bypass `CielCore.execute_tool()` for external delivery or high-risk work.
7. Bounds data-sized output before it enters context assembly.

The tool may be invoked more than once in one request through correction, healing,
continuation, or retry. “The model should call it once” is not a valid safeguard.

## Planner conventions

Monthly planning, weekly planning, and one-time reminders use separate tool packs and
one storage boundary:

- `core/planner_store.py` owns all SQL and schema initialization.
- `skills/internal/monthly_plan_ops.py` owns monthly goal tools.
- `skills/internal/weekly_plan_ops.py` owns weekly task tools.
- `skills/internal/reminder_ops.py` owns one-time reminder tools.
- `core/planner_triggers.py` reads plans and creates notifications; it does not mutate
  plans.
- `core/reminder_triggers.py` reads due reminders and closes them only from notifier
  outcomes.

Formats:

- Month: `YYYY-MM`.
- Week: ISO Monday in `YYYY-MM-DD`; any date input normalizes to that Monday.
- Weekday: `0..6`, Monday through Sunday.
- Local time: 24-hour `HH:MM`.
- Status: `active`, `completed`, or `cancelled`.
- Reminder deadline: ISO-8601; naive values are interpreted in the supplied IANA timezone
  and stored as UTC.
- Reminder status: `pending`, `delivered`, or `cancelled`.
- Reminder text uses canonical argument `title`; `message` is a compatibility alias at
  the tool boundary because provider plans may use either noun.

Identity and history:

- Monthly uniqueness: month plus case-insensitive title.
- Weekly uniqueness: week, case-insensitive title, weekday, and time.
- Equivalent adds return the existing row rather than creating duplicates.
- Completion updates status and keeps the record.
- Weekly tasks may link to a monthly goal; deletion semantics use `ON DELETE SET NULL`.
- Equivalent reminders use case-insensitive title plus UTC deadline for idempotence.
- Timed requests use `add_reminder`; immediate unscheduled todos remain in
  `ciel_workspace/todos.json` and never imply a notification.

## Router and plan JSON

Normalized route actions are `chat`, `tool`, `code`, and `multi_tool`. Tool steps have a
loaded tool name and object-shaped `args`.

```json
{
  "action": "multi_tool",
  "steps": [
    {"tool": "stealth_search", "args": {"query": "..."}},
    {"tool": "smart_scrape", "args": {"url": "{step_0}"}}
  ]
}
```

References point backward only:

- `{prev}` means the immediately previous result.
- `{step_N}` means an earlier numbered result.

Plan validation happens before permissions and execution. Do not duplicate schema,
reference, or tool-existence checks in route-specific branches.

## Context conventions

New prompt context enters through `ContextAssembler.add(name, text, priority)`. Direct
string concatenation is reserved for local formatting within a block, not adding new
cross-cutting context.

- Blocks drop whole under budget pressure.
- Recent turns are Worker-only response context.
- Active Subject is Brain-visible structured state, not raw history.
- Active lookup state is URL-specific and short-lived.
- Fact vault contents are pull-only.
- User-model output is bounded and secret-rejecting.

Current user wording overrides every remembered block.

## Safety conventions

- Permission is resolved on exact `(tool, args)`, never a tool name alone.
- `DENY` outranks grants and flags.
- Unattended risky calls become `DEFER`; deferred work is never replayed automatically.
- Interactive confirmation callbacks fail closed on absence or timeout.
- Dangerous content inside ordinary write/code tools receives the same confirmation as
  named high-risk tools.
- Outbound deduplication keys on recipient/channel and records only successful sends.

See `safety_and_risk.md` for the complete matrix.

## Filesystem conventions

User-operable file tools are restricted to:

- `ciel_workspace/` for input and work-in-progress files.
- `agent_output/` for generated deliverables.

Telegram uploads land under `ciel_workspace/telegram_uploads/`. They are treated as
received inputs, not as write instructions. Reports derived from uploads go to
`agent_output/`.

Windows paths inside a Linux container are recognized only when a sandbox segment is
present. Arbitrary drive paths are rejected rather than guessed.

## Logging and cost

`ciel_data/logs/thoughts.log` is a machine-parsed audit format. Its separator, header,
actor/action fields, and `[LLM_CALL] model=<id> in=<n> out=<n> total=<n>` order are
stable contracts. Readers normalize CRLF; the writer format does not change for UI
presentation.

Trigger audit actions describe outcomes, not merely attempted checks. Successful sends
log `delivered`, digest fallback logs `digested`, unusual suppression may log
`suppressed`, and ordinary cooldown polling remains silent.

Every provider call records the actual model identity and provider-reported usage when
available. Model aliases are measured before adoption because compatible gateways may
inject significant hidden prompt overhead.

User-facing logs never print credentials, OAuth tokens, authorization headers, or full
private `.env` values.

## Prompt harness conventions

`scripts/prompt_harness.py` has four explicit modes:

- `audit` mines recurring signatures deterministically, records whether target ownership
  is direct or inferred, reports the current feature state, and writes a sanitized report
  to the operating-system temporary directory by default.
- `targets` prints the exact `file::symbol` allow-list.
- `propose` stops before any model call for inactive or known non-prompt targets. Brain
  then classifies `prompt`, `code`, `configuration`, `data`, external dependency, or
  insufficient evidence. Worker runs only for a supported prompt-only diagnosis with
  sufficient confidence. The candidate stays outside the repository by default.
- `apply` requires a reviewed candidate plus `--yes`; it validates source and old-value
  hashes, replaces one AST-verified module-level string literal, runs the maintained
  unit suite, and restores the byte-exact original source when tests fail.

The allow-list lives in `scripts/harness_policy.json`. Code-level private-name and
path blocks cannot be disabled by a custom policy. Neither model receives a patching
tool, raw log history, environment variables, credentials, arbitrary file contents, or
Git/deployment authority. The harness never commits a passing candidate automatically.
The value validator rejects newly invented JSON response keys and new instructions that
assign blocking, sending, execution, or regeneration duties to runtime callers. Such
changes belong to reviewed code and focused contract tests.

The operator interface is CLI-only. `scripts/harness_cli.cmd` provides Windows CMD
commands for audit, target listing, report/candidate review, proposal, and explicit
apply. It has no HTTP server, browser UI, environment viewer, Git action, or deployment
action. Its apply command accepts only a candidate filename from the operating-system
temporary harness directory and requires the literal confirmation word `APPLY`.
The operator command reference lives in `docs/prompt_harness_cmd.md`; command details
remain there instead of expanding the public README.
`scripts\harness_cli.cmd full-report` combines all-history finding mining, prompt-target
integrity, static AST tool inventory, and the maintained unit suite in one report under
`agent_output/`. It never interprets static inventory as live provider verification and
never executes external or mutating tools as part of the combined run.

## Interface conventions

- All front ends call the same `AgentLoop` and `CielCore`.
- Each front end supplies `confirm_callback(tool_name, preview, tool_args) -> bool`.
- Confirmation timeout or lost client returns false.
- Cancellation requests the next safe step boundary.
- Telegram checks the chat allow-list before routing.
- UI tool lists come from `GET /skills`; front-end capability lists are not hardcoded.
- Text-to-speech uses the backend normalizer; the persona is not simplified for voice.
- The Flutter app (`ciel_app/`) mirrors the wire protocol in `lib/core/protocol.dart`;
  a backend frame change updates that file and `test/protocol_test.dart` together.
  Only `CielSocket` touches `/ws`; screens use `CielController`.
- Server address and API token are runtime settings in a client, never build constants.
- Error text that leaves the process goes through `core/redact.redact_secrets`.

## Testing conventions

Durable regression modules use `backtest/test_*.py` and are registered in
`backtest/run_all.py`.

```bash
python -m backtest.run_all --unit-only
python -m backtest.run_all --skip-exploratory
```

Focused suites may run directly. Planner behavior belongs in
`backtest/test_planner.py`; reminder persistence and delivery behavior belongs in
`backtest/test_reminders.py`; email rendering, send confirmation, and dedupe belong in
`backtest/test_outbound.py`; API token checks belong in `backtest/test_api_auth.py`
(FastAPI `TestClient` without entering its context, so Ciel never starts); pure
safeguards belong in the closest maintained unit suite. Do not add permanent
`_smoke_*.py` files for one deployment event.

The Flutter app is checked with `flutter analyze` and `flutter test` in `ciel_app/`
(fake transport and HTTP client, no network). `ciel_app/test_live/` runs the app's real
socket and REST code against a running API and is not part of `flutter test`.

Live tests:

- use explicit test labels;
- use isolated temporary database/state files where possible;
- verify provider acceptance, not only model narration;
- verify negative controls and duplicate suppression;
- remove test artifacts;
- never expose secrets in output.

## Documentation conventions

- `README.md` is concise public onboarding and operation.
- `instructionAI/` stores stable architecture, contracts, gotchas, and design reasons.
- `improve.md` stores roadmap state and pass criteria.
- `note.md` stores dated results, provider observations, and deployment events.
- Only `SKILL.md` lists every instruction file.
- Topic files cross-reference instead of duplicating entire explanations.
- Exact dependency versions stay in requirements and lockfiles.

## Safe and dangerous changes

Usually safe when covered by focused tests:

- adding a read-only auto-discovered tool pack;
- adding a trigger with a stable identity and explicit threshold;
- changing UI layout behind the existing bus and protocol;
- adding a provider through the existing model factory;
- extending planner fields through a migration owned by `PlannerStore`.

High-risk changes requiring broad regression and explicit review:

- `core/llm_connector.py` turn ordering;
- permissions, confirmation, unattended, and outbound-delivery guards;
- `thoughts.log` format;
- context routing boundaries;
- RAG collection embedding configuration;
- planner identity/schema rules;
- prompt-harness policy, sanitizer, apply, or rollback boundaries;
- Docker mounts and one-writer assumptions;
- Telegram upload sandbox behavior;
- automatic execution of deferred work;
- `main_api.py` auth middleware, WebSocket token check, and confirm callback;
- `core/redact.py` patterns and the places that call it;
- the per-step `_skip_format` path and `_email_delivery_note` send confirmation;
- the shell allowlist (`os_ops.SAFE_COMMANDS`, `BLOCKED_COMMAND_PATTERNS`).

## Known operational gotchas

- Docker source changes require `--build`; recreation alone keeps the previous image.
- Telegram has no API health route; use container state and the bot-online log.
- API and Telegram cannot safely write the same mounted state concurrently.
- `category:primary` improves Gmail queries but cannot perfectly classify marketing
  mail; presentation logic handles the remaining curation.
- Missing `SEARCH_API_KEY` silently falls back to weaker web sources. CI and runtime
  configuration must both carry the key when primary search is required.
- Headless Docker cannot load screen vision reliably; disable `vision_ops` there.
- PowerShell pipelines require explicit UTF-8 before passing Vietnamese text to Python.
- A Gmail `invalid_grant` at startup leaves Ciel running with 0 Gmail tools; the fix is a
  new `ciel_data/gmail_token.json`, not a new `credentials.json` (see
  `safety_and_risk.md`).
- Packages cannot be installed from Ciel's shell tool; add them to the Dockerfile.
  Installing inside a running container is lost at the next recreate.
- Changing the Dockerfile `apt-get` layer forces a multi-minute rebuild (PyTorch
  reinstalls); code-only changes rebuild from cache in seconds plus image export.
- Self-healing rewrites tool arguments even when the failure is environmental (e.g.
  `git: not found`), and the user sees only the last attempt's error. Check
  `thoughts.log` `HEALING/DETECT_ERROR` for the first, real error.
- Async API handlers must not do blocking file or subprocess work; the vitals loop once
  read the whole multi-MB `thoughts.log` every 2 s per client and stalled confirm frames.
