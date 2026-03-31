import os
import sys
import subprocess
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

def get_os_tools() -> dict:
    try:
        tools = []

        # 1. Chạy lệnh Shell (Quyền lực tối thượng)
        def execute_shell_command(command: str) -> str:
            try:
                # Ngăn chặn các lệnh phá hoại cơ bản
                dangerous_keywords = ["rm -rf /", "format", "del /s /q c:\\windows", "mkfs"]
                if any(kw in command.lower() for kw in dangerous_keywords):
                    return f"[CẢNH BÁO BẢO MẬT] Lệnh '{command}' bị hệ thống chặn vì có rủi ro phá hoại hệ điều hành."
                
                result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=15)
                if result.returncode == 0:
                    return f"Output của '{command}':\n{result.stdout}"
                return f"Lỗi thực thi '{command}':\n{result.stderr}"
            except subprocess.TimeoutExpired:
                return f"[CẢNH BÁO] Lệnh '{command}' chạy quá 15 giây. Đã buộc ngắt."
            except Exception as e:
                return str(e)
                
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