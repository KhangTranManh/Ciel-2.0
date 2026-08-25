# Prompt Inventory — Ciel 2.0

A single map of **every prompt in the project**: where it lives, which model consumes it,
and how prompts stack at runtime. This is a read-only inventory; use it alongside
`instructionAI/` and `improve.md` before rewriting any individual prompt.

**Maintenance rule (keep this file honest):** when you add, move, rename, or delete a
prompt, update the matching row here in the same change. One row = one prompt. If a row's
`File:line` or "Consumed by" no longer matches the code, the row is wrong — fix it.

> Line numbers drift as files change; treat them as a hint and grep the constant name to
> confirm. The **name** and **consumed-by** columns are the durable parts.

---

## 1. How prompts stack at runtime (live CielCore chat pipeline)

A single user turn assembles several prompts in sequence. This is the part that matters
most for maintenance — changing one layer affects everything downstream.

```
USER input
  │
  ├─ [Worker]  sanitize_task ......... translate/clean input to EN before routing
  │
  ├─ [Brain]   persona + CIEL_ROUTER_PROMPT + _tool_list_str + bounded context
  │            → routing decision (chat / tool / code / multi_tool)
  │
  ├─ action = chat ....... [Worker]  persona + persona_task            → reply
  ├─ action = tool ....... tool runs → [Worker] persona + format_task  → reply
  │                        then [Brain] SELF_CORRECTION_PROMPT (evaluate)
  ├─ action = code ....... [Worker]  task + code_guardrail             → script
  ├─ action = multi_tool . tools run → [Worker] persona + format_task(multi, big RULES)
  │
  ├─ email send .......... [Worker] body_task → body; outbound sanitize;
  │                        [Middleware] MIDDLEWARE_SYSTEM_PROMPT (review) if enabled
  │
  ├─ tool error .......... [Worker] recovery fix_task / syntax_task (self-healing)
  └─ RAG recall .......... [Worker] compress prompt (only if recalled context is large)
```

Key point: the **persona** (`official_ciel_personality.txt`) is prepended in three live
places — routing, chat gen, and both format tasks. The **Brain learns the tools** from
`_tool_list_str` (built by `_build_tool_list` from `_TOOL_HINTS` + each tool's docstring +
arg schema), **not** from the per-skill manuals (see §4).

---

## 2. Live prompts (affect real chat)

### 2a. Core tiers & persona

| Prompt | File:line | Consumed by | Purpose |
|---|---|---|---|
| `official_ciel_personality.txt` | persona/ (loaded `core/llm_connector.py:125`) | Brain routing + Worker chat/format | Identity, tone, behavioral logic, email rules, Operational Directives |
| `CIEL_ROUTER_PROMPT` | `core/router.py:9` | Brain (CielCore router) | Intent → JSON routing decision |
| `SELF_CORRECTION_PROMPT` | `core/llm_connector.py:41` | Brain | Judge if a tool result satisfied the request; trigger retry |
| `MIDDLEWARE_SYSTEM_PROMPT` | `agent_system/models/middleware.py:46` | Middleware | Review/repair outbound email/report before send |
| `WORKER_SYSTEM_PROMPT` | `agent_system/models/worker.py:42` | Worker | Default system message on every Worker call |

### 2b. Inline task prompts (runtime f-strings in `core/llm_connector.py`)

These are built per-request, mixing prompt text with live data. Hardest to see because
they're not named constants.

| Task var | File:line | Purpose |
|---|---|---|
| `sanitize_task` | `core/llm_connector.py:~1355` | Translate/clean the user request to EN before routing |
| `persona_task` | `core/llm_connector.py` | Generate a chat-action reply from the real user message, recent turns, and non-binding Router hint |
| `format_task` (single) | `core/llm_connector.py:~646` | Format one tool's result into a reply |
| `format_task` (multi) | `core/llm_connector.py:~928` | Synthesize a multi_tool run — the big anti-hallucination RULES block |
| `body_task` | `core/llm_connector.py:~1648` | Synthesize a professional email body from real tool data |
| `code_guardrail` / `augmented_task` | `core/llm_connector.py:~784` | Safety/quality guardrail appended to code-gen tasks |
| RAG compress prompt | `core/llm_connector.py:~736` | Compress large recalled memory before it reaches the Brain |
| Active lookup note | `core/llm_connector.py` | Deterministic, not an LLM prompt: injects query + public URLs only for an explicit lookup follow-up |

### 2c. Self-healing (`core/recovery_manager.py`)

| Prompt | File:line | Purpose |
|---|---|---|
| `fix_task` (code) | `core/recovery_manager.py:31` | "ROBUST OS DEVELOPER" — rewrite a failed Python script |
| `fix_task` (args) | `core/recovery_manager.py:65` | Guess corrected `tool_args` for a non-script tool error |
| `syntax_task` | `core/recovery_manager.py:102` | Fast syntax check on a healed script before saving |

### 2d. Tool-internal prompts (live, used inside a specific tool)

| Prompt | File:line | Purpose |
|---|---|---|
| `VISION_ANALYSIS_PROMPT` | `skills/internal/vision_ops.py:79` | Drive the vision LLM's click/act analysis (used at :507) |
| `VISION_DESCRIBE_PROMPT` | `skills/internal/vision_ops.py:121` | Describe a screenshot (used at :614) |
| `PREFLIGHT_PROMPT` | `skills/internal/vision_ops.py:349` | Plan a desktop-automation task (used at :417) |

---

## 3. Offline / auxiliary prompts (do NOT affect live chat)

Kept separate on purpose — editing these never changes runtime chat behavior.

| Prompt | File:line | Used by |
|---|---|---|
| `BRAIN_SYSTEM_PROMPT` | `agent_system/models/brain.py:56` | LangGraph pipeline (`agent_system/main.py`), NOT CielCore |
| Nightly Judge prompt | `autonomous_pipeline/data_pipeline.py:331` | Offline batch grader of logged turns |
| Simulated-Master `system_prompt` | `autonomous_pipeline/task_generator.py:233` | Generates synthetic test tasks |
| `CIEL_SYSTEM_PROMPT` / `BRAIN_SYSTEM_PROMPT` / `WORKER_SYSTEM_PROMPT` | `scripts/format_thoughts_log.py:90/116/142` | Fine-tune dataset builders (LoRA/ChatML export) |

---

## 4. ⚠ Findings — drift the inventory surfaced

**The per-skill "weapon manuals" are collected but never fed to any live LLM.** Each skill
returns a `*_SYSTEM_PROMPT` (`GMAIL_SYSTEM_PROMPT`, `TRADING_SYSTEM_PROMPT`, `SYSTEM_OPS_PROMPT`,
`OS_OPS_PROMPT`, `MEMORY_OPS_PROMPT`, `PRODUCTIVITY_PROMPT`, `GIT_SYSTEM_PROMPT`,
`WEB_AGENT_SYSTEM_PROMPT`, `TELEGRAM_SYSTEM_PROMPT`, `VISION_OPS_PROMPT`). `ToolManager`
gathers them into `self.system_prompts`, but the only function that would surface them —
`ToolManager.get_dynamic_prompt()` (`core/tool_manager.py:88`) — **is never called anywhere**
in the project. So these ~10 detailed manuals are inert: the Brain's actual tool knowledge
comes from `_tool_list_str` (`_TOOL_HINTS` + tool docstrings + arg schemas) instead.

This is the same dead-weight/drift class as the legacy `persona/` fragments (removed July 9).

**Investigated July 9, 2026 (empirical):**
- What the Brain actually sees per tool = `name` + `tool.description[:80]` (truncated!) + arg schema, plus `_TOOL_HINTS` overrides. `_TOOL_HINTS` (`core/llm_connector.py:227`) currently has exactly **one** entry (`search_gmail`), so 43/44 tools ride on an 80-char docstring snippet + arg names.
- The manuals DO contain unique, non-redundant guidance (e.g. `GMAIL_SYSTEM_PROMPT`'s natural-language→query examples, param-naming rules, the VN summary format) — but the essential bits are already echoed in `_TOOL_HINTS`/docstrings, and the system passes tests without the manuals because tool names are self-describing, arg schemas are explicit, and the Brain model is strong.
- The `WRONG_TOOL_CHOICE` harness cases are **fallback-after-failure** ("tool X failed → try Y"), not knowledge gaps — so wiring the manuals in would NOT fix them.

**Conclusion: low severity (organizational drift, not a bug). Do NOT wire the dead manuals back in** — they'd bloat the routing prompt for little gain. The correct *live* lever for tool guidance is `_TOOL_HINTS` + the tool docstring. If a specific tool ever shows real arg/selection errors in the logs, migrate just that tool's key line into `_TOOL_HINTS` (live), and delete or shrink the corresponding manual. Don't do it speculatively.

---

## 5. How to edit / maintain prompts (future work)

**The golden rule:** prompts cannot be unit-tested. Every wording change must be verified by
running a **real turn** and reading the result — `backtest/test_integration.py` (routing/tools)
and `backtest/test_hard_special.py` (email/safety/multi-step) are the checks. A change that
"looks obviously fine" can silently break behavior that only surfaces live. Change one prompt at
a time; re-run the relevant suite.

### "I want to change ___ " → edit this

| Goal | Edit | Verify with |
|---|---|---|
| Ciel's personality / tone / identity / email style | `persona/official_ciel_personality.txt` | any chat turn + an email send |
| How the Brain routes / plans (intent → action) | `CIEL_ROUTER_PROMPT` (`core/router.py:9`) | `test_integration.py` routing cases |
| **A tool's arg/usage guidance to the Brain** | `_TOOL_HINTS` (`core/llm_connector.py:227`) — the LIVE layer — and/or the tool's **docstring** (first ~80 chars matter most) | that tool's flow |
| Whether/when a tool gets picked | same as above (`_TOOL_HINTS` + docstring) — **NOT** the skill `*_SYSTEM_PROMPT` manual (§4: it's inert) | routing |
| Multi-tool report formatting / anti-hallucination rules | `format_task` (multi) `core/llm_connector.py:~928` | a multi_tool run |
| Single-tool result phrasing | `format_task` (single) `core/llm_connector.py:~646` | a single-tool run |
| Email body synthesis | `body_task` `core/llm_connector.py:~1648` + persona email rules | an email send |
| Self-correction strictness (retry-or-accept) | `SELF_CORRECTION_PROMPT` `core/llm_connector.py:41` | a failing-tool case |
| Middleware review strictness | `MIDDLEWARE_SYSTEM_PROMPT` (`agent_system/models/middleware.py:46`) | an email with a deliberate flaw |
| Self-healing rewrite behavior | `fix_task`/`syntax_task` (`core/recovery_manager.py`) | a buggy script |
| Vision (screen click/describe/plan) | `VISION_ANALYSIS/DESCRIBE/PREFLIGHT_PROMPT` (`skills/internal/vision_ops.py`) | a vision task |

### Three traps this inventory exists to prevent

1. **Editing a dead prompt.** The per-skill `*_SYSTEM_PROMPT` manuals and `ToolManager.get_dynamic_prompt()` are NOT wired to any live call (§4). Editing `GMAIL_SYSTEM_PROMPT` to change routing will do nothing. Use `_TOOL_HINTS` + docstrings instead.
2. **Forgetting the persona ripples.** `official_ciel_personality.txt` is prepended in three live places (routing, chat gen, both format tasks). A persona edit affects all of them — verify more than one path.
3. **Editing the offline copies by mistake.** `agent_system/models/brain.py`'s `BRAIN_SYSTEM_PROMPT` and the `scripts/format_thoughts_log.py` prompts look like the live ones but drive the LangGraph pipeline / fine-tune dataset, not CielCore chat (§3).

**After any prompt change, update the matching row in §2/§3 of this file** if location or purpose shifted.
