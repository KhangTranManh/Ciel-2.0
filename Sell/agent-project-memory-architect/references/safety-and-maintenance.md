# Safety and Maintenance

Apply this reference to every mode.

## Authorization boundary

- Audit and explanation requests are read-only.
- A request to create or update project memory authorizes documentation changes within
  that scope, not application rewrites, dependency changes, deployments, messages,
  commits, or pushes.
- Ask for direction when a missing owner decision would materially change the canonical
  structure or discard conflicting instructions.

## Working-tree safety

Inspect repository status before edits. Existing modifications belong to the user
unless proven otherwise. Avoid broad formatting and generated-file churn. Stage or
commit only when explicitly requested, and then include only the intended files.

Do not delete an instruction surface merely because it looks redundant. Confirm that
it is retired and that no tool or workflow still depends on it.

## Sensitive information

Never copy these into project memory or reports:

- secret values, tokens, passwords, cookies, private keys, or authorization headers;
- raw `.env` contents or credential files;
- private user memory, email bodies, customer records, or production payloads;
- full raw logs that may contain prompts, paths, identifiers, or secrets;
- personal home-directory paths when a repository-relative description is sufficient.

It is safe to document the name of a configuration key, expected secret location, and
failure behavior when that knowledge is required to operate the project.

## Stable versus volatile knowledge

Stable memory normally includes architecture, contracts, ownership, schemas, safety
boundaries, configuration semantics, test strategy, deployment topology, and durable
gotchas.

Keep the following outside stable memory unless they define a continuing invariant:

- dates and incident narratives;
- current sprint tasks and unchecked roadmap items;
- benchmark numbers and temporary provider performance;
- release notes and one-time migration status;
- live environment state;
- claims likely to become false within weeks.

If a project maintains a roadmap or operational journal, link to it as a living source
without copying its current state into the canonical instructions.

## Conflict resolution

Use this evidence order unless the repository explicitly declares another authority:

1. current executable code, schemas, and deployment configuration;
2. maintained tests and CI behavior;
3. current canonical project instructions;
4. public README and secondary documentation;
5. issue descriptions, chat summaries, old reports, and remembered context.

Do not automatically rewrite documentation when code conflicts with a deliberate
contract documented as pending or externally required. Surface the conflict and state
which source supports each interpretation.

## Validation boundary

The included validator checks structure, required frontmatter, references, index
coverage, and obvious sensitive-value patterns inside the selected context files. It does not
prove architectural accuracy, validate application code, or scan the entire repository
for secrets.

After changes:

1. inspect every changed documentation file;
2. resolve local links and indexed filenames;
3. run the validator;
4. inspect the final diff and status;
5. run relevant project checks only when warranted;
6. report exactly what was and was not verified.
