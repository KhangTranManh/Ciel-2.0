# skills/_result.py — Shared result helper for all Ciel skill modules.
#
# Centralizes the standardized result format expected by ToolManager.
# All skill modules should import from here instead of defining their own copy.
#
# Usage:
#   from skills._result import make_result


def make_result(success: bool, data=None, code: str = None, message: str = None,
                tool_name: str = "", confirm: dict = None) -> dict:
    """Build a standardized result dict for ToolManager consumption.

    Args:
        success: Whether the operation succeeded.
        data: Arbitrary payload (dict, str, etc.) on success.
        code: Error code string on failure (e.g. "TOOL_ERROR").
        message: Human-readable error message on failure.
        tool_name: Name of the tool that produced this result.
        confirm: Set by a PREVIEW-ONLY tool to declare the follow-up action a bare
            "yes" should resolve to: {"tool": "<confirm_tool_name>", "args": {...}}.
            This is the ONLY place that pairing is defined — core reads it generically,
            so any skill can opt into deterministic pending-confirmation without
            touching core/llm_connector.py at all.
    """
    result = {
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
    if success and confirm:
        result["confirm"] = confirm
    return result
