import { useEffect, useRef, useState, type KeyboardEvent } from "react";

// Text input modality — same onSubmit contract as VoiceInput.
export function TextInput({
  onSubmit,
  disabled,
  autoFocus,
  focusToken,
}: {
  onSubmit: (text: string) => void;
  disabled?: boolean;
  autoFocus?: boolean;
  /** Increment to re-focus after a reply (parent drives). */
  focusToken?: number;
}) {
  const [value, setValue] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (autoFocus && !disabled) ref.current?.focus();
  }, [autoFocus, disabled]);

  useEffect(() => {
    if (focusToken != null && focusToken > 0 && !disabled) {
      ref.current?.focus();
    }
  }, [focusToken, disabled]);

  // Grow with content, cap height.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 140)}px`;
  }, [value]);

  const submit = () => {
    if (!value.trim() || disabled) return;
    onSubmit(value);
    setValue("");
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <div className="input-row">
      <textarea
        ref={ref}
        className="text-input"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={onKeyDown}
        placeholder={
          disabled ? "Waiting for connection…" : "Message Ciel… (Enter send · Shift+Enter newline)"
        }
        rows={1}
        disabled={disabled}
        aria-label="Message Ciel"
      />
      <button type="button" className="send-btn" onClick={submit} disabled={disabled || !value.trim()}>
        Send
      </button>
    </div>
  );
}
