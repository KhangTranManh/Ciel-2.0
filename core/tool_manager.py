from colorama import Fore, Style
import json
import time
from typing import Any

class ToolManager:
    def __init__(self):
        self.tools = []
        self.tool_map = {}
        self.system_prompts = [] # NEW: We now collect prompts from the tools
        self.tool_arg_schemas = {
            "execute_shell_command": {
                "required": {"command": str},
                "optional": {}
            },
            "open_application": {
                "required": {"target_path": str},
                "optional": {}
            },
            "list_workspace": {
                "required": {},
                "optional": {}
            },
            "read_file": {
                "required": {"filename": str},
                "optional": {}
            },
            "write_file": {
                "required": {"filename": str, "content": str},
                "optional": {}
            },
            "append_file": {
                "required": {"filename": str, "content": str},
                "optional": {}
            },
            "delete_file": {
                "required": {"filename": str},
                "optional": {}
            },
            "get_file_info": {
                "required": {"filename": str},
                "optional": {}
            },
            "run_python_script": {
                "required": {"filename": str},
                "optional": {}
            },
            "save_fact": {
                "required": {"key": str, "value": str},
                "optional": {}
            },
            "get_fact": {
                "required": {"key": str},
                "optional": {}
            },
            "delete_fact": {
                "required": {"key": str},
                "optional": {}
            },
            "take_screenshot": {
                "required": {},
                "optional": {}
            },
            "git_list_repos": {
                "required": {"search_path": str},
                "optional": {}
            },
            "git_status": {
                "required": {"repo_path": str},
                "optional": {}
            },
            "git_diff": {
                "required": {"repo_path": str},
                "optional": {}
            },
            "git_commit_and_push": {
                "required": {"repo_path": str, "message": str},
                "optional": {}
            },
            "git_confirm_push": {
                "required": {"repo_path": str, "message": str},
                "optional": {}
            }
        }
        
        # Load zones independently to prevent cross-corruption
        self._load_internal_tools()
        self._load_external_tools()

    def _load_internal_tools(self):
        # 1. NẠP CÁC CÔNG CỤ BỘ NHỚ CŨ (Nếu ngài vẫn đang dùng)
        try:
            from skills.internal.memory_ops import save_fact, get_fact, delete_fact
            self.tools.extend([save_fact, get_fact, delete_fact])
            print(Fore.GREEN + "[Ciel System] Internal memory tools loaded safely." + Style.RESET_ALL)
        except Exception as e:
            pass # Bỏ qua nếu ngài đã xóa file memory_ops

        # ==========================================
        # 2. NẠP KHO VŨ KHÍ HỆ THỐNG (QUARANTINE ZONE)
        # ==========================================
        try:
            from skills.internal.system_ops import get_system_tools
            sys_data = get_system_tools()
            
            sys_tools = sys_data.get("tools", [])
            sys_prompt = sys_data.get("prompt", "")
            
            if sys_tools:
                self.tools.extend(sys_tools)
                if sys_prompt:
                    self.system_prompts.append(sys_prompt) # Nạp chỉ thị Paranoia vào não Ciel
                print(Fore.GREEN + "[Ciel System] Local System Armory (Quarantine Zone) locked and loaded." + Style.RESET_ALL)
            else:
                print(Fore.CYAN + "[Ciel System] No System tools loaded." + Style.RESET_ALL)
                
        except Exception as e:
            print(Fore.RED + f"[Ciel Fatal] Failed to load System tools: {e}" + Style.RESET_ALL)

        # ==========================================
        # 3. NẠP KHO VŨ KHÍ OS DIRECT CONTROL
        # ==========================================
        try:
            from skills.internal.os_ops import get_os_tools
            os_data = get_os_tools()

            os_tools = os_data.get("tools", [])
            os_prompt = os_data.get("prompt", "")

            if os_tools:
                self.tools.extend(os_tools)
                if os_prompt:
                    self.system_prompts.append(os_prompt)
                print(Fore.GREEN + "[Ciel System] OS Direct Control Armory loaded." + Style.RESET_ALL)
            else:
                print(Fore.CYAN + "[Ciel System] No OS Direct Control tools loaded." + Style.RESET_ALL)

        except Exception as e:
            print(Fore.RED + f"[Ciel Warning] Failed to load OS Direct Control tools: {e}" + Style.RESET_ALL)

    def _load_external_tools(self):
        try:
            from skills.external.gmail_ops import get_gmail_tools
            gmail_data = get_gmail_tools() # This now returns a dictionary!
            
            # Extract tools and prompt
            gmail_tools = gmail_data.get("tools", [])
            gmail_prompt = gmail_data.get("prompt", "")
            
            if gmail_tools:
                self.tools.extend(gmail_tools)
                if gmail_prompt:
                    self.system_prompts.append(gmail_prompt) # Add the manual to our collection
                print(Fore.GREEN + "[Ciel System] Gmail armory fully loaded and operational." + Style.RESET_ALL)
            else:
                print(Fore.CYAN + "[Ciel System] No external tools loaded." + Style.RESET_ALL)
                
        except Exception as e:
            print(f"[Ciel Warning] External tool corruption detected. Error: {e}")
        try:
            from skills.external.trading_ops import get_trading_tools
            trading_data = get_trading_tools()
            
            trading_tools = trading_data.get("tools", [])
            trading_prompt = trading_data.get("prompt", "")
            
            if trading_tools:
                self.tools.extend(trading_tools)
                if trading_prompt:
                    self.system_prompts.append(trading_prompt)
                print(Fore.GREEN + "[Ciel System] Trading armory fully loaded and operational." + Style.RESET_ALL)
            else:
                print(Fore.CYAN + "[Ciel System] No Trading tools loaded." + Style.RESET_ALL)
                
        except Exception as e:
            print(Fore.RED + f"[Ciel Warning] Trading tool corruption detected. Error: {e}" + Style.RESET_ALL)
            print(Fore.CYAN + "[Ciel System] External tools registry is empty." + Style.RESET_ALL)

        # ==========================================
        # 3. GIT VERSION CONTROL ARMORY
        # ==========================================
        try:
            from skills.external.github_ops import get_github_tools
            github_data = get_github_tools()

            github_tools = github_data.get("tools", [])
            github_prompt = github_data.get("prompt", "")

            if github_tools:
                self.tools.extend(github_tools)
                if github_prompt:
                    self.system_prompts.append(github_prompt)
                print(Fore.GREEN + "[Ciel System] Git armory fully loaded and operational." + Style.RESET_ALL)
            else:
                print(Fore.CYAN + "[Ciel System] No Git tools loaded." + Style.RESET_ALL)

        except Exception as e:
            print(Fore.RED + f"[Ciel Warning] Git tool corruption detected. Error: {e}" + Style.RESET_ALL)

    def get_tools(self) -> list:
        """Returns the list of validated tools to bind to the LLM."""
        self.tool_map = {tool.name.lower(): tool for tool in self.tools}
        return self.tools

    def get_dynamic_prompt(self) -> str:
        """NEW: Stitches all tool manuals together into one string."""
        if not self.system_prompts:
            return ""
        
        # Join all the prompts we collected with a nice divider
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
        schema = self.tool_arg_schemas.get(name)
        if not schema:
            return None

        if args is None:
            args = {}

        if not isinstance(args, dict):
            return f"Invalid arguments type for '{name}': expected object/dict."

        required = schema.get("required", {})
        optional = schema.get("optional", {})
        allowed_keys = set(required.keys()) | set(optional.keys())

        missing = [k for k in required if k not in args]
        if missing:
            return f"Missing required argument(s) for '{name}': {', '.join(missing)}."

        unknown = [k for k in args.keys() if k not in allowed_keys]
        if unknown:
            return f"Unknown argument(s) for '{name}': {', '.join(unknown)}."

        for key, expected_type in required.items():
            if not isinstance(args.get(key), expected_type):
                return f"Argument '{key}' for '{name}' must be of type {expected_type.__name__}."

        for key, expected_type in optional.items():
            if key in args and not isinstance(args.get(key), expected_type):
                return f"Argument '{key}' for '{name}' must be of type {expected_type.__name__}."

        return None

    def format_tool_result(self, result: dict) -> str:
        if not isinstance(result, dict):
            return str(result)

        if result.get("success"):
            data = result.get("data")
            if isinstance(data, dict) and "message" in data and len(data) == 1:
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