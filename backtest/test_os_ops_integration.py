import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.tool_manager import ToolManager


def main():
    # Isolate this test to internal tools only (avoid external auth/network side-effects)
    ToolManager._load_external_tools = lambda self: None

    tm = ToolManager()
    tools = tm.get_tools()
    tool_names = {t.name for t in tools}

    required = {"execute_shell_command", "take_screenshot", "open_application"}
    missing = sorted(required - tool_names)

    print("[INFO] Loaded tool count:", len(tool_names))
    print("[INFO] OS tools present:", sorted(required & tool_names))

    if missing:
        print("[FAIL] Missing OS tools:", missing)
        raise SystemExit(1)

    # Smoke test: execute_shell_command (safe read-only command)
    result = tm.execute_tool("execute_shell_command", {"command": "echo CIEL_OS_OK"})
    result_text = tm.format_tool_result(result)
    print("[INFO] execute_shell_command result:")
    print(result_text)

    if not isinstance(result, dict) or "success" not in result:
        print("[FAIL] execute_shell_command did not return standardized contract.")
        raise SystemExit(1)

    if not result.get("success"):
        print("[FAIL] execute_shell_command failed unexpectedly:", result.get("error"))
        raise SystemExit(1)

    if "CIEL_OS_OK" not in result_text:
        print("[FAIL] execute_shell_command output did not contain expected marker.")
        raise SystemExit(1)

    # Non-destructive screenshot test:
    # - Pass if screenshot succeeds, OR if Pillow is not installed and tool reports that clearly.
    screenshot_result = tm.execute_tool("take_screenshot", {})
    screenshot_text = tm.format_tool_result(screenshot_result)
    print("[INFO] take_screenshot result:")
    print(screenshot_text)

    if not isinstance(screenshot_result, dict) or "success" not in screenshot_result:
        print("[FAIL] take_screenshot did not return standardized contract.")
        raise SystemExit(1)

    if (
        "Đã chụp màn hình thành công" not in screenshot_text
        and "Pillow chưa được cài đặt" not in screenshot_text
    ):
        print("[FAIL] take_screenshot returned unexpected output.")
        raise SystemExit(1)

    print("[PASS] os_ops integration is working.")


if __name__ == "__main__":
    main()
