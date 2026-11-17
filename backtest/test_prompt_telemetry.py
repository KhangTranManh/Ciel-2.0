"""Step-0 telemetry: block-token log lines, evaluate latency/usage. Zero LLM calls.
Run: python -m backtest.test_prompt_telemetry
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from types import SimpleNamespace
from agent_system.utils.usage import format_blocks, log_blocks, estimate_tokens
import core.harness.recovery as recovery

_f = []
def check(n, c):
    print(("PASS " if c else "FAIL ") + n)
    if not c: _f.append(n)

SECRET = "TOPSECRET-PROMPT-BODY"
line = format_blocks("brain_route", {"rules": SECRET * 100, "tools": "t" * 400, "user_msg": "hi"}, 123)
check("block line has no prompt content", "TOPSECRET" not in line and "hi" not in line.split("user_msg")[0])
check("block line has names and provider in", "rules=" in line and "tools=100" in line and "in=123" in line)
check("est_total is sum of blocks", f"est_total={estimate_tokens(SECRET*100)+100+1}" in line)

got = []
log_blocks(lambda a, b, c: got.append((a, b, c)), "worker_generate", {"task": "x" * 40}, SimpleNamespace(usage_metadata={"input_tokens": 9, "output_tokens": 1, "total_tokens": 10}))
check("log_blocks writes PERF/PROMPT_BLOCKS with provider in", got and got[0][:2] == ("PERF", "PROMPT_BLOCKS") and "in=9" in got[0][2])
log_blocks(lambda *a: 1/0, "x", {"a": "b"})
check("telemetry failure swallowed", True)

class LLM:
    def invoke(self, m):
        return SimpleNamespace(content='{"satisfied": true}', usage_metadata={"input_tokens": 50, "output_tokens": 5, "total_tokens": 55})
logs, lat = [], []
fake = SimpleNamespace(
    brain=SimpleNamespace(_router_llm=LLM()),
    router=SimpleNamespace(_log_llm_latency=lambda *a: lat.append(a)),
    _tool_list_str="tool_a, tool_b",
    _log_thought=lambda a, b, c: logs.append((a, b, c)),
    _is_failure_result=lambda r: False,
)
res = recovery._evaluate_result(fake, "do it", "tool_a", {}, "all good " + SECRET)
check("evaluate returns parsed result", res.get("satisfied") is True)
check("evaluate records latency component", lat and lat[0][0] == "brain_evaluate")
check("evaluate logs LLM_CALL usage", any(b == "LLM_CALL" and "in=50" in c for a, b, c in logs))
pb = [c for a, b, c in logs if b == "PROMPT_BLOCKS"]
check("evaluate logs blocks without content", pb and "in=50" in pb[0] and "TOPSECRET" not in pb[0] and "tools=" in pb[0])
sys.exit(1 if _f else 0)
