import React, { useState } from "react";
import VoiceInput from "../shared/VoiceInput";

const StepUserIntro = ({ onNext, data, setData }) => {
  const [error, setError] = useState("");

  const handleNext = () => {
    if (!data.introduction.trim() || data.introduction.trim().length < 10) {
      setError("Please tell me a bit more about yourself (at least 10 characters).");
      return;
    }
    setError("");
    onNext();
  };

  return (
    <div className="onboarding-step">
      <h2 className="onboarding-title">Tell me about yourself</h2>
      <p className="onboarding-subtitle">
        What should I know about you? Your job, interests, how you like to work?
      </p>

      <VoiceInput
        label="Your introduction"
        value={data.introduction}
        onChange={(val) => setData({ ...data, introduction: val })}
        placeholder="e.g. I'm a designer who loves automation and Arabic music..."
        type="textarea"
      />

      {/* Override to textarea for this step */}
      <textarea
        className="onboarding-textarea"
        value={data.introduction}
        onChange={(e) => setData({ ...data, introduction: e.target.value })}
        placeholder="e.g. I'm a designer who loves automation and Arabic music..."
        rows={4}
      />
      <p className="voice-hint">💡 You can also speak your introduction using the mic above</p>

      {error && <p className="onboarding-error">{error}</p>}

      <button className="onboarding-btn primary" onClick={handleNext}>
        Continue →
      </button>
    </div>
  );
};

export default StepUserIntro;