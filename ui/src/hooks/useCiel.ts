import { useEffect, useRef, useState, useCallback } from "react";
import { bus } from "../core/bus";
import { ciel } from "../core/ws";
import type { ConfirmRequest, Vitals } from "../core/types";

export type ConnState = "connecting" | "open" | "closed";

export interface ChatTurn {
  id: number;
  role: "user" | "ciel" | "error" | "notice";
  text: string;
}

const MAX_CHAT = 200;

// Bus ↔ React. No thoughts.log UI — chat + status + confirm + vitals only.
export function useCiel() {
  const [connection, setConnection] = useState<ConnState>("connecting");
  const [chat, setChat] = useState<ChatTurn[]>([]);
  const [vitals, setVitals] = useState<Vitals | null>(null);
  const [status, setStatus] = useState<string>("");
  const [pendingConfirm, setPendingConfirm] = useState<ConfirmRequest | null>(null);

  const turnId = useRef(1);

  const push = useCallback((role: ChatTurn["role"], text: string) => {
    setChat((c) => {
      const next = [...c, { id: turnId.current++, role, text }];
      return next.length > MAX_CHAT ? next.slice(-MAX_CHAT) : next;
    });
  }, []);

  useEffect(() => {
    const offs = [
      bus.on("connection", setConnection),
      bus.on("status", setStatus),
      bus.on("vitals", setVitals),
      bus.on("response", (text) => {
        setStatus("");
        push("ciel", text);
      }),
      bus.on("error", (text) => {
        setStatus("");
        push("error", text);
      }),
      bus.on("notice", (text) => push("notice", text)),
      bus.on("confirm", (req) => setPendingConfirm(req)),
      // "thought" frames still arrive on the bus from main_api; intentionally ignored.
    ];
    ciel.connect();
    return () => {
      offs.forEach((off) => off());
    };
  }, [push]);

  const send = useCallback(
    (text: string) => {
      const trimmed = text.trim();
      if (!trimmed) return;
      if (!ciel.isOpen) {
        push("notice", "Not connected — message was not sent. Wait for the socket to reopen.");
        return;
      }
      push("user", trimmed);
      setStatus("processing");
      if (!ciel.send(trimmed)) {
        setStatus("");
        push("notice", "Send failed — socket closed mid-flight.");
      }
    },
    [push]
  );

  const respondConfirm = useCallback((approved: boolean) => {
    ciel.respondConfirm(approved);
    setPendingConfirm(null);
  }, []);

  const cancel = useCallback(() => {
    if (!status) return;
    if (!ciel.cancel()) {
      bus.emit("notice", "Could not send cancel — socket not open.");
      return;
    }
    setStatus("cancelling");
  }, [status]);

  const retryLast = useCallback(() => {
    for (let i = chat.length - 1; i >= 0; i--) {
      if (chat[i].role === "user") {
        send(chat[i].text);
        return;
      }
    }
  }, [chat, send]);

  return {
    connection,
    chat,
    vitals,
    status,
    pendingConfirm,
    send,
    respondConfirm,
    cancel,
    retryLast,
  };
}
