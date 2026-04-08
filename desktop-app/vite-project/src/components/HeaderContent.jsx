// HeaderContent.jsx - FIXED: Recalculates greeting when userName changes
import React, { useMemo } from "react";

const HEADLINES = [
  "What would you like done today?",
  "Ready when you are.",
  "What's on your mind?",
  "How can I help?",
  "Let's get something done.",
  "Your assistant is listening.",
];

const getTimeGreeting = (user) => {
  const hour = new Date().getHours();
  const name = user && user !== "User" ? `, ${user}` : "";
  if (hour >= 5  && hour < 12) return `Good morning${name}`;
  if (hour >= 12 && hour < 18) return `Good afternoon${name}`;
  return `Good evening${name}`;
};

const HeaderContent = ({ userName: propUserName, mode = "default", chatTitle = "New Chat" }) => {
  console.log("[HeaderContent] Received userName prop:", propUserName);
  
  // FIXED: Use useMemo with propUserName as dependency so it recalculates when userName changes
  const greeting = useMemo(() => getTimeGreeting(propUserName), [propUserName]);

  const headline = useMemo(() => {
    return HEADLINES[Math.floor(Math.random() * HEADLINES.length)];
  }, []);

  return (
    <header className="center-content" role="banner">
      <p className="greeting" aria-live="polite" aria-atomic="true">
        {greeting}
      </p>
      <h1 className="headline" id="main-headline" aria-live="polite" aria-atomic="true">
        {headline}
      </h1>
      {chatTitle && chatTitle !== "New Chat" && (
        <p
          className="chat-session-label"
          aria-label={`Current conversation: ${chatTitle}`}
        >
          <span aria-hidden="true" style={{ color: "var(--pink-400)", fontSize: "8px" }}>●</span>
          {chatTitle}
        </p>
      )}
    </header>
  );
};

export default HeaderContent;