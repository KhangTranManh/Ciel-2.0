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

from agent_system.utils.usage import extract_usage, format_usage
ds_meta = SimpleNamespace(usage_metadata=None, response_metadata={"token_usage": {
    "prompt_tokens": 100, "completion_tokens": 5, "total_tokens": 105, "prompt_cache_hit_tokens": 64}})
check("extract cached from DeepSeek prompt_cache_hit_tokens", extract_usage(ds_meta)["cached"] == 64)
oa_meta = SimpleNamespace(usage_metadata={"input_tokens": 100, "output_tokens": 5, "total_tokens": 105},
                          response_metadata={"token_usage": {"prompt_tokens_details": {"cached_tokens": 32}}})
check("extract cached from prompt_tokens_details fallback", extract_usage(oa_meta)["cached"] == 32)
lc_meta = SimpleNamespace(usage_metadata={"input_tokens": 100, "output_tokens": 5, "total_tokens": 105,
                                          "input_token_details": {"cache_read": 80}})
check("extract cached from usage_metadata cache_read", extract_usage(lc_meta)["cached"] == 80)
check("cached key present and 0 when missing", extract_usage(SimpleNamespace())["cached"] == 0
      and extract_usage(None)["cached"] == 0)
check("format_usage appends cached only when >0",
      format_usage("m", extract_usage(lc_meta)) == "model=m in=100 out=5 total=105 cached=80"
      and format_usage("m", {"input": 1, "output": 2, "total": 3}) == "model=m in=1 out=2 total=3")
cl = []
log_blocks(lambda a, b, c: cl.append(c), "w", {"t": "x"}, lc_meta)
check("PROMPT_BLOCKS line ends with cache=80", cl and cl[0].endswith("in=100 cache=80"))

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
