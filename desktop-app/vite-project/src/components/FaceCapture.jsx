// components/FaceCapture.jsx
import React, { useRef, useState, useCallback, useEffect } from 'react';
import Webcam from 'react-webcam';

const FaceCapture = ({ onCapture, onCancel, mode = "signup", username }) => {
  const webcamRef = useRef(null);
  const [capturing, setCapturing] = useState(false);
  const [error, setError] = useState("");
  const [countdown, setCountdown] = useState(3);
  const [preview, setPreview] = useState(null);
  const [isCheckingCamera, setIsCheckingCamera] = useState(true);

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
      }
    };
    checkCamera();
  }, []);

  const captureImage = useCallback(() => {
    if (webcamRef.current) {
      const imageSrc = webcamRef.current.getScreenshot();
      setPreview(imageSrc);
      return imageSrc;
    }
    return null;
  }, []);

  const startCapture = () => {
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
          onCapture(imageData);
        } else {
          setError("Failed to capture image. Please try again.");
          setCapturing(false);
        }
      }
    }, 1000);
  };

  const retryCapture = () => {
    setCapturing(false);
    setPreview(null);
    setError("");
  };

  if (isCheckingCamera) {
    return (
      <div className="face-capture-container">
        <div className="loading-spinner">Checking camera...</div>
      </div>
    );
  }

  return (
    <div className="face-capture-container">
      <h3 className="face-capture-title">
        {mode === "signup" ? "Register Your Face" : "Verify Your Identity"}
      </h3>
      
      <p className="face-capture-subtitle">
        {mode === "signup" 
          ? "Look at the camera. We'll capture your face for secure login."
          : `Welcome back, ${username || "User"}! Look at the camera to verify your identity.`}
      </p>
      
      <div className="webcam-wrapper">
        {!preview ? (
          <>
            <Webcam
              ref={webcamRef}
              screenshotFormat="image/jpeg"
              videoConstraints={{
                width: 480,
                height: 480,
                facingMode: "user"
              }}
              className="webcam-preview"
              mirrored={true}
            />
            {capturing && (
              <div className="countdown-overlay">
                <div className="countdown-number">{countdown}</div>
              </div>
            )}
          </>
        ) : (
          <img src={preview} alt="Captured face" className="captured-preview" />
        )}
      </div>
      
      {error && <p className="face-error-message">{error}</p>}
      
      <div className="face-capture-actions">
        {!capturing && !preview && (
          <button 
            className="face-capture-btn primary"
            onClick={startCapture}
          >
            Start Face Scan
          </button>
        )}
        
        {preview && !capturing && (
          <>
            <button 
              className="face-capture-btn primary"
              onClick={() => onCapture(preview)}
            >
              Use This Photo
            </button>
            <button 
              className="face-capture-btn secondary"
              onClick={retryCapture}
            >
              Retake Photo
            </button>
          </>
        )}
        
        <button 
          className="face-capture-btn ghost"
          onClick={onCancel}
        >
          Cancel
        </button>
      </div>
      
      <p className="face-capture-note">
        Your face data is encrypted and stored securely. We never store raw images.
      </p>
    </div>
  );
};

export default FaceCapture;