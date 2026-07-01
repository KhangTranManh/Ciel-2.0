import os
import shutil
import subprocess
from pathlib import Path
from langchain_core.tools import StructuredTool

SYSTEM_OPS_PROMPT = """
[LOCAL SYSTEM ARMORY & PARANOIA PROTOCOL]
You possess tools to interact with the Master's local machine inside the Quarantine Zone.
1. `list_workspace`: List all files and directories.
2. `read_file`: Read file content.
3. `write_file`: Create or completely overwrite a file.
4. `append_file`: Add content to the end of a file.
5. `delete_file`: Delete file or folder.
6. `get_file_info`: Check file size and type.
7. `run_python_script`: Run a .py script and get terminal output.

[STRICT SECURITY RULES - PARANOIA]
1. You can write to either `ciel_workspace/` (user data) or `agent_output/` (generated code).
2. If the user did not specify the destination path in their request, the system will ask them first before writing.
3. If the path is already in the question, use it directly.
4. DESTRUCTIVE ACTIONS: Always think carefully before using `delete_file` or `write_file`. Prefer `append_file` if modifying existing logic.
5. REPORTING: Report system operations clearly in Vietnamese.
6. EXTERNAL COMMUNICATIONS: Never disclose internal paths (agent_output/, ciel_workspace/, etc.) in emails or messages sent to external recipients. Use only generic terms like "the detailed report" or include content directly in the message.
"""

BASE_DIR = Path(__file__).resolve().parent.parent.parent
WORKSPACE_DIR = BASE_DIR / "ciel_workspace"
WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
AGENT_OUTPUT_DIR = BASE_DIR / "agent_output"
AGENT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

def _resolve_target_path(target_path: str) -> Path:
    """Resolve path allowing both ciel_workspace and agent_output based on prefix."""
    target_path = target_path.strip().replace("\\", "/")
    if target_path.startswith("agent_output/"):
        rel = target_path[len("agent_output/"):].lstrip("/")
        base = AGENT_OUTPUT_DIR
    elif target_path.startswith("ciel_workspace/"):
        rel = target_path[len("ciel_workspace/"):].lstrip("/")
        base = WORKSPACE_DIR
    else:
        # default to workspace for safety if no prefix given
        rel = target_path.lstrip("/")
        base = WORKSPACE_DIR

    resolved = (base / rel).resolve()
    if not resolved.is_relative_to(base):
        raise PermissionError(f"[CẢNH BÁO BẢO MẬT] Truy cập bị từ chối. Lệnh '{target_path}' nhắm ra ngoài vùng cho phép.")
    return resolved

def _is_safe_path(target_path: str) -> Path:
    try:
        return _resolve_target_path(target_path)
    except PermissionError:
        raise
    except Exception as e:
        raise PermissionError(f"Đường dẫn không hợp lệ: {e}")

def get_system_tools() -> dict:
    try:
        tools = []

        def list_workspace() -> str:
            try:
                items = list(WORKSPACE_DIR.glob("**/*"))
                if not items: return "Workspace hiện đang trống."
                res = ["Danh sách Vùng Cách Ly:"]
                for item in items:
                    res.append(f"[{'DIR ' if item.is_dir() else 'FILE'}] {item.relative_to(WORKSPACE_DIR)}")
                return "\n".join(res)
            except Exception as e: return f"Lỗi: {e}"
        tools.append(StructuredTool.from_function(func=list_workspace, name="list_workspace", description="List all files and directories in the workspace. YOU MUST USE THIS TOOL when asked to see what files exist or check the workspace."))

        def read_file(filename: str) -> str:
            try:
                safe_path = _is_safe_path(filename)
                if not safe_path.exists(): return f"Lỗi: '{filename}' không tồn tại."
                if not safe_path.is_file(): return f"Lỗi: '{filename}' là thư mục."
                with open(safe_path, "r", encoding="utf-8") as f: return f.read()
            except Exception as e: return str(e)
        tools.append(StructuredTool.from_function(func=read_file, name="read_file", description="Read the exact content of a file. YOU MUST USE THIS TOOL when asked to read, view, or check what is written inside a file."))

        def write_file(filename: str, content: str) -> str:
            try:
                safe_path = _is_safe_path(filename)
                safe_path.parent.mkdir(parents=True, exist_ok=True)
                with open(safe_path, "w", encoding="utf-8") as f: f.write(content)
                return f"Đã GHI ĐÈ thành công vào '{filename}'."
            except Exception as e: return str(e)
        tools.append(StructuredTool.from_function(func=write_file, name="write_file", description="Create a new file or completely overwrite an existing one. The system will ask for the target path (ciel_workspace/ or agent_output/) if not specified in the request."))

        def append_file(filename: str, content: str) -> str:
            try:
                safe_path = _is_safe_path(filename)
                safe_path.parent.mkdir(parents=True, exist_ok=True)
                with open(safe_path, "a", encoding="utf-8") as f: f.write(content + "\n")
                return f"Đã GHI NỐI THÊM vào cuối file '{filename}'."
            except Exception as e: return str(e)
        tools.append(StructuredTool.from_function(func=append_file, name="append_file", description="Append text to the end of an existing file. YOU MUST USE THIS TOOL when asked to add a line, update, or append data to a file."))

        def delete_file(filename: str) -> str:
            try:
                safe_path = _is_safe_path(filename)
                if not safe_path.exists(): return f"'{filename}' không tồn tại để xóa."
                if safe_path.is_dir():
                    shutil.rmtree(safe_path)
                    return f"Đã XÓA TOÀN BỘ thư mục '{filename}'."
                safe_path.unlink()
                return f"Đã XÓA file '{filename}'."
            except Exception as e: return str(e)
        tools.append(StructuredTool.from_function(func=delete_file, name="delete_file", description="Delete a file or folder. YOU MUST USE THIS TOOL when asked to remove, delete, or clean up files."))

        def get_file_info(filename: str) -> str:
            try:
                safe_path = _is_safe_path(filename)
                if not safe_path.exists(): return f"'{filename}' không tồn tại."
                stats = safe_path.stat()
                return f"Thông tin '{filename}': {'Thư mục' if safe_path.is_dir() else 'File'} | {stats.st_size / 1024:.2f} KB"
            except Exception as e: return str(e)
        tools.append(StructuredTool.from_function(func=get_file_info, name="get_file_info", description="Get file size, type, and stats. YOU MUST USE THIS TOOL when asked about file capacity, size, or file information."))

        def run_python_script(filename: str) -> str:
            try:
                safe_path = _is_safe_path(filename)
                if not safe_path.exists() or safe_path.suffix != '.py': return f"Lỗi: Không tìm thấy script '{filename}'."
                result = subprocess.run(["python", str(safe_path)], capture_output=True, text=True, timeout=10)
                if result.returncode == 0: return f"Output của '{filename}':\n{result.stdout}"
                return f"Lỗi chạy script '{filename}':\n{result.stderr}"
            except subprocess.TimeoutExpired: return f"[CẢNH BÁO] Script '{filename}' chạy quá 10 giây. Đã buộc ngắt."
            except Exception as e: return str(e)
        tools.append(StructuredTool.from_function(func=run_python_script, name="run_python_script", description="Execute a Python script (.py) and return the terminal output. YOU MUST USE THIS TOOL when asked to run, execute, or test a python code file."))

        return {"tools": tools, "prompt": SYSTEM_OPS_PROMPT}

    except Exception as e:
        print(f"[Ciel System Error] Failed to arm System toolkit: {e}")
        return {"tools": [], "prompt": ""}