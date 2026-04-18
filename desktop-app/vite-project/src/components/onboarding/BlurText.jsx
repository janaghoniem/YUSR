// BlurText.jsx — ReactBits-style blur-in text animation
// Each word animates from blur+opacity=0 to sharp+opacity=1 with a stagger.
// Drop-in replacement: <BlurText text="Hello world" delay={80} />

import React, { useMemo } from "react";

const BlurText = ({
  text = "",
  delay = 80,          // ms stagger between words
  duration = 500,      // ms per word transition
  className = "",
  as: Tag = "span",
}) => {
  const words = useMemo(() => text.split(" "), [text]);

  return (
    <Tag
      className={className}
      aria-label={text}
      style={{ display: "inline", lineHeight: "inherit" }}
    >
      {words.map((word, i) => (
        <span
          key={i}
          aria-hidden="true"
          style={{
            display: "inline-block",
            marginRight: "0.25em",
            opacity: 0,
            filter: "blur(8px)",
            animation: `blurTextIn ${duration}ms ease forwards`,
            animationDelay: `${i * delay}ms`,
          }}
        >
          {word}
        </span>
      ))}
      <style>{`
        @keyframes blurTextIn {
          0%   { opacity: 0; filter: blur(8px);  transform: translateY(4px); }
          100% { opacity: 1; filter: blur(0px); transform: translateY(0); }
        }
      `}</style>
    </Tag>
  );
};

export default BlurText;