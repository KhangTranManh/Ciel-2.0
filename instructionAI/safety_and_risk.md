# Safety & Risk — Ciel 2.0

Safety mechanisms that prevent Ciel from performing destructive or unwanted actions
without approval — whether a human is at the keyboard or not.

## The Decoupled Safety Model (critical — do not re-couple)

Two **independent** flags govern two **different** things:

- **`SAFETY_OPEN`** (+ `VILAO_SAFETY_BYPASS`) — relaxes Brain **content filtering**
  only (fewer false refusals on normal tasks like email). No effect on the
  destructive-tool gate.
- **`DISABLE_SAFETY_GATE`** — controls the **destructive-tool confirmation gate**
  only. Default **`false`** (gate ACTIVE / fail-safe). Set `true` only for fully
  unattended automation, and even then it does not mean "act without oversight" — see
  the unattended ceiling below, which `DISABLE_SAFETY_GATE` cannot bypass.

These were deliberately split after an incident where coupling them let a *denied*
confirmation still allow a destructive action through. Never merge them back.

## The Safety Gate (Attended Confirmation)

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

**Plus a content-based gate**, not tied to a fixed tool list: `write_file`,
`append_file`, and `execute_code()` also require Y/N when the WRITTEN CONTENT matches a
genuinely destructive pattern — drive/disk `format`, `mkfs.`, `shutil.rmtree(`,
`rm -rf /`, fork bombs, `DROP DATABASE/TABLE`, `shutdown /r`, etc.
(`_find_dangerous_code_patterns()`). This exists because the pre-declared tool list
alone missed a case where generated code wrote a fully-wired drive-format function to
disk via an ordinary `write_file` call.

**Every `(tool, args)` also resolves through `core/permissions.py`'s three-way policy**
(`AUTO`/`ASK`/`DENY`) — see Tier 3 in `architecture.md` for plan-level approval, grant
scopes, and why a grant is keyed on the exact call signature, not the tool name.

Before that permission review, `core/plan_validation.py` rejects malformed, unknown, or
forward-dependent multi-tool steps without executing any of them. It may remove only a
dependency-safe duplicate outbound delivery; `CielCore.execute_tool()` remains the final
per-turn guard, so later continuation rounds cannot bypass idempotency.

### How it works

1. `CielCore.execute_tool()` checks if the tool is in `_HIGH_RISK_TOOLS`, OR (for
   `write_file`/`append_file`/`execute_code`) scans the content for dangerous patterns.
2. If flagged, `_request_confirmation()` builds a human-readable preview (with an
   optional dynamic `risk_override` for the content-based gate) and calls
   `self.confirm_callback`.
3. The callback is wired by the entry point:
   - **CLI** (`main.py`): blocking `input("Y/N")` with a yellow safety banner.
   - **WebSocket** (`main_api.py`): sends `confirm_request` JSON to the UI, blocks up
     to 60s for `confirm_response`.
4. Denied → the tool returns `[CANCELLED]` without executing.
5. No callback set → auto-approves with a warning log (fail-open for pipelines that
   intentionally never wire one — **not** the same thing as `DISABLE_SAFETY_GATE`).

All safety events log to `thoughts.log` under the `[SAFETY]` actor:
`[CONFIRM_REQUESTED]`, `[CONFIRM_APPROVED]`, `[CONFIRM_DENIED]`, `[DEFERRED]`,
`[DUPLICATE_SEND_SUPPRESSED]`.

## The Unattended Ceiling — silence is never consent (Tier 6)

Everything above assumes a human is present to answer. A trigger firing at 03:00 has
nobody to ask, and **"nobody answered" must never resolve to "yes"**.
`PermissionPolicy.decide(..., attended=False)` adds a fourth outcome:

```
AUTO   read-only → runs
DENY   refused outright → still wins over everything
DEFER  risky + nobody present → recorded, NOT run, raised at the next interaction
```

In that context, **session grants, plan approvals, and `DISABLE_SAFETY_GATE` are all
ignored.** Each is evidence a human agreed *while present*, and none of that transfers
to a background run hours later. `DISABLE_SAFETY_GATE` means "stop asking me" — a
statement about interruptions, not a standing permission to act unsupervised. The only
escape hatch is per-tool and explicit: `CIEL_UNATTENDED_AUTO_TOOLS`, which cannot reach
past the deny list.

`CielCore.unattended` is **thread-local**, and `_run_steps` propagates it into parallel
workers by hand. A plain attribute would let the scheduler thread flip it while a
foreground request was mid-flight, turning that user's confirmations into silent
deferrals — a safety control that fails open in a thread is worse than none.

`DeferredStore` (`ciel_data/state/deferred.json`) records what was blocked and
**deliberately never replays it**. A mutating action decided against 03:00's world is
not the same action at 09:00, and approving it from a one-line summary is approving a
fragment — the exact failure Tier 3 removed. The Master re-issues it as a fresh
request, planned against the world as it now is.

`main_api.py`'s `_ws_confirm()` applies this at the point it matters most: if no
WebSocket is attached when a high-risk step needs approval (client disconnected
mid-run, or a background step outlives the connection that started it), it records
the action via `DeferredStore.add()` and returns `False` — deny, not auto-approve.
This does not go through `core.unattended`/`permissions.decide()` like the scheduler
path does; it is a second, narrower enforcement point specific to the one channel
(`main_api.py`) where "socket present" is the actual attendance signal.

## Outbound Idempotence — one delivery per recipient per turn

For an HTML report, `send_telegram_document` is the terminal Telegram delivery. The
workflow safeguard must not append `send_telegram`, and `core/telegram_interface.py`
must not relay the full synthesized response after a successful attachment. This avoids
an attachment followed by duplicate report text.

Telegram's inbound upload note (`[File/Ảnh Master vừa gửi qua Telegram, ...]`) is
transport metadata, not a request to send a reply through Telegram. Intent detection
strips it before deciding whether to append an outbound delivery step.

`execute_tool` suppresses a second outbound send (`send_gmail_message`,
`send_gmail_html_message`, `reply_to_email`, `send_telegram`,
`send_telegram_document`) to the same recipient/channel within one request.

This exists because **two independent mechanisms** can complete a plan missing its
send step — the workflow safeguard in `execute_multi_tool` (which appends one) and the
Tier-1 loop (which re-plans one) — and neither knew about the other. Reproduced live:
the same report delivered twice, with two different subjects.

Rules that make it correct:
- **Key on the RECIPIENT, never a full argument signature.** The duplicates differ in
  subject and body by construction, so a signature over all args would never match.
- **Record only on SUCCESS.** A failed send must stay retryable; marking it delivered
  would turn one provider hiccup into mail that never goes out and never says why.
- **Check before the safety gate.** Asking the Master to approve a send about to be
  suppressed is worse than not asking.
- **Scoped to one turn.** Two separate requests may legitimately mail the same person
  again.

Any new code path that can send **must** go through `execute_tool`, or it bypasses
this.

## Outbound Email — Sanitizer + Middleware (the layers before send)

Every outbound email/report body passes, in order:

1. **Deterministic sanitizer** (`_sanitize_outbound_email()`) — strips `[COGNITION]`
   lines, persona tag prefixes, signature placeholders, "email sent/Message Id"
   scaffolding, redundant Subject lines, and (email only, not file reports) internal
   paths (`agent_output/…`, `ciel_workspace/…`). Live tool numbers must survive.
2. **Middleware review** (optional, `MIDDLEWARE_ENABLED`, email/report bodies only) —
   an LLM-based semantic check for relevance/consistency/grounding a sanitizer can't
   catch (e.g. "claims Bearish but the price is above both moving averages"). A
   finalizer (can rewrite the body), capped at `MIDDLEWARE_MAX_PASSES`, and **fails
   open on every failure mode** — a hiccup, timeout, or unfixable flag must never block
   a legitimate send. It may ONLY flag numbers that contradict each other *within* the
   same body — never reject a number merely because it looks unfamiliar against the
   model's training-era knowledge.
3. The safety-gate preview (Y/N) reflects the FINAL body, after both steps.
4. The outbound idempotence guard (above) applies at send time, before the gate.

### Synthesis-placeholder guard (deterministic)

A multi_tool plan defers a write/send whose body is a synthesis placeholder, fills it
with the Worker-synthesized report, then executes it. If a placeholder ever SURVIVES to
the actual write/send, it is blocked — `_has_unsynthesized_placeholder()` matches ANY
paraphrased marker, not one fixed string, because a stronger Brain paraphrasing the
canonical marker once let a hollow shell reach disk under the old exact-string check.
Symmetric across both the email and file-write paths.

### Subject enforcement (deterministic)

When the request names an exact subject (`subject exactly '...'`, `tiêu đề '...'`), the
send step's subject is overwritten with it (`_enforce_subject()`) rather than trusting
the Brain to keep it. Applies on both the single-send (`action="tool"`) and multi_tool
paths; only touches an existing `subject` arg, so `reply_to_email` is unaffected.

## Quarantine Zone (Workspace Sandbox)

File tools in `system_ops.py` are locked to `ciel_workspace/` (and `agent_output/` for
generated code):

- `_is_safe_path(target_path)` resolves the path and checks
  `resolved_path.is_relative_to(WORKSPACE_DIR)`.
- An escaping path raises `PermissionError`.
- Applies to: `list_workspace`, `read_file`, `write_file`, `append_file`,
  `delete_file`, `get_file_info`, `run_python_script`, `read_document`.

**Never weaken this check** — it is what stops Ciel from accidentally deleting or
overwriting project source files.

## Vision Failsafe

- **`pyautogui.FAILSAFE = True`** — moving the mouse to any screen corner aborts all
  vision actions.
- **Max 10 steps** — the vision loop hard-stops after 10 screenshot→action cycles.
- **Loop-break detector** — auto-stops after 3 identical `action@grid` attempts.
- **Debug screenshots** — every vision step is saved to
  `ciel_workspace/screenshots/` for audit.
- `vision_act` is also one of the 8 high-risk tools — Y/N is required before it starts
  controlling the screen.

## Self-Healing Guardrails

- **Max 3 attempts** per tool error (syntax fix → logic rewrite → stdlib-only rewrite).
- **Syntax validation** via Worker before saving any fixed code.
- **Skip-list** (`_HEALING_SKIP_PATTERNS`) — errors no parameter guess could ever fix
  (missing library, network timeout, geo-restriction, OS socket errors) short-circuit
  past the retry loop instead of burning a guaranteed-to-fail Worker call.
- Escalating strategies prevent the same fix being tried twice.

## Shell Command Safety

`os_ops.py` has a basic dangerous-keyword guard for `execute_shell_command`. Also
covered by the Safety Gate (Master approval required) — the two layers are
independent.

## Credential Safety

- **No secrets in code** — all API keys live in `.env` (gitignored).
- **Git auto-excludes** `.env`, `credentials.json`, and token files
  (`github_ops.py`).
- **OAuth tokens** — `gmail_token.json` is auto-generated in `ciel_data/`
  (gitignored).
- **`credentials.json`** (Google OAuth client) is never tracked. If missing, Gmail
  tools simply don't load (0 tools registered); the rest of Ciel is unaffected.
  Recreate via Google Cloud Console (OAuth client, Desktop app type, Gmail API scope
  `https://mail.google.com/`).
- **A second credential boundary lives in `core/user_model.py`** — anything that looks
  like a credential is refused at the store level before it can ever be injected into
  a prompt. See Tier 7 in `architecture.md` and the "two memory stores" section of
  `data_pipeline.md`.
