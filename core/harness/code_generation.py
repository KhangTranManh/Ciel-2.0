"""Code generation delegates its write to the shared tool boundary in harness mode."""
from __future__ import annotations

def execute_code(self, task: str, filename: str) -> str:
    """Worker generates code and writes it to disk."""
    from agent_system.tools.buffer_writer import buffer_writer
    import os
    from core.harness.signals import _find_dangerous_code_patterns
    from agent_system.utils.logger import log
    manager = getattr(self, "run_manager", None)
    if manager is not None and not manager.may_continue():
        return "[RUN_LIMIT] Code generation did not start."

    buffer_writer.clear()
    # Add anti-hallucination guardrail for code generation
    code_guardrail = (
        "\n\nCRITICAL RULES FOR CODE GENERATION:\n"
        "1. Output ONLY production-ready code. Do NOT include dummy/test data, "
        "demonstration values, or example scaffolding unless explicitly asked.\n"
        "2. If input files may not exist, add proper error handling — do NOT fabricate their contents.\n"
        "3. Do NOT simulate script execution output or invent fake results.\n"
        "4. Do NOT add a dummy 'if __name__ == \"__main__\"' block with fake test data or "
        "file creation for demonstration. The main block should only call the real function "
        "with the real parameters from the task.\n"
        "5. Keep code concise: use brief inline comments only where logic is non-obvious. "
        "Do NOT write multi-line docstrings for every function. Do NOT add verbose "
        "explanatory comments on every line."
    )
    augmented_task = task + code_guardrail
    self._log_thought("WORKER", "code_task", task)
    code = self.worker.generate(augmented_task)
    self._log_thought("WORKER", "code_response", code)
    if manager is not None and manager.active is not None and filename.lower().endswith(".py"):
        import ast
        python_code = (code or "").strip()
        if python_code.startswith("```") and python_code.endswith("```"):
            python_code = "\n".join(python_code.splitlines()[1:-1]).strip()
        try:
            if not python_code:
                raise ValueError("empty generated code")
            ast.parse(python_code)
        except (SyntaxError, ValueError, TypeError) as error:
            self._log_thought("WORKER", "code_rejected", f"Python syntax validation failed: {error}")
            return "[TOOL_ERROR] Generated Python failed syntax validation; no file was written."
        code = python_code

    # Same dangerous-code gate as write_file/append_file (see execute_tool) — this
    # path writes to disk independently via buffer_writer, so it needs its own check.
    matched = _find_dangerous_code_patterns(code)
    if matched and (manager is None or manager.active is None):
        disable_gate = os.getenv("DISABLE_SAFETY_GATE", "false").lower() in ("true", "1", "yes")
        if not disable_gate:
            risk = f"Write generated code containing destructive pattern(s): {', '.join(matched)}"
            if not self._request_confirmation("execute_code", {"filename": filename}, risk_override=risk):
                return f"[CANCELLED] Master denied writing potentially destructive generated code to '{filename}'. No action was taken."

    if manager is not None and manager.active is not None:
        # Generated code is still a file write. Use the same sandbox, permission,
        # cancellation, audit and run-limit boundary as every other write.
        return self.execute_tool("write_file", {"filename": filename, "content": code},
                                 user_input=task)

    buffer_writer.append(code)
    result = buffer_writer.flush(filename)
    log.tool(result)
    return f"Code written to {filename}"
