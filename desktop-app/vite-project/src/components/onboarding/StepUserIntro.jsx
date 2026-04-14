// StepUserIntro.jsx — Fixed: only one text input (VoiceInput), no duplicate textarea

import React, { useState, useEffect } from "react";
import VoiceInput from "../shared/VoiceInput";
import BlurText from "./BlurText";
import ShinyText from "./ShinyText";
import screenReader from "../../utils/ScreenReader";

const StepUserIntro = ({ onNext, data, setData, lang = "en" }) => {
  const [error, setError] = useState("");
  const isAr = lang === "ar";

  const textEn = "Tell me about yourself. What should I know about you? Your job, interests, how you like to work?";
  const textAr = "قولي عن نفسك. إيه اللي المفروض أعرفه عنك؟ شغلتك، اهتماماتك، بتحب تشتغل إزاي؟";

  useEffect(() => {
    screenReader.stop();
    screenReader.speak(isAr ? textAr : textEn);
    return () => screenReader.stop();
  }, [isAr]);

  const handleNext = () => {
    const val = (data.introduction || "").trim();
    if (!val || val.length < 10) {
      const errEn = "Please tell me a bit more about yourself (at least 10 characters).";
      const errAr = "من فضلك قولي أكتر عن نفسك (على الأقل ١٠ حروف).";
      setError(isAr ? errAr : errEn);
      return;
    }
    setError("");
    onNext();
  };

  return (
    <div className="onboarding-step">
      <h2 className="onboarding-title">
        <BlurText text={isAr ? "قولي عن نفسك" : "Tell me about yourself"} delay={50} />
      </h2>
      <p className="onboarding-subtitle">
        {isAr
          ? "إيه اللي المفروض أعرفه عنك؟ شغلتك، اهتماماتك، بتحب تشتغل إزاي؟"
          : "What should I know about you? Your job, interests, how you like to work?"}
      </p>

      <VoiceInput
        label={isAr ? "تعريفك" : "Your introduction"}
        value={data.introduction}
        onChange={(val) => setData({ ...data, introduction: val })}
        placeholder={
          isAr
            ? "مثلاً: أنا مصمم بحب الأتوماتيك والموسيقى..."
            : "e.g. I'm a designer who loves automation and Arabic music..."
        }
        type="textarea"
        rows="4"
        lang={lang}
      />

      <p className="voice-hint">
        {isAr
          ? "💡 ممكن تقول تعريفك بالميكروفون"
          : "💡 You can also speak your introduction using the mic above"}
      </p>

      {error && <p className="onboarding-error" role="alert">{error}</p>}

      <button className="onboarding-btn primary" onClick={handleNext}>
        <ShinyText text={isAr ? "كمل →" : "Continue →"} speed={3} />
      </button>
    </div>
  );
};

export default StepUserIntro;