// ChatHistory.jsx — WhatsApp/Instagram style chat bubble viewer
import React, { useEffect, useRef } from "react";

const ChatHistory = ({ messages, onClose, chatTitle }) => {
  const bottomRef = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // Strip JSON wrapper from language agent responses
  const cleanContent = (role, content) => {
    if (role !== "assistant" || typeof content !== "string") return content;
    try {
      const parsed = JSON.parse(content);
      return (
        parsed.response_text ||
        parsed.text ||
        parsed.response ||
        content
      );
    } catch {
      return content;
    }
  };

  return (
    <div className="chat-history-overlay" onClick={onClose}>
      <div
        className="chat-history-modal"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="chat-history-header">
          <div className="chat-history-avatar">A</div>
          <div className="chat-history-header-info">
            <span className="chat-history-name">AURA</span>
            <span className="chat-history-subtitle">{chatTitle || "Chat"}</span>
          </div>
          <button className="chat-history-close" onClick={onClose}>✕</button>
        </div>

        {/* Messages */}
        <div className="chat-history-body">
          {messages.length === 0 ? (
            <div className="chat-history-empty">No messages in this chat yet.</div>
          ) : (
            messages.map((msg, idx) => {
              const isUser = msg.role === "user";
              const displayContent = cleanContent(msg.role, msg.content);

              return (
                <div
                  key={idx}
                  className={`chat-bubble-row ${isUser ? "user-row" : "agent-row"}`}
                >
                  {!isUser && (
                    <div className="bubble-avatar agent-avatar">A</div>
                  )}
                  <div className={`chat-bubble ${isUser ? "user-bubble" : "agent-bubble"}`}>
                    <p className="bubble-text">{displayContent}</p>
                  </div>
                  {isUser && (
                    <div className="bubble-avatar user-avatar">Me</div>
                  )}
                </div>
              );
            })
          )}
          <div ref={bottomRef} />
        </div>
      </div>
    </div>
  );
};

export default ChatHistory;