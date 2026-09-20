import json
import re
import ast
import math
from pathlib import Path
from datetime import datetime, timezone
import urllib.request
import urllib.error
import urllib.parse
from langchain_core.tools import StructuredTool

PRODUCTIVITY_PROMPT = """
[PRODUCTIVITY ARMORY]
You have tools for personal productivity, time, info lookup, calculation and file search:
1. `add_todo`: Add an unscheduled task to the todo list. It does NOT notify at a time.
2. `list_todos`: Show all current todos with status.
3. `complete_todo`: Mark a todo as done by its ID.
4. `get_current_time`: Get the current date and time.
5. `get_weather`: Get current weather for a city (uses wttr.in, simple text).
6. `calculate`: Safely evaluate math expressions (e.g. "2 + 2 * 3").
7. `grep_in_workspace`: Search for text pattern in workspace files (like grep).
Use these for loose planning, quick research, math, and searching code/notes.
For an alert at a specific time or after a delay, use `add_reminder` instead of `add_todo`.
Always confirm changes to the Master.
"""

BASE_DIR = Path(__file__).resolve().parent.parent.parent
WORKSPACE_DIR = BASE_DIR / "ciel_workspace"
WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
TODO_FILE = WORKSPACE_DIR / "todos.json"

def _load_todos():
    if TODO_FILE.exists():
        try:
            with open(TODO_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return []
    return []

def _save_todos(todos):
    with open(TODO_FILE, "w", encoding="utf-8") as f:
        json.dump(todos, f, indent=2, ensure_ascii=False)

_CALC_FUNCS = {
    "abs": abs, "round": round, "min": min, "max": max,
    "sum": sum, "len": len, "int": int, "float": float, "pow": pow,
    "sqrt": math.sqrt, "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "log": math.log, "log10": math.log10, "log2": math.log2, "exp": math.exp,
}
_CALC_ALLOWED_NODES = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
    ast.USub, ast.UAdd, ast.Call, ast.Load, ast.Tuple, ast.List,
)

def get_productivity_tools() -> dict:
    try:
        tools = []

        def add_todo(task: str) -> str:
            try:
                todos = _load_todos()
                todo_id = len(todos) + 1
                todos.append({"id": todo_id, "task": task, "done": False, "created": datetime.now().isoformat()})
                _save_todos(todos)
                return f"Đã thêm todo #{todo_id}: {task}"
            except Exception as e:
                return f"Lỗi thêm todo: {e}"
        tools.append(StructuredTool.from_function(
            func=add_todo,
            name="add_todo",
            description=(
                "Add an unscheduled checklist task. This does NOT send a timed notification; "
                "use add_reminder when the user asks to be alerted at a time or after a delay."
            )
        ))

        def list_todos() -> str:
            try:
                todos = _load_todos()
                if not todos:
                    return "Todo list hiện đang trống."
                lines = ["Danh sách Todo:"]
                for t in todos:
                    status = "✓" if t.get("done") else "○"
                    lines.append(f"#{t['id']} [{status}] {t['task']}")
                return "\n".join(lines)
            except Exception as e:
                return f"Lỗi lấy todo: {e}"
        tools.append(StructuredTool.from_function(
            func=list_todos,
            name="list_todos",
            description="List all todos with their status. USE THIS when asked about tasks, plans, or what to do."
        ))

        def complete_todo(todo_id: int) -> str:
            try:
                todos = _load_todos()
                for t in todos:
                    if t["id"] == todo_id:
                        t["done"] = True
                        _save_todos(todos)
                        return f"Đã hoàn thành todo #{todo_id}"
                return f"Không tìm thấy todo #{todo_id}"
            except Exception as e:
                return f"Lỗi hoàn thành todo: {e}"
        tools.append(StructuredTool.from_function(
            func=complete_todo,
            name="complete_todo",
            description="Mark a todo as completed by its numeric ID."
        ))

        def get_current_time() -> str:
            """Wall clock from the host OS — use before news/search that needs 'today'."""
            try:
                local = datetime.now().astimezone()
                utc = datetime.now(timezone.utc)
                # Master is Vietnam-local; CI runners are often UTC-only. Report both
                # so "hôm nay" for search is never taken from the model's training date.
                try:
                    from zoneinfo import ZoneInfo
                    vn = datetime.now(ZoneInfo("Asia/Ho_Chi_Minh"))
                    vn_line = (
                        f"Vietnam (Asia/Ho_Chi_Minh): {vn.strftime('%Y-%m-%d %H:%M:%S %Z')} "
                        f"— calendar date for 'hôm nay' / news: {vn.strftime('%Y-%m-%d')} "
                        f"({vn.strftime('%d/%m/%Y')})"
                    )
                except Exception:
                    vn_line = "Vietnam timezone unavailable on this host."
                return (
                    f"Thời gian hệ thống (source of truth — not model memory):\n"
                    f"- Local process: {local.strftime('%Y-%m-%d %H:%M:%S %Z')}\n"
                    f"- UTC: {utc.strftime('%Y-%m-%d %H:%M:%S %Z')}\n"
                    f"- {vn_line}\n"
                    f"When searching news for 'hôm nay', use the Vietnam calendar date above "
                    f"and stealth_search timelimit='d'."
                )
            except Exception as e:
                return f"Lỗi lấy thời gian: {e}"
        tools.append(StructuredTool.from_function(
            func=get_current_time,
            name="get_current_time",
            description=(
                "Get the real current date/time from the system clock (local, UTC, "
                "and Asia/Ho_Chi_Minh). YOU MUST USE THIS before answering 'what day "
                "is it', scheduling, or when a news/search query depends on 'today'/"
                "'hôm nay' and the date is not already given in the request."
            ),
        ))

        def get_weather(city: str) -> str:
            try:
                if not city:
                    city = "Hanoi"
                city_quoted = urllib.parse.quote(city.strip())
                url = f"https://wttr.in/{city_quoted}?format=3"
                with urllib.request.urlopen(url, timeout=10) as response:
                    data = response.read().decode("utf-8").strip()
                return f"Thời tiết ở {city}: {data}"
            except urllib.error.URLError as e:
                return f"Không lấy được thời tiết cho {city}: {e}"
            except Exception as e:
                return f"Lỗi thời tiết: {e}"
        tools.append(StructuredTool.from_function(
            func=get_weather,
            name="get_weather",
            description=(
                "Get CURRENT weather only for a city (e.g. 'Hanoi', 'Ho Chi Minh City', "
                "'London'). Uses wttr.in nowcast — NOT multi-day forecast. For "
                "'ngày mai' / 'dự báo' / 'tomorrow' or when Master says 'tra google', "
                "use stealth_search instead. City names with spaces are supported."
            ),
        ))

        def calculate(expression: str) -> str:
            try:
                tree = ast.parse(expression, mode='eval')
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call):
                        if not (isinstance(node.func, ast.Name) and node.func.id in _CALC_FUNCS):
                            return "Error: unsafe function call"
                    elif isinstance(node, ast.Name):
                        if node.id not in _CALC_FUNCS:
                            return f"Error: unknown name '{node.id}'"
                    elif not isinstance(node, _CALC_ALLOWED_NODES):
                        return "Error: unsafe expression"
                safe_dict = {"__builtins__": {}, **_CALC_FUNCS}
                result = eval(compile(tree, '<string>', 'eval'), safe_dict)
                return str(result)
            except Exception as e:
                return f"Error: {e}"
        tools.append(StructuredTool.from_function(
            func=calculate,
            name="calculate",
            description="Safely calculate math expressions like '2+2', 'sqrt(16)', 'sin(0)' etc. Use for any calculations."
        ))

        def grep_in_workspace(pattern: str, path: str = ".") -> str:
            try:
                safe_base = WORKSPACE_DIR
                target = (safe_base / path).resolve()
                if not target.is_relative_to(safe_base):
                    return "Error: path outside workspace"
                if target.is_file():
                    files = [target]
                else:
                    files = list(target.rglob("*")) if target.exists() else []
                matches = []
                regex = re.compile(pattern, re.IGNORECASE)
                for f in files:
                    if f.is_file() and f.suffix in ('.txt', '.py', '.md', '.json', '.log', '.csv'):
                        try:
                            with open(f, "r", encoding="utf-8", errors="ignore") as fh:
                                for i, line in enumerate(fh, 1):
                                    if regex.search(line):
                                        rel = f.relative_to(safe_base)
                                        matches.append(f"{rel}:{i}: {line.strip()[:100]}")
                        except:
                            pass
                if not matches:
                    return f"No matches for '{pattern}' in {path}"
                return "\n".join(matches[:20])  # limit
            except Exception as e:
                return f"Error: {e}"
        tools.append(StructuredTool.from_function(
            func=grep_in_workspace,
            name="grep_in_workspace",
            description="Search for regex pattern in workspace files (recursive). Great for finding code or notes. E.g. pattern='TODO', path='.' "
        ))

        return {
            "tools": tools,
            "prompt": PRODUCTIVITY_PROMPT
        }
    except Exception as e:
        print(f"[Productivity] Error loading tools: {e}")
        return {"tools": [], "prompt": ""}
