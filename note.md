# Ciel 2.0 — Live status

**What this file is:** rolling “what is true *right now*” — models, front-ends, open work, short changelog.  
**What it is not:** architecture or rules. Those live in [`instructionAI/`](instructionAI/) (stable) and [`architect.md`](architect.md) (roadmap/history).

---

## How to update this file

Keep it short. Prefer tables and one-line bullets.

| Do | Don’t |
|----|--------|
| Bump the **as-of date** under *Current status* | Paste full design essays (→ `instructionAI/`) |
| Add **newest** changelog entry at the **top** of *Changelog* | Duplicate long bug narratives already fixed and in `instructionAI` |
| One entry ≈ **3–8 lines**: what / where / verified? | Grow past ~200 lines without pruning old changelog |
| Link files: `` `core/foo.py` `` | Copy entire prompts or `.env` secrets |
| Move detail older than ~2 weeks into a one-liner or “see `architect.md`” | Leave stale model names after a provider switch |

**Template for a changelog entry:**

```markdown
### YYYY-MM-DD — Short title
- What changed (files).
- Why / bug if any.
- Verified: how (test name, live check, or “not yet”).
```

---

## Current status *(as of 2026-07-29)*

### Models (OpenAI-compatible custom endpoint)

Switched from Vilao (2026-07-27, balance). Vilao lines may remain in `.env` unused for rollback.

| Tier | Provider key | Model (live) | Config |
|------|----------------|--------------|--------|
| Brain | custom | `gpt-5.6-sol` | `BRAIN_PROVIDER`, `BRAIN_MODEL` |
| Worker | custom | `gpt-5.6-luna` | `WORKER_PROVIDER`, **`CODER_MODEL`** (not `WORKER_MODEL`) |
| Middleware | custom | `gpt-5.5` | `MIDDLEWARE_PROVIDER`, `MIDDLEWARE_ENABLED` |

Swap stack: `API_KEY` + `BASE_URL` + model names. Measure new aliases for **gateway-injected tokens** before adopting (see below).

### Front-ends (all → same `CielCore`)

| Entry | Role |
|-------|------|
| `main.py` | CLI (`--voice` / `--speak`) |
| `main_api.py` | FastAPI + WS `/ws`, `GET /skills` `/health`, `POST /tts` |
| `main_telegram.py` | Bot; allow-listed `chat_id`; inline Yes/No confirms; inbound media → sandbox |

Docker: `docker/` (`docker-compose.api.yml`, `docker-compose.telegram.yml`).  
`DISABLED_SKILL_MODULES` (e.g. `vision_ops`) skips packs at load (no display in container).

### UI (`ui/`)

- Workbench: skills rail · chat · prompt. **No thoughts.log stream in the UI** (WS `thought` ignored client-side).
- Confirm dialog, vitals (calls/tokens/$), optional TTS via `POST /tts`.
- Browser-first (`npm run dev`); Tauri optional. Canonical path: `D:\Ciel-2.0\ui`.

### Safety (never re-couple)

| Flag | Meaning |
|------|---------|
| `SAFETY_OPEN` (+ gateway bypass if any) | Brain **content** filter only |
| `DISABLE_SAFETY_GATE` (default **false**) | Destructive-tool **Y/N gate** only |

### Tests

| Suite | Notes |
|-------|--------|
| Fast (no LLM) | `test_context`, `test_outbound`, `test_proactive`, `test_user_model`, `test_conversation_bugs` — gate after `core/` edits |
| Live LLM / mail | `test_integration`, `test_hard_special`, … — real providers + real Gmail |
| Disposable mail | `kxctran@gmail.com`, `kxcpro123@gmail.com`, `prokxcpro@gmail.com` |
| Python | 3.12+ venv (`pandas-ta`) |

**Caveat:** `test_conversation_bugs.py` is **not** log-sandboxed — it writes into live `ciel_data/logs/thoughts.log`.

### Env gotchas

- Worker model = **`CODER_MODEL`**; worker provider = **`WORKER_PROVIDER`**.
- Model aliases drift — 4xx/empty output: check alias before code.
- Windows `thoughts.log` is CRLF; parsers must normalize (`_iter_entries`).

---

## Capability tiers (all built)

Detail: `instructionAI/architecture.md`. Off-switches live in `.env`.

| Tier | Module | One-liner |
|------|--------|-----------|
| 1 Loop | `core/continuation.py` | Observe → re-plan; Python decides, LLM plans |
| 2 Tasks | `core/task_state.py` | Durable jobs; interrupted on restart |
| 3 Permissions | `core/permissions.py` | AUTO / ASK / DENY / unattended DEFER |
| 4 Context | `core/context.py` | Budgeted assembler; drop whole blocks |
| 5 Cancel | `request_cancel` | Cooperative, step boundaries only |
| 6 Proactive | `notifier.py`, `triggers.py` | Condition triggers, budgeted |
| 7 User model | `core/user_model.py` | Injected profile ≠ secret vault |

Also: **parallel tools** `core/parallel.py` (opt-in `parallel_safe`).

---

## Deterministic safeguards (index)

Philosophy: LLM proposes; **code** gates. Prefer pattern match over exact model strings. Full write-ups: `instructionAI/safety_and_risk.md`.

| Area | Mechanism |
|------|-----------|
| High-risk tools + dangerous code | Y/N + content scan |
| Sandbox | `ciel_workspace/` / `agent_output/` |
| Outbound | Sanitizer + optional Middleware (fail-open) |
| Placeholders / subject | Block unsynthesized shells; enforce exact subject |
| multi_tool | `{{step_N}}` / workflow auto-send; one delivery per recipient/turn |
| Search | Provenance dates; language-aware locale |
| Router JSON | Tolerant extract; `chat` `task` is hint not final reply |
| Self-correct | Don’t overwrite a good result with a failed “fix” |
| Paths in Docker | Remap Windows-style paths into Linux sandbox |

---

## Cost / provider notes *(measured, keep)*

- Gateways can inject **thousands** of tokens per call (e.g. some Claude aliases ~6.5k unsuppressable). Probe: trivial request → compare provider `input_tokens` vs sent size.
- Brain call is mostly **fixed overhead** (router prompt + tool list + persona ≫ user text). New skills raise every Brain call. `ROUTER_PERSONA_MODE=slim` available (−~27% in A/B; default still full).

---

## Open / next

| Priority | Item |
|----------|------|
| Backend | Wire UI `{ type: "cancel" }` → `request_cancel` in `main_api` if not done |
| Reliability | Plan/arg validator, provider fallback client, Brain latency (persona off router, skip trivial routes) |
| UI | Optional: surface deferred approvals / proactive NOTIFY over WS |
| Deploy | UI static (e.g. Pages/Vercel) ≠ full agent; agent needs host + secrets (see ops notes, not here) |

---

## Changelog *(newest first — prune aggressively)*

### 2026-07-28 → 07-29 — Docker, Telegram, path + self-correct fixes
- Docker images for API + Telegram; CPU torch; `DISABLED_SKILL_MODULES`.
- `main_telegram.py` + allow-list + keyboard confirms + inbound media / `describe_image_file`.
- Fix: failed self-correct no longer discards a good tool result; Windows path remap in Linux containers.
- Verified: image build, `/health`, Telegram round-trip, RAG query.

### 2026-07-27 — Conversation + email routing bugs
- `chat` `task` is hint only; Worker gets real user text + reasoning.
- Referential email (“gửi qua email đó”) resolution fixed.
- Several transcript-derived conversation bugs; see `instructionAI` rules 20–21.

### 2026-07-26 — Tiers 1–7 + parallel + double-send
- All seven capability tiers landed; parallel opt-in tools.
- One send per recipient per turn (double-email fix).
- Provider injection / Brain token breakdown measured.

### 2026-07-25 and earlier
- Web search provenance/recency; multi_tool/RAG reliability; voice STT/TTS; cost tracking; Middleware tier; UI foundation.
- Detail: `architect.md` changelog.

---

## Email templates *(outbound — data first)*

Always: tools first → fill facts only → no `[...]` leftovers → sign **Ciel.** Claim sent only with real Message Id.

| Kind | Approach |
|------|----------|
| Market / risk | Plain template: prices/TA/risk from tools; or HTML via `build_market_report_html` → `send_gmail_html_message` |
| News / research | `stealth_search` first; admit failure if empty |
| Document | `read_document` first |
| Other | Todo summary, status, alert/digest, thread reply — structure to the ask |

Plain market skeleton (fill from tools):

```
Subject: Báo cáo thị trường … – [ngày]
1. Overview (live prices)  2. TA  3. Risk  4. Conclusion
— Ciel
```

HTML dashboard layout reference: `email_template/` (not literal content).

---

*Last pruned/rewritten for maintainability: 2026-07-29. Stable rules stay in `instructionAI/`.*
