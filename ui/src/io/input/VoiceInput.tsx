import { useEffect, useRef, useState } from "react";

// VOICE INPUT — mic button (browser Web Speech API / SpeechRecognition).
//
// The "leave room for voice" seam, now filled: a spoken sentence becomes text and is
// handed to the SAME `onSubmit` the keyboard TextInput uses — ciel.send(). The backend
// needs zero changes. If the browser has no SpeechRecognition (e.g. some Tauri WebView
// builds), the button renders disabled with a hint instead of disappearing silently.
//
// Note: this uses the BROWSER's on-device/online STT (instant, no backend round-trip),
// which is the right tool for UI voice input. The CLI's core/voice_input.py is a
// separate server-side path for the terminal — the two don't need to share an engine.

type SpeechRecognitionLike = {
  lang: string;
  interimResults: boolean;
  continuous: boolean;
  maxAlternatives: number;
  start: () => void;
  stop: () => void;
  abort: () => void;
  onresult: ((e: any) => void) | null;
  onend: (() => void) | null;
  onerror: ((e: any) => void) | null;
};

function createRecognition(): SpeechRecognitionLike | null {
  const w = window as any;
  const Ctor = w.SpeechRecognition || w.webkitSpeechRecognition;
  return Ctor ? (new Ctor() as SpeechRecognitionLike) : null;
}

export type VoiceState = "idle" | "listening" | "unsupported";

export function VoiceInput({
  onSubmit,
  disabled,
  lang = "vi-VN",
  onStateChange,
}: {
  onSubmit: (text: string) => void;
  disabled?: boolean;
  lang?: string;
  onStateChange?: (state: VoiceState) => void;
}) {
  const [state, setStateRaw] = useState<VoiceState>("idle");
  const recRef = useRef<SpeechRecognitionLike | null>(null);
  // Keep the latest callbacks without re-creating the recognizer every render.
  const cbRef = useRef(onSubmit);
  const stateCbRef = useRef(onStateChange);
  useEffect(() => {
    cbRef.current = onSubmit;
    stateCbRef.current = onStateChange;
  }, [onSubmit, onStateChange]);

  // Single setter that also notifies the parent (for driving the orb's state).
  const setState = (s: VoiceState) => {
    setStateRaw(s);
    stateCbRef.current?.(s);
  };

  useEffect(() => {
    const rec = createRecognition();
    if (!rec) {
      setState("unsupported");
      return;
    }
    rec.lang = lang;
    rec.interimResults = false;
    rec.continuous = false;
    rec.maxAlternatives = 1;
    rec.onresult = (e: any) => {
      const text = String(e?.results?.[0]?.[0]?.transcript ?? "").trim();
      if (text) cbRef.current(text);
    };
    rec.onend = () => setState("idle");
    rec.onerror = () => setState("idle");
    recRef.current = rec;
    return () => {
      try {
        rec.abort();
      } catch {
        /* ignore */
      }
      recRef.current = null;
    };
  }, [lang]);

  const unsupported = state === "unsupported";

  const toggle = () => {
    const rec = recRef.current;
    if (!rec) return;
    if (state === "listening") {
      rec.stop();
      setState("idle");
    } else {
      try {
        rec.start();
        setState("listening");
      } catch {
        /* start() throws if already started — ignore */
      }
    }
  };

  return (
    <button
      type="button"
      className={`mic-btn${state === "listening" ? " listening" : ""}`}
      onClick={toggle}
      disabled={disabled || unsupported}
      aria-pressed={state === "listening"}
      title={
        unsupported
          ? "Voice input not supported in this browser"
          : state === "listening"
          ? "Listening… click to stop"
          : "Speak your message"
      }
    >
      🎤
    </button>
  );
}
