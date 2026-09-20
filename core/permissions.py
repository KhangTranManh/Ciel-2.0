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

UNATTENDED RUNS (added with Tier 6)
-----------------------------------
Everything above assumes a human is present to answer. A Tier-6 trigger firing at 03:00
has nobody to ask, and "nobody answered" must never resolve to "yes". So `decide(...,
attended=False)` adds a fourth outcome, DEFER: the action is recorded in `DeferredStore`
and raised at the next interaction. Session grants, plan approvals and even
`DISABLE_SAFETY_GATE` are all ignored in that context — each of them is evidence that
someone agreed to something *while present*, and none of it transfers to a background
run hours later. The only unattended escape hatch is per-tool and explicit
(`CIEL_UNATTENDED_AUTO_TOOLS`).
"""
import json
import os
import threading
import time
import uuid


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
    # Only reachable in an UNATTENDED context (a Tier-6 trigger, a scheduled run). There
    # is nobody at the keyboard, so ASK has no meaning: the action is recorded and the
    # question is raised at the next interaction instead of being silently auto-approved.
    DEFER = "defer"


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
    "list_monthly_goals", "list_weekly_plan", "list_reminders",
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
        # Risky tools the Master has explicitly cleared to run with nobody watching.
        # Per-tool and opt-in on purpose: a blanket "allow unattended" switch would be
        # exactly the kind of setting that gets turned on once and forgotten.
        self._unattended_auto = _env_names("CIEL_UNATTENDED_AUTO_TOOLS") - self._deny
        self._gate_disabled = bool(gate_disabled)
        self._session_grants = set()      # in-memory only, never written to disk
        # Signatures (tool + exact args) approved for the currently-executing plan.
        # Deliberately separate from session grants, and deliberately keyed on the whole
        # call: approving "send THIS email as part of this plan" must not become "send
        # any email", nor "send a different email the loop invents two steps later".
        self._plan_grants = set()

    # ------------------------------------------------------------- decide
    def decide(self, tool_name: str, tool_args: dict = None, attended: bool = True) -> tuple:
        """Return (Decision, reason).

        `attended=False` means no human is at the keyboard — a Tier-6 trigger or a
        scheduled run. In that context a risky tool can only ever DEFER, because every
        mechanism that would otherwise approve it assumes someone is watching.
        """
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
        if not attended:
            # Everything below this line — the open gate, session grants, plan approvals —
            # is evidence that a human agreed to something *while present*. None of it
            # transfers to a background run hours later, so none of it is consulted.
            # `DISABLE_SAFETY_GATE` is deliberately NOT honoured here either: it means
            # "stop asking me", which is a statement about interruptions, not a standing
            # permission to act unsupervised. The escape hatch is per-tool and explicit.
            if name in self._unattended_auto:
                return (Decision.AUTO,
                        f"'{name}' explicitly allowed unattended (CIEL_UNATTENDED_AUTO_TOOLS)")
            return (Decision.DEFER,
                    f"'{name}' needs approval and nobody is at the keyboard")

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
    def review_plan(self, steps, attended: bool = True) -> dict:
        """Classify a whole plan BEFORE any of it runs.

        Returns {"deny": [...], "ask": [...], "auto": [...], "defer": [...]} of tool
        names, so the caller can refuse an impossible plan up front and raise ONE prompt
        covering every step that needs approval — instead of stopping the Master
        mid-execution with earlier steps already carried out.
        """
        out = {Decision.DENY: [], Decision.ASK: [], Decision.AUTO: [], Decision.DEFER: []}
        for st in steps or []:
            if not isinstance(st, dict):
                continue
            name = (st.get("tool_name") or "").strip()
            if not name:
                continue
            decision, _ = self.decide(name, st.get("tool_args") or {}, attended=attended)
            out[decision].append(name)
        return out


_DEFERRED_MAX = 20


class DeferredStore:
    """Actions a background run wanted to take but could not, pending the Master.

    WHY THIS DOES NOT REPLAY THEM
    -----------------------------
    The obvious next feature is "approve it and Ciel runs it". That is deliberately not
    here. A mutating action decided at 03:00 against 03:00's world — an email whose facts
    have moved on, a delete whose file has changed — is not the same action at 09:00, and
    approving it from a one-line summary is approving a fragment, which is the exact
    failure Tier 3 was built to remove. So this records what was wanted and why, tells
    the Master, and lets them re-issue it as a fresh request that gets planned against
    the world as it is now.
    """

    def __init__(self, path=None, max_items: int = _DEFERRED_MAX):
        self.path = path
        self.max_items = max_items
        self._lock = threading.RLock()
        self._items = []            # newest first
        self._load()

    def _load(self):
        try:
            if not self.path or not self.path.exists():
                return
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self._items = [i for i in (raw if isinstance(raw, list) else [])
                           if isinstance(i, dict) and i.get("id")]
        except Exception:
            self._items = []

    def _save_locked(self):
        try:
            if not self.path:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self._items[: self.max_items],
                                            ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def add(self, tool_name: str, tool_args: dict = None, reason: str = "",
            source: str = "", now: float = None) -> dict:
        """Record one blocked action. Identical repeats collapse onto one entry."""
        now = time.time() if now is None else now
        sig = step_signature(tool_name, tool_args)
        with self._lock:
            for it in self._items:
                if it.get("signature") == sig:
                    it["hits"] = int(it.get("hits") or 1) + 1
                    it["last_at"] = now
                    self._save_locked()
                    return it
            item = {"id": uuid.uuid4().hex[:8], "tool": tool_name, "signature": sig,
                    "args": tool_args or {}, "reason": reason, "source": source,
                    "at": now, "last_at": now, "hits": 1}
            self._items.insert(0, item)
            del self._items[self.max_items:]
            self._save_locked()
            return item

    def pending(self) -> list:
        with self._lock:
            return list(self._items)

    def resolve(self, item_id: str) -> bool:
        with self._lock:
            before = len(self._items)
            self._items = [i for i in self._items if i.get("id") != item_id]
            if len(self._items) != before:
                self._save_locked()
                return True
            return False

    def clear(self) -> int:
        with self._lock:
            n = len(self._items)
            self._items = []
            self._save_locked()
            return n

    def describe(self, limit: int = 5) -> str:
        """One human-readable block, or "" when there is nothing pending."""
        items = self.pending()[:limit]
        if not items:
            return ""
        lines = []
        for it in items:
            when = time.strftime("%H:%M %d/%m", time.localtime(it.get("at") or 0))
            repeat = f" (đã muốn làm {it['hits']} lần)" if int(it.get("hits") or 1) > 1 else ""
            src = f" — {it['source']}" if it.get("source") else ""
            lines.append(f"  · {it.get('tool')} lúc {when}{repeat}{src}")
        return "\n".join(lines)

    def __len__(self):
        with self._lock:
            return len(self._items)
