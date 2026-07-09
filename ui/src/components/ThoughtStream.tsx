import { useEffect, useRef } from "react";
import { actorColor, type ThoughtEntry } from "../lib/thoughtParser";

// Live view of thoughts.log as it streams in. Actor tags are colored generically
// (see actorColor), so a new tier or skill that starts logging shows up with no
// change here — the "add a skill, the UI just reflects it" principle applied to logs.
export function ThoughtStream({ thoughts }: { thoughts: ThoughtEntry[] }) {
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    endRef.current?.scrollIntoView();
  }, [thoughts]);

  return (
    <div className="thought-stream">
      <div className="panel-title">Cognition Stream</div>
      <div className="thought-list">
        {thoughts.length === 0 && <div className="thought-empty">Awaiting activity…</div>}
        {thoughts.map((t) => (
          <div key={t.id} className="thought">
            <span className="thought-actor" style={{ color: actorColor(t.actor) }}>
              {t.actor}
            </span>
            <span className="thought-action">{t.action}</span>
            {t.content && <div className="thought-content">{t.content}</div>}
          </div>
        ))}
        <div ref={endRef} />
      </div>
    </div>
  );
}
