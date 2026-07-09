import { bus } from "./bus";
import type { ClientMessage, ServerMessage } from "./types";

// WebSocket transport. Its ONLY jobs: connect, translate raw frames into typed
// bus events, and expose send(). It knows nothing about React, text, or voice —
// every consumer talks to the bus, not to this. That is the seam that keeps
// input/output modalities interchangeable.

const DEFAULT_WS_URL =
  import.meta.env.VITE_CIEL_WS_URL ?? "ws://localhost:8000/ws";

class CielSocket {
  private ws: WebSocket | null = null;
  private url: string;
  private reconnectTimer: number | null = null;
  private shouldReconnect = true;

  constructor(url: string = DEFAULT_WS_URL) {
    this.url = url;
  }

  connect(): void {
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
      return;
    }
    bus.emit("connection", "connecting");
    this.ws = new WebSocket(this.url);

    this.ws.onopen = () => bus.emit("connection", "open");

    this.ws.onmessage = (ev) => {
      let msg: ServerMessage;
      try {
        msg = JSON.parse(ev.data) as ServerMessage;
      } catch {
        // Non-JSON frame — treat as a raw thought line so nothing is lost.
        bus.emit("thought", String(ev.data));
        return;
      }
      this.dispatch(msg);
    };

    this.ws.onclose = () => {
      bus.emit("connection", "closed");
      if (this.shouldReconnect) this.scheduleReconnect();
    };

    this.ws.onerror = () => {
      // onclose will follow and handle reconnect; just surface it.
      this.ws?.close();
    };
  }

  private dispatch(msg: ServerMessage): void {
    switch (msg.type) {
      case "thought":
        bus.emit("thought", msg.data);
        break;
      case "vitals":
        bus.emit("vitals", msg.data);
        break;
      case "status":
        bus.emit("status", msg.data);
        break;
      case "response":
        bus.emit("response", msg.data);
        break;
      case "error":
        bus.emit("error", msg.data);
        break;
      case "confirm_request":
        bus.emit("confirm", msg.data);
        break;
    }
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimer != null) return;
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, 2000);
  }

  private rawSend(payload: ClientMessage): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(payload));
    } else {
      console.warn("[ws] send dropped — socket not open", payload);
    }
  }

  /** The single input contract. Keyboard OR (future) voice STT both call this. */
  send(text: string): void {
    const trimmed = text.trim();
    if (!trimmed) return;
    this.rawSend({ message: trimmed });
  }

  /** Answer a safety-gate confirmation request. */
  respondConfirm(approved: boolean): void {
    this.rawSend({ type: "confirm_response", approved });
  }

  dispose(): void {
    this.shouldReconnect = false;
    if (this.reconnectTimer != null) window.clearTimeout(this.reconnectTimer);
    this.ws?.close();
  }
}

export const ciel = new CielSocket();
