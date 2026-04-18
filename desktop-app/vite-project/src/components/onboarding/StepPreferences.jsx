// StepPreferences.jsx — Fixed:
//  • Language-aware TTS
//  • When user picks Arabic, immediately calls screenReader.setLanguage("ar")
//    so all subsequent pages speak in Egyptian Arabic (req 5)

import React, { useEffect } from "react";
import BlurText from "./BlurText";
import ShinyText from "./ShinyText";
import screenReader from "../../utils/ScreenReader";

const languages = ["English", "العربية"];
const voices    = ["Gacrux", "orpheus-english", "orpheus-arabic"];
const themes    = ["dark", "light", "auto"];

const StepPreferences = ({ onNext, data, setData, lang = "en" }) => {
  const isAr = lang === "ar" || data?.preferences?.language === "العربية";

  useEffect(() => {
    const textEn = "Your preferences! Customize AURA to feel right for you. You can always change these later in Settings.";
    const textAr = "تفضيلاتك! خلي أورا تحس إنها ليك. تقدر تغير دي بعدين في الإعدادات.";
    screenReader.stop();
    screenReader.speak(isAr ? textAr : textEn);
    return () => screenReader.stop();
  }, [isAr]);

  const toggle = (key, value) => {
    const updated = { ...data, preferences: { ...data.preferences, [key]: value } };
    setData(updated);

    // Req 5: immediately update TTS language when user picks a language
    if (key === "language") {
      const newLang = value === "العربية" ? "ar" : "en";
      screenReader.setLanguage(newLang);
    }
  };

  const labelMap = isAr
    ? { lang: "اللغة المفضلة", theme: "المظهر", voice: "الصوت", continue: "كمل →" }
    : { lang: "Preferred language", theme: "Theme", voice: "Voice", continue: "Continue →" };

  const themeLabels = isAr
    ? { dark: "داكن", light: "فاتح", auto: "تلقائي" }
    : { dark: "Dark", light: "Light", auto: "Auto" };

  return (
    <div className="onboarding-step">
      <h2 className="onboarding-title">
        <BlurText text={isAr ? "تفضيلاتك" : "Your preferences"} delay={50} />
      </h2>
      <p className="onboarding-subtitle">
        {isAr
          ? "خلي أورا تحس إنها ليك. تقدر تغير دي بعدين في الإعدادات."
          : "Customize AURA to feel right for you. You can always change these later in Settings."}
      </p>

      <div className="pref-group">
        <label className="pref-label">{labelMap.lang}</label>
        <div className="pref-options">
          {languages.map((l) => (
            <button
              key={l}
              className={`pref-chip ${data.preferences.language === l ? "selected" : ""}`}
              onClick={() => toggle("language", l)}
            >
              {l}
            </button>
          ))}
        </div>
      </div>

      <div className="pref-group">
        <label className="pref-label">{labelMap.theme}</label>
        <div className="pref-options">
          {themes.map((t) => (
            <button
              key={t}
              className={`pref-chip ${data.preferences.theme === t ? "selected" : ""}`}
              onClick={() => toggle("theme", t)}
            >
              {themeLabels[t]}
            </button>
          ))}
        </div>
      </div>

      <div className="pref-group">
        <label className="pref-label">{labelMap.voice}</label>
        <div className="pref-options">
          {voices.map((v) => (
            <button
              key={v}
              className={`pref-chip ${data.preferences.voice === v ? "selected" : ""}`}
              onClick={() => toggle("voice", v)}
            >
              {v}
            </button>
          ))}
        </div>
      </div>

      <button className="onboarding-btn primary" onClick={onNext}>
        <ShinyText text={labelMap.continue} speed={3} />
      </button>
    </div>
  );
};

export default StepPreferences;