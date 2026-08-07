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

## Hand-off for any AI (no prior chat required)

If the Master (or a new session) only points you at **`instructionAI/`**, do this:

1. **Read this file** (`SKILL.md`) end-to-end for rules and the file index.
2. **Open `../improve.md`** — living upgrade roadmap: P0–P3, Level A/B/C, journal.  
   **As of 2026-08-06:** **Level A + Level B** and **P0 (foundation)** are **PASS**.  
   **P1.1** (honest tools) and **P1.4** (middleware/sanitize) are **DONE**.  
   Continue from the **first unchecked** item (typically **P1.2** proactive formal day, **P1.3** user_model formal, **Level C** portable, or **P2/P3**) unless the Master says otherwise.
3. Open topic files in this folder as needed (`architecture.md`, `conventions.md`, `safety_and_risk.md`, `data_pipeline.md`, `voice_and_interface.md`).
4. Implement **one** roadmap item → run the tests named there → tick Pass only when criteria match → add a journal row in `improve.md`.

**Product scope:** personal multi-tool agent (machine, mail, tools, proactive, Telegram files).  
**Not in scope:** cloning Claude Code / OpenHands as an IDE coding product. Large agents are **structure references only**.

**Suggested cold-start prompt the Master can paste:**

```text
Read instructionAI/SKILL.md and improve.md.
Ciel is Level B (Core Green) as of 2026-08-06 — P0 done; P1.1/P1.4 done.
Continue from the first unchecked improve.md item (P1.2 / P1.3 / Level C / P2 / P3).
Keep Python decisions / LLM compose. Do not turn this into a coding-IDE product.
Run: python -m backtest.run_all --unit-only  (includes quality_guards)
Then the item-specific tests; only tick Pass when criteria match.
```

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

Entry points: `main.py` (CLI, `--voice`/`--speak`), `main_api.py` (FastAPI/WebSocket for
the React/Tauri UI; also serves `GET /skills`, `/health`, `POST /tts`), and
`main_telegram.py` (Telegram bot via `core/telegram_interface.py`). All three are thin
wrappers around the same `core.agent_loop.AgentLoop` / `core.llm_connector.CielCore` —
see *Three Entry Points* in `architecture.md`. Container images for the first two are in
`docker/` (see *Docker Deployment* in `architecture.md`).

## Instruction files in this folder

| File | Purpose |
|------|---------|
| `SKILL.md` | This file — project overview, file index, critical rules, **AI hand-off** |
| `architecture.md` | File tree, module map, runtime flow, and the seven agent-capability tiers |
| `conventions.md` | Code patterns, naming, tool registration contract, safe/dangerous changes, gotchas |
| `safety_and_risk.md` | The decoupled safety model, the 8-tool + content gates, the unattended DEFER ceiling, outbound idempotence, sandbox, vision/self-heal guardrails |
| `data_pipeline.md` | Short/long-term memory, the two memory stores (fact vault vs. user model), condition triggers, cost/audit tracking |
| `voice_and_interface.md` | Voice I/O, the UI's current layout, the WebSocket protocol, and what a UI rebuild needs to wire |

## Living files at repo root (not inside this folder, but required for upgrades)

| File | Purpose |
|------|---------|
| `../improve.md` | **Upgrade roadmap** — P0–P3, Level A/B/C, journal. **Level B reached 2026-08-06.** Update checkboxes when work lands. |
| `../architect.md` | Full project map + changelog (heavier; optional after SKILL + architecture) |
| `../note.md` | Dated live status / provider notes (diary; not stable rules) |
| `../backtest/run_all.py` | Unified regression runner (`python -m backtest.run_all [--unit-only\|--skip-exploratory]`) |
| `../backtest/test_quality_guards.py` | Unit guards (sanitize, write-intent, gmail digest, HTML builder, TG upload protect) — no ephemeral `_smoke_*` scripts |

For the dated changelog and current live status, see `../architect.md` and `../note.md`
— **stable rules stay in `instructionAI/`**; **what to improve next stays in `../improve.md`**.

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
   tools idempotent, or split preview/confirm via `make_result(confirm={...})`. A
   module named in `.env`'s `DISABLED_SKILL_MODULES` (comma-separated stems, e.g.
   `vision_ops`) is skipped before `ToolManager` even imports it — for a capability
   class that genuinely can't work in a given deployment (vision needs a real display;
   the Docker image has none), not for anything reachable by normal denial (that's
   `CIEL_DENY_TOOLS`, rule 14, a runtime decision — this is a load-time one).

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

    An HTML `send_telegram_document` is the terminal Telegram delivery: the plain-text
    safeguard must not append `send_telegram`, and the Telegram interface must not repost
    the full summary under the attachment.
    outbound send (`send_gmail_message`, `send_gmail_html_message`, `reply_to_email`,
    `send_telegram`, `send_telegram_document`) to the same recipient/channel within one
    request. Exists because two independent mechanisms — the workflow safeguard and the
    Tier-1 loop's re-plan — can both complete a plan missing its send step, delivering
    the same report twice with different subjects. Key on the RECIPIENT (or tool name
    for single-destination Telegram), never a full argument signature; record only on
    SUCCESS. Any new path that can send must go through `execute_tool`.

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
    untouched by both of these rules. The same `action == "chat"` branch also appends
    Brain's real `hidden_thought.reasoning` (when present) alongside `task`, labelled
    the same non-binding way — caught live: without it, the Worker asked to explain a
    "why not" it was never given the real reason for invented a plausible-sounding but
    FALSE one (a denied browser-control request came back as "I don't have permission
    to control the browser", when the real reason was Brain misreading an unrelated
    "kệ lệnh đó đi" as cancelling the whole turn). Same lesson as rule 5: give the model
    the fact, or it fabricates one to fill the gap.

22. **A new front-end (CLI/WS/Telegram/...) must set `confirm_callback`, never bypass
    it.** All three implementations — `main.py`'s blocking `input()`, `main_api.py`'s
    WebSocket JSON round-trip, `core/telegram_interface.py`'s inline-keyboard
    round-trip — share the exact signature `confirm_callback(tool_name, preview,
    tool_args) -> bool` and the same fail-closed timeout behaviour (no answer = declined,
    never approved). A front-end that can trigger real tools and skips this is a bypass
    of the Tier-3 safety gate, not a new feature. If the front-end has its own notion of
    "who is allowed to talk to this at all" (Telegram's `chat_id` allow-list is the
    first example), that check happens BEFORE the message ever reaches
    `core.process()` — never inside a tool or a prompt.

23. **`_self_correct()` must never let a FAILED escalation discard a result that already
    worked.** Caught live: `read_document` fully extracted a 1-page CV, but Brain's own
    evaluator misjudged it "cut off midway" and escalated to `execute_shell_command` —
    which itself failed twice. The old code did `result = new_result` unconditionally,
    so the Master was told "couldn't read it" about a file Ciel had already read fine.
    Fixed with `StepRecord(...).failed()` (the same deterministic failure-signal check
    Tier 1 uses) gating the overwrite: only replace the original when the new attempt
    actually did better. A second, narrower fix for the SAME false "unsatisfied"
    judgment: `read_document` now states the real page count and "TOÀN BỘ N trang,
    không bị cắt" IN the data itself (after the 30k-char truncation check, never
    before) — a fact the evaluator reads beats a prompt instruction it might ignore.

24. **A path is only "absolute" according to the OS actually running the process.**
    `skills/internal/system_ops.py`'s `_resolve_target_path()` used `Path(...).is_absolute()`
    to accept a path into the sandbox — true for `D:/Ciel-2.0/ciel_workspace/...` on
    Windows, but pathlib has no concept of drive letters on Linux, so the exact same
    string returns `False` there. Once Ciel moved into a Docker container (Linux), a
    Windows-style path the Brain produces (per its WINDOWS SYSTEM ARCHITECT rules) fell
    into the relative-default branch and got glued onto `WORKSPACE_DIR` verbatim —
    `ciel_workspace/D:/Ciel-2.0/ciel_workspace/...`, which obviously never exists, even
    though the real file was sitting right there. Fixed with `_remap_into_sandbox()`: a
    `^[A-Za-z]:/` regex catches what `is_absolute()` misses on Linux, finds a
    `ciel_workspace/`/`agent_output/` segment anywhere in the string, and remaps
    everything after it onto THIS host's real copy of that zone — a path with no such
    segment is still rejected outright, so this widens recognition, not the sandbox
    boundary.

25. **Telegram inbound photos/documents land in the sandbox as a FILE, then a normal
    turn — never a forced tool call.** `core/telegram_interface.py` downloads a
    `message.photo`/`message.document` into `ciel_workspace/telegram_uploads/` and
    enqueues a plain note (`"[Ảnh/File Master vừa gửi... đã lưu tại: ..."] + caption`)
    exactly like any text message — Brain routes it like anything else (usually to
    `read_document` / `read_file` for a file, or `describe_image_file` for an image),
    never a hardcoded path. **Do not treat "đã lưu tại" as write intent** — that phrase
    is delivery metadata; `_has_write_intent` strips it so the multi_tool write
    safeguard cannot append `write_file` onto the upload and clobber it. **Never
    `write_file`/`append_file` into `telegram_uploads/`** unless the Master explicitly
    asks to overwrite an upload (`execute_tool` blocks it). Reports go to
    `agent_output/`. Outbound: `send_telegram` (plain text; no Markdown-by-default —
    underscores break parse_mode) and `send_telegram_document` (HTML/PDF attachments —
    Telegram does not render full report HTML in the chat bubble). Analysis HTML:
    `build_analysis_report_html` in `skills/internal/report_ops.py` + template
    `email_template/analysis_report.html` — prefer `output_path=agent_output/….html`
    (when omitted, the tool derives `agent_output/<source>_summary.html`) then
    `send_telegram_document`. A short reply such as "cứ gửi qua đây" after a path
    clarification confirms this attachment workflow; it is not a new ambiguous chat turn.
    `describe_image_file` is NOT the live-screen tools
    (`vision_act`/`vision_describe`); it opens an existing FILE. Bundled in
    `vision_ops.py`, so `DISABLED_SKILL_MODULES=vision_ops` turns it off too.

26. **A Gmail query needs `category:primary` even when combining operators, and
    `category:primary` alone is not a promotions filter.** Found live: a digest query
    `newer_than:7d {is:unread is:important}` (valid Gmail OR syntax, not malformed)
    with no `category:primary` surfaced marketing/job-board mail Gmail auto-marks
    unread/important — `core/scheduler.py::_fetch_unread_emails()`'s hardcoded
    `q="is:unread"` had the same gap, fixed to `"is:unread category:primary"`. But
    verified live against a real inbox: `category:primary` does NOT reliably exclude
    job-alert/marketing senders either — Gmail's own ML categorization puts many of
    them in Primary for a given account. That is a curation problem the Gmail query
    cannot solve (a stricter query risks excluding mail the Master actually wants —
    confirmed live when "exclude ITviec" would have hidden real job applications the
    Master made). The fix that held: ask the DIGEST prompt (`scripts/daily_digest.py`'s
    `DIGEST_REQUEST`) to split "cần chú ý" (summarize fully) from "tự động/định kỳ"
    (list sender + count only) — curation belongs in how results are presented, not in
    a query trying to guess intent it cannot know.

27. **A deterministic floor must constrain the OUTCOME, not the RECOVERY PATH.** The
    single worst bug of this cycle: `_evaluate_result()` correctly refused to let an
    obviously-failed tool result be rubber-stamped as `satisfied`, but implemented that
    by `return`ing early with `action="chat"` hardcoded — short-circuiting the very
    evaluation below it, the only path handed `Available tools:` and therefore the only
    one able to name a working alternative. Net effect: the one moment recovery mattered
    most was the one moment it was structurally impossible; a failed tool could only ever
    be narrated. Found live: `get_market_price("DXY")` → "Could not find price" → forced
    chat → the Worker, told to "suggest a next step" with no tool context of its own,
    invented *"Mình chưa gọi được tra cứu web trực tiếp"* — false; `stealth_search` was
    loaded, working, and would have answered it. Fix: run the evaluation anyway, then
    force `satisfied=False` afterwards regardless of the reply. The guarantee is
    unchanged; the recovery is no longer forbidden. Generalise it: when adding a
    deterministic guard, ask whether it is forbidding a bad *answer* or a whole *avenue*.

28. **A non-LLM code path still owes the persona's rules — no prompt can fix it.**
    `_format_fact_result()` is plain Python that never reaches the Worker, and it
    hardcoded English templates ("Master, I saved your {key}"), so a Vietnamese Master
    got English replies from the fact vault no matter what the persona said. Anything
    that renders user-facing text outside the Worker must resolve language itself
    (`_detect_language(user_input)`); grep for f-strings returned straight to the user
    before assuming the persona covers them. Related, same file: dict lookups are
    exact — `get_fact("tên")` missed a fact saved as `"Tên"`, and the tool's own prompt
    tells the model to use snake_case English-ish keys, guaranteeing the mismatch. A
    lookup a human would call obvious should be case-insensitive, and a miss should
    hand back the real keys so the next attempt can succeed instead of re-guessing.

29. **An optional integration that degrades silently will degrade unnoticed.**
    `_serpapi_search()` returns `[]` (never raises) when `SEARCH_API_KEY` is absent, so
    search falls through to the weaker DuckDuckGo/RSS tier and everything still *looks*
    like it worked. That is correct for graceful degradation and dangerous for
    operations: the CI workflow passes env explicitly via `docker run -e`, so a key
    present in `.env` but missing from `.github/workflows/` meant the daily digest ran
    for days on the older source. Verified by running the container **both with and
    without** the key — a negative control is what turns "I added the var" into "the
    behaviour actually changed". Whenever a capability can silently no-op, add the
    variable in **both** places and prove the difference, don't infer it.

30. **Regression is `run_all` + maintained `test_*.py` — not a pile of `_smoke_*.py`.**
    Ephemeral live smokes were cleaned (2026-08-06). Durable checks live in
    `backtest/test_quality_guards.py` (sanitize, write-intent, gmail digest, HTML
    builder, telegram_uploads write-block) and the existing unit/live suites wired
    into `python -m backtest.run_all`. Do not reintroduce one-off smoke modules for
    every feature; fold pure guards into unit tests and keep live coverage in
    integration / hard_special when needed.

31. **Honest tool failures (P1.1) and outbound sanitize (P1.4) are product rules, not
    optional polish.** Partial/failed tools must surface error/empty honestly — never
    invent prices or file bodies. Outbound email bodies must strip `agent_output/` /
    `ciel_workspace/` path tokens while keeping real tool numbers; Middleware is
    fail-open and must not blank live tool values as "implausible".

## Current pass bar (snapshot — detail in `../improve.md`)

| Level / item | Status (2026-08-06) |
|--------------|---------------------|
| Level A (Daily OK) | **PASS** |
| Level B (Core Green) | **PASS** |
| P0 foundation | **PASS** |
| P1.1 honest tools / P1.4 middleware | **PASS** |
| P1.2 proactive day / P1.3 user_model formal | open |
| Level C portable | open |
| P2 coworker / P3 models | open |

**Default target after Level B:** next unchecked item in `improve.md` (not reopening P0).
