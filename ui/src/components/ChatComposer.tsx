// Message composer: attachment button (disabled, not yet supported), a text
// input with an inset mic button wired to real browser speech recognition
// (see ../useVoiceInput.ts -- click to dictate, transcript is appended into
// the field rather than auto-sent), and a send button. The `compact` prop
// renders the smaller in-conversation variant used once a chat is active.

import { KeyboardEvent, useState } from "react";
import paperclipIcon from "../assets/paperclip.svg";
import micIcon from "../assets/mic.svg";
import arrowUpIcon from "../assets/arrow-up.svg";
import { useVoiceInput } from "../useVoiceInput";

interface ChatComposerProps {
  onSend: (message: string) => void;
  disabled?: boolean;
  showLabel?: boolean;
  compact?: boolean;
  placeholder?: string;
}

export function ChatComposer({ onSend, disabled, showLabel, compact, placeholder }: ChatComposerProps) {
  const [value, setValue] = useState("");

  const { listening, supported, toggle } = useVoiceInput((transcript) => {
    setValue((prev) => (prev ? `${prev} ${transcript}` : transcript));
  });

  function handleSend() {
    const trimmed = value.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed);
    setValue("");
  }

  function handleKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter") {
      handleSend();
    }
  }

  return (
    <div className="composer-area">
      {showLabel && <p className="composer-label">START A NEW ANALYSIS</p>}
      <div className={`composer${compact ? " composer--compact" : ""}`}>
        <button className="composer-icon-btn" title="Attach a report (not yet supported)" disabled>
          <img src={paperclipIcon} alt="" width={18} height={18} />
        </button>
        <div className="composer-input-pill">
          <input
            type="text"
            placeholder={placeholder ?? "Ask Aero Intel about airport demand, yields, or infrastructure moves..."}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={disabled}
          />
          <button
            className={`composer-icon-btn composer-icon-btn--inset${listening ? " listening" : ""}`}
            title={
              !supported
                ? "Voice input isn't supported in this browser"
                : listening
                ? "Listening… click to stop"
                : "Voice input"
            }
            disabled={disabled || !supported}
            onClick={toggle}
            type="button"
          >
            <img src={micIcon} alt="" width={18} height={18} />
          </button>
        </div>
        <button className="composer-send-btn" onClick={handleSend} disabled={disabled || !value.trim()}>
          <img src={arrowUpIcon} alt="Send" width={18} height={18} />
        </button>
      </div>
    </div>
  );
}
