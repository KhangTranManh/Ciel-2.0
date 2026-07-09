# Ciel 2.0

Ciel is an autonomous AI assistant and System Sentinel. It runs on a **three-tier Brain-Worker-Middleware** architecture: the Brain (Router) classifies user intent, the Worker (Generator) produces text/code/formatted output, and a scoped Middleware finalizer reviews outbound email/report bodies for issues regex can't catch (topic mismatch, internal numeric contradictions, hollow templated shells).

The system includes 13+ tools across internal (filesystem, OS, vision) and external (Gmail, trading, Telegram, GitHub, web search, documents) skill packs, hybrid long-term memory via ChromaDB, deterministic workflow safeguards (auto-appends missing send/write steps so a task actually finishes), and a two-layer safety model: a content-filter bypass (independent) plus a destructive-action confirmation gate covering both known high-risk tools and LLM-generated code that matches dangerous patterns (drive format, `rmtree`, fork bombs, etc.).

Built on LangChain with multi-provider support (Vilao, DeepSeek, Gemini, Ollama). Ships with a browser-first React/Tauri desktop UI and a self-running autonomous MLOps pipeline that generates its own fine-tuning dataset.

## Key Features

- **Three-Tier Architecture**: Brain (routing/planning) → Worker (generation) → Middleware (email/report finalizer, scoped, fail-open). See note.txt for full tier breakdown.
- **Selective, Decoupled Safety Model**: Content-filter bypass (`SAFETY_OPEN`/`VILAO_SAFETY_BYPASS`) and the destructive-action confirmation gate (`DISABLE_SAFETY_GATE`) are intentionally independent flags — never re-couple them. Gate covers 8 high-risk tools plus a dangerous-code pattern scan on any Worker-written file/script.
- **Deterministic Workflow Safeguards**: If a plan is missing its terminal send step (email intent + address) or file-write step (explicit path + write verb), it's auto-appended — code-level guarantee the task actually completes, not left to Brain memory.
- **Email Send Pipeline**: Market/asset reports, research/news digests, and document summaries each gather real tool data FIRST, then compose — anti-fabrication rules block invented prices/data and hollow "attached" shells. Every outbound email passes one sanitizer + optional Middleware review before send.
- **Path-Aware File Writing**: Asks for destination path (ciel_workspace/ or agent_output/) unless already specified in the query.
- **Hybrid RAG Memory**: Short-term chat memory + long-term ChromaDB semantic memory with two-tier compression.
- **Self-Healing with Skip-List**: Multi-attempt autonomous error recovery, but skips unfixable error classes (missing library, network timeout, geo-restriction) to avoid guaranteed-to-fail retries.
- **Cost/Usage Tracking**: Every real LLM call (Brain/Worker/Middleware) logs `[LLM_CALL] model=<name>` to a single chokepoint; vitals and test dashboards report real per-tier call counts.
- **Desktop/Browser UI**: React + Tauri v2 frontend (`ui/`) — skill list and live activity are 100% backend-driven (add a skill, zero frontend edits); modality-agnostic core with reserved seams for voice input/output.
- **Autonomous MLOps Pipeline**: Background daemon (`autonomous_pipeline/`) that simulates a Master, runs real Ciel tasks, audits them with an independent Judge model, and builds a Worker fine-tuning dataset — including a Chaos Injector that manufactures hard edge cases (timeouts, missing files, API errors) the normal loop wouldn't generate on its own.
- **Vision & UI Control**: PyAutoGUI + Gemini Vision for screen interaction.
- **Multi-Provider**: Flexible Brain/Worker/Middleware provider selection via .env.

## Architecture Overview

See [architect.md](architect.md) for full details, and [note.txt](note.txt) for the current live status/changelog (safety model, email flows, cost tracking, UI foundation).

High-level flow (primary pipeline):

User Input → CielCore.process()
  → RAG Recall + Compression
  → Router (Brain) → action: chat | tool | code | multi_tool
  → Workflow safeguards (auto-append missing send/write steps)
  → (Safety gate for high-risk tools / dangerous code)
  → Execute + Worker format / Self-healing (skip-list aware)
  → Middleware review (email/report bodies only, fail-open)
  → Response + Memory update

There is also a LangGraph-based pipeline in agent_system/ for more structured multi-step code generation.

## Quick Start

1. Clone the repo and install dependencies:
   pip install -r requirements.txt

2. Configure .env (copy from example if needed):
   - Set BRAIN_PROVIDER=vilao (or deepseek/gemini/ollama)
   - Set BRAIN_MODEL=alic/qwen3.7-max (for Vilao)
   - Provide corresponding API keys
   - SAFETY_OPEN=true, VILAO_SAFETY_BYPASS=true (content-filter bypass)
   - DISABLE_SAFETY_GATE=false (default — keeps the destructive-action Y/N gate active; set true only for fully unattended flows)
   - MIDDLEWARE_ENABLED=true (optional — enables the email/report finalizer tier)

3. Run the CLI:
   python main.py

4. (Optional) Run the API server for external/UI clients:
   python main_api.py

5. (Optional) Run the desktop/browser UI — see [ui/README.md](ui/README.md):
   cd ui && npm install && npm run dev      # browser, http://localhost:1420
   cd ui && npm run tauri dev               # desktop shell (needs step 4 running)

## Configuration Highlights

- SAFETY_OPEN=true: Makes Brain content-filtering permissive for most tasks. Safety remains in for violent content, leaks, harm, and destructive actions — this flag does NOT touch the destructive-action confirmation gate.
- DISABLE_SAFETY_GATE: Separately controls the Y/N confirmation gate for destructive tools and dangerous generated code. Keep `false` unless running a fully unattended/automated flow (e.g. the autonomous pipeline).
- File Writing: Always asks for target path if not present in query. Supports both ciel_workspace/ (user data) and agent_output/ (generated artifacts).
- Providers: Brain, Worker, and Middleware can each use a different provider. Current recommended: Vilao for Brain + DeepSeek for Worker/Middleware.
- High-Risk Tools requiring confirmation: delete_file, execute_shell_command, send_gmail_message, send_gmail_html_message, reply_to_email, trash_email, git_confirm_push, vision_act — plus any write_file/append_file/execute_code call whose content matches a dangerous pattern.

## Key Directories

- ciel_workspace/ — Sandbox for user files, data, logs, screenshots.
- agent_output/ — Default location for AI-generated code and artifacts.
- core/ — Main orchestration (CielCore, Router, ToolManager, RAG, etc.).
- skills/ — Tool implementations (internal + external).
- agent_system/ — Brain/Worker models + LangGraph pipeline + buffer writer.
- ui/ — React + Tauri v2 frontend (browser-first, desktop-wrappable). See ui/README.md.
- autonomous_pipeline/ — Self-running MLOps daemon (Simulated Master, Judge, Chaos Injector, fine-tune dataset builder). See autonomous_pipeline/architect.md.
- backtest/ — Integration and stress tests, plus scripts/prompt_harness.py (mines thoughts.log for recurring failure patterns).
- instructionAI/ — AI instruction files (SKILL.md etc.).

## Recent Updates (as of July 2026)

- **Three-tier architecture**: added a scoped Middleware finalizer for outbound email/report bodies — catches topic mismatch, internal numeric contradictions, and hollow templated shells that regex/rules can't. Fail-open on every failure mode.
- **Safety model decoupled**: content-filter bypass and the destructive-action confirmation gate are now independent flags (previously coupled, which let a denied confirmation still execute). Added a dangerous-code gate so Worker-generated scripts/file writes are scanned for destructive patterns before hitting disk.
- **Deterministic workflow safeguards**: single-tool and multi-tool plans missing a required terminal step (send email / write file) are auto-completed instead of relying on the Brain to remember.
- **Email send pipeline hardened**: market/asset reports, research/news digests, and document summaries all gather real data first; anti-fabrication rules block invented data; every send passes a sanitizer (+ optional Middleware review).
- **Reliability fixes**: LLM request timeouts on all three tiers (previously could hang forever on a stalled provider); missing `timeout=` on trading API calls fixed; self-healing skip-list added for genuinely unfixable errors (missing library, network timeout, geo-restriction).
- **Cost/usage tracking**: every real LLM call across all three tiers logs to a single chokepoint; test dashboards and live vitals report real per-tier call counts.
- **UI foundation added**: React + Tauri v2 frontend, browser-first and desktop-wrappable with zero code changes between the two. Skill list and live activity are fully backend-driven. Modality-agnostic core with reserved seams for voice input/output.
- **Autonomous pipeline hardened**: rotating fallback task pool (with reasoning-leak filtering) when task-gen API calls fail; Judge audit payload sanitized (MIME decode, mojibake/tracking-URL stripping) without stripping legitimate search-result URLs; Chaos Injector added for adversarial edge-case training data.
- **prompt_harness.py**: mines thoughts.log for recurring failure patterns (regex-tagged, not LLM-based) and points at which prompt/file to patch — used to find and fix the single-tool email-send gap and the healing skip-list gap.

See architect.md for the full historical roadmap, note.txt for current live status, and autonomous_pipeline/architect.md for the MLOps pipeline.

## License / Notes

This is an internal research/experimental project. Use at your own risk. The safety mechanisms are configurable but the AI can perform powerful actions when gates are open.

For detailed architecture and instructions for AI assistants working on the code, see the instructionAI/ folder (start with SKILL.md).
