import { useEffect, useRef, useState } from "react";
import { ChatApiError, checkHealth, sendMessage } from "./api";
import { ChatComposer } from "./components/ChatComposer";
import { ConversationView, Message } from "./components/ConversationView";
import { WelcomeScreen } from "./components/WelcomeScreen";

// Lightweight perceived-latency mechanism (Section 7): no SSE/streaming
// backend change — cycling status text client-side while the one blocking
// POST /chat call is in flight. This is deliberately the smallest clean
// mechanism rather than a real progress protocol, so it doesn't delay the
// actual performance fixes; it just gives the user a sense of what stage
// a slow request is likely in.
const PROGRESS_STEPS = [
  "Understanding your question…",
  "Identifying candidate airports…",
  "Loading aviation data…",
  "Scoring candidates…",
  "Preparing your answer…",
];
const PROGRESS_STEP_INTERVAL_MS = 3500;

export default function App() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [serverOnline, setServerOnline] = useState(true);
  const progressTimerRef = useRef<number | null>(null);

  useEffect(() => {
    checkHealth().then(setServerOnline);
  }, []);

  function stopProgressCycle() {
    if (progressTimerRef.current !== null) {
      window.clearInterval(progressTimerRef.current);
      progressTimerRef.current = null;
    }
  }

  function startProgressCycle() {
    let stepIndex = 0;
    setMessages((prev) => {
      const withoutPending = prev.filter((m) => !m.pending);
      return [...withoutPending, { role: "assistant", text: PROGRESS_STEPS[0], pending: true }];
    });
    progressTimerRef.current = window.setInterval(() => {
      stepIndex = Math.min(stepIndex + 1, PROGRESS_STEPS.length - 1);
      setMessages((prev) => {
        const withoutPending = prev.filter((m) => !m.pending);
        return [...withoutPending, { role: "assistant", text: PROGRESS_STEPS[stepIndex], pending: true }];
      });
    }, PROGRESS_STEP_INTERVAL_MS);
  }

  async function handleSend(text: string) {
    setMessages((prev) => [...prev, { role: "user", text }]);
    startProgressCycle();
    setSending(true);

    try {
      const result = await sendMessage(text, sessionId);
      setSessionId(result.session_id);
      setMessages((prev) => {
        const withoutPending = prev.filter((m) => !m.pending);
        return [...withoutPending, { role: "assistant", text: result.response }];
      });
    } catch (err) {
      // Show the actual classified failure (which source failed, whether
      // it's worth retrying) instead of a single generic message for
      // every possible error — a T-100 timeout, a rate-limited provider,
      // and a genuinely offline backend are now distinguishable to the
      // user.
      const displayText =
        err instanceof ChatApiError
          ? err.message
          : "Sorry — something went wrong processing that request.";
      setMessages((prev) => {
        const withoutPending = prev.filter((m) => !m.pending);
        return [...withoutPending, { role: "assistant", text: displayText }];
      });
    } finally {
      stopProgressCycle();
      setSending(false);
    }
  }

  useEffect(() => () => stopProgressCycle(), []);

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
