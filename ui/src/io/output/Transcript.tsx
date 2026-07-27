import { useCallback, useEffect, useRef, useState } from "react";
import type { ChatTurn, ConnState } from "../../hooks/useCiel";
import { MarkdownBody } from "../../lib/markdown";

export function Transcript({
  chat,
  status,
  connection,
  onRetry,
}: {
  chat: ChatTurn[];
  status: string;
  connection?: ConnState;
  onRetry?: () => void;
}) {
  const scrollerRef = useRef<HTMLDivElement>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const stickBottom = useRef(true);
  const [showJump, setShowJump] = useState(false);
  const [copiedId, setCopiedId] = useState<number | null>(null);

  const onScroll = useCallback(() => {
    const el = scrollerRef.current;
    if (!el) return;
    const dist = el.scrollHeight - el.scrollTop - el.clientHeight;
    const atBottom = dist < 80;
    stickBottom.current = atBottom;
    setShowJump(!atBottom && chat.length > 0);
  }, [chat.length]);

  useEffect(() => {
    if (!stickBottom.current) {
      setShowJump(true);
      return;
    }
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [chat, status]);

  const jumpLatest = () => {
    stickBottom.current = true;
    setShowJump(false);
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  const copyText = async (id: number, text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopiedId(id);
      window.setTimeout(() => setCopiedId((c) => (c === id ? null : c)), 1200);
    } catch {
      /* clipboard denied */
    }
  };

  const busy = Boolean(status) && status !== "cancelling";
  const cancelling = status === "cancelling";
  const offline = connection === "closed" || connection === "connecting";

  return (
    <div className="transcript-wrap">
      <div className="transcript" ref={scrollerRef} onScroll={onScroll} aria-live="polite">
        {chat.length === 0 && !busy && !cancelling && (
          <div className="transcript-empty">
            <div className="glyph" aria-hidden>
              ›
            </div>
            {offline ? (
              <>
                <p>
                  Waiting for <em className="brand-serif">Ciel</em>…
                </p>
                <p className="transcript-hint">
                  Backend: <code>python main_api.py</code>
                </p>
              </>
            ) : (
              <>
                <p>
                  Message <em className="brand-serif">Ciel</em> to begin.
                </p>
                <p className="transcript-hint">
                  Enter send · Stop / Esc while running · Skills on the left
                </p>
              </>
            )}
          </div>
        )}

        {chat.map((turn) => (
          <div key={turn.id} className={`turn turn-${turn.role}`}>
            {turn.role === "user" && (
              <>
                <div className="turn-meta">
                  <span className="turn-role">You</span>
                  <div className="turn-actions">
                    <button
                      type="button"
                      className="turn-action-btn"
                      onClick={() => copyText(turn.id, turn.text)}
                    >
                      {copiedId === turn.id ? "Copied" : "Copy"}
                    </button>
                    {onRetry && (
                      <button type="button" className="turn-action-btn" onClick={onRetry}>
                        Retry
                      </button>
                    )}
                  </div>
                </div>
                <div className="turn-text user-bubble">{turn.text}</div>
              </>
            )}

            {(turn.role === "ciel" || turn.role === "error") && (
              <>
                <div className="turn-meta">
                  <span className="turn-role">{turn.role === "error" ? "Error" : "Ciel"}</span>
                  {turn.role === "ciel" && (
                    <div className="turn-actions">
                      <button
                        type="button"
                        className="turn-action-btn"
                        onClick={() => copyText(turn.id, turn.text)}
                      >
                        {copiedId === turn.id ? "Copied" : "Copy"}
                      </button>
                    </div>
                  )}
                </div>
                <div
                  className={`turn-text${turn.role === "error" ? " error-bubble" : " reply-bubble"}`}
                >
                  {turn.role === "ciel" ? <MarkdownBody text={turn.text} /> : turn.text}
                </div>
              </>
            )}

            {turn.role === "notice" && (
              <>
                <div className="turn-meta">
                  <span className="turn-role">Notice</span>
                </div>
                <div className="turn-text notice-bubble">{turn.text}</div>
              </>
            )}
          </div>
        ))}

        {busy && (
          <div className="turn turn-ciel" aria-busy="true">
            <div className="turn-meta">
              <span className="turn-role">Ciel</span>
            </div>
            <div className="turn-text thinking reply-bubble">
              <span className="dot" />
              <span className="dot" />
              <span className="dot" />
            </div>
          </div>
        )}
        {cancelling && (
          <div className="turn turn-notice">
            <div className="turn-meta">
              <span className="turn-role">Notice</span>
            </div>
            <div className="turn-text notice-bubble">Stopping at the next step boundary…</div>
          </div>
        )}

        <div ref={endRef} />
      </div>
      {showJump && (
        <button type="button" className="jump-latest" onClick={jumpLatest}>
          Latest ↓
        </button>
      )}
    </div>
  );
}
