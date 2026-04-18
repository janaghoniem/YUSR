// VoiceInput.jsx — Reusable voice-enabled input field
import React, { useState, useEffect } from "react";
import { Mic, MicOff, Square } from "lucide-react";

const VoiceInput = ({
  value,
  onChange,
  placeholder = "Type or speak...",
  type = "text",
  label,
  isPassword = false,
  disabled = false,
}) => {
  const [isListening, setIsListening] = useState(false);
  const [recognition, setRecognition] = useState(null);
  const [supported, setSupported] = useState(false);
  const [usingVosk, setUsingVosk] = useState(false);

  useEffect(() => {
    if (window?.electronAPI?.transcribeOnce) {
      setSupported(true);
      setUsingVosk(true);
      return;
    }

    const SpeechRecognition =
      window.SpeechRecognition || window.webkitSpeechRecognition;
    if (SpeechRecognition) {
      const rec = new SpeechRecognition();
      rec.continuous = false;
      rec.interimResults = false;
      rec.lang = "en-US";

      rec.onresult = (event) => {
        const transcript = event.results[0][0].transcript;
        onChange(transcript);
        setIsListening(false);
      };

      rec.onerror = () => setIsListening(false);
      rec.onend = () => setIsListening(false);

      setRecognition(rec);
      setSupported(true);
    }
  }, []);

  const toggleVoice = () => {
    if (usingVosk) {
      if (isListening) {
        setIsListening(false);
        return;
      }
      setIsListening(true);
      const logicalLang = /[\u0600-\u06FF]/.test(value || "") ? "ar" : "en";
      window.electronAPI
        .transcribeOnce({ lang: logicalLang, timeoutMs: 7000 })
        .then((res) => {
          const text = (res?.text || "").trim();
          if (text) onChange(text);
        })
        .catch(() => {
          // Keep silent; user can type manually.
        })
        .finally(() => setIsListening(false));
      return;
    }

    if (!recognition) return;
    if (isListening) {
      recognition.stop();
      setIsListening(false);
    } else {
      recognition.start();
      setIsListening(true);
    }
  };

  return (
    <div className="voice-input-wrapper">
      {label && <label className="onboarding-input-label">{label}</label>}
      <div className={`voice-input-container ${isListening ? "listening" : ""}`}>
        <input
          type={isPassword ? "password" : type}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={isListening ? "🎤 Listening..." : placeholder}
          className="onboarding-input"
          disabled={disabled}
          autoComplete={isPassword ? "new-password" : "off"}
        />
        {supported && !isPassword && (
          <button
            type="button"
            className={`voice-input-btn ${isListening ? "active" : ""}`}
            onClick={toggleVoice}
            title={isListening ? "Stop listening" : "Speak to fill"}
          >
            {isListening ? <Square size={16} /> : <Mic size={16} />}
          </button>
        )}
      </div>
    </div>
  );
};

export default VoiceInput;