from colorama import Fore, Style
import importlib
import json
import time
from pathlib import Path
from typing import Any


class ToolManager:
    def __init__(self):
        self.tools = []
        self.tool_map = {}
        self.system_prompts = []

        # Auto-discover and load all skill modules
        self._auto_load_skills("skills.internal", Path(__file__).resolve().parent.parent / "skills" / "internal")
        self._auto_load_skills("skills.external", Path(__file__).resolve().parent.parent / "skills" / "external")

    def _auto_load_skills(self, package_name: str, package_path: Path):
        """Auto-discover and load all skill modules from a package directory.
        
        Each skill module must expose a get_*_tools() function that returns
        {"tools": [StructuredTool, ...], "prompt": str}.
        """
        if not package_path.exists():
            return

        for py_file in sorted(package_path.glob("*.py")):
            modname = py_file.stem
            if modname.startswith("__"):
                continue

            try:
                module = importlib.import_module(f"{package_name}.{modname}")

                # Find the get_*_tools() factory function
                loader_fn = None
                for attr_name in dir(module):
                    if attr_name.startswith("get_") and attr_name.endswith("_tools") and callable(getattr(module, attr_name)):
                        loader_fn = getattr(module, attr_name)
                        break

                if loader_fn is None:
                    print(Fore.CYAN + f"[Ciel System] Skipped {modname} — no get_*_tools() factory found." + Style.RESET_ALL)
                    continue

                data = loader_fn()
                tools = data.get("tools", [])
                prompt = data.get("prompt", "")

                if tools:
                    self.tools.extend(tools)
                    if prompt:
                        self.system_prompts.append(prompt)
                    print(Fore.GREEN + f"[Ciel System] Loaded {modname}: {len(tools)} tool(s)." + Style.RESET_ALL)
                else:
                    print(Fore.CYAN + f"[Ciel System] {modname} returned 0 tools." + Style.RESET_ALL)

            except Exception as e:
                print(Fore.RED + f"[Ciel Warning] Failed to load {modname}: {e}" + Style.RESET_ALL)

    def get_tools(self) -> list:
        """Returns the list of validated tools to bind to the LLM."""
        self.tool_map = {tool.name.lower(): tool for tool in self.tools}
        return self.tools

    def get_dynamic_prompt(self) -> str:
        """Stitches all tool manuals together into one string."""
        if not self.system_prompts:
            return ""
        
        combined_prompts = "\n\n--- ACTIVE WEAPON MANUALS ---\n\n".join(self.system_prompts)
        return f"\n\n{combined_prompts}"

    def _make_result(self, success: bool, tool_name: str, duration_ms: int, data: Any = None, error_code: str = None, error_message: str = None) -> dict:
        return {
            "success": success,
            "data": data,
            "error": None if success else {"code": error_code or "TOOL_ERROR", "message": error_message or "Tool execution failed."},
            "meta": {
                "tool_name": tool_name,
                "duration_ms": duration_ms
            }
        }

    def _validate_tool_args(self, name: str, args: dict):
        """Auto-validate tool arguments using the tool's own Pydantic args_schema.
        
        No hardcoded schemas needed — every StructuredTool carries its own
        args_schema derived from the function signature.
        """
        tool = self.tool_map.get(name)
        if not tool:
            return None  # Tool not found, will be caught later

        if not hasattr(tool, 'args_schema') or not tool.args_schema:
            return None  # No schema available, skip validation

        if args is None:
            args = {}

        if not isinstance(args, dict):
            return f"Invalid arguments type for '{name}': expected object/dict."

        try:
            tool.args_schema(**args)
            return None  # Validation passed
        except Exception as e:
            # Extract a clean error message from Pydantic validation
            return f"Invalid arguments for '{name}': {e}"

    def format_tool_result(self, result: dict) -> str:
        if not isinstance(result, dict):
            return str(result)

        if result.get("success"):
            data = result.get("data")
            if isinstance(data, dict) and "message" in data:
                return str(data["message"])
            if isinstance(data, str):
                return data
            return json.dumps(data, ensure_ascii=False, indent=2) if data is not None else "Tool executed successfully."

        err = result.get("error") or {}
        code = err.get("code", "TOOL_ERROR")
        msg = err.get("message", "Unknown tool error")
        return f"[{code}] {msg}"

    def execute_tool(self, name: str, args: dict) -> dict:
        """Executes a tool by its name and returns a standardized structured result."""
        tool_name = (name or "").lower()
        start = time.perf_counter()

        if not self.tool_map:
            self.get_tools()

        if tool_name not in self.tool_map:
            duration_ms = int((time.perf_counter() - start) * 1000)
            return self._make_result(False, tool_name, duration_ms, error_code="TOOL_NOT_FOUND", error_message=f"Tool '{name}' does not exist or was quarantined.")

        validation_error = self._validate_tool_args(tool_name, args)
        if validation_error:
            duration_ms = int((time.perf_counter() - start) * 1000)
            return self._make_result(False, tool_name, duration_ms, error_code="INVALID_ARGS", error_message=validation_error)

        try:
            invoke_args = args if isinstance(args, dict) else {}
            raw_result = self.tool_map[tool_name].invoke(invoke_args)
            duration_ms = int((time.perf_counter() - start) * 1000)

            if isinstance(raw_result, dict) and {"success", "data", "error"}.issubset(raw_result.keys()):
                normalized = dict(raw_result)
                meta = normalized.get("meta") or {}
                meta.setdefault("tool_name", tool_name)
                meta.setdefault("duration_ms", duration_ms)
                normalized["meta"] = meta
                return normalized

            if raw_result is None:
                return self._make_result(True, tool_name, duration_ms, data={"message": f"Successfully executed tool: {tool_name}."})

            if isinstance(raw_result, str):
                return self._make_result(True, tool_name, duration_ms, data={"message": raw_result})

            return self._make_result(True, tool_name, duration_ms, data=raw_result)

        except Exception as e:
            duration_ms = int((time.perf_counter() - start) * 1000)
            return self._make_result(False, tool_name, duration_ms, error_code="EXECUTION_ERROR", error_message=str(e))