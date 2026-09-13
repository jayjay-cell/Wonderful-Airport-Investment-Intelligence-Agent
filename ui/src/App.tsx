import { useEffect, useState } from "react";
import { checkHealth, sendMessageStreaming } from "./api";
import { ChatComposer } from "./components/ChatComposer";
import { ConversationView } from "./components/ConversationView";
import { WelcomeScreen } from "./components/WelcomeScreen";
import { Sidebar } from "./components/Sidebar";
import { Conversation, deriveTitle, formatMeta } from "./conversation";
import moreHorizontalIcon from "./assets/more-horizontal.svg";
import shareIcon from "./assets/share.svg";
import chevronDownIcon from "./assets/chevron-down.svg";

export default function App() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [liveStatus, setLiveStatus] = useState<string | null>(null);
  const [serverOnline, setServerOnline] = useState(true);

  useEffect(() => {
    checkHealth().then(setServerOnline);
  }, []);

  const activeConversation = conversations.find((c) => c.id === activeId) ?? null;
  const hasConversation = activeConversation !== null && activeConversation.messages.length > 0;
  // Only the very first launch (no conversations exist yet) gets the
  // standalone full-screen welcome panel. Once any conversation has ever
  // existed, "New analysis" stays inside the app frame (sidebar + topbar
  // visible) with the welcome content shown inline instead.
  const showStandaloneWelcome = !hasConversation && conversations.length === 0;

  function updateConversation(id: string, updater: (c: Conversation) => Conversation) {
    setConversations((prev) => prev.map((c) => (c.id === id ? updater(c) : c)));
  }

  async function handleSend(text: string) {
    // A brand-new conversation has no backend session_id yet -- use a
    // temporary client-side id so it can exist in the sidebar/UI state
    // immediately; it's replaced with the real session_id the moment the
    // backend's first "session" event arrives (see onSession below).
    const isNewConversation = activeConversation === null;
    const tempId = isNewConversation ? `local-${Date.now()}` : activeConversation!.id;

    if (isNewConversation) {
      const newConvo: Conversation = {
        id: tempId,
        title: deriveTitle(text),
        meta: formatMeta(1),
        messages: [{ role: "user", text }],
      };
      setConversations((prev) => [newConvo, ...prev]);
      setActiveId(tempId);
    } else {
      updateConversation(tempId, (c) => ({
        ...c,
        messages: [...c.messages, { role: "user", text }],
      }));
    }

    setSending(true);
    setLiveStatus("Thinking");

    let realId = tempId;
    const sessionIdForRequest = isNewConversation ? null : activeConversation!.id;

    await sendMessageStreaming(text, sessionIdForRequest, {
      onSession: (sessionId) => {
        if (isNewConversation && sessionId !== tempId) {
          realId = sessionId;
          setConversations((prev) =>
            prev.map((c) => (c.id === tempId ? { ...c, id: sessionId } : c))
          );
          setActiveId(sessionId);
        }
      },
      onStatus: (text) => setLiveStatus(text),
      onDone: (answerText) => {
        updateConversation(realId, (c) => ({
          ...c,
          messages: [...c.messages, { role: "assistant", text: answerText }],
          meta: formatMeta(c.messages.length + 1),
        }));
        setLiveStatus(null);
        setSending(false);
      },
      onError: (message) => {
        updateConversation(realId, (c) => ({
          ...c,
          messages: [...c.messages, { role: "assistant", text: message, isError: true }],
        }));
        setLiveStatus(null);
        setSending(false);
      },
    });
  }

  function handleNewConversation() {
    setActiveId(null);
  }

  function handleSelectConversation(id: string) {
    setActiveId(id);
  }

  return (
    <div className={`app-shell app-shell--${showStandaloneWelcome ? "welcome" : "conversation"}`}>
      {showStandaloneWelcome ? (
        <div className="welcome-panel">
          {!serverOnline && (
            <div className="offline-banner">
              Backend server is unreachable at /api — start the FastAPI server and refresh.
            </div>
          )}
          <WelcomeScreen onSelectSuggestion={handleSend} />
          <ChatComposer onSend={handleSend} disabled={sending} showLabel />
        </div>
      ) : (
        <div className="app-frame">
          <Sidebar
            conversations={conversations}
            activeConversationId={activeId}
            onSelectConversation={handleSelectConversation}
            onNewConversation={handleNewConversation}
          />
          <div className="main-panel">
            <div className="topbar">
              <div className="topbar-title-group">
                <p className="topbar-title">
                  {hasConversation ? activeConversation!.title : "New analysis"}
                </p>
                <div className="topbar-icon-btn topbar-icon-btn--tint">
                  <img src={chevronDownIcon} alt="" width={12} height={12} />
                </div>
              </div>
              <div className="topbar-actions">
                <button className="topbar-icon-btn" title="Share">
                  <img src={shareIcon} alt="" width={16} height={16} />
                </button>
                <button className="topbar-icon-btn" title="More">
                  <img src={moreHorizontalIcon} alt="" width={16} height={16} />
                </button>
              </div>
            </div>

            {!serverOnline && (
              <div className="offline-banner offline-banner--inline">
                Backend server is unreachable at /api — start the FastAPI server and refresh.
              </div>
            )}

            {hasConversation ? (
              <ConversationView messages={activeConversation!.messages} liveStatus={liveStatus} />
            ) : (
              <div className="conversation conversation--empty">
                <WelcomeScreen onSelectSuggestion={handleSend} showBrand={false} />
              </div>
            )}

            <div className="composer-zone">
              <div className="composer-zone-meta">
                <div className="composer-zone-badge">
                  <span className="composer-zone-badge-dot" />
                  <span>Aero Intel</span>
                  <img src={chevronDownIcon} alt="" width={14} height={14} />
                </div>
                <p className="composer-zone-disclaimer">
                  Aero Intel can make errors. Verify key data independently.
                </p>
              </div>
              <ChatComposer
                onSend={handleSend}
                disabled={sending}
                compact
                placeholder={
                  hasConversation ? "Ask a follow-up or start a new analysis..." : "Ask Aero Intel about airport demand, yields, or infrastructure moves..."
                }
              />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
