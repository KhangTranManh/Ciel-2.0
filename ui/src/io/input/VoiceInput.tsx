// ─────────────────────────────────────────────────────────────────────────────
// VOICE INPUT — STUB / EXTENSION POINT (not wired into the UI yet)
//
// This is the "leave room for voice" seam on the INPUT side. When you build voice,
// implement STT here and call `onSubmit(transcript)` — the exact same contract the
// keyboard TextInput uses. The backend needs ZERO changes: a spoken sentence becomes
// text and flows through ciel.send() like any typed message.
//
// Recommended implementations (pick per target):
//   • Browser dev / web build:  Web Speech API (window.SpeechRecognition)
//   • Tauri desktop build:      a native STT plugin, or stream mic audio to a
//                               local/remote STT service, then call onSubmit(text)
//
// Deliberately not imported anywhere yet — it compiles as a standalone module so the
// seam is real and typechecked, without adding an unfinished button to the live UI.
// ─────────────────────────────────────────────────────────────────────────────

export interface VoiceInputProps {
  onSubmit: (text: string) => void;
  disabled?: boolean;
}

export type VoiceState = "idle" | "listening" | "unsupported";

// Placeholder factory documenting the intended shape. Replace the body when
// implementing; keep the `onSubmit(text)` contract identical to TextInput.
export function createVoiceInput(_props: VoiceInputProps): {
  start: () => void;
  stop: () => void;
  state: VoiceState;
} {
  return {
    start: () => console.warn("[voice-in] not implemented — see VoiceInput.tsx"),
    stop: () => {},
    state: "unsupported",
  };
}
