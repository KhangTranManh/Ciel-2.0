---
name: ciel-2.0
description: >
  Ciel 2.0 is a modular AI assistant with Brain-Worker architecture. It features
  intent routing, multi-tool execution, self-healing error recovery, hybrid RAG
  memory (ChromaDB + JSON), a safety gate for destructive tools, vision/UI
  interaction via PyAutoGUI + Gemini Vision, and proactive background scheduling.
  Built on LangChain with multi-provider support (Gemini, DeepSeek, Ollama).
---

# Ciel 2.0 — AI Instruction Set

## What this project is

Ciel is an autonomous AI assistant and System Sentinel. It runs on a **Brain-Worker** architecture where the Brain (Router) classifies user intent and the Worker (Generator) produces text, code, or formatted output. The system includes 13+ tools across internal (filesystem, OS, vision) and external (Gmail, trading, Telegram, GitHub) skill packs, hybrid long-term memory via ChromaDB, and a safety gate requiring explicit approval for destructive operations.

Entry points: `main.py` (CLI) and `main_api.py` (FastAPI/WebSocket for Flutter HUD).

## Instruction files in this folder

| File | Purpose |
|------|---------|
| `SKILL.md` | This file — project overview, file index, critical rules |
| `architecture.md` | File tree, module responsibilities, dependency graph, runtime flow |
| `conventions.md` | Code patterns, naming rules, tool registration, safe/dangerous changes |
| `safety_and_risk.md` | Safety gate system, high-risk tools, quarantine zone, vision failsafe |
| `data_pipeline.md` | RAG memory pipeline, facts vault, scheduler design, audit trail |

## Critical rules for any AI working on this project

1. **Never modify `thoughts.log` format.** It is the raw chronological audit trail. Use `scripts/format_thoughts_log.py` to generate readable views. Any format change breaks log parsing.

2. **`memory_ops.py` is standalone.** It reads/writes `ciel_data/facts.json` directly with zero external dependencies. Do not add imports from `core/` or `agent_system/` to it.

3. **Always use the safety gate for high-risk tools.** The six tools in `_HIGH_RISK_TOOLS` (`delete_file`, `execute_shell_command`, `send_gmail_message`, `trash_email`, `git_confirm_push`, `vision_act`) must pass through `_request_confirmation()`. Never bypass this check.

4. **Tool packs must expose a `get_*_tools()` factory.** `ToolManager` auto-discovers skill modules by scanning for functions matching `get_*_tools()`. Each must return `{"tools": [...], "prompt": "..."}`.

5. **Scheduler tasks must not pollute memory.** Scheduled tasks run in Ghost Mode — they call raw APIs directly, skip the Brain, and never write to `memory_bank.json` or RAG.

6. **Workspace operations are sandboxed.** All file tools in `system_ops.py` are locked to `ciel_workspace/` via `_is_safe_path()`. Never weaken this quarantine.

7. **Persona files are modular.** The personality in `persona/official_ciel_personality.txt` is loaded at startup. Do not hardcode personality traits in `llm_connector.py`.

8. **Keep `.env` as the single source for credentials and provider config.** Provider switching (Gemini/DeepSeek/Ollama) is done via `BRAIN_PROVIDER` and `WORKER_PROVIDER` in `.env`. No code changes required to switch providers.
