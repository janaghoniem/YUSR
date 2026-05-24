// ChatHistory.jsx — WhatsApp/Instagram style chat bubble viewer
import React, { useEffect, useRef } from "react";

const ChatHistory = ({ messages, onClose, chatTitle }) => {
  const bottomRef = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);


  // Strip JSON wrapper from assistant responses and <user_input> tags from user messages
  const cleanContent = (role, content) => {
    if (typeof content !== "string") return content;

    if (role === "user") {
      // Strip <user_input>...</user_input> security wrapper added by language_agent.py
      return content.replace(/<user_input>([\s\S]*?)<\/user_input>/g, "$1").replace(/<\/?user_input>/g, "").trim();
    }

    if (role === "assistant") {
      try {
        // Strip markdown code fences (```json ... ``` or ``` ... ```)
        const stripped = content.replace(/^```(?:json)?\s*/i, "").replace(/\s*```$/, "").trim();
        const parsed = JSON.parse(stripped);
        return (
          parsed.response_text ||
          parsed.text ||
          parsed.response ||
          content
        );
      } catch {
        return content;
      }
    }

    return content;
  };

  return (
    <div className="chat-history-overlay" onClick={onClose} role="presentation">
      <div
        className="chat-history-modal"
        role="dialog"
        aria-modal="true"
        aria-label={`Chat history for ${chatTitle || "Chat"}`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="chat-history-header">
          <div className="chat-history-avatar" aria-hidden="true">
            <img src="/aura_icon_colored.png" alt="" />
          </div>
          <div className="chat-history-header-info">
            <span className="chat-history-name">AURA</span>
            <span className="chat-history-subtitle">{chatTitle || "Chat"}</span>
          </div>
          <button className="chat-history-close" onClick={onClose} type="button" aria-label="Close chat history">✕</button>
        </div>

        <div className="chat-history-body">
          {messages.length === 0 ? (
            <div className="chat-history-empty">No messages in this chat yet.</div>
          ) : (
            messages.map((msg, idx) => {
              const isUser = msg.role === "user";
              const displayContent = cleanContent(msg.role, msg.content);
              const timestamp = msg.timestamp ? new Date(msg.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "";

              return (
                <div
                  key={idx}
                  className={`chat-bubble-row ${isUser ? "user-row" : "agent-row"}`}
                >
                  {!isUser && (
                    <div className="bubble-avatar agent-avatar">A</div>
                  )}
                  <div className={`chat-bubble-shell ${isUser ? "user-shell" : "agent-shell"}`}>
                    <div className={`chat-bubble ${isUser ? "user-bubble" : "agent-bubble"}`}>
                      <p className="bubble-text">{displayContent}</p>
                    </div>
                    {timestamp && <span className="bubble-time">{timestamp}</span>}
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