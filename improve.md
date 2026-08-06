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
- Personal multi-tool agent: files, shell, Gmail, Telegram, trading, web, optional vision/UI.
- Pipeline: **Brain (route/plan) → optional Middleware (verify outbound) → Worker (compose)** + `ToolManager` skills.
- **Seven capability tiers** (loop, task state, permissions, context, cancel, proactive, user model): decisions in **deterministic Python**; LLM only plans/composes. See `instructionAI/architecture.md`.
- Entry: `main.py` (CLI), `main_api.py` (UI), `main_telegram.py` — all via `AgentLoop` / `CielCore`.

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
- Pass date: **2026-08-06**  Models: **gpt-5.6-sol / gpt-5.6-luna / gpt-5.5**

### Level B — Core Green (near-term goal)
- [x] Level A  
- [x] `python -m backtest.run_all --skip-exploratory` → **all unit + live suites PASS**  
- [x] ≥3 real email checks (test address): Message Id; no internal paths; no placeholders / invented numbers when tools fail  
- [x] Follow-up context works (“tại sao lại thế” remembers prior turn)  
- **Evidence (2026-08-06 check):** A smoke 7/7; run_all 9/9 (2026-08-04, P0.1); emails P0.3 (multi MsgId → kxctran@…, double-send, dig); follow-up + file đó + scope veto P0.4  
- Pass date: **2026-08-06**

### Level C — Portable / hybrid-local ready
- [ ] Level B  
- [ ] Minimal `.env` documented (providers, models, keys, base URLs)  
- [ ] New machine: clone + env + unit `run_all` PASS + 5 live smokes  
- [ ] If local model: ≥20/20 Brain route JSON parse + live suites not mass-red  
- [ ] Full local 3-role not required if hybrid is solid

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
- Pass date: **2026-08-04**  Models: **gpt-5.6-sol / gpt-5.6-luna / gpt-5.5**

### P0.2 — Brain JSON / routing stable
- [x] **Do:** Brain model in `.env` must emit parseable router JSON reliably.  
- [x] **Test:** 20 smokes (greet, single tool, multi-tool, VI, EN, vague) + 5 consecutive AgentLoop turns  
- [x] **Pass when:** ≥20/20 `ROUTE_DECISION` parse OK; 5/5 consecutive simple chat + tool  
- **Verified:** 2026-08-06 live route smoke 20/20 + 5 consec. Ongoing: integration / hard_special  
- Pass date: **2026-08-06**  Brain model: **gpt-5.6-sol**

### P0.3 — Professional email by default
- [x] **Do:** Tools first → clean body → **one** send; no forced HTML dashboard unless user asks visual.  
- [x] **Test:** `test_outbound`; hard_special market+email; 2–3 real mails to test inbox  
- [x] **Pass when:** Message Id; no `agent_output/`/`ciel_workspace/` in body; no `[]` / invented prices; no double-send; referential “gửi mail đó” correct recipient  
- **Close (2026-08-06):** `test_outbound` 36/36; dig+referential; gmail list multi-id fix; live mails to kxctran@…  
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

### P1.2 — Proactive (useful, not noisy)
- [ ] Digest/alerts/stale todos: cooldown, budget, safe unattended.  
- [ ] **Test:** `test_proactive`  
- [ ] **Pass when:** suite PASS + one real day without spam.  
- Pass date: ________

### P1.3 — User model
- [ ] Preferences only; never inject secrets into `user_model`.  
- [ ] **Test:** `test_user_model`  
- [ ] **Pass when:** suite PASS.  
- Pass date: ________

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
[x] unit run_all 5/5
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
| 2026-08-03 | Baseline | sol / luna / gpt-5.5 | unit 5/5; live 2/4 | Unit OK; hard 15/16; rag partial | improve.md + run_all created |
| 2026-08-03 | Handoff | — | — | — | Moved into repo; linked from instructionAI/SKILL.md for AI continuity |
| 2026-08-03 | P0.1 RAG | sol/luna/5.5 | smoke + full test diagnose | partial→fix | Self-match ate top-k; over-fetch RAG; shell memory fallback; smoke: Python+Rust OK |
| 2026-08-04 | P0.1 RAG retest | sol/luna/5.5 | test_rag_memory full | OPERATIONAL | Python+Rust+Falcon PASS; archive/recall PASS; Brain used get_fact |
| 2026-08-04 | P0.1 run_all | sol/luna/5.5 | run_all --skip-exploratory | **9/9 PASS** | unit+live full green; hard 16/16; rag OPERATIONAL; note: gateway 408 once mid hard_special but suite recovered |
| 2026-08-04 | Live news/mail dig | sol/luna/5.5 | live | **OK** | News dig + multi mail kxctran; BTC Binance-first |
| 2026-08-06 | P0.2–P0.4 + Level A/B | sol/luna/5.5 | live + unit | **PASS** | Route 20/20; email dig/send; context 3/3; Level A/B tick |
| 2026-08-06 | P1.1 + P1.4 + TG HTML | sol/luna/5.5 | live + unit | **PASS** | Honest tools; sanitize/middleware; telegram document HTML |
| 2026-08-06 | Cleanup | — | — | done | Removed ephemeral `backtest/_smoke_*.py`; guards → `test_quality_guards` in `run_all` unit |

| 2026-08-03 | P0.1 Special 7 | sol/luna/5.5 | Special 7 smoke | PASS | Check accepts agent_output OR ciel_workspace fib; require fib_results.txt |
| 2026-08-03 | P0.1 start | — | hard Special 7 check | in progress | Accept fib under agent_output/ OR ciel_workspace/; require fib_results.txt |
|  |  |  |  |  |  |

---

## Map: this file ↔ instructionAI ↔ code

| Need | Read first | Then |
|------|------------|------|
| Rules & index | `instructionAI/SKILL.md` | topic `.md` in same folder |
| Runtime map / 7 tiers | `instructionAI/architecture.md` | `core/*` |
| How to change code safely | `instructionAI/conventions.md` | — |
| Safety / unattended / outbound | `instructionAI/safety_and_risk.md` | `core/permissions.py`, `llm_connector` |
| Memory / user model / cost | `instructionAI/data_pipeline.md` | — |
| UI / voice | `instructionAI/voice_and_interface.md` | — |
| **What to upgrade next** | **`improve.md` (this file)** | `backtest/run_all.py` |
| Dated live status (optional) | `note.md`, `architect.md` | not required for hand-off |

### Maintained backtest suites (via `run_all`)

| Suite | Tier | Module |
|-------|------|--------|
| context | unit | `backtest.test_context` |
| user_model | unit | `backtest.test_user_model` |
| proactive | unit | `backtest.test_proactive` |
| outbound | unit | `backtest.test_outbound` |
| conversation_bugs | unit | `backtest.test_conversation_bugs` |
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
Continue Ciel personal-agent upgrades from the first unchecked P0 item.
Keep Python decisions / LLM compose. Do not turn this into a coding-IDE product.
Run the tests listed for that item and only tick Pass when criteria match.
```

---

*Roadmap lives in-repo so `@instructionAI` + this file hand off cleanly. Update journal and checkboxes as work lands.*
