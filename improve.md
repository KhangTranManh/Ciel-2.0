# Ciel 2.0 — Improve Roadmap (Personal Agent)

> **For any AI assistant starting cold:**  
> 1. Read `instructionAI/SKILL.md` first (rules + architecture index).  
> 2. Read this file next (what to upgrade, how to test, when PASS).  
> 3. Only then open code under `core/`, `skills/`, `backtest/`.  
> You do **not** need prior chat history if those two sources are current.

| | |
|--|--|
| **Project root** | This repo (`Ciel-2.0`) |
| **Stable AI knowledge** | `instructionAI/` (start at `SKILL.md`) |
| **This file** | Living upgrade checklist + pass bars (update when you finish a step) |
| **Scope** | Personal agent — **not** Claude Code / IDE coding-product clone |
| **How to work** | One checklist item at a time → implement → test → tick `[x]` → log row |

---

## Hand-off: what every AI must understand

### What Ciel is
- Personal multi-tool agent: files, shell, Gmail, Telegram, trading, web, optional vision, and remote API clients.
- Pipeline: **Brain (route/plan/evaluate) → Worker (compose/format)** + `ToolManager`
  skills. Optional Middleware code is retained but disabled; the maintained deployment
  uses two model identities.
- **Seven capability tiers** (loop, task state, permissions, context, cancel, proactive, user model): decisions in **deterministic Python**; LLM only plans/composes. See `instructionAI/architecture.md`.
- Entry: `main.py` (CLI), `main_api.py` (API/WebSocket), `main_telegram.py` — all via `AgentLoop` / `CielCore`.

### Non-negotiables (detail in `instructionAI/SKILL.md`)
- Do not break `thoughts.log` format.
- Keep `SAFETY_OPEN` ≠ `DISABLE_SAFETY_GATE`.
- Data **before** email body; claim “sent” only with Message Id.
- Prefer code safeguards over “trust the model”.
- Worker model env key is **`CODER_MODEL`**, not `WORKER_MODEL`.

### What “done” means for upgrades
Use levels **A / B / C** below. Default target: **Level B (Core Green)**.  
Do not mark a P0 item done without running the tests listed under it.

### Test commands (from project root)

```text
python -m backtest.run_all --unit-only
python -m backtest.run_all --skip-exploratory
python -m backtest.run_all
```

Reports: `backtest/logs/run_all_*.txt`  
Audit trail: `ciel_data/logs/thoughts.log`

### If the user only @-mentions `instructionAI`
Open at least:
1. `instructionAI/SKILL.md` (this hand-off pointer + rules)
2. `../improve.md` (this file — roadmap)
3. Relevant topic file from the SKILL table (`architecture.md`, `conventions.md`, `safety_and_risk.md`, …)

---

## How to use this roadmap

1. Pick **one** open item (P0 first).  
2. Change code/prompts/config in the repo.  
3. Run the **Test** commands for that item.  
4. Tick `[ ]` → `[x]` only if **Pass when** is true.  
5. Add a row under **Nhật ký**.  
6. Do not open five P0 items in parallel.

---

## Agent PASS levels (definition of “ổn”)

### Level A — Daily OK
- [x] `main.py` boots; no mass `JSONDecodeError` on routing  
- [x] ~10 manual commands OK: chat VI/EN, todo, calculate, list/read file, weather/search  
- [x] `python -m backtest.run_all --unit-only` → **unit suites PASS** (now includes `quality_guards`)  
- [x] Unattended: no silent high-risk send/delete (DEFER, not auto-consent)  
- **Verified:** 2026-08-06 live smoke (boot, 10 cmds, unit, DEFER). Ongoing: `run_all --unit-only`  
- Pass date: **2026-08-06**  Provider/model baseline: recorded privately per deployment.

### Level B — Core Green (near-term goal)
- [x] Level A  
- [x] `python -m backtest.run_all --skip-exploratory` → **all unit + live suites PASS**  
- [x] ≥3 real email checks (test address): Message Id; no internal paths; no placeholders / invented numbers when tools fail  
- [x] Follow-up context works (“tại sao lại thế” remembers prior turn)  
- **Evidence (2026-08-06 check):** A smoke 7/7; run_all 9/9 (2026-08-04, P0.1); email sends with Message Id, double-send protection, and referential recipient coverage; follow-up + file đó + scope veto P0.4.
- Pass date: **2026-08-06**

### Level C — Portable / hybrid-local ready
- [x] Level B (2026-08-06)  
- [ ] Minimal `.env` documented (providers, models, keys, base URLs)  
- [ ] New machine: clone + env + unit `run_all` PASS + 5 live smokes  
- [ ] If local model: ≥20/20 Brain route JSON parse + live suites not mass-red  
- [ ] Full local 3-role not required if hybrid is solid

**Current local-model evidence:** a Qwen3 8B Ollama trial was acceptable for basic
chat but did not reliably follow the tool-routing contract. It is not the default Brain
or Worker baseline. Keep the hybrid provider stack until a candidate meets the route
JSON, tool-use, and live-suite gates above; never declare Level C from a chat-only test.

**Near-term goal: Level B.**

---

## P0 — Foundation (do first)

### P0.1 — `run_all` full green (unit + live, skip exploratory)
- [x] **Do:** Fix remaining failures; do not delete tests to fake green.  
- [x] **Test:** `run_all --unit-only` then `run_all --skip-exploratory`  
- [x] **Pass when:**  
  - Unit 5/5: context, user_model, proactive, outbound, conversation_bugs  
  - Live all PASS: integration, hard_special, brain_worker, rag_memory  
- [x] **Known baseline (2026-08-03):** hard_special 15/16 (Special 7) + rag partial — **fixed; re-run 2026-08-04 full green**  
- Pass date: **2026-08-04**  Provider/model baseline: recorded privately per deployment.

### P0.2 — Brain JSON / routing stable
- [x] **Do:** Brain model in `.env` must emit parseable router JSON reliably.  
- [x] **Test:** 20 smokes (greet, single tool, multi-tool, VI, EN, vague) + 5 consecutive AgentLoop turns  
- [x] **Pass when:** ≥20/20 `ROUTE_DECISION` parse OK; 5/5 consecutive simple chat + tool  
- **Verified:** 2026-08-06 live route smoke 20/20 + 5 consec. Ongoing: integration / hard_special  
- Pass date: **2026-08-06**  Brain model: recorded privately per deployment.

### P0.3 — Professional email by default
- [x] **Do:** Tools first → clean body → **one** send; no forced HTML dashboard unless user asks visual.  
- [x] **Test:** `test_outbound`; hard_special market+email; 2–3 real mails to test inbox  
- [x] **Pass when:** Message Id; no `agent_output/`/`ciel_workspace/` in body; no `[]` / invented prices; no double-send; referential “gửi mail đó” correct recipient  
- **Close (2026-08-06):** `test_outbound` 36/36; digest + referential-recipient coverage; Gmail multi-id fix; live mail verification to an operator-controlled test inbox.
- **HTML analysis:** `build_analysis_report_html` + `send_telegram_document` (see report_ops / telegram_ops)  
- Pass date: **2026-08-06**

### P0.4 — Conversation context (recent turns + open thread)
- [x] **Do:** History read-back; scope veto (“dừng/chỉ…thôi”); open thread for slot-fill / “file đó”.  
- [x] **Test:** `test_conversation_bugs` + 3 live cases  
- [x] **Pass when:** suite full PASS; live cases do not regress known transcript bugs  
- **Unit:** `test_conversation_bugs` 66/66 (2026-08-06)  
- **Live verified:** follow-up / file đó / scope veto (2026-08-06)  
- Pass date: **2026-08-06**

**P0 complete ≈ Level B** (plus manual email quality if not fully covered by hard_special).

---

## P1 — Personal-agent quality

### P1.1 — Daily tools (market / search / file / todo) — **DONE**

**Goal:** Khi tool fail một phần hoặc trả lỗi, Ciel **nói thật** và **không bịa** số/file/kết quả.

**In scope (5 turns tối thiểu):**
1. Market: tool lỗi BTC → reply “chưa lấy được / tool lỗi”, **không** số giá bịa.  
2. Search: empty/thin results → nói thiếu dữ liệu, không invent tin.  
3. File: `read_file` path không tồn tại → “không có file”, không giả nội dung.  
4. Todo: add/list khớp tool result (id/text thật).  
5. Mixed: multi_tool một nhánh fail (vd BTC fail, XAU OK) → báo đúng nhánh fail + số nhánh OK.

**Out of scope:** redesign tool API; proactive digest (P1.2); middleware path-leak (P1.4).

**Do (code if needed):**
- Worker/format rules already have anti-hallucination; tighten only if live fails.  
- Prefer Python: surface tool error strings; never invent price when result contains Error/N/A.

**Test:** live fail cases as needed; guards in `backtest.test_quality_guards` + unit `run_all`.  

**Pass when:** replies consistent with tool output (including honest failure).  
- **Verified:** 2026-08-06 (8/8 honest-tool cases).  
- Pass date: **2026-08-06**

---

### P1.2 — Proactive (useful, not noisy) — **DONE**
- [x] Digest/alerts/stale todos: cooldown, budget, safe unattended.  
- [x] **Test:** `test_proactive`  
- [x] **Pass when:** suite PASS + one real day without spam.  
- **Verified:** `test_proactive` 149/149 (2026-10-02). VPS `thoughts.log` 2026-09-06 →
  2026-10-02: `weekly_plan` delivered once each Monday (09-07, 09-14, 09-21, 09-28),
  two `reminder_due` deliveries (09-05), no duplicate sends and no per-poll cooldown
  noise. (The per-poll `TRIGGER FIRED … suppressed` spam seen 08-26 → 09-05 stopped with
  the quiet-cooldown change.)
- Pass date: **2026-10-02**

**Planner foundation (implemented 2026-08-26):** monthly goals and weekly actions use
separate SQLite tables and separate auto-discovered tool packs; immediate todos remain
in JSON. Opt-in `monthly_plan` and `weekly_plan` triggers catch up after downtime and
use the persistent Notifier key/cooldown path. `backtest.test_planner` is 32/32. This
does not close P1.2 by itself: the existing pass bar still requires a real day without
spam.

### P1.3 — User model — **DONE**
- [x] Preferences only; never inject secrets into `user_model`.  
- [x] **Test:** `test_user_model`  
- [x] **Pass when:** suite PASS.  
- **Verified:** `test_user_model` 108/108 in `run_all --unit-only` (2026-10-02).
- Pass date: **2026-10-02**

### P1.4 — Middleware — **DONE**

**Goal:** Middleware bảo vệ **outbound** (email/body) và **không** phá dữ liệu tool thật.

**In scope:**
1. **Path leak block:** body/reply không chứa `agent_output/` / `ciel_workspace/` / absolute internal paths khi gửi mail / báo cáo ra ngoài.  
2. **No false-flag live numbers:** BTC/XAU/weather từ tool **không** bị Middleware rewrite/reject vì “số lạ so với training”.  
3. **Internal contradiction:** claim “đã gửi” khi không có Message Id → strip/flag (đã có stale-send status; giữ/mở rộng).  
4. **Placeholder block:** `[PROFESSIONAL…]`, `[Worker:…]`, empty `[]` prices không ra body gửi.

**Out of scope:** full rewrite of Middleware model; vision; local-model-only stack (P3).

**Do:**
- Audit `MIDDLEWARE_SCOPE` + email revision path in `llm_connector`.  
- Smoke: (a) force body with `ciel_workspace/foo` → blocked or sanitized; (b) real BTC from tool → not flagged as implausible.

**Test:** `test_outbound` + `test_quality_guards` (sanitize / path strip).  
**Pass when:** path leak blocked; real tool numbers not false-flagged; no invented send claim.  
- **Verified:** 2026-08-06 (10/10 middleware/sanitize cases).  
- Pass date: **2026-08-06**

### P1.5 — Deterministic multi-tool plan validation — **DONE**

**Goal:** The Brain may propose a plan, but Python decides whether it is executable
before any tool runs.

**Scope:** `core/plan_validation.py` validates loaded tool names, object-shaped and
schema-valid arguments, prior-only `{prev}` / `{step_N}` references, and duplicate
outbound deliveries. It removes a duplicate only when no later reference would be
renumbered; otherwise it rejects the whole plan. Initial and continuation plans are both
validated, then pass through the existing plan-level permission review.

**Test:** `backtest.test_plan_validation` + `python -m backtest.run_all --unit-only`.

**Pass when:** Invalid plans execute zero steps; dependency-safe duplicate deliveries
collapse once; continuation plans cannot bypass validation or permission review.
- **Verified:** 2026-08-08 (plan_validation 10/10; unit 7/7 PASS; live search →
  synthesize → one Gmail delivery returned a real Message Id).
- Pass date: **2026-08-08**

---

### Feature — Daily Command Center

#### Phase 1 — On-demand local Daily Brief — **DONE**

`get_daily_brief` derives one prioritized, bounded read-only view from today's weekly
tasks, open todos, reminders due within the configured look-ahead, active monthly-goal
labels, and only waiting/interrupted run metadata. Python owns ranking and bounds;
Worker performs one grounded presentation. It does not persist a second task store,
inspect run arguments/artifacts, mutate work, or send proactively.

**Pass when:** deterministic priority and date/timezone filtering pass; one unavailable
source does not erase other local data; private run evidence stays excluded; runtime
uses one read plus one Worker presentation; unified unit regression is green.

- [x] Collector and `get_daily_brief` tool
- [x] Read-only permission/lane/parallel registration
- [x] Bounded structured output and source isolation
- [x] Focused collector and runtime replay coverage
- Pass date: **2026-10-31**

#### Phase 2 — Actionable Daily Brief references — **DONE**

- [x] Persist a bounded same-day `D1...Dn` mapping under `ciel_data/state/`.
- [x] Resolve completion, reminder creation, reschedule, and explicit run reissue
      through existing permission and tool boundaries; never auto-replay consent.
- [x] Expire references by date/TTL and reject stale or ambiguous numbers.

The snapshot contains only reference, source type/ID, and bounded title. Python binds
model-selected todo/weekly/reminder tools back to that verified identity before normal
plan validation and permissions. An explicit run reissue creates a fresh run from the
public goal and links IDs with `approval_inherited=false`; it never reads or copies old
step arguments, artifacts, pending confirmation, or consent.

**Pass when:** identity-only persistence and non-today clearing pass; stale, unknown,
and multi-reference requests stop before routing; guessed IDs/titles are overwritten by
the verified mapping; mismatched source tools execute nothing; explicit run reissue is
fresh and consent-free; focused and unified unit regression are green.

- Pass date: **2026-10-31**

#### Phase 3 — Proactive morning Telegram delivery — **DONE**

- [x] Add opt-in `daily_brief` trigger, configured local time, stable daily key,
      catch-up, provider retry, and successful-delivery dedupe.
- [x] Empty briefs remain silent; polling suppression does not flood the audit log.

The trigger renders the bounded local brief deterministically, writes the same
identity-only D-reference snapshot before delivery, and routes through the shared
Notifier/Telegram stack. Failed provider sends remain retryable; a later success removes
the temporary digest copy and persists the daily cooldown key.

**Pass when:** before-time and empty checks are silent; late startup catches up; a failed
provider attempt retries; success survives restart without duplicate delivery or audit
noise; focused and unified unit regression are green.

- Pass date: **2026-10-31**

#### Phase 4 — Optional external context — **DONE**

- [x] Add opt-in bounded Gmail metadata and weather without letting either failure
      suppress the local brief.
- [x] Treat Calendar as a separate tool pack; reads may enrich the brief while
      create/update/delete retain normal permission checks.

Gmail enrichment keeps only bounded From/Subject/Date metadata; weather parses one
current-condition record into bounded numeric fields. Calendar has a separate OAuth
token/tool pack: Daily Brief reads keep only event title/time, while create/update/delete
are high-risk attended actions and become DEFER when unattended. Every external source
is isolated and may fail without removing local work or the other external sources.

**Pass when:** all external fields are bounded; email bodies/snippets and Calendar
descriptions/attendees never enter the brief; source failures are independent and
fail-open; D-reference persistence remains identity-only; Calendar reads are AUTO while
mutations are ASK/DEFER; focused and unified unit regression are green.

- Pass date: **2026-10-31**

---

### Feature — Ciel Native Calendar — **PHASE 1 DONE**

Phase 1 implements native one-off event storage and bounded read tools. No create,
update, cancel, Daily Brief integration, trigger, or alert delivery is exposed yet.
Later phases do not begin before the preceding phase's pass criteria hold.

#### Goal and boundaries

Ciel owns a private calendar without requiring Google Calendar or another provider.
The native calendar is a schedule, not another task list:

- a weekly task is work to complete;
- a todo is an immediate unscheduled checklist item;
- a reminder is one notification at one deadline;
- a calendar event reserves a time interval and may own zero or more alert offsets.

Native events live in `ciel_data/planner.db`, with `core/planner_store.py` remaining the
only SQL boundary. They do not copy into todos or reminders. Daily Brief may derive a
bounded view from them, while `ciel_data/state/daily_brief.json` remains identity-only
and never becomes event storage. Google/Outlook/CalDAV synchronization is not required
for the native feature and belongs behind a later provider adapter.

#### Storage contract

The initial schema adds two owned tables:

```text
calendar_events
  id, title, start_at_utc, end_at_utc, timezone, all_day,
  start_date, end_date_exclusive, status, location, notes,
  schedule_version, created_at, updated_at

calendar_event_alerts
  id, event_id, offset_minutes, delivered_at, created_at, updated_at
```

Timed events use absolute UTC start/end plus an IANA display timezone. All-day events
use local dates with an exclusive end date and do not invent midnight UTC instants.
Exactly one timed/all-day representation is valid. End must be after start, titles and
optional text are bounded, alert offsets are non-negative and bounded, and equivalent
retries are idempotent. Cancellation retains history instead of deleting rows.

`schedule_version` changes only when start/end, all-day dates, timezone, status, or
alert offsets change. Cosmetic edits such as title/location/notes do not increment it,
so they cannot cause an already delivered alert to be sent again.

#### Phase 1 — Native storage and read-only tools

- [x] Add atomic schema migration and `PlannerStore` event/alert read methods.
- [x] Add `list_native_calendar_events` and `get_native_calendar_event`; the distinct
      names prevent collision with the optional Google `list_calendar_events` tool.
- [x] Support one-off timed and all-day events; recurrence is explicitly excluded.
- [x] Keep list output bounded and exclude notes/location unless one event is
      explicitly read.

**Pass when:** migration is repeatable; invalid/mixed timed and all-day rows execute no
write; UTC/IANA normalization covers DST boundaries; range queries include overlapping
events correctly; equivalent retries do not duplicate rows; existing planner/reminder
tests and unified unit regression stay green.

- Focused pass: **8/8** (`backtest.test_native_calendar`), plus planner 32/32,
  reminders 23/23, Daily Brief 20/20, plan validation 12/12, operational readiness
  29/29, and quality guards 67/67.
- Unified unit regression: **18/18 suites PASS**.
- Pass date: **2026-11-01**

#### Phase 2 — Mutations and Daily Brief integration

- [ ] Add create, update, and cancel tools through ordinary plan validation and
      permission review; native reads are `AUTO`, mutations are `ASK` attended and
      `DEFER` unattended.
- [ ] Add bounded native event title/time to Daily Brief without exposing notes or
      location by default and without suppressing other sources on failure.
- [ ] Extend same-day D-references with `native_calendar_event` identity only; update or
      cancel binds back to the verified event ID and never trusts a model-guessed ID.

**Pass when:** create/update/cancel are atomic and idempotent; permission denial changes
nothing; stale or mismatched D-references execute nothing; event notes never enter the
Brief snapshot; one calendar failure does not erase tasks, todos, reminders, runs, or
other external context; focused and unified regression are green.

#### Phase 3 — Event alerts through the existing Notifier

- [ ] Add opt-in `calendar_event_due` scheduler trigger with configurable bounded
      catch-up window and one or more offsets per event.
- [ ] Use notification identity
      `calendar_event:<event_id>:<schedule_version>:<offset_minutes>`.
- [ ] Mark an alert delivered only after provider success or persisted notifier dedupe;
      failed sends remain retryable and routine polling stays audit-silent.
- [ ] If `calendar_event_due` is disabled, creating an event without alerts remains
      valid, while a request that includes alerts fails atomically and honestly.
- [ ] Reschedule/cancel invalidates old pending identities by source state; it never
      deletes notifier history or replays an alert from an old schedule version.

**Pass when:** before-time checks are silent; each configured offset delivers once;
failure retries; restart does not duplicate; cosmetic edits do not re-alert; time/alert
changes use the new schedule version; cancelled/past-window events stay silent; Docker
startup and a live Telegram delivery smoke pass without a second Ciel process.

#### Phase 4 — Recurrence and interoperability (deferred)

- [ ] Define a bounded recurrence subset only after Phases 1–3 are stable; expansion
      must be range-bounded and exceptions must not rewrite unrelated occurrences.
- [ ] Add ICS export first, then reviewed ICS import with deterministic duplicate keys.
- [ ] Add optional Google, Outlook, or CalDAV adapters without changing the native
      event contract or making an external provider the source of truth by accident.

**Pass when:** recurrence cannot create unbounded rows/work; timezone/DST examples are
deterministic; import retries are idempotent; provider failure leaves native events and
alerts usable; conflict ownership is explicit and covered by tests.

#### Non-goals for the first three phases

- No multi-user/shared calendars, invitation email, attendee management, or free/busy.
- No automatic conversion between tasks, reminders, and events.
- No silent external synchronization or background mutation.
- No graphical calendar requirement; tools, Daily Brief, Telegram, and the existing API
  remain sufficient until the native contracts pass.

---

### Feature — **Inbound file/ảnh (Telegram) → hiểu path → phân tích → (HTML / Telegram)** — **DEFINED**

> Level B không phụ thuộc feature này. Implement theo phase.

#### Phase 0 — Inbound path awareness (testable NOW, no new tools)
**Already in code:** Telegram photo/document → download → `ciel_workspace/telegram_uploads/{ts}_{name}` → inbox note:

```text
[Ảnh|File Master vừa gửi qua Telegram, đã lưu tại: ciel_workspace/telegram_uploads/…]
{caption optional}
```

**Smoke (no real Telegram required):** copy a known file into `telegram_uploads/` with the same naming, inject the **exact** note format into `AgentLoop.run_step`, ask Ciel to read + summarize + (optional) `send_telegram`.

**Pass when:**
1. Model uses `read_file` / `read_document` / `describe_image_file` on that path (not invent path).  
2. Reply contains a unique marker string that exists only in the dropped file.  
3. If asked “gửi tóm tắt qua Telegram”: `send_telegram` runs (or honest fail if token missing).

**Regression:** `test_quality_guards` (write-intent + telegram_uploads write block).

#### Phase 1 — HTML analysis report — **DONE**
- Template `email_template/analysis_report.html` + `build_analysis_report_html(..., output_path=)`.  
- `send_telegram_document` for HTML file delivery.  
- **Regression:** `test_quality_guards` HTML builder; live agent path verified 2026-08-06.

#### User stories
| # | Story |
|---|--------|
| U1 | Master gửi file/ảnh Telegram + caption “tóm tắt / phân tích” → Ciel đọc path vừa lưu, trả tóm tắt (Telegram reply). |
| U2 | Same + “gửi tóm tắt qua Telegram” → `send_telegram` (đã có tool). |
| U3 | Same + “báo cáo HTML mail” → Phase 1 template + `send_gmail_html_message`. |

#### Pipeline
```
Telegram download (existing) OR smoke drop into telegram_uploads/
  → note with path (existing format)
  → read_file | read_document | describe_image_file
  → summarize (Worker, facts only)
  → deliver: chat / send_telegram / (later) HTML email
```

#### Non-goals Phase 0
No new HTML tool yet; no auto-analyze without user ask; unattended auto-send off.

---

## Open findings (from the 2026-09-29 VPS log review)

Unchecked items still need a focused test before they are closed.

- [x] **Parallel Gmail reads** fail with `SSL record layer failure`: `search_gmail` /
  `get_gmail_message` / `get_gmail_thread` are in `core/parallel.py`'s parallel-safe set
  but share one `googleapiclient` HTTP connection, which is not thread-safe. Fixed by
  keeping Gmail reads sequential; `test_plan_validation` covers the batching boundary.
- [x] **Self-healing hides the real error**: network/SSL, `not found`, and allowlist
  errors are retried with rewritten args, and only the last error reaches the Master
  (`git: not found` was reported as "used a full path"). Extend
  `_HEALING_SKIP_PATTERNS`. Fixed with deterministic error classification and original
  bounded error propagation; repairable symbol/format errors remain healable.
- [x] **Failed step feeds the next**: `{prev}` injected `No emails found.` as a Gmail
  message id, then healing invented ids. Stop a dependent step when its source failed or
  was empty (`_resolve_step_refs`). Fixed with `SKIPPED_DEPENDENCY`; focused outbound
  coverage verifies the downstream Gmail reader is never called.
- [x] **Silent capability loss**: the opt-in `capability_loss` trigger snapshots
  ToolManager's secret-free startup states and notifies through the shared persistent
  channel when a pack is `unavailable/error`. Intentional `disabled/not_configured`
  states stay silent; stable module/reason keys prevent restart and poll spam.
- [ ] **Proactive messages for remote clients**: `main_api.py` does not publish notifier output
  over the WebSocket, so a future client cannot receive weekly plans or reminders.
- [x] **API + Telegram on one host**: the one-writer rule forces a choice; run the
  Telegram poller inside the API process before deploying the app against the VPS.
  Implemented through `CIEL_API_TELEGRAM`, covered by `test_api_server`, and deployed
  as the healthy `ciel` container on the VPS on 2026-10-28.
- [ ] **Search quality**: Vietnamese price/news queries return Facebook/TikTok pages.
- [ ] **Cost**: Brain averages ~14.6k input tokens per routing call (~22k tokens per
  request); the tool list is the main contributor.

**2026-11-01 incremental reliability/telemetry close:** Gmail service construction now
prefers `build_gmail_service` with an old-library fallback and closes transient API
transports. `capability_loss` startup alerts are opt-in, classified, and deduplicated.
Provider attempts emit separate `PERF/LLM_LATENCY` metadata without changing the stable
`LLM_CALL` line or retaining exception/prompt/response text. Focused checks: Daily Brief
20/20, operational readiness 29/29, proactive 153/153, quality guards 67/67, run harness
54/54, conversation 92/92. Unified unit regression: **17/17 suites PASS**.

## P2 — Personal coworker (not IDE agent)

### P2.1 — Repo-light (only when Master asks)
- [ ] Read/edit project files + run one test command when asked — no full PR product.  
- [ ] **Pass when:** 3/3 manual tasks stay in scope.  
- Pass date: ________

### P2.2 — Task resume
- [ ] Interrupted work restores from task state.  
- [ ] **Pass when:** resume continues, not restart-from-zero.  
- Pass date: ________

### P2.3 — Permission UX
- [ ] Clear approve; unattended = DEFER.  
- [ ] **Pass when:** no silent send/delete unattended.  
- Pass date: ________

### P2.4 — Log → learning (optional)
- [ ] Parse `thoughts.log` → clean Brain/Worker jsonl.  
- [ ] **Pass when:** repeatable extract; suggest ≥500 clean pairs before serious finetune.  
- Pass date: ________

---

## P3 — Models & machines

### P3.1 — Hybrid models
- [ ] Brain = strong JSON; Worker = good writing/email (may be cheaper/local).  
- [ ] **Pass when:** Level B still holds; JSON smoke 20/20.  
- Pass date: ________  B/W/M: ________

### P3.2 — Full local (only if needed)
- [ ] Local endpoints and/or disable Middleware.  
- [ ] **Pass when:** JSON 20/20 + `run_all --skip-exploratory` not mass-fail + 10 manual OK.  
- [ ] **Fail if:** only raw `test_api.py` chat works but router JSON fails.  
- Pass date: ________

### P3.3 — Machine move
- [ ] Clone, venv, `.env`, Gmail creds if needed, unit run_all, live smoke.  
- [ ] **Pass when:** Level A on new machine in one setup session.  
- Pass date: ________

---

## Do not do (unless product goal changes)

- [ ] Turn Ciel into Claude Code / OpenHands clone  
- [ ] Multi-user / billing / marketplace  
- [ ] Full vision autonomy before P0 is done  
- [ ] Large local finetune while tests red and data thin  
- [ ] Flood of new tools while core still failing  

---

## Daily OK checklist (print)

```
[x] unit run_all 12/12
[x] live run_all --skip-exploratory full green
[x] 20/20 Brain JSON parse
[x] 3 emails: Message Id, no path, no placeholder, no double-send
[x] follow-up context OK
[x] "dừng lại" / only-list OK
[x] unattended no silent send/delete
[x] models recorded in journal
```

All checked → **Level B** → personal agent trustworthy for daily use.  
**Reached: 2026-08-06** (Level A smoke + prior P0.1–P0.4 evidence).

---

## Upgrade journal

| Date | Item | Models (B/W/M) | Tests | Result | Notes |
|------|------|----------------|-------|--------|-------|
| 2026-08-03 | Baseline | provider-configured | unit 5/5; live 2/4 | Unit OK; hard 15/16; rag partial | improve.md + run_all created |
| 2026-08-03 | Handoff | — | — | — | Moved into repo; linked from instructionAI/SKILL.md for AI continuity |
| 2026-08-03 | P0.1 start | — | hard Special 7 check | in progress | Accept fib under agent_output/ OR ciel_workspace/; require fib_results.txt |
| 2026-08-03 | P0.1 Special 7 | sol/luna/5.5 | Special 7 smoke | PASS | Check accepts agent_output OR ciel_workspace fib; require fib_results.txt |
| 2026-08-03 | P0.1 RAG | provider-configured | smoke + full test diagnose | partial→fix | Self-match ate top-k; over-fetch RAG; shell memory fallback; smoke: Python+Rust OK |
| 2026-08-04 | P0.1 RAG retest | provider-configured | test_rag_memory full | OPERATIONAL | Python+Rust+Falcon PASS; archive/recall PASS; Brain used get_fact |
| 2026-08-04 | P0.1 run_all | provider-configured | run_all --skip-exploratory | **9/9 PASS** | unit+live full green; hard 16/16; rag OPERATIONAL; note: gateway 408 once mid hard_special but suite recovered |
| 2026-08-04 | Live news/mail dig | provider-configured | live | **OK** | News digest + multi-recipient mail verification; BTC Binance-first |
| 2026-08-06 | P0.2–P0.4 + Level A/B | provider-configured | live + unit | **PASS** | Route 20/20; email digest/send; context 3/3; Level A/B tick |
| 2026-08-06 | P1.1 + P1.4 + TG HTML | provider-configured | live + unit | **PASS** | Honest tools; sanitize/middleware; Telegram document HTML |
| 2026-08-06 | Cleanup | — | — | done | Removed ephemeral `backtest/_smoke_*.py`; guards → `test_quality_guards` in `run_all` unit |
| 2026-08-08 | P1.5 plan validation | provider-configured | unit 7/7; plan 10/10; live search→Gmail | **PASS** | Pure validator before execution; continuation re-validation; duplicate delivery repair only when reference-safe; one real Gmail Message Id |
| 2026-08-24 | Conversation follow-up repair | provider-configured | `test_conversation_bugs` 78/78; deployed lookup/guard smoke | **PASS** | Chat cannot claim an unexecuted tool call; successful search/scrape carries a 15-minute, RAM-only query + public-URL anchor only for an explicit follow-up. |
| 2026-08-25 | Documentation and Telegram ops refresh | — | link/content audit | done | README, hand-off docs, RAG walkthrough, prompt inventory, and deployment instructions now describe the bounded lookup bridge and keep configuration/secrets out of public docs. |
| 2026-08-25 | Terse lookup continuation | provider-configured | `test_conversation_bugs` 82/82 | **PASS** | Fresh “làm đi” / “mở đi” follows one cached public URL with deterministic `smart_scrape`; several URLs require a selection unless explicitly requested together. |
| 2026-08-25 | Two-model topology | Brain + Worker | unit 7/7; config + boot smoke | **PASS** | Middleware and Router Assistant disabled; dormant aliases mirror Worker/Brain; optional code retained for rollback. |
| 2026-08-25 | Active subject handoff | Brain + Worker | unit 8/8 suites; subject 8/8; exact two-turn live smoke | **PASS** | RAM-only grounded topic/entities/action reaches Brain before routing; 15-minute/3-turn expiry; laptop price/used follow-up routed `multi_tool`. |
| 2026-08-26 | Monthly/weekly planner foundation | Brain + Worker | planner 32/32; unit 9/9; startup wiring smoke | **PASS (foundation)** | Separate monthly/weekly tools over planner.db; JSON todos unchanged; deterministic catch-up triggers and persistent delivery dedupe shared by CLI/Telegram/API. P1.2 still needs a real-day no-spam observation. |
| 2026-08-27 | Planner VPS delivery smoke | Brain + Worker | planner 32/32 in container; isolated 302s Telegram run | **PASS (deployment smoke)** | Rebuilt/recreated Telegram; one weekly message delivered and repeated polls suppressed using temporary DB/state. Production data unchanged. This is not the formal P1.2 real-day observation. |
| 2026-09-29 | Gmail outage (ops) | — | VPS container log | **fixed** | Refresh token expired/revoked (`invalid_grant`) → `gmail_ops` loaded 0 tools and email silently failed. New `gmail_token.json` authorized locally and uploaded; container restarted; 9 Gmail tools loaded. OAuth app should be published (Testing tokens expire in 7 days). |
| 2026-09-29 | Email Markdown → HTML | Brain + Worker | `test_outbound` 53/53; unit 11/11 | **PASS** | `plaintext_to_html` renders tables (with alignment), headings, lists, rules; a live email to an external recipient had shown raw `|---|` rows. Deployed `dd60899`. |
| 2026-09-29 | Git on the VPS | Brain + Worker | `quality_guards` 34/34; unit 11/11; live `git -C /repo log` | **PASS** | Image installs git; host checkout mounted read-only at `/repo` (`CIEL_REPO_PATH`); `git_list_repos` no longer loops through `/proc` (scan of `/` hung 8+ min); shell allowlist follows host OS. Deployed `83a5634`. |
| 2026-10-02 | Verified email send claim | Brain + Worker | `test_outbound` 64/64; unit 11/11 | **PASS** | Data steps of a send plan skip per-step Worker formatting (it wrote “Đã gửi” before the send; seen live 2026-09-29 when the Master then denied the send). Reply now opens with ✅/⚠️/❌ from the Message Id plus a Gmail fetch-back. Deployed `360bc52`. |
| 2026-10-02 | Secret redaction | — | `quality_guards` 41/41 | **PASS** | Telegram poll errors had printed the bot token (inside the request URL) into the container log 6×. `core/redact.py` masks it and any secret-named env value in logs, tool results, and API/Telegram error replies. Rotate the bot token. |
| 2026-10-02 | API hardening + Flutter app | Brain + Worker | `api_auth` 13/13; unit 12/12; Flutter analyze clean, 26/26 tests, web build; live app↔API check | **PASS** | API confirm callback had auto-approved every risky action (`get_event_loop()` raised on the executor thread → `return True`); now fails closed. `CIEL_API_TOKEN` on HTTP + WebSocket; raw thought frames opt-in; vitals loop no longer reads the 12 MB log per tick. React/Tauri `ui/` replaced by Flutter `ciel_app/` (Android/iOS/Windows/macOS/web, runtime server + token). |
| 2026-10-02 | P1.2 + P1.3 | — | `test_proactive` 149/149; `test_user_model` 108/108; VPS log review | **PASS** | See P1.2/P1.3 evidence above. |
| 2026-10-08 | One-container deployment + dashboard | — | `api_server` 26/26; unit 13/13; Flutter analyze clean, 28/28 tests; compose config (with/without tunnel); served-build smoke in headless Chrome; VPS build/health 2026-10-28 | **PASS (deployed)** | `main_api.py` serves the Flutter web build at `/` (public static, API still token-gated by routing) and runs Telegram in-process (`CIEL_API_TELEGRAM`), so one process owns `ciel_data/`. `AgentLoop` turn lock; approvals per WebSocket connection (`api/confirm.py`). Multi-stage Dockerfile (Flutter 3.35.4 → Python), single `docker/docker-compose.yml` with `cloudflared` (profile `tunnel`), port 8000 on loopback. Replaces `docker-compose.api.yml`/`.telegram.yml`. VPS smoke: dashboard 200, `/health` ready, auth gate 401, Telegram online, 0 restart/OOM. |
| 2026-10-28 | VPS audit reliability fixes | Brain + Worker | focused: outbound 71/71, quality 59/59, plan 12/12; unit 13/13 suites (648/648); VPS health/syntax/startup smoke | **PASS (deployed)** | Provider timeouts finish honestly; route logs normalize recoverable JSON; all multi-tool branches keep raw evidence until one final synthesis; deterministic failures skip argument healing; empty/failed references stop dependents; shared-client Gmail reads are sequential. Deployed in the one-container runtime. Gmail OAuth independently returned `invalid_grant` on restart and needs a browser-authorized replacement token. |

---

## 2026-10-30 - Local runtime harness refactor

Implemented the three-lane runtime in `core/harness/`, with compatible CielCore
entry points, durable run/step/artifact/event storage, shared execution limits,
explicit escalation, fresh approval on reissue, and one synthesis per multi-tool
request. Existing context, RAG, permission, planner/reminder and front-end services
remain shared. `recovery_manager.py` is a compatibility import; Python syntax checks
are deterministic. Local `.env` enables the harness after restart; source default
remains disabled for existing deployments. No VPS deployment was performed.

Staged checks: storage 6/6, lanes 4/4, runtime 11/11, workflow replay 22/22.
An additional constructor/first-turn smoke passed 1/1 with fake providers and an
isolated root. Final maintained offline regression: **16/16 suites PASS**, including
43/43 harness checks. Report: `backtest/logs/run_all_20261030_152951.txt`.
Provider-backed/live email tests were not run. See `instructionAI/runtime_harness.md`
for ownership, rollout, boundaries and commands.

---

## Map: this file ↔ instructionAI ↔ code

| Need | Read first | Then |
|------|------------|------|
| Rules & index | `instructionAI/SKILL.md` | topic `.md` in same folder |
| Runtime map / 7 tiers | `instructionAI/architecture.md` | `core/*` |
| How to change code safely | `instructionAI/conventions.md` | — |
| Safety / unattended / outbound | `instructionAI/safety_and_risk.md` | `core/permissions.py`, `llm_connector` |
| Memory / user model / cost | `instructionAI/data_pipeline.md` | — |
| API / voice | `instructionAI/voice_and_interface.md` | `main_api.py` |
| **What to upgrade next** | **`improve.md` (this file)** | `backtest/run_all.py` |
| Dated live status (optional) | `note.md` | not required for hand-off |

### Maintained backtest suites (via `run_all`)

| Suite | Tier | Module |
|-------|------|--------|
| context | unit | `backtest.test_context` |
| active_subject | unit | `backtest.test_active_subject` |
| user_model | unit | `backtest.test_user_model` |
| proactive | unit | `backtest.test_proactive` |
| planner | unit | `backtest.test_planner` |
| daily_brief | unit | `backtest.test_daily_brief` |
| outbound | unit | `backtest.test_outbound` |
| conversation_bugs | unit | `backtest.test_conversation_bugs` |
| quality_guards | unit | `backtest.test_quality_guards` |
| plan_validation | unit | `backtest.test_plan_validation` |
| prompt_harness | unit | `backtest.test_prompt_harness` |
| run_harness | unit | `backtest.test_run_harness` |
| reminders | unit | `backtest.test_reminders` |
| api_auth | unit | `backtest.test_api_auth` |
| api_server | unit | `backtest.test_api_server` |
| integration | live | `backtest.test_integration` |
| hard_special | live | `backtest.test_hard_special` |
| brain_worker | live | `backtest.test_brain_worker` |
| rag_memory | live | `backtest.test_rag_memory` |
| live_conversation | exploratory | `backtest.live_conversation_test` |

Removed as non-regression: sample generators (`generate_test_samples` / `run_test_samples`).

---

## Suggested first message to a new AI

```text
Read instructionAI/SKILL.md and improve.md.
Continue Ciel personal-agent upgrades from the first unchecked roadmap item.
Keep Python decisions / LLM compose. Do not turn this into a coding-IDE product.
Run the tests listed for that item and only tick Pass when criteria match.
```

---

*Roadmap lives in-repo so `@instructionAI` + this file hand off cleanly. Update journal and checkboxes as work lands.*
