# Conventions — Ciel 2.0

Code patterns, naming rules, and gotchas for working on this project.

## Brain-Worker Separation

The system strictly separates **routing** from **generation**:

- **Brain** — Decides what to do. Outputs JSON with `hidden_thought` + action. Never generates content.
- **Worker** — Generates text/code. Never makes routing decisions. Has tenacity retry protection.
- **Router** — Uses Brain to classify intent into `chat`, `tool`, `code`, or `multi_tool`.

The Brain and Worker are separate LLM instances with different models and temperatures. Brain runs at `temperature=0.1` (deterministic routing), Worker at `temperature=0.2` (creative but consistent).

## Tool Pack Registration

New tools are added by creating a module in `skills/internal/` or `skills/external/`:

1. Create a file (e.g., `skills/external/my_ops.py`).
2. Define a function matching `get_*_tools()` that returns `{"tools": [StructuredTool, ...], "prompt": "..."}`.
3. `ToolManager.__init__()` auto-discovers all `*.py` files in `skills/internal/` and `skills/external/` via `_auto_load_skills()`.
4. No manual registration needed — just drop the file in the right folder.

The `prompt` string is the tool's "manual" — it gets stitched into the Brain's system prompt via `get_dynamic_prompt()`.

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

## Naming Conventions

- **Modules**: `snake_case` ending in `_ops.py` for tool packs (e.g., `gmail_ops.py`, `trading_ops.py`).
- **Factory functions**: `get_*_tools()` — must match this pattern for auto-discovery.
- **Tool names**: `snake_case` matching the function name (e.g., `get_market_price`, `execute_shell_command`).
- **Log actors**: `BRAIN`, `WORKER`, `ROUTER`, `HEALING`, `RAG`, `USER`, `SAFETY` — used in `_log_thought()`.
- **Config keys**: `UPPER_SNAKE_CASE` in both `.env` and `agent_system/config.py`.

## Audit Trail (`thoughts.log`)

Every Brain/Worker decision is logged to `ciel_data/logs/thoughts.log` in this format:

```
[2026-05-21 14:30:00] [ACTOR] [ACTION]
content
------------------------------------------------------------
```

- **Never modify this format** — `scripts/format_thoughts_log.py` parses it.
- Actors: `BRAIN`, `WORKER`, `ROUTER`, `HEALING`, `RAG`, `USER`, `SAFETY`.
- Actions: `REQUEST`, `ROUTE_DECISION`, `CHAT_RESPONSE`, `TOOL_EXECUTE`, `HEALING_PROMPT`, etc.
- Generated views (`thoughts_view.md`, `thoughts_view.jsonl`) are gitignored and disposable.

## Safe vs Dangerous to Modify

### Safe to change

- Add new tool pack in `skills/internal/` or `skills/external/` (auto-discovered).
- Edit persona files in `persona/`.
- Adjust RAG thresholds (`MIN_RELEVANCE_SCORE`, `MIN_QUERY_LENGTH`, `DEFAULT_TOP_K`) in `rag_manager.py`.
- Add new scheduled tasks in `scheduler.py` (follow Ghost Mode pattern).
- Update model names in `.env`.
- Add new tests in `backtest/`.

### Dangerous to change

- **`_log_thought()` format** in `llm_connector.py` — breaks log parsing.
- **`_is_safe_path()` logic** in `system_ops.py` — weakens sandbox quarantine.
- **`_HIGH_RISK_TOOLS` set** in `llm_connector.py` — removing tools disables safety checks.
- **Router JSON schema** in `router.py` — must match parsing in `CielCore.process()`.
- **`memory_ops.py` imports** — must remain standalone (no `core/` or `agent_system/` deps).
- **`thoughts.log` file path** — hardcoded in `llm_connector.py`, `main_api.py`, and `format_thoughts_log.py`.

## Known Gotchas

1. **Windows paths**: The Router prompt includes WINDOWS SYSTEM ARCHITECT rules enforcing double-quoted absolute paths and `python -m` prefix. Without this, tools fail on paths with spaces.

2. **Format string escaping**: Vision prompts in `vision_ops.py` use Python `.format()` — curly braces in prompt text must be doubled (`{{` and `}}`) or the app crashes with `Single '}' in format string`.

3. **Gmail credential signature**: `langchain_google_community` has a known typo (`client_sercret_file` vs `client_secrets_file`). The code in `gmail_ops.py` and `scheduler.py` handles both via `inspect.signature()`.

4. **RAG lazy loading**: `rag_manager.py` lazy-loads ChromaDB and sentence-transformers on first use. If these packages are missing, RAG degrades gracefully — CielCore still works with JSON-only short-term memory.

5. **Self-correction isolation**: Self-correction retries (Brain evaluating tool results) are not saved to chat memory or RAG to prevent noise accumulation.

6. **Scheduler Ghost Mode**: Scheduled tasks must never write to `memory_bank.json` or call `rag_manager.embed_and_save()`. They use raw API calls and a single Worker call for formatting.
