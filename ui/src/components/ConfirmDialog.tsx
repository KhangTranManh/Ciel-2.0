import { useEffect, useRef, useState } from "react";
import { CONFIRM_TIMEOUT_SEC, type ConfirmRequest } from "../core/types";

// Safety-gate confirmation — mirrors main_api 60s wait.
// Enter/Y approve · Esc/N deny · backdrop does not dismiss · simple focus trap.

export function ConfirmDialog({
  request,
  onRespond,
  timeoutSec = CONFIRM_TIMEOUT_SEC,
}: {
  request: ConfirmRequest;
  onRespond: (approved: boolean) => void;
  timeoutSec?: number;
}) {
  const [left, setLeft] = useState(timeoutSec);
  const answered = useRef(false);
  const onRespondRef = useRef(onRespond);
  const dialogRef = useRef<HTMLDivElement>(null);
  const approveRef = useRef<HTMLButtonElement>(null);
  onRespondRef.current = onRespond;

  const answer = (approved: boolean) => {
    if (answered.current) return;
    answered.current = true;
    onRespondRef.current(approved);
  };

  useEffect(() => {
    answered.current = false;
    setLeft(timeoutSec);
    // Focus approve on open (safety: explicit primary action).
    window.setTimeout(() => approveRef.current?.focus(), 0);
  }, [request, timeoutSec]);

  useEffect(() => {
    if (left <= 0) {
      answer(false);
      return;
    }
    const t = window.setTimeout(() => setLeft((s) => s - 1), 1000);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [left]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" || e.key === "n" || e.key === "N") {
        e.preventDefault();
        e.stopPropagation();
        answer(false);
        return;
      }
      if (e.key === "Enter" || e.key === "y" || e.key === "Y") {
        // Don't approve while typing inside a nested control (none currently).
        if ((e.target as HTMLElement)?.tagName === "TEXTAREA") return;
        e.preventDefault();
        e.stopPropagation();
        answer(true);
        return;
      }
      // Focus trap: Tab cycles Approve ↔ Deny.
      if (e.key === "Tab" && dialogRef.current) {
        const focusable = dialogRef.current.querySelectorAll<HTMLElement>(
          "button:not([disabled])"
        );
        if (focusable.length === 0) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const urgent = left <= 10;

  return (
    <div className="modal-backdrop" role="presentation">
      <div
        ref={dialogRef}
        className="modal"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
        aria-describedby="confirm-preview"
      >
        <div className="modal-head">
          <span className="modal-warn" id="confirm-title">
            Safety Check
          </span>
          <span className="modal-tool">{request.tool_name}</span>
          <span
            className={`modal-timer${urgent ? " urgent" : ""}`}
            title="Auto-deny when the backend times out (~60s)"
          >
            {left}s
          </span>
        </div>
        <pre className="modal-preview" id="confirm-preview">
          {request.preview}
        </pre>
        {request.tool_args && Object.keys(request.tool_args).length > 0 && (
          <pre className="modal-args">{JSON.stringify(request.tool_args, null, 2)}</pre>
        )}
        <div className="modal-hint">Enter / Y approve · Esc / N deny · auto-deny at 0s</div>
        <div className="modal-actions">
          <button type="button" className="btn-deny" onClick={() => answer(false)}>
            Deny
          </button>
          <button
            type="button"
            className="btn-approve"
            ref={approveRef}
            onClick={() => answer(true)}
          >
            Approve
          </button>
        </div>
      </div>
    </div>
  );
}
