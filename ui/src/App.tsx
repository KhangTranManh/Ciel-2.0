import { useEffect, useState } from "react";
import { useCiel } from "./hooks/useCiel";
import { VitalsBar } from "./components/VitalsBar";
import { SkillGrid } from "./components/SkillGrid";
import { ConfirmDialog } from "./components/ConfirmDialog";
import { Orb } from "./components/Orb";
import { Transcript } from "./io/output/Transcript";
import { TextInput } from "./io/input/TextInput";
import { VoiceInput, type VoiceState } from "./io/input/VoiceInput";
import {
  enableSpeaker,
  disableSpeaker,
  backendSpeak,
  onSpeakingChange,
  getSpeechAnalyser,
} from "./io/output/speaker";
import type { OrbState } from "./orb";

// Layout: top vitals bar, then three columns — skills (left), the audio-reactive
// orb (center, the centerpiece), and the conversation (right). The orb's state is
// derived from the real conversation and its audio reactivity from the TTS voice.
export default function App() {
  const { connection, chat, vitals, status, pendingConfirm, send, respondConfirm } = useCiel();

  const [speakerOn, setSpeakerOn] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [analyser, setAnalyser] = useState<AnalyserNode | null>(null);
  const [voiceState, setVoiceState] = useState<VoiceState>("idle");

  // Speaker toggle: when on, subscribe backendSpeak to reply events (edge-tts).
  useEffect(() => {
    if (speakerOn) enableSpeaker(backendSpeak);
    else disableSpeaker();
    return () => disableSpeaker();
  }, [speakerOn]);

  // Track TTS speaking + expose its analyser to the orb.
  useEffect(() => {
    return onSpeakingChange((v) => {
      setSpeaking(v);
      setAnalyser(v ? getSpeechAnalyser() : null);
    });
  }, []);

  // Orb state priority: speaking > thinking (awaiting reply) > listening > idle.
  const orbState: OrbState = speaking
    ? "speaking"
    : status
    ? "thinking"
    : voiceState === "listening"
    ? "listening"
    : "idle";

  const statusLabel = speaking
    ? "Speaking…"
    : status
    ? "Thinking…"
    : voiceState === "listening"
    ? "Listening…"
    : connection === "open"
    ? "Ready"
    : "Connecting…";

  return (
    <div className="app">
      <VitalsBar vitals={vitals} connection={connection} />

      <div className="body orb-body">
        <aside className="col-left">
          <SkillGrid activity={vitals?.skills ?? []} />
        </aside>

        <main className="col-orb">
          <div className="orb-logo">
            J.A.R.V.I.S <span>· CIEL 2.0</span>
          </div>
          <Orb state={orbState} analyser={analyser} />
          <div className={`orb-status ${orbState}`}>{statusLabel}</div>
        </main>

        <aside className="col-chat">
          <Transcript chat={chat} status={status} />
          <div className="input-dock">
            <button
              type="button"
              className="speaker-toggle"
              aria-pressed={speakerOn}
              title={speakerOn ? "Ciel is reading replies aloud — click to mute" : "Let Ciel read replies aloud"}
              onClick={() => setSpeakerOn((s) => !s)}
            >
              {speakerOn ? "🔊" : "🔇"}
            </button>
            <TextInput onSubmit={send} disabled={connection !== "open"} />
            <VoiceInput onSubmit={send} disabled={connection !== "open"} onStateChange={setVoiceState} />
          </div>
        </aside>
      </div>

      {pendingConfirm && <ConfirmDialog request={pendingConfirm} onRespond={respondConfirm} />}
    </div>
  );
}
