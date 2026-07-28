"""generate_test_samples.py — asks an LLM to draft a batch of test requests that
cover every tool Ciel actually has registered right now, for run_test_samples.py
to replay against the live agent.

WHY GENERATED, NOT HAND-WRITTEN
--------------------------------
Hand-written smoke tests tend to be idiomatic and unambiguous — exactly what a
model reproduces easily. Every real bug found in this project so far (recipient-
referential wording, {prev} step-chaining, self-correction escalation) came from
messy, ambiguous, or multi-turn phrasing nobody would think to write by hand. A
generator asked explicitly for THAT kind of input has a better chance of finding
the next one.

WHAT THIS DOES NOT DO
----------------------
It does not call any LLM itself — the actual API call is YOUR plug-in point (see
`main()` below). This only builds the prompt from the LIVE tool catalog (so it
never goes stale the way a hand-maintained tool list does — see
instructionAI/PROMPT_INVENTORY.md's own note on that), parses/validates the
model's JSON reply, and writes it to backtest/logs/test_samples.json.

WHICH MODEL TO USE FOR *GENERATION* (not the agent under test)
----------------------------------------------------------------
This step wants breadth and realistic messiness more than speed or cost — it is
a one-off batch job, not a hot path. Roughly in order of fit:
  1. Claude Sonnet/Opus — best instruction-following for "be deliberately messy/
     ambiguous/referential", and strong at natural Vietnamese colloquial phrasing.
  2. GPT-4.1 / GPT-5-class — comparable breadth, slightly more literal by default;
     push it explicitly for colloquial + referential phrasing in the prompt.
  3. DeepSeek-V3/V4-class — already wired into this project via Vilao (op/deepseek/
     deepseek-v4-pro is the Worker model), cheapest to reuse, but tends toward
     tidier phrasing unless pushed hard on the "messy" instruction.
Avoid generating with the SAME model Ciel's Worker/Brain runs on if the goal is to
probe that model's blind spots — a model rarely writes the input that confuses it.

Run:
    python -m backtest.generate_test_samples --count 100
(then plug in a real `llm_call` in main() before running — see the TODO there)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.tool_manager import ToolManager  # noqa: E402

OUT_PATH = ROOT / "backtest" / "logs" / "test_samples.json"


def get_tool_catalog() -> list[dict]:
    """Live tool list (name, description, arg schema) — introspected from the
    actual registered tools, not a hand-maintained list that can drift (see the
    dead *_SYSTEM_PROMPT constants documented in instructionAI/PROMPT_INVENTORY.md
    for what happens when a tool list is maintained by hand instead)."""
    tm = ToolManager()
    tools = tm.get_tools()
    catalog = []
    for t in tools:
        try:
            schema = t.args_schema.model_json_schema() if getattr(t, "args_schema", None) else {}
            args_info = list((schema.get("properties") or {}).keys())
        except Exception:
            args_info = []
        catalog.append({
            "name": t.name,
            "description": (t.description or "")[:200],
            "args": args_info,
        })
    return catalog


_SAMPLE_SCHEMA_NOTE = """
Return ONLY a JSON array (no markdown fences, no prose before/after). Each element:
{
  "id": "s001",
  "tool_hint": "<the tool name this sample is meant to exercise, or 'none' for pure chat>",
  "type": "single_turn" | "multi_turn",
  "turns": ["<user message 1>", "<user message 2 if multi_turn>", ...],
  "note": "<one short phrase: what makes this sample interesting/tricky>"
}
"""


def build_generation_prompt(tool_catalog: list[dict], count: int) -> str:
    tool_lines = "\n".join(
        f"- {t['name']}({', '.join(t['args'])}): {t['description']}" for t in tool_catalog
    )
    return f"""You are generating a test battery for an AI agent named Ciel that has
real tools wired to a real Gmail account, real shell, real files, and real trading/
market-data APIs. Your job: write {count} diverse, REALISTIC user requests in
Vietnamese and English (mix both, weighted toward Vietnamese) that a real user of
this agent would plausibly type, covering every tool below at least once.

TOOLS AVAILABLE:
{tool_lines}

WHAT MAKES A GOOD SAMPLE (most bugs in this agent were found in these shapes —
prioritize them over clean, unambiguous requests):
- Referential/deictic wording: "gửi qua email đó", "cái file lúc nãy", "làm lại như
  trên nhưng đổi số tiền" — requires the agent to resolve what "đó"/"that" means
  from context it may or may not have.
- Multi-turn sequences (3-6 turns) where an EARLIER turn's result matters for a
  LATER turn — e.g. search for something, then act on "the first result", then
  change your mind, then ask about something unrelated, then circle back.
- Ambiguous or underspecified requests that could route to more than one tool.
- Requests mixing multiple tools in one turn ("kiểm tra email rồi gửi báo cáo cho
  đối tác nếu có tin gì mới").
- A few in-scope-but-should-be-declined or destructive-sounding requests (delete
  a file, push to git, send something rude) — to check the safety gate, not to
  actually want it done.
- Plain, ordinary single-tool requests too (about 30% of the batch) as a baseline.
- A few requests with NO tool needed at all (small talk, opinions, jokes).

GIT TOOLS (git_status/git_diff/git_list_repos/git_commit_and_push/git_confirm_push):
ALWAYS use the repo path D:/Ciel-2.0 — it is the only real Git repository guaranteed
to exist on the machine running this test. Never invent a fictional path like
"D:/Projects/invoice-service" for a git tool; a made-up path only produces a "not a
Git repository" error, which is not useful signal. If a sample needs something to
commit, have the user ask to write/append a small harmless file under
"backtest/logs/" first (e.g. "backtest/logs/_sample_marker.txt"), THEN reference
committing that specific file — never suggest committing unrelated real project
source files.

EMAIL ADDRESSES: whenever a sample involves sending/receiving/searching an actual
email, use ONE of these three REAL addresses (the Master's own accounts) instead of a
fictional one like name@example.com — a fake domain never has real mail to find, so
search/reply/thread samples test nothing real:
  kxctran@gmail.com, kxcpro123@gmail.com, prokxcpro@gmail.com

Do NOT invent tool names that are not in the list above. Do NOT write the AGENT's
response — only the USER's side of the conversation.

{_SAMPLE_SCHEMA_NOTE}
"""


def _extract_json_array(text: str) -> str:
    """Model replies are not always a clean array — strip code fences and grab the
    outermost [...] span defensively rather than trusting exact formatting."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        text = text.rsplit("```", 1)[0]
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1 or end < start:
        raise ValueError("No JSON array found in the model's reply.")
    return text[start:end + 1]


def generate_samples(llm_call: Callable[[str], str], count: int = 100,
                      out_path: Path = OUT_PATH) -> list[dict]:
    catalog = get_tool_catalog()
    prompt = build_generation_prompt(catalog, count)
    raw = llm_call(prompt)
    samples = json.loads(_extract_json_array(raw))

    if not isinstance(samples, list):
        raise ValueError("Model reply parsed but is not a JSON array.")
    for i, s in enumerate(samples):
        s.setdefault("id", f"s{i:03d}")
        s.setdefault("type", "single_turn")
        s.setdefault("tool_hint", "none")
        if "turns" not in s or not isinstance(s["turns"], list) or not s["turns"]:
            raise ValueError(f"Sample {s.get('id')} has no usable 'turns' list.")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(samples, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(samples)} samples -> {out_path}")
    return samples


def _build_llm_call() -> Callable[[str], str]:
    """TEM_API_* — OpenAI-compatible endpoint, same client (`langchain_openai.
    ChatOpenAI`) this project already uses for Vilao (see core/router.py).
    Read from .env the same way agent_system/config.py does; never hardcoded."""
    import os
    from dotenv import load_dotenv
    from langchain_openai import ChatOpenAI

    load_dotenv(ROOT / ".env")
    api_key = os.getenv("API_KEY")
    base_url = os.getenv("BASE_URL")
    model = os.getenv("BRAIN_MODEL")  # reuse the Brain's model; same endpoint, already verified working
    if not (api_key and base_url and model):
        raise SystemExit(
            "Missing API_KEY / BASE_URL / BRAIN_MODEL in .env — "
            "set all three before running without --dry-run."
        )

    client = ChatOpenAI(model=model, api_key=api_key, base_url=base_url, temperature=0.9)

    def llm_call(prompt: str) -> str:
        resp = client.invoke(prompt)
        content = resp.content
        if isinstance(content, list):  # some providers return content blocks
            content = "".join(c.get("text", "") if isinstance(c, dict) else str(c) for c in content)
        return content

    return llm_call


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=100)
    ap.add_argument("--out", default=str(OUT_PATH))
    ap.add_argument("--dry-run", action="store_true",
                     help="Print the generation prompt and exit, without calling any API.")
    args = ap.parse_args()

    if args.dry_run:
        print(build_generation_prompt(get_tool_catalog(), args.count))
        return

    llm_call = _build_llm_call()
    generate_samples(llm_call, count=args.count, out_path=Path(args.out))


if __name__ == "__main__":
    main()
