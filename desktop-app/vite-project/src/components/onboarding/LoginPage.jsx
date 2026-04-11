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
  
  const mediaRecorderRef = useRef(null);
  const audioChunksRef = useRef([]);
  const silenceFrameRef = useRef(null);
  const noSpeechTimeoutRef = useRef(null);
  const audioContextRef = useRef(null);

  const stopRecording = () => {
    if (mediaRecorderRef.current && mediaRecorderRef.current.state === "recording") {
      mediaRecorderRef.current.stop();
    }
    setIsListening(false);
    if (silenceFrameRef.current) cancelAnimationFrame(silenceFrameRef.current);
    if (noSpeechTimeoutRef.current) clearTimeout(noSpeechTimeoutRef.current);
    if (audioContextRef.current) {
      try { audioContextRef.current.close(); } catch (e) {}
      audioContextRef.current = null;
    }
  };

  const handleTranscript = (text) => {
    if (!text) return;
    const lower = text.toLowerCase();
    if (lower.includes("face")) {
      intentionalStop.current = true;
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

  const processAudio = async (blob) => {
    const reader = new FileReader();
    reader.readAsDataURL(blob);
    reader.onloadend = async () => {
      const base64 = reader.result.split(",")[1];
      try {
        const res = await fetch("http://localhost:8000/transcribe", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ audio_data: base64, session_id: "login", user_id: "login" }),
        });
        const data = await res.json();
        if (res.ok && data.transcript) {
          setTranscript(data.transcript);
          handleTranscript(data.transcript);
        }
      } catch (err) {
        console.error("Transcription error:", err);
      } finally {
        if (!intentionalStop.current) startRecording();
      }
    };
  };

  const startRecording = async () => {
    if (intentionalStop.current) return;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      mediaRecorderRef.current = recorder;
      audioChunksRef.current = [];

      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) audioChunksRef.current.push(e.data);
      };

      recorder.onstop = () => {
        const blob = new Blob(audioChunksRef.current, { type: "audio/webm" });
        stream.getTracks().forEach((t) => t.stop());
        if (!intentionalStop.current) processAudio(blob);
      };

      recorder.start();
      setIsListening(true);
      setSttError("");

      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      const audioCtx = new AudioCtx();
      audioContextRef.current = audioCtx;
      const sourceNode = audioCtx.createMediaStreamSource(stream);
      const analyser = audioCtx.createAnalyser();
      analyser.fftSize = 2048;
      sourceNode.connect(analyser);
      const bufferLength = analyser.fftSize;
      const dataArray = new Uint8Array(bufferLength);
      let silentStart = null;

      const checkSilence = () => {
        analyser.getByteTimeDomainData(dataArray);
        let sum = 0;
        for (let i = 0; i < bufferLength; i++) {
          sum += Math.abs(dataArray[i] - 128);
        }
        const avg = sum / bufferLength;

        if (avg < 3) {
          if (!silentStart) silentStart = Date.now();
          else if (Date.now() - silentStart > 3000) {
            if (mediaRecorderRef.current && mediaRecorderRef.current.state === 'recording') {
              mediaRecorderRef.current.stop();
            }
          }
        } else {
          silentStart = null;
        }

        if (mediaRecorderRef.current && mediaRecorderRef.current.state === 'recording') {
          silenceFrameRef.current = requestAnimationFrame(checkSilence);
        }
      };

      silenceFrameRef.current = requestAnimationFrame(checkSilence);

      noSpeechTimeoutRef.current = setTimeout(() => {
        if (mediaRecorderRef.current && mediaRecorderRef.current.state === 'recording') {
          mediaRecorderRef.current.stop();
        }
      }, 10000);

    } catch (err) {
      console.error("Mic access denied:", err);
      setSttError("Microphone access denied.");
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
        <AudioIndicator isSpeaking={isSpeaking} isListening={isListening} transcript={transcript} error={sttError} />

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

        <div style={{ position: "relative", zIndex: 10, width: "100%", height: "100%", display: "flex", justifyContent: "center", alignItems: "center" }}>
          <SpotlightCard className="onboarding-container spotlight-override" spotlightColor="rgba(255, 255, 255, 0.1)">
            <button 
              className="onboarding-btn ghost" 
              onClick={() => { setShowFaceLogin(false); setShowManualLogin(false); }}
              style={{ alignSelf: "flex-start", marginBottom: "1rem", padding: "8px 0" }}
            >
              <ShinyText text="← Back to Login" disabled={false} speed={3} className="" />
            </button>
            <FaceCapture
              onCapture={showManualLogin ? handleFaceCapture : handleFaceOnlyCapture}
              onCancel={() => { setShowFaceLogin(false); setShowManualLogin(false); }}
              mode="login"
              username={showManualLogin ? username : undefined}
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
      <AudioIndicator isSpeaking={isSpeaking} isListening={isListening} transcript={transcript} error={sttError} />

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