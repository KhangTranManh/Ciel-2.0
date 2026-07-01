# Safety & Risk — Ciel 2.0

Safety mechanisms that prevent Ciel from performing destructive actions without approval.

## Safety Gate (Tool Confirmation)

Six tools require the Master's explicit Y/N approval before execution:

| Tool | Risk |
|------|------|
| `delete_file` | Permanently DELETE a file from the workspace |
| `execute_shell_command` | Run an OS shell command on the machine |
| `send_gmail_message` | Send an email from the Master's Gmail |
| `trash_email` | Move an email to Trash |
| `git_confirm_push` | Commit and PUSH code to the remote repo |
| `vision_act` | Autonomously control the screen (click, type, scroll) |

### How it works

1. `CielCore.execute_tool()` checks if the tool name is in `_HIGH_RISK_TOOLS`.
2. If yes, `_request_confirmation()` builds a human-readable preview and calls `self.confirm_callback`.
3. The callback is wired by the entry point:
   - **CLI** (`main.py`): Blocking `input("Y/N")` with yellow safety banner.
   - **WebSocket** (`main_api.py`): Sends `confirm_request` JSON to Flutter, blocks up to 60s for `confirm_response`.
4. If denied → tool returns `[CANCELLED]` without executing.
5. If no callback is set → auto-approves with a warning log (fail-open for unattended pipelines).

### Logging

All safety events are logged to `thoughts.log` with the `[SAFETY]` actor tag:
- `[SAFETY] [CONFIRM_REQUESTED]`
- `[SAFETY] [CONFIRM_APPROVED]`
- `[SAFETY] [CONFIRM_DENIED]`

### Testing

- `test_integration.py` sets `core.confirm_callback = lambda *_: True` for auto-approval.
- Test 20 specifically verifies that a denied confirmation prevents file deletion.

## Quarantine Zone (Workspace Sandbox)

File tools in `system_ops.py` are locked to the `ciel_workspace/` directory:

- `_is_safe_path(target_path)` resolves the path and checks `resolved_path.is_relative_to(WORKSPACE_DIR)`.
- If the resolved path escapes the workspace, a `PermissionError` is raised.
- This applies to: `list_workspace`, `read_file`, `write_file`, `append_file`, `delete_file`, `get_file_info`, `run_python_script`.

**Never weaken this check** — it prevents Ciel from accidentally deleting or overwriting project source files.

## Vision Failsafe

The `vision_ops.py` module has multiple safety layers:

- **`pyautogui.FAILSAFE = True`**: Moving the mouse to any screen corner aborts all vision actions.
- **Max 10 steps**: The vision loop hard-stops after 10 screenshot→action cycles.
- **Loop-break detector**: Auto-stops after 3 identical `action@grid` attempts (prevents infinite clicking).
- **Debug screenshots**: All vision steps are saved as PNGs in `ciel_workspace/screenshots/` for audit.

## Self-Healing Guardrails

The `RecoveryManager` has built-in limits:

- **Max 3 attempts** per tool error (syntax fix → logic rewrite → stdlib-only rewrite).
- **Syntax validation** via Worker before saving any fixed code.
- Escalating strategies prevent the same fix from being tried twice.

## Shell Command Safety

`os_ops.py` has a basic dangerous-keyword guard for `execute_shell_command`. However, this tool is also covered by the Safety Gate (requires Master approval).

## Credential Safety

- **No secrets in code**: All API keys live in `.env` (gitignored).
- **Git auto-excludes**: `github_ops.py` automatically excludes `.env`, `credentials.json`, and token files from commits.
- **OAuth tokens**: `gmail_token.json` and `calendar_token.json` are auto-generated and stored in `ciel_data/`.
