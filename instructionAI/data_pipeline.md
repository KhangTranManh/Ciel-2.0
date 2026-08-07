# Data Pipeline — Ciel 2.0

How Ciel stores, recalls, and manages memory and scheduled data flows. For *why* the two
memory stores are split and the full authority/decay/learning rules of the user model,
see Tier 7 in `architecture.md` — this file covers the mechanics: what's on disk, how
recall actually runs, and its known failure modes.

## Hybrid Memory Architecture

```
Short-Term (JSON)                    Long-Term (ChromaDB)
┌─────────────────────┐              ┌─────────────────────────┐
│ memory_bank.json    │   overflow   │ ciel_data/vector_memory/│
│ Max 20 messages     │ ──────────→  │ ChromaDB + MiniLM-L6-v2 │
│ LangChain history   │              │ Semantic search         │
└─────────────────────┘              └─────────────────────────┘
```

### Short-Term Memory

- **File**: `ciel_data/memory_bank.json` — JSON array of
  `{"type": "human|ai", "content": "..."}`.
- **Max size**: 20 messages (`self.max_history`). Overflow archives into ChromaDB via
  `rag_manager.embed_and_save()`.
- **Load filter**: `_load_chat_memory()` strips toxic/refusal patterns on load.
- **Reaches the model in exactly two places**, both response-side, via
  `CielCore._recent_turns_block()`: `execute_chat`'s prompt and the tool-result format
  path. **Never** the Router — see the July-2026 decision under RAG Recall Pipeline
  below; `test_conversation_bugs.py` asserts this holds.

### Long-Term Memory (RAG)

- **Storage**: `ciel_data/vector_memory/` (ChromaDB persistent directory).
- **Embedding model**: `all-MiniLM-L6-v2` (local, fast, ~80MB) via
  `SentenceTransformerEmbeddingFunction`. A switch to ChromaDB's own ONNX
  `DefaultEmbeddingFunction` was tried to drop the torch dependency, then reverted:
  ChromaDB persists the embedding function choice IN the collection itself, so a
  different one at runtime doesn't migrate an EXISTING collection — it just fails
  ("sentence_transformers ... not installed") the moment the collection is actually
  queried/written to. `docker/requirements-docker.txt` installs a CPU-only `torch`
  wheel to keep this cheap in the container (~3.5GB, not the ~10GB a GPU build pulls).
- **Collection**: `ciel_long_term_memory`.
- **Graceful degradation**: missing/failing `chromadb`/`sentence-transformers` → RAG
  silently disables; CielCore keeps working with JSON-only short-term memory.

### RAG Recall Pipeline

```
User Input → rag_manager.search_similar(query, top_k=3)
  → Skip if query < 15 chars (MIN_QUERY_LENGTH)
  → Skip if all results score < 0.65 (MIN_RELEVANCE_SCORE)
  → Skip a result whose archived question normalises identically to THIS query
    (_normalize_for_selfmatch — a repeated question is the single most "similar"
    thing in the store by construction, so without this it recalls its own prior
    failure as "context" and repeats it)
  → Tier-1: zero-token regex/structural cleanup
     Removes: smart_scrape output, git_diff, raw HTML, base64, large fenced blocks
     Extracts: compact [date] Human: ... | Ai: ... lines
  → Tier-2: Worker compression (only if Tier-1 output > 4000 chars)
  → Bounded by ContextAssembler (CONTEXT_RECALL_BUDGET) at its SOURCE, before it
    fuses with the request — the only block whose size depends on retrieved data
  → [CURRENT USER REQUEST] is placed BEFORE [RECALLED PAST CONTEXT] in the merged
    text (reordered from recall-first) so noisy recall cannot push the Master's
    actual words out of the part of the prompt a model attends to most reliably
  → Router (Brain) sees this merged block; chat_history itself never reaches routing
```

**Why chat_history is excluded from routing, deliberately.** An earlier version fed
`chat_history` into the Router and it let an old unresolved request bleed into a new,
unrelated one. RAG recall is the intentional, *gated* substitute for cross-turn context
at the Router (relevance-scored, self-match-filtered); raw history is not. The
*response* path gets a second, separate channel — `_recent_turns_block()` — for exactly
the case RAG can't cover: the turn that was **just** answered and hasn't been archived
yet.

### Memory Fallback for Inspection Tools

The Brain often (reasonably) tries to VERIFY a memory question against ground truth by
routing it to an inspection tool (`list_workspace`/`read_file`/`get_file_info`) instead
of answering from recalled context — a good instinct, since RAG can be stale. But those
tools are on `_SKIP_SELF_CORRECTION`, so a directory listing used to be returned
verbatim as "the answer" when the inspection found nothing relevant.
`_memory_fallback_for_inspection()` closes this deterministically (no Brain call): when
a turn had recalled context, the request is a question, the tool is an inspection tool,
and its result shares no content word with the question — Ciel answers from the
recalled context via one Worker call, explicitly labeled as long-term memory that
**could not be verified from the workspace**. Never fires on imperative requests
("list my files"), when the result already addresses the question, or when there was
no recall.

### Key Configuration Constants

| Constant | Value | Location |
|----------|-------|----------|
| `MIN_QUERY_LENGTH` | 15 | `rag_manager.py` |
| `MIN_RELEVANCE_SCORE` | 0.65 | `rag_manager.py` |
| `DEFAULT_TOP_K` | 3 | `rag_manager.py` |
| `MAX_MEMORY_SNIPPET_CHARS` | 1800 | `rag_manager.py` |
| `RAG_LLM_COMPRESS_CHAR_THRESHOLD` | 4000 | `llm_connector.py` |
| `self.max_history` | 20 | `llm_connector.py` |
| `CONTEXT_RECALL_BUDGET` | 600 (default) | `agent_system/config.py` |
| `CONTEXT_RECENT_TURNS_BUDGET` | 500 (default) | `agent_system/config.py` |

## Two Memory Stores — and the Difference Is a Security Boundary

| Store | Module | Holds | Read path |
|---|---|---|---|
| `ciel_data/facts.json` | `skills/internal/memory_ops.py` | secrets, credentials | **pull-only, NEVER injected** |
| `ciel_data/user_model.json` | `core/user_model.py` | preferences, profile | **injected**, refuses credentials |

**Never merge them, and never inject the vault.** Everything in an injected store is
sent to the provider on every call that carries it — including third-party gateways.

### Fact Vault (pull-only)

- **Tools**: `save_fact(key, value)`, `get_fact(key)`, `delete_fact(key)`. Standalone —
  no imports from `core/` or `agent_system/`; keep it that way.
- The Router prefers recalled RAG context over redundant `get_fact` calls.
- `_format_fact_result()` renders raw tool output for the user; raw data stays in
  `thoughts.log`.
- **Why it stayed empty for months**: pull-only by design. Recall needs the model to
  guess an exact snake_case key *and* choose to look it up; writing needs the Master to
  say "remember this" out loud. That gap is what the user model (below) fills — it was
  never a bug in this module.

### User Model — the push side (Tier 7, full detail in `architecture.md`)

A small, bounded profile that enters the prompt by itself, kept honest by three rules —
authority (what the Master said outranks what Ciel inferred), decay (non-stated traits
fade unless re-observed), and a hard token ceiling (renders `""` while empty). It learns
unprompted behind a free deterministic gate (`assess_preference()`), never spending a
call on an ordinary turn.

`looks_like_secret()` is the credential boundary: a deterministic refusal checked on
both key and value, with separators (`_`/`-`) normalised to spaces before matching —
without that, `\bcvv\b` does not match `card_cvv`.

The file (`ciel_data/user_model.json`) is written indented and unescaped on purpose: it
describes a real person, so it must be readable, editable, and deletable by hand.
`forget()` / `forget_all()` really delete.

## Proactive Scheduler & Condition Triggers (Tier 6, full detail in `architecture.md`)

The CI daily digest in `scripts/daily_digest.py` searches current news and reads article
content through `smart_scrape`. If a planner passes a whole `stealth_search` result instead
of one URL, `skills/external/web_agent_ops.py` extracts its first concrete article URL before
fetching. The digest is plain text: each reported news item includes its source, date, and an
exact `Link:` URL from tool output; Markdown decoration is not used in Telegram delivery.

- **Module**: `core/scheduler.py` (legacy clock tasks) + `core/triggers.py` +
  `core/notifier.py` (condition-based, Tier 6).
- **Design principle**: Zero-Token Standby — Python watches the clock/conditions, the
  LLM sleeps until a check actually needs to compose prose.

### Legacy clock tasks

| Task | Schedule | What it does |
|------|----------|-------------|
| Morning Digest | 08:00 daily | Gmail + market prices → Worker formats → `daily_brief.md` → delivered |
| Brain Cleanse | 23:00 daily | Flush short-term memory into RAG + generate Daily Summary |

Morning Digest is now also available as a **declared trigger** (`morning_digest`).
When the trigger engine owns it, `start_background()` skips registering the 08:00 clock
task — two mechanisms delivering one brief is a double-send the Notifier cannot dedupe.

### The nine condition triggers

Three groups — Ciel watching itself, the clock, and the outside world behind
thresholds you set — opt-in **by name** via `PROACTIVE_TRIGGERS` (empty = nothing
runs). Full table, the four anti-noise rules, and the unattended permission ceiling
they share with Tier 3 are in `architecture.md`'s Tier 6 section.

**Reading `thoughts.log` from a check**: always go through `_iter_entries`. The live
log is 100% CRLF; a raw/binary tail that skips normalisation silently reports "nothing
found" forever — indistinguishable from a healthy system.

### Adding a New Scheduled Task

1. Prefer a **Trigger** over a clock task: write `check(now) -> Notification | None`
   and register it in `build_triggers()`. Take `now` as a parameter — never call
   `time.time()` inside — so a whole day simulates in a test with zero LLM calls.
2. Use raw API calls — import from skill modules (e.g.
   `from skills.external.trading_ops import fetch_market_price`).
3. Use `_get_worker()` for formatting (lazy-loaded, single call) only if the finding
   genuinely needs prose; most don't.
4. For a true wall-clock task, wrap the check in `daily_at(hour, minute, …)` rather
   than adding a second mechanism.
5. **Never** call `rag_manager.embed_and_save()` or write to `memory_bank.json`
   (Ghost Mode — scheduled/triggered work must not pollute conversational memory).

## Audit Trail

- **File**: `ciel_data/logs/thoughts.log` — see `conventions.md` for the exact format
  and why it must never change.
- **Generated views** (gitignored, disposable): `thoughts_view.md` (grouped Markdown),
  `thoughts_view.jsonl` (structured). Generator: `python scripts/format_thoughts_log.py
  --limit 30`.

## Cost / Usage Tracking

Every real LLM call across all tiers logs one
`[LLM_CALL] model=<id> in=<n> out=<n> total=<n>` entry (exact provider token counts via
`agent_system/utils/usage.py`). `CielCore` accumulates per-tier counts/tokens/
estimated-USD at the single `_log_thought()` chokepoint; the API's vitals feed surfaces
this live. Pricing lives in `core/cost.py`, overridable per-model via
`ciel_data/model_pricing.json` with zero code changes. For a historical view, run
`python -m scripts.cost_report [--since-days N]` — read-only, no LLM call involved.
This same log is also what Tier 6's `daily_cost` trigger reads to notice a spend spike
without any extra tracking mechanism.
