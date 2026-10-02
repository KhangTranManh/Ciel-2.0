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

API and Telegram fail closed when the client disappears or times out. The API callback
runs on an executor thread, so it schedules the confirm request on the server loop
captured at startup; before this was fixed, `asyncio.get_event_loop()` raised on that
thread and the handler returned `True`, auto-approving every risky API action. A confirm
request that cannot be delivered is now a denial. Raw `CielCore`
with no callback also fails closed: each risky call is denied
(`confirm_denied_no_callback`), and a plan containing a step that needs approval is
cancelled before its first step (`plan_denied_no_callback`). A new front end must wire a
callback before it can run any high-risk tool.

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
4. the confirmation preview using the final (plain/Markdown) body;
5. Markdown → inline-styled HTML rendering (`core/outbound.plaintext_to_html`), so
   tables, headings and lists do not reach the recipient as raw `|` and `---`;
6. provider delivery and Message ID verification;
7. a re-check that fetches the sent message back by that Message Id before the Master
   is told “✅ Đã gửi”. A missing Message Id or a failed re-check is reported as ❌/⚠️.

In a plan that ends in a send, the data steps are not formatted individually by the
Worker. A per-step reply sees the whole request (“gửi email…”) and wrote “Đã gửi” before
the send had run; one synthesis now reads the raw step results.

Middleware is bounded and fail-open. It may identify contradictions inside the body,
but cannot reject grounded tool data merely because the model considers it unfamiliar.

A synthesis placeholder surviving to a real write/send is blocked by pattern, not one
canonical string. Stronger models may paraphrase placeholders.

## Filesystem sandbox

File tools operate under `ciel_workspace/` and, for generated outputs,
`agent_output/`. Resolved paths escaping those roots raise `PermissionError`.

Docker images contain no `.git`. Compose mounts the host checkout read-only at `/repo`
and sets `CIEL_REPO_PATH=/repo`, which becomes the `[WORKING DIRECTORY]` note, so git
tools can read status, diff, and log but cannot commit or push from a container. The
shell allowlist is chosen by host OS: Windows keeps its cmd list, Linux gets read-only
POSIX commands. Package installs (`apt-get`, `winget`, `sudo`) are never allowlisted;
system packages belong in the Dockerfile.

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
  An expired or revoked refresh token (`invalid_grant`) does the same and is visible only
  in the container log (`gmail_ops returned 0 tools`). Replacing `credentials.json` does
  not fix it: delete `ciel_data/gmail_token.json`, authorize again on a machine with a
  browser, and copy the new token to the server. An OAuth app left in "Testing" issues
  refresh tokens that expire after 7 days; publish it to avoid that.
- `core/redact.redact_secrets` masks the Telegram bot token (inside `/bot<token>/` URLs or
  bare) and every environment value whose name looks secret (`*_KEY`, `*_TOKEN`,
  `*SECRET*`, `*PASSWORD*`). It is applied to Telegram poll/send/download errors, the
  `send_telegram*` tool results (which reach `thoughts.log` and the models), Telegram
  fatal replies, and the API `error` frame. `requests` exceptions embed the full URL, so
  without it a network error printed the bot token into the container log.
- `CIEL_API_TOKEN` guards the API for remote clients; the Flutter app keeps it in the OS
  keychain. A token compiled into a client build would be extractable, so the app takes
  it at runtime only.
- `UserModel` rejects secret-like content before storage and prompt injection.
- The prompt harness sanitizes bounded evidence before Brain/Worker use, hard-blocks
  private path/name patterns independently of its JSON allow-list, and rejects
  secret-like candidate values before any source write. Attribution may expose a named
  boolean feature state such as `MIDDLEWARE_ENABLED=false`, but never its source file or
  any neighboring environment values. Inactive and non-prompt targets stop before a
  model call; prompt candidates cannot invent caller contracts or response fields.
- Logs and documentation never include raw keys, passwords, tokens, private `.env`
  values, or authorization headers.

Repository publication also requires checking history, not only the working tree.
Removing a secret from the latest commit does not remove it from earlier Git objects.

## Audit events

Confirmation requests, approvals, denials, deferrals, duplicate suppression, trigger
outcomes, and provider usage are recorded in `thoughts.log` using the existing stable
format. The audit trail supports diagnosis; it does not grant authority or serve as a
user-facing response stream.
