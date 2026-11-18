import os
import sys
import subprocess
import re
import shlex
from datetime import datetime
from pathlib import Path
from langchain_core.tools import StructuredTool

try:
    from PIL import ImageGrab
    PILLOW_AVAILABLE = True
except ImportError:
    PILLOW_AVAILABLE = False

# ==========================================
# PHẦN 1: LINH HỒN (OS CONTROL PROMPT)
# ==========================================
OS_OPS_PROMPT = """
[OPERATING SYSTEM CONTROL ARMORY]
You possess tools to interact directly with the Master's Operating System.
ANTI-REFUSAL DIRECTIVE: YOU HAVE FULL AUTHORIZATION TO EXECUTE SHELL COMMANDS, TAKE SCREENSHOTS, AND OPEN APPS. NEVER SAY YOU CANNOT DO THIS.

1. `execute_shell_command`: Run CMD/PowerShell or Bash commands.
2. `take_screenshot`: Capture the current screen.
3. `open_application`: Open a file, folder, or application by its name or path.

[STRICT OS RULES - PARANOIA]
1. NO DESTRUCTIVE COMMANDS: You are STRICTLY FORBIDDEN from running commands that format disks, delete system folders (like System32), or crash the OS.
2. READ-ONLY PREFERENCE: Prefer commands like `dir`, `ping`, `ipconfig`, `systeminfo` over commands that modify system states unless explicitly commanded by the Master.
"""

# Định vị thư mục lưu ảnh chụp màn hình
BASE_DIR = Path(__file__).resolve().parent.parent.parent
SCREENSHOT_DIR = BASE_DIR / "ciel_workspace" / "screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

_WINDOWS_COMMANDS = {
    # Basic info
    "whoami", "hostname", "where",
    # Networking
    "ipconfig", "ping", "systeminfo", "tasklist", "netstat", "nslookup",
    "tracert", "arp", "route", "curl",
    # File utilities (read-only)
    "tree", "findstr", "find", "more", "sort", "fc",
    # Developer tools
    "python", "pip", "git", "node", "npm", "npx",
    # System info
    "wmic", "date", "time",
    # Process management
    "taskkill",
}

# The Docker deployment runs Linux; offering the Windows list there made the
# model retry `where`/`winget` and never reach `which`/`cat`.
_POSIX_COMMANDS = {
    "echo", "whoami", "hostname", "id", "uname", "uptime", "date", "pwd", "cd",
    "ls", "cat", "head", "tail", "grep", "wc", "stat", "file", "find", "sort", "tree",
    "which", "df", "du", "free", "ps",
    "ping", "curl", "nslookup",
    "python", "python3", "pip", "git", "node", "npm", "npx",
}

IS_WINDOWS = os.name == "nt"
SAFE_COMMANDS = _WINDOWS_COMMANDS if IS_WINDOWS else _POSIX_COMMANDS
HOST_SHELL = "Windows cmd.exe" if IS_WINDOWS else "Linux /bin/sh"

BLOCKED_COMMAND_PATTERNS = [
    r"\s-delete\b",
    r"\s-exec(?:dir)?\b",
    r"\bformat\b",
    r"\bdel\b",
    r"\berase\b",
    r"\brmdir\b",
    r"\bshutdown\b",
    r"\brestart\b",
    r"\bmkfs\b",
    r"\brm\s+-rf\b",
    r"system32",
    r"\breg\s+delete\b",
    r"\bnet\s+stop\b",
]

# The executor accepts one allowlisted process only; shell syntax is rejected.
BLOCKED_META_CHARS = ["&&", "||", "|", ">", ">>", "<", ";", "`", "$("]

# Interpreters, installers and anything that talks to an arbitrary host (ping and
# nslookup can leak data through the hostname they resolve) need confirmation.
_APPROVAL_COMMANDS = frozenset({"python", "python3", "pip", "node", "npm", "npx", "curl", "ping", "nslookup", "tracert"})
# File names that hold credentials. Reading them, even inside the repo, asks first.
_SECRET_NAME_PATTERNS = (
    ".env", ".env.*", "*.env", "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*", "id_ed25519*", "id_ecdsa*", "id_dsa*",
    "*credential*", "*token*", "*secret*", "*password*", ".netrc", ".git-credentials", "*.sqlite", "*.sqlite3", "*.db",
)
_SECRET_DIR_NAMES = frozenset({".ssh", ".gnupg", ".aws", ".kube", ".docker"})
_APPROVAL_WINDOWS_COMMANDS = frozenset({"taskkill", "route"})
_FIND_WRITE_OR_EXECUTE = frozenset({"-delete", "-fprint", "-fprint0", "-fprintf", "-fls"})
_GIT_DANGEROUS_OPTIONS = (
    "--output", "--ext-diff", "--no-ext-diff", "--textconv", "--exec", "--upload-pack",
    "--receive-pack", "--config", "core.sshcommand",
)


def _command_tokens(command: str) -> list[str]:
    """Parse exactly one command for subprocess(shell=False)."""
    return shlex.split(command, posix=not IS_WINDOWS)


def _path_candidates(args: list[str]) -> list[str]:
    """Every argument that could name a file: positional args and --opt=value."""
    out = []
    for arg in args:
        if arg.startswith("-"):
            if "=" in arg:
                out.append(arg.split("=", 1)[1])
            continue
        out.append(arg)
    return out


def _touches_outside_or_secret(args: list[str]) -> bool:
    """True if any argument resolves outside BASE_DIR or names a credential file.

    The shell tool runs with cwd=BASE_DIR, so relative paths resolve there.  A
    non-path argument (a grep pattern, `aux`) resolves inside the repo and stays
    AUTO; unparseable values fail closed.
    """
    import fnmatch
    root = BASE_DIR.resolve()
    for value in _path_candidates(args):
        if not value:
            continue
        try:
            raw = Path(os.path.expanduser(value))
            resolved = (raw if raw.is_absolute() else root / raw).resolve()
            rel = resolved.relative_to(root)
        except (ValueError, OSError, RuntimeError):
            return True
        parts = [part.lower() for part in rel.parts] + [Path(value).name.lower()]
        if any(part in _SECRET_DIR_NAMES for part in parts):
            return True
        if any(fnmatch.fnmatch(part, pattern) for part in parts for pattern in _SECRET_NAME_PATTERNS):
            return True
    return False


def shell_command_requires_approval(command: str) -> bool:
    """Return True unless this is a narrowly-defined inspection command.

    This is deliberately an allowlist rather than an attempt to recognize every
    mutating option supported by Git and POSIX tools.  Unknown syntax keeps the
    existing confirmation gate, rather than becoming an accidental bypass.
    """
    try:
        tokens = _command_tokens(command)
    except ValueError:
        return True
    if not tokens:
        return True
    base = Path(tokens[0]).name.lower()
    if base in _APPROVAL_COMMANDS or base in _APPROVAL_WINDOWS_COMMANDS:
        return True
    args = tokens[1:]
    if base != "git" and _touches_outside_or_secret(args):
        return True
    if base == "find":
        return any(arg in _FIND_WRITE_OR_EXECUTE or arg.startswith(("-exec", "-ok")) for arg in args)
    if base == "sort":
        return any(arg == "-o" or arg.startswith("--output=") for arg in args)
    if base == "tree":
        return any(arg == "-o" or arg.startswith("--output=") for arg in args)
    if base != "git":
        return False
    if any(arg == "-c" or arg.startswith("-c") or arg.startswith("--config=")
           or any(marker in arg.lower() for marker in _GIT_DANGEROUS_OPTIONS)
           for arg in args):
        return True
    # Git's global -C can change the target repository.  A repository within the
    # Ciel workspace remains an inspection target; all other destinations ask.
    filtered = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "-C":
            if index + 1 >= len(args):
                return True
            try:
                Path(args[index + 1]).resolve().relative_to(BASE_DIR.resolve())
            except ValueError:
                return True
            index += 2
            continue
        filtered.append(arg)
        index += 1
    if filtered == ["--version"]:
        return False
    if not filtered:
        return True
    subcommand, rest = filtered[0].lower(), filtered[1:]
    # Each accepted form below has no write mode and cannot invoke a helper.
    if subcommand in {"version", "rev-parse", "ls-files", "ls-tree"}:
        return False
    if subcommand == "status":
        return any(arg not in {"--short", "--branch", "--porcelain", "-s", "-b"} for arg in rest)
    if subcommand == "log":
        return any(arg not in {"--oneline", "--decorate", "--all", "--graph", "--stat", "--name-only"}
                   for arg in rest)
    if subcommand == "diff":
        return any(arg not in {"--stat", "--name-only", "--name-status", "--cached", "--staged"}
                   for arg in rest)
    if subcommand == "show":
        return any(arg not in {"--stat", "--name-only", "--name-status"} for arg in rest)
    if subcommand == "branch":
        return any(arg not in {"-a", "-r", "-v", "-vv", "--list", "--show-current"} for arg in rest)
    if subcommand == "remote":
        return not (rest in ([], ["-v"]) or (len(rest) == 2 and rest[0] in {"show", "get-url"}))
    return True


from skills._result import make_result as _make_result
from core.redact import redact_secrets


def _extract_base_command(command: str) -> str:
    parts = command.strip().split(maxsplit=1)
    if not parts:
        return ""
    return parts[0].lower()


def _validate_shell_command(command: str):
    if not command or not isinstance(command, str):
        return "INVALID_COMMAND", "Lệnh shell không hợp lệ hoặc đang trống."

    normalized = command.strip().lower()
    try:
        tokens = _command_tokens(command)
    except ValueError as exc:
        return "INVALID_COMMAND", f"Invalid shell command: {exc}"
    if not tokens:
        return "INVALID_COMMAND", "Shell command is empty."
    if Path(tokens[0]).name.lower() not in SAFE_COMMANDS:
        return "NOT_ALLOWLISTED", f"Command '{tokens[0]}' is not allowlisted."

    # Block dangerous meta-characters (but NOT pipe |)
    if any(token in normalized for token in BLOCKED_META_CHARS):
        return "UNSAFE_OPERATOR", f"Lệnh bị chặn vì chứa toán tử shell nguy hiểm ({', '.join(BLOCKED_META_CHARS)})."

    # Block dangerous command patterns
    for pattern in BLOCKED_COMMAND_PATTERNS:
        if re.search(pattern, normalized):
            return "BLOCKED_COMMAND", f"Lệnh '{command}' bị chặn vì khớp mẫu nguy hiểm: {pattern}"

    # Split by pipe and validate EACH segment independently
    segments = [seg.strip() for seg in command.split("|")]
    for segment in segments:
        base_cmd = _extract_base_command(segment)
        if not base_cmd:
            continue
        if base_cmd not in SAFE_COMMANDS:
            return "NOT_ALLOWLISTED", (
                f"Lệnh '{base_cmd}' chưa nằm trong allowlist an toàn. "
                f"Các lệnh được phép: {', '.join(sorted(SAFE_COMMANDS))}."
            )

    return None

def get_os_tools() -> dict:
    try:
        tools = []

        # 1. Chạy lệnh Shell (Quyền lực tối thượng)
        def execute_shell_command(command: str) -> str:
            try:
                validation = _validate_shell_command(command)
                if validation:
                    code, msg = validation
                    return _make_result(False, code=code, message=msg, tool_name="execute_shell_command")
                
                result = subprocess.run(_command_tokens(command), shell=False, capture_output=True,
                                        text=True, timeout=15, cwd=str(BASE_DIR))
                # Recursive reads (grep -r .) can still touch credential files the
                # path check cannot see; mask known secret values before the
                # output reaches the model, logs or Telegram.
                result.stdout = redact_secrets(result.stdout)
                result.stderr = redact_secrets(result.stderr)
                if result.returncode == 0:
                    return _make_result(
                        True,
                        data={
                            "message": f"Output của '{command}':\n{result.stdout}",
                            "stdout": result.stdout,
                            "stderr": result.stderr,
                            "returncode": result.returncode
                        },
                        tool_name="execute_shell_command"
                    )
                return _make_result(
                    False,
                    code="COMMAND_FAILED",
                    message=f"Lỗi thực thi '{command}':\n{result.stderr}",
                    tool_name="execute_shell_command"
                )
            except subprocess.TimeoutExpired:
                return _make_result(
                    False,
                    code="COMMAND_TIMEOUT",
                    message=f"[CẢNH BÁO] Lệnh '{command}' chạy quá 15 giây. Đã buộc ngắt.",
                    tool_name="execute_shell_command"
                )
            except Exception as e:
                return _make_result(False, code="COMMAND_EXCEPTION", message=str(e), tool_name="execute_shell_command")
                
        tools.append(StructuredTool.from_function(
            func=execute_shell_command, 
            name="execute_shell_command", 
            description=f"Execute one allowlisted {HOST_SHELL} command on the host and return the output. YOU MUST USE THIS TOOL when the Master asks to run a system command, ping, check IP, or interact with the OS."
        ))

        # 2. Chụp ảnh màn hình (Thị giác)
        def take_screenshot() -> str:
            if not PILLOW_AVAILABLE:
                return "Lỗi: Thư viện Pillow chưa được cài đặt. Master cần chạy 'pip install Pillow'."
            try:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filepath = SCREENSHOT_DIR / f"screenshot_{timestamp}.png"
                screenshot = ImageGrab.grab()
                screenshot.save(filepath)
                return f"Đã chụp màn hình thành công. File lưu tại: {filepath}"
            except Exception as e:
                return f"Lỗi khi chụp màn hình: {e}"
                
        tools.append(StructuredTool.from_function(
            func=take_screenshot, 
            name="take_screenshot", 
            description="Take a screenshot of the Master's current screen and save it to the workspace. YOU MUST USE THIS TOOL when the Master asks to capture the screen, take a picture of the screen, or see what is on the monitor."
        ))

        # 3. Mở phần mềm / File
        def open_application(target_path: str) -> str:
            try:
                if sys.platform == "win32":
                    os.startfile(target_path)
                elif sys.platform == "darwin": # macOS
                    subprocess.Popen(["open", target_path])
                else: # Linux
                    subprocess.Popen(["xdg-open", target_path])
                return f"Đã gửi lệnh mở: '{target_path}'. (Nếu ứng dụng tồn tại, nó sẽ hiện lên màn hình)."
            except Exception as e:
                return f"Không thể mở '{target_path}'. Lỗi: {e}"
                
        tools.append(StructuredTool.from_function(
            func=open_application, 
            name="open_application", 
            description="Open a file, folder, or application executable on the host machine. YOU MUST USE THIS TOOL when the Master asks to open an app, launch a program, or open a specific folder."
        ))

        host_rules = (
            f"3. HOST SHELL: {HOST_SHELL}. Use only commands for this OS. Allowed: "
            f"{', '.join(sorted(SAFE_COMMANDS))}. One command per call (a single | pipe is "
            f"fine); &&, ||, ;, >, <, and backticks are always rejected. Installing packages "
            f"(apt-get, winget, sudo) is not possible from this tool.\n"
        )
        return {"tools": tools, "prompt": OS_OPS_PROMPT + host_rules}

    except Exception as e:
        print(f"[Ciel System Error] Failed to arm OS toolkit: {e}")
        return {"tools": [], "prompt": ""}
