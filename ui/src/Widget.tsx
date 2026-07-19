import { useEffect, useRef, useState } from "react";
import { getCurrentWindow, currentMonitor, PhysicalPosition, PhysicalSize } from "@tauri-apps/api/window";
import { useCiel } from "./hooks/useCiel";
import { Transcript } from "./io/output/Transcript";
import { TextInput } from "./io/input/TextInput";
import { VoiceInput } from "./io/input/VoiceInput";
import { enableSpeaker, disableSpeaker, backendSpeak } from "./io/output/speaker";
import { WidgetErrorBoundary } from "./WidgetErrorBoundary";

// Floating desktop widget — the "as simple as possible" alternative to the full
// dashboard: a small always-on-top bubble that expands into a compact chat panel.
// Chat + voice only, nothing else (no SkillGrid/Vitals/Orb) — reuses the exact same
// bus/ws hook and input/output components the full App does, just in a tiny window
// (see ui/src-tauri/tauri.conf.json's "widget" window).
//
// Draggable like iOS AssistiveTouch via `data-tauri-drag-region` (Tauri's own native
// drag mechanism — confirmed working live). Expand/collapse resizes the SAME window
// in place, anchored to wherever the user last left it.
//
// POSITION TRACKING: earlier versions called win.outerPosition()/outerSize() at
// click-time to compute "where is the window right now" before resizing — that path
// was never confirmed working (drag worked because startDragging() doesn't need to
// read anything back first). This version instead tracks position/size PASSIVELY via
// onMoved/onResized event listeners into a ref, so resizing never depends on a
// synchronous read call succeeding — only on the writes (setSize/setPosition), which
// are the same calls the working drag/initial-placement path already proved out.

const BUBBLE = { width: 64, height: 64 };
const PANEL = { width: 340, height: 460 };
const SCREEN_MARGIN = 16; // gap kept from the screen edge on initial placement

export default function Widget() {
  const { connection, chat, status, send } = useCiel();
  const [expanded, setExpanded] = useState(false);
  const [speakerOn, setSpeakerOn] = useState(false);

  // Best-known current window position/size, kept fresh by onMoved/onResized —
  // never read back synchronously from Tauri at click-time (see note above).
  const known = useRef<{ x: number; y: number; width: number; height: number } | null>(null);

  useEffect(() => {
    const win = getCurrentWindow();
    let unlistenMoved: (() => void) | undefined;
    let unlistenResized: (() => void) | undefined;

    (async () => {
      try {
        // Initial placement: bottom-right of the current monitor.
        await win.setSize(new PhysicalSize(BUBBLE.width, BUBBLE.height));
        const monitor = await currentMonitor();
        const x = monitor ? monitor.position.x + monitor.size.width - BUBBLE.width - SCREEN_MARGIN : SCREEN_MARGIN;
        const y = monitor ? monitor.position.y + monitor.size.height - BUBBLE.height - SCREEN_MARGIN : SCREEN_MARGIN;
        await win.setPosition(new PhysicalPosition(x, y));
        known.current = { x, y, width: BUBBLE.width, height: BUBBLE.height };

        unlistenMoved = await win.onMoved(({ payload }) => {
          if (known.current) { known.current.x = payload.x; known.current.y = payload.y; }
        });
        unlistenResized = await win.onResized(({ payload }) => {
          if (known.current) { known.current.width = payload.width; known.current.height = payload.height; }
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

  // Expand/collapse: resize in place using the LAST KNOWN position (from the
  // onMoved/onResized listeners above), keeping the current bottom-right corner
  // fixed so the panel grows up-and-left from wherever the bubble is.
  const didInitialExpand = useRef(false);
  useEffect(() => {
    if (!didInitialExpand.current) {
      didInitialExpand.current = true; // skip the mount-time run — already placed above
      return;
    }
    const size = expanded ? PANEL : BUBBLE;
    const win = getCurrentWindow();
    const k = known.current;
    const bottomRightX = k ? k.x + k.width : undefined;
    const bottomRightY = k ? k.y + k.height : undefined;

    (async () => {
      try {
        await win.setSize(new PhysicalSize(size.width, size.height));
        if (bottomRightX !== undefined && bottomRightY !== undefined) {
          const newX = bottomRightX - size.width;
          const newY = bottomRightY - size.height;
          await win.setPosition(new PhysicalPosition(newX, newY));
          known.current = { x: newX, y: newY, width: size.width, height: size.height };
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

  if (!expanded) {
    return (
      <button
        type="button"
        data-tauri-drag-region=""
        className="widget-bubble"
        onClick={() => setExpanded(true)}
        onDoubleClick={(e) => { e.preventDefault(); e.stopPropagation(); }}
        title="Drag to move · click to chat with Ciel"
      >
        💬
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
            <button type="button" className="widget-close" onClick={() => setExpanded(false)} title="Collapse">
              ✕
            </button>
          </div>
        </div>
        <Transcript chat={chat} status={status} />
        <div className="input-dock">
          <TextInput onSubmit={send} disabled={connection !== "open"} />
          <VoiceInput onSubmit={send} disabled={connection !== "open"} />
        </div>
      </div>
    </WidgetErrorBoundary>
  );
}
