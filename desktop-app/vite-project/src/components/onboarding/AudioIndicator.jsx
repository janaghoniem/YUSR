import React from "react";
import "./AudioIndicator.css";

const AudioIndicator = ({ isSpeaking, isListening, transcript, error }) => {
  if (!isSpeaking && !isListening && !error) return null;

  return (
    <div style={{ position: "absolute", top: "40px", right: "20px", display: "flex", alignItems: "center", gap: "10px", zIndex: 1000 }}>
      {error && (
        <div style={{ background: "rgba(255, 0, 0, 0.5)", color: "#ffcccc", padding: "4px 10px", borderRadius: "8px", fontSize: "12px" }}>
          {error}
        </div>
      )}
      {isListening && transcript && (
        <div style={{
          background: "rgba(0, 0, 0, 0.5)",
          color: "#00ccff",
          padding: "4px 10px",
          borderRadius: "8px",
          fontSize: "12px",
          backdropFilter: "blur(4px)",
          animation: "fade-in 0.3s ease"
        }}>
          "{transcript}"
        </div>
      )}
      <div className={`audio-indicator-wrapper ${isSpeaking ? "speaking" : "listening"}`} style={{ position: "relative", top: "auto", right: "auto" }}>
        {isSpeaking ? (
          <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="audio-icon speaking-icon">
            <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"></polygon>
            <path d="M15.54 8.46a5 5 0 0 1 0 7.07"></path>
            <path d="M19.07 4.93a10 10 0 0 1 0 14.14"></path>
          </svg>
        ) : (
          <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="audio-icon listening-icon">
            <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"></path>
            <path d="M19 10v2a7 7 0 0 1-14 0v-2"></path>
            <line x1="12" y1="19" x2="12" y2="22"></line>
          </svg>
        )}
      </div>
    </div>
  );
};

export default AudioIndicator;