"""Graph Edges — Conditional routing logic between nodes."""
from .state import AgentState
from ..utils.logger import log


def router_edge(state: AgentState) -> str:
    """Decide what happens after a Worker step.

    Returns:
        "worker"     — more steps remain, loop back
        "write_file" — all steps done, file output requested
        "done"       — all steps done, no file needed
    """
    status = state.get("status", "done")

    if status == "continue":
        remaining = len(state["plan"]) - state["current_step"]
        log.brain(f"Router: {remaining} step(s) remaining -> WORKER")
        return "worker"
    elif status == "write_file":
        log.brain("Router: All steps done -> FILE WRITE")
        return "write_file"
    else:
        log.brain("Router: All steps done -> FINISH")
        return "done"
