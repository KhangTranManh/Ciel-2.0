import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.tool_manager import ToolManager


def build_internal_tool_manager() -> ToolManager:
    ToolManager._load_external_tools = lambda self: None
    return ToolManager()


def test_execute_shell_command_blocks_dangerous_pattern():
    tm = build_internal_tool_manager()

    result = tm.execute_tool("execute_shell_command", {"command": "del important.txt"})

    assert isinstance(result, dict)
    assert result["success"] is False
    assert result["error"]["code"] in {"BLOCKED_COMMAND", "NOT_ALLOWLISTED"}


def test_execute_shell_command_blocks_unsafe_operator():
    tm = build_internal_tool_manager()

    result = tm.execute_tool("execute_shell_command", {"command": "echo hi && dir"})

    assert isinstance(result, dict)
    assert result["success"] is False
    assert result["error"]["code"] == "UNSAFE_OPERATOR"


def test_execute_shell_command_allows_safe_command():
    tm = build_internal_tool_manager()

    result = tm.execute_tool("execute_shell_command", {"command": "echo TOOL_OK"})
    text = tm.format_tool_result(result)

    assert result["success"] is True
    assert "TOOL_OK" in text


def test_tool_manager_invalid_args_contract():
    tm = build_internal_tool_manager()

    result = tm.execute_tool("execute_shell_command", {"cmd": "echo bad"})

    assert isinstance(result, dict)
    assert result["success"] is False
    assert result["error"]["code"] == "INVALID_ARGS"
    assert "Missing required argument" in result["error"]["message"]
