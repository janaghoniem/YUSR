// OnboardingPage.jsx
// Voice-first conversational onboarding with speech recognition
// Asks all questions relevant to AURA: name, role, language, accessibility
// needs, task preferences, working hours, device, privacy, and account creation.

import React, { useState, useEffect, useRef, useCallback } from "react";
import AuraLogo from "../AuraLogo";
import { Mic, Send, MicOff, CheckCircle2 } from "lucide-react";

const TOTAL_STEPS = 8; // 0=intro, 1=name, 2=role, 3=language, 4=accessibility, 5=tasks, 6=account, 7=done

// ── Utility: does browser support speech recognition? ─────────────
const SpeechAPI = window.SpeechRecognition || window.webkitSpeechRecognition;

// ── Step configs ──────────────────────────────────────────────────
const STEPS = {
  1: {
    q: "What's your name? I'll use it every time we talk.",
    qAr: "ما اسمك؟ هاستخدمه كل ما نتكلم.",
    placeholder: "e.g. Layla, Omar, Hana…",
    field: "name",
    validate: (v) => v.trim().length >= 1 ? "" : "Please tell me your name.",
  },
  2: {
    q: "What do you do? Knowing your role helps me give better answers.",
    qAr: "بتشتغل في إيه؟ لو عارف دورك هقدر أساعدك أحسن.",
    placeholder: "e.g. Software engineer, student, designer…",
    field: "role",
    validate: () => "",
  },
  3: {
    q: "Which language do you prefer we speak in?",
    qAr: "أيه اللغة اللي بتحب نتكلم بيها؟",
    field: "language",
    type: "chips",
    options: ["English", "العربية", "Both / كلهم"],
  },
  4: {
    q: "Do you have any accessibility needs I should know about? This helps me adjust how I communicate.",
    qAr: "عندك أي احتياجات خاصة لازم أعرفها؟ ده هيساعدني أتكيف معاك.",
    placeholder: "e.g. vision impaired, prefer slow speech, large text…",
    field: "accessibility",
    validate: () => "",
    skipLabel: "No specific needs",
    skipLabelAr: "مفيش احتياجات خاصة",
  },
  5: {
    q: "What kinds of tasks do you most want AURA to help with?",
    qAr: "أيه النوع من المهام اللي محتاج أورا تساعدك فيها أكتر؟",
    field: "taskTypes",
    type: "chips-multi",
    options: [
      "UI automation",
      "File management",
      "Web research",
      "Writing & drafting",
      "Summarising documents",
      "Scheduling & reminders",
      "Code & dev tasks",
      "Arabic language tasks",
    ],
  },
  6: {
    q: "How would you like AURA to talk to you?",
    qAr: "بتحب أورا تتكلم معاك إزاي؟",
    field: "tone",
    type: "chips",
    options: ["Concise & direct", "Detailed & thorough", "Friendly & casual"],
    optionsAr: ["مختصر ومباشر", "مفصل وشامل", "ودي وغير رسمي"],
  },
  7: {
    q: "Almost done — create your account to save everything across sessions.",
    qAr: "تقريبًا خلصنا — أنشئ حسابك عشان نحفظ كل حاجة.",
    field: "account",
    type: "account",
  },
};

// ── Mini hook: speech recognition ─────────────────────────────────
function useSpeechInput(lang = "en-US") {
  const [isListening, setIsListening] = useState(false);
  const [transcript, setTranscript] = useState("");
  const recognizerRef = useRef(null);

  const start = useCallback(() => {
    if (!SpeechAPI) return;
    const r = new SpeechAPI();
    r.lang = lang;
    r.interimResults = true;
    r.continuous = false;
    r.onresult = (e) => {
      const t = Array.from(e.results).map((res) => res[0].transcript).join("");
      setTranscript(t);
    };
    r.onend = () => setIsListening(false);
    r.onerror = () => setIsListening(false);
    r.start();
    recognizerRef.current = r;
    setIsListening(true);
    setTranscript("");
  }, [lang]);

  const stop = useCallback(() => {
    recognizerRef.current?.stop();
    setIsListening(false);
  }, []);

  return { isListening, transcript, start, stop, supported: !!SpeechAPI };
}

// ── Voice input row (mic + text input + send) ─────────────────────
const VoiceInputRow = ({ placeholder, value, onChange, onSubmit, lang = "en-US", disabled }) => {
  const { isListening, transcript, start, stop, supported } = useSpeechInput(lang);

  useEffect(() => { if (transcript) onChange(transcript); }, [transcript]);

  const handleMic = () => { isListening ? stop() : start(); };

  return (
    <div className="ob-mic-row">
      {supported && (
        <button
          type="button"
          className={`ob-mic-btn ${isListening ? "recording" : ""}`}
          onClick={handleMic}
          aria-label={isListening ? "Stop recording" : "Start voice input"}
          aria-pressed={isListening}
        >
          {isListening ? <MicOff size={20} aria-hidden="true" /> : <Mic size={20} aria-hidden="true" />}
        </button>
      )}
      <input
        className="ob-text-input"
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={isListening ? "Listening…" : placeholder}
        aria-label={placeholder}
        disabled={disabled}
        onKeyDown={(e) => { if (e.key === "Enter" && value.trim()) onSubmit(); }}
      />
      <button
        type="button"
        className="ob-send-btn"
        onClick={onSubmit}
        disabled={!value.trim() || disabled}
        aria-label="Continue"
      >
        <Send size={16} aria-hidden="true" />
      </button>
    </div>
  );
};

// ── Main component ─────────────────────────────────────────────────
const OnboardingPage = ({ userId, onComplete }) => {
  const [step, setStep] = useState(0);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [globalError, setGlobalError] = useState("");
  const [fieldError, setFieldError] = useState("");
  const [inputValue, setInputValue] = useState("");

  const [data, setData] = useState({
    name: "",
    role: "",
    language: "English",
    accessibility: "",
    taskTypes: [],
    tone: "Friendly & casual",
    username: "",
    email: "",
    password: "",
    confirmPassword: "",
    voice: "Gacrux",
  });

  // Shown conversation history in the bubble UI
  const [conversation, setConversation] = useState([]);

  const isArabic = data.language === "العربية" || data.language === "Both / كلهم";
  const srLang = isArabic ? "ar-SA" : "en-US";

  const pushAura = useCallback((text) => {
    setConversation((prev) => [...prev, { role: "aura", text }]);
  }, []);

  const pushUser = useCallback((text) => {
    setConversation((prev) => [...prev, { role: "user", text }]);
  }, []);

  // Ask the current step's question when step changes
  useEffect(() => {
    if (step === 0) {
      // Intro handled by its own render
      window.speechSynthesis.cancel();
    } else if (STEPS[step]) {
      const cfg = STEPS[step];
      const q = isArabic && cfg.qAr ? cfg.qAr : cfg.q;
      pushAura(q);
      
      // Speak the question and choices
      window.speechSynthesis.cancel();
      const utteranceText = [q];
      if (cfg.options) {
        const opts = isArabic && cfg.optionsAr ? cfg.optionsAr : cfg.options;
        utteranceText.push(isArabic ? "The options are:" : "The options are:");
        utteranceText.push(opts.join(", "));
      }

      const utterance = new SpeechSynthesisUtterance(utteranceText.join(". "));
      utterance.lang = srLang;
      utterance.rate = 0.9;
      window.speechSynthesis.speak(utterance);
    }
    setInputValue("");
    setFieldError("");
  }, [step, isArabic, srLang, pushAura]);

  const progress = (step / (TOTAL_STEPS - 1)) * 100;

  // ── Advance from a text/voice answer ──
  const handleTextSubmit = () => {
    const cfg = STEPS[step];
    if (!cfg) return;
    const err = cfg.validate ? cfg.validate(inputValue) : "";
    if (err) { setFieldError(err); return; }
    pushUser(inputValue || (isArabic ? cfg.skipLabelAr : cfg.skipLabel) || inputValue);
    setData((d) => ({ ...d, [cfg.field]: inputValue }));
    setStep((s) => s + 1);
  };

  // ── Advance from a chip selection ──
  const handleChipSingle = (val) => {
    const cfg = STEPS[step];
    pushUser(val);
    setData((d) => ({ ...d, [cfg.field]: val }));
    setStep((s) => s + 1);
  };

  const handleChipMultiToggle = (val) => {
    setData((d) => {
      const arr = d.taskTypes.includes(val)
        ? d.taskTypes.filter((x) => x !== val)
        : [...d.taskTypes, val];
      return { ...d, taskTypes: arr };
    });
  };

  const handleChipMultiSubmit = () => {
    pushUser(data.taskTypes.length ? data.taskTypes.join(", ") : "Not sure yet");
    setStep((s) => s + 1);
  };

  const handleSkip = () => {
    const cfg = STEPS[step];
    const label = isArabic ? cfg.skipLabelAr || "تخطى" : cfg.skipLabel || "Skip";
    pushUser(label);
    setStep((s) => s + 1);
  };

  // ── Final account creation ──
  const handleCreateAccount = async () => {
    if (!data.username || data.username.length < 3) { setFieldError("Username must be at least 3 characters."); return; }
    if (!data.password || data.password.length < 6) { setFieldError("Password must be at least 6 characters."); return; }
    if (data.password !== data.confirmPassword) { setFieldError("Passwords don't match."); return; }
    setFieldError("");
    setIsSubmitting(true);
    setGlobalError("");
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
        body: JSON.stringify({
          user_id: userId,
          username: data.username,
          password: data.password,
          introduction: [
            data.name && `Name: ${data.name}`,
            data.role && `Role: ${data.role}`,
            data.accessibility && `Accessibility needs: ${data.accessibility}`,
            data.taskTypes.length && `Task preferences: ${data.taskTypes.join(", ")}`,
            `Tone preference: ${data.tone}`,
          ].filter(Boolean).join(". "),
          preferences: {
            language: data.language,
            theme: "dark",
            voice: data.voice,
            tone: data.tone,
            taskTypes: data.taskTypes,
            accessibility: data.accessibility,
          },
        }),
      });
      if (!res.ok) { const d = await res.json(); throw new Error(d.detail || "Account creation failed"); }
      localStorage.setItem("onboardingComplete", "true");
      localStorage.setItem("userName", data.name || data.username);
      localStorage.setItem("ttsVoice", data.voice);
      setStep(TOTAL_STEPS - 1); // done screen
    } catch (err) {
      setGlobalError(err.message || "Something went wrong. Please try again.");
    } finally {
      setIsSubmitting(false);
    }
  };

  // ── Done ──
  const handleDone = () => {
    onComplete({ username: data.name || data.username, preferences: { language: data.language, voice: data.voice, tone: data.tone } });
  };

  // ──────────────────────────────────────────────────────────────
  //  RENDER
  // ──────────────────────────────────────────────────────────────
  return (
    <div className="onboarding-overlay">
      <div
        className="onboarding-container"
        role="main"
        aria-label="AURA onboarding"
      >
        {/* Progress */}
        {step > 0 && step < TOTAL_STEPS - 1 && (
          <>
            <div className="onboarding-progress-bar" role="progressbar" aria-valuenow={Math.round(progress)} aria-valuemin={0} aria-valuemax={100} aria-label={`Setup progress: ${Math.round(progress)}%`}>
              <div className="onboarding-progress-fill" style={{ width: `${progress}%` }} />
            </div>
            <p className="onboarding-step-counter">Step {step} of {TOTAL_STEPS - 2}</p>
          </>
        )}

        {/* ── STEP 0: Animated intro ── */}
        {step === 0 && <IntroStep onNext={() => setStep(1)} />}

        {/* ── STEPS 1–6: Conversational ── */}
        {step >= 1 && step <= 6 && (
          <div className="onboarding-step" aria-live="polite">
            {/* Conversation bubbles */}
            <div className="ob-bubble-wrap" aria-label="Conversation">
              {conversation.map((msg, i) => (
                <div key={i} className={`ob-bubble ${msg.role}`}>
                  {msg.text}
                </div>
              ))}
            </div>

            {/* Input area for current step */}
            {STEPS[step] && (() => {
              const cfg = STEPS[step];

              if (cfg.type === "chips") {
                const opts = isArabic && cfg.optionsAr ? cfg.optionsAr : cfg.options;
                return (
                  <div className="pref-options" role="group" aria-label={cfg.q}>
                    {opts.map((opt, i) => (
                      <button
                        key={i}
                        className={`pref-chip ${(isArabic && cfg.optionsAr ? cfg.optionsAr[i] === data[cfg.field] : opt === data[cfg.field]) ? "selected" : ""}`}
                        onClick={() => handleChipSingle(cfg.options[i])}
                        aria-pressed={(cfg.options[i] === data[cfg.field])}
                      >
                        {opt}
                      </button>
                    ))}
                  </div>
                );
              }

              if (cfg.type === "chips-multi") {
                return (
                  <div>
                    <div className="pref-options" role="group" aria-label="Select all that apply">
                      {cfg.options.map((opt) => (
                        <button
                          key={opt}
                          className={`pref-chip ${data.taskTypes.includes(opt) ? "selected" : ""}`}
                          onClick={() => handleChipMultiToggle(opt)}
                          aria-pressed={data.taskTypes.includes(opt)}
                        >
                          {opt}
                        </button>
                      ))}
                    </div>
                    <button
                      className="onboarding-btn primary"
                      onClick={handleChipMultiSubmit}
                      style={{ marginTop: "14px" }}
                    >
                      {data.taskTypes.length ? `Continue with ${data.taskTypes.length} selected →` : "Skip for now →"}
                    </button>
                  </div>
                );
              }

              if (cfg.type === "account") {
                return <AccountForm data={data} setData={setData} fieldError={fieldError} setFieldError={setFieldError} onSubmit={handleCreateAccount} isSubmitting={isSubmitting} isArabic={isArabic} srLang={srLang} />;
              }

              // Default: text input
              return (
                <div>
                  <VoiceInputRow
                    placeholder={cfg.placeholder || ""}
                    value={inputValue}
                    onChange={setInputValue}
                    onSubmit={handleTextSubmit}
                    lang={srLang}
                  />
                  {cfg.skipLabel && (
                    <button className="onboarding-btn ghost" onClick={handleSkip} style={{ marginTop: "10px" }}>
                      {isArabic ? cfg.skipLabelAr : cfg.skipLabel}
                    </button>
                  )}
                  {fieldError && <p className="onboarding-error" role="alert">{fieldError}</p>}
                </div>
              );
            })()}

            {globalError && <p className="global-error" role="alert">{globalError}</p>}
          </div>
        )}

        {/* ── STEP 7: Done ── */}
        {step === TOTAL_STEPS - 1 && (
          <DoneStep name={data.name || data.username} onDone={handleDone} />
        )}

        {/* Back */}
        {step > 1 && step < TOTAL_STEPS - 1 && !isSubmitting && (
          <button
            className="onboarding-btn ghost back-btn"
            onClick={() => {
              setConversation((c) => c.slice(0, -2)); // remove last Q+A pair
              setStep((s) => s - 1);
            }}
          >
            ← Back
          </button>
        )}
      </div>
    </div>
  );
};

// ── Sub-components ────────────────────────────────────────────────

const INTRO_LINES = [
  "Hi there. I'm AURA.",
  "Your intelligent assistant — built to think, create, and act.",
  "I remember what you like, learn your habits, and get smarter every session.",
  "Let's take a minute to get to know each other.",
];

function IntroStep({ onNext }) {
  const [visible, setVisible] = useState(0);
  const [done, setDone] = useState(false);

  useEffect(() => {
    if (visible < INTRO_LINES.length) {
      const t = setTimeout(() => setVisible((v) => v + 1), 850);
      return () => clearTimeout(t);
    } else {
      const t = setTimeout(() => setDone(true), 400);
      return () => clearTimeout(t);
    }
  }, [visible]);

  return (
    <div className="onboarding-step step-intro" aria-live="polite">
      <AuraLogo size={52} color="#FF3D9A" animated={true} aria-label="AURA logo" style={{ marginBottom: 20 }} />
      <div className="intro-lines">
        {INTRO_LINES.slice(0, visible).map((line, i) => (
          <p key={i} className={`intro-line ${i === visible - 1 ? "fade-in" : ""}`}>{line}</p>
        ))}
      </div>
      {done && (
        <button className="onboarding-btn primary" onClick={onNext}>
          Let's get started →
        </button>
      )}
    </div>
  );
}

function AccountForm({ data, setData, fieldError, setFieldError, onSubmit, isSubmitting, isArabic, srLang }) {
  const [checking, setChecking] = useState(false);
  const [usernameAvailable, setUsernameAvailable] = useState(null);

  const checkUsername = async (u) => {
    if (u.length < 3) return;
    setChecking(true);
    try {
      const res = await fetch(`http://localhost:8000/onboarding/check-username?username=${encodeURIComponent(u)}`);
      const d = await res.json();
      setUsernameAvailable(d.available);
    } catch { setUsernameAvailable(null); }
    finally { setChecking(false); }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
      {/* Username */}
      <div className="voice-input-wrapper">
        <label className="onboarding-input-label" htmlFor="ob-username">Username</label>
        <div className="voice-input-container">
          <input
            id="ob-username"
            className="onboarding-input"
            type="text"
            value={data.username}
            autoComplete="username"
            placeholder="Choose a username…"
            onChange={(e) => { setData((d) => ({ ...d, username: e.target.value })); checkUsername(e.target.value); }}
          />
        </div>
        {checking && <p className="onboarding-hint">Checking…</p>}
        {usernameAvailable === true && <p className="onboarding-hint success">✓ Available</p>}
        {usernameAvailable === false && <p className="onboarding-error">✗ Already taken</p>}
      </div>

      {/* Password */}
      <div className="voice-input-wrapper">
        <label className="onboarding-input-label" htmlFor="ob-password">Password</label>
        <div className="voice-input-container">
          <input id="ob-password" className="onboarding-input" type="password" value={data.password} autoComplete="new-password" placeholder="At least 6 characters…" onChange={(e) => setData((d) => ({ ...d, password: e.target.value }))} />
        </div>
      </div>

      {/* Confirm */}
      <div className="voice-input-wrapper">
        <label className="onboarding-input-label" htmlFor="ob-confirm">Confirm password</label>
        <div className="voice-input-container">
          <input id="ob-confirm" className="onboarding-input" type="password" value={data.confirmPassword} autoComplete="new-password" placeholder="Repeat password…" onChange={(e) => setData((d) => ({ ...d, confirmPassword: e.target.value }))} />
        </div>
      </div>

      {/* Voice */}
      <div className="voice-input-wrapper">
        <label className="onboarding-input-label" htmlFor="ob-voice">Preferred voice</label>
        <select id="ob-voice" className="settings-select" value={data.voice} onChange={(e) => setData((d) => ({ ...d, voice: e.target.value }))}>
          <option value="Gacrux">Gacrux (default)</option>
          <option value="orpheus-english">Orpheus English</option>
          <option value="orpheus-arabic">Orpheus Arabic</option>
        </select>
      </div>

      {fieldError && <p className="onboarding-error" role="alert">{fieldError}</p>}

      <button
        className="onboarding-btn primary"
        onClick={onSubmit}
        disabled={isSubmitting || usernameAvailable === false}
        style={{ marginTop: "8px" }}
      >
        {isSubmitting ? "Creating account…" : "Create account & start →"}
      </button>
    </div>
  );
}

function DoneStep({ name, onDone }) {
  return (
    <div className="onboarding-step" style={{ alignItems: "center", textAlign: "center", gap: 16, paddingTop: 8 }} aria-live="polite">
      <CheckCircle2 size={52} color="#FF3D9A" aria-hidden="true" />
      <h2 className="onboarding-title" style={{ textAlign: "center" }}>
        You're all set{name ? `, ${name}` : ""}!
      </h2>
      <p className="onboarding-subtitle" style={{ textAlign: "center", marginBottom: 0 }}>
        AURA knows what you need. Just say <strong style={{ color: "var(--pink-300)" }}>"Hey AURA"</strong> or tap the mic to begin.
      </p>
      <button
        className="onboarding-btn primary"
        onClick={onDone}
        style={{ alignSelf: "center", marginTop: 4 }}
        autoFocus
      >
        Start using AURA →
      </button>
    </div>
  );
}

export default OnboardingPage;