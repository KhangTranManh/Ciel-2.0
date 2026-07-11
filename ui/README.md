# Ciel 2.0 — UI

React frontend for Ciel 2.0. **Browser-first, Tauri-wrappable.** The same React app
runs in a browser during development and gets wrapped by a thin Tauri shell for the
desktop build — no app-code changes between the two.

The interface is voice-first and audio-reactive: an **audio-reactive Three.js particle
orb** (in the spirit of [ethanplusai/jarvis](https://github.com/ethanplusai/jarvis)) is
the centerpiece, flanked by the dynamic skills panel (left) and the conversation (right).
Both voice directions are wired: a mic button (browser STT) and a 🔊 toggle that reads
replies aloud through the backend's edge-tts voice.

## Run (dev)

Backend first (from repo root):

```bash
python main_api.py          # serves ws://localhost:8000/ws  +  GET /skills, /health, POST /tts
```

Then the UI:

```bash
cd ui
npm install
npm run dev                 # http://localhost:1420
```

Point the UI at a non-default backend with `VITE_CIEL_WS_URL` (e.g. `ws://host:8000/ws`).

## Architecture (why it's laid out this way)

```
src/
  orb.ts         Three.js audio-reactive particle orb — framework-agnostic:
                 createOrb(canvas) -> { setState, setAnalyser, destroy }
  core/          transport + protocol — knows nothing about React or modality
    types.ts     wire protocol (additive-only; safe to extend)
    bus.ts       tiny typed event bus — the single hub everything subscribes to
    ws.ts        WebSocket transport -> emits bus events; exposes send()
    http.ts      REST base + /skills fetch
  io/            ← MODALITY LAYER (both directions now live)
    input/       TextInput.tsx · VoiceInput.tsx (mic → Web Speech API → onSubmit(text))
    output/      Transcript.tsx · speaker.ts (bus 'response' → POST /tts → play + AnalyserNode)
  components/     Orb.tsx (centerpiece) · SkillGrid · VitalsBar · ConfirmDialog
  hooks/useCiel  bus <-> React state bridge
  lib/           thoughtParser (streams thoughts.log lines -> entries)
```

Layout (`App.tsx`): **skills (left) · orb (center) · conversation (right)**. The orb's
state (idle/listening/thinking/speaking) is derived from the real conversation, and its
audio reactivity comes from an `AnalyserNode` fed by the TTS `<audio>` in `speaker.ts`.

### Two design rules this codebase keeps

1. **Add a skill, the UI reflects it — zero frontend edits.** The skill list is served
   dynamically from the backend `/skills` manifest (built by `ToolManager` auto-discovery)
   and live-activity comes from the vitals feed. `SkillGrid` renders whatever exists;
   unknown actors/tools get sensible defaults.

2. **Modalities are pluggable — and now filled.** Everything talks to the `bus`, never to
   the WebSocket directly. Input produces text → `ciel.send(text)`; output consumes bus
   `response` events. Both voice directions are implemented on that seam:
   - **Voice input** — `io/input/VoiceInput.tsx` uses the browser Web Speech API (vi-VN)
     and calls the same `onSubmit(text)` the keyboard uses. Backend unchanged. (Some Tauri
     WebView2 builds lack `SpeechRecognition`; the mic button self-disables there.)
   - **Voice output (approach B)** — `enableSpeaker(backendSpeak)` (wired to the 🔊 toggle
     in `App.tsx`). `backendSpeak` POSTs the reply to the backend `POST /tts`, which runs
     the SAME `to_speech()` normalizer + edge-tts as the CLI and returns MP3 — so the
     browser gets identical vi-VN neural quality with no client-side voice code, and the
     audio is routed through a Web Audio `AnalyserNode` so the orb pulses to the real voice.

   Still-reserved backend extension points (additive, not required): a `response_chunk`
   streaming frame (speak-as-you-go) and a `lang` field on responses (VN/EN voice pick).

## Desktop app (Tauri) — scaffolded

The Tauri v2 shell lives in `src-tauri/`. Its config (`src-tauri/tauri.conf.json`)
points `devUrl` at `http://localhost:1420` (this Vite dev server) and `frontendDist`
at `../dist`, so the React code is identical between browser and desktop.

Prereqs (one-time): Rust (https://rustup.rs) + MSVC C++ Build Tools. WebView2 ships
with Windows 11.

```bash
# Dev — launches the desktop window against the live Vite dev server + backend:
python main_api.py          # backend, from repo root
cd ui && npm run tauri dev  # builds the Rust shell (slow first time) then opens the app

# Release build — produces an installer/exe under src-tauri/target/release/:
npm run tauri build
```

`npm run tauri dev` runs `beforeDevCommand` (`npm run dev`) for you, so you don't need
a separate `npm run dev`. The backend (`main_api.py`) must be started separately.

> **Windows: run Tauri/cargo from PowerShell, not Git Bash.** Git Bash ships a
> coreutils `link` that shadows the MSVC `link.exe`, so `cargo build` from bash fails
> with `link: extra operand ... Try 'link --help'`. PowerShell resolves the MSVC
> linker correctly. (Pure `npm run dev`/`build` is unaffected — this only matters once
> Rust compiles.)
