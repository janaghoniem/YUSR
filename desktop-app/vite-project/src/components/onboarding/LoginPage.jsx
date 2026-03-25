// LoginPage.jsx - Face-only login (no username needed)
import React, { useState, useEffect } from "react";
import FaceCapture from "../FaceCapture";

const LoginPage = ({ onLogin, onSignUp }) => {
  const [username, setUsername] = useState(() => {
    return localStorage.getItem("userName") || "";
  });
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [showFaceLogin, setShowFaceLogin] = useState(false);
  const [showManualLogin, setShowManualLogin] = useState(false);
  const [hasFaceAuth, setHasFaceAuth] = useState(false);

  // Check if user has face auth when username changes (for manual login)
  const checkFaceAuthStatus = async (username) => {
    if (username.length < 3) return;
    try {
      const res = await fetch(
        `http://localhost:8000/onboarding/face-status/${encodeURIComponent(username)}`
      );
      const data = await res.json();
      setHasFaceAuth(data.has_face_auth);
    } catch (error) {
      console.error("Failed to check face auth status:", error);
    }
  };

  const handleUsernameChange = (e) => {
    const value = e.target.value;
    setUsername(value);
    if (value.length >= 3) {
      checkFaceAuthStatus(value);
    } else {
      setHasFaceAuth(false);
    }
  };

  // Face-only login - no username needed!
  const handleFaceOnlyCapture = async (faceImage) => {
    setLoading(true);
    setError("");

    try {
      const res = await fetch("http://localhost:8000/onboarding/login-face-only", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ face_image: faceImage }),
      });

      const data = await res.json();

      if (!res.ok) {
        throw new Error(data.detail || "Face verification failed");
      }

      localStorage.setItem("userId", data.user_id);
      localStorage.setItem("userName", data.username);
      localStorage.setItem("onboardingComplete", "true");
      localStorage.setItem("authMethod", "face");

      if (data.preferences?.voice) {
        localStorage.setItem("ttsVoice", data.preferences.voice);
      }

      onLogin({
        userId: data.user_id,
        username: data.username,
        preferences: data.preferences,
      });

    } catch (err) {
      setError(err.message || "Face verification failed. Please try again.");
      setShowFaceLogin(false);
    } finally {
      setLoading(false);
    }
  };

  const handleFaceCapture = async (faceImage) => {
    setLoading(true);
    setError("");

    try {
      const res = await fetch("http://localhost:8000/onboarding/verify-face", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, face_image: faceImage }),
      });

      const data = await res.json();

      if (!res.ok) {
        throw new Error(data.detail || "Face verification failed");
      }

      localStorage.setItem("userId", data.user_id);
      localStorage.setItem("userName", data.username);
      localStorage.setItem("onboardingComplete", "true");
      localStorage.setItem("authMethod", "face");

      if (data.preferences?.voice) {
        localStorage.setItem("ttsVoice", data.preferences.voice);
      }

      onLogin({
        userId: data.user_id,
        username: data.username,
        preferences: data.preferences,
      });

    } catch (err) {
      setError(err.message || "Face verification failed. Please try again.");
      setShowFaceLogin(false);
    } finally {
      setLoading(false);
    }
  };

  const handleLogin = async () => {
    if (!username.trim() || !password.trim()) {
      setError("Please enter both username and password.");
      return;
    }

    setLoading(true);
    setError("");

    try {
      const res = await fetch("http://localhost:8000/onboarding/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });

      const data = await res.json();

      if (!res.ok) {
        throw new Error(data.detail || "Login failed");
      }

      localStorage.setItem("userId", data.user_id);
      localStorage.setItem("userName", data.username);
      localStorage.setItem("onboardingComplete", "true");
      localStorage.setItem("authMethod", "password");

      if (data.preferences?.voice) {
        localStorage.setItem("ttsVoice", data.preferences.voice);
      }

      onLogin({
        userId: data.user_id,
        username: data.username,
        preferences: data.preferences,
      });

    } catch (err) {
      setError(err.message || "Login failed. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  // Face-only login screen
  if (showFaceLogin) {
    return (
      <FaceCapture
        onCapture={showManualLogin ? handleFaceCapture : handleFaceOnlyCapture}
        onCancel={() => {
          setShowFaceLogin(false);
          setShowManualLogin(false);
        }}
        mode="login"
        username={showManualLogin ? username : undefined}
      />
    );
  }

  return (
    <div className="onboarding-overlay">
      <div className="onboarding-container">
        <div className="intro-orb" />

        <h2 className="onboarding-title">Welcome to AURA</h2>
        <p className="onboarding-subtitle">
          Sign in with your face for a seamless, password-free experience.
        </p>

        {/* Face-Only Login Button - Primary */}
        <button
          className="onboarding-btn primary"
          onClick={() => setShowFaceLogin(true)}
          style={{ marginTop: "24px", width: "100%" }}
        >
          🔐 Login with Face →
        </button>

        {/* Divider */}
        <div style={{ 
          margin: "20px 0", 
          display: "flex", 
          alignItems: "center",
          gap: "12px",
          color: "rgba(255,255,255,0.5)",
          fontSize: "12px"
        }}>
          <hr style={{ flex: 1, borderColor: "rgba(255,255,255,0.2)" }} />
          <span>or</span>
          <hr style={{ flex: 1, borderColor: "rgba(255,255,255,0.2)" }} />
        </div>

        {/* Manual Login Toggle */}
        <button
          className="onboarding-btn ghost"
          onClick={() => setShowManualLogin(!showManualLogin)}
          style={{ marginBottom: "16px" }}
        >
          {showManualLogin ? "← Back to Face Login" : "Use Username & Password Instead"}
        </button>

        {/* Manual Login Form (only if toggled) */}
        {showManualLogin && (
          <>
            <div className="voice-input-wrapper">
              <label className="onboarding-input-label">Username</label>
              <div className="voice-input-container">
                <input
                  type="text"
                  className="onboarding-input"
                  value={username}
                  onChange={handleUsernameChange}
                  placeholder="Your username..."
                  onKeyDown={(e) => e.key === "Enter" && handleLogin()}
                  autoComplete="username"
                />
              </div>
            </div>

            <div className="voice-input-wrapper" style={{ marginTop: "16px" }}>
              <label className="onboarding-input-label">Password</label>
              <div className="voice-input-container">
                <input
                  type="password"
                  className="onboarding-input"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="Your password..."
                  onKeyDown={(e) => e.key === "Enter" && handleLogin()}
                  autoComplete="current-password"
                />
              </div>
            </div>

            {/* Face Login Option for Manual Mode */}
            {hasFaceAuth && (
              <button
                className="onboarding-btn secondary"
                onClick={() => {
                  setShowManualLogin(false);
                  setShowFaceLogin(true);
                }}
                style={{ marginTop: "12px" }}
              >
                Or Use Face Login →
              </button>
            )}

            <button
              className="onboarding-btn primary"
              onClick={handleLogin}
              disabled={loading}
              style={{ marginTop: "24px" }}
            >
              {loading ? "Signing in..." : "Sign In →"}
            </button>
          </>
        )}

        {error && (
          <p className="onboarding-error" style={{ marginTop: "12px" }}>
            {error}
          </p>
        )}

        <button
          className="onboarding-btn ghost"
          onClick={onSignUp}
          style={{ marginTop: "24px" }}
        >
          New user? Create an account
        </button>
      </div>
    </div>
  );
};

export default LoginPage;