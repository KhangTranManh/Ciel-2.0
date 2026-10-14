"""One process, several front ends: dashboard serving, per-connection approvals,
turn serialization, and Telegram inside the API process.

No models, no network: Ciel is replaced by small fakes, so only the server wiring is
under test.

    python -m backtest.test_api_server
"""
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# A throwaway dashboard build, set before main_api decides what to mount at import.
_WEB = Path(tempfile.mkdtemp(prefix="ciel_web_"))
(_WEB / "index.html").write_text("<html>ciel dashboard</html>", encoding="utf-8")
(_WEB / "main.dart.js").write_text("console.log('app')", encoding="utf-8")
os.environ["CIEL_WEB_DIR"] = str(_WEB)

from fastapi.testclient import TestClient  # noqa: E402

import main_api  # noqa: E402
from api.confirm import WebSocketConfirmer  # noqa: E402
from core.agent_loop import AgentLoop  # noqa: E402

_passed, _failed = 0, []
TOKEN = "unit-test-token"


def check(name, cond, detail=""):
    global _passed
    if cond:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed.append(name)
        print(f"  FAIL  {name}" + (f"  -- {detail}" if detail else ""))


def _next_frame(ws, skip=("vitals",)):
    while True:
        frame = ws.receive_json()
        if frame.get("type") not in skip:
            return frame


# ─────────────────────────────────────────────────────────────────────────────
def test_dashboard():
    print("\n[1] The dashboard is public; every API route still needs the token")
    main_api.API_TOKEN = TOKEN
    client = TestClient(main_api.app)

    page = client.get("/")
    check("/ serves the dashboard without a token", page.status_code == 200 and "ciel dashboard" in page.text,
          f"{page.status_code} {page.text[:60]}")
    asset = client.get("/main.dart.js")
    check("app assets are public", asset.status_code == 200, str(asset.status_code))
    check("assets are revalidated so a deploy is picked up",
          asset.headers.get("cache-control") == "no-cache", str(asset.headers.get("cache-control")))
    check("/skills is not shadowed by the dashboard mount and still needs the token",
          client.get("/skills").status_code == 401)
    check("/tts still needs the token", client.post("/tts", json={"text": "x"}).status_code == 401)
    check("/health stays open", client.get("/health").status_code == 200)
    missing = client.get("/no-such-file.js")
    check("an unknown path is a 404, not an auth bypass into some API",
          missing.status_code == 404, str(missing.status_code))
    check("FastAPI's own /openapi.json is not public",
          client.get("/openapi.json").status_code == 401)


# ─────────────────────────────────────────────────────────────────────────────
class _FakeCore:
    def __init__(self):
        self.confirm_callback = None
        self.cancelled = 0

    def request_cancel(self, reason=""):
        self.cancelled += 1


class _ApprovingAgent:
    """Asks once per turn and reports the answer, like a turn with one risky step."""

    def __init__(self):
        self.core = _FakeCore()

    def run_step(self, user_input, confirm_callback=None):
        approved = confirm_callback("send_gmail_message", f"preview for {user_input}", {"to": "a@b.c"})
        return f"{user_input}: {'approved' if approved else 'denied'}"


def test_ws_approvals_are_per_connection():
    print("\n[2] An approval request goes to the connection that started the turn")
    main_api.API_TOKEN = TOKEN
    saved = main_api.ciel_agent
    main_api.ciel_agent = _ApprovingAgent()
    try:
        client = TestClient(main_api.app)
        with client.websocket_connect(f"/ws?token={TOKEN}") as phone, \
                client.websocket_connect(f"/ws?token={TOKEN}") as browser:
            phone.send_json({"message": "mail report"})
            check("the sender gets a processing status", _next_frame(phone) == {"type": "status", "data": "processing"})
            request = _next_frame(phone)
            check("the sender gets the confirm request",
                  request.get("type") == "confirm_request"
                  and request["data"]["tool_name"] == "send_gmail_message", str(request))

            browser.send_json({"type": "confirm_response", "approved": True})
            phone.send_json({"type": "confirm_response", "approved": "yes"})
            reply = _next_frame(phone)
            check("another device's 'approve' cannot answer this turn, and only JSON true approves",
                  reply == {"type": "response", "data": "mail report: denied"}, str(reply))

            phone.send_json({"message": "second"})
            _next_frame(phone)
            _next_frame(phone)
            phone.send_json({"type": "confirm_response", "approved": True})
            reply = _next_frame(phone)
            check("the sender's own approval is honoured",
                  reply == {"type": "response", "data": "second: approved"}, str(reply))

            phone.send_json({"type": "cancel"})
            time.sleep(0.2)
            check("Stop reaches the core", main_api.ciel_agent.core.cancelled == 1)
    finally:
        main_api.ciel_agent = saved


def test_ws_before_ready():
    print("\n[3] A message before Ciel finished starting is answered, not lost")
    main_api.API_TOKEN = TOKEN
    saved = main_api.ciel_agent
    main_api.ciel_agent = None
    try:
        client = TestClient(main_api.app)
        with client.websocket_connect(f"/ws?token={TOKEN}") as ws:
            ws.send_json({"message": "hello"})
            frame = _next_frame(ws)
            check("the client is told Ciel is still starting",
                  frame.get("type") == "error" and "starting" in frame.get("data", ""), str(frame))
    finally:
        main_api.ciel_agent = saved


# ─────────────────────────────────────────────────────────────────────────────
class _LoopRunner:
    """A real event loop on a thread, like the server's, for the confirmer to post to."""

    def __init__(self):
        import asyncio
        self.loop = asyncio.new_event_loop()
        self.sent = []
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()

    async def send(self, frame):
        self.sent.append(frame)

    async def fail(self, frame):
        raise RuntimeError("socket closed")

    def stop(self):
        self.loop.call_soon_threadsafe(self.loop.stop)


def _ask_in_thread(confirmer):
    box = {}
    t = threading.Thread(target=lambda: box.setdefault("result", confirmer("delete_file", "p", {})))
    t.start()
    return t, box


def test_confirmer_fails_closed():
    print("\n[4] Every failure path of an approval is a denial")
    runner = _LoopRunner()
    try:
        c = WebSocketConfirmer(runner.loop, runner.send, timeout=0.3)
        started = time.time()
        check("no answer before the timeout is a denial", c("delete_file", "p", {}) is False)
        check("...and it waited for the timeout, not forever", time.time() - started < 2)

        c = WebSocketConfirmer(runner.loop, runner.send, timeout=5)
        t, box = _ask_in_thread(c)
        time.sleep(0.2)
        c.close()
        t.join(2)
        check("the client disconnecting while asked is an immediate denial",
              not t.is_alive() and box.get("result") is False)
        check("a closed connection is never asked again", c("delete_file", "p", {}) is False)

        c = WebSocketConfirmer(runner.loop, runner.fail, timeout=5)
        check("a request that cannot be delivered is a denial", c("delete_file", "p", {}) is False)

        c = WebSocketConfirmer(runner.loop, runner.send, timeout=5)
        c.answer(True)
        t, box = _ask_in_thread(c)
        time.sleep(0.2)
        c.answer(False)
        t.join(2)
        check("an answer sent before the question cannot pre-approve it", box.get("result") is False)
    finally:
        runner.stop()


# ─────────────────────────────────────────────────────────────────────────────
class _SlowCore:
    def __init__(self):
        self.confirm_callback = "default"
        self.seen = []
        self.active = 0
        self.max_active = 0
        self._lock = threading.Lock()

    def process(self, text):
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        self.seen.append((text, self.confirm_callback))
        time.sleep(0.05)
        with self._lock:
            self.active -= 1
        return f"done {text}"


def test_turns_are_serialized():
    print("\n[5] One core, several front ends: turns run one at a time")
    loop = AgentLoop.__new__(AgentLoop)
    loop.core = _SlowCore()
    loop._turn_lock = threading.Lock()

    def telegram_confirm(*a):
        return True

    threads = [threading.Thread(target=loop.run_step, args=(f"t{i}", telegram_confirm if i % 2 else None))
               for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    check("never two turns at once", loop.core.max_active == 1, str(loop.core.max_active))
    by_turn = dict(loop.core.seen)
    check("a turn with its own callback uses it", by_turn["t1"] is telegram_confirm)
    check("a turn without one keeps the default", by_turn["t0"] == "default")
    check("the default is restored after every turn", loop.core.confirm_callback == "default")


def test_telegram_routes_its_own_approvals():
    print("\n[6] Telegram inside the API process answers only its own turns")
    os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:unit-test")
    os.environ.setdefault("TELEGRAM_CHAT_ID", "1")
    from core.telegram_interface import TelegramInterface

    calls = []

    class Agent:
        core = _FakeCore()

        def run_step(self, text, confirm_callback=None):
            calls.append((text, confirm_callback))
            return "ok"

    agent = Agent()
    agent.core.confirm_callback = "api-default"
    bot = TelegramInterface(agent)
    def fake_call(method, **params):
        time.sleep(0.05)
        return {"ok": True, "result": []}

    bot._call = fake_call
    bot.start()
    bot._inbox.put("hello")
    deadline = time.time() + 3
    while not calls and time.time() < deadline:
        time.sleep(0.05)
    bot.stop()
    check("the Telegram turn carries Telegram's own approval callback",
          calls and calls[0][1] == bot.confirm_callback, str(calls))
    check("starting the bot does not take over the core's default callback",
          agent.core.confirm_callback == "api-default")


def main():
    print("=" * 72)
    print("API SERVER SUITE (fakes only — no models, no network)")
    print("=" * 72)
    saved = main_api.API_TOKEN
    try:
        test_dashboard()
        test_ws_approvals_are_per_connection()
        test_ws_before_ready()
        test_confirmer_fails_closed()
        test_turns_are_serialized()
        test_telegram_routes_its_own_approvals()
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
