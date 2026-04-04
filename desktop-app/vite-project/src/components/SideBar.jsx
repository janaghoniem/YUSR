// SideBar.jsx
import React from "react";
import { Settings, Menu, X, SquarePen, MessageSquare } from "lucide-react";

const SideBar = ({ collapsed, onToggle, onSettingsClick, onNewChat, chats = [], onSwitchChat, currentSessionId }) => {
  return (
    <>
      <aside
        className={`sidebar ${collapsed ? "collapsed" : ""}`}
        role="navigation"
        aria-label="Main navigation"
        aria-expanded={!collapsed}
      >
        <div className="sidebar-top">
          <div className="logo-area">
            {!collapsed && (
              <span className="logo-wordmark" aria-hidden="true" style={{marginLeft: 10, fontSize: "1.2rem"}}>AURA</span>
            )}
          </div>
          <button
            className="toggle-btn"
            onClick={onToggle}
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            aria-expanded={!collapsed}
            aria-controls="sidebar-content"
          >
            {collapsed
              ? <Menu size={19} aria-hidden="true" />
              : <X size={19} aria-hidden="true" />}
          </button>
        </div>

        <div className="sidebar-middle" id="sidebar-content">
          <button
            className="new-chat-btn"
            onClick={onNewChat}
            aria-label="Start a new chat"
            title="New chat"
          >
            <SquarePen size={16} aria-hidden="true" />
            {!collapsed && <span>New chat</span>}
          </button>

          {!collapsed && chats.length > 0 && (
            <div className="chat-history" role="region" aria-label="Recent conversations">
              <p className="chat-history-label" id="chat-history-heading">Recent</p>
              <ul className="chat-list" role="list" aria-labelledby="chat-history-heading">
                {chats.slice(0, 20).map((chat, idx) => {
                  const sid = chat.session_id || chat.sessionId || chat.id || null;
                  const title = chat.title || chat.name || `Chat ${idx + 1}`;
                  const isActive = currentSessionId === sid;
                  if (!sid) {
                    return (
                      <li key={`invalid-${idx}`} className="chat-item disabled" aria-disabled="true" title="Unavailable">
                        <MessageSquare size={13} aria-hidden="true" style={{ flexShrink: 0, opacity: 0.4 }} />
                        <span className="chat-title">{title}</span>
                      </li>
                    );
                  }
                  return (
                    <li
                      key={sid}
                      className={`chat-item ${isActive ? "active" : ""}`}
                      onClick={() => onSwitchChat?.(sid, title)}
                      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSwitchChat?.(sid, title); } }}
                      role="button"
                      tabIndex={0}
                      aria-label={`Open conversation: ${title}`}
                      aria-current={isActive ? "page" : undefined}
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

        <div className="sidebar-bottom">
          <button
            className="sidebar-bottom-btn"
            onClick={onSettingsClick}
            aria-label="Open settings"
            title="Settings"
          >
            <Settings size={16} aria-hidden="true" />
            {!collapsed && <span>Settings</span>}
          </button>
        </div>
      </aside>

      {!collapsed && (
        <div
          className="sidebar-overlay"
          onClick={onToggle}
          role="button"
          tabIndex={0}
          aria-label="Close sidebar"
          onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onToggle(); } }}
        />
      )}
    </>
  );
};

export default SideBar;