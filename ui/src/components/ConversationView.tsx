// Conversation message feed — matches Figma node 88:2's "messages-feed"
// (93:54): user bubble right-aligned with a plane avatar, assistant
// replies as a full-width card with a plane-mark header row, and a live
// status row (93:112 "typing-row" in the design) while the agent is
// working. The status text is REAL, not decorative -- it streams from the
// backend as each tool actually runs (see ../api.ts's sendMessageStreaming
// and agent/graph.py's run_turn_streaming).

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
          <div key={i} className="ai-msg-row">
            <div className="ai-msg-header">
              <div className="msg-avatar msg-avatar--ai">
                <img src={planeIcon} alt="" width={18} height={18} />
              </div>
              <p className="ai-msg-name">Aero Intel</p>
            </div>
            <div className={`ai-response-card${m.isError ? " ai-response-card--error" : ""}`}>
              <p className="ai-response-text">{m.text}</p>
            </div>
          </div>
        )
      )}

      {liveStatus && (
        <div className="typing-row">
          <div className="msg-avatar msg-avatar--ai">
            <img src={planeIcon} alt="" width={18} height={18} />
          </div>
          <div className="typing-bubble">
            <p className="typing-name">Aero Intel</p>
            <p className="typing-status shimmer-text">{liveStatus}</p>
            <span className="typing-dots" aria-hidden="true">
              <span />
              <span />
              <span />
            </span>
          </div>
        </div>
      )}

      <div ref={bottomRef} />
    </div>
  );
}
