// VoiceControls.jsx
import React, { useState } from "react";
import { Mic, Settings, X, Send, Pause, Square, Play } from "lucide-react";

const VoiceControls = ({
  isRecording,
  orbState,
  onMicClick,
  onCancel,
  chatMode,
  setChatMode,
  onSendText,
  onSettingsClick,
  isExecuting = false,
  onInterrupt,
}) => {
  const [text, setText] = useState("");
  const [isPaused, setIsPaused] = useState(false);

  if (chatMode) {
    const handleSend = () => {
      if (!text.trim()) return;
      onSendText(text);
      setText("");
    };

    return (
      <div className="chat-input-wrapper">
        <div className="chat-input-container">
          <input
            type="text"
            placeholder={isExecuting ? "Type interrupt command or message..." : "Type your message..."}
            className="chat-input"
            aria-label="Chat message input"
            role="textbox"
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") handleSend();
            }}
            autoFocus
          />

          <button className="send-btn" onClick={handleSend}>
            <Send size={18} />
          </button>
                  
          <button
            className="voice-return-btn"
            onClick={() => setChatMode(false)}
            style={{ cursor: "pointer" }}
          >
            <Mic size={18} />
          </button>
        </div>

        {/* Interrupt buttons shown during execution in chat mode too */}
        {isExecuting && onInterrupt && (
          <div className="interrupt-controls-inline">
            {!isPaused ? (
              <button 
                className="interrupt-btn interrupt-pause"
                onClick={() => { onInterrupt("pause"); setIsPaused(true); }}
                title="Pause execution"
              >
                <Pause size={14} /> Pause
              </button>
            ) : (
              <button 
                className="interrupt-btn interrupt-resume"
                onClick={() => { onInterrupt("resume"); setIsPaused(false); }}
                title="Resume execution"
              >
                <Play size={14} /> Resume
              </button>
            )}
            <button 
              className="interrupt-btn interrupt-stop"
              onClick={() => { onInterrupt("stop"); setIsPaused(false); }}
              title="Stop execution"
            >
              <Square size={14} /> Stop
            </button>
          </div>
        )}
      </div>
    );
  }

  return (
    <div className={`voice-controls ${isRecording ? "recording" : ""}`} role="region" aria-label="Voice controls">
      <button className="control-btn" onClick={onCancel} aria-label="Cancel" title="Cancel">
        <X size={20} />
      </button>

      <button
        className="mic-btn"
        onClick={onMicClick}
        aria-label={isRecording ? "Stop recording" : "Activate microphone"}
        aria-pressed={isRecording}
        role="button"
        title={isRecording ? "Stop recording" : "Activate microphone (voice interrupts always work)"}
      >
        <Mic size={22} />
      </button>

      <button 
        className="control-btn"
        onClick={onSettingsClick}
        aria-label="Settings"
        title="Open settings"
      >
        <Settings size={20} />
      </button>

      {/* Interrupt controls - visible during execution */}
      {isExecuting && onInterrupt && (
        <div className="interrupt-controls">
          {!isPaused ? (
            <button 
              className="interrupt-btn interrupt-pause"
              onClick={() => { onInterrupt("pause"); setIsPaused(true); }}
              title="Pause (or say 'AURA pause')"
            >
              <Pause size={16} />
            </button>
          ) : (
            <button 
              className="interrupt-btn interrupt-resume"
              onClick={() => { onInterrupt("resume"); setIsPaused(false); }}
              title="Resume (or say 'AURA resume')"
            >
              <Play size={16} />
            </button>
          )}
          <button 
            className="interrupt-btn interrupt-stop"
            onClick={() => { onInterrupt("stop"); setIsPaused(false); }}
            title="Stop (or say 'AURA stop')"
          >
            <Square size={16} />
          </button>
        </div>
      )}
    </div>
  );
};

export default VoiceControls;