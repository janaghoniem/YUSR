import React, { useEffect, useState } from "react";

const lines = [
  "Hi there. I'm AURA.",
  "Your intelligent assistant — built to help you think, create, and act.",
  "I remember your preferences, learn your habits, and get smarter over time.",
  "Let's set things up together. It only takes a minute.",
];

const StepIntro = ({ onNext }) => {
  const [visibleLines, setVisibleLines] = useState(0);
  const [done, setDone] = useState(false);

  useEffect(() => {
    if (visibleLines < lines.length) {
      const timer = setTimeout(() => setVisibleLines((v) => v + 1), 900);
      return () => clearTimeout(timer);
    } else {
      const timer = setTimeout(() => setDone(true), 400);
      return () => clearTimeout(timer);
    }
  }, [visibleLines]);

  return (
    <div className="onboarding-step step-intro">
      <div className="intro-orb" />
      <div className="intro-lines">
        {lines.slice(0, visibleLines).map((line, i) => (
          <p key={i} className={`intro-line ${i === visibleLines - 1 ? "fade-in" : ""}`}>
            {line}
          </p>
        ))}
      </div>
      {done && (
        <button className="onboarding-btn primary" onClick={onNext}>
          Let's get started →
        </button>
      )}
    </div>
  );
};

export default StepIntro;