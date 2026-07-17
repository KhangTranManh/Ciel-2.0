# Data Pipeline — Ciel 2.0

How Ciel stores, recalls, and manages memory and scheduled data flows.

## Hybrid Memory Architecture

Ciel uses a two-tier memory system:

```
Short-Term (JSON)                    Long-Term (ChromaDB)
┌─────────────────────┐              ┌─────────────────────────┐
│ memory_bank.json    │   overflow   │ ciel_data/vector_memory/│
│ Max 20 messages     │ ──────────→  │ ChromaDB + MiniLM-L6-v2 │
│ LangChain history   │              │ Semantic search         │
└─────────────────────┘              └─────────────────────────┘
```

### Short-Term Memory

- **File**: `ciel_data/memory_bank.json`
- **Format**: JSON array of `{"type": "human|ai", "content": "..."}` messages.
- **Max size**: 20 messages (configurable via `self.max_history`).
- **Overflow**: When messages exceed 20, `_trim_history()` archives the oldest messages into ChromaDB via `rag_manager.embed_and_save()`.
- **Load filter**: `_load_chat_memory()` strips toxic/refusal patterns on load.

### Long-Term Memory (RAG)

- **Storage**: `ciel_data/vector_memory/` (ChromaDB persistent directory).
- **Embedding model**: `all-MiniLM-L6-v2` (local, fast, ~80MB).
- **Collection**: `ciel_long_term_memory`.
- **Graceful degradation**: If `chromadb` or `sentence-transformers` are missing, RAG is silently disabled and CielCore works with JSON-only memory.

### RAG Recall Pipeline

```
User Input → rag_manager.search_similar(query, top_k=3)
  → Skip if query < 15 chars (MIN_QUERY_LENGTH)
  → Skip if all results score < 0.65 (MIN_RELEVANCE_SCORE)
  → Tier-1: Zero-token regex/structural cleanup
     Removes: smart_scrape output, git_diff, raw HTML, base64, large fenced blocks
     Extracts: compact [date] Human: ... | Ai: ... lines
  → Tier-2: Worker compression (only if Tier-1 output > 4000 chars)
     Compresses into factual Human/Ai summary lines
  → Inject as [RECALLED PAST CONTEXT] block into user prompt
```

### Key Configuration Constants

| Constant | Value | Location |
|----------|-------|----------|
| `MIN_QUERY_LENGTH` | 15 | `rag_manager.py` |
| `MIN_RELEVANCE_SCORE` | 0.65 | `rag_manager.py` |
| `DEFAULT_TOP_K` | 3 | `rag_manager.py` |
| `MAX_MEMORY_SNIPPET_CHARS` | 1800 | `rag_manager.py` |
| `RAG_LLM_COMPRESS_CHAR_THRESHOLD` | 4000 | `llm_connector.py` |
| `self.max_history` | 20 | `llm_connector.py` |

## Fact Vault

- **File**: `ciel_data/facts.json`
- **Module**: `skills/internal/memory_ops.py` (completely standalone — no external deps).
- **Tools**: `save_fact(key, value)`, `get_fact(key)`, `delete_fact(key)`.
- **Design**: Simple key-value JSON store. The Router prefers recalled RAG context over redundant `get_fact` calls (RECALLED CONTEXT rule in `router.py`).
- `_format_fact_result()` in `llm_connector.py` converts raw tool output into user-friendly text (raw data stays in `thoughts.log`).

## Proactive Scheduler

- **Module**: `core/scheduler.py`
- **Design**: Zero-Token Standby — Python watches the clock, LLM sleeps until needed.

### Design Principles

1. **Direct Execution**: Tools are called as raw Python/APIs, skipping the Brain entirely.
2. **Ghost Mode**: Scheduled tasks never write to `memory_bank.json` or RAG.
3. **Single API Call**: Only the Worker is invoked once to format the digest.
4. **Lazy Worker**: Worker LLM is only loaded when a task actually fires.

### Current Scheduled Tasks

| Task | Schedule | What it does |
|------|----------|-------------|
| Morning Digest | 08:00 daily | Fetch unread Gmail + Forex/Metals prices → Worker formats → Save to `daily_brief.md` → Send via Telegram |
| Brain Cleanse | 23:00 daily | Flush short-term memory into RAG + generate Daily Summary |

### Adding a New Scheduled Task

1. Write a module-level function in `scheduler.py` (e.g., `_evening_report()`).
2. Use raw API calls — import from skill modules (e.g., `from skills.external.trading_ops import fetch_market_price`).
3. Use `_get_worker()` for formatting (lazy-loaded, single call).
4. Register in `start_background()` with `schedule.every().day.at("HH:MM").do(your_fn)`.
5. **Never** call `rag_manager.embed_and_save()` or write to `memory_bank.json`.

## Audit Trail

- **File**: `ciel_data/logs/thoughts.log`
- **Purpose**: Raw chronological Brain/Worker/Middleware thought audit trail.
- **Format**: See `conventions.md` for exact format.
- **Generated views** (gitignored, disposable):
  - `ciel_data/logs/thoughts_view.md` — Grouped Markdown debug view.
  - `ciel_data/logs/thoughts_view.jsonl` — Structured log view.
- **Generator**: `python scripts/format_thoughts_log.py --limit 30`

## Cost / Usage Tracking

Every real LLM call across all three tiers logs one `[LLM_CALL] model=<id> in=<n> out=<n>
total=<n>` entry (exact provider token counts via `agent_system/utils/usage.py`). `CielCore`
accumulates per-tier counts/tokens/estimated-USD at the single `_log_thought()` chokepoint; the
API's vitals feed surfaces this live. Pricing lives in `core/cost.py`, overridable per-model via
`ciel_data/model_pricing.json` with zero code changes. For a historical view (not just the live
session), run `python -m scripts.cost_report [--since-days N]` — aggregates by tier/model/day
from `thoughts.log`, read-only, no LLM call involved.
