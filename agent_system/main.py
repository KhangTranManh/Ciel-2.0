"""Brain-Worker Agent System — Entry Point.
Run: python -m agent_system.main (from Ciel 2.0/ directory)
"""
import sys
import os

# Fix Windows encoding
os.environ["PYTHONIOENCODING"] = "utf-8"
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from colorama import Fore, Style, init as colorama_init

colorama_init()

from .graph.builder import build_graph
from .graph.state import AgentState
from .utils.logger import log


def run(user_request: str) -> str:
    """Execute a single request through the Brain-Worker pipeline."""
    log.divider()
    log.system("=" * 50)
    log.system("  BRAIN-WORKER AGENT SYSTEM")
    log.system("=" * 50)
    log.system(f"Request: \"{user_request}\"")
    log.divider()

    graph = build_graph()

    initial_state: AgentState = {
        "user_request": user_request,
        "plan": [],
        "current_step": 0,
        "step_results": [],
        "final_output": "",
        "status": "",
        "output_filepath": None,
    }

    final_state = graph.invoke(initial_state)

    log.divider()
    log.system("=" * 50)
    log.system("  EXECUTION COMPLETE")
    log.system("=" * 50)

    # Build final output
    if final_state.get("final_output"):
        output = final_state["final_output"]
    elif final_state.get("step_results"):
        # Filter out tool markers
        content_results = [
            r for r in final_state["step_results"]
            if not r.startswith("[Buffered") and not r.startswith("[Tool:")
        ]
        output = "\n\n".join(content_results)
    else:
        output = "No output generated."

    log.result("Final Output:")
    log.divider()
    print(output)
    log.divider()

    return output


def main():
    """Interactive loop for testing."""
    print(Fore.CYAN + """
    ==========================================
      Brain-Worker Agent System
      Brain  : Gemini API (Router)
      Worker : Ollama Local (Generator)
      Type 'exit' to quit
    ==========================================
    """ + Style.RESET_ALL)

    while True:
        try:
            user_input = input(Fore.GREEN + "\nRequest: " + Style.RESET_ALL).strip()
        except (KeyboardInterrupt, EOFError):
            break

        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit"):
            log.system("Shutting down.")
            break

        try:
            run(user_input)
        except Exception as e:
            log.error(f"Pipeline failed: {e}")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    main()
