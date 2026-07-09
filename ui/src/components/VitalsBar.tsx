import type { Vitals } from "../core/types";
import type { ConnState } from "../hooks/useCiel";

// Top status bar: connection, the always-on cognition tiers, real per-tier LLM call
// counts (from the [LLM_CALL] counter, not an estimate), and GPU VRAM.
export function VitalsBar({ vitals, connection }: { vitals: Vitals | null; connection: ConnState }) {
  const calls = vitals?.llm_calls ?? {};
  const total = vitals?.llm_calls_total ?? 0;

  return (
    <div className="vitals-bar">
      <div className="brand">
        <span className="brand-mark">CIEL</span>
        <span className="brand-sub">2.0</span>
      </div>

      <div className={`conn conn-${connection}`}>
        <span className="conn-dot" />
        {connection}
      </div>

      <div className="tiers">
        {vitals &&
          Object.entries(vitals.tiers).map(([name, on]) => (
            <span key={name} className={`tier ${on ? "tier-on" : "tier-off"}`}>
              {name}
            </span>
          ))}
      </div>

      <div className="cost" title="LLM calls this session (BRAIN / WORKER / MIDDLEWARE)">
        <span className="cost-label">LLM calls</span>
        <span className="cost-total">{total}</span>
        <span className="cost-breakdown">
          {["BRAIN", "WORKER", "MIDDLEWARE"].map((k) => (
            <span key={k} className="cost-part">
              {k[0]}:{calls[k] ?? 0}
            </span>
          ))}
        </span>
      </div>

      {vitals && (
        <div className="vram" title="GPU VRAM">
          VRAM {vitals.vram_used}/{vitals.vram_total}G
        </div>
      )}
    </div>
  );
}
