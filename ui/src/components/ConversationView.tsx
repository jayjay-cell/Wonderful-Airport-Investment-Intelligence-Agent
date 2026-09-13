// Conversation message feed: user messages are compact right-aligned
// bubbles; assistant replies are plain readable text blocks with no
// repeated name/logo per message (identity is shown once, in the sidebar
// and topbar). The live status row's text is REAL, not decorative -- it
// streams from the backend as each tool actually runs (see
// ../api.ts's sendMessageStreaming and agent/graph.py's run_turn_streaming).

import { useEffect, useRef } from "react";
import planeIcon from "../assets/plane.svg";
import type { Message } from "../conversation";

interface ConversationViewProps {
  messages: Message[];
  liveStatus: string | null;
}

export function ConversationView({ messages, liveStatus }: ConversationViewProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, liveStatus]);

  return (
    <div className="conversation">
      {messages.map((m, i) =>
        m.role === "user" ? (
          <div key={i} className="user-msg-row">
            <div className="msg-avatar msg-avatar--user">
              <img src={planeIcon} alt="" width={18} height={18} />
            </div>
            <div className="user-bubble">
              <p className="user-bubble-text">{m.text}</p>
            </div>
          </div>
        ) : (
          <div key={i} className={`ai-msg-row${m.isError ? " ai-msg-row--error" : ""}`}>
            {m.isError && (
              <div className="ai-error-icon" aria-hidden="true">
                !
              </div>
            )}
            <div className="ai-response-text">{m.text}</div>
          </div>
        )
      )}

      {liveStatus && (
        <div className="typing-row">
          <span className="typing-dot" aria-hidden="true" />
          <p className="typing-status shimmer-text">{liveStatus}</p>
        </div>
      )}

      <div ref={bottomRef} />
    </div>
  );
}
