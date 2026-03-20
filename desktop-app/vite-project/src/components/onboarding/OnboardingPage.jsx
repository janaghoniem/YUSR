import React, { useState } from "react";
import StepIntro from "./StepIntro";
import StepUserIntro from "./StepUserIntro";
import StepPreferences from "./StepPreferences";
import StepCreateAccount from "./StepCreateAccount";

const TOTAL_STEPS = 4;

const OnboardingPage = ({ userId, onComplete }) => {
  const [step, setStep] = useState(0);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState("");

  const [formData, setFormData] = useState({
    introduction: "",
    username: "",
    password: "",
    confirmPassword: "",
    preferences: {
      language: "English",
      theme: "dark",
      voice: "Gacrux",
    },
  });

  const next = () => setStep((s) => Math.min(s + 1, TOTAL_STEPS - 1));

  const handleFinalSubmit = async () => {
    setIsSubmitting(true);
    setError("");
    console.log("Submitting with userId:", userId, "| formData:", formData);

    try {
      const payload = {
        user_id: userId,
        username: formData.username,
        password: formData.password,
        introduction: formData.introduction,
        preferences: formData.preferences,
      };

      const res = await fetch("http://localhost:8000/onboarding/create-account", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (!res.ok) {
        const data = await res.json();
        console.log("Backend error response:", data); // temporary, helps debug
        const errorMsg = typeof data.detail === "string"
          ? data.detail
          : Array.isArray(data.detail)
            ? data.detail.map(e => e.msg).join(", ")
            : "Account creation failed";
        throw new Error(errorMsg);
      }

      // Persist onboarding state so we never show it again
      localStorage.setItem("onboardingComplete", "true");
      localStorage.setItem("userName", formData.username);
      localStorage.setItem("ttsVoice", formData.preferences.voice);

      // Tell App.jsx we're done
      onComplete({
        username: formData.username,
        preferences: formData.preferences,
      });
    } catch (err) {
      setError(err.message || "Something went wrong. Please try again.");
    } finally {
      setIsSubmitting(false);
    }
  };

  const progressPct = ((step) / (TOTAL_STEPS - 1)) * 100;

  return (
    <div className="onboarding-overlay">
      <div className="onboarding-container">
        {/* Progress bar */}
        <div className="onboarding-progress-bar">
          <div
            className="onboarding-progress-fill"
            style={{ width: `${progressPct}%` }}
          />
        </div>

        {/* Step counter */}
        {step > 0 && (
          <p className="onboarding-step-counter">
            Step {step} of {TOTAL_STEPS - 1}
          </p>
        )}

        {/* Steps */}
        {step === 0 && <StepIntro onNext={next} />}
        {step === 1 && (
          <StepUserIntro onNext={next} data={formData} setData={setFormData} />
        )}
        {step === 2 && (
          <StepPreferences onNext={next} data={formData} setData={setFormData} />
        )}
        {step === 3 && (
          <StepCreateAccount
            onSubmit={handleFinalSubmit}
            data={formData}
            setData={setFormData}
            isSubmitting={isSubmitting}
          />
        )}

        {error && <p className="onboarding-error global-error">{error}</p>}

        {/* Back button */}
        {step > 0 && !isSubmitting && (
          <button
            className="onboarding-btn ghost back-btn"
            onClick={() => setStep((s) => s - 1)}
          >
            ← Back
          </button>
        )}
      </div>
    </div>
  );
};

export default OnboardingPage;