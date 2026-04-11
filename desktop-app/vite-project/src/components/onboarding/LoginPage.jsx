// LoginPage.jsx — Redesigned with AURA Design System v3.1
import React, { useState, useEffect, useRef } from "react";
import FaceCapture from "../FaceCapture";
import SpotlightCard from "./SpotlightCard";
import ShinyText from "./ShinyText";
import Aurora from "./Aurora";
import AudioIndicator from "./AudioIndicator";
import screenReader from "../../utils/ScreenReader";

const LoginPage = ({ onLogin, onSignUp }) => {
  const [username, setUsername] = useState(() => localStorage.getItem("userName") || "");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [showFaceLogin, setShowFaceLogin] = useState(false);
  const [showManualLogin, setShowManualLogin] = useState(false);
  const [hasFaceAuth, setHasFaceAuth] = useState(false);
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [sttError, setSttError] = useState("");
  const intentionalStop = useRef(false);

  const [isListening, setIsListening] = useState(false);
  const [transcript, setTranscript] = useState("");
  const recognitionRef = useRef(null);

  const stopRecording = () => {
    if (recognitionRef.current) {
      try { recognitionRef.current.stop(); } catch (e) {}
    }
    setIsListening(false);
  };

  const handleTranscript = (text) => {
    if (!text) return;
    const lower = text.toLowerCase();

    // If currently in face login view, handle specific commands
    if (showFaceLogin) {
      if (lower.includes("start face scan") || lower.includes("scan") || lower.includes("start scan")) {
        window.dispatchEvent(new CustomEvent('face-capture-start-scan'));
        setTranscript("");
      } else if (lower.includes("use this photo") || lower.includes("use photo") || lower.includes("use") || lower.includes("confirm")) {
        window.dispatchEvent(new CustomEvent('face-capture-use-photo'));
        setTranscript("");
      } else if (lower.includes("retake") || lower.includes("retake photo") || lower.includes("try again")) {
        window.dispatchEvent(new CustomEvent('face-capture-retake-photo'));
        setTranscript("");
      } else if (lower.includes("back") || lower.includes("back to login") || lower.includes("cancel")) {
        intentionalStop.current = false;
        setShowFaceLogin(false);
        setShowManualLogin(false);
        setTranscript("");
      }
      return;
    }

    if (lower.includes("face")) {
      intentionalStop.current = false;
      stopRecording();
      setShowFaceLogin(true);
      setTranscript("");
    } else if (lower.includes("password") || lower.includes("manual") || lower.includes("username")) {
      intentionalStop.current = true;
      stopRecording();
      setShowManualLogin(true);
      setTranscript("");
    } else if (lower.includes("create") || lower.includes("sign up") || lower.includes("new account")) {
      intentionalStop.current = true;
      stopRecording();
      onSignUp();
      setTranscript("");
    }
  };

  const startRecording = async () => {
    if (intentionalStop.current) return;
    
    try {
      setIsListening(true);
      setSttError("");
      
      // Explicitly request microphone access first to wake up Electron's permissions
      await navigator.mediaDevices.getUserMedia({ audio: true });

      const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
      if (!SpeechRecognition) {
        setSttError("STT unsupported.");
        setIsListening(false);
        return;
      }

      const recognition = new SpeechRecognition();
      recognition.continuous = false;
      recognition.interimResults = false;
      recognition.lang = 'en-US';
      recognitionRef.current = recognition;

      recognition.onstart = () => {
        setIsListening(true);
        setSttError("");
      };

      recognition.onresult = (event) => {
        if (intentionalStop.current) return;
        const currentTranscript = event.results[0][0].transcript;
        setTranscript(currentTranscript);
        handleTranscript(currentTranscript);
      };

      recognition.onerror = (event) => {
        if (event.error !== 'no-speech' && event.error !== 'aborted') {
          console.error("Local SpeechRecognition error:", event.error);
          setSttError(`Mic Error: ${event.error}`);
        }
      };

      recognition.onend = () => {
        if (!intentionalStop.current && !isSpeaking) {
          try {
            recognition.start();
          } catch (e) {}
        } else {
          setIsListening(false);
        }
      };

      recognition.start();
    } catch (err) {
      console.error("Local STT Error:", err);
      setSttError("Mic access denied");
      setIsListening(false);
    }
  };

  useEffect(() => {
    setIsSpeaking(true);
    screenReader.speak("Welcome to AURA. Sign in with your face for a seamless, password-free experience. Would you like to login with face, use a password, or create an account?", {
      onComplete: () => {
        setIsSpeaking(false);
        intentionalStop.current = false;
        startRecording();
      }
    });

    return () => {
      intentionalStop.current = true;
      screenReader.stop();
      stopRecording();
    };
  }, []);

  const checkFaceAuthStatus = async (u) => {
    if (u.length < 3) return;
    try {
      const res = await fetch(`http://localhost:8000/onboarding/face-status/${encodeURIComponent(u)}`);
      const data = await res.json();
      setHasFaceAuth(data.has_face_auth);
    } catch { /* silent */ }
  };

  const handleUsernameChange = (e) => {
    const value = e.target.value;
    setUsername(value);
    if (value.length >= 3) checkFaceAuthStatus(value);
    else setHasFaceAuth(false);
  };

  const handleFaceOnlyCapture = async (faceImage) => {
    setLoading(true); setError("");
    try {
      const res = await fetch("http://localhost:8000/onboarding/login-face-only", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ face_image: faceImage }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Face verification failed");
      localStorage.setItem("userId", data.user_id);
      localStorage.setItem("userName", data.username);
      localStorage.setItem("onboardingComplete", "true");
      localStorage.setItem("authMethod", "face");
      if (data.preferences?.voice) localStorage.setItem("ttsVoice", data.preferences.voice);
      onLogin({ userId: data.user_id, username: data.username, preferences: data.preferences });
    } catch (err) { setError(err.message || "Face verification failed. Please try again."); setShowFaceLogin(false); }
    finally { setLoading(false); }
  };

  const handleFaceCapture = async (faceImage) => {
    setLoading(true); setError("");
    try {
      const res = await fetch("http://localhost:8000/onboarding/verify-face", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, face_image: faceImage }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Face verification failed");
      localStorage.setItem("userId", data.user_id);
      localStorage.setItem("userName", data.username);
      localStorage.setItem("onboardingComplete", "true");
      localStorage.setItem("authMethod", "face");
      if (data.preferences?.voice) localStorage.setItem("ttsVoice", data.preferences.voice);
      onLogin({ userId: data.user_id, username: data.username, preferences: data.preferences });
    } catch (err) { setError(err.message || "Face verification failed. Please try again."); setShowFaceLogin(false); }
    finally { setLoading(false); }
  };

  const handleLogin = async () => {
    if (!username.trim() || !password.trim()) { setError("Please enter both username and password."); return; }
    setLoading(true); setError("");
    try {
      const res = await fetch("http://localhost:8000/onboarding/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Login failed");
      localStorage.setItem("userId", data.user_id);
      localStorage.setItem("userName", data.username);
      localStorage.setItem("onboardingComplete", "true");
      localStorage.setItem("authMethod", "password");
      if (data.preferences?.voice) localStorage.setItem("ttsVoice", data.preferences.voice);
      onLogin({ userId: data.user_id, username: data.username, preferences: data.preferences });
    } catch (err) { setError(err.message || "Login failed. Please try again."); }
    finally { setLoading(false); }
  };

  if (showFaceLogin) {
    return (
      <div className="onboarding-overlay" style={{ position: "fixed", inset: 0 }}>
        {/* Topbar for the draggable region in onboarding/login */}
        <div className="titlebar" style={{ position: "absolute", top: 0, left: 0, width: "100%", zIndex: 100, backgroundColor: "transparent" }}>
          <div className="titlebar-drag">
            <span className="titlebar-title" style={{ paddingLeft: "10px", opacity: 0.8 }}>AURA</span>
          </div>
          <div className="titlebar-buttons">
            <button className="titlebar-btn" onClick={() => window.electronAPI?.minimizeWindow?.()} title="Minimize">—</button>
            <button className="titlebar-btn" onClick={() => window.electronAPI?.maximizeWindow?.()} title="Maximize">□</button>
            <button className="titlebar-btn titlebar-close" onClick={() => window.electronAPI?.closeWindow?.()} title="Close">X</button>
          </div>
        </div>

        {/* Soft gradient Pink background alongside cinematic */}
        <Aurora />

        {/* Background Cinematic iframe */}
        <iframe
          src="/aura-cinematic-bg.html"
          style={{
            position: "absolute",
            width: "100%",
            height: "100%",
            border: "none",
            pointerEvents: "none",
            zIndex: 0
          }}
          title="Cinematic Background"
        />

        <div style={{ position: "relative", zIndex: 1000 }}>
          <AudioIndicator isSpeaking={isSpeaking} isListening={isListening} transcript={transcript} error={sttError} />
        </div>

        <div style={{ position: "relative", zIndex: 10, width: "100%", height: "100%", display: "flex", justifyContent: "center", alignItems: "center" }}>
          <SpotlightCard className="onboarding-container spotlight-override" spotlightColor="rgba(255, 255, 255, 0.1)">
            <button 
              className="onboarding-btn ghost" 
              onClick={() => { setShowFaceLogin(false); setShowManualLogin(false); }}
              style={{ alignSelf: "flex-start", marginBottom: "0.5rem", padding: "8px 12px", width: "auto", flexWrap: "wrap", whiteSpace: "normal" }}
              aria-label="Go back to manual login"
            >
              <ShinyText text="← Back to Login" disabled={false} speed={3} className="" />
            </button>
            <FaceCapture
              onCapture={showManualLogin ? handleFaceCapture : handleFaceOnlyCapture}
              onCancel={() => { setShowFaceLogin(false); setShowManualLogin(false); }}
              mode="login"
              username={showManualLogin ? username : undefined}
              onSpeakStart={() => { 
                setIsSpeaking(true); 
                intentionalStop.current = true; 
                stopRecording(); 
              }}
              onSpeakEnd={() => { 
                setIsSpeaking(false); 
                intentionalStop.current = false; 
                startRecording(); 
              }}
            />
          </SpotlightCard>
        </div>
      </div>
    );
  }

  return (
    <div className="onboarding-overlay" style={{ position: "fixed", inset: 0 }}>
      {/* Topbar for the draggable region in onboarding/login */}
      <div className="titlebar" style={{ position: "absolute", top: 0, left: 0, width: "100%", zIndex: 100, backgroundColor: "transparent" }}>
        <div className="titlebar-drag">
          <span className="titlebar-title" style={{ paddingLeft: "10px", opacity: 0.8 }}>AURA</span>
        </div>
        <div className="titlebar-buttons">
          <button className="titlebar-btn" onClick={() => window.electronAPI?.minimizeWindow?.()} title="Minimize">—</button>
          <button className="titlebar-btn" onClick={() => window.electronAPI?.maximizeWindow?.()} title="Maximize">□</button>
          <button className="titlebar-btn titlebar-close" onClick={() => window.electronAPI?.closeWindow?.()} title="Close">X</button>
        </div>
      </div>

      {/* Soft gradient Pink background alongside cinematic */}
      <Aurora />

      {/* Background Cinematic iframe */}
      <iframe
        src="/aura-cinematic-bg.html"
        style={{
          position: "absolute",
          width: "100%",
          height: "100%",
          border: "none",
          pointerEvents: "none",
          zIndex: 0
        }}
        title="Cinematic Background"
      />

      <div style={{ position: "relative", zIndex: 1000 }}>
        <AudioIndicator isSpeaking={isSpeaking} isListening={isListening} transcript={transcript} error={sttError} />
      </div>
      
      <div style={{ position: "relative", zIndex: 10, width: "100%", height: "100%", display: "flex", justifyContent: "center", alignItems: "center" }}>
        <SpotlightCard className="onboarding-container spotlight-override" spotlightColor="rgba(255, 255, 255, 0.1)">
          {/* Animated orb identity */}
          <div style={{ display: "flex", justifyContent: "center", marginBottom: "20px" }}>
             <img src="/auro_icon_haze.png" alt="AURA Logo" style={{ width: "80px", height: "80px", objectFit: "contain", filter: "drop-shadow(0 0 10px rgba(255,255,255,0.4))" }} />
          </div>

        <h2 className="onboarding-title" style={{ textAlign: "center", marginBottom: "8px" }}>
          <ShinyText text="Welcome to AURA" disabled={false} speed={3} className="" />
        </h2>
        <p className="onboarding-subtitle" style={{ textAlign: "center", opacity: 0.8 }}>
          Sign in with your face for a seamless, password-free experience.
        </p>

        {/* Primary face login */}
        <button
          className="onboarding-btn primary"
          onClick={() => setShowFaceLogin(true)}
          disabled={loading}
          style={{ width: "100%", textAlign: "center", justifyContent: "center", marginTop: "1rem" }}
        >
          {loading ? "Authenticating..." : <ShinyText text="🔐 Login with Face →" disabled={false} speed={3} className="" />}
        </button>

        {/* Divider */}
        <div style={{ display: "flex", alignItems: "center", gap: "12px", margin: "20px 0", color: "var(--text-disabled)", fontSize: "12px" }}>
          <div style={{ flex: 1, height: "1px", background: "rgba(255,255,255,0.08)" }} />
          <span>or</span>
          <div style={{ flex: 1, height: "1px", background: "rgba(255,255,255,0.08)" }} />
        </div>

        {/* Toggle manual */}
        <button
          className="onboarding-btn ghost"
          onClick={() => setShowManualLogin(!showManualLogin)}
          style={{ marginBottom: "12px", width: "100%", justifyContent: "center" }}
        >
          {showManualLogin ? "← Back to Face Login" : "Use Username & Password Instead"}
        </button>

        {/* Manual login form */}
        {showManualLogin && (
          <>
            <div className="voice-input-wrapper" style={{ marginBottom: "14px" }}>
              <label className="onboarding-input-label" htmlFor="login-username">Username</label>
              <div className="voice-input-container">
                <input
                  id="login-username"
                  type="text"
                  className="onboarding-input"
                  value={username}
                  onChange={handleUsernameChange}
                  placeholder="Your username…"
                  onKeyDown={(e) => e.key === "Enter" && handleLogin()}
                  autoComplete="username"
                />
              </div>
            </div>

            <div className="voice-input-wrapper" style={{ marginBottom: "14px" }}>
              <label className="onboarding-input-label" htmlFor="login-password">Password</label>
              <div className="voice-input-container">
                <input
                  id="login-password"
                  type="password"
                  className="onboarding-input"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="Your password…"
                  onKeyDown={(e) => e.key === "Enter" && handleLogin()}
                  autoComplete="current-password"
                />
              </div>
            </div>

            {hasFaceAuth && (
              <button className="onboarding-btn secondary" onClick={() => { setShowManualLogin(false); setShowFaceLogin(true); }} style={{ marginBottom: "10px", width: "100%", justifyContent: "center" }}>
                Or Use Face Login →
              </button>
            )}

            <button className="onboarding-btn primary" onClick={handleLogin} disabled={loading} style={{ marginTop: "8px", width: "100%", justifyContent: "center" }}>
              {loading ? "Signing in…" : <ShinyText text="Sign In →" disabled={false} speed={3} className="" />}
            </button>
          </>
        )}

        {error && <p className="onboarding-error" role="alert" style={{ marginTop: "12px" }}>{error}</p>}

          <button className="onboarding-btn ghost" onClick={onSignUp} style={{ marginTop: "20px", alignSelf: "center" }}>
            New user? Create an account
          </button>
        </SpotlightCard>
      </div>
    </div>
  );
};

export default LoginPage;