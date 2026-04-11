// OnboardingPage.jsx — Redesigned with AURA Design System v3.1
import React, { useState, useEffect, useRef, useCallback } from "react";
import { Mic, Send, MicOff, CheckCircle2, ArrowLeft } from "lucide-react";
import SpotlightCard from "./SpotlightCard";
import BlurText from "./BlurText";
import SplitText from "./SplitText";
import ShinyText from "./ShinyText";
import Aurora from "./Aurora";
import AudioIndicator from "./AudioIndicator";

const TOTAL_STEPS = 8;
const SpeechAPI = typeof window !== "undefined" && (window.SpeechRecognition || window.webkitSpeechRecognition);

const STEPS = {
  1: { q: "First things first, what should I call you?", qAr: "مبدئياً، تحب أريك إيه؟", placeholder: "e.g. Layla, Omar...", field: "name", validate: (v) => v.trim().length >= 1 ? "" : "I'd love to know your name!" },
  2: { q: "Awesome! And what do you do for work or study? It helps me tailor my answers to you.", qAr: "ممتاز! ممكن تقولي بتشتغل أو بتدرس إيه؟ ده هيساعدني أجاوبك أحسن.", placeholder: "e.g. Software engineer, designer...", field: "role", validate: () => "" },
  3: { q: "Which language do you feel most comfortable chatting in?", qAr: "إيه اللغة اللي بتفضل نتكلم بيها؟", field: "language", type: "chips", options: ["English", "العربية", "Both / كلهم"] },
  4: { q: "Got it! Do you have any specific accessibility needs I should keep in mind?", qAr: "تمام! عندك أي احتياجات خاصة تحب أعمل حسابها؟", placeholder: "e.g. large text, voice only...", field: "accessibility", validate: () => "", skipLabel: "No specific needs", skipLabelAr: "مفيش احتياجات خاصة" },
  5: { q: "What's the main thing you'd like me to help you out with every day?", qAr: "إيه اكتر حاجة هتحتاجني أساعدك فيها كل يوم؟", field: "taskTypes", type: "chips-multi", options: ["UI automation", "File management", "Web research", "Writing emails", "Summarising docs", "Scheduling", "Coding tasks", "Translating"] },
  6: { q: "What's your preferred communication style with me?", qAr: "تحب طريقتي في الكلام معاك تكون إزاي؟", field: "tone", type: "chips", options: ["Concise & direct", "Detailed & thorough", "Friendly & casual"], optionsAr: ["مختصر ومباشر", "مفصل وشامل", "ودي وغير رسمي"] },
  7: { q: "Perfect. Let's finish up by securing your profile so I can remember all of this.", qAr: "ممتاز! خلينا نأمن ملفك عشان أفتكر كل ده.", field: "account", type: "account" },
};

const OnboardingPage = ({ userId, onComplete, onBack }) => {
  const [step, setStep] = useState(0);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [globalError, setGlobalError] = useState("");
  const [fieldError, setFieldError] = useState("");
  const [inputValue, setInputValue] = useState("");
  const [data, setData] = useState({ name: "", role: "", language: "English", accessibility: "", taskTypes: [], tone: "Friendly & casual", username: "", email: "", password: "", confirmPassword: "", voice: "Gacrux" });
  const [conversation, setConversation] = useState([]);
  
  // Voice states
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [isListening, setIsListening] = useState(false);
  const [transcript, setTranscript] = useState("");
  const [sttError, setSttError] = useState("");
  
  const intentionalStop = useRef(false);
  const recognitionRef = useRef(null);
  const isSpeakingRef = useRef(false);

  const isArabic = data.language === "العربية" || data.language === "Both / كلهم";
  const srLang = isArabic ? "ar-SA" : "en-US";

  const pushAura = useCallback((text) => setConversation((prev) => [...prev, { role: "aura", text }]), []);
  const pushUser = useCallback((text) => setConversation((prev) => [...prev, { role: "user", text }]), []);

  const stopRecording = useCallback(() => {
    if (recognitionRef.current) {
      try { recognitionRef.current.stop(); } catch (e) {}
    }
    setIsListening(false);
  }, []);

  const handleTranscriptRef = useRef(null);

  const startRecording = useCallback(async () => {
    if (intentionalStop.current) return;
    try {
      setIsListening(true);
      setSttError("");
      await navigator.mediaDevices.getUserMedia({ audio: true });

      if (!SpeechAPI) {
        setSttError("STT unsupported.");
        setIsListening(false);
        return;
      }

      const recognition = new SpeechAPI();
      recognition.continuous = false;
      recognition.interimResults = true;
      recognition.lang = srLang;
      recognitionRef.current = recognition;

      recognition.onstart = () => {
        setIsListening(true);
        setSttError("");
      };

      recognition.onresult = (event) => {
        if (intentionalStop.current) return;
        const currentTranscript = Array.from(event.results)
          .map((res) => res[0].transcript)
          .join("");
        setTranscript(currentTranscript);
        if (handleTranscriptRef.current && event.results[0].isFinal) {
           handleTranscriptRef.current(currentTranscript.trim());
           setTranscript("");
        }
      };

      recognition.onerror = (event) => {
        if (event.error !== 'no-speech' && event.error !== 'aborted') {
          console.error("Local SpeechRecognition error:", event.error);
          setSttError(`Mic Error: ${event.error}`);
        }
      };

      recognition.onend = () => {
        if (!intentionalStop.current && !isSpeakingRef.current) {
          try { recognition.start(); } catch (e) {}
        } else {
          setIsListening(false);
        }
      };

      recognition.start();
    } catch (err) {
      setSttError("Mic access denied");
      setIsListening(false);
      console.error(err);
    }
  }, [srLang]);

  // Voice Interaction Logic
  useEffect(() => {
    handleTranscriptRef.current = (text) => {
      if (!text) return;
      
      const lower = text.toLowerCase();
      // Intro Step
      if (step === 0 && (lower.includes("aura next") || lower.includes("aura, next") || lower.includes("next"))) {
         setStep(1); return;
      }
      // Text Steps
      if (step > 0 && step <= 7) {
        const cfg = STEPS[step];
        if (cfg) {
           // Skip magic word
           if (lower === "skip" && cfg.skipLabel) {
             handleSkip(); return;
           }

           if (cfg.type === "chips") {
             const opts = isArabic && cfg.optionsAr ? cfg.optionsAr : cfg.options;
             // Match voice to a chip option
             const match = opts.find(o => lower.includes(o.toLowerCase()));
             if (match) { handleChipSingle(match); return; }
           } 
           else if (cfg.type === "chips-multi") {
             const match = cfg.options.find(o => lower.includes(o.toLowerCase()));
             if (match) { handleChipMultiToggle(match); }
             if (lower.includes("continue") || lower.includes("next") || lower.includes("done")) {
                handleChipMultiSubmit(); return;
             }
           }
           else if (cfg.type === "account") {
             // Let user type normally for passwords
           }
           else {
             // General text input (e.g. name, role)
             // Automatically put into input field
             setInputValue(prev => prev ? prev + " " + text : text);
             if (lower.includes("next") || lower.includes("submit")) {
                 // user says "next" to submit text field
                 // strip "next" from the end
                 setInputValue(prev => prev.toLowerCase().replace("next", "").replace("submit", "").trim());
                 setTimeout(() => handleTextSubmit(), 100);
             }
           }
        }
      }
    };
  }, [step, isArabic]);

  useEffect(() => {
    if (step === 0) { 
      window.speechSynthesis?.cancel(); 
      intentionalStop.current = false;
      setIsSpeaking(true);
      isSpeakingRef.current = true;
      const introText = INTRO_LINES.join(" ");
      const u = new SpeechSynthesisUtterance(introText);
      u.lang = "en-US"; u.rate = 0.9;
      u.onend = () => { setIsSpeaking(false); isSpeakingRef.current = false; startRecording(); };
      window.speechSynthesis?.speak(u);
    }
    else if (STEPS[step]) {
      const cfg = STEPS[step];
      const q = isArabic && cfg.qAr ? cfg.qAr : cfg.q;
      pushAura(q);
      window.speechSynthesis?.cancel();
      intentionalStop.current = true;
      stopRecording();
      setIsSpeaking(true);
      isSpeakingRef.current = true;
      const utterance = new SpeechSynthesisUtterance(q);
      utterance.lang = srLang; utterance.rate = 0.9;
      utterance.onend = () => {
         setIsSpeaking(false);
         isSpeakingRef.current = false;
         intentionalStop.current = false;
         startRecording();
      };
      window.speechSynthesis?.speak(utterance);
    }
    setInputValue(""); setFieldError("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step, isArabic, srLang]);

  useEffect(() => {
    return () => {
      intentionalStop.current = true;
      stopRecording();
      window.speechSynthesis?.cancel();
    };
  }, [stopRecording]);

  const progress = (step / (TOTAL_STEPS - 1)) * 100;

  const handleTextSubmit = () => {
    // Need to use the latest input value from state
    setInputValue(currentVal => {
        const cfg = STEPS[step];
        if (!cfg) return currentVal;
        const finalVal = currentVal || transcript; // use latest
        const err = cfg.validate ? cfg.validate(finalVal) : "";
        if (err) { setFieldError(err); return currentVal; }
        pushUser(finalVal || (isArabic ? cfg.skipLabelAr : cfg.skipLabel) || finalVal);
        setData((d) => ({ ...d, [cfg.field]: finalVal }));
        setStep((s) => s + 1);
        setTranscript("");
        return "";
    });
  };
  const handleChipSingle = (val) => { const cfg = STEPS[step]; pushUser(val); setData((d) => ({ ...d, [cfg.field]: val })); setStep((s) => s + 1); };
  const handleChipMultiToggle = (val) => { setData((d) => { const arr = d.taskTypes.includes(val) ? d.taskTypes.filter((x) => x !== val) : [...d.taskTypes, val]; return { ...d, taskTypes: arr }; }); };
  const handleChipMultiSubmit = () => { pushUser(data.taskTypes.length ? data.taskTypes.join(", ") : "Not sure yet"); setStep((s) => s + 1); };
  const handleSkip = () => { const cfg = STEPS[step]; const label = isArabic ? cfg.skipLabelAr || "تخطى" : cfg.skipLabel || "Skip"; pushUser(label); setStep((s) => s + 1); };

  const handleCreateAccount = async () => {
    if (!data.username || data.username.length < 3) { setFieldError("Username must be at least 3 characters."); return; }
    if (!data.password || data.password.length < 6) { setFieldError("Password must be at least 6 characters."); return; }
    if (data.password !== data.confirmPassword) { setFieldError("Passwords don't match."); return; }
    setFieldError(""); setIsSubmitting(true); setGlobalError("");
    try {
      const currentUserId = localStorage.getItem("userId");
      if (!currentUserId) throw new Error("User ID not found. Please restart onboarding.");
      const res = await fetch("http://localhost:8000/onboarding/create-account", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          user_id: currentUserId,
          username: data.username,
          password: data.password,
          introduction: [data.name && `Name: ${data.name}`, data.role && `Role: ${data.role}`, data.accessibility && `Accessibility: ${data.accessibility}`, data.taskTypes.length && `Tasks: ${data.taskTypes.join(", ")}`, `Tone: ${data.tone}`].filter(Boolean).join(". "),
          preferences: { language: data.language, theme: "dark", voice: data.voice, tone: data.tone, taskTypes: data.taskTypes, accessibility: data.accessibility },
        }),
      });
      if (!res.ok) { const d = await res.json(); throw new Error(d.detail || "Account creation failed"); }
      localStorage.setItem("onboardingComplete", "true");
      localStorage.setItem("userName", data.name || data.username);
      localStorage.setItem("ttsVoice", data.voice);
      setStep(TOTAL_STEPS - 1);
    } catch (err) { setGlobalError(err.message || "Something went wrong. Please try again."); }
    finally { setIsSubmitting(false); }
  };

  const handleDone = () => {
    onComplete({ username: data.name || data.username, preferences: { language: data.language, voice: data.voice, tone: data.tone } });
  };

  return (
    <div className="onboarding-overlay" style={{ position: "fixed", inset: 0 }}>
      {/* Topbar for the draggable region in onboarding/login */}
      <div className="titlebar" style={{ position: "absolute", top: 0, left: 0, width: "100%", zIndex: 100, backgroundColor: "transparent" }}>
        <div className="titlebar-drag">
          <span className="titlebar-title" style={{ paddingLeft: "10px", opacity: 0.8 }}>AURA</span>
        </div>
        <div className="titlebar-buttons">
          <button className="titlebar-btn" onClick={() => window.electronAPI?.minimizeWindow?.()} title="Minimize" aria-label="Minimize">—</button>
          <button className="titlebar-btn" onClick={() => window.electronAPI?.maximizeWindow?.()} title="Maximize" aria-label="Maximize">□</button>
          <button className="titlebar-btn titlebar-close" onClick={() => window.electronAPI?.closeWindow?.()} title="Close" aria-label="Close">X</button>
        </div>
      </div>

      {/* Cinematic Aurora background matching LoginPage */}
      <Aurora />
      <iframe
        src="/aura-cinematic-bg.html"
        style={{ position: "absolute", width: "100%", height: "100%", border: "none", pointerEvents: "none", zIndex: 0 }}
        title="Cinematic Background"
        aria-hidden="true"
      />

      <div style={{ position: "relative", zIndex: 1000 }}>
        <AudioIndicator isSpeaking={isSpeaking} isListening={isListening} transcript={transcript} error={sttError} />
      </div>

      {/* Back button (matching FaceCapture styling roughly, but distinct and visible) */}
      <button
        onClick={onBack}
        className="onboarding-back-btn tooltip-trigger"
        aria-label="Go back to login screen"
        style={{
          position: "absolute",
          top: "40px", // Below titlebar
          left: "24px",
          zIndex: 100,
          background: "rgba(255, 61, 154, 0.15)",
          border: "1px solid rgba(255, 61, 154, 0.3)",
          borderRadius: "50%",
          width: "44px",
          height: "44px",
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          color: "var(--blossom-300)",
          cursor: "pointer",
          backdropFilter: "blur(8px)",
          transition: "all 0.2s ease"
        }}
        onMouseEnter={(e) => {
          e.currentTarget.style.background = "rgba(255, 61, 154, 0.25)";
          e.currentTarget.style.transform = "scale(1.05)";
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.background = "rgba(255, 61, 154, 0.15)";
          e.currentTarget.style.transform = "scale(1)";
        }}
      >
        <ArrowLeft size={20} />
      </button>

      <div style={{ position: "relative", zIndex: 10, width: "100%", height: "100%", display: "flex", justifyContent: "center", alignItems: "center" }}>
        <SpotlightCard className="onboarding-container spotlight-override" spotlightColor="rgba(255, 255, 255, 0.15)" style={{ position: "relative", padding: "3rem 2rem", margin: "auto" }}>
          
          {/* Sleek, WCAG-compliant Back Button Using ReactBits */}
          {(step > 0 && step < TOTAL_STEPS - 1) && (
            <button 
              className="onboarding-btn ghost" 
              onClick={() => {
                  setConversation(c => c.slice(0, -3));
                  setStep(s => s - 1);
              }}
              style={{ position: "absolute", top: "1.5rem", left: "1.5rem", padding: "8px 12px", width: "auto", zIndex: 50 }}
              aria-label="Go back to previous step"
              role="button"
              tabIndex="0"
            >
              <ShinyText text="← Back" disabled={false} speed={3} />
            </button>
          )}

          {/* Progress */}
          {step > 0 && step < TOTAL_STEPS - 1 && (
            <>
              <div className="onboarding-progress-bar" role="progressbar" aria-valuenow={Math.round(progress)} aria-valuemin={0} aria-valuemax={100} aria-label={`Setup progress: ${Math.round(progress)}%`}>
                <div className="onboarding-progress-fill" style={{ width: `${progress}%` }} />
              </div>
              <p className="onboarding-step-counter">Step {step} of {TOTAL_STEPS - 2}</p>
            </>
          )}

          {/* Step 0 — animated intro */}
          {step === 0 && <IntroStep onNext={() => setStep(1)} />}

          {/* Steps 1–6 — conversational */}
          {step >= 1 && step <= 6 && (
            <div className="onboarding-step" aria-live="polite">
              {/* Chat bubbles */}
              <div className="ob-bubble-wrap" aria-label="Conversation">
                {conversation.map((msg, i) => (
                  <div key={i} className={`ob-bubble ${msg.role}`}>{msg.text}</div>
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
                          className={`pref-chip ${cfg.options[i] === data[cfg.field] ? "selected" : ""}`}
                          onClick={() => handleChipSingle(cfg.options[i])}
                          aria-pressed={cfg.options[i] === data[cfg.field]}
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
                          <button key={opt} className={`pref-chip ${data.taskTypes.includes(opt) ? "selected" : ""}`} onClick={() => handleChipMultiToggle(opt)} aria-pressed={data.taskTypes.includes(opt)}>
                            {opt}
                          </button>
                        ))}
                      </div>
                      <button className="onboarding-btn primary" onClick={handleChipMultiSubmit} style={{ marginTop: "14px" }}>
                        {data.taskTypes.length ? `Continue with ${data.taskTypes.length} selected →` : "Skip for now →"}
                      </button>
                    </div>
                  );
                }

                if (cfg.type === "account") {
                  return <AccountForm data={data} setData={setData} fieldError={fieldError} setFieldError={setFieldError} onSubmit={handleCreateAccount} isSubmitting={isSubmitting} isArabic={isArabic} srLang={srLang} />;
                }

                return (
                  <div>
                    <VoiceInputRow 
                         placeholder={cfg.placeholder || ""} 
                         value={inputValue || transcript} 
                         onChange={setInputValue} 
                         onSubmit={handleTextSubmit} 
                         isListening={isListening}
                         onListenToggle={() => {
                             if (isListening) stopRecording(); else startRecording();
                         }}
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

          {/* Step TOTAL_STEPS-1 — done */}
          {step === TOTAL_STEPS - 1 && <DoneStep name={data.name || data.username} onDone={handleDone} />}
        </SpotlightCard>
      </div>
    </div>
  );
};

/* ── Sub-components ── */

const VoiceInputRow = ({ placeholder, value, onChange, onSubmit, isListening, onListenToggle }) => {
  return (
    <div className="ob-mic-row">
      <button
        type="button"
        className={`ob-mic-btn ${isListening ? "recording" : ""}`}
        onClick={onListenToggle}
        aria-label={isListening ? "Stop recording" : "Start voice input"}
        aria-pressed={isListening}
      >
        {isListening ? <MicOff size={19} aria-hidden="true" /> : <Mic size={19} aria-hidden="true" />}
      </button>
      <input
        className="ob-text-input"
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={isListening ? "Listening…" : placeholder}
        aria-label={placeholder}
        onKeyDown={(e) => { if (e.key === "Enter" && value.trim()) onSubmit(); }}
      />
      <button
        type="button"
        className="ob-send-btn"
        onClick={onSubmit}
        disabled={!value.trim()}
        aria-label="Continue"
      >
        <Send size={15} aria-hidden="true" />
      </button>
    </div>
  );
};

const INTRO_LINES = [
  "Hello there, I'm AURA.",
  "Your personal intelligent companion, designed to help with whatever you need.",
  "I'll remember what you like, adapt to your habits, and grow smarter every time we chat.",
  "Let's take a quick moment so I can get to know you.",
  "Whenever you're ready, just say 'Aura next' or tap the button!"
];

function IntroStep({ onNext }) {
  const [visible, setVisible] = useState(0);
  const [done, setDone] = useState(false);

  useEffect(() => {
    if (visible < 1) {
      // First line appears quickly
      const t = setTimeout(() => setVisible(1), 500);
      return () => clearTimeout(t);
    } else if (visible < INTRO_LINES.length) {
      // Delay before the next line
      const t = setTimeout(() => setVisible((v) => v + 1), 1600);
      return () => clearTimeout(t);
    } else {
      const t = setTimeout(() => setDone(true), 800);
      return () => clearTimeout(t);
    }
  }, [visible]);

  return (
    <div className="onboarding-step step-intro" aria-live="polite">
      <div className="intro-lines" style={{ marginTop: "2rem", padding: "1rem", minHeight: "220px", display: "flex", flexDirection: "column", justifyContent: "center" }}>
        {INTRO_LINES.slice(0, visible).map((line, i) => (
          <p key={i} className="intro-line" style={{ fontSize: "1.2rem", fontWeight: "500", textAlign: "center", marginBottom: "0.8rem" }}>
             {i === 0 ? (
                <SplitText text={line} delay={40} className="intro-split-text" />
             ) : i === INTRO_LINES.length - 1 ? (
                 <ShinyText text={line} disabled={false} speed={3} className="" />
             ) : (
                <BlurText text={line} delay={100} animateBy="words" />
             )}
          </p>
        ))}
      </div>
      {done && (
        <button className="onboarding-btn primary" onClick={onNext} aria-label="Let's get started" style={{ margin: "0 auto", display: "block", marginTop: "1rem" }}>
          Let's get started →
        </button>
      )}
    </div>
  );
}

function AccountForm({ data, setData, fieldError, setFieldError, onSubmit, isSubmitting }) {
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
      <div className="voice-input-wrapper">
        <label className="onboarding-input-label" htmlFor="ob-username">Username</label>
        <div className="voice-input-container">
          <input id="ob-username" className="onboarding-input" type="text" value={data.username} autoComplete="username" placeholder="Choose a username…" onChange={(e) => { setData((d) => ({ ...d, username: e.target.value })); checkUsername(e.target.value); }} />
        </div>
        {checking && <p className="onboarding-hint">Checking…</p>}
        {usernameAvailable === true  && <p className="onboarding-hint success">✓ Available</p>}
        {usernameAvailable === false && <p className="onboarding-error">✗ Already taken</p>}
      </div>

      <div className="voice-input-wrapper">
        <label className="onboarding-input-label" htmlFor="ob-password">Password</label>
        <div className="voice-input-container">
          <input id="ob-password" className="onboarding-input" type="password" value={data.password} autoComplete="new-password" placeholder="At least 6 characters…" onChange={(e) => setData((d) => ({ ...d, password: e.target.value }))} />
        </div>
      </div>

      <div className="voice-input-wrapper">
        <label className="onboarding-input-label" htmlFor="ob-confirm">Confirm password</label>
        <div className="voice-input-container">
          <input id="ob-confirm" className="onboarding-input" type="password" value={data.confirmPassword} autoComplete="new-password" placeholder="Repeat password…" onChange={(e) => setData((d) => ({ ...d, confirmPassword: e.target.value }))} />
        </div>
      </div>

      <div className="voice-input-wrapper">
        <label className="onboarding-input-label" htmlFor="ob-voice">Preferred voice</label>
        <select id="ob-voice" className="settings-select" value={data.voice} onChange={(e) => setData((d) => ({ ...d, voice: e.target.value }))}>
          <option value="Gacrux">Gacrux (default)</option>
          <option value="orpheus-english">Orpheus English</option>
          <option value="orpheus-arabic">Orpheus Arabic</option>
        </select>
      </div>

      {fieldError && <p className="onboarding-error" role="alert">{fieldError}</p>}

      <button className="onboarding-btn primary" onClick={onSubmit} disabled={isSubmitting || usernameAvailable === false} style={{ marginTop: "8px" }}>
        {isSubmitting ? "Creating account…" : "Create account & start →"}
      </button>
    </div>
  );
}

function DoneStep({ name, onDone }) {
  return (
    <div className="onboarding-step" style={{ alignItems: "center", textAlign: "center", gap: 16, paddingTop: 8 }} aria-live="polite">
      <CheckCircle2 size={52} color="var(--blossom-400)" aria-hidden="true" style={{ filter: "drop-shadow(0 0 16px rgba(255,61,154,0.4))" }} />
      <h2 className="onboarding-title" style={{ textAlign: "center" }}>
        You're all set{name ? `, ${name}` : ""}!
      </h2>
      <p className="onboarding-subtitle" style={{ textAlign: "center", marginBottom: 0 }}>
        AURA knows what you need. Just say{" "}
        <strong style={{ color: "var(--blossom-300)" }}>"Hey AURA"</strong>{" "}
        or tap the mic to begin.
      </p>
      <button className="onboarding-btn primary" onClick={onDone} style={{ alignSelf: "center", marginTop: 4 }} autoFocus>
        Start using AURA →
      </button>
    </div>
  );
}

export default OnboardingPage;