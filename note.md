# Ciel 2.0 — Live Status & Reference

Rolling status, the deterministic-safeguard inventory, a condensed changelog, and the outbound
email templates. This is the "what is true right now" file — for the stable architecture see
[`instructionAI/`](instructionAI/) and [`architect.md`](architect.md).

> The universal "how to store project knowledge for an AI" guide that used to live at the top of
> `note.txt` was a duplicate — it now lives only in [`instructionAI/Reamdepromp.md`](instructionAI/Reamdepromp.md).

---

## Agent-capability tiers (roadmap, as of 2026-07-26)

Five tiers were first identified as the gap between "executes commands" and "pursues
goals". Two more were added once it became clear the original five would produce a
cheaper, steadier agent but still not a JARVIS: 4 and 5 only optimise what already runs,
while 6 and 7 add behaviour that has never existed here. The ordering matters — each
tier was only worth building once the previous had removed the reason the model kept
being asked to compensate in prose.

| Tier | What it fixes | Status |
|------|---------------|--------|
| **1 — Agent loop** | A flat plan cannot express "if X then Y". Execute → observe → re-plan. | ✅ `core/continuation.py` |
| **2 — Task state** | Interrupted work vanished; "what were you doing?" had to be guessed. | ✅ `core/task_state.py` |
| **3 — Permissions** | Approval was binary and arrived mid-execution, so "no" left work half-done. | ✅ `core/permissions.py` |
| **4 — Context discipline** | **99% of every Brain call is fixed overhead** (4,291 tok: router prompt 37%, tool list 33%, persona 28%, the actual request **0.4%**). Injections were added ad hoc in `process()`, with no assembler and no token budget. | ✅ `core/context.py` — budgeted assembler + measured router-persona A/B (−27% available) |
| **5 — Ergonomics** | A long request cannot be interrupted — `main.py` is a blocking `input()` loop, so the only way out of a 566s call is killing the process. | ✅ cooperative cancel at step boundaries; Ctrl+C cancels the request, not the process |
| **6 — Proactivity** | Ciel only ever answers. It could fire on a clock, but never on a *condition* — so it could say "good morning" and not "that job is still stuck". | ✅ `core/notifier.py`, `core/triggers.py` — 9 triggers in 3 groups, unattended permission ceiling, feedback muting |
| **7 — User model** | The fact vault is **pull-only and empty**: nothing injects facts into context, so the model must guess an exact snake_case key *and* choose to call `get_fact`. Proactivity without this is spam. | ✅ `core/user_model.py` — bounded profile injected into prompts, learned unprompted behind a free gate |

Tiers 6 and 7 were promoted ahead of 4 and 5 deliberately: 4 and 5 only optimise what
already runs, while 6 and 7 add behaviour that never existed. Both are now complete;
**4 and 5 are what remain**, and 4 is still the hard blocker for local/small models.

Not a tier, but done alongside: **parallel tool execution** (`core/parallel.py`).

Tier 4 is also the blocker for **local/small models**: the 4,291-token floor means a
4K-context model cannot run Ciel at all, regardless of how capable it is. The routing
contract itself is already model-agnostic — verified by swapping the Brain to a much
cheaper alias with no loss of correctness (see the provider section below).

## Current Status (as of 2026-07-29)

**Model tiers — all on ONE custom OpenAI-compatible endpoint (switched from Vilao
2026-07-27 after Vilao hit `INSUFFICIENT_BALANCE`; Vilao config kept in `.env`, unused,
for rollback):**

| Tier | Provider | Model | Config key |
|------|----------|-------|------------|
| Brain (Router/Planner) | custom | `gpt-5.6-sol` | `BRAIN_PROVIDER`, `BRAIN_MODEL` |
| Worker (Generator) | custom | `gpt-5.6-luna` | `WORKER_PROVIDER`, **`CODER_MODEL`** |
| Middleware (Finalizer) | custom | `gpt-5.5` | `MIDDLEWARE_PROVIDER`, `MIDDLEWARE_ENABLED` |

Switching provider going forward is a 2-variable `.env` edit (`API_KEY`+`BASE_URL`) plus
whichever `*_MODEL` names change — see `agent_system/config.py`'s note.

**Three front-ends now, all thin wrappers over the same `CielCore`:** `main.py` (CLI),
`main_api.py` (WebSocket/REST, Vercel-facing), `main_telegram.py` (Telegram bot,
`core/telegram_interface.py` — chat_id allow-listed, inline-keyboard confirms, accepts
inbound photos/documents). All three can run in Docker (`docker/`) — see the Docker
Deployment entry below.

- **Config traps:** the Worker's MODEL is read from `CODER_MODEL`, never `WORKER_MODEL`; its
  PROVIDER is `WORKER_PROVIDER` (`CODER_PROVIDER` is not read).
- **Model aliases drift.** DeepSeek retired `deepseek-chat` (use `deepseek-v4-pro`/`-flash`); Vilao
  aliases change too (e.g. `awkr/…` → `ccf/…`). If a tier returns empty output or a 4xx, verify the
  alias is still live *before* suspecting the code.

**Safety model (decoupled — never re-couple):**
- `SAFETY_OPEN=true` + `VILAO_SAFETY_BYPASS=true` → relax Brain **content** filtering only.
- `DISABLE_SAFETY_GATE=false` (default) → the destructive-tool **confirmation gate stays ACTIVE**.
  Coupling these two once let a *denied* confirmation still delete the file — keep them separate.

**Testing:** the 5 no-LLM suites (`test_context`, `test_outbound`, `test_proactive`,
`test_user_model`, `test_conversation_bugs` — 369 assertions total) are the fast
regression gate, run after every change to `core/`. `test_conversation_bugs.py` is
**not log/state-sandboxed** — it builds real `CielCore()` instances, so running it
writes its fixture conversations straight into the real `ciel_data/logs/thoughts.log`;
confirmed live to read like a "repeating bug" on casual inspection after several runs
in one session. `test_integration.py`/`test_hard_special.py`/`test_brain_worker.py`/
`test_rag_memory.py` make real LLM calls and send **real** email — 3 disposable
addresses are approved for this: `kxctran@gmail.com`, `kxcpro123@gmail.com`,
`prokxcpro@gmail.com` — verified by a real Gmail Message Id, not by response text.
A `venv` on **Python 3.12+** is required (`pandas-ta`).

---

## Deterministic Safeguards (the core philosophy in code)

The LLM's decision is a *proposal*, checked by deterministic code before anything is written or
sent. **Recurring lesson: any guard that keys off an EXACT model-emitted string is fragile — a
stronger model paraphrases it. Match by pattern/intent instead.**

| Safeguard | What it does | Where |
|-----------|--------------|-------|
| Decoupled safety flags | Content filter vs destructive-gate are independent | `config.py` |
| High-risk tool gate + dangerous-code gate | 8 tools need Y/N; write/exec content is scanned for destructive patterns | `_HIGH_RISK_TOOLS`, `_find_dangerous_code_patterns()` |
| Workspace sandbox | File tools quarantined to `ciel_workspace/`/`agent_output/` | `_is_safe_path()` |
| Outbound sanitizer + Middleware | Strip meta/paths/placeholders; then a scoped, fail-open LLM finalizer for email bodies | `_sanitize_outbound_email()`, `middleware.py` |
| **Synthesis-placeholder guard** | Block any *paraphrased* `[SYNTHESIZE…]`/`[…_TO_BE_SYNTHESIZED]` shell from reaching a file **or** an inbox (symmetric) | `_has_unsynthesized_placeholder()` |
| **Subject enforcement** | Overwrite a Brain-composed subject with the user's exact `subject '...'` | `_enforce_subject()` |
| Workflow safeguards | Auto-append a missing terminal send/write step | `process()` / `execute_multi_tool` |
| **Multi_tool dependent steps** | `{{prev}}`/`{{step_N}}` in a later step's args ← an earlier step's raw output | `_resolve_step_refs()` |
| **Inspection-tool memory fallback** | A memory question routed to `list_workspace`/`read_file` that returns nothing → answer from recalled RAG, labeled *unverified* | `_memory_fallback_for_inspection()` |
| Self-healing skip-list | Don't retry errors no param guess can fix (missing lib, timeout, geo) | `_HEALING_SKIP_PATTERNS` |
| Anti-fabrication | Claim "sent" only on a real Message Id; referential sends reuse the exact prior reply | `_is_referential_send()` |
| **Search results carry their own provenance** | Every hit prints `Published` + `Source`; undated ones are labeled `UNKNOWN … do NOT state a date` so no date can be invented | `web_agent_ops.py` |
| **Real-date recency filter** | Enforce the requested time window on actual `pubDate` (DuckDuckGo's `timelimit` let 8–16-day-old hits through a "past day" query) | `_WINDOW_DAYS` |
| **Language-aware search** | `[USER LANGUAGE: X]` survives the translate step → Vietnamese question yields a Vietnamese query and Vietnamese outlets | `_detect_language()` + Router rule |
| **Tolerant router JSON** | Slice the first balanced `{…}` (prose/trailing text tolerated) before `json.loads` | `_extract_json_object()` |
| **Generic pending confirmation** | A preview tool declares its own follow-up via `make_result(confirm=…)`; a later bare "yes" executes it with **zero** LLM calls, survives restart, one slot only | `continuation`-adjacent: `_set_pending_action()`, `skills/_result.py` |
| **Tier-1 loop policy** | Whether to observe-and-re-plan is decided by 5 structural signals in pure Python; all bounds (rounds/calls/time/steps) enforced in code, fail-open | `core/continuation.py` |
| **Literal preservation across paraphrase** | Every path/URL/email the Master typed must survive the Brain-translate step verbatim; if one is redacted or altered the translation is DISCARDED | `_lost_literals()` |
| **Parallel batching is opt-in** | Only tools explicitly declared read-only batch concurrently — a blacklist would silently parallelise a newly added mutating skill | `core/parallel.py` |

---

## Provider gotcha: gateway-injected prompts (measured 2026-07-26)

**A model alias can silently add thousands of tokens to EVERY call.** Measure a new alias
before adopting it — one probe call is enough:

| Endpoint / alias | Injected per call | Suppressible? |
|---|---|---|
| Vilao `ccf/claude-opus-4-8` | **~6,500** | ❌ no — same with or without a system message |
| Vilao `nt/cx/gpt-5.6-sol` | **~10** | ✅ (2,485 only if NO system message is sent) |
| Vilao `op/deepseek/deepseek-v4-pro` | ~4 | ✅ |
| TEM `claude-*` | ~1,750 | ❌ — model self-identifies as "Claude Code" |
| TEM `gpt-5.6-*` | 0 | ✅ |

Ciel always sends a SystemMessage to the Router, so it lands on the clean side of every
alias that behaves this way — except the ones that inject unconditionally.

**Switching `BRAIN_MODEL` from `ccf/claude-opus-4-8` to `nt/cx/gpt-5.6-sol` (same
provider, same key, one line in `.env`): identical 11/11 correctness, Brain tokens
74,273 → 35,687 (−52%), latency 218s → 107s (−51%), per-call 8,252 → 4,460 tokens.**
~79% of the old Brain cost was injected text nobody asked for.

How to check an alias: send one trivial request and compare the provider's reported
`input_tokens` against what you actually sent. Anything above a handful is theirs.

## Where a Brain call's tokens go (measured with tiktoken, 2026-07-26)

| Component | Tokens | Share |
|---|---|---|
| `CIEL_ROUTER_PROMPT` | 1,591 | 37% |
| Tool list (44 tools, ~32 each) | 1,437 | 33% |
| Persona | 1,205 | 28% |
| Deterministic injections | 42 | 1% |
| **The user's actual request** | **16** | **0.4%** |

**99% of every Brain call is fixed overhead resent verbatim.** Two consequences: every new
skill permanently raises the cost of every call, and the 1,205-token persona is being sent
to a component that only ever emits JSON. The 4,291-token floor is also the hard blocker
for small/local models — a 4K-context model cannot run this at all.

## Changelog (most recent first)

### 2026-07-28 → 07-29 — Docker deployment, a Telegram front-end, and two real bugs found live

**Docker (`docker/`).** Two front-ends, one image, split into separate compose files
(`docker-compose.api.yml`/`docker-compose.telegram.yml`) so each can be
built/started/stopped independently. `DISABLED_SKILL_MODULES` (new `.env` knob) lets
`ToolManager` skip a whole skill module before even importing it — used to disable
`vision_ops` (screen control needs a real display/mouse the container doesn't have).
A detour worth recording: tried swapping RAG's embedding function to ChromaDB's own
ONNX `DefaultEmbeddingFunction` to drop the ~10GB `torch`+CUDA dependency chain —
reverted after discovering live that ChromaDB persists the embedding-fn choice IN the
collection itself, so a different one at runtime doesn't migrate the real, existing
`vector_memory/` (1250+ real memories) — it just fails at query time. Kept
`sentence-transformers`, but the Dockerfile installs a CPU-only `torch` wheel first,
landing at ~3.5GB instead of ~10GB. Both real images (`ciel-api`, `ciel-telegram`)
verified end-to-end against a real Docker daemon — build, `/health`, `/skills`, a live
Telegram round-trip, and a real RAG search against the real collection.

**A third front-end: `main_telegram.py` / `core/telegram_interface.py`.** Long-polls the
Bot API directly (no new dependency), chat_id allow-listed (this front-end can run real
tools — shell, email, file writes — so an unauthorized sender must never reach
`core.process()`), confirm gate via inline Yes/No keyboard (same `confirm_callback`
contract as the CLI prompt and the WebSocket dialog). Verified live end-to-end,
including a vision task (`vision_act` opening YouTube) run bare on Windows (Docker has
no display for `pyautogui`). Also accepts inbound photos/documents — downloaded into
`ciel_workspace/telegram_uploads/`, handed to Ciel as an ordinary message, routed like
anything else (never a forced tool call). New tool for the image case:
`describe_image_file` (`skills/internal/vision_ops.py`) — looks at an existing image
FILE, not the live screen; shares `_call_vision_llm` with `vision_act`/`vision_describe`
only as an implementation detail, and (a known trade-off, not a bug) is bundled into
the same module so `DISABLED_SKILL_MODULES=vision_ops` disables it too even though it
needs no display.

**Two real bugs found reading `thoughts.log`, both fixed:**

1. **`_self_correct()` discarded a good result when its own "fix" attempt failed.**
   `read_document` fully extracted a 1-page CV; Brain's evaluator misjudged it "cut off
   midway" and escalated to `execute_shell_command`, which failed twice (missing lib,
   then a script bug) — the OLD code did `result = new_result` unconditionally, so the
   Master was told "couldn't read it" about a file Ciel had already read correctly.
   Fixed with `StepRecord(...).failed()` (Tier 1's own deterministic failure-signal
   check) gating the overwrite — only replace the original when the new attempt
   actually did better. Paired with a narrower fix for the same false judgment:
   `read_document` now states the real PDF page count and "TOÀN BỘ N trang, không bị
   cắt" IN the returned text itself (placed after the 30k-char truncation check, never
   before) — a fact the evaluator reads outranks a prompt instruction it might ignore.
   Verified with a scripted reproduction of the exact failure chain: the original CV
   content now survives.
2. **A Windows-style path (`D:/Ciel-2.0/ciel_workspace/...`) silently failed inside the
   Linux container.** `_resolve_target_path()`'s `Path(...).is_absolute()` check is
   true for that string on Windows, false on Linux (no drive-letter concept) — so once
   Ciel moved into Docker, an otherwise-valid path into the real sandbox fell into the
   relative-default branch and got glued onto `WORKSPACE_DIR` verbatim, producing a
   path that never exists even though the real file was right there. Fixed with
   `_remap_into_sandbox()`: a `^[A-Za-z]:/` regex catches what `is_absolute()` misses,
   finds a `ciel_workspace/`/`agent_output/` segment anywhere in the string, and remaps
   onto THIS host's real copy of that zone — a path with no such segment is still
   rejected, so this widens recognition, not the sandbox boundary.

**Gmail digest curation.** `core/scheduler.py::_fetch_unread_emails()`'s hardcoded
`q="is:unread"` had no `category:primary`, so marketing/job-board mail Gmail
auto-marks unread leaked into the morning digest — fixed. But verified live against a
real inbox that `category:primary` alone is NOT sufficient (Gmail's own categorization
puts many job-alert senders in Primary for this account, and the Master confirmed some
of those — ITviec — are real, wanted mail, ruling out a sender blocklist). The fix that
held: ask the digest prompt (`scripts/daily_digest.py`) to split "cần chú ý" (summarized
in full) from "tự động/định kỳ" (sender+count only) — curation belongs in how results
are presented, not in a query trying to guess intent it cannot know.

**New CI job:** `.github/workflows/health_check.yml` now builds the real Docker image
(not a bare `pip install` on the runner — doubles as a daily proof the image still
builds) and, after the health check passes, runs `scripts/daily_digest.py` — asks Ciel
through real Brain routing for a Gmail+news summary, reports to Telegram. Also fixed:
the workflow's own env vars were stale (`BRAIN_PROVIDER=vilao`/`GPT_API_KEY` left over
from before the 2026-07-27 provider switch), which is why it kept reporting "alive"
against a model no longer in use and eventually failed with Vilao's 402.

### 2026-07-26 — The email was sent TWICE (fixed), and a correction to yesterday's note

I reported earlier that the router "drops the send step ~1 run in 3, so the mail is never
sent". **That was wrong, and the truth is worse.**

Capturing the router's full decision showed it does not drop anything. It sometimes
*defers* the send deliberately — `needs_followup: true`, with the send described in
`response_hint` ("…rồi gửi tới kxctran@gmail.com") — which is a plan shape the router
prompt explicitly permits under RESULT-DEPENDENT REQUESTS. It wants the data before
composing.

Two independent mechanisms then complete that plan, and **neither knows about the other**:

1. the deterministic workflow safeguard in `execute_multi_tool` (llm_connector.py ~2510),
   which appends a send step with the generic subject `"Báo cáo từ Ciel"`;
2. the Tier-1 loop, whose re-plan routes to `send_gmail_message` with the Brain's own
   subject.

Both fire. Reproduced live: two emails to the same address, two different subjects.

The comment at the loop's call site — *"extra rounds … can never double-send"* — is true
only for a send present in the ORIGINAL plan, which is separated out and run once after
synthesis. It never covered a send the loop invents. My Tier-1 work added the second
path and I did not notice.

**Fix: outbound idempotence at the one choke point every send passes through**
(`execute_tool`). Keyed by RECIPIENT, not by a full argument signature — the whole
problem is that the two mechanisms compose different subjects and bodies for the same
intended message, so a signature over all args would never match. Scoped to one turn, so
two separate requests may still mail the same person. Recorded only on SUCCESS, so a
failed send stays retryable. Checked BEFORE the safety gate: asking the Master to approve
a send that is about to be suppressed is worse than not asking. First delivery wins,
which is also structurally the better one — the loop runs before synthesis, so the
Brain's properly-subjected message goes out and the generic fallback is the one dropped.

Verified: `backtest/test_outbound.py` **22 assertions** (tool layer stubbed), plus two
LIVE runs to the Master's test address. The deferred shape that used to double-send now
delivers once (`Message Id: 19f9efa2bc961789`), with the log showing the second attempt
suppressed 57 seconds later; the ordinary path still delivers exactly once
(`19f9efdcfee2657d`) with the guard never firing.

**A separate, pre-existing bug found while verifying this — also fixed.** On the ordinary
path the report is synthesized BEFORE the send runs, so — correctly obeying the
anti-fabrication rule — it wrote *"Chưa gửi email … chưa có kết quả gửi thực tế từ công
cụ"*. The send then succeeded and `[EMAIL] Sent successfully` was appended underneath, so
the Master read **a denial and a confirmation of the same send**, one after the other.
The Middleware had even caught the status text and stripped it from the EMAIL
(`[MIDDLEWARE] [REVISED] … Nội dung chứa mô tả trạng thái gửi email`) — but not from the
copy shown to the Master. Confirmed not caused by the idempotence guard: the log shows no
suppression in that run (`Message Id: 19f9efdcfee2657d`).

Root cause was synthesis rule 6, which told the Worker that when there is no send result
it should "explicitly say the report is ready but do not claim it was emailed" — inviting
exactly that sentence, at a moment when a send step was still *pending*. Fixed the way
the path-redaction bug was: **instruct AND verify.**

- *Instruct* — rule 6 now forbids narrating delivery status at all when no Message Id is
  present, and says outright that a send may still run afterwards and that the system
  appends the real outcome itself.
- *Verify* — `_strip_stale_send_status()` removes "not sent" claims, and runs **only** on
  the branch where a real Message Id already came back, so every sentence it can delete
  has been disproved by the tool result. Deliberately asymmetric: negative claims are
  stripped, positive ones never are, so anti-fabrication is never weakened. Matched by
  intent (a negation beside a send word) rather than any exact string — the recurring
  lesson that a guard keyed to a model's exact wording breaks on the next paraphrase.

Verified live three times: exactly one send per turn, and the reply now ends with
`[EMAIL] Sent successfully` and no contradiction. Notably the strip **never fired** — the
prompt fix alone was enough in all three runs, so it stands as the backstop rather than
the mechanism. Suite: `backtest/test_outbound.py` is now **36 assertions**, including
that a true "Đã gửi … Message Id" line is never touched.

Minor, unfixed, cosmetic: one run produced an English subject ("Current Gold Price
(XAU/USD)") on a Vietnamese body.

### 2026-07-27 — A real email went to the wrong person: "gửi qua email đó" resolved wrong

Found by the Master re-reading `thoughts.log`: a real Message Id went to
`kxctran@gmail.com` when the address actually under discussion — declined once,
re-raised, then confirmed with "không không, cứ gửi qua email đó đi" — was
`prokxcpro@gmail.com`. Distinct from the router-task-hijack bug below: the existing
`_is_referential_send()`/`_last_ai_message_text()` guard protects the email BODY when
the Master says "send this/that content" — there was never an equivalent guard for the
RECIPIENT when the Master says "send it to that email". A comment in the code even
named this exact gap ("email"/"mail" are deliberately excluded from the body-referential
patterns because 'gửi qua email đó' means the recipient, not the content") without ever
closing it.

Fixed with `_resolve_referential_recipient()`: when the current turn's wording is
recipient-referential ("email đó", "địa chỉ đó", "that email") and names no address of
its own, `to` is grounded in the most recent explicit email address actually mentioned
in `chat_history` — mirroring the existing content-override's use of real history over
the Router's guess. An explicit address in the current turn always wins over history.

**Fixing `to` alone was not enough — a second bug surfaced while verifying.** The
override originally ran only right before the send, but the report BODY had already
been synthesized earlier in `execute_multi_tool`, from the Router's own confused
`response_hint` (which sees the full RAG-recalled context and had blended two separate
email threads). Result: a real email to the CORRECTED recipient
(`prokxcpro@gmail.com`) carried the sentence *"Đã gửi báo cáo giá vàng ... đến
kxctran@gmail.com"* — a body about a different address entirely, sent to a real
person. Moved the override to the TOP of `execute_multi_tool` (before any synthesis
runs) and, when it fires, ground the synthesis prompt in the corrected fact: *"CONFIRMED
RECIPIENT: ... NOT {original}"* — the same "hand the model the fact, don't trust it to
infer" pattern used everywhere else in this codebase.

Verified against Vilao being temporarily down (billing hold) by capturing the actual
synthesis prompt instead of a live model call: the corrected recipient and the
exclusion note both land in the prompt exactly as intended. `execute_tool` runs with
the corrected `to` (confirmed via the `Re-executing send_gmail_message` log line and
the actual call args). Suite: `backtest/test_conversation_bugs.py` grew to **55
assertions**.

### 2026-07-27 — The router was writing the final reply, not routing to one

Found by the Master re-reading their OWN real session after the fixes above: the
"Hoàng Hên" follow-up still got a "which match?" reply despite my recent-turns fix, and
separately, a Vietnamese insult got answered in English. Both traced to the exact same
root cause, and it is a different, deeper bug than anything fixed earlier today.

`CIEL_ROUTER_PROMPT` asks for `{"action": "chat", "task": "what the Worker should do"}`
— `task` is documented as a HINT. A strong Brain routinely overstepped that and
pre-wrote the actual final reply into it. Caught in the raw log, verbatim:

```json
{"hidden_thought": {"notes": "Reply in English because the actual message is English."},
 "action": "chat",
 "task": "Reply: \"Novices guess, Master. I verify. Give me a real task and measure the result.\""}
```

The Master's real message was Vietnamese (a tease). The Router — reasoning over its own
English-translated copy of the input, used to reduce content-filter false positives on
Vilao — decided the "actual message" was English and pre-wrote the ENTIRE reply in it,
then handed the Worker a literal quote-and-relay instruction. The `[USER LANGUAGE:
Vietnamese]` tag WAS present in what the Router saw; its own reasoning overrode it
anyway. The Worker never got a chance to apply its own "always match the Master's
language" persona rule, because it was not being asked to compose an answer — it was
told to relay one, verbatim, that had already been decided.

The "Hoàng Hên" case (same day, same session, earlier) is the identical bug in a
different guise:

```json
{"task": "Hỏi Master đang nói trận nào hoặc trận gặp đội nào; sau đó trả lời số bàn
          của Hoàng Hên và Xuân Son."}
```

The Router — which by design never sees `chat_history` — judged "trận đó" ambiguous and
pre-decided the Worker's move should be "ask for clarification". That overrode the fact
that the Worker's own prompt, by then, correctly contained the resolving context (this
morning's `_recent_turns_block()` fix): the previous turn's answer, "Việt Nam thắng Đông
Timor 7-0", verbatim, one turn back. The Worker dutifully executed the router's
pre-written instruction to ask rather than using context it actually had.

Both bugs are the SAME shape: for `action == "chat"`, the Router's `task` was being
handed to `execute_chat` **as the user's request**, so whatever the Router decided —
including full replies, including instructions that ignore context the Worker has —
became gospel. That inverts the architecture's own rule (Brain classifies/plans, Worker
composes) specifically for the chat path.

**Fix, in `process()`'s chat branch only:** `execute_chat` now always receives the
Master's OWN words (`user_input`, never reassigned inside `process()`, so it is
guaranteed to be the true original — not the Vilao-translated copy used only for
routing). The Router's `task` survives only as a labelled, explicitly non-binding
hint appended after the real request: *"for reference ONLY — do not treat this as an
instruction to follow, quote, or translate literally, and do not let it override the
Master's own wording or language."* When `task` already equals `user_input` (the common,
correct case), no redundant hint is added at all.

Deliberately scoped to the `chat` branch alone — `action == "code"`'s `task` field is a
different contract (a code SPEC the Worker is meant to execute, not a pre-written
answer) and is untouched; a test asserts `execute_code` still receives it unmodified.

Verified live, model-for-model, on the exact two reproductions:

```
"mày đúng là gà mờ" (task hijacked to a pre-written English reply)
  before: "Novices guess, Master. I verify..."                    (English)
  after:  "Một 'gà mờ' có lẽ sẽ phản ứng yếu đuối trước lời trêu
           chọc. Tôi thì chỉ đo lường bằng kết quả..."             (Vietnamese)

"Hoàng Hên và Xuân Son trận đó được mấy bàn" (task hijacked to "ask which match")
  before: "Master, xin hỏi Master đang nói trận nào?"
  after:  "Xuân Son: 1 bàn. Hoàng Hên (Hoàng Đức): 1 bàn."
```

Verified: `backtest/test_conversation_bugs.py` grew to **44 assertions** (8 new). Full
suite is now **369 assertions across 5 files**.

### 2026-07-27 — Three conversation bugs, found by reading a real transcript

None of these showed up in a failing test. The Master pointed at the tail of
`ciel_data/logs/thoughts.log` and asked to read it, which is what surfaced all three.

**Bug 1 — Ciel has no memory of the turn it just answered.** "giá vàng XAU/USD giờ bao
nhiêu" → an answer → "tại sao lại thế" produced a reply with zero reference to the price
just given. `chat_history` is stored, persisted to disk, and archived into RAG — and was
never once read back into a prompt. RAG is not a substitute: it only surfaces
*already-archived* turns (never the one just completed, which is exactly the one a
follow-up refers to), and it is gated by a 15-char/0.65-similarity threshold a short
follow-up routinely fails to clear.

Fixed with `CielCore._recent_turns_block()` — the last 3 turns of the CURRENT
conversation, injected via `ContextAssembler` (bounded, dropped whole under pressure) into
**only** `execute_chat` and the tool-result format path. Deliberately **not** sent to the
Router: the July-2026 decision to keep `chat_history` out of routing stands, and still
matters — a test in the new suite asserts an old unrelated request in `chat_history`
never reaches `router.route()`. Verified live: "thủ đô của Nhật Bản là thành phố nào" →
"Tokyo" → "tại sao lại là thành phố đó" (no mention of Tokyo) → a correct answer about why
Tokyo became the capital in 1868.

A second issue surfaced while fixing this: the log showed RAG recalling the CURRENT
question's own prior occurrence — "phân tích thêm về tin đó" recalled a past instance of
literally the same question, including its own unhelpful "please specify" reply, as
"context". A near-identical question is the single most similar thing in the store BY
CONSTRUCTION, so this was not a rare edge case — any repeated or rephrased follow-up would
self-recall its own failure and repeat the non-answer. Filtered in
`rag_manager.search_similar()` via `_normalize_for_selfmatch()`: strips case/punctuation
and drops a result whose archived question normalises identically to the current one.
Deliberately narrow — a paraphrase is NOT filtered, only a literal repeat, because a
paraphrase is exactly the case genuine recall should still help with. Also reordered the
merged block so `[CURRENT USER REQUEST]` comes before `[RECALLED PAST CONTEXT]` (was
reversed): if recall ever surfaces noise, it must not push the Master's actual words out
of the part of the prompt a model attends to most reliably.

**Bug 2 — an explicit "chỉ … thôi" / "đừng …" was ignored.** Every signal in
`ContinuationPolicy.assess()` is a reason to CONTINUE the agent loop; none of them was a
reason a human gave to STOP. "liệt kê từng file thôi, rồi DỪNG lại" still tripped the
fan-out signal (S4) and looped anyway. Reproduced live on 5/5 constructed cases (Vietnamese
and English).

Fixed with a **scope veto** (`_SCOPE_VETO_RE` / `request_has_scope_veto()`), checked
before every other signal — before the budget check, before the no-steps guard, before
fan-out. Deliberately asymmetric with the rest of the policy: a missed continuation costs
a slightly thinner answer, but overriding an explicit "don't" does work nobody asked for,
which given the loop can reach `send_gmail_message`/`delete_file` is the worse failure in
both directions. Verified the same 5 bug cases now stop, a control set of the identical
fan-out shape MINUS a stop word still continues (proving the veto targets the instruction,
not the shape), and 6 ordinary requests containing similar-looking words (email/git/report
requests using "chỉ", "đọc", "không") are not falsely flagged.

**Bug 3 — a 4,051-character pasted conversation was answered "Đã rõ."** The model was not
wrong — it was correctly obeying `execute_chat`'s "Respond EXTREMELY concisely... shortest
answer possible" applied uniformly regardless of input size. For a genuine question that
is right; for a routed-to-chat wall of pasted text with no clear instruction in it, the
shortest valid answer to "confirm you understood" is exactly two words.

Fixed by branching the instruction on `estimate_tokens(task)`: above ~220 tokens with no
clear ask, the rule becomes "state what you understood (naming the actual content) then
ask what to do with it — do not compress into a one-line acknowledgement, do not silently
guess a task." Verified live with a realistic multi-topic economic-news paste: 603-char
reply naming gold prices, VN stocks, Fed rates, oil, and Q2 earnings, versus the old
"Đã rõ."

All three interact: bug 1 is *why* the Master had to paste a whole conversation back in
(bug 3's trigger) — Ciel had already forgotten it. Fixing 1 reduces how often 3 gets
exercised at all; fixing 3 means when it still happens, the paste is not wasted.

Verified: `backtest/test_conversation_bugs.py` — **36 assertions**, no LLM calls (Worker
stubbed for prompt-assembly checks; `continuation.py` checks are pure Python). Full suite
count is now **361 assertions across 5 files**, plus the live-model confirmations above.

### 2026-07-26 — Tiers 4 and 5: all seven tiers now built

**Tier 4 — one place that decides what goes into a prompt.** `core/context.py` replaces
six `enriched_input +=` lines with a `ContextAssembler`: named blocks, a token budget,
and a log line saying what the call actually carried. Priority decides what is dropped;
insertion order decides layout — separating those matters, because changing ordering AND
adding a budget at once would make any A/B uninterpretable. Blocks drop **whole, never
truncated**: half a `[WORKING DIRECTORY: …]` note still reads as a fact while being
wrong. RAG recall is bounded at its source, since it is the only block whose size depends
on retrieved data rather than on code.

**The router persona A/B — measured, and it corrected me.** I expected the 1,205-token
persona to be plainly redundant for a component that only emits JSON:

| mode | persona tok | Brain input tok (11 cases) | decisions |
|---|---|---|---|
| `full` | 1,205 | 46,728 | baseline |
| `slim` | 46 | **34,221 (−27%)** | 10/11 identical |

The single disagreement looked like a real regression — `slim` produced an email plan
with no `send_gmail_message` step. Re-running that case 3× per arm: **`full` produced the
send step 2/3, `slim` 2/3.** Sampling noise, not a persona effect. So the saving is real
and the regression was not — but 11 cases at one repetition is not enough to change a
default silently, so `ROUTER_PERSONA_MODE` still ships as `full`. Set `slim` to take it.

**A separate, pre-existing bug that re-run exposed:** for an explicit "gửi mail cho
kxctran@gmail.com báo cáo tình hình vàng", the router omits the send step roughly **1 run
in 3**, on BOTH persona modes. Ciel gathers the data and never sends the mail. Nothing to
do with Tier 4 — it was always there, and only showed up because the A/B repeated one
case instead of running it once. Worth fixing next.

**Tier 5 — a request you can stop.** Ctrl+C during a request now cancels the *request*;
Ctrl+C at the prompt still exits. The job closes as `cancelled`, so `status` tells it
apart from a crash — before, killing the process destroyed the Tier-2 record along with
the run. Cancellation is cooperative and checked **only at step boundaries** (`_run_steps`
before each batch, `_continue_until_done` before each planner call): stopping inside a
half-written file or a half-sent email is not a cancellation, it is a corruption. The
flag clears at the *start* of `process()`, not when it fires, so a Ctrl+C landing between
turns cannot silently kill the next request. `threading.Event`, so the UI can cancel the
same way.

Verified: `backtest/test_context.py` **33 assertions**; live — a cancelled plan returns
`False` and runs **zero** tools, while the same plan uncancelled runs both.

### 2026-07-26 — Tiers 6 and 7 completed

**Tier 6 — the rest of proactivity.** Nine triggers in three groups: A watches Ciel
itself, B is the clock, C is the outside world behind thresholds the Master set. Four
things landed with it:

- **Unattended permission ceiling.** A trigger firing at 03:00 has nobody to ask, so
  `decide(..., attended=False)` returns a fourth decision, `DEFER`. Session grants, plan
  approvals and even `DISABLE_SAFETY_GATE` are ignored there — every one of them is
  evidence a human agreed *while present*, and none of that transfers to a background
  run. The flag is **thread-local**, because a plain attribute would let the scheduler
  thread downgrade a foreground request's confirmations mid-flight, and it is propagated
  explicitly into parallel workers: a safety control that fails open in a worker thread
  is worse than none. Verified live — the same `delete_file` call asks when attended and
  defers when not, with the confirm callback never firing.
- **`DeferredStore` deliberately does not replay.** A mutating action decided against
  03:00's world is not the same action at 09:00, and approving it from a one-line summary
  is approving a fragment — the exact failure Tier 3 removed. It records, reports, and
  lets the Master re-issue.
- **The digest is now read out.** Findings demoted for budget were only "not lost" if
  something eventually drains them; `make_digest_check` does.
- **Feedback.** After `PROACTIVE_REPEAT_LIMIT` interrupts about the *same* finding that
  is still being raised, it goes quiet. Muted per **key**, not per trigger, so one stuck
  task falls silent while a different one still gets through.

`_morning_digest` became a declared trigger, so there is now one delivery mechanism
instead of two — and the scheduler skips its 08:00 clock task when the engine owns it,
since two mechanisms delivering one brief is a double-send the Notifier cannot dedupe.

**Tier 7b — learning without being asked.** `assess_preference()` is free Python and
skips one-off wording ("hôm nay", "lần này") outright, so an ordinary turn spends
nothing; only explicit durable wording buys one extraction call. **Python decides the
kind** — letting the model self-report authority would make the authority rule
meaningless, since it would simply claim `stated` and overwrite anything. It runs on a
daemon thread from the top of `process()`: measured, the reply returns in **0.8 ms**
while a real extraction takes 5–8 s.

Live, against the real model:

```
"từ giờ mọi báo cáo phải ngắn gọn, có số liệu…"  -> 1 trait, 8.5s   (background)
"đừng bao giờ gửi mail cho t sau 10 giờ tối"     -> 1 trait, 5.0s
"hôm nay t bận nên trả lời ngắn thôi"            -> 0 trait, 0.0s   gate skipped it
"đọc file note.md giúp t"                        -> 0 trait, 0.0s
```

**The bug the suite caught:** `DeferredStore` defines `__len__`, so an **empty** store is
falsy — and empty is its normal state. `if deferred_store` therefore dropped the one
trigger whose entire job is to report on it, precisely when there was nothing to report
yet. Now `is not None` everywhere.

Verified: `backtest/test_proactive.py` **148 assertions**, `backtest/test_user_model.py`
**108 assertions**, both LLM-free. Plus live runs on the real core for the unattended
ceiling, thread-locality, trigger wiring and end-to-end learning.

### 2026-07-26 — Tier-7a: memory that pushes instead of waiting to be asked

`core/user_model.py`. The diagnosis first: `facts.json` has been `{}` this entire
project, and that is what the design produces. The vault is **pull-only** — recall needs
the model to guess an exact snake_case key *and* decide to call `get_fact`; writing needs
the Master to say "remember this" out loud. A memory that only works when someone
remembers to use it is not memory. Tier 7a is the push side.

**The two stores are split by what happens to them, not by what they hold.** The moment
a store is auto-injected, everything in it goes to the provider on every call — including
third-party gateways. So `facts.json` keeps credentials and is never injected, while
`user_model.json` is injected and **refuses** to hold credentials (`looks_like_secret`,
checked on key *and* value).

Three rules stop a profile from rotting: **authority** (`stated` > `inferred` >
`observed`, and a lower authority can never overwrite a higher one, so a bad inference
cannot quietly replace an instruction), **decay** (non-stated traits fade on a half-life
unless re-observed — "I'm busy today" must not become a permanent trait), and a **hard
token ceiling** (`render()` truncates strongest-first; this is a fixed tax on every call
that carries it, the exact pattern Tier 4 exists to control).

**Two real bugs the suite caught, both at the security boundary:**

- `\bcvv\b` did **not** match `card_cvv`, and `\bpin\b` did not match `bank_pin` —
  because `_` is a word character, so there is no `\b` before `cvv`. Both credentials
  were being **accepted into the store that gets injected into every prompt**. Fixed by
  normalising separators to spaces before matching.
- `normalize_key` produced `preferred___language` from `Preferred   Language`, so two
  spellings of one idea became two traits — and the authority/contradiction rules only
  work when the same idea lands on one key. Fixed by collapsing underscore runs.

Verified: `backtest/test_user_model.py` — **63 assertions, no LLM**, months of decay
simulated instantly. Plus a live run on the real `CielCore`: empty profile injects
**0 tokens** and produces a byte-identical prompt; a 3-trait profile costs **110 tokens**
against a 250 budget and reaches both the chat prompt and the tool-format prompt;
`wifi_password` was refused and appears nowhere in either; `USER_MODEL_ENABLED=false`
restores the exact pre-Tier-7 prompt.

Deliberately **not** injected into the Router: at 1,591 tokens it is already 37% of a
Brain call, and a profile changes *how* an answer reads far more often than *which tool*
is right. Revisit in Tier 4, once one assembler owns the budget.

Next (7b): the write path that notices a preference **without being told** — a
deterministic gate decides whether a turn plausibly contained one, and only then does a
single extraction call happen. Until then the profile only fills via `remember()`.

### 2026-07-26 — Tier-6 Group A: Ciel speaks first, about itself

`core/notifier.py` + `core/triggers.py`, wired into the scheduler's existing daemon
thread. Three triggers watching Ciel itself — `unfinished_task`, `daily_cost`,
`repeated_failure` — because that set needs no network, no API budget and no rate limit,
cannot spam (the events are genuinely rare), and is the most assistant-like thing
available: a system that notices it is unwell and says so.

The design rule is the same as every tier before it: **deterministic Python decides
whether to speak; nothing here calls an LLM at all.**

- **Contract enforced in code** — a `Notification` with no `action` is demoted to the
  digest and can never interrupt. "FYI" messages are what make an assistant tiresome.
- **Edge, not level** — the `key` comes from the identity of the thing (task id, tool
  name, date), and a per-key cooldown means a condition that *stays* true is announced
  once, not on every poll.
- **Routing by liveness** — a running process is not a present human. Past
  `PROACTIVE_IDLE_SECONDS` the CLI stops counting and delivery falls to Telegram. A
  `NOTIFY` is queued and flushed between prompts (never into a half-typed line); an
  `ASK` prints immediately and escalates if the Master never came back.
- **Budget in Python** — `PROACTIVE_DAILY_BUDGET` caps interruptions; over it, findings
  drop to the digest rather than being lost.
- Off by default, opt-in **by name** — a blacklist would silently include triggers added
  later, exactly as it would for auto-registered skills.

**The bug worth remembering.** Both log-reading checks initially found *zero* entries in
the real `thoughts.log`. `_log_thought` writes in text mode, so on Windows the live log
is **100% CRLF**, while the tail is read in binary (to bound its size) and therefore
skips Python's newline translation — the entry separator never matched. The unit suite
passed throughout, because `write_text` produced the same CRLF and the tests only
compared the parser against itself. It surfaced only when run against the production
log. **A monitoring trigger that silently never fires looks exactly like a healthy
system**, which makes this the most expensive class of bug to ship. Fixed in
`_read_tail`; after the fix the same log parses 2,492 entries.

Verified: `backtest/test_proactive.py` — **78 assertions, no LLM, no network**. Every
decision takes `now` as a parameter, so a full day (cooldowns expiring, budget filling,
escalation at +30min, midnight rollover) simulates in milliseconds. Plus a live wiring
run on the real core, which caught a real interrupted job (287 minutes idle) and
reported 1,191,796 real tokens — and exposed a second, smaller bug: the action text read
"chạy nốt **0** bước còn lại", because an interrupted record often holds only the steps
that *finished*. The count is now quoted only when the record proves work is outstanding.

Still open in Tier 6: condition triggers on the outside world (needs Tier 7 to filter
them), the digest reader, and the permission ceiling for unattended runs — today the
scheduler bypasses Tier 3 entirely, which is safe only because these triggers read.

### 2026-07-26 — The email bypass was removed (it was costing what it claimed to save)

Any request that looked like an email send used to skip the Brain entirely and be planned
by regex heuristics, so the provider's content filter could never fire on it. Live testing
showed the cure was worse than the disease:

| | Heuristic bypass | Brain |
|---|---|---|
| Subject the Master asked for | ignored, invented its own | honoured verbatim |
| "3 giờ chiều mai" | `"Email từ Ciel"` | `"Nhắc lịch họp lúc 15:00 ngày 27/07/2026"` |
| Search query | split the request at the first send-verb → **`"viết một"`** | `"tình hình kinh tế Việt Nam hôm nay tin tức mới nhất"` |
| Resulting email | *"không thu được dữ liệu thực tế nào"* | 5 real articles with dates and sources |

**3/3 email requests routed through the Brain with no content filtering at all**, so the
bypass was dodging a filter that no longer applies. It was also redundant: the `except`
branch in `process()` already falls back to the same heuristics *reactively*, when a
filter genuinely blocks the call — paying the cost only when it is real.

`EMAIL_BYPASS_BRAIN=true` restores the old behaviour if a provider ever needs it.

Two bugs found while testing this, both pre-existing:
- **The search query was built by splitting at the first send-verb** and keeping what came
  before, which only works for "\<topic\> … then send to X". "viết một email … tóm tắt tình
  hình kinh tế" put the topic *after*, so the query became `"viết một"`. Now the noise is
  subtracted rather than the sentence being cut in half.
- **The email regex `[\w.\-]+@…` does not match `+` in the local part.** Against
  `first.last+tag@gmail.com` it matches only `tag@gmail.com` — a valid-looking but WRONG
  recipient, silently. Seven copies of that pattern had drifted through
  `llm_connector.py`, three of them used to extract the recipient. All now share one
  `_EMAIL_RE`.

### 2026-07-26 — Tier-3 permissions (approve the plan, not the fragments)

`core/permissions.py`. Approval was binary (`_HIGH_RISK_TOOLS` = always ask, else never)
and arrived **mid-execution**, so declining step 3 of 4 left steps 1-2 already done. Now
every `(tool, args)` is `AUTO` / `ASK` / `DENY`, and a multi_tool plan raises **one**
prompt before the first step runs — a "no" means nothing ran.

- `DENY` (`CIEL_DENY_TOOLS`) is unconditional: no session grant, plan approval or
  `DISABLE_SAFETY_GATE` can reach past it.
- Session grants (CLI `A` = "always this tool") are by name, in-memory, never persisted.
- **Plan grants are keyed on `(tool + exact args)`, not the tool name.** The first cut
  keyed on names, which meant approving `delete_file` for the reviewed plan silently
  approved a *different* `delete_file` the Tier-1 loop proposed later — a step the Master
  never saw. Signature-keying also makes a leftover grant harmless.
- Verified: 2 risky steps → exactly 1 prompt (was 2); declining → 0 tools executed;
  read-only plan → 0 prompts; deny-listed step → aborts without asking.

Known gaps: no per-argument risk rules for `execute_shell_command` (the destructive-content
scan still only covers `write_file`/`execute_code`), and `main_api.py` auto-approves when
no WebSocket is attached (pre-existing, unlike the CLI which blocks).

### 2026-07-26 — Tier-2 task state (interrupted work stops vanishing)

`core/task_state.py`. Until now the only cross-turn state was one slot holding one
pending confirmation: a job killed by a crash, a restart or a closed terminal left no
trace, and "đang làm gì?" had to be guessed by the model (the Router sees no
`chat_history`, and the phrase is shorter than RAG's `MIN_QUERY_LENGTH`).

- `TaskRecord` (goal · status · steps · note) persisted to `ciel_data/state/tasks.json`,
  rolling 20. Opened only for tool/multi_tool/code — pure chat can leave nothing undone.
- **Resumption:** a record still `active` at load time can only come from a dead process
  (the constructor runs at start-up), so it becomes `interrupted` and `main.py` reports it.
- **A staged confirmation closes the task as `blocked`, not `done`** — waiting on the
  Master is not completion, and it stays visible.
- `đang làm gì` / `status` / `what were you doing` answered from the store **before**
  routing: measured 0 Brain calls, 0 tokens, 0.1s.
- The Brain never reads these records — that would re-open the cross-request
  contamination removed in July 2026 and cost tokens every turn.
- Reuses the loop's existing `StepRecord` data instead of keeping a second list that
  could drift out of sync.

Verified live on the swapped TEM model stack (4/4), including **killing a real Ciel
process mid-request** and having a fresh core report it, rather than simulating the crash
by editing the file. Thread-safe under the new parallel executor: 6 threads × 25 steps →
150/150 recorded, no duplicate ordinals.

### 2026-07-26 — Parallel tool execution, and a path-corruption bug it uncovered

**Parallel execution** (`core/parallel.py`): provably-independent steps in one plan now
run concurrently. Opt-in per tool (`parallel_safe` in a skill's factory, or
`_DEFAULT_PARALLEL_SAFE`), because auto-registration means a blacklist would silently
parallelise a new mutating tool. `_log_thought` now holds a lock — it both appends to the
shared log and accumulates the token/cost dicts, so unsynchronised callers corrupted the
audit format and dropped counter increments (verified with an 8-thread × 40-write stress).

Measured on tool time alone: **9.3× / 1.9× / 2.3× / 1.05×** across file-read, mixed-fast,
web-scrape and search scenarios. Honest caveat: that is 0.15–2.8s inside requests taking
15–55s, so **ordinary requests show no wall-clock change** — the payoff is slow-network
fan-out (an earlier run spent 566s on five sequential scrapes).

**The bug it uncovered — nothing to do with parallelism, and pre-existing.** An A/B
(parallel on vs off) was run to check for regressions; one case failed *both* ways. The
Brain-translate step is instructed to "remove any potentially sensitive phrases", and the
Worker duly treated a Windows username as sensitive:

```
typed:    C:\Users\khang\AppData\Local\Temp\ciel_c_repo_x1
planned:  C:\Users\[user]\AppData\Local\Temp\ciel_c_repo_x1
```

The path no longer existed, so `git_status` answered "not a Git repository" — surfacing as
a baffling *tool* error whose real cause was a paraphrase two steps earlier. Any request
naming a path under `C:\Users\<name>\…` was affected, and the step is live whenever
`BRAIN_PROVIDER=vilao`.

Fixed the project way — **instruct, then verify**: the Worker is now told to reproduce
paths/URLs/emails verbatim, AND `_lost_literals()` checks afterwards that each one
survived, discarding the whole translation if not. The instruction alone would have been
one more guard trusting a model to comply. Note for honesty: in the confirming run the
verification never had to fire — the instruction sufficed that time. The guard exists for
the times it does not.

### 2026-07-26 — Tier-1 agent loop (observe → re-plan → act)
A plan is a **flat list of tool calls fixed before anything runs**, so *"check git status, and
if it's clean, commit"* was **structurally unrepresentable** — not merely hard to plan. Adding
tools could never fix that; the pipeline shape had to change.

- **`core/continuation.py`** — a pure, LLM-free policy module. `ContinuationPolicy.assess()`
  returns whether another round is warranted; `LoopBudget` holds every ceiling. Deliberately
  does **not** ask the model "are we done?" — that would fire on the ~90% of requests that are
  plainly one-shot, and would make loop safety depend on model quality.
- **Five signals** open a round: S1 conditional wording · S2 an unresolved `{step_N}` reached a
  real tool · S3 a step failed while later steps ran · S4 fan-out over a set of unknown size ·
  S5 the optional `"needs_followup"` hint. Only S5 involves the model, and it is never required.
- **Re-planning reuses `Router.route()` and the ordinary plan schema** — no second format for a
  weaker model to fail at.
- **Verified live on a swapped model stack** (all four tiers on a different provider): the
  conditional branch commits when the repo is dirty and correctly does **nothing** when clean —
  and in one run the loop *recovered from the Brain routing to the wrong repo* in round 0.
- **Cost on ordinary requests: zero extra calls.** An 11-case regression spent the same 8 Brain
  calls with the loop on as off.

Three defects the hard multi-domain suite exposed, all fixed:
1. **Fan-out was invisible** — "liệt kê file rồi đọc *từng* file" ran the listing and stopped.
   No signal covered *unknown cardinality*; S4 added.
2. **False positive on sequencing** — `kiểm tra X rồi Y` matched the conditional regex, so
   "check git then summarise" burned a planner call. Now requires a decision verb after `rồi`.
3. **The wall-clock ceiling did nothing** — it was tested only *between* rounds, so one round of
   five slow `smart_scrape` calls ran **566s** past a 120s cap. Now checked before every step,
   plus `max_steps_per_round=4`.

Contract for new skills is in [`instructionAI/conventions.md`](instructionAI/conventions.md)
("Skill Contract"). The one that bites: **a tool can now be invoked more than once per request** —
make it idempotent, or split preview/confirm.

### 2026-07-25 — Web search overhaul (answers that state *when* and *what*)
Symptom: news answers had no dates and stayed vague ("World Bank published an outlook
focusing on GDP, inflation and risks"). Two independent causes, both fixed.

- **The tool never supplied dates.** `stealth_search` used `ddgs.text()` — fields are
  `title/href/body` only, and for news queries it returned section landing pages. Any date
  in an answer had been scraped out of snippet prose, or invented.
  → **Google News RSS is now the primary news source** (free, no API key, no new library —
  `requests` + stdlib `xml.etree`): real `pubDate`, real outlet, and `hl`/`gl`/`ceid`
  language-region targeting. `ddgs.news()` then `ddgs.text()` remain as fallbacks because
  the RSS endpoint is unofficial.
- **Generic "what's the news" now uses the TOP STORIES feed**, not keyword search: searching
  "top news headlines today" matched articles *titled* that (roundups like "School Assembly
  News Headlines"), while the feed returns the actual lead stories. `_is_generic_news_query()`
  routes between them.
- **Recency is enforced in code** on the real `pubDate` — DuckDuckGo's own `timelimit` was
  demonstrably unreliable (a `'d'` window returned 8–16-day-old hits, previously invisible
  because no dates were shown).
- **Landing pages are dropped** (generic-blurb hints + "title starts with the outlet name",
  which is language-agnostic). Filtering happens BEFORE trimming to `max_results`.
- **Everything is answered in the user's language context.** `[USER LANGUAGE: X]` is appended
  AFTER the translate-to-English step so it survives it; the Router builds the query in that
  language for local topics (English/international topics stay English) and is barred from
  putting a literal date string in a query (use `timelimit`).
- **The formatter stopped throwing detail away.** Retrieval tools (`stealth_search`,
  `smart_scrape`, `read_document`) now get a COMPLETENESS rule — cover every item, keep dates/
  numbers/names/sources, never guess a date, omit content-free items — while every other tool
  keeps the terse formatting (generation time scales with output length). Search results also
  get an 8,000-char budget instead of the generic 2,000, which had been silently truncating
  later hits.
- Verified end-to-end: a Vietnamese request returns Vietnamese lead stories (typhoon Noul at
  force 11–12, Hanoi flooding) and an English one returns NYT/WaPo/CNBC/AP — each item dated
  and attributed. Notably the Worker also flagged a metadata/headline date mismatch on its own
  rather than repeating it.

### 2026-07-24 → 07-25 — Reliability pass (multi_tool, RAG recall, provider) + docs
Root cause across most issues this session: the **Brain layer** is the least reliable tier; the
deterministic scaffolding around it is what makes the system dependable.

- **Synthesis-placeholder guard generalized (B1) + symmetric file guard (B2).** A stronger Brain
  (Opus) paraphrased the canonical `[…_TO_BE_SYNTHESIZED]` marker (e.g.
  `[SYNTHESIZE_FROM_RESULTS: …]`), so the exact-string deferral missed it and a hollow shell reached
  disk. `_has_unsynthesized_placeholder()` now matches any such marker; the write path got the same
  hard-block the email path already had. **A/B-proven** (same model, fix off → hollow file returns).
- **Inspection-tool memory fallback (A2).** The amnesia recall test failed because the Brain
  (reasonably) verified a memory question against the workspace (`list_workspace`) and, finding
  nothing, returned the bare listing. Now falls back to recalled RAG context, labeled unverified.
  **A/B-proven** (fix off → recall returns the listing, no answer).
- **Multi_tool: decomposition guidance (A3) + dependent steps (B3).** Router prompt now teaches
  per-step decomposition and `{{prev}}`/`{{step_N}}` references; `_resolve_step_refs()` substitutes
  an earlier step's raw output into a later step's args (verbatim; sub-part extraction is future work).
- **Subject enforcement.** `subject exactly '...'` is now honored deterministically on both the
  single-send and multi_tool paths (the Brain frequently swaps in its own subject).
- **Tolerant router JSON (A5).** `_extract_json_object()` tolerates prose some models wrap around
  the decision object, instead of failing the whole ~14s Brain call on a `JSONDecodeError`.
- **Worker gained a `vilao` provider branch.** Previously `WORKER_PROVIDER=vilao` fell through to the
  Ollama-localhost branch and failed connection-refused; now mirrors the Brain's vilao wiring.
- **Test integrity (E3).** `check_file_exists` now rejects a file whose content is still an
  unsynthesized placeholder (it used to false-pass a hollow shell); integration code-gen accepts the
  `code` *or* `tool`/`write_file` route as long as the file on disk is correct.
- **`.env` cleaned to pure-Vilao** (removed dead `LOCAL_MODEL`/Ollama/`CODER_PROVIDER`/`WORKER_MODEL`).
- **Known-open (not yet fixed):** provider empty-response crashes the pipeline instead of degrading;
  Brain arg-extraction can put request text into a path arg; a natural-language placeholder
  (`[thời điểm gửi email]`) can slip into a single-send body (self-correction catches it, post-send).
  See the **Reliability roadmap** below.

### 2026-07-18 — Test isolation
`reset_session()` clears in-memory `chat_history` before each `process()`-touching test (only 3
integration tests + all 16 hard_special cases actually shared state). Additive, zero regressions.

### 2026-07-11 — Voice I/O + JARVIS orb + provider fallback
- CLI voice seam: `core/voice_input.py` (STT) + `core/speech_output.py` (TTS, deterministic
  `to_speech()` normalizer — voice never dumbs down the prompts). Default STT moved to local
  `faster-whisper` (offline, no reliance on a free cloud endpoint); default TTS `edge-tts` with a
  backoff retry for its intermittent `NoAudioReceived`.
- UI: audio-reactive Three.js particle **orb** ported *into* the React/Tauri app (kept SkillGrid /
  VitalsBar / ConfirmDialog); `POST /tts` reuses the CLI engine so the browser gets identical voice.
- Brain fallback provider (manual, one-line `.env`) documented as plan-B for the single-provider risk.

### 2026-07-09 → 07-10 — Cost/usage tracking + healing skip-list + UI foundation
Token-precise `[LLM_CALL] model=<id> in=<n> out=<n> total=<n>` per tier at one `_log_thought`
chokepoint; live vitals + `scripts/cost_report.py`; pricing in `core/cost.py` (override via
`ciel_data/model_pricing.json`). `_HEALING_SKIP_PATTERNS` stops burning Worker calls on unfixable
errors. `GET /skills`/`/health`; dynamic skill manifest (add a skill → UI reflects it, no frontend edits).

### 2026-07-05 → 07-06 — Third tier + hardening
Middleware tier (email-scoped, fail-open finalizer); explicit `LLM_REQUEST_TIMEOUT` on all clients
(a stalled provider used to hang forever); dangerous-code gate; routing-intent + cross-request
contamination fixes; `test_hard_special.py` rewritten to assert real disk/tool truth + BUG DASHBOARD;
`scripts/prompt_harness.py` mines the log for recurring failure patterns.

*(Full pre-2026-07 detail: `architect.md` Changelog.)*

---

## Reliability Roadmap (next — "make the Brain layer dependable")

Three pillars, most in Phase 1 are deterministic and low-risk:

1. **Brain reliability** — plan validator + arg-repair (catch a request phrase used as a path,
   bad recipient, malformed symbol), richer tool hints (drop the 80-char description truncation),
   more compliance safeguards.
2. **Provider fallback** — a resilient client (primary → empty/4xx/timeout → fallback → graceful
   error, never a crash) + a boot health-check per tier.
3. **Latency** — Brain is ~14 s/route and ~78% of wall-time; drop persona from the routing prompt,
   skip the translate pre-call and skip routing for trivially-classifiable turns, consider a faster
   routing model.

---

## Recommended Email Templates

When Ciel prepares an email (`send_gmail_message` / `send_gmail_html_message` / `reply_to_email`):
**pick the right template, gather real data from tools FIRST, fill every placeholder with live facts
only, sign as "Ciel."** Never leave a `[...]` placeholder, never invent data, never expose internal
paths. Three data-first send flows are active and follow these rules:

- **Market / Asset report** — the primary, backtested flow (see template 1 below).
- **Research / news** — any "tổng hợp / tin tức … + gửi": run `stealth_search` FIRST
  (recency-windowed, current year), then write the body from real findings; if search fails, say so
  honestly — never send a hollow shell.
- **Document** — "đọc file X.pdf/.docx … rồi gửi": run `read_document` FIRST, summarize the actual
  content; if the file is unreadable, report that instead of sending an empty report.

### 1. Market / Asset Report *(primary — validated live for XAU/BTC + risk + send)*

Use for any request with XAU/USD / BTC / gold / crypto + evaluation/risk + send. Fill every `[ ]`
with real numbers from the current tool results; if a tool fails, say so instead of inventing.

```
Subject: Báo cáo thị trường XAUUSD & BTC – Đánh giá rủi ro [ngày]

Kính gửi anh/chị,

1. Tổng quan thị trường
   - XAU/USD: [giá thực từ tool] ([thay đổi %])
   - BTC/USD: [giá thực từ tool] ([thay đổi % 24h])
   - Các chỉ số khác nếu có (volume, dominance...).

2. Phân tích kỹ thuật & xu hướng
   - Xu hướng chính cho XAUUSD / BTC.
   - Các mức hỗ trợ/kháng cự và chỉ báo (nếu có từ tool).

3. Đánh giá rủi ro
   - Mức rủi ro tổng thể (thấp/trung bình/cao).
   - Yếu tố chính (tin tức, macro...) và khuyến nghị hành động.

4. Kết luận
   Tóm tắt ngắn và lời khuyên theo dõi.

Trân trọng,
Ciel
```

### 1b. Market Dashboard (rich HTML — mirrors `email_template/Report.pdf` layout)

For a *styled/visual* report: gather live data (`get_market_price`, `get_crypto_stats`,
`analyze_crypto_technical`) → `build_market_report_html(...)` (fills the dashboard with real values,
`N/A` when missing: 4 KPI tiles = BTC/XAU price + 24h change%, table = per-asset RSI/trend, blocks =
risk level/factors, conclusion) → `send_gmail_html_message(to, subject, html_body)`.
`Report.pdf` is a *visual layout reference only*, not literal content. Use the plain template 1 when
plain text is requested.

### 2–6. Other flows *(structure to fill as needed)*

- **2. Todo / Productivity Summary** — todos added/completed, current status, follow-ups.
- **3. General Task / Status Report** — what was done, results, any issues, next steps.
- **4. Alert / Digest** — scheduled/urgent sends; shorter, clear action items.
- **5. Gmail reply / summary** — reply respecting the thread (`Re: …`) or summarize searched Gmail.
- **6. Custom** — fallback; structure flexes to the request.
