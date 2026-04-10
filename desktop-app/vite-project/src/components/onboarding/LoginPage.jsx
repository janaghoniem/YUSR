// LoginPage.jsx — Redesigned with AURA Design System v3.1
import React, { useState } from "react";
import FaceCapture from "../FaceCapture";

const LoginPage = ({ onLogin, onSignUp }) => {
  const [username, setUsername] = useState(() => localStorage.getItem("userName") || "");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [showFaceLogin, setShowFaceLogin] = useState(false);
  const [showManualLogin, setShowManualLogin] = useState(false);
  const [hasFaceAuth, setHasFaceAuth] = useState(false);

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
      <div className="onboarding-overlay">
        <FaceCapture
          onCapture={showManualLogin ? handleFaceCapture : handleFaceOnlyCapture}
          onCancel={() => { setShowFaceLogin(false); setShowManualLogin(false); }}
          mode="login"
          username={showManualLogin ? username : undefined}
        />
      </div>
    );
  }

  return (
    <div className="onboarding-overlay">
      <div className="onboarding-container">
        {/* Animated orb identity */}
        <div className="intro-orb" aria-hidden="true" />

        <h2 className="onboarding-title">Welcome back</h2>
        <p className="onboarding-subtitle">
          Sign in with your face for a seamless, password-free experience.
        </p>

        {/* Primary face login */}
        <button
          className="onboarding-btn primary"
          onClick={() => setShowFaceLogin(true)}
          disabled={loading}
          style={{ width: "100%", textAlign: "center", justifyContent: "center" }}
        >
          🔐 Login with Face →
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
          style={{ marginBottom: "12px" }}
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
              <button className="onboarding-btn secondary" onClick={() => { setShowManualLogin(false); setShowFaceLogin(true); }} style={{ marginBottom: "10px" }}>
                Or Use Face Login →
              </button>
            )}

            <button className="onboarding-btn primary" onClick={handleLogin} disabled={loading} style={{ marginTop: "8px" }}>
              {loading ? "Signing in…" : "Sign In →"}
            </button>
          </>
        )}

        {error && <p className="onboarding-error" role="alert" style={{ marginTop: "12px" }}>{error}</p>}

        <button className="onboarding-btn ghost" onClick={onSignUp} style={{ marginTop: "20px", alignSelf: "center" }}>
          New user? Create an account
        </button>
      </div>
    </div>
  );
};

export default LoginPage;