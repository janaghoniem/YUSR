// StepIntro.jsx — Fixed:
//  • BlurText now imported from local BlurText.jsx (no missing module)
//  • TTS reads intro lines using the singleton screenReader
//  • Lines animate correctly with BlurText stagger

import React, { useEffect, useState } from "react";
import BlurText from "./BlurText";
import ShinyText from "./ShinyText";
import screenReader from "../../utils/ScreenReader";

const lines = [
  "Hi there. I'm AURA.",
  "Your intelligent assistant — built to help you think, create, and act.",
  "I remember your preferences, learn your habits, and get smarter over time.",
  "Let's set things up together. It only takes a minute.",
];

const StepIntro = ({ onNext, lang = "en" }) => {
  const [visibleLines, setVisibleLines] = useState(0);
  const [done, setDone] = useState(false);

  // Arabic translations for the intro lines
  const arLines = [
    "أهلاً. أنا أورا.",
    "مساعدك الذكي — مصمم لمساعدتك على التفكير والإبداع والعمل.",
    "أتذكر تفضيلاتك وأتعلم من عاداتك وأتحسن مع الوقت.",
    "خلينا نبدأ الإعداد. هياخد دقيقة بس.",
  ];

  const displayLines = lang === "ar" ? arLines : lines;

  useEffect(() => {
    // Speak all lines via TTS
    const textToSpeak = displayLines.join(" ");
    screenReader.stop();
    screenReader.speak(textToSpeak);

    return () => screenReader.stop();
  }, [lang]);

  // Stagger lines appearing on screen
  useEffect(() => {
    if (visibleLines < displayLines.length) {
      const timer = setTimeout(() => setVisibleLines((v) => v + 1), 1800);
      return () => clearTimeout(timer);
    } else {
      const timer = setTimeout(() => setDone(true), 800);
      return () => clearTimeout(timer);
    }
  }, [visibleLines, displayLines.length]);

  const btnLabel = lang === "ar" ? "هيبدأ →" : "Let's get started →";

  return (
    <div className="onboarding-step step-intro">
      <div className="intro-lines">
        {displayLines.slice(0, visibleLines).map((line, i) => (
          <p key={i} className="intro-line" aria-live="polite">
            <BlurText text={line} delay={60} />
          </p>
        ))}
      </div>
      {done && (
        <button
          className="onboarding-btn primary"
          onClick={onNext}
          aria-label={lang === "ar" ? "ابدأ الإعداد" : "Start setup"}
        >
          <ShinyText text={btnLabel} speed={3} />
        </button>
      )}
    </div>
  );
};

export default StepIntro;