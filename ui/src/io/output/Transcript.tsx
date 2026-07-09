import { useEffect, useRef } from "react";
import type { ChatTurn } from "../../hooks/useCiel";

// The visual (text) output modality. It renders the conversation turns. A voice
// output modality (speaker.ts) consumes the SAME bus "response" events independently,
// so enabling speech never touches this component.
export function Transcript({ chat, status }: { chat: ChatTurn[]; status: string }) {
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [chat, status]);

  return (
    <div className="transcript">
      {chat.length === 0 && (
        <div className="transcript-empty">
          <div className="glyph">◇</div>
          <p>Ciel is standing by, Master.</p>
        </div>
      )}
      {chat.map((turn) => (
        <div key={turn.id} className={`turn turn-${turn.role}`}>
          <div className="turn-role">
            {turn.role === "user" ? "You" : turn.role === "error" ? "Error" : "Ciel"}
          </div>
          <div className="turn-text">{turn.text}</div>
        </div>
      ))}
      {status === "processing" && (
        <div className="turn turn-ciel">
          <div className="turn-role">Ciel</div>
          <div className="turn-text thinking">
            <span className="dot" />
            <span className="dot" />
            <span className="dot" />
          </div>
        </div>
      )}
      <div ref={endRef} />
    </div>
  );
}
