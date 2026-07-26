"""parallel.py — decide which consecutive tool steps may run concurrently.

WHY OPT-IN AND NOT A BLACKLIST
------------------------------
The obvious design is "run everything except writes/sends in parallel". That is unsafe
here specifically because skills auto-register: dropping `skills/**/my_ops.py` into the
tree adds tools that `core/` has never heard of, so a blacklist would silently
parallelise a brand-new mutating tool the day someone adds it. Instead a tool must be
declared parallel-safe — by `_DEFAULT_PARALLEL_SAFE` for built-ins, or by the skill
itself returning `parallel_safe: [...]` from its `get_*_tools()` factory. Anything
unknown runs sequentially, exactly as before.

WHAT "SAFE" MEANS
-----------------
Three independent conditions, all checked in code:
  1. The tool is declared parallel-safe (read-only / no outside effect).
  2. Its args carry no {step_N} / {prev} reference — a step consuming an earlier
     step's output cannot start before that output exists.
  3. It is not a high-risk tool. Those block on a Y/N prompt, and several threads
     racing for one stdin is a deadlock, not a speed-up.

Ordering is preserved: batches run one after another in the original order, and results
are collected back in the original order, so {prev} / {step_N} still mean what they
meant when everything ran sequentially.
"""
import re

# Built-in tools that only read. Deliberately explicit rather than pattern-matched on
# the name: "get_" and "search_" prefixes are conventions, not guarantees, and a wrong
# guess here runs a mutating tool concurrently with something else.
_DEFAULT_PARALLEL_SAFE = frozenset({
    # market / data
    "get_market_price", "get_crypto_stats", "analyze_crypto_technical",
    # information retrieval
    "stealth_search", "smart_scrape", "read_document",
    # workspace reads
    "read_file", "get_file_info", "list_workspace", "grep_in_workspace",
    # git reads
    "git_status", "git_diff", "git_list_repos",
    # mail reads
    "search_gmail", "get_gmail_message", "get_gmail_thread",
    # misc reads / pure computation
    "get_weather", "calculate", "get_current_time", "get_fact", "list_todos",
})

# Matches one OR two braces, mirroring CielCore._STEP_REF_RE: a step written {step_1}
# is just as dependent as {{step_1}}, and treating it as independent would start it
# before the output it needs exists.
_STEP_REF_RE = re.compile(r"\{{1,2}\s*(?:prev|step[_ ]?\d+)(?:\.output)?\s*\}{1,2}", re.IGNORECASE)


def _has_step_ref(value) -> bool:
    """True if any (possibly nested) string in the args references an earlier step."""
    if isinstance(value, str):
        return bool(_STEP_REF_RE.search(value))
    if isinstance(value, dict):
        return any(_has_step_ref(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(_has_step_ref(v) for v in value)
    return False


def is_parallel_safe(step, safe_names, high_risk_names=frozenset()) -> bool:
    """Whether ONE step may share a batch with others. See module docstring."""
    if not isinstance(step, dict):
        return False
    name = (step.get("tool_name") or "").strip().lower()
    if not name or name not in safe_names or name in high_risk_names:
        return False
    return not _has_step_ref(step.get("tool_args") or {})


def plan_batches(steps, safe_names, high_risk_names=frozenset(), max_workers: int = 4) -> list:
    """Group `steps` into ordered batches; only a batch of >1 runs concurrently.

    Consecutive parallel-safe steps merge, capped at `max_workers` so one huge fan-out
    cannot open thirty sockets at once. Every other step becomes a batch of one, which
    reproduces the original sequential behaviour exactly — that is the point: this
    function can only ever group things, never reorder or drop them.
    """
    batches, current = [], []
    for st in steps or []:
        if is_parallel_safe(st, safe_names, high_risk_names) and len(current) < max_workers:
            current.append(st)
            continue
        if current:
            batches.append(current)
            current = []
        if is_parallel_safe(st, safe_names, high_risk_names):
            current = [st]          # previous batch hit max_workers; start a new one
        else:
            batches.append([st])    # sequential step, on its own
    if current:
        batches.append(current)
    return batches


def collect_parallel_safe(skill_data_list) -> set:
    """Union of the built-in safe set with every skill's declared `parallel_safe`."""
    names = set(_DEFAULT_PARALLEL_SAFE)
    for data in skill_data_list or []:
        try:
            for n in (data or {}).get("parallel_safe") or []:
                if isinstance(n, str) and n.strip():
                    names.add(n.strip().lower())
        except Exception:
            continue
    return names
