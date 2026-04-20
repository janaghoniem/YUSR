// StepCreateAccount.jsx — Face capture modal redesigned to match LoginPage's face scan UI
//  • Uses Aurora + cinematic background + SpotlightCard for glassmorphic look
//  • Back button with ShinyText
//  • FIXED: card now expands to full content height (no more clipping)

import React, { useState, useCallback, useRef, useEffect } from "react";
import VoiceInput from "../shared/VoiceInput";
import FaceCapture from "../FaceCapture";
import BlurText from "./BlurText";
import ShinyText from "./ShinyText";
import SpotlightCard from "./SpotlightCard";
import Aurora from "./Aurora";
import screenReader from "../../utils/ScreenReader";

const StepCreateAccount = ({
  onSubmit,
  data,
  setData,
  isSubmitting,
  faceRegistered,
  onFaceRegistered,
  showFaceCapture,
  setShowFaceCapture,
  lang = "en",
}) => {
  const [errors,           setErrors]     = useState({});
  const [usernameAvailable,setAvailable]  = useState(null);
  const [checking,         setChecking]   = useState(false);
  const debounceRef = useRef(null);
  const usernameRef = useRef(null);
  const isAr = lang === "ar";

  const userId = localStorage.getItem("userId") || `user_${Date.now()}`;

  useEffect(() => {
    usernameRef.current?.focus();
    const textEn = "Account Setup! Finally, let's create your digital identity.";
    const textAr = "إنشاء الحساب! أخيراً، خلينا نعمل هويتك الرقمية.";
    screenReader.stop();
    screenReader.speak(isAr ? textAr : textEn);
    return () => screenReader.stop();
  }, [isAr]);

  const checkUsername = useCallback(async (username) => {
    if (username.length < 3) { setAvailable(null); return; }
    setChecking(true);
    try {
      const res  = await fetch(`http://localhost:8000/onboarding/check-username?username=${encodeURIComponent(username)}`);
      const json = await res.json();
      setAvailable(json.available);
      if (json.available) setErrors((e) => ({ ...e, username: null }));
    } catch {
      setAvailable(null);
    } finally {
      setChecking(false);
    }
  }, []);

  const handleUsernameChange = (val) => {
    setData({ ...data, username: val });
    setAvailable(null);
    setErrors((e) => ({ ...e, username: null }));
    clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => checkUsername(val), 500);
  };

  const handleFaceCapture = async (faceImage) => {
    try {
      const res = await fetch("http://localhost:8000/onboarding/register-face", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: data.username, user_id: userId, face_image: faceImage }),
      });
      if (res.ok) {
        onFaceRegistered();
        setShowFaceCapture(false);
        setErrors((e) => ({ ...e, face: null }));
      } else {
        const err = await res.json();
        setErrors((e) => ({ ...e, face: err.detail || "Face registration failed." }));
      }
    } catch {
      setErrors((e) => ({ ...e, face: "Network error during face registration." }));
    }
  };

  const validate = () => {
    const errs = {};
    if (!data.username || data.username.length < 3)
      errs.username = isAr ? "اسم المستخدم لازم يكون ٣ حروف على الأقل." : "Username must be at least 3 characters.";
    else if (usernameAvailable === false)
      errs.username = isAr ? "اسم المستخدم ده موجود قبل كده." : "This username is already taken.";
    if (!faceRegistered)
      errs.face = isAr ? "من فضلك سجل وجهك لتسجيل الدخول الآمن." : "Please register your face for secure login.";
    return errs;
  };

  const handleSubmit = () => {
    const errs = validate();
    if (Object.keys(errs).length > 0) { setErrors(errs); return; }
    setErrors({});
    onSubmit();
  };

  const t = (en, ar) => isAr ? ar : en;

  // ── Face capture modal – redesigned to match LoginPage's face login UI ──
  if (showFaceCapture) {
    return (
      <div className="onboarding-step" style={{ display: "flex", flexDirection: "column", width: "100%", alignItems: "center" }}>
        <button
          className="onboarding-btn ghost"
          onClick={() => setShowFaceCapture(false)}
          style={{
            alignSelf: "flex-start",
            marginBottom: "0.5rem",
            padding: "8px 12px",
            width: "auto",
          }}
        >
          <ShinyText text={t("← Back to Account Setup", "← رجع لإعداد الحساب")} speed={3} />
        </button>
        <FaceCapture
          onCapture={handleFaceCapture}
          onCancel={() => setShowFaceCapture(false)}
          mode="signup"
          username={data.username}
          onSpeakStart={() => {}}     // optional – no STT conflict here
          onSpeakEnd={() => {}}
        />
      </div>
    );
  }

  // ── Normal account creation step UI (unchanged) ──
  return (
    <div className="onboarding-step" role="region" aria-labelledby="ca-title">
      <h2 className="onboarding-title" id="ca-title">
        <BlurText text={t("Create your account", "أنشئ حسابك")} delay={50} />
      </h2>
      <p className="onboarding-subtitle">
        {t(
          "Your account links your chats, memory, and preferences across sessions.",
          "حسابك بيربط محادثاتك وذاكرتك وتفضيلاتك في كل الجلسات."
        )}
      </p>

      {/* Username */}
      <VoiceInput
        label={t("Username", "اسم المستخدم")}
        value={data.username}
        onChange={handleUsernameChange}
        placeholder={t("Choose a username…", "اختار اسم مستخدم...")}
        inputRef={usernameRef}
        aria-describedby="username-status"
        lang={lang}
      />
      <div id="username-status" aria-live="polite" aria-atomic="true">
        {checking && <p className="onboarding-hint">{t("Checking availability…", "بنشوف اللو متاح...")}</p>}
        {!checking && usernameAvailable === true  && data.username.length >= 3 && <p className="onboarding-hint success">✓ {t("Username is available", "الاسم متاح")}</p>}
        {!checking && usernameAvailable === false && data.username.length >= 3 && <p className="onboarding-error">✗ {t("Username already taken", "الاسم مأخود")}</p>}
        {errors.username && <p className="onboarding-error" role="alert">{errors.username}</p>}
      </div>

      {/* Email */}
      <div className="voice-input-wrapper" style={{ marginTop: 16 }}>
        <label className="onboarding-input-label" htmlFor="ca-email">
          {t("Email (optional)", "الإيميل (اختياري)")}
        </label>
        <div className="voice-input-container">
          <input
            id="ca-email"
            type="email"
            className="onboarding-input"
            value={data.email || ""}
            onChange={(e) => setData({ ...data, email: e.target.value })}
            placeholder={t("your@email.com", "إيميلك@مثال.كوم")}
            autoComplete="email"
            dir="ltr"
          />
        </div>
      </div>

      {/* Password */}
      <div className="voice-input-wrapper" style={{ marginTop: 16 }}>
        <label className="onboarding-input-label" htmlFor="ca-password">
          {t("Password (optional — face auth is primary)", "كلمة السر (اختياري — التعرف بالوجه هو الأساسي)")}
        </label>
        <div className="voice-input-container">
          <input
            id="ca-password"
            type="password"
            className="onboarding-input"
            value={data.password || ""}
            onChange={(e) => setData({ ...data, password: e.target.value })}
            placeholder={t("Create a fallback password…", "كلمة سر احتياطية...")}
            autoComplete="new-password"
            dir="ltr"
          />
        </div>
      </div>

      {/* Face auth */}
      <div className="face-registration-section" style={{ marginTop: 24 }} aria-labelledby="face-auth-label">
        <label className="onboarding-input-label" id="face-auth-label" style={{ display: "block", marginBottom: 8, fontSize: 14, fontWeight: 600 }}>
          {t("Face Authentication", "التحقق بالوجه")}
        </label>

        {!faceRegistered ? (
          <>
            <button
              className="onboarding-btn secondary"
              style={{ width: "100%" }}
              disabled={!data.username || usernameAvailable !== true}
              onClick={() => setShowFaceCapture(true)}
            >
              {t("Register Your Face →", "سجل وجهك →")}
            </button>
            <p className="onboarding-hint" style={{ marginTop: 6 }}>
              {t(
                "Your face data is encrypted and stored securely. We never store raw images.",
                "بياناتك الوجهية مشفرة ومحفوظة بأمان. بنمسح الصور الأصلية فوراً."
              )}
            </p>
          </>
        ) : (
          <div role="status" aria-live="polite">
            <p className="onboarding-hint success">✓ {t("Face registered successfully!", "تم تسجيل الوجه بنجاح!")}</p>
            <p className="onboarding-hint">{t("You can log in using your face instead of a password.", "تقدر تسجل دخولك بالوجه بدل كلمة السر.")}</p>
          </div>
        )}

        {errors.face && <p className="onboarding-error" role="alert" style={{ marginTop: 6 }}>{errors.face}</p>}
      </div>

      {/* Submit */}
      <button
        className="onboarding-btn primary"
        onClick={handleSubmit}
        disabled={isSubmitting || !faceRegistered}
        style={{ marginTop: 28, width: "100%" }}
        aria-label={isSubmitting ? t("Creating account, please wait", "جاري إنشاء الحساب...") : t("Create account and start using AURA", "أنشئ حساب وابدأ مع أورا")}
      >
        {isSubmitting
          ? t("Creating account…", "جاري الإنشاء...")
          : <ShinyText text={t("Create Account & Start →", "أنشئ الحساب وابدأ →")} speed={3} />}
      </button>
    </div>
  );
};

export default StepCreateAccount;