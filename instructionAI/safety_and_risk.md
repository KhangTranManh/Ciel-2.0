# Safety & Risk — Ciel 2.0

Safety mechanisms that prevent Ciel from performing destructive actions without approval.

## The Decoupled Safety Model (critical — do not re-couple)

Two **independent** flags govern two **different** things:

- **`SAFETY_OPEN`** (+ `VILAO_SAFETY_BYPASS`) — relaxes Brain **content filtering** only (fewer
  false refusals on normal tasks like email). Has NO effect on the destructive-tool gate.
- **`DISABLE_SAFETY_GATE`** — controls the **destructive-tool confirmation gate** only. Default
  **`false`** (gate ACTIVE / fail-safe). Set `true` only for fully unattended automation.

These were deliberately split after an incident where coupling them meant a *denied* confirmation
could still let a destructive action through. Never merge them back.

## Safety Gate (Tool Confirmation)

**Eight tools** require the Master's explicit Y/N approval before execution:

| Tool | Risk |
|------|------|
| `delete_file` | Permanently DELETE a file from the workspace |
| `execute_shell_command` | Run an OS shell command on the machine |
| `send_gmail_message` | Send a plain-text email from the Master's Gmail |
| `send_gmail_html_message` | Send a rich HTML email (e.g. market dashboard reports) |
| `reply_to_email` | Reply to an existing email thread |
| `trash_email` | Move an email to Trash |
| `git_confirm_push` | Commit and PUSH code to the remote repo |
| `vision_act` | Autonomously control the screen (click, type, scroll) |

**Plus a content-based gate** (not tied to a fixed tool list): `write_file`, `append_file`, and
`execute_code()` also require Y/N when the WRITTEN CONTENT matches a genuinely destructive pattern
— drive/disk `format`, `mkfs.`, `shutil.rmtree(`, `rm -rf /`, fork bombs, `DROP DATABASE/TABLE`,
`shutdown /r`, etc. (`_find_dangerous_code_patterns()` in `llm_connector.py`). This exists because
the pre-declared tool list alone missed a case where generated code wrote a fully-wired drive-format
function to disk via an ordinary `write_file` call, gated only against the OS system drive.

### How it works

1. `CielCore.execute_tool()` checks if the tool name is in `_HIGH_RISK_TOOLS`, OR (for
   `write_file`/`append_file`/`execute_code`) scans the content for dangerous patterns.
2. If flagged, `_request_confirmation()` builds a human-readable preview (with an optional
   dynamic `risk_override` reason for the content-based gate) and calls `self.confirm_callback`.
3. The callback is wired by the entry point:
   - **CLI** (`main.py`): Blocking `input("Y/N")` with a yellow safety banner.
   - **WebSocket** (`main_api.py`): Sends `confirm_request` JSON to the UI, blocks up to 60s for
     `confirm_response`.
4. If denied → tool returns `[CANCELLED]` without executing.
5. If no callback is set → auto-approves with a warning log (fail-open for unattended pipelines
   that intentionally never wire a callback — NOT the same as `DISABLE_SAFETY_GATE`).

### Logging

All safety events are logged to `thoughts.log` with the `[SAFETY]` actor tag:
`[CONFIRM_REQUESTED]`, `[CONFIRM_APPROVED]`, `[CONFIRM_DENIED]`.

## Outbound Email — Sanitizer + Middleware (the two layers before send)

Every outbound email/report body passes through, in order:

1. **Deterministic sanitizer** (`_sanitize_outbound_email()`) — strips `[COGNITION]` lines, persona
   tag prefixes, signature placeholders, "email sent/Message Id" scaffolding, redundant Subject lines,
   and (for email, not file reports) internal paths.
2. **Middleware review** (optional, `MIDDLEWARE_ENABLED`, scoped to email/report bodies) — an LLM-based
   semantic check for relevance/consistency/grounding that a sanitizer can't catch (e.g. "claims
   Bearish but the price is above both moving averages"). Acts as a finalizer (can rewrite the body),
   is capped at `MIDDLEWARE_MAX_PASSES`, and **fails open on every failure mode** — a Middleware
   hiccup, timeout, or unfixable flag must never block a legitimate send. It may ONLY flag numbers
   that contradict each other WITHIN the same body — never reject a number merely because it looks
   unfamiliar against the model's own training-era knowledge.
3. The safety-gate preview (Y/N) reflects the FINAL body, after both steps.

### Synthesis-placeholder guard (deterministic, both file writes and email)

A multi_tool plan defers a write/send whose body is a synthesis placeholder, fills it with the
Worker-synthesized report, then executes it. If a placeholder ever SURVIVES to the actual
write/send, it is blocked — `_has_unsynthesized_placeholder()` matches ANY paraphrased marker
(`[SYNTHESIZE_FROM_RESULTS: ...]`, `[..._TO_BE_SYNTHESIZED]`, bracketed ALL-CAPS shells), not one
fixed string. This is symmetric: the email path already hard-blocked its canonical marker, and the
FILE-write path now has the same guard (a hollow `[SYNTHESIZE...]` shell used to reach disk when a
stronger Brain paraphrased the marker and the old exact-string check missed it).

### Subject enforcement (deterministic)

When the request names an exact subject (`subject exactly '...'`, `tiêu đề '...'`), the send step's
subject is overwritten with it (`_enforce_subject()`) rather than trusting the Brain to keep it — the
Brain routinely substitutes its own descriptive subject. Applies on BOTH the single-send (`action="tool"`)
and multi_tool paths; only touches an existing `subject` arg (so `reply_to_email` is unaffected).

## Quarantine Zone (Workspace Sandbox)

File tools in `system_ops.py` are locked to the `ciel_workspace/` (and `agent_output/` for
generated code) directories:

- `_is_safe_path(target_path)` resolves the path and checks `resolved_path.is_relative_to(WORKSPACE_DIR)`.
- If the resolved path escapes the workspace, a `PermissionError` is raised.
- Applies to: `list_workspace`, `read_file`, `write_file`, `append_file`, `delete_file`,
  `get_file_info`, `run_python_script`, `read_document`.

**Never weaken this check** — it prevents Ciel from accidentally deleting or overwriting project
source files.

## Vision Failsafe

The `vision_ops.py` module has multiple safety layers:

- **`pyautogui.FAILSAFE = True`**: Moving the mouse to any screen corner aborts all vision actions.
- **Max 10 steps**: The vision loop hard-stops after 10 screenshot→action cycles.
- **Loop-break detector**: Auto-stops after 3 identical `action@grid` attempts (prevents infinite clicking).
- **Debug screenshots**: All vision steps are saved as PNGs in `ciel_workspace/screenshots/` for audit.
- `vision_act` is ALSO one of the 8 high-risk tools — it requires Y/N before it starts controlling the screen.

## Self-Healing Guardrails

The `RecoveryManager` has built-in limits:

- **Max 3 attempts** per tool error (syntax fix → logic rewrite → stdlib-only rewrite).
- **Syntax validation** via Worker before saving any fixed code.
- **Skip-list** (`_HEALING_SKIP_PATTERNS`): errors no parameter guess could ever fix (missing
  library, network timeout, geo-restriction, OS socket errors) short-circuit straight past the
  retry loop instead of burning a guaranteed-to-fail Worker call.
- Escalating strategies prevent the same fix from being tried twice.

## Shell Command Safety

`os_ops.py` has a basic dangerous-keyword guard for `execute_shell_command`. This tool is ALSO
covered by the Safety Gate (requires Master approval) — the two are independent layers.

## Credential Safety

- **No secrets in code**: All API keys live in `.env` (gitignored).
- **Git auto-excludes**: `github_ops.py` automatically excludes `.env`, `credentials.json`, and
  token files from commits.
- **OAuth tokens**: `gmail_token.json` is auto-generated and stored in `ciel_data/` (gitignored).
- **`credentials.json`** (Google OAuth client) is never tracked by git — if missing, Gmail tools
  simply don't load (0 tools registered); the rest of Ciel is unaffected. Recreate it via the
  Google Cloud Console (OAuth client, Desktop app type, Gmail API scope
  `https://mail.google.com/`).
