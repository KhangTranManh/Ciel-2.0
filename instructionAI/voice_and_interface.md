# Voice & Interface — Ciel 2.0

How Ciel listens, talks back, and presents itself — on the CLI and in the browser/
desktop UI.

## Design Principle (read this before touching voice)

**Voice is a modality layer, not a rewrite of the persona.** A spoken sentence becomes
text and flows through the exact same pipeline the keyboard uses
(`AgentLoop.run_step()` / `ciel.send()`). A reply is spoken only AFTER a deterministic
normalizer cleans it — the prompts, persona, and text/HUD/email formatting are never
dumbed down to be "speech-friendly." If TTS output sounds wrong, fix the normalizer
(`to_speech()`), not the Brain/Worker prompts.

## Speech-to-Text (`core/voice_input.py`)

Mic capture via `sounddevice` (bundles PortAudio; installs clean on Windows). Energy-
based endpointing auto-calibrates ambient noise and stops after ~1.3s of silence.

| `STT_BACKEND` | Engine | Notes |
|---|---|---|
| `whisper` *(default)* | `faster-whisper`, local CPU (int8) | Offline, no key. `WHISPER_MODEL=small` benchmarks ~1.1–1.4s/utterance with good Vietnamese accuracy — chosen specifically to avoid depending on a free/unofficial cloud endpoint. `warmup()` pre-loads the model at startup. |
| `google` | SpeechRecognition → free Google Web Speech | No key, vi-VN, needs internet. Kept as a fallback; historically the default, moved off because free cloud STT is a reliability risk. |
| `gemini` | google-genai | Needs `GEMINI_API_KEY`. |

`main.py --voice`: Enter on an empty line = speak; `:v`/`:voice` = one-off capture in
any mode.

## Text-to-Speech (`core/speech_output.py`)

`to_speech()` strips markdown, emojis, `[TAG]`-style headers, code blocks, and URLs
(numbers preserved) before the text reaches the TTS engine.

| `TTS_BACKEND` | Engine | Notes |
|---|---|---|
| `edge` *(default)* | edge-tts (Microsoft neural voices) | Free, no key, vi-VN neural. **Reliability caveat**: an UNOFFICIAL free endpoint that intermittently raises `NoAudioReceived` (~1/3 of calls, independent of text content). `_edge_synth_bytes()` retries with backoff — that retry is the fix for sporadic failures, not the text/voice. |
| `pyttsx3` | Offline OS/SAPI voices | No internet; weak/no Vietnamese voices typically. |
| `space` / `rvc` | mikuTTS HF Space (RVC character voice) | Experimental. ~25s/utterance, the Space may sleep. Not a default. |

Prosody: `TTS_VOICE`, `TTS_RATE`, `TTS_PITCH`, `TTS_VOLUME`. RVC-only (only when
`TTS_BACKEND=space`): `RVC_MODEL`, `RVC_TTS_VOICE`, `RVC_F0_UP`, `RVC_F0_METHOD`,
`RVC_INDEX_RATE`, `RVC_PROTECT`, `RVC_SPACE`.

**GPU note:** none of the default backends use the local GPU — edge-tts and the RVC
Space run remotely; `pyttsx3` and `faster-whisper` (small, int8) are light CPU workloads.

## Voice in the Browser/Desktop UI

- **Output** (reuse the CLI engine, don't reinvent it in the browser): the UI's 🔊
  toggle POSTs the reply text to `POST /tts`, which runs the SAME `to_speech()` +
  edge-tts as the CLI and returns MP3 bytes — identical quality, zero client-side
  normalization code. The `<audio>` element routes through a Web Audio `AnalyserNode`
  (currently only consumed for a "speaking" state indicator — see below).
- **Input**: `ui/src/io/input/VoiceInput.tsx` uses the browser's native Web Speech API
  (`SpeechRecognition`, vi-VN) — a SEPARATE engine from the CLI's server-side
  `voice_input.py`, intentionally (instant, no backend round-trip). Some Tauri WebView2
  builds lack `SpeechRecognition`; the mic button self-disables rather than erroring.

## The Current UI Layout

`App.tsx` renders a two-region workbench — **not** the three-column orb layout earlier
versions had:

```
┌─────────────┬───────────────────────────────┐
│ Skills rail │  Session header               │
│ (collapsible,│  Status strip                 │
│  backend-    │  Transcript (chat history)    │
│  driven via  │  Input dock (text · voice ·   │
│  GET /skills)│    stop-button when busy ·    │
│             │    🔊 read-aloud toggle)       │
└─────────────┴───────────────────────────────┘
```

`VitalsBar` (cost/tokens) sits above both, and `ConnectionBanner` shows reconnect
state. `ConfirmDialog` overlays modally when a safety-gate decision is pending.

**The raw thought stream is gone from the UI entirely** — `ThoughtStream.tsx` and
`thoughtParser.ts` were removed. The backend still tails `thoughts.log` and emits
`{"type": "thought", ...}` frames over the WebSocket (harmless — nothing subscribes to
them client-side), but nothing renders them. Do not resurrect a raw-log view without a
real reason; `thoughts.log` is an audit trail, not a UI primitive.

### The orb is currently unmounted

`ui/src/orb.ts` (a framework-agnostic Three.js particle system) and
`ui/src/components/Orb.tsx` (its React wrapper) still exist in the codebase and still
compile, but **`App.tsx` no longer imports or renders `Orb`**. The current UI favors a
leaner skills+chat workbench. If you are asked to bring the orb back: `createOrb(canvas)
→ { setState, setAnalyser, destroy }` is unchanged and ready to remount — it knows
nothing about React, Ciel, or the bus, so mounting it again is a component change, not
a rewrite. It was originally kept alive as a component (not deleted) specifically so
this stays cheap. If you touch it: never propose replacing the React shell to get the
orb look — the app's `SkillGrid`/`VitalsBar`/`ConfirmDialog`/cancel button have no
equivalent in a from-scratch orb-only UI.

## Modality Seam (why voice was cheap to add, and why removing the orb was cheap too)

Everything talks to `ui/src/core/bus.ts`, never directly to the WebSocket. Input
produces text and calls `ciel.send(text)` — voice input just calls the same function a
keystroke would. Output consumes bus `response` events — voice output just subscribes
an extra handler, and the orb (when mounted) just subscribes to state changes the same
way. This is why voice I/O could be added, and the orb could be unmounted, without
touching the WebSocket protocol, the Router, or any tool.

---

## The WebSocket Protocol

`WS /ws` — one socket, JSON both ways. Plain text sent to the socket is treated as user
input.

| Direction | `type` | Payload / meaning |
|---|---|---|
| server → client | `thought` | one entry tailed live from `thoughts.log` (unconsumed by the current UI — see above) |
| server → client | `vitals` | per-tier call counts, exact token totals, estimated USD |
| server → client | `response` | the final reply for a turn |
| server → client | `status` | transient state |
| server → client | `error` | failure surfaced to the user |
| server → client | `confirm_request` | safety gate needs a decision — see below |
| client → server | `confirm_response` | `{"approved": true\|false}` |
| client → server | `cancel` | Tier-5 interrupt request — **wired**, see below |
| client → server | *(raw text)* | a user message |

**`confirm_request` carries two distinct cases**, and a UI must render them
differently: `tool_name` is a real tool → a single risky action (the old per-call
prompt). `tool_name == "plan"` → Tier-3 plan-level approval, one question covering
every step that needs consent, asked *before anything runs* — answering "no" means
**nothing ran**, the opposite of the per-call case where earlier steps already
happened.

The CLI also offers a third answer, `A` = *approve this tool for the rest of the
session* (`permissions.grant_for_session`). The UI has no equivalent yet — worth
adding, since a gate that's too noisy gets switched off entirely.

## Cancellation — wired end to end (Tier 5)

Both sides are done: `ws.ts::cancel()` sends `{"type": "cancel"}`;
`main_api.py`'s WebSocket loop calls `ciel_agent.core.request_cancel("cancelled from
UI")` on receipt. `useCiel.ts` exposes a `cancel()` that the UI's Stop button and Esc
key call while a request is in flight.

Cancellation is **cooperative and lands at the next step boundary**, never mid-tool —
the UI reflects this with a `status === "cancelling"` state ("Stopping at the next step
boundary…") rather than implying an instant stop. The job closes as `cancelled`, which
`status` queries report distinctly from a crash.

## What Still Needs Wiring

Two backend capabilities are finished and simply not surfaced in the current UI yet:

| Gap | Backend is ready | What the UI needs |
|---|---|---|
| **Proactive notifications** | `core/notifier.py` routes app → CLI → Telegram; `AppChannel` is first in priority | `main_api.py` never constructs an `AppChannel`, so a Tier-6 notification falls through to Telegram even with the UI open. Needs: `AppChannel(is_live_fn=lambda: _confirm_ws is not None, send_fn=lambda note: _push({"type": "notification", ...}))`, plus a new `notification` frame type the UI renders — distinctly for `notify` (one-shot) vs `ask` (expects an answer, escalates to Telegram if ignored). |
| **Deferred approvals** | `DeferredStore` records background actions blocked because nobody was present | surface `core.deferred.pending()` as a small queue. Offer **"re-issue this request"**, never "approve and run it now" — Ciel deliberately does not replay a stale mutating action against a world that has moved on. |

New read-only surfaces worth building panels for, once the above lands:

| Source | What it is | Why a UI wants it |
|---|---|---|
| `core.tasks.recent()` | Tier-2 durable task records — goal, steps, status | a real activity/history panel; `interrupted` jobs are resumable |
| `core.user_model.live_traits()` | Tier-7 learned profile, with provenance | let the Master **see and delete** what Ciel believes about them |
| `core.user_model.explain(key)` | why a trait is believed, and how confident | "why do you think that?" answered honestly |
| `notifier.peek_digest()` | findings held back by the daily budget | a quiet "things I didn't interrupt you for" list |

The user-model panel is worth building carefully: it describes a real person, stored
in a human-readable file precisely so it can be audited and corrected.
`forget(key)`/`forget_all()` already exist — a profile you cannot erase is not one
anybody consented to.

**Known-open, not a UI concern:** `main_api.py` still auto-approves a confirmation
request when no WebSocket is attached. That predates the Tier-6 unattended ceiling and
is the one remaining place where silence still means consent — see `safety_and_risk.md`.

## Things That Will Bite a Rewrite

- **Never render `thoughts.log` as the primary output.** It is a raw audit trail
  parsed by three scripts — do not reformat it to suit a UI.
- **`GET /skills` is the only source of the tool list.** The skill grid is 100%
  backend-driven so a new `skills/**/*_ops.py` appears with zero frontend edits. Keep
  that property.
- **Don't reimplement TTS in the browser.** `POST /tts` runs the same normalizer +
  engine as the CLI; a client-side voice would drift from it immediately.
- **The orb is framework-agnostic on purpose** (`ui/src/orb.ts`, no React import), and
  currently unmounted rather than deleted — see above. If you change frameworks or
  decide to bring it back, it comes with you unchanged.
- **Keep the bus seam.** Every capability in the "what still needs wiring" table above
  is a new *event type* on the existing bus, not a new transport.
