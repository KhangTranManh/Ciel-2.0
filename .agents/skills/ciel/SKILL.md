---
name: ciel-2.0
description: >
  Ciel 2.0 is a modular personal AI assistant with a two-model Brain/Router and
  Worker architecture, deterministic plan validation and permissions, hybrid RAG
  memory, bounded follow-up context, proactive notifications, and durable monthly
  and weekly planning. Built on LangChain with custom OpenAI-compatible, Gemini,
  DeepSeek, Vilao, and Ollama provider support.
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
