from agent_system.models.worker import Worker
import json
import os

class RecoveryManager:
    """Analyzes errors from tools and attempts to self-heal."""
    
    def __init__(self, worker: Worker, log_thought_fn):
        self.worker = worker
        self.log_thought = log_thought_fn

    def heal_tool_error(self, tool_name: str, tool_args: dict, result_text: str, attempt: int = 1, previous_code: str = "") -> tuple[bool, str, dict]:
        """
        Attempts to fix a tool error.
        Returns (Success, ActionType, ActionData)
        ActionType can be "code_fix", "retry_tool", or "failed"
        """
        self.log_thought("HEALING", "detect_error", result_text)
        try:
            if tool_name == "run_python_script" and "filename" in tool_args:
                if not previous_code:
                    try:
                        from skills.internal.system_ops import _is_safe_path
                        safe_path = _is_safe_path(tool_args["filename"])
                        if os.path.exists(safe_path):
                            with open(safe_path, "r", encoding="utf-8") as f:
                                previous_code = f.read()
                    except Exception:
                        pass

                fix_task = (
                    f"### ROLE: ROBUST OS DEVELOPER\n"
                    f"When rewriting code to fix OS errors:\n"
                    f"1. Implement error handling for FileNotFoundError and PermissionError.\n"
                    f"2. Use 'os.path.normpath' to ensure slashes are correct for Windows (\\).\n"
                    f"3. If the error is 'Access Denied', suggest using '--user' for pip or checking Administrator privileges.\n\n"
                    f"### CONTEXT:\n"
                    f"- ORIGINAL GOAL: Fix the script '{tool_args['filename']}'\n"
                    f"- ATTEMPT NUMBER: {attempt} / 3\n"
                    f"- PREVIOUS FAILED CODE:\n```python\n{previous_code}\n```\n"
                    f"- LATEST ERROR LOG: {result_text}\n\n"
                    f"### INSTRUCTIONS:\n"
                    f"1. If this is ATTEMPT 1: Fix the most obvious cause (syntax or missing import).\n"
                    f"2. If this is ATTEMPT 2 or 3: The previous fix FAILED. DO NOT repeat the same logic. You MUST find an alternative library or rewrite the core logic entirely.\n"
                    f"3. If 'pip install' failed or it's a ModuleNotFoundError: The module is fake. Remove it and use Python Standard Libraries (os, sys, json, etc.) to achieve the goal.\n\n"
                    f"### OUTPUT:\n"
                    f"Return ONLY the complete, executable Python code."
                )
                
                self.log_thought("WORKER", "healing_prompt", f"Attempt {attempt}: Code fix")
                response = self.worker.generate(fix_task).strip()
                
                if response.startswith("```"):
                    response = response.split("\n", 1)[-1]
                    if response.endswith("```"):
                        response = response[:-3]
                    response = response.strip()
                if response.startswith("python\n"):
                    response = response[7:]
                    
                return True, "code_fix", {"code": response, "filename": tool_args["filename"]}
                
            else:
                # Parameter fixing for other tools
                fix_task = (
                    f"The tool '{tool_name}' failed with arguments {tool_args}.\n"
                    f"Error: {result_text}\n\n"
                    f"### TASK:\n"
                    f"Based on the error, suggest the CORRECTED arguments for this tool.\n"
                    f"Example: If 'EUR/USD' failed, try 'EURUSD' or 'EUR_USD'.\n\n"
                    f"### OUTPUT:\n"
                    f"Return ONLY a valid JSON of the corrected 'tool_args'."
                )
                
                self.log_thought("WORKER", "healing_prompt", f"Attempt {attempt}: Arg fix")
                response = self.worker.generate(fix_task).strip()
                
                if response.startswith("```"):
                    response = response.split("\n", 1)[-1]
                    if response.endswith("```"):
                        response = response[:-3]
                    response = response.strip()
                if response.startswith("json\n"):
                    response = response[5:]
                    
                try:
                    parsed = json.loads(response)
                    if "tool_args" in parsed:
                        return True, "retry_tool", parsed["tool_args"]
                    elif "tool_name" in parsed:
                        return True, "retry_tool", parsed.get("tool_args", {})
                    else:
                        return True, "retry_tool", parsed
                except json.JSONDecodeError:
                    return False, "failed", {"error": "Could not parse JSON arguments."}
                
        except Exception as e:
            return False, "failed", {"error": str(e)}

    def check_syntax(self, fixed_code: str) -> tuple[bool, str]:
        """Runs a fast LLM pass to check for obvious python syntax errors."""
        syntax_task = (
            f"Check the following code for basic Python syntax errors (missing colons, indentation issues, unclosed brackets).\n\n"
            f"### CODE TO CHECK:\n"
            f"```python\n{fixed_code}\n```\n\n"
            f"### OUTPUT:\n"
            f"- If valid: Return 'VALID'\n"
            f"- If invalid: Return 'INVALID: [Reason]'"
        )
        self.log_thought("WORKER", "syntax_check", "Running fast syntax validation...")
        response = self.worker.generate(syntax_task).strip()
        if response.startswith("INVALID"):
            return False, response
        return True, ""
