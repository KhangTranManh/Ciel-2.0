# Prompt Harness — Windows CMD Runbook

This runbook operates Ciel's prompt harness from Windows Command Prompt. The harness
has no dashboard or local HTTP server. Reports and candidates stay under
`%TEMP%\ciel_prompt_harness`; source changes happen only after an explicit apply.

## Safety boundary

- `audit`, `targets`, `list`, and `show-*` do not call a model or edit source.
- `propose` calls Brain and calls Worker only when attribution and confidence checks pass.
- `apply` accepts one candidate from the temporary harness directory, requires the
  literal confirmation `APPLY`, validates hashes and prompt contracts, and runs the
  maintained unit suite.
- The harness does not edit `.env`, credentials, Gmail tokens, private runtime data,
  Git history, deployment state, or more than one allow-listed prompt literal.
- A candidate is a proposal, not proof that its diagnosis is correct.

## 1. Open CMD at the repository

```bat
cd /d "D:\Program Files\Ciel 2.0\Ciel 2.0"
```

Confirm that the CLI wrapper is available:

```bat
scripts\harness_cli.cmd help
```

The wrapper sets UTF-8 automatically. For raw Python commands, set it manually:

```bat
set PYTHONUTF8=1
```

## 2. Audit recurring failures

Audit the last 14 days and require at least two matching occurrences:

```bat
scripts\harness_cli.cmd audit 2 14
```

Arguments:

```text
audit [minimum occurrence count] [number of days]
```

Useful variants:

```bat
REM Broader review: include one-off findings from the last 30 days
scripts\harness_cli.cmd audit 1 30

REM Only stronger recurring findings from the last 7 days
scripts\harness_cli.cmd audit 3 7
```

Audit is deterministic and does not call Brain or Worker.

### One full-project report

Run all-history log mining, prompt-target validation, static tool inventory, and every
maintained unit suite into one Markdown report:

```bat
scripts\harness_cli.cmd full-report
scripts\harness_cli.cmd show-project-report
```

For a faster report without rerunning the unit suite:

```bat
scripts\harness_cli.cmd full-report quick
```

The report is saved under `agent_output\ciel_harness_project_report_*.md`. It inventories
live tools but deliberately does not send Gmail/Telegram messages, delete files, execute
shell actions, push Git, or run other mutating capability smokes.

## 3. Read the audit report

Show the newest report:

```bat
scripts\harness_cli.cmd show-report
```

List every stored report and candidate, newest first:

```bat
scripts\harness_cli.cmd list
```

The temporary location is:

```bat
echo %TEMP%\ciel_prompt_harness
```

Each report finding contains:

```text
Signature         Exact value required by propose
Target            Prompt file and symbol currently associated with the finding
Attribution       direct-event or inferred ownership
Current runtime   Whether an optional target is currently enabled
Proposal status   Eligible for Brain review or blocked before model call
Why               Deterministic reason for that status
Recent examples   Sanitized bounded evidence from thoughts.log
```

Do not copy only the tool name. Copy the complete signature after `##` and before the
occurrence count. For example:

```text
SELF_CORRECTION:OTHER / stealth_search
```

## 4. Find Gmail-related suggestions

Run a broader audit when Gmail problems may have occurred only once:

```bat
scripts\harness_cli.cmd audit 1 30
scripts\harness_cli.cmd show-report
```

Review headings or examples containing one of these tool names:

```text
send_gmail_message
send_gmail_html_message
reply_to_email
create_gmail_draft
search_gmail
```

Common interpretations:

```text
HOLLOW_CONTENT / send_gmail_message
  The Middleware log reported an empty or placeholder body. If Middleware is currently
  disabled, attribution blocks proposal before model calls. This is not permission to
  force a Middleware candidate; inspect the email synthesis/code boundary.

TOOL_ERROR / send_gmail_message
  The send tool returned an execution/provider error. Brain must distinguish prompt,
  credentials/configuration, provider, or code causes.

SELF_CORRECTION:* / search_gmail
  Brain considered a Gmail search result incomplete and attempted correction. The
  target may be inferred rather than causally proven.

PROVIDER_ERROR / send_gmail_message
  Configuration/provider diagnosis is required. It cannot generate a prompt candidate.
```

The report's `Target` line tells which prompt the harness currently associates with the
finding. A Gmail tool name does not guarantee that `GMAIL_SYSTEM_PROMPT` is the correct
cause; Middleware, Router, code, configuration, or external service state may own it.

## 5. Ask Brain to diagnose one exact finding

Use an exact eligible signature copied from the current report:

```bat
scripts\harness_cli.cmd propose "<EXACT SIGNATURE>" 2 14
```

Gmail example — use only if this exact heading exists in the report:

```bat
scripts\harness_cli.cmd propose "TOOL_ERROR / send_gmail_message" 1 30
```

Save the full terminal diagnosis outside the repository while still displaying it later:

```bat
scripts\harness_cli.cmd propose "<EXACT SIGNATURE>" 2 14 > "%TEMP%\ciel_prompt_harness\last_propose.txt" 2>&1
type "%TEMP%\ciel_prompt_harness\last_propose.txt"
```

Possible outcomes:

```text
Deterministic attribution blocked this target before any model call
  Target is inactive or already known to be a non-prompt cause. No candidate exists.

Brain classified this finding outside the safe prompt-only lane
  Brain ran, but type/target/confidence did not pass. Worker did not generate a value.

Candidate saved outside the repository: ...candidate_YYYYMMDD_HHMMSS.json
  Brain approved the prompt lane, Worker proposed a value, and contract checks passed.

Harness stopped safely: ...
  Model output, target, secret, growth, schema, or caller-contract validation failed.
```

`Brain initialized` or `Worker initialized` only confirms client construction. A Worker
proposal occurred only when output reports generation and a new candidate path.

## 6. Review exactly the generated candidate

First list candidates and copy the exact new filename:

```bat
scripts\harness_cli.cmd list
```

Then view that specific file:

```bat
scripts\harness_cli.cmd show-candidate "candidate_YYYYMMDD_HHMMSS.json"
```

Calling `show-candidate` without a filename displays the latest existing candidate. It
may predate a blocked or rejected propose run, so an exact filename is preferred.

Candidate fields:

```text
file / symbol       Exact allow-listed prompt value
source_sha256       Hash of the source when the candidate was generated
old_value_sha256    Hash of the old prompt value
new_value           Worker's proposed complete replacement value
diagnosis           Brain classification, confidence, and root cause
```

Reject the candidate instead of applying when:

- it targets a component that did not participate in the relevant runtime path;
- it invents a JSON field the caller does not parse;
- it tells a caller to block, regenerate, send, or execute behavior unsupported by code;
- it removes persona or unrelated safety rules;
- it contains placeholders, private data, credentials, email contents, or unrelated edits;
- the diagnosis describes a code, configuration, provider, or data problem.

The harness contract validator automatically rejects several of these cases, but human
review remains mandatory.

## 7. Apply one reviewed candidate

Use the exact reviewed filename and type `APPLY` literally:

```bat
scripts\harness_cli.cmd apply "candidate_YYYYMMDD_HHMMSS.json" APPLY
```

Apply performs this sequence:

```text
Validate filename and temporary-state location
→ validate allow-listed file::symbol
→ validate source and old-value hashes
→ validate secrets, growth, JSON fields, and caller contract
→ replace one AST-verified prompt literal
→ run python -m backtest.run_all --unit-only
→ retain on pass or restore the original bytes on failure/timeout
```

Apply does not commit, push, deploy, restart Docker, or reload the VPS.

## 8. Verify after apply

Run the focused harness regression:

```bat
python -m backtest.test_prompt_harness
```

Run the complete maintained unit path if it was not already shown by apply:

```bat
python -m backtest.run_all --unit-only
```

Then reproduce the original Gmail/search behavior in a controlled smoke test. Confirm
the provider outcome or Message ID rather than relying only on model narration.

## 9. Raw Python equivalents

The wrapper is preferred on Windows CMD, but these commands are equivalent:

```bat
set PYTHONUTF8=1

python -m scripts.prompt_harness --mode audit --min-count 2 --since-days 14
python -m scripts.prompt_harness --mode targets
python -m scripts.prompt_harness --mode propose --signature "<EXACT SIGNATURE>" --min-count 2 --since-days 14
python -m scripts.prompt_harness --mode apply --candidate "%TEMP%\ciel_prompt_harness\candidate_YYYYMMDD_HHMMSS.json" --yes
```

The raw apply form accepts a path because it is the lower-level interface. Prefer the
CMD wrapper, which restricts selection to a filename already inside the harness state
directory.

## 10. Troubleshooting

### Signature must match exactly once

Run a fresh audit using the same count/day window, then copy the exact heading:

```bat
scripts\harness_cli.cmd audit 2 14
scripts\harness_cli.cmd show-report
```

### No candidate was created

Read the terminal status. `blocked`, `non-prompt`, `target_supported=false`, or confidence
below the maintained threshold are valid safe outcomes, not harness crashes.

### An old candidate appears

Use `list`, compare timestamps, and view a named file. A rejected propose does not erase
older temporary candidates.

```bat
scripts\harness_cli.cmd list
scripts\harness_cli.cmd show-candidate "<EXACT CANDIDATE FILENAME>"
```

### Unicode arrows or Vietnamese text display incorrectly

The wrapper sets UTF-8 for Python. If raw Python commands are used:

```bat
set PYTHONUTF8=1
```

### Candidate is stale

The target source or prompt changed after proposal. Run a new audit and propose again;
do not bypass hash validation.

### Unit evaluation fails

The harness restores the original prompt bytes. Read the failing suite output, fix the
underlying code/test issue separately, then generate a fresh candidate.
