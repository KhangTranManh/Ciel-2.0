"""
Quick API usage report for the autonomous pipeline.

- DeepSeek: queries /user/balance for real remaining credit.
- Gemini:   no public usage endpoint; estimates calls from state.cycle_count
            (per-cycle: 1 Brain pro + 1 Worker flash on normal cycles, 0 on chaos).

Run:  python -m autonomous_pipeline.api_usage
"""
import json
import os
from pathlib import Path

import requests
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# ---------------------------------------------------------------------------
# Pricing (USD per million tokens) — adjust if rates change
# deepseek-v4-pro assumed ≈ deepseek-chat tier pricing
# ---------------------------------------------------------------------------
PRICING = {
    "deepseek_input":   0.27,   # $/M input tokens  (cache miss)
    "deepseek_output":  1.10,   # $/M output tokens
    "gemini_pro_input":   1.25, # $/M input tokens  (gemini-2.5-pro, ≤200k ctx)
    "gemini_pro_output":  2.50, # $/M output tokens
    "gemini_flash_input":  0.075, # $/M input tokens (gemini-2.5-flash)
    "gemini_flash_output": 0.30,  # $/M output tokens
}

# Average token estimates per call (rough; tune if you log token counts)
AVG_TOKENS = {
    # Master: system prompt + lookback, outputs just the task string
    "master_input":  1500,
    "master_output":   60,
    # Judge: system prompt + up to 4 pairs of request+response, JSON eval output
    "judge_input":   3200,
    "judge_output":   900,
    # Brain: system + task, outputs routing JSON
    "brain_input":   1200,
    "brain_output":   150,
    # Worker: system + tool result, outputs formatted response
    "worker_input":  2500,
    "worker_output":  400,
}


def deepseek_balance():
    key = os.getenv("DEEPSEEK_API_KEY")
    if not key:
        return "DEEPSEEK_API_KEY not set."
    try:
        r = requests.get(
            "https://api.deepseek.com/user/balance",
            headers={"Authorization": f"Bearer {key}"},
            timeout=15,
        )
        if r.status_code != 200:
            return f"HTTP {r.status_code}: {r.text[:120]}"
        data = r.json()
        infos = data.get("balance_infos", [])
        if not infos:
            return f"is_available={data.get('is_available')} (no balance_infos)"
        lines = [f"is_available={data.get('is_available')}"]
        for b in infos:
            lines.append(
                f"  {b.get('currency')}: total={b.get('total_balance')} "
                f"granted={b.get('granted_balance')} topped_up={b.get('topped_up_balance')}"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"Error: {e}"


def gemini_estimate():
    if not os.getenv("GEMINI_API_KEY"):
        return "GEMINI_API_KEY not set."
    state_path = BASE_DIR / "autonomous_pipeline" / "state.json"
    if not state_path.exists():
        return "state.json missing — cannot estimate."
    with open(state_path, "r", encoding="utf-8") as f:
        state = json.load(f)
    cycles = state.get("cycle_count", 0)
    chaos = cycles // 10                # 1 in 10 cycles is chaos (no Gemini calls)
    normal = cycles - chaos
    return (
        f"cycles={cycles} (normal={normal}, chaos={chaos})\n"
        f"  Brain (gemini-2.5-pro):   ~{normal} calls\n"
        f"  Worker (gemini-2.5-flash): ~{normal} calls\n"
        f"  Total Gemini est:         ~{normal * 2} calls\n"
        f"  (real numbers higher due to retries; check Google AI Studio for exact quota)"
    )


def deepseek_estimate():
    """Per-cycle estimate from state.json (independent of balance endpoint)."""
    state_path = BASE_DIR / "autonomous_pipeline" / "state.json"
    if not state_path.exists():
        return ""
    with open(state_path, "r", encoding="utf-8") as f:
        state = json.load(f)
    cycles = state.get("cycle_count", 0)
    chaos = cycles // 10
    normal = cycles - chaos
    # Normal: 1 Master + 1 Judge per cycle. Chaos: 1 Judge only.
    return (
        f"cycles={cycles} (normal={normal}, chaos={chaos})\n"
        f"  Master (deepseek-v4-pro): ~{normal} calls\n"
        f"  Judge  (deepseek-v4-pro): ~{cycles} calls\n"
        f"  Total DeepSeek est:       ~{normal + cycles} calls"
    )


def cost_estimate():
    """Estimate total spend in USD from call counts × average token cost."""
    state_path = BASE_DIR / "autonomous_pipeline" / "state.json"
    if not state_path.exists():
        return "state.json missing — cannot estimate."
    with open(state_path, "r", encoding="utf-8") as f:
        state = json.load(f)
    cycles = state.get("cycle_count", 0)
    chaos  = cycles // 10
    normal = cycles - chaos

    def usd(calls, in_tok, out_tok, in_price, out_price):
        return calls * (in_tok / 1e6 * in_price + out_tok / 1e6 * out_price)

    master = usd(normal, AVG_TOKENS["master_input"],  AVG_TOKENS["master_output"],
                 PRICING["deepseek_input"], PRICING["deepseek_output"])
    judge  = usd(cycles, AVG_TOKENS["judge_input"],   AVG_TOKENS["judge_output"],
                 PRICING["deepseek_input"], PRICING["deepseek_output"])
    brain  = usd(normal, AVG_TOKENS["brain_input"],   AVG_TOKENS["brain_output"],
                 PRICING["gemini_pro_input"], PRICING["gemini_pro_output"])
    worker = usd(normal, AVG_TOKENS["worker_input"],  AVG_TOKENS["worker_output"],
                 PRICING["gemini_flash_input"], PRICING["gemini_flash_output"])

    total_ds  = master + judge
    total_gem = brain  + worker
    total     = total_ds + total_gem

    return (
        f"  Master  (deepseek-v4-pro):  ${master:.3f}\n"
        f"  Judge   (deepseek-v4-pro):  ${judge:.3f}\n"
        f"  Brain   (gemini-2.5-pro):   ${brain:.3f}\n"
        f"  Worker  (gemini-2.5-flash): ${worker:.3f}\n"
        f"  ----------------------------------------\n"
        f"  DeepSeek subtotal:          ${total_ds:.3f}\n"
        f"  Gemini   subtotal:          ${total_gem:.3f}\n"
        f"  TOTAL estimated spend:      ${total:.3f}\n"
        f"  (token averages are estimates — see AVG_TOKENS in api_usage.py)"
    )


def main():
    print("=" * 50)
    print("  API Usage Report")
    print("=" * 50)
    print("\n[DeepSeek — real balance from /user/balance]")
    print(deepseek_balance())
    print("\n[DeepSeek — estimated calls (state.json)]")
    print(deepseek_estimate())
    print("\n[Gemini — estimated calls (state.json)]")
    print(gemini_estimate())
    print("\n[Estimated spend (calls × avg tokens × price)]")
    print(cost_estimate())
    print("=" * 50)


if __name__ == "__main__":
    main()
