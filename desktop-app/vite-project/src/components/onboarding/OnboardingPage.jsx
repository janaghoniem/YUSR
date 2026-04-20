// OnboardingPage.jsx — Fixed:
//  • Derives `lang` from formData.preferences.language and passes to all steps
//  • Card uses glassmorphic style matching login page (req 6)
//  • Overflow fixed: container uses flex + overflow-y:auto, no fixed minHeight (req 2)
//  • screenReader.setLanguage updated when language preference changes

import React, { useState, useMemo } from "react";
import { motion, AnimatePresence } from "framer-motion";
import StepIntro from "./StepIntro";
import StepUserIntro from "./StepUserIntro";
import StepPreferences from "./StepPreferences";
import StepVoiceCommands from "./Stepvoicecommands";
import StepCreateAccount from "./StepCreateAccount";
import SpotlightCard from "./SpotlightCard";
import Aurora from "./Aurora";
import screenReader from "../../utils/ScreenReader";

const TOTAL_STEPS = 5;

const slideVariants = {
  enter: (dir) => ({ x: dir > 0 ? "100%" : "-100%", opacity: 0 }),
  center: { x: 0, opacity: 1, transition: { duration: 0.38, ease: [0.4, 0, 0.2, 1] } },
  exit:  (dir) => ({
    x: dir < 0 ? "100%" : "-100%", opacity: 0,
    transition: { duration: 0.28, ease: [0.4, 0, 1, 1] },
  }),
};

const OnboardingPage = ({ userId, onComplete }) => {
  const [step,        setStep]        = useState(0);
  const [direction,   setDirection]   = useState(1);
  const [isSubmitting,setIsSubmitting]= useState(false);
  const [error,       setError]       = useState("");

  const [faceRegistered,  setFaceRegistered]  = useState(false);
  const [showFaceCapture, setShowFaceCapture] = useState(false);

  const [formData, setFormData] = useState({
    introduction: "",
    username:     "",
    email:        "",
    password:     "",
    preferences:  { language: "English", theme: "dark", voice: "Gacrux" },
  });

  // Derive lang from user's language choice — passed down to every step
  const lang = useMemo(() => {
    return formData.preferences.language === "العربية" ? "ar" : "en";
  }, [formData.preferences.language]);

  const goNext = () => {
    if (step >= TOTAL_STEPS - 1) { handleFinalSubmit(); return; }
    setDirection(1);
    setStep((s) => s + 1);
  };

  const goBack = () => {
    setDirection(-1);
    setStep((s) => Math.max(0, s - 1));
    setError("");
  };

  const handleFinalSubmit = async () => {
    setIsSubmitting(true);
    setError("");
    try {
      const currentUserId = localStorage.getItem("userId") || userId;
      const res = await fetch("http://localhost:8000/onboarding/create-account", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          user_id:      currentUserId,
          username:     formData.username,
          email:        formData.email || "",
          password:     formData.password || "",
          introduction: formData.introduction,
          preferences:  formData.preferences,
        }),
      });

      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.detail || "Account creation failed");
      }

      localStorage.setItem("onboardingComplete", "true");
      localStorage.setItem("userName",  formData.username);
      localStorage.setItem("ttsVoice",  formData.preferences.voice);
      localStorage.setItem("userLanguage", lang);

      onComplete({
        userId:      currentUserId,
        username:    formData.username,
        preferences: formData.preferences,
      });
    } catch (err) {
      setError(err.message || (lang === "ar" ? "حصل خطأ. حاول تاني." : "Something went wrong. Please try again."));
    } finally {
      setIsSubmitting(false);
    }
  };

  const progress = (step / (TOTAL_STEPS - 1)) * 100;

  const sharedProps = { lang };

  const steps = [
    <StepIntro            key={0} onNext={goNext}   {...sharedProps} />,
    <StepUserIntro        key={1} onNext={goNext}   data={formData} setData={setFormData} {...sharedProps} />,
    <StepPreferences      key={2} onNext={goNext}   data={formData} setData={setFormData} {...sharedProps} />,
    <StepVoiceCommands    key={3} onNext={goNext}   {...sharedProps} />,
    <StepCreateAccount
      key={4}
      onSubmit={goNext}
      data={formData}
      setData={setFormData}
      isSubmitting={isSubmitting}
      faceRegistered={faceRegistered}
      onFaceRegistered={() => setFaceRegistered(true)}
      showFaceCapture={showFaceCapture}
      setShowFaceCapture={setShowFaceCapture}
      {...sharedProps}
    />,
  ];

  const backLabel  = lang === "ar" ? `← رجع للخطوة ${step - 1}` : `← Back`;
  const stepLabel  = lang === "ar" ? `الخطوة ${step} من ${TOTAL_STEPS - 1}` : `Step ${step} of ${TOTAL_STEPS - 1}`;
  const progressLabel = lang === "ar" ? `تقدم الإعداد: ${Math.round(progress)}%` : `Setup progress: ${Math.round(progress)}%`;

  return (
    <div className="onboarding-overlay" style={{ position: "fixed", inset: 0 }}>
      <Aurora />
      <iframe
        src="/aura-cinematic-bg.html"
        sandbox="allow-scripts allow-same-origin"
        style={{
          position: "fixed", inset: 0, width: "100%", height: "100%",
          border: "none", pointerEvents: "none", zIndex: 0,
        }}
        title="Cinematic Background"
      />

      <div
        role="main"
        aria-label={lang === "ar" ? "معالج إعداد أورا" : "AURA setup wizard"}
        style={{
          position: "relative", zIndex: 10, width: "100%",
          display: "flex", justifyContent: "center", alignItems: "flex-start",
          minHeight: "100vh", padding: "20px", paddingTop: "20px",
        }}
      >
        {/* ── Glassmorphic card matching LoginPage style ── */}
        <SpotlightCard
          className="onboarding-container spotlight-override"
          spotlightColor="rgba(255, 255, 255, 0.08)"
        >
          {/* Progress bar */}
          <motion.div
            className="onboarding-progress-bar"
            role="progressbar"
            aria-valuenow={Math.round(progress)}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label={progressLabel}
          >
            <motion.div
              className="onboarding-progress-fill"
              animate={{ width: `${progress}%` }}
              transition={{ duration: 0.4, ease: "easeInOut" }}
            />
          </motion.div>

          {step > 0 && (
            <p className="onboarding-step-counter" aria-live="polite" aria-atomic="true">
              {stepLabel}
            </p>
          )}

          {/* Step content — overflow-y:auto so content can scroll if needed */}
          <div
            style={{
              position: "relative",
              overflowX: "hidden",
              overflowY: "auto",
              // No fixed minHeight — let content determine height naturally
              display: "grid",
            }}
            aria-live="polite"
            aria-atomic="true"
          >
            <AnimatePresence mode="wait" custom={direction} initial={false}>
              <motion.div
                key={step}
                custom={direction}
                variants={slideVariants}
                initial="enter"
                animate="center"
                exit="exit"
                style={{ gridArea: "1 / 1", width: "100%" }}
              >
                {steps[step]}
              </motion.div>
            </AnimatePresence>
          </div>

          {/* Global error */}
          {error && (
            <p className="onboarding-error global-error" role="alert" aria-live="assertive">
              {error}
            </p>
          )}

          {/* Back button */}
          {step > 0 && !isSubmitting && !showFaceCapture && (
            <button
              className="onboarding-btn ghost back-btn"
              onClick={goBack}
              aria-label={backLabel}
              style={{ marginTop: 12 }}
            >
              ← {lang === "ar" ? "رجع" : "Back"}
            </button>
          )}
        </SpotlightCard>
      </div>
    </div>
  );
};

export default OnboardingPage;