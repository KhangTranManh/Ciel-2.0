# Voice and Interfaces — Ciel 2.0

## Interface principle

Voice, CLI, API/UI, and Telegram are transport layers over the same `AgentLoop` and
`CielCore`. They do not define different personas, routing rules, tools, permissions, or
memory semantics.

Every interactive front end provides:

- user input as text;
- final responses from the shared runtime;
- a confirmation callback for risky work;
- cooperative cancellation;
- its own presence or allow-list boundary.

## Speech to text

`core/voice_input.py` captures microphone audio, calibrates ambient energy, and stops on
silence.

| `STT_BACKEND` | Engine | Characteristics |
|---|---|---|
| `whisper` | `faster-whisper`, local int8 | Default; offline after model download |
| `google` | Google Web Speech through SpeechRecognition | Network fallback, no project key |
| `gemini` | Gemini audio transcription | Requires Gemini credentials |

CLI controls:

```bash
python main.py --voice
python main.py --voice --speak
```

In voice mode, an empty Enter starts capture; `:v` or `:voice` performs one capture in
any mode. Captured text enters the same request path as typed text.

## Text to speech

`core/speech_output.py::to_speech()` deterministically removes code blocks, Markdown,
URLs, emoji, and audit-like tags before synthesis. The original text response remains
unchanged.

| `TTS_BACKEND` | Engine | Characteristics |
|---|---|---|
| `edge` | Microsoft Edge neural speech | Default network voice; retry protected |
| `pyttsx3` | Operating-system speech | Offline; voice quality depends on installed voices |
| `space` / `rvc` | Remote RVC Space | Experimental and slow |

Voice tuning uses `TTS_VOICE`, `TTS_RATE`, `TTS_PITCH`, and `TTS_VOLUME`. RVC-specific
options apply only to the experimental backend.

Voice formatting problems are fixed in `to_speech()`, not by simplifying the Brain,
Worker, or persona prompts.

## CLI

`main.py` provides:

- typed and optional spoken input;
- optional spoken output;
- blocking safety confirmation;
- session-scoped tool grants;
- Ctrl+C cancellation during a request;
- live presence tracking for CLI notifications.

An explicit unsafe/auto-approve CLI mode wires a callback that returns true. This is an
operator choice, not the default safety behavior.

## API

`main_api.py` provides:

- `GET /health`
- `GET /skills`
- `POST /tts`
- `WS /ws`

Ciel does not serve a graphical interface. With `CIEL_API_TOKEN` set, every route
except `GET /health` requires authentication.

Turns from every front end in the process (remote client, Telegram) run one at a
time through `AgentLoop.run_step()`. Each WebSocket connection has its own
`api.confirm.WebSocketConfirmer`: a `confirm_request` goes only to the connection whose
message started the turn, and an answer from another device is ignored. It denies on
timeout (60 s), on disconnect (immediately), when the request cannot be delivered, and
for any answer other than JSON `true`. Work that no client started uses the core's
default callback, which records the action as deferred and denies it. The callback runs
on an executor thread and posts to the server event loop captured at startup, because
`asyncio.get_event_loop()` raises on that thread (an earlier handler approved the action
on that error). A message sent before Ciel finished starting gets an `error` frame.

`CIEL_API_TOKEN`, when set, is required on every route except `/health`
(`Authorization: Bearer …`, or `?token=` on the WebSocket because browsers cannot set
WebSocket headers). An invalid WebSocket token receives an `error` frame `unauthorized`
and close code `4401`; clients stop reconnecting on that code. Unset keeps the
localhost-only open behaviour and prints a startup warning. `/health` reports
`auth_required`.

`thought` frames (raw `thoughts.log` lines with full prompts and email bodies) are sent
only when `CIEL_API_STREAM_THOUGHTS=true`. The vitals loop reads only the log tail and
runs `nvidia-smi` off the event loop; reading the whole multi-MB log every 2 s per
client stalled every frame, including confirm requests.

### WebSocket frames

| Direction | Type | Purpose |
|---|---|---|
| Server → client | `status` | Processing and cancellation state |
| Server → client | `response` | Final user-facing response |
| Server → client | `error` | Surfaced runtime failure |
| Server → client | `vitals` | Model calls, token totals, estimated cost |
| Server → client | `thought` | Audit event; opt-in, no shipped client renders it |
| Server → client | `confirm_request` | Tool or whole-plan approval request |
| Client → server | `confirm_response` | Boolean confirmation decision |
| Client → server | `cancel` | Request cancellation at next step boundary |
| Client → server | raw text | User message |

`tool_name == "plan"` means approval covers all displayed risky steps before anything
runs. A normal tool name means one per-call decision.

## Remote client requirements

No graphical client is currently shipped. A future client connects to `WS /ws`, obtains
its server address and `CIEL_API_TOKEN` through runtime configuration, and implements
the documented frames. It must show `confirm_request` to the user and send
`confirm_response` only for an explicit decision. Lost connections, timeouts, malformed
frames, and unavailable confirmation UI must deny the request.

## Telegram

`main_telegram.py` and `core/telegram_interface.py` provide:

- long polling without an HTTP server;
- numeric chat-ID allow-list before routing;
- text, photo, and document input;
- sandboxed downloads under `ciel_workspace/telegram_uploads/`;
- inline confirmation for risky calls;
- `/cancel` cooperative cancellation;
- plain-text and document delivery.

Inbound files become normal user turns containing their safe local path plus caption.
Brain chooses an appropriate read/describe tool; transport does not force a tool.

Generated HTML/PDF reports go to `agent_output/` and use
`send_telegram_document`. The interface does not duplicate the report as a second long
chat response after a successful attachment.

Telegram Compose intentionally disables the API image health check. Health is container
state plus the `[Telegram] Bot online` startup log.

## Proactive interface routing

`core/proactive_setup.py` currently constructs:

- CLI presence channel plus Telegram fallback for CLI mode;
- Telegram channel for API and Telegram modes.

The backend `AppChannel` abstraction exists, but `main_api.py` does not yet publish
proactive notification frames to the WebSocket. Consequently an API/UI deployment
routes proactive messages to Telegram even while the UI is open.

Deferred approvals are stored but do not yet have a dedicated UI queue. A future panel
must offer “reissue this request,” not “approve and replay,” because stale mutating work
is deliberately non-replayable.

## Cancellation contract

Cancellation is cooperative:

1. the interface calls `CielCore.request_cancel()`;
2. the runtime sets a thread-safe event;
3. the next step boundary stops execution;
4. the durable task closes as cancelled.

Interfaces display “stopping at the next step boundary” rather than claiming immediate
termination. Killing a tool mid-write or mid-send is not supported.

## Rewrite constraints

- Tool lists remain backend-driven through `GET /skills`.
- Every new front end wires confirmation before it can invoke tools.
- One controller/bus remains the UI modality seam; only the transport touches `/ws`.
- TTS normalization remains server-side and shared.
- Raw audit logs remain outside the user-facing transcript.
- Active Subject, recent turns, and Telegram file metadata remain core contracts; an
  interface does not implement its own conversational memory. Any local
  chat history is display-only and is never sent back as context.
- An API reachable from another device requires `CIEL_API_TOKEN` and, beyond the
  home network, HTTPS.
