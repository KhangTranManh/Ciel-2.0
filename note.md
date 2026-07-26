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
| **4 — Context discipline** | **99% of every Brain call is fixed overhead** (4,291 tok: router prompt 37%, tool list 33%, persona 28%, the actual request **0.4%**). Injections are added ad hoc in `process()`; there is no single assembler and no token budget. | ⬜ measured, not built |
| **5 — Ergonomics** | A long request cannot be interrupted — `main.py` is a blocking `input()` loop, so the only way out of a 566s call is killing the process. | ⬜ not started |
| **6 — Proactivity** | Ciel only ever answers. It could fire on a clock, but never on a *condition* — so it could say "good morning" and not "that job is still stuck". | 🟡 Group A done (`core/notifier.py`, `core/triggers.py`); condition triggers on the outside world pending |
| **7 — User model** | The fact vault is **pull-only and empty**: nothing injects facts into context, so the model must guess an exact snake_case key *and* choose to call `get_fact`. Proactivity without this is spam. | ⬜ next |

Tiers 6 and 7 were promoted ahead of 4 and 5 deliberately (see the 2026-07-26 entry).
7 should land before the rest of 6: what makes an interruption welcome is knowing what
this person cares about, and the morning digest already proves the point — it sends the
same three forex pairs every day because it has no way to know.

Not a tier, but done alongside: **parallel tool execution** (`core/parallel.py`).

Tier 4 is also the blocker for **local/small models**: the 4,291-token floor means a
4K-context model cannot run Ciel at all, regardless of how capable it is. The routing
contract itself is already model-agnostic — verified by swapping the Brain to a much
cheaper alias with no loss of correctness (see the provider section below).

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
