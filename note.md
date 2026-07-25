# Ciel 2.0 — Live Status & Reference

Rolling status, the deterministic-safeguard inventory, a condensed changelog, and the outbound
email templates. This is the "what is true right now" file — for the stable architecture see
[`instructionAI/`](instructionAI/) and [`architect.md`](architect.md).

> The universal "how to store project knowledge for an AI" guide that used to live at the top of
> `note.txt` was a duplicate — it now lives only in [`instructionAI/Reamdepromp.md`](instructionAI/Reamdepromp.md).

---

## Current Status (as of 2026-07-25)

**Model tiers — all on Vilao (OpenAI-compatible gateway):**

| Tier | Provider | Model | Config key |
|------|----------|-------|------------|
| Brain (Router/Planner) | Vilao | `ccf/claude-opus-4-8` | `BRAIN_PROVIDER`, `BRAIN_MODEL` |
| Worker (Generator) | Vilao | `op/deepseek/deepseek-v4-pro` | `WORKER_PROVIDER`, **`CODER_MODEL`** |
| Middleware (Finalizer) | Vilao | `op/deepseek/deepseek-v4-pro` | `MIDDLEWARE_PROVIDER`, `MIDDLEWARE_ENABLED` |

- **Config traps:** the Worker's MODEL is read from `CODER_MODEL`, never `WORKER_MODEL`; its
  PROVIDER is `WORKER_PROVIDER` (`CODER_PROVIDER` is not read).
- **Model aliases drift.** DeepSeek retired `deepseek-chat` (use `deepseek-v4-pro`/`-flash`); Vilao
  aliases change too (e.g. `awkr/…` → `ccf/…`). If a tier returns empty output or a 4xx, verify the
  alias is still live *before* suspecting the code.

**Safety model (decoupled — never re-couple):**
- `SAFETY_OPEN=true` + `VILAO_SAFETY_BYPASS=true` → relax Brain **content** filtering only.
- `DISABLE_SAFETY_GATE=false` (default) → the destructive-tool **confirmation gate stays ACTIVE**.
  Coupling these two once let a *denied* confirmation still delete the file — keep them separate.

**Testing:** `backtest/test_integration.py` (28/28 on the current build) and
`backtest/test_rag_memory.py` (OPERATIONAL) are the model-eval suites; `test_hard_special.py`,
`run_multi_gmail_test.py`, etc. send **real** email to the disposable `kxctran@gmail.com` and verify
by a real Gmail Message Id, not by response text. A `venv` on **Python 3.12+** is required
(`pandas-ta`).

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

---

## Changelog (most recent first)

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
