"""Safety approvals over a WebSocket connection.

CielCore calls the confirm callback on the agent's worker thread and blocks until it
returns. This bridges that thread to the async server: the request goes to the one
client whose message started the turn, and every failure path is a denial.
"""
from __future__ import annotations

import asyncio
import threading
from typing import Awaitable, Callable

SendJson = Callable[[dict], Awaitable[None]]

CONFIRM_TIMEOUT_SECONDS = 60
DELIVERY_TIMEOUT_SECONDS = 5


class WebSocketConfirmer:
    def __init__(self, loop: asyncio.AbstractEventLoop, send_json: SendJson,
                 timeout: float = CONFIRM_TIMEOUT_SECONDS):
        self._loop = loop
        self._send_json = send_json
        self._timeout = timeout
        self._lock = threading.Lock()
        self._event = threading.Event()
        self._pending = False
        self._approved = False
        self._closed = False

    def __call__(self, tool_name: str, preview: str, tool_args: dict) -> bool:
        with self._lock:
            if self._closed:
                return False
            self._event.clear()
            self._approved = False
            self._pending = True
        try:
            frame = {"type": "confirm_request",
                     "data": {"tool_name": tool_name, "preview": preview, "tool_args": tool_args}}
            asyncio.run_coroutine_threadsafe(self._send_json(frame), self._loop) \
                .result(timeout=DELIVERY_TIMEOUT_SECONDS)
        except Exception as e:
            print(f"[API Safety] Could not deliver confirm request ({e}) — denying {tool_name}.")
            self._finish()
            return False

        answered = self._event.wait(timeout=self._timeout)
        with self._lock:
            approved = answered and self._approved and not self._closed
            self._pending = False
        if not answered:
            print(f"[API Safety] No answer within {self._timeout:.0f}s — denying {tool_name}.")
        return approved

    def answer(self, approved) -> None:
        """Only a literal JSON `true` approves; anything else is a denial."""
        with self._lock:
            if not self._pending:
                return
            self._approved = approved is True
            self._event.set()

    def close(self) -> None:
        """The client left; a question still waiting for it is denied immediately."""
        with self._lock:
            self._closed = True
            self._approved = False
            self._event.set()

    def _finish(self) -> None:
        with self._lock:
            self._pending = False
