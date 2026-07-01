# skills/_result.py — Shared result helper for all Ciel skill modules.
#
# Centralizes the standardized result format expected by ToolManager.
# All skill modules should import from here instead of defining their own copy.
#
# Usage:
#   from skills._result import make_result


def make_result(success: bool, data=None, code: str = None, message: str = None, tool_name: str = "") -> dict:
    """Build a standardized result dict for ToolManager consumption.

    Args:
        success: Whether the operation succeeded.
        data: Arbitrary payload (dict, str, etc.) on success.
        code: Error code string on failure (e.g. "TOOL_ERROR").
        message: Human-readable error message on failure.
        tool_name: Name of the tool that produced this result.
    """
    return {
        "success": success,
        "data": data,
        "error": None if success else {
            "code": code or "TOOL_ERROR",
            "message": message or "Tool operation failed."
        },
        "meta": {
            "tool_name": tool_name
        }
    }
