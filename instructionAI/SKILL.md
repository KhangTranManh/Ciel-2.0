---
name: ciel-2.0
description: >
  Ciel 2.0 is a modular autonomous AI assistant with a three-tier Brain → Router →
  Middleware → Worker pipeline, extended by seven agent-capability tiers (an observe/
  re-plan loop, durable task state, scoped consent, bounded context, interruptible
  requests, proactivity, and a model of the user). Every tier decides in deterministic
  Python and only plans with the LLM, so behaviour survives a change of model. Built on
  LangChain with multi-provider support (Vilao, DeepSeek, Gemini, Ollama).
---

# Ciel 2.0 — AI Instruction Set

## What this project is

Ciel is an autonomous AI assistant and System Sentinel built on a **Brain → Router →
Middleware → Worker** pipeline. The **Brain/Router** classifies intent and plans, the
**Worker** produces text/code, and the optional **Middleware** semantically verifies
outbound email/report bodies before send. Around that core sit auto-discovered tool
packs, hybrid long-term memory, a destructive-action safety gate, token-precise cost
tracking, and both CLI and browser/desktop interfaces. Ciel can also listen (STT) and
talk back (TTS).

On top of the core sit **seven agent-capability tiers** — the gap between "executes
commands" and "pursues goals": the observe/re-plan loop, durable task state, scoped
consent, bounded context, interruptible requests, proactivity, and a model of the user.
See `architecture.md` for what each one does and why it exists; every tier is
individually switchable in `.env` and degrades to the pre-tier behaviour when off.

Entry points: `main.py` (CLI, `--voice`/`--speak`) and `main_api.py`
(FastAPI/WebSocket for the React/Tauri UI; also serves `GET /skills`, `/health`,
`POST /tts`).

## Instruction files in this folder

| File | Purpose |
|------|---------|
| `SKILL.md` | This file — project overview, file index, critical rules |
| `architecture.md` | File tree, module map, runtime flow, and the seven agent-capability tiers |
| `conventions.md` | Code patterns, naming, tool registration contract, safe/dangerous changes, gotchas |
| `safety_and_risk.md` | The decoupled safety model, the 8-tool + content gates, the unattended DEFER ceiling, outbound idempotence, sandbox, vision/self-heal guardrails |
| `data_pipeline.md` | Short/long-term memory, the two memory stores (fact vault vs. user model), condition triggers, cost/audit tracking |
| `voice_and_interface.md` | Voice I/O, the UI's current layout, the WebSocket protocol, and what a UI rebuild needs to wire |

For the dated changelog and current live status, see `../architect.md` and `../note.md`
— **not this folder**; `instructionAI/` holds stable knowledge, not a diary.

A project-agnostic prompt that bootstraps/maintains an `instructionAI/`-style folder in
any other project lives at `references/portable_instruction_generator_prompt.md`.
`references/Reamdepromp.md` is an unrelated personal scratch prompt — ignore it.

**Rebuilding or replacing the frontend?** Start at *Rebuilding the UI* in
`voice_and_interface.md`. Two backend capabilities (proactive notifications, deferred
approvals) are finished and simply not surfaced in the UI yet.

## Critical rules for any AI working on this project

### Logging, safety flags, tools

1. **Never modify `thoughts.log`'s format.** It is the raw chronological audit trail
   parsed by `scripts/format_thoughts_log.py`, `scripts/prompt_harness.py`,
   `scripts/cost_report.py`, and `core/triggers.py::_iter_entries`. The
   `[LLM_CALL] model=<id> in=<n> out=<n> total=<n>` line must keep `model=` first and
   space-delimited. The file is written in text mode, so on Windows it is **100% CRLF**;
   any new reader must normalise that (`_iter_entries` already does).

2. **Keep the two safety flags SEPARATE.** `SAFETY_OPEN` tunes Brain **content
   filtering** only; `DISABLE_SAFETY_GATE` controls the **destructive-tool confirmation
   gate** only (default off = gate active). Coupling them once let a *denied*
   confirmation still delete a file — never re-couple them.

3. **Always route high-risk actions through the safety gate.** Eight tools
   (`delete_file`, `execute_shell_command`, `send_gmail_message`,
   `send_gmail_html_message`, `reply_to_email`, `trash_email`, `git_confirm_push`,
   `vision_act`) require Y/N, plus a content-based gate on
   `write_file`/`append_file`/`execute_code` when the content matches a destructive
   pattern.

4. **Prefer deterministic safeguards over trusting the LLM.** Sanitizers, workflow
   safeguards, the dangerous-code scan, the healing skip-list, the synthesis-placeholder
   guard, multi_tool step-refs, the inspection-tool memory fallback, tolerant router-JSON
   extraction, and exact-subject enforcement are all code, not model judgment. Recurring
   lesson: a safeguard keyed to an EXACT model-emitted string is fragile — a stronger
   model paraphrases past it; match by pattern/intent instead. Middleware is the only
   LLM-based check, scoped narrowly (email only), and **fail-open**.

5. **Gather real data BEFORE composing any email body.** Anti-fabrication rules forbid
   inventing prices/numbers; claim "sent" only on a real Message Id. The same principle
   covers retrieved data — `stealth_search` labels undated results
   `Published: UNKNOWN … do NOT state a date`. A tool must hand over the facts (date,
   source) the answer needs; the model cannot cite what it never received.

6. **`memory_ops.py` is standalone.** It reads/writes `ciel_data/facts.json` directly
   with zero imports from `core/` or `agent_system/`. Keep it that way.

7. **Tool packs auto-register.** Drop a `skills/**/*_ops.py` exposing `get_*_tools()`
   returning `{"tools": [...], "prompt": "..."}`; `ToolManager` discovers it and the UI
   skill grid updates from `GET /skills` with zero frontend edits. **Because nothing in
   `core/` gates a new pack, the burden is on the pack** — read *Skill Contract* in
   `conventions.md` first. The rule that bites most: a tool can be invoked more than
   once per request (self-healing ×3, self-correction ×2, the agent loop ×N) — make
   tools idempotent, or split preview/confirm via `make_result(confirm={...})`.

8. **Workspace file ops are sandboxed** to `ciel_workspace/` / `agent_output/` via
   `_is_safe_path()`. Never weaken that check.

9. **Voice never changes the prompts.** TTS output is cleaned by the deterministic
   `to_speech()` normalizer, not by dumbing down the persona — text, HUD, and email
   keep rich formatting.

10. **`.env` is the single source for credentials + provider config.** Swap providers
    via `BRAIN_PROVIDER`/`WORKER_PROVIDER`/`MIDDLEWARE_PROVIDER` with no code changes.
    Gotcha: the Worker model is read from `CODER_MODEL`, not `WORKER_MODEL`.

11. **Measure a model alias before adopting it — gateways inject hidden prompts.**
    Measured on the same endpoint/key: one alias added ~6,500 unsuppressable tokens per
    call while another added ~10, for identical correctness. Issue one trivial request
    and compare the provider's reported `input_tokens` against what you actually sent
    before trusting a new alias. See the provider table in `../note.md`.

### The seven agent-capability tiers (`core/`) — read `architecture.md` before changing any

One rule runs through all seven: **the decision is deterministic Python; the model only
plans or composes.** That is what lets each tier survive a change, or a downgrade, of
model.

12. **Tier 1 — the loop decides in code, plans with the LLM** (`core/continuation.py`).
    `ContinuationPolicy.assess()` is pure Python and free, so an ordinary request spends
    zero extra calls; only a deterministic signal opens a re-plan round. **The scope
    veto is checked FIRST**, before any continuation signal: an explicit "chỉ … thôi" /
    "đừng …" / "only …" must outrank fan-out or any other reason to continue — a
    continuation signal must never override a human's explicit "don't". Every bound
    (rounds, planner calls, wall-clock — checked *before each step*, not just between
    rounds) lives in `LoopBudget` and is fail-open on any error.

13. **Tier 2 — durable task state** (`core/task_state.py`). A job record survives a
    crash/restart; anything still `active` at load time is reclassified `interrupted`.
    The Brain never reads these records — feeding them into routing would reopen the
    cross-request contamination Tier-1's design deliberately avoids.

14. **Tier 3 — permissions** (`core/permissions.py`). Every `(tool, args)` resolves to
    `AUTO`/`ASK`/`DENY` (and, unattended, `DEFER` — rule 18). Plan approvals are scoped
    to the exact `(tool + args)` reviewed, never to a tool name — a step the loop
    invents later would otherwise inherit an approval the Master never gave. `DENY`
    outranks every grant and `DISABLE_SAFETY_GATE`.

15. **Tier 4 — one place assembles context, and it has a budget**
    (`core/context.py`). Add context via `ContextAssembler.add(name, text, priority)`,
    never by appending to a string. Blocks are dropped **whole** under budget pressure,
    never truncated — half a `[WORKING DIRECTORY: …]` note still reads as a fact while
    being wrong. Bound anything data-sized **at its source**; RAG recall is the only
    block that can grow without anyone editing code.

16. **Tier 5 — cancellation is cooperative and checked at STEP boundaries only**
    (`CielCore.request_cancel()` / `_abort_if_cancelled()`). Never mid-tool — stopping
    inside a half-written file or half-sent email is corruption, not cancellation. The
    flag clears at the *start* of a turn so a Ctrl+C between turns cannot kill the next
    request. Thread-safe (`threading.Event`), so the UI cancels the same way over the
    WebSocket.

17. **Tier 6 — proactivity is opt-in, budgeted, and must never repeat itself**
    (`core/triggers.py`, `core/notifier.py`). A `Notification` with no `action` cannot
    interrupt (demoted to the digest). Its `key` must come from the identity of the
    underlying thing, never message text, or the per-key cooldown cannot turn a
    standing condition into a single event. `PROACTIVE_DAILY_BUDGET` caps interruptions
    in Python, never by asking a model to restrain itself. Delivery routes by
    **liveness** — a running process is not a present human.

18. **An unattended run can never be given consent by silence.**
    `PermissionPolicy.decide(..., attended=False)` returns `DEFER`, not `ASK`: recorded
    in `DeferredStore`, raised at the next interaction. Session grants, plan approvals,
    and `DISABLE_SAFETY_GATE` are all ignored there — each is evidence a human agreed
    *while present*. `CielCore.unattended` is thread-local and must be propagated
    explicitly into worker threads; a safety control that fails open in a thread is
    worse than none. Deferred actions are never auto-replayed.

19. **Tier 7 — two memory stores, and the difference is a security boundary**
    (`core/user_model.py`). `facts.json` is pull-only and may hold credentials — it is
    **never** injected. `user_model.json` **is** injected and refuses anything matching
    `looks_like_secret()` on both key and value. Never merge the two. Inside the
    profile: what the Master **stated** outranks what Ciel **inferred** (a lower
    authority cannot overwrite a higher one), non-stated traits **decay**, and
    `render()` is hard-capped by `USER_MODEL_TOKEN_BUDGET` — it renders `""` when empty,
    so it costs nothing until it knows something. Learning (7b) is gated by
    `assess_preference()` — free Python that decides the KIND itself; the model only
    proposes key/value, or it would always claim `stated`.

### Two fixes found by reading real transcripts, not test output

20. **One delivery per recipient per turn.** `execute_tool` suppresses a second
    outbound send (`send_gmail_message`, `send_gmail_html_message`, `reply_to_email`,
    `send_telegram`) to the same recipient within one request. Exists because two
    independent mechanisms — the workflow safeguard and the Tier-1 loop's re-plan — can
    both complete a plan missing its send step, delivering the same report twice with
    different subjects. Key on the RECIPIENT, never a full argument signature (the
    duplicates differ in subject/body by construction); record only on SUCCESS. Any new
    path that can send must go through `execute_tool`.

21. **The router's `task` field, for `action == "chat"`, is a HINT — never the final
    reply.** `process()`'s chat branch always passes the Master's real `user_input` to
    `execute_chat`; `task` survives only as a labelled, non-binding hint appended after
    it. A strong Brain routinely pre-writes the actual reply into `task` (caught
    literally once: `"task": "Reply: \"...\""`) — handed to the Worker as the request,
    that silently bypasses the persona's language-matching rule and any recent-turns
    context, since the Worker is just relaying an answer a component that never sees
    `chat_history` already decided. `chat_history` itself reaches the model in exactly
    two places, both response-side (`_recent_turns_block()` feeding `execute_chat` and
    the tool-result format path) — never routing. `action == "code"`'s `task` is a
    different contract (a spec to execute, not a pre-written answer) and stays
    untouched by both of these rules.
