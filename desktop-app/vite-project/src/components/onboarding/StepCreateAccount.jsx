import React, { useState } from "react";
import VoiceInput from "../shared/VoiceInput";

const StepCreateAccount = ({ onSubmit, data, setData, isSubmitting }) => {
  const [errors, setErrors] = useState({});
  const [usernameAvailable, setUsernameAvailable] = useState(null);
  const [checking, setChecking] = useState(false);

  const checkUsername = async (username) => {
    if (username.length < 3) return;
    setChecking(true);
    try {
      const res = await fetch(
        `http://localhost:8000/onboarding/check-username?username=${encodeURIComponent(username)}`
      );
      const result = await res.json();
      setUsernameAvailable(result.available);
    } catch {
      setUsernameAvailable(null);
    } finally {
      setChecking(false);
    }
  };

  const validate = () => {
    const errs = {};
    if (!data.username || data.username.length < 3)
      errs.username = "Username must be at least 3 characters.";
    if (usernameAvailable === false) errs.username = "Username is already taken.";
    if (!data.password || data.password.length < 6)
      errs.password = "Password must be at least 6 characters.";
    if (data.password !== data.confirmPassword)
      errs.confirmPassword = "Passwords do not match.";
    return errs;
  };

  const handleSubmit = () => {
    const errs = validate();
    if (Object.keys(errs).length > 0) {
      setErrors(errs);
      return;
    }
    setErrors({});
    onSubmit();
  };

  return (
    <div className="onboarding-step">
      <h2 className="onboarding-title">Create your account</h2>
      <p className="onboarding-subtitle">
        Your account links your chats, memory, and preferences across sessions.
      </p>

      <VoiceInput
        label="Username"
        value={data.username}
        onChange={(val) => {
          setData({ ...data, username: val });
          checkUsername(val);
        }}
        placeholder="Choose a username..."
      />
      {checking && <p className="onboarding-hint">Checking availability...</p>}
      {usernameAvailable === true && (
        <p className="onboarding-hint success">✓ Username is available</p>
      )}
      {usernameAvailable === false && (
        <p className="onboarding-error">✗ Username already taken</p>
      )}
      {errors.username && <p className="onboarding-error">{errors.username}</p>}

      <div className="voice-input-wrapper" style={{ marginTop: "16px" }}>
        <label className="onboarding-input-label">Password</label>
        <div className="voice-input-container">
          <input
            type="password"
            className="onboarding-input"
            value={data.password}
            onChange={(e) => setData({ ...data, password: e.target.value })}
            placeholder="Create a password..."
            autoComplete="new-password"
          />
        </div>
      </div>
      {errors.password && <p className="onboarding-error">{errors.password}</p>}

      <div className="voice-input-wrapper" style={{ marginTop: "16px" }}>
        <label className="onboarding-input-label">Confirm Password</label>
        <div className="voice-input-container">
          <input
            type="password"
            className="onboarding-input"
            value={data.confirmPassword}
            onChange={(e) => setData({ ...data, confirmPassword: e.target.value })}
            placeholder="Repeat your password..."
            autoComplete="new-password"
          />
        </div>
      </div>
      {errors.confirmPassword && (
        <p className="onboarding-error">{errors.confirmPassword}</p>
      )}

      <button
        className="onboarding-btn primary"
        onClick={handleSubmit}
        disabled={isSubmitting}
        style={{ marginTop: "24px" }}
      >
        {isSubmitting ? "Creating account..." : "Create Account & Start →"}
      </button>
    </div>
  );
};

export default StepCreateAccount;