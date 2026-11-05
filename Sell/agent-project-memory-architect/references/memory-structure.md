# Memory Structure

Use this reference when creating a new canonical knowledge layer or substantially
reorganizing an existing one.

## Choose a layout

Preserve an existing layout when it is clear and maintained. For a small repository,
one root file may be enough:

```text
AGENTS.md               Project identity, commands, and critical rules
```

For a larger repository, use the project's documentation namespace:

```text
AGENTS.md                       Short entry point or compatibility pointer
docs/agent-context/
  index.md                      Canonical index and cross-cutting rules
  architecture.md               Ownership, dependencies, and runtime flows
  conventions.md                Code, data, test, and operational conventions
  safety.md                     Permissions, secrets, and external actions
```

`instructionAI/SKILL.md`, `CLAUDE.md`, Cursor rules, or another established format is
also valid. Do not rename a working system to match these examples. Add topic files only
when the repository has enough stable evidence to support them.

## Entry-point requirements

The canonical entry should contain:

1. Format metadata required by that entry type. A `SKILL.md` file needs YAML
   frontmatter. An `AGENTS.md` or `CLAUDE.md` file does not.
2. A concise statement of project identity and maintained scope.
3. Current entry points or operational surfaces.
4. Links or an index for every canonical context file and its unique responsibility.
5. Numbered critical rules: facts whose violation can break safety, correctness, data,
   deployment, or maintenance.
6. The standard validation or regression command, if one exists.
7. A rule that separates stable knowledge from roadmap and dated operational status.

Keep detailed subsystem material in topic files. The entry point should be sufficient
for routing, not an encyclopedia.

## Topic ownership

### Architecture

Include repository structure at a useful level, dependency direction, primary runtime
flows, process ownership, persistence boundaries, and interface entry points. Prefer
small diagrams or structured flows only when they clarify relationships.

### Conventions

Document conventions demonstrated by the code: naming, tool or plugin registration,
result shapes, error handling, serialization, test placement, logging contracts, and
safe-versus-dangerous change areas. Explain operational gotchas that cost real time to
rediscover.

### Safety and risk

Describe permission order, destructive operations, unattended behavior, secrets,
filesystem containment, external delivery, authentication, audit behavior, and
failure/retry constraints. State which layer owns each control.

### Conditional topics

Add a topic only when it has enough independent material and a stable owner. Split a
topic when readers routinely need one part without the rest, not merely because a file
is long.

## Content filter

Include:

- facts tied to current code, configuration, schemas, or maintained tests;
- design reasons that prevent a future agent from undoing an intentional constraint;
- exact paths, symbols, configuration names, and commands when stable;
- known limitations and optional behavior labeled accurately.

Exclude:

- secrets, credentials, tokens, private payloads, or production log bodies;
- temporary local paths and personal machine habits;
- benchmark snapshots, incidents, dates, release notes, and rapidly changing status;
- generic programming advice;
- speculative future features presented as current behavior;
- duplicated explanations owned by another topic file.

## Writing rules

- Write declarative facts: “The API validates…” rather than “You should validate…”.
- Distinguish “must” invariants from recommendations and observations.
- Name the owner of a rule: module, service, schema, process, or policy.
- Cross-reference details rather than copying them.
- Update incorrect text in place. Do not preserve contradictions as an append-only
  history inside stable documentation.
- Prefer bounded examples that clarify a contract; avoid large source-code copies.
