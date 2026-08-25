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

For all project knowledge, see `instructionAI/SKILL.md` (the master copy).

**Cold start:** `instructionAI/SKILL.md` → `improve.md` (Level B baseline; continue the first unchecked roadmap item).
**Tests:** `python -m backtest.run_all --unit-only` (includes `quality_guards`).

## Quick Reference

| File | Location |
|------|----------|
| Project overview & critical rules | `instructionAI/SKILL.md` |
| File tree & dependency graph | `instructionAI/architecture.md` |
| Code patterns & gotchas | `instructionAI/conventions.md` |
| Safety gate & risk rules | `instructionAI/safety_and_risk.md` |
| Memory pipeline & scheduler | `instructionAI/data_pipeline.md` |
| Upgrade roadmap (Level A/B/C, P0–P3) | `improve.md` (root) |
| Live status / changelog (optional) | `note.md` (root) |
