import React, { useEffect, useState } from "react";
import CinematicIntro from "../CinematicIntro";
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
    // Speak first line on mount
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(lines.join(" "));
    utterance.lang = "en-US";
    window.speechSynthesis.speak(utterance);

    return () => window.speechSynthesis.cancel();
  }, []);

  useEffect(() => {
    if (visibleLines < lines.length) {
      const timer = setTimeout(() => setVisibleLines((v) => v + 1), 2000); // 2s per line
      return () => clearTimeout(timer);
    } else {
      const timer = setTimeout(() => setDone(true), 1000);
      return () => clearTimeout(timer);
    }
  }, [visibleLines]);

  return (
    <div className="onboarding-step step-intro">
      <div className="intro-orb" style={{ width: 250, height: 250, margin: '0 auto', display: 'flex', justifyContent: 'center', alignItems: 'center' }}>
        <CinematicIntro width="100%" height="100%" />
      </div>
      <div className="intro-lines">
        {lines.slice(0, visibleLines).map((line, i) => (
          <p key={i} className={`intro-line ${i === visibleLines - 1 ? "fade-in" : ""}`} aria-live="polite">
            {line}
          </p>
        ))}
      </div>
      {done && (
        <button className="onboarding-btn primary" onClick={onNext} aria-label="Start setup">
          Let's get started →
        </button>
      )}
    </div>
  );
};

export default StepIntro;