// Conversation sidebar: brand mark, new-analysis button, a "Recent" list of
// past conversations, and a user footer pinned to the bottom.

import planeIcon from "../assets/plane.svg";
import plusIcon from "../assets/plus.svg";
import moreHorizontalIcon from "../assets/more-horizontal.svg";
import type { Conversation } from "../conversation";

interface SidebarProps {
  conversations: Conversation[];
  activeConversationId: string | null;
  onSelectConversation: (id: string) => void;
  onNewConversation: () => void;
}

export function Sidebar({
  conversations,
  activeConversationId,
  onSelectConversation,
  onNewConversation,
}: SidebarProps) {
  return (
    <aside className="sidebar">
      <div className="sidebar-brand">
        <div className="sidebar-brand-mark">
          <img src={planeIcon} alt="" width={16} height={16} />
        </div>
        <div className="sidebar-brand-copy">
          <p className="sidebar-brand-name">Aero Intel</p>
          <p className="sidebar-brand-tagline">Airport intelligence</p>
        </div>
      </div>

      <button className="sidebar-new-btn" onClick={onNewConversation}>
        <img src={plusIcon} alt="" width={16} height={16} />
        <span>New analysis</span>
      </button>

      {conversations.length > 0 && (
        <div className="sidebar-recent">
          <p className="sidebar-recent-label">Recent</p>
          <div className="sidebar-convo-list">
            {conversations.map((c) => (
              <button
                key={c.id}
                className={`sidebar-convo-item${c.id === activeConversationId ? " active" : ""}`}
                onClick={() => onSelectConversation(c.id)}
              >
                <p className="sidebar-convo-title">{c.title}</p>
                <p className="sidebar-convo-meta">{c.meta}</p>
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="sidebar-spacer" />

      <div className="sidebar-footer">
        <div className="sidebar-avatar">A</div>
        <div className="sidebar-footer-copy">
          <p className="sidebar-footer-name">Analyst</p>
          <p className="sidebar-footer-role">Investment research</p>
        </div>
        <img src={moreHorizontalIcon} alt="" width={16} height={16} />
      </div>
    </aside>
  );
}
