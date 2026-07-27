import { useEffect, useRef, useState } from "react";
import {
  getCurrentWindow,
  currentMonitor,
  PhysicalPosition,
  LogicalSize,
} from "@tauri-apps/api/window";
import { useCiel } from "./hooks/useCiel";
import { Transcript } from "./io/output/Transcript";
import { TextInput } from "./io/input/TextInput";
import { VoiceInput } from "./io/input/VoiceInput";
import { ConfirmDialog } from "./components/ConfirmDialog";
import { enableSpeaker, disableSpeaker, backendSpeak } from "./io/output/speaker";
import { WidgetErrorBoundary } from "./WidgetErrorBoundary";

// Floating desktop widget — the "as simple as possible" alternative to the full
// dashboard: a small always-on-top bubble that expands into a compact chat panel.
// Chat + voice + safety confirm — reuses the same bus/ws hook and I/O components
// as App, just in a tiny window (src-tauri/tauri.conf.json "widget" window).
//
// Browser-only `npm run dev` never loads this file as the root (main.tsx falls
// back to App when not in a Tauri context).
//
// Sizes are logical CSS pixels via LogicalSize so HiDPI screens match the CSS
// 64×64 bubble / 340×460 panel. Position still uses physical coords from monitor
// + onMoved (those events report physical units).

const BUBBLE = { width: 64, height: 64 };
const PANEL = { width: 340, height: 460 };
const SCREEN_MARGIN = 16;

export default function Widget() {
  const { connection, chat, status, pendingConfirm, send, respondConfirm, cancel } = useCiel();
  const [expanded, setExpanded] = useState(false);
  const [speakerOn, setSpeakerOn] = useState(false);

  // Best-known current window position/size (physical), kept fresh by onMoved/onResized.
  const known = useRef<{ x: number; y: number; width: number; height: number } | null>(null);

  // Safety confirm must be visible even if the bubble is collapsed.
  useEffect(() => {
    if (pendingConfirm) setExpanded(true);
  }, [pendingConfirm]);

  useEffect(() => {
    const win = getCurrentWindow();
    let unlistenMoved: (() => void) | undefined;
    let unlistenResized: (() => void) | undefined;

    (async () => {
      try {
        await win.setSize(new LogicalSize(BUBBLE.width, BUBBLE.height));
        const monitor = await currentMonitor();
        // Monitor position/size are physical; approximate scale for margin placement.
        const scale = monitor ? monitor.scaleFactor || 1 : 1;
        const bw = Math.round(BUBBLE.width * scale);
        const bh = Math.round(BUBBLE.height * scale);
        const margin = Math.round(SCREEN_MARGIN * scale);
        const x = monitor
          ? monitor.position.x + monitor.size.width - bw - margin
          : SCREEN_MARGIN;
        const y = monitor
          ? monitor.position.y + monitor.size.height - bh - margin
          : SCREEN_MARGIN;
        await win.setPosition(new PhysicalPosition(x, y));
        known.current = { x, y, width: bw, height: bh };

        unlistenMoved = await win.onMoved(({ payload }) => {
          if (known.current) {
            known.current.x = payload.x;
            known.current.y = payload.y;
          }
        });
        unlistenResized = await win.onResized(({ payload }) => {
          if (known.current) {
            known.current.width = payload.width;
            known.current.height = payload.height;
          }
        });
      } catch (e) {
        console.error("[widget] initial placement/listeners failed:", e);
      }
    })();

    return () => {
      unlistenMoved?.();
      unlistenResized?.();
    };
    // Mount-only: initial placement and listeners must be set up exactly once.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Expand/collapse: resize in place using last known physical position, keep
  // bottom-right corner fixed so the panel grows up-and-left from the bubble.
  const didInitialExpand = useRef(false);
  useEffect(() => {
    if (!didInitialExpand.current) {
      didInitialExpand.current = true;
      return;
    }
    const size = expanded ? PANEL : BUBBLE;
    const win = getCurrentWindow();
    const k = known.current;
    const bottomRightX = k ? k.x + k.width : undefined;
    const bottomRightY = k ? k.y + k.height : undefined;

    (async () => {
      try {
        await win.setSize(new LogicalSize(size.width, size.height));
        if (bottomRightX !== undefined && bottomRightY !== undefined) {
          // After LogicalSize, onResized will update known; approximate physical
          // target from previous scale so we don't jump before the event lands.
          const scale =
            k && k.width > 0 ? k.width / (expanded ? BUBBLE.width : PANEL.width) : 1;
          const pw = Math.round(size.width * scale);
          const ph = Math.round(size.height * scale);
          const newX = bottomRightX - pw;
          const newY = bottomRightY - ph;
          await win.setPosition(new PhysicalPosition(newX, newY));
          known.current = { x: newX, y: newY, width: pw, height: ph };
        }
      } catch (e) {
        console.error("[widget] expand/collapse resize failed:", e);
      }
    })();
  }, [expanded]);

  useEffect(() => {
    if (speakerOn) enableSpeaker(backendSpeak);
    else disableSpeaker();
    return () => disableSpeaker();
  }, [speakerOn]);

  const busy = Boolean(status);

  if (!expanded) {
    return (
      <button
        type="button"
        data-tauri-drag-region=""
        className={`widget-bubble${pendingConfirm ? " needs-confirm" : ""}`}
        onClick={() => setExpanded(true)}
        onDoubleClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
        }}
        title={
          pendingConfirm
            ? "Safety confirmation waiting — click to open"
            : "Drag to move · click to chat with Ciel"
        }
      >
        {pendingConfirm ? "⚠" : "💬"}
      </button>
    );
  }

  return (
    <WidgetErrorBoundary>
      <div className="widget-panel widget-panel-enter">
        <div className="widget-header" data-tauri-drag-region="">
          <span className="widget-title">Ciel</span>
          <div className="widget-header-actions">
            <button
              type="button"
              className="speaker-toggle"
              aria-pressed={speakerOn}
              onClick={() => setSpeakerOn((s) => !s)}
              title={speakerOn ? "Mute" : "Read replies aloud"}
            >
              {speakerOn ? "🔊" : "🔇"}
            </button>
            <button
              type="button"
              className="widget-close"
              onClick={() => setExpanded(false)}
              title="Collapse"
            >
              ✕
            </button>
          </div>
        </div>
        <Transcript chat={chat} status={status} />
        <div className="input-dock liquid-glass">
          <TextInput onSubmit={send} disabled={connection !== "open"} />
          {busy ? (
            <button
              type="button"
              className="cancel-btn"
              onClick={cancel}
              title="Stop at the next step boundary"
            >
              Stop
            </button>
          ) : (
            <VoiceInput onSubmit={send} disabled={connection !== "open"} />
          )}
        </div>
        {pendingConfirm && <ConfirmDialog request={pendingConfirm} onRespond={respondConfirm} />}
      </div>
    </WidgetErrorBoundary>
  );
}
