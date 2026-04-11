// components/FaceCapture.jsx
import React, { useRef, useState, useCallback, useEffect } from 'react';
import Webcam from 'react-webcam';
import { ArrowLeft } from 'lucide-react';
import ShinyText from './onboarding/ShinyText';
import screenReader from '../utils/ScreenReader';

const FaceCapture = ({ onCapture, onCancel, mode = "signup", username, onSpeakStart, onSpeakEnd }) => {
  const webcamRef = useRef(null);
  const [capturing, setCapturing] = useState(false);
  const [error, setError] = useState("");
  const [countdown, setCountdown] = useState(3);
  const [preview, setPreview] = useState(null);
  const [isCheckingCamera, setIsCheckingCamera] = useState(true);

  // Helper to coordinate speech with the parent's STT recorder
  const speakWithParentCoordination = (text) => {
    if (onSpeakStart) onSpeakStart();
    screenReader.speak(text, {
      onComplete: () => {
        if (onSpeakEnd) onSpeakEnd();
      }
    });
  };

  // Read instructions out loud on component load
  useEffect(() => {
    const textToSpeak = mode === "signup"
      ? "Register your face. Look at the camera. We will capture your face for secure login. You can say 'start face scan' or 'back to login'. Your face data is encrypted and immediately analyzed inside a secure environment. We never store raw images."
      : `Verify your Identity. Look at the camera, ${username || "User"}, to verify your identity. You can say 'start face scan' or 'back to login'. Your face data is encrypted and immediately analyzed inside a secure environment. We never store raw images.`;

    speakWithParentCoordination(textToSpeak);

    return () => {
      screenReader.stop();
    };
  }, [mode, username]);

  // Check if webcam is available
  useEffect(() => {
    const checkCamera = async () => {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ video: true });
        stream.getTracks().forEach(track => track.stop());
        setIsCheckingCamera(false);
      } catch (err) {
        setError("Camera access denied. Please enable camera permissions.");
        setIsCheckingCamera(false);
        speakWithParentCoordination("Camera access denied. Please enable camera permissions.");
      }
    };
    checkCamera();
  }, []);

  // Listen for STT triggered face capture window events
  useEffect(() => {
    const handleStartScanEvent = () => {
      if (!capturing && !preview) {
        startCapture();
      }
    };
    const handleUsePhotoEvent = () => {
      if (preview && !capturing) {
        speakWithParentCoordination("Photo confirmed. Processing. Please wait.");
        onCapture(preview);
      }
    };
    const handleRetakePhotoEvent = () => {
      if (preview && !capturing) {
        retryCapture();
      }
    };

    window.addEventListener('face-capture-start-scan', handleStartScanEvent);
    window.addEventListener('face-capture-use-photo', handleUsePhotoEvent);
    window.addEventListener('face-capture-retake-photo', handleRetakePhotoEvent);
    return () => {
      window.removeEventListener('face-capture-start-scan', handleStartScanEvent);
      window.removeEventListener('face-capture-use-photo', handleUsePhotoEvent);
      window.removeEventListener('face-capture-retake-photo', handleRetakePhotoEvent);
    };
  }, [capturing, preview, onCapture]);

  const captureImage = useCallback(() => {
    if (webcamRef.current) {
      const imageSrc = webcamRef.current.getScreenshot();
      setPreview(imageSrc);
      return imageSrc;
    }
    return null;
  }, []);

  const startCapture = () => {
    speakWithParentCoordination("Starting scan. Three. Two. One.");
    setCapturing(true);
    setError("");
    let count = 3;
    setCountdown(count);
    
    const interval = setInterval(() => {
      count--;
      setCountdown(count);
      
      if (count === 0) {
        clearInterval(interval);
        const imageData = captureImage();
        if (imageData) {
          setCapturing(false); // CRITICAL FIX: Unlock state so 'use this photo' event works
          speakWithParentCoordination("Image captured successfully. Would you like to use this photo or retake it?");
        } else {
          setError("Failed to capture image. Please try again.");
          speakWithParentCoordination("Failed to capture image. Please try again.");
          setCapturing(false);
        }
      }
    }, 1000);
  };

  const retryCapture = () => {
    speakWithParentCoordination("Ready to retake photo.");
    setCapturing(false);
    setPreview(null);
    setError("");
  };

  if (isCheckingCamera) {
    return (
      <div style={{ display: "flex", justifyContent: "center", alignItems: "center", height: "100%", width: "100%", padding: "20px" }}>
        <ShinyText text="Checking camera..." disabled={false} speed={3} />
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", width: "100%", animation: "faceFadeIn 0.5s ease-out" }}>
      <style>{`
        @keyframes faceFadeIn {
          0% { opacity: 0; transform: translateY(10px); }
          100% { opacity: 1; transform: translateY(0); }
        }
        .camera-wrapper {
          position: relative;
          width: 250px;
          height: 250px;
          border-radius: 50%;
          overflow: hidden;
          margin: 1rem auto;
          box-shadow: 0 0 30px rgba(255, 255, 255, 0.1);
          border: 2px solid rgba(255,255,255,0.2);
          transition: all 0.3s ease;
          flex-shrink: 0;
        }
        .camera-wrapper.capturing {
          border-color: rgba(255, 255, 255, 0.8);
          box-shadow: 0 0 50px rgba(255, 255, 255, 0.6);
          transform: scale(1.05);
        }
        .webcam-preview {
          width: 100%;
          height: 100%;
          object-fit: cover;
        }
        .countdown-overlay {
          position: absolute;
          top: 0; left: 0; right: 0; bottom: 0;
          display: flex;
          align-items: center;
          justify-content: center;
          background: rgba(0,0,0,0.55);
          color: white;
          font-size: 5.5rem;
          font-weight: bold;
          backdrop-filter: blur(4px);
        }
        .disclaimer-box {
          background: rgba(255, 255, 255, 0.12);
          border: 1px solid rgba(255, 255, 255, 0.25);
          border-radius: 8px;
          padding: 12px 16px;
          margin-top: 24px;
          font-size: 0.9rem;
          line-height: 1.4;
          text-align: center;
          color: #ffffff;
          max-width: 95%;
          box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
        }
      `}</style>
      
      <h3 className="onboarding-title" style={{ textAlign: "center", marginBottom: "8px" }} aria-live="polite">
        {mode === "signup" ? (
          <ShinyText text="Register Your Face" disabled={false} speed={3} />
        ) : (
          <ShinyText text="Verify Your Identity" disabled={false} speed={3} />
        )}
      </h3>
      
      <p className="onboarding-subtitle" style={{ textAlign: "center", opacity: 0.8, marginBottom: "0.5rem" }}>
        {mode === "signup" 
          ? "Look at the camera. We'll capture your face for secure login."
          : "Look at the camera to verify your identity."}
      </p>

      {/* Back button (matching OnboardingPage styling, prominent edge positioning) */}
      <button
        onClick={onCancel}
        className="onboarding-back-btn tooltip-trigger"
        aria-label="Go back to login screen"
        style={{
          position: "absolute",
          top: "40px", // Below titlebar matches OnboardingPage
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
      
      <div className={`camera-wrapper ${capturing ? "capturing" : ""}`}>
        {!preview ? (
          <>
            <Webcam
              ref={webcamRef}
              screenshotFormat="image/jpeg"
              videoConstraints={{
                width: 320,
                height: 320,
                facingMode: "user"
              }}
              className="webcam-preview"
              mirrored={true}
            />
            {capturing && (
              <div className="countdown-overlay">
                <div>{countdown}</div>
              </div>
            )}
          </>
        ) : (
          <img src={preview} alt="Captured face" className="webcam-preview" />
        )}
      </div>
      
      {error && <p className="onboarding-error" style={{ textAlign: "center", marginBottom: "1rem" }}>{error}</p>}
      
      <div style={{ display: "flex", flexDirection: "column", gap: "10px", width: "100%", maxWidth: "300px", marginTop: "1rem" }}>
        {!capturing && !preview && (
          <button 
            className="onboarding-btn primary"
            onClick={startCapture}
            style={{ width: "100%", justifyContent: "center", padding: "12px" }}
          >
            Start Face Scan →
          </button>
        )}
        
        {preview && !capturing && (
          <>
            <button 
              className="onboarding-btn primary"
              onClick={() => {
                screenReader.speak("Photo confirmed. Processing...");
                onCapture(preview);
              }}
              style={{ width: "100%", justifyContent: "center", padding: "12px" }}
            >
              Use This Photo
            </button>
            <button 
              className="onboarding-btn ghost"
              onClick={retryCapture}
              style={{ width: "100%", justifyContent: "center", padding: "12px" }}
            >
              Retake Photo
            </button>
          </>
        )}
      </div>
      
      <div className="disclaimer-box" role="note" tabIndex="0" style={{ marginTop: "16px", padding: "10px 14px", fontSize: "0.85rem" }}>
        <span style={{ opacity: 1, fontWeight: "500" }}>🔒 Your face data is encrypted and immediately analyzed inside a secure environment. We never store raw images.</span>
      </div>
    </div>
  );
};

export default FaceCapture;