import React, { useState } from "react";

const LoginPage = ({ onLogin, onSignUp }) => {
  const [username, setUsername] = useState(() => {
    return localStorage.getItem("userName") || "";
  });
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

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

  return (
    <div className="onboarding-overlay">
      <div className="onboarding-container">
        <div className="intro-orb" />

        <h2 className="onboarding-title">Welcome back</h2>
        <p className="onboarding-subtitle">
          Sign in to access your chats, memory, and preferences from any device.
        </p>

        {/* Username */}
        <div className="voice-input-wrapper">
          <label className="onboarding-input-label">Username</label>
          <div className="voice-input-container">
            <input
              type="text"
              className="onboarding-input"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="Your username..."
              onKeyDown={(e) => e.key === "Enter" && handleLogin()}
              autoComplete="username"
            />
          </div>
        </div>

        {/* Password */}
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

        {error && (
          <p className="onboarding-error" style={{ marginTop: "12px" }}>
            {error}
          </p>
        )}

        <button
          className="onboarding-btn primary"
          onClick={handleLogin}
          disabled={loading}
          style={{ marginTop: "24px" }}
        >
          {loading ? "Signing in..." : "Sign In →"}
        </button>

        <button
          className="onboarding-btn ghost"
          onClick={onSignUp}
          style={{ marginTop: "12px" }}
        >
          New user? Create an account
        </button>
      </div>
    </div>
  );
};

export default LoginPage;