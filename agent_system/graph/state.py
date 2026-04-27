"""AgentState — shared state across all graph nodes."""
from typing import TypedDict, Optional


class AgentState(TypedDict):
    # The original user request
    user_request: str

    # Brain's JSON plan — list of step dicts
    # Each step: {"step": 1, "task": "...", "assign": "worker"|"tool", "tool_name": "...", "tool_args": {...}}
    plan: list

    # Index of the step currently being executed
    current_step: int

    # Results collected from each completed step
    step_results: list

    # Final assembled output string
    final_output: str

    # Routing signal: "continue", "write_file", "done"
    status: str

    # Optional file path if the Brain decides to write output to disk
    output_filepath: Optional[str]
