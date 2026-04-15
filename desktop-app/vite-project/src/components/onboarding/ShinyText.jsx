// ShinyText.jsx — Animated shimmer text component
// Usage: <ShinyText text="Click me →" speed={3} />

import React from "react";

const ShinyText = ({ text = "", speed = 3, disabled = false, className = "" }) => {
  if (disabled) return <span className={className}>{text}</span>;

  const animDuration = `${speed}s`;

  return (
    <span
      className={className}
      style={{
        display: "inline-block",
        background: `linear-gradient(
          120deg,
          rgba(255,255,255,0.55) 0%,
          rgba(255,255,255,0.95) 40%,
          rgba(255,180,220,0.9) 50%,
          rgba(255,255,255,0.95) 60%,
          rgba(255,255,255,0.55) 100%
        )`,
        backgroundSize: "200% auto",
        WebkitBackgroundClip: "text",
        WebkitTextFillColor: "transparent",
        backgroundClip: "text",
        animation: `shinyTextSlide ${animDuration} linear infinite`,
      }}
    >
      {text}
      <style>{`
        @keyframes shinyTextSlide {
          0%   { background-position: 200% center; }
          100% { background-position: -200% center; }
        }
      `}</style>
    </span>
  );
};

export default ShinyText;