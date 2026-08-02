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
- [ ] `main.py` boots; no mass `JSONDecodeError` on routing  
- [ ] ~10 manual commands OK: chat VI/EN, todo, calculate, list/read file, weather/search  
- [ ] `python -m backtest.run_all --unit-only` → **5/5 suites PASS**  
- [ ] Unattended: no silent high-risk send/delete (DEFER, not auto-consent)

### Level B — Core Green (near-term goal)
- [ ] Level A  
- [ ] `python -m backtest.run_all --skip-exploratory` → **all unit + live suites PASS**  
- [ ] ≥3 real email checks (test address): Message Id; no internal paths; no placeholders / invented numbers when tools fail  
- [ ] Follow-up context works (“tại sao lại thế” remembers prior turn)

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
- [ ] **Do:** Fix remaining failures; do not delete tests to fake green.  
- [ ] **Test:** `run_all --unit-only` then `run_all --skip-exploratory`  
- [ ] **Pass when:**  
  - Unit 5/5: context, user_model, proactive, outbound, conversation_bugs  
  - Live all PASS: integration, hard_special, brain_worker, rag_memory  
- [ ] **Known baseline (2026-08-03):** hard_special 15/16 (Special 7: missing `ciel_workspace/fibonacci.py`); rag_memory partial (Python/Rust recall)  
- Pass date: ________  Models: ________

### P0.2 — Brain JSON / routing stable
- [ ] **Do:** Brain model in `.env` must emit parseable router JSON reliably.  
- [ ] **Test:** 20 smokes (greet, single tool, multi-tool, VI, EN, vague) + integration chat/edge  
- [ ] **Pass when:** ≥20/20 `ROUTE_DECISION` parse OK; 5/5 consecutive simple chat + one tool in `main.py`  
- Pass date: ________  Brain model: ________

### P0.3 — Professional email by default
- [ ] **Do:** Tools first → clean body → **one** send; no forced HTML dashboard unless user asks visual.  
- [ ] **Test:** `test_outbound`; hard_special market+email; 2–3 real mails to test inbox  
- [ ] **Pass when:** Message Id; no `agent_output/`/`ciel_workspace/` in body; no `[]` / invented prices; no double-send; referential “gửi mail đó” correct recipient  
- Pass date: ________

### P0.4 — Conversation context (recent turns + open thread)
- [ ] **Do:** History read-back; scope veto (“dừng/chỉ…thôi”); open thread for slot-fill / “file đó”.  
- [ ] **Test:** `test_conversation_bugs` + 3 live cases  
- [ ] **Pass when:** suite full PASS; live cases do not regress known transcript bugs  
- Pass date: ________

**P0 complete ≈ Level B** (plus manual email quality if not fully covered by hard_special).

---

## P1 — Personal-agent quality

### P1.1 — Daily tools (market / search / file / todo)
- [ ] Partial tool failure → honest; no fabrication.  
- [ ] **Pass when:** 5/5 manual tool turns match tool results.  
- Pass date: ________

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

### P1.4 — Middleware
- [ ] Catch path leaks / internal contradictions; never reject live tool numbers as “implausible vs training”.  
- [ ] **Pass when:** path leak blocked; real tool numbers not false-flagged.  
- Pass date: ________

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
[ ] unit run_all 5/5
[ ] live run_all --skip-exploratory full green
[ ] 20/20 Brain JSON parse
[ ] 3 emails: Message Id, no path, no placeholder, no double-send
[ ] follow-up context OK
[ ] "dừng lại" / only-list OK
[ ] unattended no silent send/delete
[ ] models recorded in journal
```

All checked → **Level B** → personal agent trustworthy for daily use.

---

## Upgrade journal

| Date | Item | Models (B/W/M) | Tests | Result | Notes |
|------|------|----------------|-------|--------|-------|
| 2026-08-03 | Baseline | sol / luna / gpt-5.5 | unit 5/5; live 2/4 | Unit OK; hard 15/16; rag partial | improve.md + run_all created |
| 2026-08-03 | Handoff | — | — | — | Moved into repo; linked from instructionAI/SKILL.md for AI continuity |
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
