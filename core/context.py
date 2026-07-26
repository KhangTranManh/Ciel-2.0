"""context.py — Tier-4: one place that decides what goes into a prompt.

WHAT THIS FIXES
---------------
Context was assembled by appending to a string in `process()`:

    enriched_input = user_input
    if recalled: enriched_input = f"…{recalled}…{user_input}"
    enriched_input = f"{enriched_input}\\n\\n[USER LANGUAGE: …]"
    enriched_input += f"\\n\\n[WORKING DIRECTORY: …]"

Every one of those lines is individually reasonable, and together they have three
problems that only show up at scale:

  * **No budget.** Nothing bounds the total. RAG recall is the dangerous one — it is the
    only block whose size depends on data rather than on code, so a long recalled
    passage silently pushes the request itself toward the edge of the window.
  * **No visibility.** When a prompt misbehaves there is no record of what was in it.
    "Which blocks did this call actually carry?" could only be answered by re-reading
    the code path and guessing.
  * **No ordering discipline.** The `+=` order is the order someone happened to add the
    feature in. The most important thing in the prompt — the Master's actual request —
    ends up buried in the middle, which is measurably the worst position in a long
    context.

MEASURED CONTEXT FOR WHY THIS TIER EXISTS
-----------------------------------------
A Brain call carries ~4,291 fixed tokens: router prompt 1,591 (37%), tool list 1,437
(33%), persona 1,205 (28%), deterministic injections 42 (1%) — and the user's actual
request, 16 tokens (0.4%). **99% of every call is overhead resent verbatim.** That is
also the hard blocker for local models: a 4K-context model cannot run Ciel at all,
however capable it is.

WHAT THIS DELIBERATELY IS NOT
-----------------------------
Not a prompt template engine, and not an LLM-driven summariser. It orders blocks, counts
them, drops the least important when over budget, and says what it did. Every decision
is plain Python, so the same request produces the same prompt on every provider and
every model size.
"""
import threading
from dataclasses import dataclass, field


# Priorities. Higher survives longer when the budget bites. The gaps are deliberate:
# a new block should be able to land between two existing ones without renumbering.
P_REQUEST = 100      # the Master's actual words — must never be dropped
P_CRITICAL = 80      # facts a plan is wrong without (working directory)
P_IMPORTANT = 60     # materially changes the answer (user language)
P_HELPFUL = 40       # improves the answer when it fits (recalled context, profile)
P_OPTIONAL = 20      # nice to have


@dataclass
class Block:
    name: str
    text: str
    priority: int = P_HELPFUL
    tokens: int = 0


@dataclass
class AssemblyReport:
    """What actually went into the prompt. Logged, so a bad prompt can be explained."""
    kept: list = field(default_factory=list)        # [(name, tokens)]
    dropped: list = field(default_factory=list)     # [(name, tokens)]
    total_tokens: int = 0
    budget: int = 0

    def summary(self) -> str:
        kept = ", ".join(f"{n}={t}" for n, t in self.kept) or "-"
        line = f"{self.total_tokens} tok" + (f"/{self.budget}" if self.budget else "") \
               + f" | kept: {kept}"
        if self.dropped:
            line += " | DROPPED: " + ", ".join(f"{n}={t}" for n, t in self.dropped)
        return line


def _count(text: str) -> int:
    from .user_model import estimate_tokens
    return estimate_tokens(text)


class ContextAssembler:
    """Collects named blocks, then renders them in priority order within a budget.

    Usage is deliberately boring — `add()` as many times as the flow needs, `render()`
    once. The value is not the machinery; it is that there is exactly ONE place where
    the question "what is in this prompt, and why" has an answer.
    """

    def __init__(self, counter=None):
        self._blocks = []
        self._count = counter or _count
        self._lock = threading.Lock()

    def add(self, name: str, text: str, priority: int = P_HELPFUL) -> "ContextAssembler":
        """Add one block. Empty text is ignored, so callers need no `if` around it."""
        if not text or not str(text).strip():
            return self
        with self._lock:
            self._blocks.append(Block(name=name, text=str(text).strip(), priority=priority))
        return self

    def render(self, budget_tokens: int = 0, separator: str = "\n\n") -> tuple:
        """Return (text, AssemblyReport).

        **Priority decides what is dropped; insertion order decides layout.** Keeping
        those two separate is deliberate. Emitting highest-priority-first would also be
        defensible — a request buried between machine-generated notes is measurably
        harder for a model to weigh — but changing ordering AND introducing a budget in
        one step makes any A/B uninterpretable: a regression could come from either.
        Ordering is a separate experiment, run on its own.

        Over budget, the lowest-priority blocks are dropped **whole**, never truncated.
        Half a `[WORKING DIRECTORY: …]` note is worse than none: it still reads as a
        fact while being wrong, and a wrong path is the exact failure that note exists
        to prevent. `P_REQUEST` blocks are never dropped — a prompt without the request
        is not a smaller prompt, it is a broken one.
        """
        with self._lock:
            blocks = list(self._blocks)
        for b in blocks:
            b.tokens = self._count(b.text)

        report = AssemblyReport(budget=int(budget_tokens or 0))
        sep_cost = self._count(separator)

        if budget_tokens:
            # Decide membership in priority order (weakest dropped first)…
            keep_idx, used = set(), 0
            for i, b in sorted(enumerate(blocks), key=lambda p: (-p[1].priority, p[0])):
                cost = b.tokens + (sep_cost if keep_idx else 0)
                if (used + cost) > budget_tokens and b.priority < P_REQUEST:
                    continue
                keep_idx.add(i)
                used += cost
        else:
            keep_idx, used = set(range(len(blocks))), sum(
                b.tokens + (sep_cost if i else 0) for i, b in enumerate(blocks))

        # …but emit in the order the caller added them.
        kept = []
        for i, b in enumerate(blocks):
            if i in keep_idx:
                kept.append(b)
                report.kept.append((b.name, b.tokens))
            else:
                report.dropped.append((b.name, b.tokens))

        report.total_tokens = used
        return separator.join(b.text for b in kept), report

    def __len__(self):
        with self._lock:
            return len(self._blocks)
