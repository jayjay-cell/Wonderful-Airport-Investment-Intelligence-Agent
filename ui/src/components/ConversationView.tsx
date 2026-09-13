// Conversation view — designed separately from Figma (no source design
// existed for this screen; see plan Section 5a). Extends the welcome
// screen's visual language (accent color, rounded cards, fonts) rather
// than a generic chat-bubble style, since it needs to carry the agent's
// structured, evidence-labeled answers legibly.

import { useEffect, useRef } from "react";

export interface Message {
  role: "user" | "assistant";
  text: string;
  pending?: boolean;
}

interface ConversationViewProps {
  messages: Message[];
}

export function ConversationView({ messages }: ConversationViewProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  return (
    <div className="conversation">
      {messages.map((m, i) => (
        <div key={i} className={`message-row ${m.role}`}>
          <div
            className={`message-bubble ${m.role}${m.pending ? " pending" : ""}`}
          >
            {m.text}
          </div>
        </div>
      ))}
      <div ref={bottomRef} />
    </div>
  );
}
