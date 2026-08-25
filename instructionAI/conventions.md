# Conventions — Ciel 2.0

Code patterns, naming rules, and gotchas for working on this project. For what each of
the seven agent-capability tiers *does* and *why*, see `architecture.md` — this file is
about the patterns that keep them safe to extend.

## Brain → Router → Middleware → Worker Separation

The system strictly separates **routing**, **verification**, and **generation**:

- **Brain / Router** — decides what to do. Outputs JSON with `hidden_thought` + action.
  Never generates final content. For `action == "chat"`, its `task` field is a *hint*,
  not the reply — see the Known Gotchas entry below; a strong model routinely tries to
  pre-write the answer into it.
- **Middleware** *(optional, email-scoped)* — semantically reviews outbound
  email/report bodies AFTER the deterministic sanitizer, BEFORE the safety-gate
  preview. Catches what regex can't (topic mismatch, internal numeric contradictions,
  hollow templated shells). A FINALIZER (edits the body in place), not a blocking gate,
  and **fail-open** on any error. Disabled by default (`MIDDLEWARE_ENABLED=false`).
- **Worker** — generates text/code. Never makes routing decisions. Tenacity retry
  protection.

Each tier is a separate LLM instance, independently configurable (provider + model +
temperature) via `.env`. Brain runs low-temperature for routing; Worker runs slightly
higher for natural generation.

## Tool Pack Registration

1. Create a file (e.g., `skills/external/my_ops.py`).
2. Define a function matching `get_*_tools()` returning
   `{"tools": [StructuredTool, ...], "prompt": "..."}`.
3. `ToolManager.__init__()` auto-discovers all `*.py` files in `skills/internal/` and
   `skills/external/` — no manual registration. The UI's skill grid (`GET /skills`)
   picks it up automatically too.

The `prompt` string is the tool's "manual." **Caveat** (see `PROMPT_INVENTORY.md`):
these per-skill manuals are collected but not fed to the live Brain prompt — the actual
per-tool guidance the Brain sees is `name` + `description[:80]` + arg schema, plus
`_TOOL_HINTS` overrides in `core/llm_connector.py`. If a tool needs stronger routing
guidance, extend `_TOOL_HINTS` rather than assuming the manual is read.

## Skill Contract — what a new tool MUST honour

Auto-discovery means a new pack never touches `core/`. That is exactly why these
invariants matter: nothing stops you breaking them, and the failure shows up later as
wrong output, not an import error.

**1. A tool may be invoked MORE THAN ONCE per user request.** Three independent
mechanisms can re-invoke it: self-healing (up to 3), Brain self-correction (up to 2),
and the Tier-1 loop (up to `AGENT_LOOP_MAX_ROUNDS`). Design every tool to be
**idempotent or preview-only**:
- Read/query tools — inherently fine.
- Tools with outside effects (send, push, delete, pay, post) — split into a **preview**
  tool and a **confirm** tool; let the preview declare the pairing (point 2). The loop
  stops the moment a confirmation is staged, so the Master decides.
- Never make a single call both decide and act irreversibly.

**2. Opt into confirmation via the result, never by editing core.**

```python
return make_result(
    True, data={"message": preview_text}, tool_name="my_preview",
    confirm={"tool": "my_commit", "args": {...}},   # ← the whole registration
)
```

`CielCore.execute_tool` reads `result["confirm"]` generically. A bare "yes" later
resolves it with zero LLM calls and survives a process restart
(`ciel_data/state/pending_action.json`). One pending slot: a new risky request while
one is outstanding is refused deterministically.

**3. Return the standard envelope** — `make_result()` from `skills/_result.py`. A bare
string still works (`ToolManager` wraps it), but it can never opt into point 2, and
`success=False` is the only thing self-healing recognises as failure.

**4. Errors must be shaped, not prose.** `ContinuationPolicy` decides "this step failed"
from the result's opening (`[TOOL_ERROR`, `Error:`, `Lỗi:`, `not found`, `[]`). A tool
reporting failure as a cheerful sentence is invisible to both the healer and the loop.

**5. If a tool lists things, list them line-per-item.** The fan-out signal (Tier 1, S4)
counts entries matching bullet / `1.` / bare-filename lines. A listing returned as one
comma-joined blob reads as a single item, and "do X for each of them" silently stops
after the listing.

**6. Long results: hand over the facts, don't assume the model infers them.** Dates,
sources, and units belong in the tool output (see `stealth_search`'s `Published:`
labels). Add the tool to `_TOOLS_NEEDING_FORMAT` / `_RETRIEVAL_TOOLS` in
`core/llm_connector.py` if it needs completeness-oriented formatting over the terse
default.

**7. Destructive tools go in `_RISK_DESCRIPTIONS`** (`core/llm_connector.py`) so the
Y/N safety gate covers them. Separate from point 2: the gate asks before *this* call,
`confirm=` carries an action across *turns*. Risky tools usually want both.

**8. Declare `parallel_safe` only for tools with no outside effect.** A tool listed
there may run concurrently with others in the same plan (`core/parallel.py`). Omit it
and the tool stays sequential — the safe default. Never list a tool that writes, sends,
deletes, mutates shared state, or is not thread-safe.

**9. An outbound tool must go through `CielCore.execute_tool`**, never a side path —
that is the one choke point where the duplicate-recipient guard and the unattended
`DEFER` ceiling apply (see `safety_and_risk.md`).

## Router JSON Structure

```json
{
  "hidden_thought": {
    "observation": "What the Brain literally sees",
    "reasoning": "Why this action, what was rejected",
    "risk": "Any risk identified or 'none'"
  },
  "action": "chat|tool|code|multi_tool",
  ...action-specific fields...
}
```

`hidden_thought` is logged to `thoughts.log` but never saved to `memory_bank.json`.
`Router.route()` does **not** feed raw `chat_history` into the routing call —
deliberate, to avoid conflating an old unresolved request with a new unrelated one.
Cross-turn continuity for the *response* flows through `_recent_turns_block()`
(Tier 4) and RAG recall instead; the Router only ever sees the current request plus a
recall block bounded and filtered against self-match.

For `action == "chat"`, `task` is a hint the Router may set — it is never handed to the
Worker as the request. See the Known Gotchas entry for why.

## Naming Conventions

- **Modules**: `snake_case` ending in `_ops.py` for tool packs.
- **Factory functions**: `get_*_tools()` — must match this pattern for auto-discovery.
- **Tool names**: `snake_case` matching the function name.
- **Log actors**: `BRAIN`, `WORKER`, `MIDDLEWARE`, `ROUTER`, `HEALING`, `RAG`, `USER`,
  `SAFETY`, `SYSTEM`, `CONTEXT`, `TRIGGER`, `USER_MODEL` — used in `_log_thought()`.
  Every real LLM invocation, across all tiers, also logs a
  `[<TIER>] [LLM_CALL] model=<id> in=<n> out=<n> total=<n>` line.
- **Config keys**: `UPPER_SNAKE_CASE` in both `.env` and `agent_system/config.py`.

## Cost Tracking

Every real LLM call (Brain/Worker/Middleware, including healing/syntax-check sub-calls)
logs one `[LLM_CALL]` entry with EXACT provider token counts
(`agent_system/utils/usage.py:extract_usage()`). `CielCore` accumulates per-tier call
counts, token totals, and an ESTIMATED USD cost (`core/cost.py`, overridable via
`ciel_data/model_pricing.json`) at the single `_log_thought()` chokepoint. Live totals
surface in the API's vitals feed; `scripts/cost_report.py` aggregates historical spend.
**Do not add a second place that counts LLM calls** — extend the chokepoint.

## Audit Trail (`thoughts.log`)

```
[2026-05-21 14:30:00] [ACTOR] [ACTION]
content
------------------------------------------------------------
```

**Never modify this format** — `scripts/format_thoughts_log.py`,
`scripts/prompt_harness.py`, `scripts/cost_report.py`, and `core/triggers.py`'s
`_iter_entries` all parse it. The `[LLM_CALL]` line must keep `model=<id>` as the FIRST
space-delimited token. The file is opened in **text mode**, so on Windows it is 100%
CRLF — any new reader that takes a raw/binary tail must normalise that itself
(`_iter_entries` already does; copy its pattern, don't reinvent it).

Generated views (`thoughts_view.md`, `thoughts_view.jsonl`) are gitignored and
disposable.

## Safe vs Dangerous to Modify

### Safe to change

- Add a new tool pack in `skills/internal/` or `skills/external/` (auto-discovered).
- Edit persona files in `persona/`.
- Adjust RAG thresholds (`MIN_RELEVANCE_SCORE`, `MIN_QUERY_LENGTH`, `DEFAULT_TOP_K`) in
  `rag_manager.py`.
- Add a new Trigger in `triggers.py` (take `now` as a parameter so it stays testable) —
  preferred over adding a raw clock task to `scheduler.py`.
- Add a context block via `ContextAssembler.add(name, text, priority)` — never by
  appending to a prompt string.
- Toggle any tier via `.env`: `AGENT_LOOP_*`, `AGENT_PARALLEL_*`, `CONTEXT_*`,
  `ROUTER_PERSONA_MODE`, `USER_MODEL_*`, `PROACTIVE_*`. Every one degrades to the
  pre-tier behaviour when off.
- Update model names / provider selection in `.env`.
- Add/override LLM prices in `ciel_data/model_pricing.json`.
- Swap voice STT/TTS backends via `STT_BACKEND`/`TTS_BACKEND` in `.env`.
- Add new tests in `backtest/` — prefer the no-LLM style (`test_context.py`,
  `test_proactive.py`, `test_user_model.py`, `test_outbound.py`,
  `test_conversation_bugs.py`, `test_quality_guards.py`, `test_plan_validation.py`) for pure Python decision
  logic. Wire new unit suites into `backtest/run_all.py` `SUITES`. **Do not** add
  long-lived `backtest/_smoke_*.py` one-offs — fold guards into `test_quality_guards`
  or a named `test_*.py` suite.

### Dangerous to change

- **`_log_thought()`'s format** in `llm_connector.py` — breaks log parsing, cost
  accumulation, and `triggers.py::_iter_entries`'s CRLF normalisation.
- **`_outbound_key()` / the duplicate-send guard** in `llm_connector.py` — the only
  thing stopping two mechanisms from delivering the same email twice. Key on the
  recipient, record on success only.
- **`PermissionPolicy.decide(..., attended=)` and `CielCore.unattended`** — the
  unattended ceiling. Keep `unattended` thread-local and keep propagating it into
  parallel workers by hand.
- **`_abort_if_cancelled()` call sites** — cancellation must stay at STEP boundaries.
  Moving a check inside a tool turns a cancellation into a corruption.
- **`ContinuationPolicy.assess()`'s scope veto** must stay the FIRST check, before
  every continuation signal — see Tier 1 in `architecture.md`.
- **The router's `task` field, for `action == "chat"`** — never hand it to
  `execute_chat` as the request. See the Known Gotchas entry below;
  `action == "code"`'s `task` is a different, untouched contract.
- **`UserModel.looks_like_secret()`** — the boundary keeping credentials out of a store
  injected into every prompt. Keep normalising `_`/`-` to spaces before matching.
- **`_is_safe_path()` logic** in `system_ops.py` — weakens sandbox quarantine.
- **`_HIGH_RISK_TOOLS` / `_RISK_DESCRIPTIONS`** in `llm_connector.py` — removing tools
  disables safety checks.
- **`_find_dangerous_code_patterns()`** in `llm_connector.py` — the content-based gate
  for generated code.
- **`_has_unsynthesized_placeholder()`** in `llm_connector.py` — matches ANY paraphrased
  synthesis-placeholder marker, not one fixed string. Used by both the multi_tool
  deferred-write detection and the write/email placeholder guards; a stronger Brain
  once paraphrased the canonical marker and an exact-string check let a hollow shell
  reach disk.
- **`_resolve_step_refs()`** in `llm_connector.py` — deterministic `{{prev}}`/
  `{{step_N}}` substitution. Keep the fast-path (`"{{" not in value`) so token-free
  plans stay untouched.
- **`stealth_search`'s source chain** in `web_agent_ops.py` — Google News RSS is
  PRIMARY specifically because it is unofficial and the `ddgs` fallbacks exist for
  when it fails. Don't collapse the chain, drop `_is_generic_news_query()`, drop the
  real-`pubDate` recency filter, or reorder past "filter landing pages BEFORE trimming
  to `max_results`".
- **Router JSON parsing** in `router.py` — `_extract_json_object()` slices the first
  balanced `{...}` before `json.loads`, tolerating prose some models wrap around the
  decision.
- **`memory_ops.py` imports** — must remain standalone (no `core/`/`agent_system/`
  deps).
- **`thoughts.log`'s file path** — hardcoded in `llm_connector.py`, `main_api.py`, and
  `format_thoughts_log.py`.
- **Coupling `SAFETY_OPEN` and `DISABLE_SAFETY_GATE`** — independent by design (see
  `safety_and_risk.md`).
- **`to_speech()`'s normalizer being replaced by prompt changes** — voice cleanup stays
  deterministic post-processing, never a persona rewrite.

## Known Gotchas

**Environment / providers**
1. `agent_system/config.py` reads the Worker's model from `CODER_MODEL`, not
   `WORKER_MODEL` — a `.env` entry literally named `WORKER_MODEL` has no effect.
2. Measure a new model alias before adopting it: gateways can inject thousands of
   hidden, unsuppressable tokens per call. See the provider table in `note.md`.

**Windows-specific**
3. The Router prompt includes WINDOWS SYSTEM ARCHITECT rules enforcing double-quoted
   absolute paths and a `python -m` prefix — without this, tools fail on paths with
   spaces.
4. `thoughts.log` is 100% CRLF on Windows (text-mode writes). A binary/raw tail read
   must normalise newlines itself or every check silently reports "nothing found".
5. PowerShell's legacy pipeline encoding can replace Vietnamese/Unicode characters with
   `?` before Python receives them. Before `@' … '@ | python -`, set
   `[Console]::InputEncoding`, `[Console]::OutputEncoding`, `$OutputEncoding`, and
   `PYTHONUTF8` to UTF-8. Do not diagnose a malformed subject as a Gmail problem when
   the same `?` is already visible in the `execute_tool` log.

**Integration quirks**
6. Vision prompts in `vision_ops.py` use Python `.format()` — curly braces in prompt
   text must be doubled (`{{`/`}}`) or the app crashes with `Single '}' in format
   string`.
6. `langchain_google_community` has a known typo (`client_sercret_file` vs
   `client_secrets_file`); `gmail_ops.py` and `scheduler.py` handle both via
   `inspect.signature()`.
7. `edge-tts` (default TTS) hits an unofficial Microsoft endpoint that intermittently
   raises `NoAudioReceived` — the fix is `_edge_synth_bytes()`'s backoff retry, not a
   different text/voice.

**Degradation and isolation**
8. `rag_manager.py` lazy-loads ChromaDB/sentence-transformers; if missing, RAG degrades
   gracefully and CielCore keeps working with JSON-only short-term memory.
9. Self-correction retries (Brain evaluating tool results) are not saved to chat memory
   or RAG, to prevent noise accumulation.
10. Scheduled/legacy clock tasks must never write to `memory_bank.json` or call
    `rag_manager.embed_and_save()` (Ghost Mode).
11. `_HEALING_SKIP_PATTERNS` in `llm_connector.py` short-circuits error classes no
    retry could ever fix (missing library, network timeout, geo-restriction) — removing
    this is not "more robust", it just burns a guaranteed-to-fail Worker call each time.

**Conversation-memory rules (found live — see Tier 4 in `architecture.md`)**
12. `chat_history` reaches the model in exactly TWO places, both response-side, never
    routing: `_recent_turns_block()` feeding `execute_chat` and the tool-result format
    path. Do not add it to `router.route()`'s input — `test_conversation_bugs.py`
    asserts the router never sees it.
13. The router's `task` field, for `action == "chat"`, is a HINT — never the reply. A
    strong Brain routinely pre-writes the actual final reply into it (once caught
    literally: `"task": "Reply: \"...\""`), which bypasses the persona's
    language-matching rule and any recent-turns context if handed to the Worker as the
    request. `action == "code"`'s `task` is a different, untouched contract (a spec to
    execute, not a pre-written answer).
14. `ContinuationPolicy.assess()`'s scope veto must stay checked FIRST: an explicit
    "chỉ … thôi" / "đừng …" / "only …" outranks every continuation signal, including
    fan-out.
15. **"send it to that email" is a RECIPIENT reference, not a content reference** — a
    real email once went to the wrong address because only the body-referential guard
    (`_is_referential_send`) existed. `_resolve_referential_recipient()` grounds `to` in
    `chat_history` when the current turn names no address of its own, and must run at
    the TOP of `execute_multi_tool` — before the report body is synthesized — or the
    body ends up narrating the WRONG address even though the send itself goes to the
    right one. When the override fires, the synthesis prompt must be told the confirmed
    recipient explicitly (`recipient_override_note`); don't trust the model to have
    reached the same correction on its own.
16. **A chat response cannot claim that a tool is running.** `action == "chat"` gives
    the Worker no tool handle. `_block_unbacked_chat_tool_promise()` rejects a concrete
    search/tool promise such as "running `stealth_search`" or "waiting for the results"
    and returns an honest clarification instead; it does not create a fake pending task.
    The matcher must not treat ordinary language such as "I will call you Master" as a
    tool call. This is a code guard as well as an `execute_chat()` prompt boundary
    because Router and Worker see intentionally different context.
17. **A successful live lookup may ground one narrow follow-up.** `_active_lookup`
    retains only the query and up to three public URLs in RAM for 15 minutes. It reaches
    the Router when the next request explicitly says to scrape/read/deepen that result,
    or uses a terse read-only imperative such as “làm đi” / “mở đi” while exactly one
    URL is live. Several cached URLs require a choice unless the Master explicitly asks
    for all of them. `_apply_active_lookup_route_override()` enforces the resulting
    read-only `smart_scrape` plan after model routing, so a model cannot misclassify a
    terse continuation as chat. It then clears on a new topic and on restart. It is not
    a shortcut for feeding general `chat_history` or Tier-2 task records into routing.
    It must remain available even when `CONTEXT_RECENT_TURNS_ENABLED=false`, because its
    URLs came from a real tool this process just ran, not from raw conversation history.
    Keep the regression in `backtest/test_conversation_bugs.py`.
