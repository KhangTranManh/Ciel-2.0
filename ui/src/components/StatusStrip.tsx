import type { ConfirmRequest, Vitals } from "../core/types";
import type { ConnState } from "../hooks/useCiel";
import type { VoiceState } from "../io/input/VoiceInput";

// Status from connection / status / confirm / vitals skills — no thoughts.log.
export function StatusStrip({
  connection,
  status,
  pendingConfirm,
  speaking,
  voiceState,
  vitals,
}: {
  connection: ConnState;
  status: string;
  pendingConfirm: ConfirmRequest | null;
  speaking: boolean;
  voiceState: VoiceState;
  vitals: Vitals | null;
}) {
  const activeSkills =
    vitals?.skills?.filter((s) => s.active).map((s) => s.module.replace(/_ops$/, "")) ?? [];

  let tone: "idle" | "busy" | "confirm" | "speak" | "listen" | "offline" = "idle";
  let label = "Ready";

  if (connection === "closed") {
    tone = "offline";
    label = "Offline — reconnecting…";
  } else if (connection === "connecting") {
    tone = "offline";
    label = "Connecting…";
  } else if (pendingConfirm) {
    tone = "confirm";
    label = `Waiting for approval · ${pendingConfirm.tool_name}`;
  } else if (status === "cancelling") {
    tone = "busy";
    label = "Stopping at the next step boundary…";
  } else if (speaking) {
    tone = "speak";
    label = "Speaking…";
  } else if (status) {
    tone = "busy";
    label =
      activeSkills.length > 0
        ? `Working · ${activeSkills.slice(0, 3).join(", ")}${activeSkills.length > 3 ? "…" : ""}`
        : status === "processing"
        ? "Working…"
        : status;
  } else if (voiceState === "listening") {
    tone = "listen";
    label = "Listening…";
  }

  return (
    <div className={`status-strip liquid-glass tone-${tone}`} role="status" aria-live="polite">
      <span className="status-strip-dot" />
      <span className="status-strip-label">{label}</span>
    </div>
  );
}
