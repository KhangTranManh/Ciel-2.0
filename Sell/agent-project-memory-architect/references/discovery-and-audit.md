# Discovery and Audit

Use this reference for audit, bootstrap, and migration work.

## Search order

Start at the repository root and look for explicit project instructions before reading
broad source trees:

1. Root `AGENTS.md`, `CLAUDE.md`, `CONVENTIONS.md`, or another declared entry point.
2. Dedicated context folders such as `instructionAI/`, `docs/agent-context/`, or the
   repository's established documentation location.
3. `.agents/`, `.claude/`, `.cursor/rules/`, and
   `.github/copilot-instructions.md`.
4. Public onboarding and architecture documentation.
5. Package manifests, entry points, deployment definitions, schemas, migrations, and
   maintained tests.

Use fast file and text search when available. Respect nested instruction precedence:
an instruction file closer to a target path may intentionally specialize a root rule.

## Evidence map

Maintain a working map with these columns:

| Claim | Evidence | Confidence | Destination |
|---|---|---|---|
| Runtime entry point | Exact file and symbol | High/medium/low | Architecture |
| Safety boundary | Policy module and tests | High/medium/low | Safety |
| Test command | Runner or CI configuration | High/medium/low | Conventions |

Do not place the map in the repository unless it is useful to maintain there. Its
purpose is to prevent attractive but unsupported documentation.

## Audit dimensions

### Identity and scope

- Is the project purpose stated in current, concrete terms?
- Are shipped interfaces distinguished from prototypes, retired clients, and roadmap
  items?
- Are supported environments and deployment modes evidence-based?

### Architecture

- Are entry points, subsystem ownership, dependency direction, and primary runtime
  flows documented?
- Does the architecture describe current code rather than an intended rewrite?
- Are concurrency, persistence, and process-ownership assumptions recorded when they
  can cause corruption or duplicated work?

### Contracts and invariants

- Are model outputs, external responses, tool arguments, schemas, and permissions
  validated at deterministic boundaries?
- Are high-risk or destructive operations and their confirmation rules explicit?
- Are retry, cancellation, idempotency, and partial-failure semantics documented?

### Data and privacy

- Are durable, transient, cached, and private data stores distinguished?
- Are secret locations described without revealing values?
- Are filesystem and outbound-data boundaries clear?

### Testing and operations

- Do documented commands exist?
- Are focused and full regression commands distinguished?
- Are live tests labeled, isolated, and cleaned up?
- Are deployment instructions compatible with current manifests and process topology?

### Maintenance quality

- Is there one canonical source rather than several drifting copies?
- Does the entry point identify every canonical topic file or link to its index?
- Are dated status, benchmarks, incidents, TODOs, and release notes outside stable
  project memory?
- Can a fresh agent discover details progressively instead of loading everything?

## Finding severity

- **Critical**: Following the documentation could expose secrets, bypass safety,
  corrupt data, execute the wrong deployment, or perform an external mutation.
- **High**: The instructions contradict current architecture, ownership, interfaces,
  schemas, or maintained test behavior.
- **Medium**: Important behavior is missing, ambiguous, duplicated, or difficult to
  discover.
- **Low**: Wording, organization, or cross-linking creates friction without changing
  behavior.

For audit-only requests, return prioritized findings with evidence and proposed fixes;
do not silently implement them.
