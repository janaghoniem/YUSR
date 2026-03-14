// SideBar.jsx
import React from "react";
import { Settings, Menu, X, SquarePen } from "lucide-react";

const SideBar = ({ collapsed, onToggle, onSettingsClick, onNewChat, chats = [], onSwitchChat, currentSessionId }) => {
  return (
    <>
      <aside className={`sidebar ${collapsed ? "collapsed" : ""}`} role="navigation" aria-label="Main sidebar">
        
        {/* TOP ZONE */}
        <div className="sidebar-top">
          {!collapsed && <span className="logo">AURA</span>}
          <button className="toggle-btn" onClick={onToggle} aria-label="Toggle sidebar" aria-expanded={!collapsed}>
            {collapsed ? <Menu size={22} /> : <X size={22} />}
          </button>
        </div>

        {/* MIDDLE ZONE */}
        <div className="sidebar-middle">
          <button className="new-chat-btn" onClick={onNewChat} aria-label="Start a new chat">
            <SquarePen size={18} />
            {!collapsed && <span>New chat</span>}
          </button>

          {/* CHAT HISTORY */}
          {!collapsed && chats.length > 0 && (
            <div className="chat-history">
              <div className="chat-history-label">Recent chats</div>
              <ul className="chat-list">
                {chats.slice(0, 10).map((chat, idx) => {
                  const sid = chat.session_id || chat.sessionId || chat.id || null;
                  const title = chat.title || chat.name || `Chat ${idx + 1}`;

                  if (!sid) {
                    return (
                      <li key={`invalid-${idx}`} className="chat-item disabled" title="Invalid chat">
                        <span className="chat-title">{title}</span>
                      </li>
                    );
                  }

                  return (
                    <li
                      key={sid}
                      className={`chat-item ${currentSessionId === sid ? "active" : ""}`}
                      onClick={() => onSwitchChat && onSwitchChat(sid, title)}
                      onKeyDown={(e) => { if (e.key === 'Enter') onSwitchChat && onSwitchChat(sid, title); }}
                      role="button"
                      tabIndex={0}
                      title={title}
                    >
                      <span className="chat-title">{title}</span>
                    </li>
                  );
                })}
              </ul>
            </div>
          )}
        </div>

        {/* BOTTOM ZONE */}
        <button className="sidebar-bottom" onClick={onSettingsClick} aria-label="Open settings">
          <Settings size={18} />
          {!collapsed && <span>Settings</span>}
        </button>
      </aside>

      {collapsed && <div className="sidebar-overlay" onClick={onToggle} role="button" aria-label="Open sidebar overlay"></div>}
    </>
  );
};

export default SideBar;
