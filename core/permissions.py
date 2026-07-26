"""permissions.py — Tier-3: what needs the Master's approval, and when to ask.

WHAT WAS WRONG WITH THE OLD MODEL
---------------------------------
Approval was binary and per-call: a tool was either in `_HIGH_RISK_TOOLS` (always ask,
every single time) or free (never ask). Two consequences, both bad:

  * A multi-step plan interrupted the Master N times, once per risky step, each prompt
    arriving AFTER earlier steps had already run — so "no" at step 3 left steps 1-2 done.
    The Master was approving fragments with no view of the whole.
  * There was no way to say "yes, and stop asking me about git_status this session", nor
    to say "never run this tool at all", so the gate was either noisy or off entirely
    (`DISABLE_SAFETY_GATE`), and people turn noisy gates off.

THE MODEL HERE
--------------
Every (tool, args) pair resolves to exactly one of three decisions:

  AUTO — read-only / no outside effect. Never interrupts.
  ASK  — has an effect worth a confirmation.
  DENY — refused outright, no prompt offered. For tools the Master has ruled out.

On top of that, two things reduce interruptions WITHOUT reducing control:
  * plan-level approval — one prompt showing every ASK step in the plan, answered once
    BEFORE anything runs, so nothing is half-done when the Master says no;
  * session grants — an explicit, opt-in "don't ask again for this tool" that lasts only
    for the running process and is never persisted to disk.

DENY always wins, and is checked first: no grant, no plan approval and no
`DISABLE_SAFETY_GATE` can override it. That is the point of having the tier at all.
"""
import json
import os


def step_signature(tool_name: str, tool_args) -> str:
    """Stable identity of one concrete call — the tool AND the arguments it was given.

    Plan approval is keyed on this rather than on the tool name. Name-keyed grants are
    too coarse: approving `delete_file` for the plan the Master reviewed would also
    silently approve a *different* `delete_file`, on a different file, proposed later by
    the Tier-1 loop — a step the Master never saw. Keyed this way a grant can only ever
    re-approve the identical action, which is also why a stale grant is harmless.
    """
    try:
        args = json.dumps(tool_args or {}, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:
        args = str(tool_args)
    return f"{(tool_name or '').strip().lower()}::{args}"


class Decision:
    AUTO = "auto"
    ASK = "ask"
    DENY = "deny"


# Read-only tools: no filesystem writes, no outbound messages, no state mutation.
# Same property the parallel executor requires, so the two lists agree by construction —
# see core/parallel.py. Kept as a literal set rather than a name-prefix guess ("get_*"),
# because a wrong guess here silently removes a confirmation prompt.
_AUTO_TOOLS = frozenset({
    "get_market_price", "get_crypto_stats", "analyze_crypto_technical",
    "stealth_search", "smart_scrape", "read_document",
    "read_file", "get_file_info", "list_workspace", "grep_in_workspace",
    "git_status", "git_diff", "git_list_repos",
    "search_gmail", "get_gmail_message", "get_gmail_thread",
    "get_weather", "calculate", "get_current_time", "get_fact", "list_todos",
    "build_market_report_html",
})


def _env_names(var: str) -> frozenset:
    return frozenset(n.strip().lower() for n in os.getenv(var, "").split(",") if n.strip())


class PermissionPolicy:
    """Decides AUTO / ASK / DENY. Pure: no I/O, no prompting, no LLM."""

    def __init__(self, risky_names, deny_names=None, auto_names=None, gate_disabled=False):
        self._risky = {str(n).lower() for n in (risky_names or ())}
        self._deny = set(deny_names if deny_names is not None else _env_names("CIEL_DENY_TOOLS"))
        self._auto = set(auto_names or _AUTO_TOOLS) | _env_names("CIEL_AUTO_TOOLS")
        self._gate_disabled = bool(gate_disabled)
        self._session_grants = set()      # in-memory only, never written to disk
        # Signatures (tool + exact args) approved for the currently-executing plan.
        # Deliberately separate from session grants, and deliberately keyed on the whole
        # call: approving "send THIS email as part of this plan" must not become "send
        # any email", nor "send a different email the loop invents two steps later".
        self._plan_grants = set()

    # ------------------------------------------------------------- decide
    def decide(self, tool_name: str, tool_args: dict = None) -> tuple:
        """Return (Decision, reason)."""
        name = (tool_name or "").strip().lower()

        # DENY first, and unconditionally. A tool the Master has ruled out must not be
        # reachable through a session grant, a plan approval, or an open safety gate.
        if name in self._deny:
            return Decision.DENY, f"'{name}' is on the deny list (CIEL_DENY_TOOLS)"

        if name in self._auto:
            return Decision.AUTO, "read-only"

        if name not in self._risky:
            return Decision.AUTO, "not classified as risky"

        # From here the tool IS risky.
        if self._gate_disabled:
            return Decision.AUTO, "safety gate disabled (DISABLE_SAFETY_GATE)"
        if step_signature(name, tool_args) in self._plan_grants:
            return Decision.AUTO, "this exact step was approved in the plan"
        if name in self._session_grants:
            return Decision.AUTO, "approved earlier this session"
        return Decision.ASK, "risky tool, not yet approved"

    # ------------------------------------------------------------- grants
    def grant_for_session(self, tool_name: str) -> bool:
        """Stop asking about this tool until the process exits. Refused for deny-listed
        tools — otherwise a grant would become a way around the deny list."""
        name = (tool_name or "").strip().lower()
        if not name or name in self._deny:
            return False
        self._session_grants.add(name)
        return True

    def revoke_session_grants(self):
        self._session_grants.clear()

    def grant_for_plan(self, steps) -> set:
        """Approve the exact STEPS the Master just reviewed (not their tool names).

        Takes step dicts, not names, so the grant is scoped to the calls that appeared in
        the preview. A later step using the same tool with different arguments is a
        different action and is asked about again. Deny-listed tools are dropped, so a
        plan approval can never become a route around the deny list.
        """
        granted = set()
        for st in steps or []:
            if not isinstance(st, dict):
                continue
            name = (st.get("tool_name") or "").strip().lower()
            if not name or name in self._deny:
                continue
            granted.add(step_signature(name, st.get("tool_args") or {}))
        self._plan_grants |= granted
        return granted

    def clear_plan_grants(self):
        """MUST be called when the plan ends — including on failure. A grant that
        outlives its plan is a confirmation the Master never actually gave."""
        self._plan_grants.clear()

    @property
    def session_grants(self) -> set:
        return set(self._session_grants)

    # --------------------------------------------------------- plan review
    def review_plan(self, steps) -> dict:
        """Classify a whole plan BEFORE any of it runs.

        Returns {"deny": [...], "ask": [...], "auto": [...]} of tool names, so the caller
        can refuse an impossible plan up front and raise ONE prompt covering every step
        that needs approval — instead of stopping the Master mid-execution with earlier
        steps already carried out.
        """
        out = {Decision.DENY: [], Decision.ASK: [], Decision.AUTO: []}
        for st in steps or []:
            if not isinstance(st, dict):
                continue
            name = (st.get("tool_name") or "").strip()
            if not name:
                continue
            decision, _ = self.decide(name, st.get("tool_args") or {})
            out[decision].append(name)
        return out
