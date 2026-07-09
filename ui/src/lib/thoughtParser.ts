// Parses the raw thoughts.log lines the backend streams over the "thought" channel.
//
// The backend tails the log and sends ONE line at a time, but a logical entry spans
// several lines:  "[ts] [ACTOR] [ACTION]"  then content line(s)  then a "----" rule.
// So parsing is a small state machine (accumulate content under the last header).
//
// Nothing here is hardcoded per actor/tool — any new actor tag (a new skill, a new
// tier) renders automatically. Colors fall back to a neutral default for unknown tags.

export interface ThoughtEntry {
  id: number;
  ts: string;
  actor: string;
  action: string;
  content: string;
}

const HEADER_RE = /^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]\s+\[([^\]]+)\]\s+\[([^\]]+)\]\s*$/;
const SEPARATOR_RE = /^-{20,}\s*$/;

export type ParsedLine =
  | { kind: "header"; ts: string; actor: string; action: string }
  | { kind: "separator" }
  | { kind: "content"; text: string };

export function parseLine(line: string): ParsedLine {
  const h = HEADER_RE.exec(line);
  if (h) return { kind: "header", ts: h[1], actor: h[2], action: h[3] };
  if (SEPARATOR_RE.test(line)) return { kind: "separator" };
  return { kind: "content", text: line };
}

// Stateful accumulator: feed it lines in order, it emits completed ThoughtEntry
// objects. Keeps the "current" header open until a new header or a separator.
export class ThoughtAccumulator {
  private nextId = 1;
  private current: Omit<ThoughtEntry, "id"> | null = null;

  /** Returns a finished entry when one closes, else null. */
  push(line: string): ThoughtEntry | null {
    const parsed = parseLine(line);
    if (parsed.kind === "header") {
      const finished = this.flush();
      this.current = { ts: parsed.ts, actor: parsed.actor, action: parsed.action, content: "" };
      return finished;
    }
    if (parsed.kind === "separator") {
      return this.flush();
    }
    // content
    if (this.current) {
      this.current.content += (this.current.content ? "\n" : "") + parsed.text;
    }
    return null;
  }

  private flush(): ThoughtEntry | null {
    if (!this.current) return null;
    const entry: ThoughtEntry = { id: this.nextId++, ...this.current };
    this.current = null;
    return entry;
  }
}

// Actor -> accent color. Unknown actors get a neutral slate, so nothing breaks
// when a new tier/skill starts logging under a tag this map has never seen.
const ACTOR_COLORS: Record<string, string> = {
  USER: "#8a8f98",
  BRAIN: "#8d7fe8",
  WORKER: "#d98b5f",
  MIDDLEWARE: "#57acae",
  TOOL: "#c9a24b",
  HEALING: "#e0a93e",
  SAFETY: "#d4695e",
  RAG: "#6fb98f",
  SYSTEM: "#676b7a",
};

export function actorColor(actor: string): string {
  return ACTOR_COLORS[actor.toUpperCase()] ?? "#9aa0ad";
}
