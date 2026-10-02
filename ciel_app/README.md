# Ciel app (Flutter)

One client for Android, iOS, Windows, macOS and web. It talks to a Ciel API
server (`main_api.py`) anywhere — your PC, your LAN, or the VPS — chosen in
**Settings**, not baked into the build.

## Run

```bash
cd ciel_app
flutter pub get
flutter run -d chrome          # web
flutter run -d windows         # needs Visual Studio "Desktop development with C++"
flutter run -d <android-id>    # needs the Android SDK
```

Optional build-time default server (users can still change it in Settings):

```bash
flutter run --dart-define=CIEL_SERVER_URL=https://ciel.example.com
```

## Connect to a server

1. Start the backend with an access token:

   ```dotenv
   # .env on the server
   CIEL_API_TOKEN=<long random string>
   ```

   ```bash
   python main_api.py            # or docker compose -f docker/docker-compose.api.yml up -d
   ```

2. In the app open **Settings** → enter the server address and token →
   **Test connection** → **Save**.

Accepted addresses: `192.168.1.10:8000`, `http://host:8000`,
`https://ciel.example.com`, `https://example.com/ciel` (reverse-proxy prefix), or a
pasted `wss://…/ws`. The app derives the REST and WebSocket URLs itself.

Use `https://` for anything outside your home network — the token and your
messages travel over this connection. Plain `http://` is allowed only so a LAN
server works during development.

## What it does

| Feature | Backend contract |
|---|---|
| Chat with Markdown (tables included), history kept on the device | `WS /ws` → `{"message": …}`; `response` / `error` / `status` frames |
| Safety approval with 60 s countdown → auto-deny | `confirm_request` → `{"type":"confirm_response","approved":…}` |
| Stop (button or Esc) at the next step boundary | `{"type":"cancel"}` |
| Session usage: calls, tokens, cost | `vitals` frames |
| Skills list with recently-used highlight | `GET /skills` + `vitals.skills` |
| Voice input | on-device `speech_to_text` |
| Read replies aloud | `POST /tts` (server voice, same as the CLI) |
| Auto-reconnect every 2 s and on app resume; "token rejected" stops retrying | WebSocket close code `4401` |

The token is sent as `Authorization: Bearer …` on REST calls and as `?token=` on
the WebSocket (browsers cannot set WebSocket headers). It is stored in the OS
keychain/keystore via `flutter_secure_storage`.

## Layout

```text
lib/
  core/      protocol.dart (wire format) · endpoints.dart · ciel_socket.dart · api_client.dart · settings_store.dart
  state/     ciel_controller.dart (all app state) · chat_turn.dart (local history)
  features/  chat · confirm · skills · vitals · settings · voice
  theme/     monochrome tokens from the old ui/styles.md
```

Screens only talk to `CielController`; only `CielSocket` talks to the server.

## Tests

```bash
flutter analyze
flutter test                                   # offline: fake socket + fake HTTP

# Against a real running backend:
flutter test test_live/live_backend_test.dart \
  --dart-define=SERVER=http://127.0.0.1:8000 --dart-define=TOKEN=<CIEL_API_TOKEN>
```

## Not yet

- Proactive messages (weekly plan, reminders) still go to Telegram; the API does not
  push them over the WebSocket yet.
- Desktop floating widget from the old Tauri UI.
- Push notifications.
- The API and the Telegram bot cannot run at the same time against the same
  `ciel_data/` (one-writer rule).
