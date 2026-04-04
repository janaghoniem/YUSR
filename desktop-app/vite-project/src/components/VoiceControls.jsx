// VoiceControls.jsx — Claude-inspired, accessibility-first
import React, { useState, useRef } from "react";
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
  const inputRef = useRef(null);

  /* ── CHAT (text) MODE ─────────────────────────────────────── */
  if (chatMode) {
    const handleSend = () => {
      const trimmed = text.trim();
      if (!trimmed) return;
      onSendText(trimmed);
      setText("");
    };

    return (
      <div className="chat-input-wrapper" role="search" aria-label="Text input area">
        <div className="chat-input-container">
          <label htmlFor="chat-text-input" className="sr-only">
            Type a message to AURA
          </label>
          <input
            id="chat-text-input"
            ref={inputRef}
            type="text"
            placeholder={
              isExecuting
                ? "Type a command or message…"
                : "Message AURA…"
            }
            className="chat-input"
            aria-label={
              isExecuting
                ? "Message input — you can type interrupt commands"
                : "Message input"
            }
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                handleSend();
              }
            }}
            autoFocus
            autoComplete="off"
            spellCheck="false"
          />

          <button
            className="send-btn"
            onClick={handleSend}
            aria-label="Send message"
            disabled={!text.trim()}
            title="Send"
          >
            <Send size={16} aria-hidden="true" />
          </button>

          <button
            className="voice-return-btn"
            onClick={() => setChatMode(false)}
            aria-label="Switch to voice mode"
            title="Use voice instead"
          >
            <Mic size={16} aria-hidden="true" />
          </button>
        </div>

        {/* Interrupt controls in chat mode */}
        {isExecuting && onInterrupt && (
          <div
            className="interrupt-controls-inline"
            role="group"
            aria-label="Execution controls"
          >
            {!isPaused ? (
              <button
                className="interrupt-btn interrupt-pause"
                onClick={() => {
                  onInterrupt("pause");
                  setIsPaused(true);
                }}
                aria-label='Pause execution (or say "AURA pause")'
                title="Pause"
              >
                <Pause size={13} aria-hidden="true" />
                <span>Pause</span>
              </button>
            ) : (
              <button
                className="interrupt-btn interrupt-resume"
                onClick={() => {
                  onInterrupt("resume");
                  setIsPaused(false);
                }}
                aria-label='Resume execution (or say "AURA resume")'
                title="Resume"
              >
                <Play size={13} aria-hidden="true" />
                <span>Resume</span>
              </button>
            )}
            <button
              className="interrupt-btn interrupt-stop"
              onClick={() => {
                onInterrupt("stop");
                setIsPaused(false);
              }}
              aria-label='Stop execution (or say "AURA stop")'
              title="Stop"
            >
              <Square size={13} aria-hidden="true" />
              <span>Stop</span>
            </button>
          </div>
        )}
      </div>
    );
  }

  /* ── VOICE MODE ───────────────────────────────────────────── */
  return (
    <div
      className={`voice-controls ${isRecording ? "recording" : ""}`}
      role="region"
      aria-label="Voice controls"
      aria-live="polite"
    >
      {/* Cancel / switch to chat */}
      <button
        className="control-btn"
        onClick={onCancel}
        aria-label="Switch to chat mode"
        title="Chat mode"
      >
        <X size={18} aria-hidden="true" />
      </button>

      {/* Main mic button */}
      <button
        className="mic-btn"
        onClick={onMicClick}
        aria-label={
          isRecording
            ? "Stop recording — press to send"
            : "Start recording — press and speak"
        }
        aria-pressed={isRecording}
        title={isRecording ? "Stop recording" : "Start recording"}
      >
        <Mic size={21} aria-hidden="true" />
      </button>

      {/* Settings */}
      <button
        className="control-btn"
        onClick={onSettingsClick}
        aria-label="Open settings"
        title="Settings"
      >
        <Settings size={18} aria-hidden="true" />
      </button>

      {/* Floating interrupt controls during execution */}
      {isExecuting && onInterrupt && (
        <div
          className="interrupt-controls"
          role="group"
          aria-label="Execution controls"
        >
          {!isPaused ? (
            <button
              className="interrupt-btn interrupt-pause"
              onClick={() => {
                onInterrupt("pause");
                setIsPaused(true);
              }}
              aria-label='Pause execution (or say "AURA pause")'
            >
              <Pause size={14} aria-hidden="true" />
            </button>
          ) : (
            <button
              className="interrupt-btn interrupt-resume"
              onClick={() => {
                onInterrupt("resume");
                setIsPaused(false);
              }}
              aria-label='Resume execution (or say "AURA resume")'
            >
              <Play size={14} aria-hidden="true" />
            </button>
          )}
          <button
            className="interrupt-btn interrupt-stop"
            onClick={() => {
              onInterrupt("stop");
              setIsPaused(false);
            }}
            aria-label='Stop execution (or say "AURA stop")'
          >
            <Square size={14} aria-hidden="true" />
          </button>
        </div>
      )}
    </div>
  );
};

export default VoiceControls;