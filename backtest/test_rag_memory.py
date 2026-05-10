"""test_rag_memory.py — The "Amnesia" Test for Hybrid Memory.

This test proves that Ciel's long-term RAG memory works by:
  1. Telling Ciel a unique, fictional fact.
  2. Flooding the short-term memory (20 msgs) to force the fact out.
  3. Asking Ciel to recall the fact — which now only exists in RAG.

Success Criteria:
  - Ciel answers the recall question correctly.
  - thoughts.log shows [RAG] [ARCHIVED] and [RAG] [RECALLED] entries.
"""

import sys
import time
from pathlib import Path
from colorama import init, Fore, Style

# Allow imports from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

init(autoreset=True)


def header(text: str):
    print(f"\n{Fore.CYAN}{'='*60}")
    print(f"  {text}")
    print(f"{'='*60}{Style.RESET_ALL}")


def step(num: int, desc: str):
    print(f"\n{Fore.YELLOW}[Step {num}]{Style.RESET_ALL} {desc}")


def result(passed: bool, detail: str):
    if passed:
        print(f"  {Fore.GREEN}[PASS]{Style.RESET_ALL} {detail}")
    else:
        print(f"  {Fore.RED}[FAIL]{Style.RESET_ALL} {detail}")


def main():
    header("RAG HYBRID MEMORY — AMNESIA TEST")

    # ─── Initialize CielCore ───
    step(0, "Initializing CielCore...")
    from core.llm_connector import CielCore
    core = CielCore()
    print(f"  {Fore.GREEN}CielCore ready.{Style.RESET_ALL}")

    # Check current RAG memory count
    from core import rag_manager
    mem_before = rag_manager.get_memory_count()
    print(f"  Long-term memories before test: {mem_before}")

    # ─── Step 1: Plant a unique, fictional fact ───
    secret_fact = (
        "I am planning a secret project named 'Operation Midnight Falcon' "
        "which uses Python and Rust together."
    )
    step(1, f"Planting secret fact: '{secret_fact[:60]}...'")
    response = core.process(secret_fact)
    print(f"  Ciel: {response[:150]}")

    # ─── Step 2: Flood with filler messages to push fact out of short-term ───
    filler_prompts = [
        # Normal / Tech chat
        "What is 2 + 2?",
        "Hello Ciel",
        "What is Python?",
        "How does the internet work?",
        "What is an API?",
        "Explain databases briefly",
        "What is machine learning?",
        "How does GPS work?",
        "What are microservices?",
        "Explain REST APIs",
        
        # Tool: OS Ops
        "What is the current time? (use shell to check date/time)",
        "Can you take a screenshot of my current screen?",
        "Can you open notepad or calculator for me?",
        "Run a shell command to list running processes (tasklist)",
        
        # Weird / Abstract chat
        "Do penguins have knees?",
        "Are hotdogs considered a type of sandwich?",
        "If a tree falls in the forest and no one is around, does it make a sound?",
        "Why is the sky blue?",
        "Tell me a joke about a potato.",
        "How many grains of sand are on earth?",
        "Can you write a haiku about a rogue AI?",
        
        # Tool: Trading Ops
        "What is the current price of BTCUSDT?",
        "Check the 24h stats for ETHUSDT on Binance.",
        "Analyze technical indicators for SOLUSDT.",
        "Get my MEXC portfolio balance.",
        "What is the contract info for DOGEUSDT?",
        
        # Tool: Memory Ops
        "Save a fact: 'The master loves drinking espresso.'",
        "What did you save about my coffee preference?",
        "Delete the fact about my coffee preference.",
        
        # Tool: Workspace Ops
        "Can you list all files in my workspace?",
        "Read the file ciel_data/facts.json and tell me what is inside.",
        "Write a file named 'test_potato.txt' containing 'potato'.",
        "Delete the file 'test_potato.txt'.",
        
        # Tool: Gmail Ops
        "Check my 5 most recent unread emails.",
        "Search my emails for the word 'security'.",
        "Are there any new emails from Google?",
        
        # More normal/tech chat to ensure we push things out
        "What is a compiler?",
        "Explain version control",
        "What is an operating system?",
        "How does memory allocation work?",
        "What is recursion?",
        "Explain multithreading",
        "What is a blockchain?",
        "How does TCP/IP work?",
    ]
    
    filler_count = len(filler_prompts)  # Will be ~45
    step(2, f"Flooding with {filler_count} filler messages to overflow short-term memory...")

    for i, prompt in enumerate(filler_prompts):
        print(f"  [{i+1}/{filler_count}] {prompt[:40]}...", end=" ")
        try:
            resp = core.process(prompt)
            print(f"{Fore.GREEN}OK{Style.RESET_ALL} ({len(resp)} chars)")
        except Exception as e:
            print(f"{Fore.RED}ERR: {e}{Style.RESET_ALL}")

    # Check RAG was populated
    mem_after = rag_manager.get_memory_count()
    print(f"\n  Long-term memories after flooding: {mem_after}")
    result(mem_after > mem_before, f"RAG grew from {mem_before} to {mem_after} memories")

    # ─── Step 3: The Recall Test ───
    recall_query = "What languages are we using for my secret project?"
    step(3, f"Testing recall: '{recall_query}'")

    # First, verify the fact is NOT in short-term memory
    short_term_content = " ".join(
        m.content for m in core.chat_history.messages
    ).lower()
    fact_in_short_term = "midnight falcon" in short_term_content
    result(
        not fact_in_short_term,
        f"Secret fact {'still' if fact_in_short_term else 'NOT'} in short-term memory "
        f"(this {'is expected to be gone' if not fact_in_short_term else 'should have been pushed out'})"
    )

    # Now ask Ciel to recall
    recall_response = core.process(recall_query)
    print(f"\n  {Fore.CYAN}Ciel's answer:{Style.RESET_ALL} {recall_response[:300]}")

    # Check if the answer mentions the key facts
    resp_lower = recall_response.lower()
    has_python = "python" in resp_lower
    has_rust = "rust" in resp_lower
    has_falcon = "falcon" in resp_lower or "midnight" in resp_lower

    result(has_python, f"Mentions 'Python': {'YES' if has_python else 'NO'}")
    result(has_rust, f"Mentions 'Rust': {'YES' if has_rust else 'NO'}")
    # Note: Worker may not repeat project name since the question only asks about languages
    if has_falcon:
        result(True, f"Bonus: Also mentions 'Midnight Falcon': YES")
    else:
        print(f"  {Fore.YELLOW}[INFO]{Style.RESET_ALL} Worker didn't repeat 'Midnight Falcon' (expected — question only asks about languages)")

    # ─── Step 4: Verify thoughts.log ───
    step(4, "Checking thoughts.log for RAG activity...")
    thoughts_file = Path(__file__).resolve().parent.parent / "ciel_data" / "logs" / "thoughts.log"
    if thoughts_file.exists():
        log_content = thoughts_file.read_text(encoding="utf-8", errors="ignore")
        has_archived = "[RAG]" in log_content and "[ARCHIVED]" in log_content
        has_recalled = "[RAG]" in log_content and "[RECALLED]" in log_content
        result(has_archived, f"[RAG] [ARCHIVED] entries found: {'YES' if has_archived else 'NO'}")
        result(has_recalled, f"[RAG] [RECALLED] entries found: {'YES' if has_recalled else 'NO'}")
    else:
        result(False, "thoughts.log not found")

    # ─── Summary ───
    header("TEST SUMMARY")
    all_passed = (
        mem_after > mem_before
        and not fact_in_short_term
        and has_python and has_rust
    )
    if all_passed:
        print(f"  {Fore.GREEN}HYBRID MEMORY SYSTEM: OPERATIONAL{Style.RESET_ALL}")
        print(f"  Short-term (JSON): {len(core.chat_history.messages)} messages")
        print(f"  Long-term (RAG):   {mem_after} memories")
    else:
        print(f"  {Fore.RED}HYBRID MEMORY SYSTEM: PARTIAL FAILURE{Style.RESET_ALL}")
        print(f"  Review thoughts.log and test output above for details.")


if __name__ == "__main__":
    main()
