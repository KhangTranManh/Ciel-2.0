import { useEffect, useRef, useState } from "react";
import { useCiel } from "./hooks/useCiel";
import { VitalsBar } from "./components/VitalsBar";
import { ConnectionBanner } from "./components/ConnectionBanner";
import { SkillGrid } from "./components/SkillGrid";
import { ConfirmDialog } from "./components/ConfirmDialog";
import { StatusStrip } from "./components/StatusStrip";
import { Transcript } from "./io/output/Transcript";
import { TextInput } from "./io/input/TextInput";
import { VoiceInput, type VoiceState } from "./io/input/VoiceInput";
import {
  enableSpeaker,
  disableSpeaker,
  backendSpeak,
  onSpeakingChange,
} from "./io/output/speaker";

// Workbench: skills rail · chat · prompt. No thoughts.log stream in the UI.
// Wire protocol unchanged (backend may still send thought frames; we ignore them).
export default function App() {
  const {
    connection,
    chat,
    vitals,
    status,
    pendingConfirm,
    send,
    respondConfirm,
    cancel,
    retryLast,
  } = useCiel();

  const [speakerOn, setSpeakerOn] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [voiceState, setVoiceState] = useState<VoiceState>("idle");
  const [skillsCollapsed, setSkillsCollapsed] = useState(false);
  const [focusToken, setFocusToken] = useState(0);
  const prevStatus = useRef(status);

  useEffect(() => {
    if (speakerOn) enableSpeaker(backendSpeak);
    else disableSpeaker();
    return () => disableSpeaker();
  }, [speakerOn]);

  useEffect(() => {
    return onSpeakingChange(setSpeaking);
  }, []);

  useEffect(() => {
    const wasBusy = Boolean(prevStatus.current);
    const nowIdle = !status;
    if (wasBusy && nowIdle && connection === "open" && !pendingConfirm) {
      setFocusToken((n) => n + 1);
    }
    prevStatus.current = status;
  }, [status, connection, pendingConfirm]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (pendingConfirm) return;
      if (status) {
        e.preventDefault();
        cancel();
        return;
      }
      setSkillsCollapsed(true);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [pendingConfirm, status, cancel]);

  const busy = Boolean(status);
  const activeSkillCount = vitals?.skills?.filter((s) => s.active).length ?? 0;

  return (
    <div className="app agents-shell">
      <VitalsBar vitals={vitals} connection={connection} />
      <ConnectionBanner connection={connection} />

      <div className={`body agents-body${skillsCollapsed ? " skills-collapsed" : ""}`}>
        <aside className="skills-rail" aria-label="Skills">
          <div className="rail-head">
            <button
              type="button"
              className="rail-toggle"
              onClick={() => setSkillsCollapsed((c) => !c)}
              title={skillsCollapsed ? "Expand skills" : "Collapse skills"}
              aria-expanded={!skillsCollapsed}
            >
              {skillsCollapsed ? "›" : "‹"}
            </button>
            {!skillsCollapsed && (
              <span className="rail-title">
                Skills
                {activeSkillCount > 0 && <span className="chip-badge">{activeSkillCount}</span>}
              </span>
            )}
          </div>
          {!skillsCollapsed && <SkillGrid activity={vitals?.skills ?? []} />}
        </aside>

        <main className="col-chat">
          <header className="session-header">
            <div className="session-title">
              <span className="session-dot" data-state={busy ? "busy" : connection} />
              <span>
                Ciel <em className="brand-serif">2.0</em>
              </span>
              <span className="session-meta">
                {speaking
                  ? "speaking"
                  : busy
                  ? "running"
                  : voiceState === "listening"
                  ? "listening"
                  : connection === "open"
                  ? "ready"
                  : connection}
              </span>
            </div>
          </header>

          <StatusStrip
            connection={connection}
            status={status}
            pendingConfirm={pendingConfirm}
            speaking={speaking}
            voiceState={voiceState}
            vitals={vitals}
          />

          <Transcript
            chat={chat}
            status={status}
            connection={connection}
            onRetry={retryLast}
          />

          <div className="input-dock agents-prompt">
            <div className="prompt-row">
              <span className="prompt-prefix" aria-hidden>
                ›
              </span>
              <TextInput
                onSubmit={send}
                disabled={connection !== "open"}
                autoFocus={connection === "open" && !pendingConfirm}
                focusToken={focusToken}
              />
              {busy ? (
                <button
                  type="button"
                  className="cancel-btn"
                  onClick={cancel}
                  title="Stop at next step boundary (Esc)"
                >
                  Stop
                </button>
              ) : (
                <VoiceInput
                  onSubmit={send}
                  disabled={connection !== "open"}
                  onStateChange={setVoiceState}
                />
              )}
              <button
                type="button"
                className="speaker-toggle"
                aria-pressed={speakerOn}
                title={speakerOn ? "Mute TTS" : "Read aloud (POST /tts)"}
                onClick={() => setSpeakerOn((s) => !s)}
              >
                <span aria-hidden>{speakerOn ? "🔊" : "🔇"}</span>
              </button>
            </div>
            <div className="prompt-hint">
              {busy
                ? "Esc or Stop — interrupt at next step boundary"
                : "Enter send · Shift+Enter newline · optional mic / TTS"}
            </div>
          </div>
        </main>
      </div>

      {pendingConfirm && <ConfirmDialog request={pendingConfirm} onRespond={respondConfirm} />}
    </div>
  );
}
