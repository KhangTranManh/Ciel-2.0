import { useEffect, useRef, useState, useCallback } from "react";
import { bus } from "../core/bus";
import { ciel } from "../core/ws";
import type { ConfirmRequest, Vitals } from "../core/types";
import { ThoughtAccumulator, type ThoughtEntry } from "../lib/thoughtParser";

export type ConnState = "connecting" | "open" | "closed";

export interface ChatTurn {
  id: number;
  role: "user" | "ciel" | "error";
  text: string;
}

// Single React surface over the bus. Components read this; they never touch the
// WebSocket directly. Voice input would call `send()` here too; voice output would
// subscribe to the same bus "response" event outside React (see io/output/speaker).
export function useCiel() {
  const [connection, setConnection] = useState<ConnState>("connecting");
  const [chat, setChat] = useState<ChatTurn[]>([]);
  const [thoughts, setThoughts] = useState<ThoughtEntry[]>([]);
  const [vitals, setVitals] = useState<Vitals | null>(null);
  const [status, setStatus] = useState<string>("");
  const [pendingConfirm, setPendingConfirm] = useState<ConfirmRequest | null>(null);

  const turnId = useRef(1);
  const accumulator = useRef(new ThoughtAccumulator());

  useEffect(() => {
    const offs = [
      bus.on("connection", setConnection),
      bus.on("status", setStatus),
      bus.on("vitals", setVitals),
      bus.on("response", (text) => {
        setStatus("");
        setChat((c) => [...c, { id: turnId.current++, role: "ciel", text }]);
      }),
      bus.on("error", (text) => {
        setStatus("");
        setChat((c) => [...c, { id: turnId.current++, role: "error", text }]);
      }),
      bus.on("confirm", (req) => setPendingConfirm(req)),
      bus.on("thought", (line) => {
        const entry = accumulator.current.push(line);
        if (entry) setThoughts((t) => [...t.slice(-299), entry]);
      }),
    ];
    ciel.connect();
    return () => {
      offs.forEach((off) => off());
    };
  }, []);

  const send = useCallback((text: string) => {
    const trimmed = text.trim();
    if (!trimmed) return;
    setChat((c) => [...c, { id: turnId.current++, role: "user", text: trimmed }]);
    setStatus("processing");
    ciel.send(trimmed);
  }, []);

  const respondConfirm = useCallback((approved: boolean) => {
    ciel.respondConfirm(approved);
    setPendingConfirm(null);
  }, []);

  return { connection, chat, thoughts, vitals, status, pendingConfirm, send, respondConfirm };
}
