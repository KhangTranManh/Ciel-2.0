# Ciel 2.0 — UI

React frontend for Ciel 2.0. **Browser-first, Tauri-wrappable.** The same React app
runs in a browser during development and gets wrapped by a thin Tauri shell for the
desktop build — no app-code changes between the two.

## Run (dev)

Backend first (from repo root):

```bash
python main_api.py          # serves ws://localhost:8000/ws  +  GET /skills, /health
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
  core/          transport + protocol — knows nothing about React or modality
    types.ts     wire protocol (additive-only; safe to extend)
    bus.ts       tiny typed event bus — the single hub everything subscribes to
    ws.ts        WebSocket transport -> emits bus events; exposes send()
    http.ts      REST base + /skills fetch
  io/            ← MODALITY LAYER (the "room for voice")
    input/       TextInput.tsx (live) · VoiceInput.tsx (stub: STT -> onSubmit(text))
    output/      Transcript.tsx (live) · speaker.ts (stub: bus 'response' -> TTS)
  components/     SkillGrid · ThoughtStream · VitalsBar · ConfirmDialog
  hooks/useCiel  bus <-> React state bridge
  lib/           thoughtParser (streams thoughts.log lines -> entries)
```

### Two design rules this codebase keeps

1. **Add a skill, the UI reflects it — zero frontend edits.** The skill list is served
   dynamically from the backend `/skills` manifest (built by `ToolManager` auto-discovery)
   and live-activity comes from the vitals feed. `SkillGrid` and `ThoughtStream` render
   whatever exists; unknown actors/tools get sensible defaults.

2. **Modalities are pluggable.** Everything talks to the `bus`, never to the WebSocket
   directly. Input produces text → `ciel.send(text)`. Output consumes bus `response`
   events. So:
   - **Voice input** = implement `io/input/VoiceInput.tsx` (STT) and call the same
     `onSubmit(text)` the keyboard uses. Backend unchanged.
   - **Voice output** = `enableSpeaker(browserSpeak)` in `main.tsx` (one line). The
     transcript keeps working untouched.

   Backend extension points already reserved for a better voice experience (additive,
   not required now): a `response_chunk` streaming frame (speak-as-you-go) and a `lang`
   field on responses (pick VN/EN TTS voice).

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
