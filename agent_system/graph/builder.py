"""Graph Builder — Wires all nodes and edges into a compiled LangGraph."""
from langgraph.graph import StateGraph, END

from .state import AgentState
from .nodes import brain_node, worker_node, file_write_node
from .edges import router_edge
from ..utils.logger import log


def build_graph():
    """Construct and compile the Brain-Worker pipeline graph.

    Flow:
        brain_node -> worker_node -> [router_edge] -> worker_node (loop)
                                                    -> file_write_node -> END
                                                    -> END
    """
    log.system("Building Brain-Worker graph...")

    graph = StateGraph(AgentState)

    # Add nodes
    graph.add_node("brain", brain_node)
    graph.add_node("worker", worker_node)
    graph.add_node("file_write", file_write_node)

    # Entry point
    graph.set_entry_point("brain")

    # Brain always goes to Worker first
    graph.add_edge("brain", "worker")

    # Worker uses conditional routing
    graph.add_conditional_edges(
        "worker",
        router_edge,
        {
            "worker": "worker",         # Loop back for next step
            "write_file": "file_write",  # Flush buffer to disk
            "done": END,                 # Finish without file
        },
    )

    # File write always finishes
    graph.add_edge("file_write", END)

    compiled = graph.compile()
    log.system("Graph compiled successfully.")
    return compiled
