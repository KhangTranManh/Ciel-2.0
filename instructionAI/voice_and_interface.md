# Voice & Interface — Ciel 2.0

How Ciel listens, talks back, and presents itself — on the CLI and in the browser/desktop UI.

## Design Principle (read this before touching voice)

**Voice is a modality layer, not a rewrite of the persona.** A spoken sentence becomes text and
flows through the exact same pipeline the keyboard uses (`AgentLoop.run_step()` / `ciel.send()`).
A reply is spoken only AFTER a deterministic normalizer cleans it — the prompts, persona, and
text/HUD/email formatting are never dumbed down to be "speech-friendly." If TTS output sounds
wrong, fix the normalizer (`to_speech()`), not the Brain/Worker prompts.

## Speech-to-Text (`core/voice_input.py`)

Mic capture via `sounddevice` (bundles PortAudio; installs clean on Windows — no PyAudio/compiler
needed). Energy-based endpointing auto-calibrates ambient noise and stops after ~1.3s of silence.

| `STT_BACKEND` | Engine | Notes |
|---|---|---|
| `whisper` *(default)* | `faster-whisper`, local CPU (int8) | Offline, no key. `WHISPER_MODEL=small` benchmarked at ~1.1–1.4s/utterance with good Vietnamese accuracy — chosen specifically to avoid depending on a free/unofficial cloud endpoint. `warmup()` pre-loads the model at startup so the first spoken command isn't slow. |
| `google` | SpeechRecognition → free Google Web Speech | No key, vi-VN, needs internet. Kept as a fallback; historically the default, moved off because free/unofficial cloud STT is a reliability risk. |
| `gemini` | google-genai | Needs `GEMINI_API_KEY`. |

`main.py --voice`: Enter on an empty line = speak; `:v`/`:voice` = one-off capture in any mode.

## Text-to-Speech (`core/speech_output.py`)

`to_speech()` strips markdown, emojis, `[TAG]`-style headers (e.g. `[COGNITION]`), code blocks,
and URLs — numbers are preserved — before the text reaches the TTS engine.

| `TTS_BACKEND` | Engine | Notes |
|---|---|---|
| `edge` *(default)* | edge-tts (Microsoft neural voices) | Free, no key, vi-VN neural (`vi-VN-HoaiMyNeural`/`vi-VN-NamMinhNeural` — the only 2 VN voices available). **Reliability caveat:** this is an UNOFFICIAL free endpoint that intermittently raises `NoAudioReceived` (verified: ~1/3 of calls, independent of text content — throttling, not bad input). `_edge_synth_bytes()` retries with backoff; if you see sporadic TTS failures, that retry is the fix, not the text/voice. |
| `pyttsx3` | Offline OS/SAPI voices | No internet; weak/no Vietnamese voices typically. |
| `space` / `rvc` | mikuTTS Hugging Face Space (RVC character voice) | Experimental. ~25s/utterance, the Space may sleep. Not a default — for trying a character voice only. |

Prosody: `TTS_VOICE`, `TTS_RATE`, `TTS_PITCH`, `TTS_VOLUME`. RVC-only knobs (only apply when
`TTS_BACKEND=space`): `RVC_MODEL`, `RVC_TTS_VOICE`, `RVC_F0_UP`, `RVC_F0_METHOD`, `RVC_INDEX_RATE`,
`RVC_PROTECT`, `RVC_SPACE`.

**GPU note:** none of the default backends use the local GPU — edge-tts and the RVC Space run on
remote servers; `pyttsx3` and `faster-whisper` (small, int8) are CPU-only and light enough without one.

## Voice in the Browser/Desktop UI

- **Output (approach B — reuse the CLI engine, don't reinvent it in the browser)**: the UI's 🔊
  toggle POSTs the reply text to the backend `POST /tts`, which runs the SAME `to_speech()` +
  edge-tts as the CLI (`core/speech_output.py:synth_to_bytes()`) and returns MP3 bytes. This gives
  the browser identical voice quality with zero client-side normalization code. The `<audio>`
  element is routed through a Web Audio `AnalyserNode` so the UI's orb can react to the real voice.
- **Input**: `ui/src/io/input/VoiceInput.tsx` uses the browser's native Web Speech API
  (`SpeechRecognition`, vi-VN) — a SEPARATE engine from the CLI's server-side `voice_input.py`
  (this is intentional: instant, no backend round-trip, and the right tool for a browser context).
  Some Tauri WebView2 builds lack `SpeechRecognition`; the mic button self-disables rather than
  erroring.

## The Orb (UI centerpiece)

`ui/src/orb.ts` is a **framework-agnostic** Three.js particle system:
`createOrb(canvas) → { setState, setAnalyser, destroy }`. It knows nothing about React, Ciel, or
the bus — `ui/src/components/Orb.tsx` wraps it in React, and `App.tsx` feeds it real state.

- ~2000 particles on a Fibonacci sphere, nearest-neighbor connecting lines (density scales per
  state), bright "electrons" traveling the lines during `thinking`.
- Four states — `idle` / `listening` / `thinking` / `speaking` — interpolated smoothly (radius,
  speed, brightness, line density, color all ease toward the active state's targets).
- **Audio-reactive to the REAL voice**, not a canned animation: `speaker.ts` exposes the TTS
  `AnalyserNode`; bass frequencies push particles outward and boost brightness while Ciel talks.
- Orb state priority in `App.tsx`: `speaking` (TTS audio playing) > `thinking` (awaiting a reply,
  i.e. `status` is set) > `listening` (mic active) > `idle`.
- Inspired by `ethanplusai/jarvis`, but deliberately NOT built as a from-scratch vanilla-JS app —
  it was ported INTO the existing React/Tauri app as a component specifically to keep `SkillGrid`
  (dynamic tool panel), `VitalsBar` (live cost/tokens), and `ConfirmDialog` (safety-gate Y/N),
  none of which the standalone JARVIS UI has. Never propose replacing the React shell to get the
  orb look — extend the orb, don't discard the app around it.

## UI Layout

Three columns in `App.tsx`: **skills (left, backend-driven via `GET /skills`) · orb (center) ·
conversation (right, Transcript + input dock with text box, 🎤, 🔊)**. `ThoughtStream` (the raw
cognition feed) was intentionally removed from the layout in favor of the orb + chat; `VitalsBar`
(cost/tokens) and `ConfirmDialog` (safety-gate) are unchanged and still backend-driven.

## Modality Seam (why voice was cheap to add)

Everything talks to `ui/src/core/bus.ts`, never directly to the WebSocket. Input produces text and
calls `ciel.send(text)` — voice input just calls the same function a keystroke would. Output
consumes bus `response` events — voice output just subscribes an extra handler. This is why voice
I/O could be added without touching the WebSocket protocol, the Router, or any tool.
