"""Graph Nodes — Brain, Worker, and FileWrite nodes for the LangGraph pipeline."""
from ..models.brain import Brain
from ..models.worker import Worker
from ..tools.buffer_writer import buffer_writer
from ..utils.logger import log
from .state import AgentState


# Singletons — created once, reused across all invocations
_brain = None
_worker = None


def _get_brain() -> Brain:
    global _brain
    if _brain is None:
        _brain = Brain()
    return _brain


def _get_worker() -> Worker:
    global _worker
    if _worker is None:
        _worker = Worker()
    return _worker


def brain_node(state: AgentState) -> AgentState:
    """Brain analyzes the user request and produces a JSON plan."""
    log.divider()
    log.brain("=== BRAIN NODE ACTIVATED ===")

    brain = _get_brain()
    parsed = brain.plan(state["user_request"])

    plan = parsed.get("plan", [])
    filepath = parsed.get("output_filepath")

    state["plan"] = plan
    state["current_step"] = 0
    state["step_results"] = []
    state["output_filepath"] = filepath
    state["status"] = "continue" if plan else "done"

    return state


def worker_node(state: AgentState) -> AgentState:
    """Worker executes the current step from the Brain's plan."""
    log.divider()

    plan = state["plan"]
    idx = state["current_step"]

    if idx >= len(plan):
        log.error(f"Worker called but no more steps (idx={idx}, total={len(plan)})")
        state["status"] = "done"
        return state

    step = plan[idx]
    step_num = step.get("step", idx + 1)
    task = step.get("task", "")
    assign = step.get("assign", "worker")

    log.worker(f"=== WORKER NODE -- Step {step_num}/{len(plan)} ===")

    if assign == "tool":
        # This is a buffer_write step — append previous worker result
        tool_name = step.get("tool_name", "buffer_write")
        flush_to = step.get("flush_to")  # Multi-file: immediate flush to specific path

        if tool_name == "buffer_write" and state["step_results"]:
            # Find last actual content (skip tool markers)
            last_content = None
            for r in reversed(state["step_results"]):
                if not r.startswith("[Buffered") and not r.startswith("[Tool:") and not r.startswith("[Flushed"):
                    last_content = r
                    break

            if last_content:
                buffer_writer.append(last_content)

                if flush_to:
                    # Multi-file mode: flush immediately to the specific file
                    flush_result = buffer_writer.flush(flush_to)
                    log.tool(f"Flushed to: {flush_to}")
                    state["step_results"].append(f"[Flushed step {step_num} -> {flush_to}]")
                else:
                    state["step_results"].append(f"[Buffered step {step_num}]")
            else:
                log.tool(f"Tool step: {tool_name} (no content to buffer)")
                state["step_results"].append(f"[Tool: {tool_name}]")
        else:
            log.tool(f"Tool step: {tool_name} (no content to buffer)")
            state["step_results"].append(f"[Tool: {tool_name}]")
    else:
        # Worker generation step — ISOLATED. No prior output carried over.
        worker = _get_worker()

        # Only use the Brain's shared_context hint (a short one-liner), NOT previous output
        shared_ctx = step.get("shared_context", "")

        result = worker.generate(task, shared_ctx)
        state["step_results"].append(result)

        # Preview first 120 chars
        preview = result[:120].replace("\n", " ")
        log.worker(f"Output preview: \"{preview}{'...' if len(result) > 120 else ''}\"")

    # Advance to next step
    state["current_step"] = idx + 1

    # Determine status
    if state["current_step"] >= len(plan):
        if state.get("output_filepath"):
            state["status"] = "write_file"
        else:
            state["status"] = "done"
    else:
        state["status"] = "continue"

    return state


def file_write_node(state: AgentState) -> AgentState:
    """Flush the buffer to disk in a single I/O operation."""
    log.divider()
    log.tool("=== FILE WRITE NODE ===")

    filepath = state.get("output_filepath", "./agent_output/output.txt")

    # If buffer is empty, write step_results directly
    if not buffer_writer._buffer and state["step_results"]:
        # Collect only actual content (skip tool markers)
        content_results = [
            r for r in state["step_results"]
            if not r.startswith("[Buffered") and not r.startswith("[Tool:")
        ]
        for content in content_results:
            buffer_writer.append(content)

    result = buffer_writer.flush(filepath)
    log.tool(result)

    state["final_output"] = f"File written: {filepath}"
    state["status"] = "done"

    return state
