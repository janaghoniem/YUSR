// OnboardingPage.jsx - Fully Fixed with Proper User ID Management
import React, { useState, useEffect } from "react";
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
    email: "",
    password: "",
    confirmPassword: "",
    preferences: {
      language: "English",
      theme: "dark",
      voice: "Gacrux",
    },
  });

  // CRITICAL: Ensure the correct userId is stored in localStorage
  useEffect(() => {
    console.log("[OnboardingPage] Current userId from props:", userId);
    
    // Check if we have a valid userId
    if (!userId || userId === "undefined" || userId === "null") {
      console.error("[OnboardingPage] Invalid userId received:", userId);
      // Generate a new userId if invalid
      const newUserId = `user_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
      console.log("[OnboardingPage] Generated new userId:", newUserId);
      localStorage.setItem("userId", newUserId);
    } else {
      // Sync localStorage with the userId from props
      const storedUserId = localStorage.getItem("userId");
      if (storedUserId !== userId) {
        console.log("[OnboardingPage] Syncing localStorage userId from", storedUserId, "to", userId);
        localStorage.setItem("userId", userId);
      }
    }
    
    // Log the current state for debugging
    console.log("[OnboardingPage] Form data:", {
      username: formData.username,
      preferences: formData.preferences
    });
  }, [userId, formData.username, formData.preferences]);

  const next = () => setStep((s) => Math.min(s + 1, TOTAL_STEPS - 1));

  const handleFinalSubmit = async () => {
    setIsSubmitting(true);
    setError("");

    try {
      // Get the current userId from localStorage (should be synced)
      const currentUserId = localStorage.getItem("userId");
      
      if (!currentUserId) {
        throw new Error("User ID not found. Please restart onboarding.");
      }
      
      console.log("[OnboardingPage] Submitting account creation for:", {
        user_id: currentUserId,
        username: formData.username,
        has_password: !!formData.password
      });
      
      const payload = {
        user_id: currentUserId,
        username: formData.username,
        email: formData.email || "",
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
        throw new Error(data.detail || "Account creation failed");
      }

      console.log("[OnboardingPage] Account created successfully for:", formData.username);
      
      // Persist onboarding state
      localStorage.setItem("onboardingComplete", "true");
      localStorage.setItem("userName", formData.username);
      localStorage.setItem("ttsVoice", formData.preferences.voice);
      
      // Ensure userId is still correct
      const finalUserId = localStorage.getItem("userId");
      console.log("[OnboardingPage] Final userId after account creation:", finalUserId);

      // Call the onComplete callback to transition to main app
      onComplete({
        userId: finalUserId,
        username: formData.username,
        preferences: formData.preferences,
      });
      
    } catch (err) {
      console.error("[OnboardingPage] Account creation error:", err);
      setError(err.message || "Something went wrong. Please try again.");
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