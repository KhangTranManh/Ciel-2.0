import { useState, type KeyboardEvent } from "react";

// Text input modality. It produces a string and hands it to `onSubmit` — exactly
// what a future VoiceInput (STT) will also do. Neither knows about the other; the
// parent wires both to the same ciel.send(). That symmetry is the whole point.
export function TextInput({
  onSubmit,
  disabled,
}: {
  onSubmit: (text: string) => void;
  disabled?: boolean;
}) {
  const [value, setValue] = useState("");

  const submit = () => {
    if (!value.trim()) return;
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
        className="text-input"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={onKeyDown}
        placeholder="Message Ciel…  (Enter to send, Shift+Enter for newline)"
        rows={1}
        disabled={disabled}
      />
      {/* Voice input slots in right here later — same onSubmit contract. */}
      <button className="send-btn" onClick={submit} disabled={disabled || !value.trim()}>
        Send
      </button>
    </div>
  );
}
