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
//   • browserSpeak (fallback) — window.speechSynthesis. Offline, weaker Vietnamese;
//     used automatically when /tts fails so the 🔊 toggle is never silent-fail.
// ─────────────────────────────────────────────────────────────────────────────

import { bus } from "../../core/bus";
import { HTTP_BASE } from "../../core/http";

let unsubscribe: (() => void) | null = null;
let currentAudio: HTMLAudioElement | null = null;
let currentSource: MediaElementAudioSourceNode | null = null;
let speakGen = 0; // ignore stale async completions after stop/newer reply

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
      bus.emit("notice", "Voice output failed.");
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
  speakGen += 1;
  if (currentSource) {
    try {
      currentSource.disconnect();
    } catch {
      /* already disconnected */
    }
    currentSource = null;
  }
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
// route it through the analyser so the orb reacts to the voice. On failure, fall
// back to browserSpeak and surface a soft notice (edge-tts is known-flaky).
export async function backendSpeak(text: string): Promise<void> {
  if (!text || !text.trim()) return;
  stopSpeaking(); // interrupt the previous reply if it's still speaking
  const gen = speakGen;
  try {
    const res = await fetch(`${HTTP_BASE}/tts`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    if (gen !== speakGen) return; // interrupted
    if (res.status === 204) return; // nothing speakable after normalize
    if (!res.ok) {
      console.warn("[voice-out] /tts", res.status, "— falling back to browser TTS");
      bus.emit("notice", `TTS backend failed (${res.status}) — using browser voice.`);
      browserSpeak(text);
      return;
    }
    const blob = await res.blob();
    if (gen !== speakGen) return;
    const url = URL.createObjectURL(blob);
    const audio = new Audio(url);
    currentAudio = audio;

    // Route through Web Audio for the analyser (best-effort — falls back to direct
    // playback if the AudioContext or media source can't be created).
    const a = ensureAudio();
    if (a) {
      try {
        await a.ctx.resume();
        if (gen !== speakGen) {
          URL.revokeObjectURL(url);
          return;
        }
        // One MediaElementSource per element; disconnect any previous source first.
        if (currentSource) {
          try {
            currentSource.disconnect();
          } catch {
            /* ignore */
          }
          currentSource = null;
        }
        const src = a.ctx.createMediaElementSource(audio);
        src.connect(a.analyser);
        currentSource = src;
      } catch {
        /* not routable — audio still plays directly to the speakers below */
      }
    }

    audio.onended = () => {
      URL.revokeObjectURL(url);
      if (currentSource) {
        try {
          currentSource.disconnect();
        } catch {
          /* ignore */
        }
        currentSource = null;
      }
      if (currentAudio === audio) currentAudio = null;
      if (gen === speakGen) emitSpeaking(false);
    };
    audio.onerror = () => {
      URL.revokeObjectURL(url);
      if (currentAudio === audio) currentAudio = null;
      if (gen === speakGen) {
        emitSpeaking(false);
        bus.emit("notice", "Audio playback failed — using browser voice.");
        browserSpeak(text);
      }
    };
    emitSpeaking(true);
    await audio.play();
  } catch (err) {
    if (gen !== speakGen) return;
    emitSpeaking(false);
    console.error("[voice-out] backendSpeak failed", err);
    bus.emit("notice", "TTS unreachable — using browser voice.");
    browserSpeak(text);
  }
}

// Fallback browser implementation (OS voices, offline). Used when /tts fails.
// Does not strip markdown (backend does); good enough as a last resort.
export function browserSpeak(text: string): void {
  if (typeof window === "undefined" || !("speechSynthesis" in window)) return;
  window.speechSynthesis.cancel();
  const utter = new SpeechSynthesisUtterance(text);
  utter.lang = "vi-VN";
  utter.onstart = () => emitSpeaking(true);
  utter.onend = () => emitSpeaking(false);
  utter.onerror = () => emitSpeaking(false);
  window.speechSynthesis.speak(utter);
}
