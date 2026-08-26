# Safety and Risk — Ciel 2.0

## Security model

Ciel separates content permissiveness, execution authority, filesystem scope, and
unattended behavior. No single flag disables all four.

| Control | Purpose |
|---|---|
| `SAFETY_OPEN` | Brain content-filter permissiveness only |
| `DISABLE_SAFETY_GATE` | Interactive destructive-tool confirmation only |
| `PermissionPolicy` | Exact-call `AUTO`, `ASK`, `DENY`, or `DEFER` decision |
| Filesystem sandbox | Restricts user-operable paths |
| Unattended ceiling | Prevents silence or old grants becoming consent |

`SAFETY_OPEN` and `DISABLE_SAFETY_GATE` were separated after coupling them allowed a
denied destructive action to proceed. They never share logic.

## Pre-execution order

```text
Brain plan
  -> deterministic plan validation
  -> exact-call permission review
  -> attended plan confirmation when required
  -> per-call delivery/content guards
  -> tool execution
```

An invalid multi-tool plan executes nothing. Permission approval applies only to the
tool and arguments displayed; later continuation steps must be validated and reviewed
again.

## High-risk tools

The following tools require explicit attended approval unless a narrower policy denies
them first:

| Tool | Risk |
|---|---|
| `delete_file` | Permanent workspace deletion |
| `execute_shell_command` | Arbitrary host/container command |
| `send_gmail_message` | External plain-text email |
| `send_gmail_html_message` | External HTML email |
| `reply_to_email` | External thread reply |
| `trash_email` | Inbox mutation |
| `git_confirm_push` | Commit and remote push |
| `vision_act` | Autonomous screen control |

`write_file`, `append_file`, and `execute_code` also require approval when their
content matches destructive patterns such as disk formatting, recursive root deletion,
database dropping, shutdown commands, or fork bombs. The content gate covers risk that
cannot be captured by a fixed tool-name list.

## Confirmation channels

Supported front ends wire the same callback signature:

```python
confirm_callback(tool_name, preview, tool_args) -> bool
```

- CLI uses a blocking terminal prompt and supports a session grant.
- API uses a WebSocket `confirm_request`/`confirm_response` exchange.
- Telegram uses an inline Yes/No keyboard restricted to the configured chat ID.

API and Telegram fail closed when the client disappears or times out. The raw
`CielCore` compatibility path still warns and auto-approves when no callback exists;
therefore a new front end must always set one and must not rely on the raw default.

## Unattended ceiling

`PermissionPolicy.decide(..., attended=False)` converts risky work to `DEFER`.

In unattended mode:

- `DENY` still wins.
- Safe read-only calls may remain `AUTO`.
- Session grants are ignored.
- Plan approvals are ignored.
- `DISABLE_SAFETY_GATE` is ignored.
- Only explicitly configured `CIEL_UNATTENDED_AUTO_TOOLS` may widen the safe set, and
  never past a deny rule.

Deferred calls are recorded in `ciel_data/state/deferred.json` and never replayed
automatically. The user reissues a fresh request against current state.

Unattended state is thread-local and explicitly propagated into parallel workers. A
background trigger cannot change the permission context of a foreground user turn.

## Outbound delivery controls

### One delivery per recipient per turn

`CielCore.execute_tool()` deduplicates successful Gmail and Telegram sends by
recipient/channel. This covers:

- `send_gmail_message`
- `send_gmail_html_message`
- `reply_to_email`
- `send_telegram`
- `send_telegram_document`

The check runs before confirmation, and a delivery is recorded only after tool success.
Failure remains retryable. The scope resets at the start of the next user turn.

An HTML Telegram attachment is terminal delivery for that turn: workflow safeguards do
not append a second text send, and Telegram does not repost the full report body after
the document.

### Email sanitization and Middleware

Outbound email passes through:

1. deterministic removal of internal cognition, duplicate subject scaffolding,
   placeholders, false sent-status prose, and internal paths;
2. optional Middleware semantic review for relevance and internal consistency;
3. final exact-subject enforcement when the user specified one;
4. the confirmation preview using the final body;
5. provider delivery and Message ID verification.

Middleware is bounded and fail-open. It may identify contradictions inside the body,
but cannot reject grounded tool data merely because the model considers it unfamiliar.

A synthesis placeholder surviving to a real write/send is blocked by pattern, not one
canonical string. Stronger models may paraphrase placeholders.

## Filesystem sandbox

File tools operate under `ciel_workspace/` and, for generated outputs,
`agent_output/`. Resolved paths escaping those roots raise `PermissionError`.

Linux containers can receive Windows-style paths from model prompts. Remapping accepts
only paths containing a recognized sandbox segment and maps the suffix to the current
host/container root. Arbitrary drive paths remain rejected.

Telegram uploads are stored under `ciel_workspace/telegram_uploads/`. The inbound note
that reports their saved location is transport metadata, not write intent. Uploads are
not overwritten unless the user explicitly asks; derived reports belong in
`agent_output/`.

## Plan and recovery safety

`core/plan_validation.py` verifies loaded tool names, schema-compatible object
arguments, backward step references, and dependency-safe duplicate handling before any
permission or execution.

Recovery is bounded:

- tool healing has a fixed attempt ceiling;
- syntax is validated before repaired code is saved;
- environmental and non-repairable failures skip model-based retries;
- a failed corrective attempt cannot replace an already successful result;
- deterministic failure signals cannot be overruled by a model saying “satisfied.”

## Vision safety

Desktop vision uses PyAutoGUI failsafe corners, a finite action loop, repeated-action
detection, screenshot auditing, and high-risk confirmation for `vision_act`.

Headless Docker deployments disable `vision_ops` through
`DISABLED_SKILL_MODULES=vision_ops`. Loading a display-dependent module and denying it
later is less reliable than excluding it at discovery time.

## Credentials and private data

- `.env`, `credentials.json`, Gmail tokens, `ciel_data/`, and deployment credentials
  stay outside Git.
- Docker bind-mounts private files instead of baking them into the image.
- Git tooling excludes known secret paths from automated commits.
- Missing Gmail credentials disable Gmail capability rather than the whole assistant.
- `UserModel` rejects secret-like content before storage and prompt injection.
- Logs and documentation never include raw keys, passwords, tokens, private `.env`
  values, or authorization headers.

Repository publication also requires checking history, not only the working tree.
Removing a secret from the latest commit does not remove it from earlier Git objects.

## Audit events

Confirmation requests, approvals, denials, deferrals, duplicate suppression, trigger
outcomes, and provider usage are recorded in `thoughts.log` using the existing stable
format. The audit trail supports diagnosis; it does not grant authority or serve as a
user-facing response stream.
