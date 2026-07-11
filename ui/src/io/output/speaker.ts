// ─────────────────────────────────────────────────────────────────────────────
// VOICE OUTPUT — the "leave room for voice" seam on the OUTPUT side.
//
// Ciel's replies arrive on the bus "response" channel. enableSpeaker(fn) subscribes
// a speak function to it — the transcript UI already listens to the same event, so
// text + speech stay in sync and NO other component changes.
//
// TWO speak implementations are provided:
//   • backendSpeak (approach B, DEFAULT) — POST the raw reply to the backend `/tts`,
//     which runs the SAME to_speech() normalizer + edge-tts neural voice as the CLI,
//     then plays the returned MP3. Best quality, identical to CLI, no markdown/emoji
//     read aloud (the backend strips it). Requires the backend to be reachable.
//   • browserSpeak (fallback) — window.speechSynthesis. Offline, but uses the OS
//     voices (weaker Vietnamese) and would read markdown/tags unless normalized, so
//     it's a fallback only.
// ─────────────────────────────────────────────────────────────────────────────

import { bus } from "../../core/bus";
import { HTTP_BASE } from "../../core/http";

let unsubscribe: (() => void) | null = null;
let currentAudio: HTMLAudioElement | null = null;

// ── Audio analysis (feeds the orb's audio reactivity) ───────────────────────
// The TTS <audio> is routed through Web Audio: element → AnalyserNode → speakers.
// The orb reads the analyser so it pulses to Ciel's ACTUAL spoken voice. Speaking
// start/end is broadcast so the UI can flip the orb into/out of the "speaking" state.
let audioCtx: AudioContext | null = null;
let analyser: AnalyserNode | null = null;
const speakingSubs = new Set<(speaking: boolean) => void>();

function ensureAudio(): { ctx: AudioContext; analyser: AnalyserNode } | null {
  try {
    if (!audioCtx || !analyser) {
      const AC = window.AudioContext || (window as any).webkitAudioContext;
      if (!AC) return null;
      audioCtx = new AC();
      analyser = audioCtx.createAnalyser();
      analyser.fftSize = 256;
      analyser.smoothingTimeConstant = 0.8;
      analyser.connect(audioCtx.destination);
    }
    return { ctx: audioCtx, analyser };
  } catch {
    return null;
  }
}

export function getSpeechAnalyser(): AnalyserNode | null {
  return analyser;
}

export function onSpeakingChange(cb: (speaking: boolean) => void): () => void {
  speakingSubs.add(cb);
  return () => speakingSubs.delete(cb);
}

function emitSpeaking(v: boolean): void {
  speakingSubs.forEach((cb) => {
    try {
      cb(v);
    } catch (err) {
      console.error("[voice-out] speaking subscriber threw", err);
    }
  });
}

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
  stopSpeaking();
}

// Stop any in-flight playback (browser or backend) so replies never overlap.
export function stopSpeaking(): void {
  if (currentAudio) {
    currentAudio.pause();
    currentAudio.src = "";
    currentAudio = null;
  }
  if (typeof window !== "undefined" && "speechSynthesis" in window) {
    window.speechSynthesis.cancel();
  }
  emitSpeaking(false);
}

// Approach B (default): backend edge-tts + to_speech(), play the returned MP3 and
// route it through the analyser so the orb reacts to the voice.
export async function backendSpeak(text: string): Promise<void> {
  if (!text || !text.trim()) return;
  stopSpeaking(); // interrupt the previous reply if it's still speaking
  try {
    const res = await fetch(`${HTTP_BASE}/tts`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    if (!res.ok || res.status === 204) return; // 204 = nothing speakable after normalize
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const audio = new Audio(url);
    currentAudio = audio;

    // Route through Web Audio for the analyser (best-effort — falls back to direct
    // playback if the AudioContext or media source can't be created).
    const a = ensureAudio();
    if (a) {
      try {
        await a.ctx.resume();
        const src = a.ctx.createMediaElementSource(audio);
        src.connect(a.analyser);
      } catch {
        /* not routable — audio still plays directly to the speakers below */
      }
    }

    audio.onended = () => {
      URL.revokeObjectURL(url);
      if (currentAudio === audio) currentAudio = null;
      emitSpeaking(false);
    };
    emitSpeaking(true);
    await audio.play();
  } catch (err) {
    emitSpeaking(false);
    console.error("[voice-out] backendSpeak failed", err);
  }
}

// Fallback browser implementation (OS voices, offline). Left available but not the
// default — backendSpeak matches the CLI's quality and normalization.
export function browserSpeak(text: string): void {
  if (typeof window === "undefined" || !("speechSynthesis" in window)) return;
  const utter = new SpeechSynthesisUtterance(text);
  window.speechSynthesis.speak(utter);
}
