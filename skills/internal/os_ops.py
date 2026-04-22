import os
import sys
import subprocess
import re
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

SAFE_COMMANDS = {
    "echo", "dir", "type", "whoami", "hostname", "ver", "ipconfig",
    "ping", "systeminfo", "tasklist", "where", "cd", "cls"
}

BLOCKED_COMMAND_PATTERNS = [
    r"\bformat\b",
    r"\bdel\b",
    r"\berase\b",
    r"\brmdir\b",
    r"\bshutdown\b",
    r"\brestart\b",
    r"\bmkfs\b",
    r"\brm\s+-rf\b",
    r"system32",
]

BLOCKED_META_CHARS = ["&&", "||", "|", ">", "<", ";", "`"]


def _make_result(success: bool, data=None, code: str = None, message: str = None, tool_name: str = "") -> dict:
    return {
        "success": success,
        "data": data,
        "error": None if success else {
            "code": code or "OS_OPS_ERROR",
            "message": message or "OS operation failed."
        },
        "meta": {
            "tool_name": tool_name
        }
    }


def _extract_base_command(command: str) -> str:
    parts = command.strip().split(maxsplit=1)
    if not parts:
        return ""
    return parts[0].lower()


def _validate_shell_command(command: str):
    if not command or not isinstance(command, str):
        return "INVALID_COMMAND", "Lệnh shell không hợp lệ hoặc đang trống."

    normalized = command.strip().lower()
    if any(token in normalized for token in BLOCKED_META_CHARS):
        return "UNSAFE_OPERATOR", "Lệnh bị chặn vì chứa toán tử shell nguy hiểm (&&, ||, |, >, <, ;, `)."

    for pattern in BLOCKED_COMMAND_PATTERNS:
        if re.search(pattern, normalized):
            return "BLOCKED_COMMAND", f"Lệnh '{command}' bị chặn vì khớp mẫu nguy hiểm: {pattern}"

    base_command = _extract_base_command(command)
    if base_command not in SAFE_COMMANDS:
        return "NOT_ALLOWLISTED", (
            f"Lệnh gốc '{base_command}' chưa nằm trong allowlist an toàn. "
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
                
                result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=15)
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
            description="Execute a CMD, PowerShell, or Bash command on the host OS and return the output. YOU MUST USE THIS TOOL when the Master asks to run a system command, ping, check IP, or interact with the OS."
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

        return {"tools": tools, "prompt": OS_OPS_PROMPT}

    except Exception as e:
        print(f"[Ciel System Error] Failed to arm OS toolkit: {e}")
        return {"tools": [], "prompt": ""}