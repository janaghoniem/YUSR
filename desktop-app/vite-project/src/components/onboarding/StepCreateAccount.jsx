// StepCreateAccount.jsx - With Fixed User ID Management
import React, { useState, useCallback, useRef, useEffect } from "react";
import VoiceInput from "../shared/VoiceInput";
import FaceCapture from "../FaceCapture";

const StepCreateAccount = ({ onSubmit, data, setData, isSubmitting }) => {
  const [errors, setErrors] = useState({});
  const [usernameAvailable, setUsernameAvailable] = useState(null);
  const [checking, setChecking] = useState(false);
  const [showFaceRegistration, setShowFaceRegistration] = useState(false);
  const [faceRegistered, setFaceRegistered] = useState(false);
  const debounceTimerRef = useRef(null);
  
  // Get the correct userId from localStorage (which should be synced by OnboardingPage)
  const [userId] = useState(() => {
    const stored = localStorage.getItem("userId");
    if (!stored) {
      console.error("[StepCreateAccount] No userId found in localStorage!");
      // Generate a temporary one if not found
      const tempId = `temp_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
      console.log("[StepCreateAccount] Generated temporary userId:", tempId);
      localStorage.setItem("userId", tempId);
      return tempId;
    }
    console.log("[StepCreateAccount] Using userId from localStorage:", stored);
    return stored;
  });

  // Log when component mounts to debug
  useEffect(() => {
    console.log("[StepCreateAccount] Current state:", {
      userId: userId,
      username: data.username,
      faceRegistered: faceRegistered,
      usernameAvailable: usernameAvailable
    });
  }, [userId, data.username, faceRegistered, usernameAvailable]);

  const checkUsername = useCallback(async (username) => {
    if (username.length < 3) {
      setUsernameAvailable(null);
      return;
    }
    
    setChecking(true);
    try {
      const res = await fetch(
        `http://localhost:8000/onboarding/check-username?username=${encodeURIComponent(username)}`
      );
      const result = await res.json();
      setUsernameAvailable(result.available);
      
      if (result.available) {
        setErrors(prev => ({ ...prev, username: null }));
      }
    } catch (error) {
      console.error("Username check failed:", error);
      setUsernameAvailable(null);
    } finally {
      setChecking(false);
    }
  }, []);

  const handleUsernameChange = (val) => {
    setData({ ...data, username: val });
    
    if (usernameAvailable !== null) {
      setUsernameAvailable(null);
    }
    
    if (errors.username) {
      setErrors(prev => ({ ...prev, username: null }));
    }
    
    if (debounceTimerRef.current) {
      clearTimeout(debounceTimerRef.current);
    }
    
    debounceTimerRef.current = setTimeout(() => {
      checkUsername(val);
    }, 500);
  };

  const validate = () => {
    const errs = {};
    if (!data.username || data.username.length < 3) {
      errs.username = "Username must be at least 3 characters.";
    } else if (usernameAvailable === false) {
      errs.username = "Username is already taken.";
    }
    
    if (!faceRegistered) {
      errs.face = "Please register your face for secure login.";
    }
    
    return errs;
  };

  const handleFaceCapture = async (faceImage) => {
    try {
      // Get the current userId from localStorage (should be set by OnboardingPage)
      let currentUserId = localStorage.getItem("userId");
      
      // If this is a temp ID, replace it with a proper one
      if (!currentUserId || currentUserId.startsWith('temp_')) {
        currentUserId = `user_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
        console.log("[StepCreateAccount] Replaced temp userId with:", currentUserId);
        localStorage.setItem("userId", currentUserId);
      }
      
      console.log("[StepCreateAccount] Registering face for:", {
        username: data.username,
        user_id: currentUserId
      });
      
      const response = await fetch("http://localhost:8000/onboarding/register-face", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: data.username,
          user_id: currentUserId,
          face_image: faceImage,
        }),
      });

      if (response.ok) {
        const result = await response.json();
        console.log("[StepCreateAccount] Face registration successful:", result);
        setFaceRegistered(true);
        setShowFaceRegistration(false);
        setErrors({ ...errors, face: null });
      } else {
        const error = await response.json();
        console.error("[StepCreateAccount] Face registration failed:", error);
        setErrors({ ...errors, face: error.detail || "Face registration failed" });
      }
    } catch (error) {
      console.error("[StepCreateAccount] Face registration error:", error);
      setErrors({ ...errors, face: "Failed to register face. Please try again." });
    }
  };

  const handleSubmit = () => {
    const errs = validate();
    if (Object.keys(errs).length > 0) {
      setErrors(errs);
      return;
    }
    
    // Final check: ensure we have a valid userId
    let finalUserId = localStorage.getItem("userId");
    if (!finalUserId || finalUserId.startsWith('temp_')) {
      finalUserId = `user_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
      localStorage.setItem("userId", finalUserId);
      console.log("[StepCreateAccount] Generated final userId:", finalUserId);
    }
    
    console.log("[StepCreateAccount] Submitting account with userId:", finalUserId);
    setErrors({});
    onSubmit();
  };

  if (showFaceRegistration) {
    return (
      <FaceCapture
        onCapture={handleFaceCapture}
        onCancel={() => setShowFaceRegistration(false)}
        mode="signup"
        username={data.username}
      />
    );
  }

  return (
    <div className="onboarding-step">
      <h2 className="onboarding-title">Create your account</h2>
      <p className="onboarding-subtitle">
        Your account links your chats, memory, and preferences across sessions.
      </p>

      <VoiceInput
        label="Username"
        value={data.username}
        onChange={handleUsernameChange}
        placeholder="Choose a username..."
      />
      {checking && <p className="onboarding-hint">Checking availability...</p>}
      {usernameAvailable === true && data.username.length >= 3 && (
        <p className="onboarding-hint success">✓ Username is available</p>
      )}
      {usernameAvailable === false && data.username.length >= 3 && (
        <p className="onboarding-error">✗ Username already taken</p>
      )}
      {errors.username && <p className="onboarding-error">{errors.username}</p>}

      {/* Email field */}
      <div className="voice-input-wrapper" style={{ marginTop: "16px" }}>
        <label className="onboarding-input-label">Email (optional)</label>
        <div className="voice-input-container">
          <input
            type="email"
            className="onboarding-input"
            value={data.email || ""}
            onChange={(e) => setData({ ...data, email: e.target.value })}
            placeholder="your@email.com"
            autoComplete="email"
          />
        </div>
      </div>

      {/* Face Registration Section */}
      <div className="face-registration-section" style={{ marginTop: "24px" }}>
        <label className="onboarding-input-label">Face Authentication</label>
        {!faceRegistered ? (
          <button
            className="onboarding-btn secondary"
            onClick={() => setShowFaceRegistration(true)}
            style={{ width: "100%" }}
            disabled={!data.username || usernameAvailable !== true}
          >
            Register Your Face →
          </button>
        ) : (
          <div className="face-success-message">
            <p className="onboarding-hint success">✓ Face registered successfully!</p>
            <p className="onboarding-hint">
              You'll be able to log in using your face instead of a password.
            </p>
          </div>
        )}
        {errors.face && <p className="onboarding-error">{errors.face}</p>}
      </div>

      {/* Optional password fallback */}
      <div className="voice-input-wrapper" style={{ marginTop: "16px" }}>
        <label className="onboarding-input-label">
          Password (Optional - for fallback)
        </label>
        <div className="voice-input-container">
          <input
            type="password"
            className="onboarding-input"
            value={data.password}
            onChange={(e) => setData({ ...data, password: e.target.value })}
            placeholder="Create a password (optional)..."
            autoComplete="new-password"
          />
        </div>
        <p className="onboarding-hint">
          Password is optional if you're using face authentication.
        </p>
      </div>

      <button
        className="onboarding-btn primary"
        onClick={handleSubmit}
        disabled={isSubmitting || !faceRegistered}
        style={{ marginTop: "24px" }}
      >
        {isSubmitting ? "Creating account..." : "Create Account & Start →"}
      </button>
    </div>
  );
};

export default StepCreateAccount;