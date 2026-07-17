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

For the dated changelog and current live status, see `../architect.md` and `../note.txt`
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
   the dangerous-code scan, and the healing skip-list are code, not model judgment. The Middleware
   tier is the ONLY LLM-based check and is scoped narrowly (email only) and **fail-open**.

5. **Gather real data BEFORE composing any email body.** Market/search/document tools run first;
   anti-fabrication rules forbid inventing prices/numbers. Claim "sent" only on a real Message Id.

6. **`memory_ops.py` is standalone.** It reads/writes `ciel_data/facts.json` directly with zero
   imports from `core/` or `agent_system/`. Keep it that way.

7. **Tool packs auto-register.** Drop a `skills/**/*_ops.py` exposing `get_*_tools()` returning
   `{"tools": [...], "prompt": "..."}`. `ToolManager` discovers it; the UI skill grid updates from
   `GET /skills` with zero frontend edits. No manual registration.

8. **Workspace file ops are sandboxed** to `ciel_workspace/` / `agent_output/` via `_is_safe_path()`.
   Never weaken that check.

9. **Voice never changes the prompts.** TTS output is cleaned by the deterministic `to_speech()`
   normalizer, not by dumbing down the persona (text UI, HUD, and email keep rich formatting).

10. **`.env` is the single source for credentials + provider config.** Swap providers via
    `BRAIN_PROVIDER`/`WORKER_PROVIDER`/`MIDDLEWARE_PROVIDER` with no code changes. Gotcha: the Worker
    model reads `CODER_MODEL`, not `WORKER_MODEL`.
