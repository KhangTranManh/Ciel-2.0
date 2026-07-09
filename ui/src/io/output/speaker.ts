// ─────────────────────────────────────────────────────────────────────────────
// VOICE OUTPUT — STUB / EXTENSION POINT (not enabled yet)
//
// This is the "leave room for voice" seam on the OUTPUT side. Ciel's replies arrive
// on the bus "response" channel. To speak them, enable a subscriber here — the
// transcript UI already listens to the same event, so text + speech stay in sync and
// NO component needs to change.
//
// enableSpeaker() shows the exact wiring. When you build voice out:
//   • Browser dev / web build:  window.speechSynthesis.speak(...)
//   • Tauri desktop build:      a native TTS plugin, or POST text to a TTS service.
//
// Future backend help (additive, not required now): a `lang` field on the response
// frame so the right voice (VN/EN) is chosen — Ciel is already multilingual. Until
// then, detect language client-side or default to the app locale.
// ─────────────────────────────────────────────────────────────────────────────

import { bus } from "../../core/bus";

let unsubscribe: (() => void) | null = null;

export function enableSpeaker(speak: (text: string) => void): void {
  if (unsubscribe) return; // already enabled
  unsubscribe = bus.on("response", (text) => {
    try {
      speak(text);
    } catch (err) {
      console.error("[voice-out] speak failed", err);
    }
  });
}

export function disableSpeaker(): void {
  unsubscribe?.();
  unsubscribe = null;
}

// Example browser implementation, left unwired. Call
//   enableSpeaker(browserSpeak)
// from App once you want speech, or swap in a Tauri/native TTS call.
export function browserSpeak(text: string): void {
  if (typeof window === "undefined" || !("speechSynthesis" in window)) return;
  const utter = new SpeechSynthesisUtterance(text);
  window.speechSynthesis.speak(utter);
}
