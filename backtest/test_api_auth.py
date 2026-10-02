"""API access token for remote clients (Flutter app on phone/desktop).

The API can run shell commands and send email, so once it is reachable from other
devices every route except /health must require CIEL_API_TOKEN. Uses FastAPI's
TestClient without entering its context, so Ciel's startup (models, tools) never
runs — only the auth boundary is under test.

    python -m backtest.test_api_auth
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402
from starlette.websockets import WebSocketDisconnect  # noqa: E402

import main_api  # noqa: E402

_passed, _failed = 0, []


def check(name, cond, detail=""):
    global _passed
    if cond:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed.append(name)
        print(f"  FAIL  {name}" + (f"  -- {detail}" if detail else ""))


def _ws_rejected(client, path):
    try:
        with client.websocket_connect(path) as ws:
            first = ws.receive_json()
            try:
                ws.receive_json()
            except WebSocketDisconnect as closed:
                return first, closed.code
            return first, None
    except WebSocketDisconnect as closed:
        return None, closed.code


def test_token_required():
    print("\n[1] With CIEL_API_TOKEN set, only /health is open")
    main_api.API_TOKEN = "unit-test-token"
    client = TestClient(main_api.app)
    auth = {"Authorization": "Bearer unit-test-token"}

    health = client.get("/health")
    check("/health stays open for connection tests",
          health.status_code == 200 and health.json().get("auth_required") is True, health.text)
    check("/skills without a token is 401", client.get("/skills").status_code == 401)
    check("/skills with a wrong token is 401",
          client.get("/skills", headers={"Authorization": "Bearer nope"}).status_code == 401)
    check("/skills with the token is allowed", client.get("/skills", headers=auth).status_code == 200)
    check("/tts without a token is 401", client.post("/tts", json={"text": "hi"}).status_code == 401)
    preflight = client.options("/skills", headers={"Origin": "http://localhost:5000",
                                                   "Access-Control-Request-Method": "GET"})
    check("CORS preflight is not blocked", preflight.status_code in (200, 204), str(preflight.status_code))
    denied = client.get("/skills", headers={"Origin": "http://localhost:5000"})
    check("a 401 still carries CORS headers (browser sees 'unauthorized', not a CORS error)",
          denied.status_code == 401 and "access-control-allow-origin" in denied.headers)

    first, code = _ws_rejected(client, "/ws")
    check("WebSocket without a token is closed with 4401",
          code == main_api.WS_UNAUTHORIZED and (first or {}).get("data") == "unauthorized", f"{first} {code}")
    first, code = _ws_rejected(client, "/ws?token=wrong")
    check("WebSocket with a wrong token is closed with 4401", code == main_api.WS_UNAUTHORIZED, str(code))

    with client.websocket_connect("/ws?token=unit-test-token") as ws:
        ws.send_json({"type": "cancel"})
        check("WebSocket with ?token= is accepted", True)


def test_open_when_unset():
    print("\n[2] Without CIEL_API_TOKEN the local-only behaviour is unchanged")
    main_api.API_TOKEN = ""
    client = TestClient(main_api.app)
    check("/skills needs no token", client.get("/skills").status_code == 200)
    check("/health reports auth not required", client.get("/health").json().get("auth_required") is False)


def test_thoughts_not_streamed_by_default():
    print("\n[3] Raw thoughts.log is not streamed unless enabled")
    check("CIEL_API_STREAM_THOUGHTS defaults to off",
          os.getenv("CIEL_API_STREAM_THOUGHTS") is not None or main_api.STREAM_THOUGHTS is False)


def main():
    print("=" * 72)
    print("API AUTH SUITE (no Ciel startup, no network)")
    print("=" * 72)
    saved = main_api.API_TOKEN
    try:
        test_token_required()
        test_open_when_unset()
        test_thoughts_not_streamed_by_default()
    finally:
        main_api.API_TOKEN = saved
    total = _passed + len(_failed)
    print("\n" + "=" * 72)
    print(f"RESULT: {_passed}/{total} passed")
    for name in _failed:
        print(f"  - {name}")
    print("=" * 72)
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
