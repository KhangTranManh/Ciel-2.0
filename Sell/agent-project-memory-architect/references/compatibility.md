# Cross-Agent Compatibility

Use this reference only when the user wants more than one coding-agent ecosystem to
discover the same project knowledge.

## One canonical source

Choose one complete source of truth. It can be a root instruction file, an existing
project guide, or a dedicated documentation folder. Tool-specific files should point
to that source and retain only behavior unique to the tool.

Before replacing any existing instruction file:

1. read it in full;
2. identify unique rules and path-specific scope;
3. migrate stable shared rules into the canonical source;
4. retain genuinely tool-specific directives locally;
5. verify that the pointer path is correct from the tool's working directory.

## Common surfaces

| Ecosystem | Typical surface | Guidance |
|---|---|---|
| Generic coding agents | Root or nested `AGENTS.md` | Keep scope and precedence explicit |
| Codex skills | `.agents/skills/<name>/SKILL.md` | Use a concise discovery wrapper when repository-local skills are supported |
| Claude Code | `CLAUDE.md` or `.claude/` | Preserve Claude-specific commands only when needed |
| Cursor | `.cursor/rules/` | Keep matching/scope metadata accurate; avoid copying the whole manual |
| GitHub Copilot | `.github/copilot-instructions.md` | Point to canonical knowledge and retain GitHub-specific constraints |

Support varies by product version. Inspect the repository's established format and the
current tool documentation when exact metadata or precedence matters.

## Pointer content

A pointer should state:

- the exact canonical entry path;
- that it must be read before substantive changes;
- that workspace files override remembered summaries;
- how nested or tool-specific rules interact with the canonical source.

Do not claim a tool automatically follows a pointer unless that behavior is verified.
When a tool cannot follow references reliably, keep a minimal critical subset locally
and record how it is synchronized.

## Migration checks

- No unique safety or deployment rule was lost.
- No compatibility file contains a stale full copy of the canonical instructions.
- Relative paths resolve from the environment that reads them.
- Nested instructions retain their narrower scope.
- Removing a retired tool surface does not remove documentation still used by another
  agent or CI process.
