import { useEffect, useState } from "react";
import { checkHealth, sendMessage } from "./api";
import { ChatComposer } from "./components/ChatComposer";
import { ConversationView, Message } from "./components/ConversationView";
import { WelcomeScreen } from "./components/WelcomeScreen";

export default function App() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [serverOnline, setServerOnline] = useState(true);

  useEffect(() => {
    checkHealth().then(setServerOnline);
  }, []);

  async function handleSend(text: string) {
    setMessages((prev) => [
      ...prev,
      { role: "user", text },
      { role: "assistant", text: "Thinking...", pending: true },
    ]);
    setSending(true);

    try {
      const result = await sendMessage(text, sessionId);
      setSessionId(result.session_id);
      setMessages((prev) => {
        const withoutPending = prev.filter((m) => !m.pending);
        return [...withoutPending, { role: "assistant", text: result.response }];
      });
    } catch (err) {
      setMessages((prev) => {
        const withoutPending = prev.filter((m) => !m.pending);
        return [
          ...withoutPending,
          {
            role: "assistant",
            text: "Sorry — I couldn't reach the analysis service. Please check that the backend is running and try again.",
          },
        ];
      });
    } finally {
      setSending(false);
    }
  }

  const hasConversation = messages.length > 0;

  return (
    <div className="app-shell">
      {!hasConversation ? (
        <div className="welcome-panel">
          {!serverOnline && (
            <div className="offline-banner">
              Backend server is unreachable at /api — start the FastAPI server and refresh.
            </div>
          )}
          <WelcomeScreenContent onSelectSuggestion={handleSend} sending={sending} />
        </div>
      ) : (
        <div className="conversation-shell">
          {!serverOnline && (
            <div className="offline-banner">
              Backend server is unreachable at /api — start the FastAPI server and refresh.
            </div>
          )}
          <ConversationView messages={messages} />
          <ChatComposer onSend={handleSend} disabled={sending} />
        </div>
      )}
    </div>
  );
}

// WelcomeScreen renders its own .welcome-panel wrapper + bg-glow; here we
// need the composer nested inside that same panel (per the Figma layout),
// so this thin wrapper renders WelcomeScreen's inner content plus the
// composer as siblings within App's own .welcome-panel above.
function WelcomeScreenContent({
  onSelectSuggestion,
  sending,
}: {
  onSelectSuggestion: (text: string) => void;
  sending: boolean;
}) {
  return (
    <>
      <WelcomeScreen onSelectSuggestion={onSelectSuggestion} />
      <ChatComposer onSend={onSelectSuggestion} disabled={sending} showLabel />
    </>
  );
}
