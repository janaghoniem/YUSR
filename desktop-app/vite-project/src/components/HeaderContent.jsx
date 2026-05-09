// HeaderContent.jsx — Professional voice app, dynamic but polished headlines
import React, { useState, useEffect, useCallback, useRef } from "react";

const HEADLINE_ROTATION_MS = 60 * 60 * 1000;

const srOnlyStyle = {
  position: "absolute",
  width: "1px",
  height: "1px",
  padding: 0,
  margin: "-1px",
  overflow: "hidden",
  clip: "rect(0, 0, 0, 0)",
  whiteSpace: "nowrap",
  border: 0,
};

// ========== Professional, varied headline pools ==========
// Morning (5:00 – 11:59)
const MORNING_HEADLINES = [
  "What would you like to achieve today?",
  "Let's plan a productive morning.",
  "Ready to begin? Your first priority is waiting.",
  "How can I help you get a head start?",
  "Today's opportunities start now. What's our focus?",
  "Your morning agenda — let's build it together.",
  "Setting the right direction early.",
  "What's the one thing that would make today successful?",
];

// Afternoon (12:00 – 17:59)
const AFTERNOON_HEADLINES = [
  "How is your day progressing?",
  "Let's maintain momentum.",
  "Need help refocusing for the afternoon?",
  "Checking in — what needs your attention next?",
  "Halfway there. What's the next logical step?",
  "Afternoon session ready. Where should we direct energy?",
  "Time to tackle your next task.",
  "How can I support you right now?",
];

// Evening (18:00 – 4:59)
const EVENING_HEADLINES = [
  "Time to wrap up and reflect.",
  "Let's review today's accomplishments.",
  "Preparing for tomorrow? I can help.",
  "Evening overview — what matters most?",
  "Close out the day with clarity.",
  "Shall we capture any final thoughts?",
  "Setting a calm, productive end to the day.",
  "Your evening assistant is ready.",
];

const getHeadlineArrayByHour = () => {
  const hour = new Date().getHours();
  if (hour >= 5 && hour < 12) return MORNING_HEADLINES;
  if (hour >= 12 && hour < 18) return AFTERNOON_HEADLINES;
  return EVENING_HEADLINES;
};

const randomItem = (arr) => arr[Math.floor(Math.random() * arr.length)];

const getFreshHeadline = () => {
  const arr = getHeadlineArrayByHour();
  return randomItem(arr);
};

// Professional time greeting (light icons only, optional)
const getTimeGreeting = (user = "") => {
  const hour = new Date().getHours();
  const hasUser = user && user.trim().toLowerCase() !== "user" && user.trim() !== "";
  const namePart = hasUser ? `, ${user}` : "";

  if (hour >= 5 && hour < 12) return `Good morning${namePart}`;
  if (hour >= 12 && hour < 18) return `Good afternoon${namePart}`;
  return `Good evening${namePart}`;
};

const HeaderContent = ({ userName: propUserName, mode = "default", chatTitle = "New Chat", onContentReady }) => {
  const [currentDate, setCurrentDate] = useState("");
  const [greeting, setGreeting] = useState("");
  const [headline, setHeadline] = useState("");
  const [announcement, setAnnouncement] = useState("");
  const intervalRef = useRef(null);
  const currentDateRef = useRef("");

  useEffect(() => {
    const options = { weekday: 'long', month: 'short', day: 'numeric' };
    const formattedDate = new Date().toLocaleDateString(undefined, options);
    currentDateRef.current = formattedDate;
    setCurrentDate(formattedDate);
  }, []);

  const updateDynamicContent = useCallback(() => {
    const newGreeting = getTimeGreeting(propUserName);
    const newHeadline = getFreshHeadline();
    const dateText = currentDateRef.current;
    setGreeting(newGreeting);
    setHeadline(newHeadline);
    setAnnouncement(`${newGreeting}. ${newHeadline}.`);

    if (onContentReady && dateText) {
      onContentReady({ greeting: newGreeting, headline: newHeadline, currentDate: dateText });
    }
  }, [propUserName, onContentReady]);

  useEffect(() => {
    updateDynamicContent();
    intervalRef.current = setInterval(() => {
      updateDynamicContent();
    }, HEADLINE_ROTATION_MS); // rotates hourly so the title does not churn too quickly

    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [updateDynamicContent]);

  return (
    <header className="center-content" role="banner" style={{ animation: "fadeUp var(--dur-slow) var(--ease-out)" }}>
      <p
        className="greeting"
        aria-live="polite"
        aria-atomic="true"
        style={{
          fontSize: "22px",
          color: "var(--text-muted)",
          display: "flex",
          alignItems: "center",
          gap: "8px",
          fontWeight: "500",
        }}
      >
        {greeting}
        {currentDate && (
          <span style={{ opacity: 0.5, fontSize: "16px" }}>• {currentDate}</span>
        )}
      </p>
      <h1
        className="headline"
        id="main-headline"
        style={{
          background: "linear-gradient(135deg, var(--text-primary) 0%, var(--pink-300) 100%)",
          WebkitBackgroundClip: "text",
          WebkitTextFillColor: "transparent",
          marginBottom: "12px",
          marginTop: "4px",
          transition: "opacity 0.2s ease",
        }}
      >
        {headline}
      </h1>
      <span aria-live="polite" aria-atomic="true" role="status" style={srOnlyStyle}>
        {announcement}
      </span>
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