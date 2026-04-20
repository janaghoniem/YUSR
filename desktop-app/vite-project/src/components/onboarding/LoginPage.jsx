// LoginPage.jsx — Fixed:
//  • STT: no infinite-loop on "network" error — halts and shows text-mode fallback
//  • Uses useSpeechRecognition hook for clean lifecycle
//  • TTS: uses screenReader singleton
//  • Glassmorphic card style consistent with OnboardingPage (req 6)
//  • Egyptian Arabic support in voice commands

import React, { useState, useEffect, useRef, useCallback } from "react";
import FaceCapture from "../FaceCapture";
import SpotlightCard from "./SpotlightCard";
import ShinyText from "./ShinyText";
import Aurora from "./Aurora";
import AudioIndicator from "./AudioIndicator";
import screenReader from "../../utils/ScreenReader";
import { useSpeechRecognition } from "../../hooks/useSpeechRecognition";

const LoginPage = ({ onLogin, onSignUp }) => {
  const [username,       setUsername]       = useState(() => localStorage.getItem("userName") || "");
  const [password,       setPassword]       = useState("");
  const [error,          setError]          = useState("");
  const [loading,        setLoading]        = useState(false);
  const [showFaceLogin,  setShowFaceLogin]  = useState(false);
  const [showManualLogin,setShowManualLogin]= useState(false);
  const [hasFaceAuth,    setHasFaceAuth]    = useState(false);
  const [isSpeaking,     setIsSpeaking]     = useState(false);
  const sttLockedRef = useRef(false);  // true while TTS is speaking (avoid STT overlap)

  // ── STT — uses the safe hook that won't loop on network errors ────────────
  const handleSTTResult = useCallback((text) => {
    if (sttLockedRef.current) return;
    const lower = text.toLowerCase();

    if (showFaceLogin) {
      if (lower.match(/start|scan/))         { window.dispatchEvent(new CustomEvent("face-capture-start-scan"));   return; }
      if (lower.match(/use|confirm|photo/))  { window.dispatchEvent(new CustomEvent("face-capture-use-photo"));    return; }
      if (lower.match(/retake|again/))       { window.dispatchEvent(new CustomEvent("face-capture-retake-photo")); return; }
      if (lower.match(/back|cancel/))        { stt.stop(); setShowFaceLogin(false); setShowManualLogin(false);     return; }
      return;
    }

    if (lower.includes("face") || lower.includes("وجه")) {
      stt.stop(); setShowFaceLogin(true);
    } else if (lower.match(/password|manual|username|كلمة السر/)) {
      stt.stop(); setShowManualLogin(true);
    } else if (lower.match(/create|sign up|new account|حساب جديد/)) {
      stt.stop(); onSignUp();
    }
  }, [showFaceLogin, onSignUp]);

  const stt = useSpeechRecognition({
    lang: "en-US",           // handles EN; for AR recognition a bilingual tag can be used
    continuous: true,
    interimResults: false,
    maxNetworkRetries: 0,    // immediately stop on network error — no loop
    onResult: handleSTTResult,
  });

  // ── Initial TTS greeting ──────────────────────────────────────────────────
  useEffect(() => {
    setIsSpeaking(true);
    screenReader.speak(
      "Welcome to AURA. Sign in with your face for a seamless, password-free experience. Say 'face' to login with face, 'password' for manual login, or 'create' for a new account.",
      {
        onStart:    () => { sttLockedRef.current = true; },
        onComplete: () => {
          setIsSpeaking(false);
          sttLockedRef.current = false;
          if (!stt.networkUnavailable) stt.start();
        },
      }
    );
    return () => {
      screenReader.stop();
      stt.stop();
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Pause STT during speaking, resume after ───────────────────────────────
  useEffect(() => {
    if (isSpeaking) { stt.stop(); sttLockedRef.current = true; }
    else            { sttLockedRef.current = false; }
  }, [isSpeaking]);

  // ── Backend helpers ────────────────────────────────────────────────────────
  const checkFaceAuthStatus = async (u) => {
    if (u.length < 3) return;
    try {
      const res  = await fetch(`http://localhost:8000/onboarding/face-status/${encodeURIComponent(u)}`);
      const data = await res.json();
      setHasFaceAuth(data.has_face_auth);
    } catch { /* silent */ }
  };

  const handleUsernameChange = (e) => {
    const v = e.target.value;
    setUsername(v);
    if (v.length >= 3) checkFaceAuthStatus(v);
    else setHasFaceAuth(false);
  };

  const handleFaceOnlyCapture = async (faceImage) => {
    setLoading(true); setError("");
    try {
      const res = await fetch("http://localhost:8000/onboarding/login-face-only", {
        method: "POST", headers: { "Content-Type": "application/json" },
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
    } catch (err) {
      setError(err.message || "Face verification failed. Please try again.");
      setShowFaceLogin(false);
    } finally {
      setLoading(false);
    }
  };

  const handleFaceCapture = async (faceImage) => {
    setLoading(true); setError("");
    try {
      const res = await fetch("http://localhost:8000/onboarding/verify-face", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, face_image: faceImage }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Face verification failed");
      localStorage.setItem("userId", data.user_id);
      localStorage.setItem("userName", data.username);
      localStorage.setItem("onboardingComplete", "true");
      if (data.preferences?.voice) localStorage.setItem("ttsVoice", data.preferences.voice);
      onLogin({ userId: data.user_id, username: data.username, preferences: data.preferences });
    } catch (err) {
      setError(err.message || "Face verification failed. Please try again.");
      setShowFaceLogin(false);
    } finally {
      setLoading(false);
    }
  };

  const handleLogin = async () => {
    if (!username) { setError("Please enter your username."); return; }
    setLoading(true); setError("");
    try {
      const res  = await fetch("http://localhost:8000/onboarding/login", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Login failed");
      localStorage.setItem("userId", data.user_id);
      localStorage.setItem("userName", data.username);
      localStorage.setItem("onboardingComplete", "true");
      if (data.preferences?.voice) localStorage.setItem("ttsVoice", data.preferences.voice);
      onLogin({ userId: data.user_id, username: data.username, preferences: data.preferences });
    } catch (err) {
      setError(err.message || "Login failed. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  // ── Shared titlebar for auth pages ────────────────────────────────────────
  const Titlebar = () => (
    <div className="titlebar" style={{ position: "absolute", top: 0, left: 0, width: "100%", zIndex: 100, backgroundColor: "transparent" }}>
      <div className="titlebar-drag">
        <span className="titlebar-title" style={{ paddingLeft: 10, opacity: 0.8 }}>AURA</span>
      </div>
      <div className="titlebar-buttons">
        <button className="titlebar-btn" onClick={() => window.electronAPI?.minimizeWindow?.()}>—</button>
        <button className="titlebar-btn" onClick={() => window.electronAPI?.maximizeWindow?.()}>□</button>
        <button className="titlebar-btn titlebar-close" onClick={() => window.electronAPI?.closeWindow?.()}>X</button>
      </div>
    </div>
  );

  // ── Face login view ────────────────────────────────────────────────────────
  if (showFaceLogin) {
    return (
      <div className="onboarding-overlay" style={{ position: "fixed", inset: 0 }}>
        <Titlebar />
        <Aurora />
        <iframe src="/aura-cinematic-bg.html" sandbox="allow-scripts allow-same-origin"
          style={{ position: "absolute", width: "100%", height: "100%", border: "none", pointerEvents: "none", zIndex: 0 }}
          title="Cinematic Background"
        />
        <div style={{ position: "relative", zIndex: 1000 }}>
          <AudioIndicator isSpeaking={isSpeaking} isListening={stt.listening} transcript={stt.transcript} />
        </div>
        <div style={{ position: "relative", zIndex: 10, width: "100%", height: "100%", display: "flex", justifyContent: "center", alignItems: "center", padding: 20 }}>
          <SpotlightCard className="onboarding-container spotlight-override" spotlightColor="rgba(255,255,255,0.08)">
            <button
              className="onboarding-btn ghost"
              onClick={() => { setShowFaceLogin(false); setShowManualLogin(false); }}
              style={{ alignSelf: "flex-start", marginBottom: "0.5rem", padding: "8px 12px", width: "auto" }}
            >
              <ShinyText text="← Back to Login" speed={3} />
            </button>
            <FaceCapture
              onCapture={showManualLogin ? handleFaceCapture : handleFaceOnlyCapture}
              onCancel={() => { setShowFaceLogin(false); setShowManualLogin(false); }}
              mode="login"
              username={showManualLogin ? username : undefined}
              onSpeakStart={() => { setIsSpeaking(true); stt.stop(); }}
              onSpeakEnd={() => {
                setIsSpeaking(false);
                if (!stt.networkUnavailable) stt.start();
              }}
            />
          </SpotlightCard>
        </div>
      </div>
    );
  }

  // ── Main login view ────────────────────────────────────────────────────────
  return (
    <div className="onboarding-overlay" style={{ position: "fixed", inset: 0 }}>
      <Aurora />
      <iframe src="/aura-cinematic-bg.html"
        style={{ position: "absolute", width: "100%", height: "100%", border: "none", pointerEvents: "none", zIndex: 0 }}
        title="Cinematic Background"
      />
      <div style={{ position: "relative", zIndex: 1000 }}>
        <AudioIndicator isSpeaking={isSpeaking} isListening={stt.listening} transcript={stt.transcript} />
      </div>

      <div style={{
        position: "relative", zIndex: 10, width: "100%", height: "100%",
        display: "flex", justifyContent: "center", alignItems: "center", padding: 20,
      }}>
        <SpotlightCard className="onboarding-container spotlight-override" spotlightColor="rgba(255,255,255,0.08)">
          {/* Logo */}
          <div style={{ display: "flex", justifyContent: "center", marginBottom: 20 }}>
            <img src="/auro_icon_haze.png" alt="AURA Logo"
              style={{ width: 80, height: 80, objectFit: "contain", filter: "drop-shadow(0 0 10px rgba(255,255,255,0.4))" }}
            />
          </div>

          <h2 className="onboarding-title" style={{ textAlign: "center", marginBottom: 8 }}>
            <ShinyText text="Welcome to AURA" speed={3} />
          </h2>
          <p className="onboarding-subtitle" style={{ textAlign: "center", opacity: 0.8 }}>
            Sign in with your face for a seamless, password-free experience.
          </p>

          {/* STT network-unavailable notice */}
          {stt.networkUnavailable && (
            <p className="onboarding-hint" style={{ textAlign: "center", marginBottom: 8 }}>
              🎙️ Voice control unavailable offline — use buttons below.
            </p>
          )}

          {/* Primary face login */}
          <button
            className="onboarding-btn primary"
            onClick={() => setShowFaceLogin(true)}
            disabled={loading}
            style={{ width: "100%", textAlign: "center", justifyContent: "center", marginTop: "1rem" }}
          >
            {loading ? "Authenticating..." : <ShinyText text="🔐 Login with Face →" speed={3} />}
          </button>

          {/* Divider */}
          <div style={{ display: "flex", alignItems: "center", gap: 12, margin: "20px 0", color: "var(--text-disabled)", fontSize: 12 }}>
            <div style={{ flex: 1, height: 1, background: "rgba(255,255,255,0.08)" }} />
            <span>or</span>
            <div style={{ flex: 1, height: 1, background: "rgba(255,255,255,0.08)" }} />
          </div>

          <button
            className="onboarding-btn ghost"
            onClick={() => setShowManualLogin(!showManualLogin)}
            style={{ marginBottom: 12, width: "100%", justifyContent: "center" }}
          >
            {showManualLogin ? "← Back to Face Login" : "Use Username & Password Instead"}
          </button>

          {showManualLogin && (
            <>
              <div className="voice-input-wrapper" style={{ marginBottom: 14 }}>
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
              <div className="voice-input-wrapper" style={{ marginBottom: 14 }}>
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
                <button className="onboarding-btn secondary"
                  onClick={() => { setShowManualLogin(false); setShowFaceLogin(true); }}
                  style={{ marginBottom: 10, width: "100%", justifyContent: "center" }}
                >
                  Or Use Face Login →
                </button>
              )}
              <button className="onboarding-btn primary" onClick={handleLogin} disabled={loading}
                style={{ marginTop: 8, width: "100%", justifyContent: "center" }}
              >
                {loading ? "Signing in…" : <ShinyText text="Sign In →" speed={3} />}
              </button>
            </>
          )}

          {error && <p className="onboarding-error" role="alert" style={{ marginTop: 12 }}>{error}</p>}

          <button className="onboarding-btn ghost" onClick={onSignUp} style={{ marginTop: 20, alignSelf: "center" }}>
            New user? Create an account
          </button>
        </SpotlightCard>
      </div>
    </div>
  );
};

export default LoginPage;