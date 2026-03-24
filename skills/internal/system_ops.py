import os
import shutil
import subprocess
from pathlib import Path
from langchain_core.tools import StructuredTool

# ==========================================
# PHẦN 1: LINH HỒN (SYSTEM SENTINEL PROMPT)
# ==========================================
SYSTEM_OPS_PROMPT = """
[LOCAL SYSTEM ARMORY & PARANOIA PROTOCOL]
You possess tools to interact with the Master's local machine inside the Quarantine Zone:
1. `list_workspace`: Liệt kê tất cả file và thư mục.
2. `read_file`: Đọc nội dung file.
3. `write_file`: Tạo mới hoặc GHI ĐÈ toàn bộ nội dung file.
4. `append_file`: Ghi NỐI THÊM nội dung vào cuối file (Dùng khi update code hoặc ghi log).
5. `delete_file`: Xóa file hoặc thư mục.
6. `get_file_info`: Kiểm tra dung lượng và loại tệp.
7. `run_python_script`: Chạy file script .py và trả về kết quả terminal.

[STRICT SECURITY RULES - PARANOIA]
1. QUARANTINE ZONE: You are physically locked inside the `ciel_workspace` directory. Do NOT attempt to access files outside this folder.
2. DESTRUCTIVE ACTIONS: Always think carefully before using `delete_file` or `write_file`. Prefer `append_file` if modifying existing logic.
3. REPORTING: Report system operations clearly in Vietnamese.
"""

# Định vị Vùng Cách Ly
BASE_DIR = Path(__file__).resolve().parent.parent.parent
WORKSPACE_DIR = BASE_DIR / "ciel_workspace"
WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)

def _is_safe_path(target_path: str) -> Path:
    """Thuật toán Chống Vượt Ngục (Anti-Directory Traversal)."""
    try:
        resolved_path = (WORKSPACE_DIR / target_path).resolve()
        if not str(resolved_path).startswith(str(WORKSPACE_DIR)):
            raise PermissionError(f"[CẢNH BÁO BẢO MẬT] Truy cập bị từ chối. Lệnh '{target_path}' nhắm ra ngoài Vùng Cách Ly.")
        return resolved_path
    except Exception as e:
        raise PermissionError(f"Đường dẫn không hợp lệ: {e}")

def get_system_tools() -> dict:
    try:
        tools = []

        # 1. Liệt kê
        def list_workspace() -> str:
            try:
                items = list(WORKSPACE_DIR.glob("**/*"))
                if not items: return "Workspace hiện đang trống."
                res = ["Danh sách Vùng Cách Ly:"]
                for item in items:
                    res.append(f"[{'DIR ' if item.is_dir() else 'FILE'}] {item.relative_to(WORKSPACE_DIR)}")
                return "\n".join(res)
            except Exception as e: return f"Lỗi: {e}"
        tools.append(StructuredTool.from_function(func=list_workspace, name="list_workspace", description="Liệt kê file/thư mục trong workspace."))

        # 2. Đọc file
        def read_file(filename: str) -> str:
            try:
                safe_path = _is_safe_path(filename)
                if not safe_path.exists(): return f"Lỗi: '{filename}' không tồn tại."
                if not safe_path.is_file(): return f"Lỗi: '{filename}' là thư mục."
                with open(safe_path, "r", encoding="utf-8") as f: return f.read()
            except Exception as e: return str(e)
        tools.append(StructuredTool.from_function(func=read_file, name="read_file", description="Đọc nội dung file."))

        # 3. Ghi đè file
        def write_file(filename: str, content: str) -> str:
            try:
                safe_path = _is_safe_path(filename)
                safe_path.parent.mkdir(parents=True, exist_ok=True)
                with open(safe_path, "w", encoding="utf-8") as f: f.write(content)
                return f"Đã GHI ĐÈ thành công vào '{filename}'."
            except Exception as e: return str(e)
        tools.append(StructuredTool.from_function(func=write_file, name="write_file", description="Ghi đè hoặc tạo mới file."))

        # 4. Ghi nối tiếp (MỚI)
        def append_file(filename: str, content: str) -> str:
            try:
                safe_path = _is_safe_path(filename)
                safe_path.parent.mkdir(parents=True, exist_ok=True)
                with open(safe_path, "a", encoding="utf-8") as f: f.write(content + "\n")
                return f"Đã GHI NỐI THÊM vào cuối file '{filename}'."
            except Exception as e: return str(e)
        tools.append(StructuredTool.from_function(func=append_file, name="append_file", description="Ghi nối thêm nội dung vào cuối file đã có."))

        # 5. Xóa file/thư mục (MỚI)
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
        tools.append(StructuredTool.from_function(func=delete_file, name="delete_file", description="Xóa file hoặc thư mục (Cẩn thận)."))

        # 6. Lấy thông tin (MỚI)
        def get_file_info(filename: str) -> str:
            try:
                safe_path = _is_safe_path(filename)
                if not safe_path.exists(): return f"'{filename}' không tồn tại."
                stats = safe_path.stat()
                return f"Thông tin '{filename}': {'Thư mục' if safe_path.is_dir() else 'File'} | {stats.st_size / 1024:.2f} KB"
            except Exception as e: return str(e)
        tools.append(StructuredTool.from_function(func=get_file_info, name="get_file_info", description="Xem kích thước và loại tệp."))

        # 7. Chạy Python
        def run_python_script(filename: str) -> str:
            try:
                safe_path = _is_safe_path(filename)
                if not safe_path.exists() or safe_path.suffix != '.py': return f"Lỗi: Không tìm thấy script '{filename}'."
                result = subprocess.run(["python", str(safe_path)], capture_output=True, text=True, timeout=10)
                if result.returncode == 0: return f"Output của '{filename}':\n{result.stdout}"
                return f"Lỗi chạy script '{filename}':\n{result.stderr}"
            except subprocess.TimeoutExpired: return f"[CẢNH BÁO] Script '{filename}' chạy quá 10 giây. Đã buộc ngắt."
            except Exception as e: return str(e)
        tools.append(StructuredTool.from_function(func=run_python_script, name="run_python_script", description="Chạy file .py và trả về output terminal."))

        return {"tools": tools, "prompt": SYSTEM_OPS_PROMPT}

    except Exception as e:
        print(f"[Ciel System Error] Failed to arm System toolkit: {e}")
        return {"tools": [], "prompt": ""}