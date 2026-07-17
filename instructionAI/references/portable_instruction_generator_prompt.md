# Portable Instruction-Generator Prompt

A project-agnostic prompt. Paste it into a new conversation (any AI, any repo) so the AI
**bootstraps or catches up on `instructionAI/` before doing anything else**, and treats that
folder as ground truth for the rest of the session — including after a context reset, a
compacted/summarized history, or a brand-new session that "forgot" everything prior.

This is the reusable core of the guide that used to live inline in this project's `note.txt`,
turned into an actual instruction addressed to the AI instead of a comment block.

---

## The prompt (copy everything in the fenced block below)

```
SYSTEM BOOTSTRAP — READ THIS FIRST, BEFORE ANY OTHER ACTION.

Step 0 — locate project memory. Check, in this order, for a directory or file holding
persistent project knowledge: `instructionAI/SKILL.md`, `AGENTS.md`, `CLAUDE.md`,
`.cursor/rules/`, `.github/copilot-instructions.md`, or `CONVENTIONS.md` at the repo root.

CASE A — one of these exists:
  Read `instructionAI/SKILL.md` (or the equivalent entry point) FIRST, in full, before
  reading any source code or answering the user. Follow its file index and read every
  file it lists that's relevant to the task. Treat everything written there as GROUND
  TRUTH that OVERRIDES your own generic assumptions, training-data defaults, or guesses
  about "how projects like this usually work." If something in the codebase looks like
  it should be "fixed" or "improved" but the instructions explain a deliberate reason for
  it, do NOT change it without flagging the conflict to the user first.

CASE B — none exists:
  Before starting substantive work, tell the user you found no persistent project
  instructions, and OFFER to bootstrap `instructionAI/` (don't silently skip this, and
  don't build it unasked if the task is trivial/one-off). If they agree, create:
    instructionAI/
      SKILL.md            — entry point: YAML frontmatter (name, description), a "What
                             this project is" section, a table of every file in this
                             folder, and a numbered list of critical rules (things that
                             cause BREAKAGE if violated — not style preferences).
      architecture.md      — file/folder tree with a one-line role per file, the
                             dependency graph (what imports/calls what), and the primary
                             runtime flow.
      conventions.md       — naming patterns, code style actually used (derive it from
                             the code, don't invent one), known gotchas discovered while
                             working in the repo, and an explicit "safe to change" vs
                             "dangerous to change" list.
  Add topic files only as the project actually needs them (data_pipeline.md, api_spec.md,
  safety_and_risk.md, database.md, testing.md, auth_and_security.md, deployment.md,
  model_spec.md, environment.md, voice_and_interface.md, etc.) — don't create empty
  placeholders speculatively.

STANDING RULE FOR THE REST OF THIS SESSION AND ALL FUTURE ONES ON THIS REPO:
  - If your context is ever compacted, summarized, or you start a new session on this
    repo, RE-READ `instructionAI/SKILL.md` before resuming work — never rely on a
    remembered summary of it once the actual file is available again.
  - If you are about to fall back to a generic/default assumption (a common framework
    convention, a "usually projects do X" guess, or your own prior turn's unverified
    claim) and `instructionAI/` exists and could answer the question, READ IT instead of
    guessing. A fallback guess that contradicts a documented, deliberate project decision
    is a bug you would be introducing.
  - When you learn something new and non-obvious about the project's architecture,
    conventions, or a gotcha that cost time to discover, UPDATE the relevant
    `instructionAI/*.md` file — don't let it live only in your own turn's output where
    it will be lost. Update the file's own content when something changes; don't just
    append a dated note (dated changelogs belong in a separate living doc, e.g.
    `architect.md` / `CHANGELOG.md` / `note.txt` — see the exclude-list below).
  - Keep `SKILL.md`'s file table in sync whenever you add or remove a topic file.

CONTENT RULES (apply when writing or editing any instructionAI/*.md file):
  INCLUDE — architecture, file responsibilities, naming/code conventions, data flow,
    API/interface contracts, safety/security constraints, known gotchas, dependency
    graphs, safe-vs-dangerous-to-change lists, design decisions AND WHY (prevents an AI
    from "fixing" something intentional), environment/setup steps.
  EXCLUDE — anything that goes stale within ~2 weeks: TODO lists, benchmark numbers,
    account IDs/keys/secrets, deploy schedules, exact dependency versions (belongs in the
    lockfile/requirements file instead), bug trackers, dated meeting/decision logs. If a
    project wants a rolling status/changelog, keep it in a SEPARATE file the SKILL.md can
    point to — don't mix stable knowledge with things that change weekly.
  VOICE — write as facts ("The service uses X"), not instructions ("You should use X").
    Be specific: exact paths, exact function/module names, exact config keys. Explain the
    WHY behind non-obvious decisions.
  LENGTH — each file ~50–300 lines; split into subtopics past that. Keep the whole folder
    skimmable — a fresh AI should be able to explain the project after reading only this
    folder. If it can't, the docs are incomplete.
  CROSS-REFERENCE, DON'T DUPLICATE — reference other files by name ("see conventions.md
    for..."); `SKILL.md` is the only file allowed to list every other file.

COMPATIBILITY (optional, do only if the user wants multi-tool coverage): mirror a
one-line pointer into whichever of these the user's toolchain actually reads —
`.agents/skills/*/SKILL.md` or `.agents/AGENTS.md` (Gemini/Antigravity), `.claude/`
(Claude Code), `.cursor/rules/` (Cursor), `.github/copilot-instructions.md` (Copilot),
root `AGENTS.md` or `CONVENTIONS.md` (generic fallback most tools check). The master copy
stays in `instructionAI/`; other locations just point to it — one source of truth.

Acknowledge Step 0 explicitly in your first reply of the session (one line: instructions
found and read, or none found and you're offering to bootstrap) before addressing the
user's actual request.
```

---

## Why this exists (context, not part of the pasted prompt)

The original version of this guide lived as a large comment block inside this project's
`note.txt`. That worked but had two problems as a *reusable* artifact: (1) it read as
documentation *about* the convention rather than an instruction addressed *to* the AI, and (2)
being addressed as prose rather than an imperative "do this first" step meant a fresh AI session
could read it and still not treat it as binding — it looked like background material, not a
directive. This version fixes both: it opens with "READ THIS FIRST," is phrased as steps and
rules, and explicitly states the exact failure mode being prevented (falling back to a generic
assumption instead of checking the documented one) — which is the concern that prompted writing
this file.

## How to use it going forward

- **Starting a new project with an AI**: paste the fenced prompt as your first message (or put
  it in whatever "system rules" mechanism your AI tool supports — a Claude Code `CLAUDE.md`,
  a Cursor rule, a custom system prompt). The AI bootstraps `instructionAI/` for that project.
- **Resuming work on an existing project that already has `instructionAI/`**: pasting the same
  prompt still works — Case A fires, and the AI reads `SKILL.md` before touching anything.
- **This file itself**: if you refine the wording later, edit only this file — it is the single
  source of truth for the portable prompt. Don't fork copies into other projects; point back
  here, or copy the current fenced block fresh each time so drift doesn't accumulate.
