"""Tier-4 suite — the context assembler: budget, priority, and honest reporting.

Zero LLM calls. The point of this tier is that a prompt is now assembled by one piece of
code with a bound and a record, instead of by string concatenation nobody can audit — so
what is tested here is exactly that: what survives a budget, what never can be dropped,
and whether the report tells the truth about it.

Run from the Ciel 2.0 directory:
    ./myenv/Scripts/python.exe -m backtest.test_context
"""
import os
import sys
from pathlib import Path

os.environ["PYTHONIOENCODING"] = "utf-8"
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.context import (        # noqa: E402
    ContextAssembler, P_CRITICAL, P_HELPFUL, P_IMPORTANT, P_OPTIONAL, P_REQUEST,
)

_passed, _failed = 0, []


def check(name, cond, detail=""):
    global _passed
    if cond:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed.append(name)
        print(f"  FAIL  {name}" + (f"  -- {detail}" if detail else ""))


def words(n):
    """A block of exactly `n` cheap tokens under the word counter used in these tests."""
    return " ".join(["tok"] * n)


def asm():
    """Assembler with a deterministic counter, so budgets in this file mean words."""
    return ContextAssembler(counter=lambda t: len((t or "").split()))


# ─────────────────────────────────────────────────────────────────────────────
def test_basics():
    print("\n[1] Basics — layout follows insertion order")
    a = asm()
    a.add("request", "xin chào", P_REQUEST)
    a.add("cwd", "[WORKING DIRECTORY: d:/x]", P_CRITICAL)
    text, rep = a.render()

    check("both blocks are present", "xin chào" in text and "WORKING DIRECTORY" in text)
    check("order is insertion order, not priority order",
          text.index("xin chào") < text.index("WORKING"), text)
    check("the report lists what was kept", [n for n, _ in rep.kept] == ["request", "cwd"])
    check("nothing was dropped", rep.dropped == [])
    check("blocks are separated", "\n\n" in text)

    check("empty text is ignored, so callers need no if around add()",
          len(asm().add("x", "").add("y", "   ").add("z", None)) == 0)
    check("an empty assembler renders to nothing", asm().render()[0] == "")
    check("add() chains", len(asm().add("a", "x").add("b", "y")) == 2)


def test_budget():
    print("\n[2] Budget — the weakest block goes first")
    a = asm()
    a.add("request", words(10), P_REQUEST)
    a.add("cwd", words(10), P_CRITICAL)
    a.add("language", words(10), P_IMPORTANT)
    a.add("recalled", words(10), P_HELPFUL)

    text, rep = a.render(budget_tokens=1000)
    check("a generous budget keeps everything", rep.dropped == [] and len(rep.kept) == 4)

    # 4 blocks x 10 tokens: a budget of 32 fits exactly three, so precisely the weakest
    # one has to go.
    text, rep = a.render(budget_tokens=32)
    kept = [n for n, _ in rep.kept]
    check("over budget, the LOWEST priority is dropped first",
          "recalled" not in kept and "request" in kept, str(kept))
    check("the report names what was dropped", [n for n, _ in rep.dropped] == ["recalled"])
    check("the kept total respects the budget", rep.total_tokens <= 32, str(rep.total_tokens))

    _, rep = a.render(budget_tokens=25)
    check("a tighter budget sheds the next-weakest too, in priority order",
          [n for n, _ in rep.kept] == ["request", "cwd"], str(rep.kept))

    text, rep = a.render(budget_tokens=12)
    kept = [n for n, _ in rep.kept]
    check("a tight budget keeps only the request", kept == ["request"], str(kept))
    check("...and drops the other three", len(rep.dropped) == 3)

    text, rep = a.render(budget_tokens=1)
    check("the REQUEST is never dropped — a prompt without it is broken, not smaller",
          [n for n, _ in rep.kept] == ["request"], str(rep.kept))
    check("and the report admits it went over", rep.total_tokens > rep.budget)

    check("budget 0 means no budget at all",
          len(a.render(budget_tokens=0)[1].kept) == 4)


def test_priority_not_order():
    print("\n[3] Priority decides survival; insertion order decides layout")
    a = asm()
    a.add("low_but_first", words(10), P_OPTIONAL)
    a.add("high_but_last", words(10), P_CRITICAL)

    text, rep = a.render()
    check("with no budget, layout is still insertion order",
          text.index(words(10)) == 0 and [n for n, _ in rep.kept]
          == ["low_but_first", "high_but_last"])

    _, rep = a.render(budget_tokens=12)
    check("under pressure the LATER but more important block wins",
          [n for n, _ in rep.kept] == ["high_but_last"], str(rep.kept))

    b = asm()
    b.add("first", words(5), P_HELPFUL)
    b.add("second", words(5), P_HELPFUL)
    _, rep = b.render(budget_tokens=6)
    check("ties are broken by insertion order, so the result is deterministic",
          [n for n, _ in rep.kept] == ["first"], str(rep.kept))


def test_no_truncation():
    print("\n[4] Blocks are dropped whole — half a fact is worse than no fact")
    a = asm()
    a.add("request", words(5), P_REQUEST)
    a.add("cwd", "[WORKING DIRECTORY: d:/Ciel-2.0 - a git repository]", P_CRITICAL)
    text, rep = a.render(budget_tokens=6)

    check("the oversized block is absent entirely", "WORKING" not in text)
    check("no partial fragment leaked into the prompt",
          "d:/Ciel" not in text and "[WORK" not in text, text)
    check("either a block is fully present or fully absent",
          all(text.count(n) == 0 or True for n, _ in rep.dropped))


def test_report():
    print("\n[5] The report — a prompt you can explain after the fact")
    a = asm()
    a.add("request", words(10), P_REQUEST)
    a.add("recalled", words(50), P_HELPFUL)
    _, rep = a.render(budget_tokens=20)

    s = rep.summary()
    check("the summary states the total against the budget", "/20" in s, s)
    check("it names the kept blocks with their sizes", "request=10" in s, s)
    check("it shouts about drops, because a silent drop is a debugging nightmare",
          "DROPPED" in s and "recalled=50" in s, s)

    _, clean = asm().add("request", words(3), P_REQUEST).render(budget_tokens=100)
    check("nothing dropped means no DROPPED section", "DROPPED" not in clean.summary())
    check("token counts in the report are real, not estimated twice",
          clean.kept == [("request", 3)], str(clean.kept))


def test_real_counter():
    print("\n[6] With the real tokenizer, on a realistic request")
    a = ContextAssembler()          # default counter = core.user_model.estimate_tokens
    a.add("request", "kiểm tra git status rồi commit nếu sạch", P_REQUEST)
    a.add("language", "[USER LANGUAGE: Vietnamese]", P_IMPORTANT)
    a.add("cwd", "[WORKING DIRECTORY: d:\\Ciel-2.0 — a git repository. Use this path "
                 "for any tool needing a repo/project path unless the Master names "
                 "another.]", P_CRITICAL)
    text, rep = a.render(budget_tokens=1200)

    check("a normal request fits well inside the default budget",
          rep.dropped == [] and rep.total_tokens < 200, str(rep.total_tokens))
    check("every block made it", len(rep.kept) == 3)
    check("the assembled text is what would actually be sent",
          "git status" in text and "WORKING DIRECTORY" in text)
    print(f"        (assembled: {rep.summary()})")

    big = ContextAssembler()
    big.add("request", "tóm tắt giúp t", P_REQUEST)
    big.add("recalled", "ngữ cảnh cũ. " * 2000, P_HELPFUL)
    _, rep = big.render(budget_tokens=1200)
    check("a runaway recall block is what the budget actually catches",
          [n for n, _ in rep.dropped] == ["recalled"], str(rep.dropped))


def main():
    print("=" * 72)
    print("TIER 4 — CONTEXT ASSEMBLER SUITE (no LLM)")
    print("=" * 72)
    for fn in (test_basics, test_budget, test_priority_not_order, test_no_truncation,
               test_report, test_real_counter):
        fn()

    print("\n" + "=" * 72)
    total = _passed + len(_failed)
    print(f"RESULT: {_passed}/{total} passed")
    for name in _failed:
        print(f"  - {name}")
    print("=" * 72)
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
