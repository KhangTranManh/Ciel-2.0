---
name: agent-project-memory-architect
description: Audits, creates, consolidates, or updates repository context for AI coding agents. Use when any software project needs accurate architecture, conventions, safety rules, test guidance, or compatible instruction files grounded in its current codebase.
---

# Agent Project Memory Architect

Create a compact repository guide that helps coding agents understand a project across
sessions. Ground every claim in repository evidence. Preserve the project's existing
format, terminology, and ownership.

## Select the mode

- **Audit**: inspect existing project instructions and report gaps or contradictions.
  Do not edit unless the user also asks for changes.
- **Bootstrap**: create the smallest useful instruction set when none exists and the
  user asks for one.
- **Maintain**: update existing knowledge after implementation or deployment changes.
- **Migrate**: consolidate duplicated tool-specific instructions behind one canonical
  source while retaining the compatibility files the project actually uses.

For discovery and audit criteria, read
[references/discovery-and-audit.md](references/discovery-and-audit.md). For new or
substantially reorganized memory, read
[references/memory-structure.md](references/memory-structure.md). Read
[references/compatibility.md](references/compatibility.md) only when multiple coding
agents or instruction formats are in scope. Always apply
[references/safety-and-maintenance.md](references/safety-and-maintenance.md) before
writing or validating files.

## Operating contract

1. Treat workspace files as authoritative over chat summaries, old documentation, and
   generic framework assumptions.
2. Inspect the working tree before editing. Preserve unrelated changes and distinguish
   pre-existing modifications from this task.
3. Find existing instruction systems before choosing a location. Keep the established
   canonical source when it works. If none exists, choose a structure that matches the
   repository and the user's agent tools. Do not impose a product-specific folder name.
4. Read enough implementation evidence to support each documented fact: entry points,
   package manifests, runtime configuration, tests, deployment files, schemas, and the
   modules directly relevant to the task. Do not claim exhaustive coverage after a
   shallow scan.
5. Separate stable project knowledge from volatile status. Stable architecture,
   contracts, invariants, and design reasons belong in the canonical guide. Roadmaps,
   incidents, benchmarks, and dated deployment results belong in a separate living
   document only when the project maintains one.
6. Write facts, not aspirations. Label planned or optional behavior explicitly. Remove
   claims for components that are no longer present or supported.
7. Explain non-obvious reasons where changing a behavior could cause breakage. Avoid
   generic advice that a capable coding agent already knows.
8. Keep one source of truth. Compatibility files should point to the canonical entry
   point instead of copying the full rules unless a tool cannot follow pointers.
9. Do not expose secrets, credentials, private user data, raw production logs, internal
   account identifiers, or machine-specific paths. Refer to secret names and storage
   contracts only when they are necessary to operate the project safely.
10. Do not modify application code, dependencies, deployment state, Git history, or
    external systems merely to make the documentation easier to write.

## Workflow

### 1. Establish scope

Identify the requested mode, repository root, intended agent tools, and whether the
user authorized edits. If the user asked only for an audit or explanation, remain
read-only.

### 2. Discover existing memory

Search for `AGENTS.md`, `CLAUDE.md`, `instructionAI/SKILL.md`, project documentation,
`.cursor/rules/`, `.github/copilot-instructions.md`, `.agents/`, and other explicit guidance.
Map which file is canonical, which files are compatibility surfaces, and which claims
are duplicated or stale.

### 3. Gather evidence

Inspect the smallest sufficient set of source and configuration files. Build an
evidence map connecting every important documentation claim to a concrete path,
symbol, command, schema, or test. When code and documentation disagree, report the
conflict and follow current code unless the repository marks the behavior as an
intentional future contract.

### 4. Design or update the memory

Use progressive disclosure:

- the entry point identifies the project, indexes the knowledge files, and contains
  only the cross-cutting rules that can cause breakage;
- topic files own detailed architecture, conventions, safety, data, testing, or
  deployment knowledge;
- optional topics exist only when the project has evidence that justifies them.

Update facts in place rather than appending a second, conflicting explanation. Remove
retired components from current-state sections. Preserve historical facts only where
they explain a still-relevant constraint.

### 5. Add compatibility surfaces when requested

Create only the files needed by the user's actual toolchain. Keep each pointer concise
and identify the canonical entry point. Never overwrite substantial existing
instructions without reconciling their unique rules first.

### 6. Validate

Run the included read-only checker when Python is available. It auto-detects common
entry files:

```bash
python scripts/validate_memory.py <repository-root>
```

Use `--entry <relative-path>` when auto-detection is ambiguous. Use
`--memory-dir <relative-directory>` when a root entry file points to a documentation
folder. Then inspect local links, the final diff, and repository status. Run project
tests only when the changes require them. A documentation checker does not validate
runtime behavior.

### 7. Hand off

Report the canonical entry point, files created or updated, important conflicts
resolved, validation performed, and any uncertainty that still needs owner input. Keep
the report independent of hidden reasoning and do not imply unrun tests passed.

## Completion standard

The result is complete when a fresh coding agent can determine what the project is,
where its major boundaries are, which rules prevent breakage, and where to read deeper
without loading the whole repository or relying on stale session history.
