import type { ConfirmRequest } from "../core/types";

// Safety-gate confirmation. When the backend hits a high-risk tool it sends a
// confirm_request and BLOCKS until the user answers — this modal is that answer.
// Fail-safe: closing/denying returns false, which the backend treats as [CANCELLED].
export function ConfirmDialog({
  request,
  onRespond,
}: {
  request: ConfirmRequest;
  onRespond: (approved: boolean) => void;
}) {
  return (
    <div className="modal-backdrop" onClick={() => onRespond(false)}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <span className="modal-warn">⚠ Safety Check</span>
          <span className="modal-tool">{request.tool_name}</span>
        </div>
        <pre className="modal-preview">{request.preview}</pre>
        {request.tool_args && Object.keys(request.tool_args).length > 0 && (
          <pre className="modal-args">{JSON.stringify(request.tool_args, null, 2)}</pre>
        )}
        <div className="modal-actions">
          <button className="btn-deny" onClick={() => onRespond(false)}>
            Deny
          </button>
          <button className="btn-approve" onClick={() => onRespond(true)}>
            Approve
          </button>
        </div>
      </div>
    </div>
  );
}
