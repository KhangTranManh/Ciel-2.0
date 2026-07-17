# Conventions — Ciel 2.0

Code patterns, naming rules, and gotchas for working on this project.

## Brain → Middleware → Worker Separation

The system strictly separates **routing**, **verification**, and **generation**:

- **Brain** — Decides what to do. Outputs JSON with `hidden_thought` + action. Never generates content.
- **Middleware** *(optional, email-scoped)* — Semantically reviews outbound email/report bodies AFTER
  the deterministic sanitizer, BEFORE the safety-gate preview. Catches what regex can't (topic
  mismatch, internal numeric contradictions, hollow templated shells). Acts as a FINALIZER (edits
  the body in place) not a blocking gate, and is **fail-open** on any error — a Middleware hiccup
  must never block a send. Disabled by default (`MIDDLEWARE_ENABLED=false`).
- **Worker** — Generates text/code. Never makes routing decisions. Has tenacity retry protection.
- **Router** — Uses Brain to classify intent into `chat`, `tool`, `code`, or `multi_tool`.

Each tier is a separate LLM instance, independently configurable (provider + model + temperature)
via `.env`. Brain runs deterministic-leaning (low temperature) for routing; Worker runs slightly
higher for natural generation.

## Tool Pack Registration

New tools are added by creating a module in `skills/internal/` or `skills/external/`:

1. Create a file (e.g., `skills/external/my_ops.py`).
2. Define a function matching `get_*_tools()` that returns `{"tools": [StructuredTool, ...], "prompt": "..."}`.
3. `ToolManager.__init__()` auto-discovers all `*.py` files in `skills/internal/` and `skills/external/`.
4. No manual registration needed — just drop the file in the right folder. The UI's skill grid
   (`GET /skills`) picks it up automatically too.

The `prompt` string is the tool's "manual." **Caveat (see `PROMPT_INVENTORY.md`):** these per-skill
manuals are collected but not fed to the live Brain prompt in the current build — the actual
per-tool guidance the Brain sees is `name` + `description[:80]` + arg schema, plus `_TOOL_HINTS`
overrides in `core/llm_connector.py`. If a tool needs stronger routing guidance, add/extend a
`_TOOL_HINTS` entry rather than assuming the manual is read.

## Router JSON Structure

Every Router response follows this exact format:

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

The `hidden_thought` is logged to `thoughts.log` for debugging but never saved to `memory_bank.json`.
`Router.route()` does NOT feed raw `chat_history` into the routing call (removed deliberately —
see architect.md Changelog) to avoid conflating an old unresolved request with a new unrelated one;
cross-turn continuity flows through RAG recall and a deterministic `_is_referential_send()` check.

## Naming Conventions

- **Modules**: `snake_case` ending in `_ops.py` for tool packs (e.g., `gmail_ops.py`, `trading_ops.py`).
- **Factory functions**: `get_*_tools()` — must match this pattern for auto-discovery.
- **Tool names**: `snake_case` matching the function name (e.g., `get_market_price`, `execute_shell_command`).
- **Log actors**: `BRAIN`, `WORKER`, `MIDDLEWARE`, `ROUTER`, `HEALING`, `RAG`, `USER`, `SAFETY` —
  used in `_log_thought()`. `MIDDLEWARE` actions: `REVIEWED`, `REVISED`, `FLAGGED_UNFIXABLE`,
  `REVIEW_ERROR`. Every real LLM invocation across all three tiers also logs a
  `[<TIER>] [LLM_CALL] model=<id> in=<n> out=<n> total=<n>` line (see Cost Tracking below).
- **Config keys**: `UPPER_SNAKE_CASE` in both `.env` and `agent_system/config.py`.

## Cost Tracking (added July 2026)

Every real LLM call (Brain/Worker/Middleware, including healing/syntax-check sub-calls) logs one
`[LLM_CALL]` entry with EXACT provider token counts (`agent_system/utils/usage.py:extract_usage()`).
`CielCore` accumulates per-tier call counts, token totals, and an ESTIMATED USD cost
(`core/cost.py`, pricing overridable via `ciel_data/model_pricing.json` — no code change) at the
single `_log_thought()` chokepoint. Live totals surface in the API's vitals feed;
`scripts/cost_report.py` aggregates historical spend by tier/model/day from `thoughts.log`.
**Do not** add a second place that counts LLM calls — extend the chokepoint instead.

## Audit Trail (`thoughts.log`)

Every Brain/Worker/Middleware decision is logged to `ciel_data/logs/thoughts.log` in this format:

```
[2026-05-21 14:30:00] [ACTOR] [ACTION]
content
------------------------------------------------------------
```

- **Never modify this format** — `scripts/format_thoughts_log.py`, `scripts/prompt_harness.py`,
  and `scripts/cost_report.py` all parse it. In particular, the `[LLM_CALL]` content line must keep
  `model=<id>` as the FIRST space-delimited token (existing `re.search(r"model=(\S+)")` consumers
  depend on it).
- Generated views (`thoughts_view.md`, `thoughts_view.jsonl`) are gitignored and disposable.

## Safe vs Dangerous to Modify

### Safe to change

- Add new tool pack in `skills/internal/` or `skills/external/` (auto-discovered).
- Edit persona files in `persona/`.
- Adjust RAG thresholds (`MIN_RELEVANCE_SCORE`, `MIN_QUERY_LENGTH`, `DEFAULT_TOP_K`) in `rag_manager.py`.
- Add new scheduled tasks in `scheduler.py` (follow Ghost Mode pattern).
- Update model names / provider selection in `.env`.
- Add/override LLM prices in `ciel_data/model_pricing.json`.
- Swap voice STT/TTS backends via `STT_BACKEND`/`TTS_BACKEND` in `.env`.
- Add new tests in `backtest/`.

### Dangerous to change

- **`_log_thought()` format** in `llm_connector.py` — breaks log parsing and cost accumulation.
- **`_is_safe_path()` logic** in `system_ops.py` — weakens sandbox quarantine.
- **`_HIGH_RISK_TOOLS` / `_RISK_DESCRIPTIONS`** in `llm_connector.py` — removing tools disables safety checks.
- **`_find_dangerous_code_patterns()`** in `llm_connector.py` — the content-based gate for generated code.
- **Router JSON schema** in `router.py` — must match parsing in `CielCore.process()`.
- **`memory_ops.py` imports** — must remain standalone (no `core/` or `agent_system/` deps).
- **`thoughts.log` file path** — hardcoded in `llm_connector.py`, `main_api.py`, and `format_thoughts_log.py`.
- **Coupling `SAFETY_OPEN` and `DISABLE_SAFETY_GATE`** — they are independent by design (see safety_and_risk.md).
- **`to_speech()` normalizer being replaced by prompt changes** — voice cleanup must stay
  deterministic post-processing, not a persona rewrite (see voice_and_interface.md).

## Known Gotchas

1. **Windows paths**: The Router prompt includes WINDOWS SYSTEM ARCHITECT rules enforcing double-quoted
   absolute paths and `python -m` prefix. Without this, tools fail on paths with spaces.

2. **Format string escaping**: Vision prompts in `vision_ops.py` use Python `.format()` — curly braces
   in prompt text must be doubled (`{{` and `}}`) or the app crashes with `Single '}' in format string`.

3. **Gmail credential signature**: `langchain_google_community` has a known typo (`client_sercret_file`
   vs `client_secrets_file`). The code in `gmail_ops.py` and `scheduler.py` handles both via
   `inspect.signature()`.

4. **RAG lazy loading**: `rag_manager.py` lazy-loads ChromaDB and sentence-transformers on first use.
   If these packages are missing, RAG degrades gracefully — CielCore still works with JSON-only
   short-term memory.

5. **Self-correction isolation**: Self-correction retries (Brain evaluating tool results) are not
   saved to chat memory or RAG to prevent noise accumulation.

6. **Scheduler Ghost Mode**: Scheduled tasks must never write to `memory_bank.json` or call
   `rag_manager.embed_and_save()`. They use raw API calls and a single Worker call for formatting.

7. **`WORKER_MODEL` naming trap**: `agent_system/config.py` reads the Worker's model from
   `CODER_MODEL`, not `WORKER_MODEL`. A `.env` entry literally named `WORKER_MODEL` has no effect.

8. **Healing skip-list**: not every tool error is worth an autonomous retry. `_HEALING_SKIP_PATTERNS`
   in `llm_connector.py` short-circuits errors no parameter guess could ever fix (missing library,
   network timeout, geo-restriction) — don't remove this expecting "more retries = more robust";
   it was added because those retries were guaranteed to fail and cost a real LLM call each time.

9. **Free/unofficial voice backends can be flaky**: the default TTS backend (`edge-tts`) hits an
   unofficial Microsoft endpoint that intermittently raises `NoAudioReceived` — the fix is a
   backoff retry (`_edge_synth_bytes()`), not a different text/voice. The default STT backend is
   local `faster-whisper`, chosen specifically to avoid this class of problem for voice input.
