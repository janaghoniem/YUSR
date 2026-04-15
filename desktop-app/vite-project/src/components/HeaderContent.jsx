// HeaderContent.jsx — Centered, futuristic, Syne font with gradient headline
import React, { useMemo, useState, useEffect } from "react";

const MORNING_HEADLINES = [
  "What would you like to achieve today?",
  "Let's get a head start.",
  "Ready to tackle the day?",
  "How can I assist your morning?",
];

const AFTERNOON_HEADLINES = [
  "How is your day going?",
  "Let's keep the momentum going.",
  "Need help with anything this afternoon?",
  "Ready for your next task?",
];

const EVENING_HEADLINES = [
  "Winding down?",
  "Let's wrap up for the day.",
  "Reflecting on today?",
  "Your assistant is ready for the evening.",
];

const getContextualHeadline = () => {
  const hour = new Date().getHours();
  let headlines;
  if (hour >= 5 && hour < 12) headlines = MORNING_HEADLINES;
  else if (hour >= 12 && hour < 18) headlines = AFTERNOON_HEADLINES;
  else headlines = EVENING_HEADLINES;
  
  return headlines[Math.floor(Math.random() * headlines.length)];
};

const getTimeGreeting = (user) => {
  const hour = new Date().getHours();
  // Don't append default user name to make it more natural
  const hasUser = user && user.trim().toLowerCase() !== "user";
  const namePart = hasUser ? `, ${user}` : "";
  
  if (hour >= 5  && hour < 12) return `Good morning${namePart} 🌅`;
  if (hour >= 12 && hour < 18) return `Good afternoon${namePart} ☀️`;
  return `Good evening${namePart} 🌙`;
};

const HeaderContent = ({ userName: propUserName, mode = "default", chatTitle = "New Chat", onContentReady }) => {
  const [currentDate, setCurrentDate] = useState("");

  useEffect(() => {
    // Add current date nicely formatted
    const options = { weekday: 'long', month: 'short', day: 'numeric' };
    setCurrentDate(new Date().toLocaleDateString(undefined, options));
  }, []);

  const greeting = useMemo(() => getTimeGreeting(propUserName), [propUserName]);
  const headline = useMemo(() => getContextualHeadline(), []);

  useEffect(() => {
    if (!onContentReady || !currentDate) return;
    onContentReady({ greeting, headline, currentDate });
  }, [onContentReady, greeting, headline, currentDate]);

  return (
    <header className="center-content" role="banner" style={{ animation: "fadeUp var(--dur-slow) var(--ease-out)" }}>
      <p className="greeting" aria-live="polite" aria-atomic="true" style={{ fontSize: "22px", color: "var(--text-muted)", display: "flex", alignItems: "center", gap: "8px", fontWeight: "500" }}>
        {greeting}
        {currentDate && <span style={{ opacity: 0.5, fontSize: "16px" }}>• {currentDate}</span>}
      </p>
      <h1 
        className="headline" 
        id="main-headline" 
        aria-live="polite" 
        aria-atomic="true"
        style={{
          background: "linear-gradient(135deg, var(--text-primary) 0%, var(--pink-300) 100%)",
          WebkitBackgroundClip: "text",
          WebkitTextFillColor: "transparent",
          marginBottom: "12px",
          marginTop: "4px"
        }}
      >
        {headline}
      </h1>
      {chatTitle && chatTitle !== "New Chat" && (
        <p
          className="chat-session-label"
          aria-label={`Current conversation: ${chatTitle}`}
          style={{ fontSize: "15px" }}
        >
          <span aria-hidden="true" style={{ color: "var(--pink-400)", fontSize: "10px" }}>●</span>
          {chatTitle}
        </p>
      )}
    </header>
  );
};

export default HeaderContent;