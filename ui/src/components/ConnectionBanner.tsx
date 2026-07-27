import type { ConnState } from "../hooks/useCiel";

// Non-blocking banner when the WS is not ready. No new protocol — connection
// state already comes from bus "connection" events.
export function ConnectionBanner({ connection }: { connection: ConnState }) {
  if (connection === "open") return null;

  const connecting = connection === "connecting";

  return (
    <div
      className={`connection-banner${connecting ? " connecting" : " offline"}`}
      role="status"
      aria-live="polite"
    >
      <span className="connection-banner-dot" />
      <div className="connection-banner-text">
        {connecting ? (
          <>
            <strong>Connecting</strong>
            <span> to Ciel backend (ws://…/ws). Start with </span>
            <code>python main_api.py</code>
            <span> if this hangs.</span>
          </>
        ) : (
          <>
            <strong>Offline</strong>
            <span> — socket closed. Reconnecting automatically… Messages will not send until open.</span>
          </>
        )}
      </div>
    </div>
  );
}
