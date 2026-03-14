import React from "react";

const languages = ["English", "العربية"];
const voices = ["Gacrux", "orpheus-english", "orpheus-arabic"];
const themes = ["dark", "light", "auto"];

const StepPreferences = ({ onNext, data, setData }) => {
  const toggle = (key, value) => {
    setData({ ...data, preferences: { ...data.preferences, [key]: value } });
  };

  return (
    <div className="onboarding-step">
      <h2 className="onboarding-title">Your preferences</h2>
      <p className="onboarding-subtitle">
        Customize AURA to feel right for you. You can always change these later in Settings.
      </p>

      <div className="pref-group">
        <label className="pref-label">Preferred language</label>
        <div className="pref-options">
          {languages.map((lang) => (
            <button
              key={lang}
              className={`pref-chip ${data.preferences.language === lang ? "selected" : ""}`}
              onClick={() => toggle("language", lang)}
            >
              {lang}
            </button>
          ))}
        </div>
      </div>

      <div className="pref-group">
        <label className="pref-label">Theme</label>
        <div className="pref-options">
          {themes.map((t) => (
            <button
              key={t}
              className={`pref-chip ${data.preferences.theme === t ? "selected" : ""}`}
              onClick={() => toggle("theme", t)}
            >
              {t.charAt(0).toUpperCase() + t.slice(1)}
            </button>
          ))}
        </div>
      </div>

      <div className="pref-group">
        <label className="pref-label">Voice</label>
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
        Continue →
      </button>
    </div>
  );
};

export default StepPreferences;