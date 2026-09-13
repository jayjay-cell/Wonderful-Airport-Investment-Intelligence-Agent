// Shared conversation/message types. A "conversation" here is purely a
// client-side grouping (title + message list) keyed by the backend
// session_id -- the backend has no concept of conversation titles or a
// conversation list; it only tracks message history per session_id (see
// api/session_store.py). Multiple conversations in the sidebar means
// multiple session_ids, each with its own backend-side history.

export interface Message {
  role: "user" | "assistant";
  text: string;
  isError?: boolean;
}

export interface Conversation {
  id: string; // == backend session_id once the first message has been sent
  title: string;
  meta: string; // e.g. "Today · 2 messages"
  messages: Message[];
}

export function deriveTitle(firstUserMessage: string): string {
  const trimmed = firstUserMessage.trim();
  if (trimmed.length <= 48) return trimmed;
  return trimmed.slice(0, 45).trimEnd() + "…";
}

export function formatMeta(messageCount: number): string {
  const turns = Math.ceil(messageCount / 2);
  return `Today · ${turns} message${turns === 1 ? "" : "s"}`;
}
