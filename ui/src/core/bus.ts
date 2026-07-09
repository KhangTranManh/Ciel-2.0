import type { BusEvents } from "./types";

// Tiny typed event bus — the single hub every part of the app subscribes to.
//
// Why this exists: it decouples *what happened* (a response arrived, a thought
// was logged) from *who reacts* (the transcript renders it; a future Speaker
// speaks it; a future analytics panel counts it). Adding voice output later is
// "bus.on('response', speak)" and nothing else changes. Adding voice input is
// "call ciel.send(text)" from an STT result — also nothing else changes.

type Handler<T> = (payload: T) => void;

class EventBus {
  // Internal storage is intentionally loosely typed (Handler<any>); the public
  // on()/emit() signatures below stay fully type-safe, which is what callers see.
  private handlers: Partial<Record<keyof BusEvents, Set<Handler<any>>>> = {};

  on<K extends keyof BusEvents>(event: K, handler: Handler<BusEvents[K]>): () => void {
    (this.handlers[event] ??= new Set()).add(handler as Handler<any>);
    // Return an unsubscribe fn (convenient for React useEffect cleanup).
    return () => this.handlers[event]?.delete(handler as Handler<any>);
  }

  emit<K extends keyof BusEvents>(event: K, payload: BusEvents[K]): void {
    this.handlers[event]?.forEach((h) => {
      try {
        h(payload);
      } catch (err) {
        // A misbehaving subscriber must never break the bus for others.
        console.error(`[bus] handler for "${String(event)}" threw`, err);
      }
    });
  }
}

export const bus = new EventBus();
