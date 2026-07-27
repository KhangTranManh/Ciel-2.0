import type { Vitals } from "../core/types";
import type { ConnState } from "../hooks/useCiel";

function formatUsd(n: number | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  if (n === 0) return "$0";
  if (n < 0.01) return `$${n.toFixed(4)}`;
  return `$${n.toFixed(3)}`;
}

function formatTokens(n: number | undefined): string {
  if (n == null) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 10_000) return `${Math.round(n / 1000)}k`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

function tierTip(
  k: string,
  calls: Record<string, number>,
  tokens: Record<string, { total?: number; input?: number; output?: number } | undefined>,
  cost: Record<string, number>
): string {
  const t = tokens[k];
  const tok = t?.total ?? 0;
  const inn = t?.input ?? 0;
  const out = t?.output ?? 0;
  return [
    k,
    `${calls[k] ?? 0} calls`,
    `${tok} tokens (${inn} in / ${out} out)`,
    formatUsd(cost[k]),
  ].join(" · ");
}

export function VitalsBar({ vitals, connection }: { vitals: Vitals | null; connection: ConnState }) {
  const calls = vitals?.llm_calls ?? {};
  const totalCalls = vitals?.llm_calls_total ?? 0;
  const totalTokens = vitals?.llm_tokens_total;
  const totalCost = vitals?.llm_cost_usd_total;
  const costByTier = vitals?.llm_cost_usd ?? {};
  const tokensByTier = vitals?.llm_tokens ?? {};

  const showVram =
    vitals != null &&
    !(vitals.vram_used === 3.2 && vitals.vram_total === 12) &&
    vitals.vram_total > 0;

  const sessionTip = [
    "Session totals (from vitals WS)",
    `${totalCalls} calls`,
    `${formatTokens(totalTokens)} tokens`,
    formatUsd(totalCost),
  ].join(" · ");

  return (
    <header className="vitals-bar liquid-glass" aria-label="Session vitals">
      <div className="brand">
        <span className="brand-mark">
          Ciel <em className="brand-serif">2.0</em>
        </span>
      </div>

      <div className={`conn conn-${connection}`} title={`WebSocket ${connection}`}>
        <span className="conn-dot" aria-hidden />
        <span className="sr-only">Connection: </span>
        {connection}
      </div>

      <div className="tiers" aria-label="Active tiers">
        {vitals &&
          Object.entries(vitals.tiers).map(([name, on]) => (
            <span
              key={name}
              className={`tier ${on ? "tier-on" : "tier-off"}`}
              title={on ? `${name} loaded` : `${name} off`}
            >
              {name}
            </span>
          ))}
      </div>

      <div className="cost" title={sessionTip}>
        <span className="cost-label">LLM</span>
        <span className="cost-total">{totalCalls}</span>
        <span className="cost-unit">calls</span>
        <span className="cost-sep" aria-hidden>
          ·
        </span>
        <span className="cost-total">{formatTokens(totalTokens)}</span>
        <span className="cost-unit">tok</span>
        <span className="cost-sep" aria-hidden>
          ·
        </span>
        <span className="cost-total cost-usd">{formatUsd(totalCost)}</span>
        <span className="cost-breakdown">
          {["BRAIN", "WORKER", "MIDDLEWARE"].map((k) => (
            <span key={k} className="cost-part" title={tierTip(k, calls, tokensByTier, costByTier)}>
              {k[0]}:{calls[k] ?? 0}
            </span>
          ))}
        </span>
      </div>

      {showVram && (
        <div className="vram" title="GPU VRAM (nvidia-smi)">
          VRAM {vitals!.vram_used}/{vitals!.vram_total}G
        </div>
      )}
    </header>
  );
}
