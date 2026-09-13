// Composer bar — matches Figma node 76:268 exactly: 64px attachment button,
// nested translucent input pill containing a 64px mic button inside it, and
// an 80px send button. Mic icon present per design but non-functional —
// voice is an explicit later add-on (plan Section 5a).

import { KeyboardEvent, useState } from "react";
import paperclipIcon from "../assets/paperclip.svg";
import micIcon from "../assets/mic.svg";
import arrowUpIcon from "../assets/arrow-up.svg";

interface ChatComposerProps {
  onSend: (message: string) => void;
  disabled?: boolean;
  showLabel?: boolean;
}

export function ChatComposer({ onSend, disabled, showLabel }: ChatComposerProps) {
  const [value, setValue] = useState("");

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
      <div className="composer">
        <button className="composer-icon-btn" title="Attach a report (not yet supported)" disabled>
          <img src={paperclipIcon} alt="" width={18} height={18} />
        </button>
        <div className="composer-input-pill">
          <input
            type="text"
            placeholder="Ask Aero Intel about airport demand, yields, or infrastructure moves..."
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={disabled}
          />
          <button className="composer-icon-btn composer-icon-btn--inset" title="Voice input (coming soon)" disabled>
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
