---
name: ciel-2.0
description: >
  Ciel 2.0 is a modular autonomous AI assistant with a three-tier Brain → Middleware
  → Worker architecture. It features intent routing, multi-tool execution, self-healing
  error recovery, a semantic email/report finalizer, hybrid RAG memory (ChromaDB + JSON),
  a destructive-tool safety gate decoupled from content filtering, token-precise cost
  tracking, vision/UI interaction, voice I/O (STT + TTS), and a JARVIS-style audio-reactive
  orb UI. Built on LangChain with multi-provider support (Vilao, DeepSeek, Gemini, Ollama).
---

# Ciel 2.0 — AI Instruction Set

## What this project is

Ciel is an autonomous AI assistant and System Sentinel built on a **three-tier
Brain → Middleware → Worker** architecture. The **Brain (Router)** classifies user intent,
the **Worker** produces text/code, and the optional **Middleware** semantically verifies
outbound email/report bodies before they are sent. Around this core sit auto-discovered
tool packs, hybrid long-term memory, a destructive-action safety gate, token-precise cost
tracking, and both CLI and browser/desktop interfaces (the latter with an audio-reactive
particle orb). Ciel can also listen (speech-to-text) and talk back (text-to-speech).

Entry points: `main.py` (CLI, supports `--voice`/`--speak`) and `main_api.py`
(FastAPI/WebSocket for the React/Tauri UI; also serves `GET /skills`, `/health`, `POST /tts`).

## Instruction files in this folder

| File | Purpose |
|------|---------|
| `SKILL.md` | This file — project overview, file index, critical rules |
| `architecture.md` | File tree, module responsibilities, three-tier runtime flow, providers |
| `conventions.md` | Code patterns, naming, tool registration, log actors, safe/dangerous changes |
| `safety_and_risk.md` | The decoupled safety model, 8 high-risk tools + dangerous-code gate, sandbox, vision failsafe |
| `data_pipeline.md` | RAG memory pipeline, fact vault, scheduler, cost tracking, audit trail |
| `voice_and_interface.md` | Voice I/O (STT/TTS backends, normalizer), the UI orb, and the modality seam |

For the dated changelog and current live status, see `../architect.md` and `../note.md`
(NOT this folder — instructionAI/ holds stable knowledge only).

For a project-agnostic prompt that bootstraps/maintains an `instructionAI/`-style folder in
ANY other project (not specific to Ciel), see `references/portable_instruction_generator_prompt.md`.

## Critical rules for any AI working on this project

1. **Never modify `thoughts.log` format.** It is the raw chronological audit trail parsed by
   `scripts/format_thoughts_log.py`, `scripts/prompt_harness.py`, and `scripts/cost_report.py`.
   The `[LLM_CALL] model=<id> in=<n> out=<n> total=<n>` line in particular must keep `model=`
   first and space-delimited.

2. **Keep the two safety flags SEPARATE.** `SAFETY_OPEN` tunes Brain **content filtering** only;
   `DISABLE_SAFETY_GATE` controls the **destructive-tool confirmation gate** only (default off = gate
   active). Never re-couple them — doing so once made a *denied* confirmation still delete the file.

3. **Always route high-risk actions through the safety gate.** Eight tools (`delete_file`,
   `execute_shell_command`, `send_gmail_message`, `send_gmail_html_message`, `reply_to_email`,
   `trash_email`, `git_confirm_push`, `vision_act`) require Y/N, PLUS a content-based gate on
   `write_file`/`append_file`/`execute_code` when the content matches a destructive pattern.

4. **Prefer deterministic safeguards over trusting the LLM.** Sanitizers, workflow safeguards,
   the dangerous-code scan, the healing skip-list, the synthesis-placeholder guard
   (`_has_unsynthesized_placeholder`), multi_tool step-refs (`_resolve_step_refs`), the
   inspection-tool memory fallback (`_memory_fallback_for_inspection`), tolerant router-JSON
   extraction (`_extract_json_object`), and exact-subject enforcement (`_enforce_subject`) are all
   code, not model judgment. Recurring lesson: any safeguard that keys off an EXACT model-emitted
   string is fragile — a stronger Brain paraphrases it; match by pattern/intent instead. The
   Middleware tier is the ONLY LLM-based check and is scoped narrowly (email only) and **fail-open**.

5. **Gather real data BEFORE composing any email body.** Market/search/document tools run first;
   anti-fabrication rules forbid inventing prices/numbers. Claim "sent" only on a real Message Id.
   The same principle governs retrieved data: `stealth_search` labels undated results
   `Published: UNKNOWN … do NOT state a date`, and the tool-result formatter tells the Worker to
   report each item's date rather than guess one. A tool must hand over the facts (date, source)
   the answer needs — the model cannot cite what it never received.

6. **`memory_ops.py` is standalone.** It reads/writes `ciel_data/facts.json` directly with zero
   imports from `core/` or `agent_system/`. Keep it that way.

7. **Tool packs auto-register.** Drop a `skills/**/*_ops.py` exposing `get_*_tools()` returning
   `{"tools": [...], "prompt": "..."}`. `ToolManager` discovers it; the UI skill grid updates from
   `GET /skills` with zero frontend edits. No manual registration.

   **Because nothing in `core/` gates a new pack, the burden is on the pack.** Read
   *Skill Contract* in `conventions.md` before adding one. The rule that most often bites:
   **a tool can be invoked more than once per request** — self-healing (3), Brain
   self-correction (2), and the Tier-1 loop (`AGENT_LOOP_MAX_ROUNDS`) can each re-run it.
   Make tools idempotent, or split preview/confirm and declare the pairing with
   `make_result(confirm={"tool": …, "args": …})` rather than editing core.

7c. **Three agent-capability tiers are live; read `architecture.md` before changing any
   of them.** Tier 1 = the observe/re-plan loop (`continuation.py`), Tier 2 = durable task
   state (`task_state.py`), Tier 3 = `AUTO`/`ASK`/`DENY` permissions with plan-level
   approval (`permissions.py`). All three share one rule: **the decision is deterministic
   Python, the LLM only plans.** Plan approvals are scoped to the exact `(tool + args)`
   reviewed — never to a tool name, or a step the loop invents later would inherit an
   approval the Master never gave. `DENY` outranks every grant and `DISABLE_SAFETY_GATE`.

7h. **One delivery per recipient per turn.** `execute_tool` suppresses a second
   outbound send (`send_gmail_message`, `send_gmail_html_message`, `reply_to_email`,
   `send_telegram`) to the same recipient within one request. This exists because TWO
   mechanisms independently complete a plan missing its send step — the workflow
   safeguard in `execute_multi_tool` and the Tier-1 loop's re-plan — and they delivered
   the same report twice with different subjects. Key on the RECIPIENT, never on a full
   argument signature: the duplicates differ in subject and body by construction. Record
   only on SUCCESS (a failed send must stay retryable), reset per turn, and check before
   the safety gate. Any new path that can send must go through `execute_tool`.

7g. **Prompts are assembled in one place, and long runs can be stopped.** Add context
   through `ContextAssembler` (`core/context.py`), never by appending to a string:
   blocks carry a priority so the budget knows what to shed, they are dropped **whole**
   (a truncated `[WORKING DIRECTORY: …]` still reads as a fact while being wrong), and
   every drop is logged. Bound anything data-sized at its source — RAG recall is the
   only block that can grow without anyone editing code. For Tier 5, check
   `_abort_if_cancelled()` at **step boundaries only**: stopping mid-tool is corruption,
   not cancellation.

7e. **Two memory stores, and the difference is a security boundary**
   (`core/user_model.py`). `facts.json` is pull-only and may hold credentials; it is
   NEVER injected. `user_model.json` IS injected into prompts and therefore refuses
   anything matching `looks_like_secret()` — everything in it is sent to the provider on
   every call that carries it. Never merge the two, and never inject the vault. Inside
   the profile: what the Master **stated** outranks what Ciel **inferred** (a lower
   authority cannot overwrite a higher one), non-stated traits **decay** so an offhand
   remark cannot harden into a permanent trait, and `render()` is **hard-capped** by
   `USER_MODEL_TOKEN_BUDGET` because this is a fixed tax on every call — the exact cost
   pattern Tier 4 exists to control. It renders `""` when empty, so it costs nothing
   until it knows something. Tier 7b learns unprompted, but only behind
   `assess_preference()` — free Python that skips one-off wording ("hôm nay") outright
   and decides the KIND itself; the model only proposes key/value, or it would always
   claim `stated` and overwrite what the Master actually said.

7f. **An unattended run can never be given consent by silence.** `decide(...,
   attended=False)` returns `DEFER`, not `ASK`: the action goes to `DeferredStore` and is
   raised at the next interaction. Session grants, plan approvals and
   `DISABLE_SAFETY_GATE` are all ignored there — every one of them is evidence that a
   human agreed *while present*. `CielCore.unattended` is thread-local and must be
   propagated explicitly into any worker thread; a safety control that fails open in a
   worker is worse than none. Deferred actions are never replayed automatically —
   re-running a mutating decision against a changed world is a different action.

7d. **Proactivity is opt-in, budgeted, and must never repeat itself**
   (`core/triggers.py`, `core/notifier.py`). A trigger's `check()` is plain Python over
   data already on disk — an idle Ciel costs zero tokens. Three rules hold it together:
   a `Notification` with no `action` cannot interrupt (it is demoted to the digest);
   its `key` must come from the identity of the underlying thing, never the message
   text, or the per-key cooldown cannot turn a standing condition into a single event;
   and `PROACTIVE_DAILY_BUDGET` caps interruptions in Python, never by asking a model to
   restrain itself. Delivery routes by **liveness** — a running process is not a present
   human. New checks that read `thoughts.log` must use `_iter_entries`: the live log is
   CRLF, and a parser that misses that reports "nothing wrong" forever.

7b. **The Tier-1 loop decides in code, plans with the LLM** (`core/continuation.py`).
   `ContinuationPolicy.assess()` is pure Python and costs nothing, so an ordinary request
   spends zero extra calls; only when a deterministic signal fires does one planner call
   happen. Every bound (rounds, planner calls, wall-clock — checked *before each step*, not
   just between rounds — and steps per round) lives in `LoopBudget`. It is fail-open: any
   error keeps the first round's answer. When adding a signal, add it to `assess()` with a
   unit test; never make the loop's safety depend on the model saying a magic word.

8. **Workspace file ops are sandboxed** to `ciel_workspace/` / `agent_output/` via `_is_safe_path()`.
   Never weaken that check.

9. **Voice never changes the prompts.** TTS output is cleaned by the deterministic `to_speech()`
   normalizer, not by dumbing down the persona (text UI, HUD, and email keep rich formatting).

10. **`.env` is the single source for credentials + provider config.** Swap providers via
    `BRAIN_PROVIDER`/`WORKER_PROVIDER`/`MIDDLEWARE_PROVIDER` with no code changes. Gotcha: the Worker
    model reads `CODER_MODEL`, not `WORKER_MODEL`.

11. **Measure a model alias before adopting it — gateways inject hidden prompts.** Measured
    on the same endpoint and key: `ccf/claude-opus-4-8` added **~6,500 tokens to every call**
    (unsuppressable), while `nt/cx/gpt-5.6-sol` added ~10. Switching between them changed
    nothing but one `.env` line, yet cut Brain tokens 52% and latency 51% at identical
    11/11 correctness — ~79% of the old cost was text nobody sent. To check: issue one
    trivial request and compare the provider's reported `input_tokens` against what you
    actually sent. See the provider table in `../note.md`.
