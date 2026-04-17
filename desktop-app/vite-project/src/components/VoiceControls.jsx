// VoiceControls.jsx — Claude/ChatGPT-style tall input · sleek voice mode
import React, { useState, useRef, useEffect } from "react";
import { Mic, Settings, X, Send, Pause, Square, Play } from "lucide-react";
import BorderGlow from "./BorderGlow";

const PLACEHOLDERS = [
  "Message AURA...",
  "Ask me anything...",
  "What's on your mind?",
  "Try 'Summarize my recent conversations'",
  "Need help with your tasks?",
  "Type 'Set an alarm for 8 AM'"
];

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
  wakePulse = false,
}) => {
  const [text, setText] = useState("");
  const [isPaused, setIsPaused] = useState(false);
  const [placeholderIdx, setPlaceholderIdx] = useState(0);
  const [placeholderText, setPlaceholderText] = useState("");
  const [isDeleting, setIsDeleting] = useState(false);
  const [showCursor, setShowCursor] = useState(true);
  const textareaRef = useRef(null);

  // Blinking cursor
  useEffect(() => {
    const cursorInterval = setInterval(() => {
      setShowCursor((prev) => !prev);
    }, 500);
    return () => clearInterval(cursorInterval);
  }, []);

  // Typing effect for placeholders
  useEffect(() => {
    if (!chatMode) return;

    const currentString = PLACEHOLDERS[placeholderIdx];
    let timeout;
    
    if (isDeleting) {
      if (placeholderText === "") {
        setIsDeleting(false);
        setPlaceholderIdx((prev) => (prev + 1) % PLACEHOLDERS.length);
      } else {
        timeout = setTimeout(() => {
          setPlaceholderText(currentString.substring(0, placeholderText.length - 1));
        }, 40); // Erase speed
      }
    } else {
      if (placeholderText === currentString) {
        timeout = setTimeout(() => {
          setIsDeleting(true);
        }, 2000); // Pause when full
      } else {
        timeout = setTimeout(() => {
          setPlaceholderText(currentString.substring(0, placeholderText.length + 1));
        }, 60); // Type speed
      }
    }

    return () => clearTimeout(timeout);
  }, [placeholderText, isDeleting, placeholderIdx, chatMode]);

  // Auto-grow textarea
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 320) + "px";
  }, [text]);

  /* ── CHAT (text) MODE ─────────────────────────────────────── */
  if (chatMode) {
    const handleSend = () => {
      const trimmed = text.trim();
      if (!trimmed) return;
      onSendText(trimmed);
      setText("");
      if (textareaRef.current) {
        textareaRef.current.style.height = "auto";
      }
    };

    return (
      <div className="chat-input-wrapper" role="search" aria-label="Text input area">
        <BorderGlow 
          style={{ width: '100%', maxWidth: '700px', display: 'flex' }} 
          className="rounded-2xl" 
          glowColor="40 80 80" 
          colors={['#c084fc', '#f472b6', '#38bdf8']}
          animated={true} 
          duration={4}
        >
        <div className="chat-input-container" style={{ flex: 1, maxWidth: '100%' }}>
          {/* Multiline textarea that grows */}
          <label htmlFor="chat-text-input" className="sr-only">
            Type a message to AURA
          </label>
          <textarea
            id="chat-text-input"
            ref={textareaRef}
            rows={1}
            placeholder={
              isExecuting ? "Type a command or message…" : placeholderText + (showCursor ? "|" : "")
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

          {/* Bottom toolbar */}
          <div className="chat-input-toolbar">
            {/* Left: attach + voice toggle */}
            <div className="chat-input-left-actions">
              {/* Left action buttons */}
              <button
                className="voice-return-btn"
                onClick={() => setChatMode(false)}
                aria-label="Switch to voice mode"
                title="Use voice instead"
                type="button"
              >
                <Mic size={16} aria-hidden="true" />
              </button>

              {/* Interrupt inline controls */}
              {isExecuting && onInterrupt && (
                <>
                  {!isPaused ? (
                    <button
                      className="interrupt-btn interrupt-pause"
                      onClick={() => { onInterrupt("pause"); setIsPaused(true); }}
                      aria-label='Pause execution'
                      title="Pause"
                      type="button"
                    >
                      <Pause size={12} aria-hidden="true" />
                      <span>Pause</span>
                    </button>
                  ) : (
                    <button
                      className="interrupt-btn interrupt-resume"
                      onClick={() => { onInterrupt("resume"); setIsPaused(false); }}
                      aria-label='Resume execution'
                      title="Resume"
                      type="button"
                    >
                      <Play size={12} aria-hidden="true" />
                      <span>Resume</span>
                    </button>
                  )}
                  <button
                    className="interrupt-btn interrupt-stop"
                    onClick={() => { onInterrupt("stop"); setIsPaused(false); }}
                    aria-label='Stop execution'
                    title="Stop"
                    type="button"
                  >
                    <Square size={12} aria-hidden="true" />
                    <span>Stop</span>
                  </button>
                </>
              )}
            </div>

            {/* Right: send */}
            <button
              className="send-btn"
              onClick={handleSend}
              aria-label="Send message"
              disabled={!text.trim()}
              title="Send"
              type="button"
            >
              <Send size={15} aria-hidden="true" />
            </button>
          </div>
        </div>
        </BorderGlow>
      </div>
    );
  }

  /* ── VOICE MODE ───────────────────────────────────────────── */
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', width: '100%', marginTop: 'auto' }}>
      <BorderGlow 
        className={`rounded-full ${isRecording ? "recording" : ""} ${wakePulse ? "wake-pulse" : ""}`}
        style={{ 
          width: 'fit-content', 
          display: 'flex', 
          borderRadius: '9999px',
          alignSelf: 'center'
        }}
        glowColor={wakePulse ? "170 86 70" : (isRecording ? "330 100 60" : "40 80 80")}
        colors={wakePulse ? ['#45f3ff', '#38bdf8', '#8bffe5'] : (isRecording ? ['#ff4d6d', '#ff758c', '#ff8fa3'] : ['#c084fc', '#f472b6', '#38bdf8'])}
        backgroundColor="transparent"
        animated={true} 
        duration={wakePulse ? 1.5 : (isRecording ? 2 : 4)}
      >
        <div
          className={`voice-controls ${isRecording ? "recording" : ""} ${wakePulse ? "wake-pulse" : ""}`}
          role="region"
          aria-label="Voice controls"
          aria-live="polite"
          style={{ marginTop: 0, alignSelf: 'center', backgroundColor: 'rgba(25,20,15,0.72)', backdropFilter: 'blur(28px)', WebkitBackdropFilter: 'blur(28px)' }}
        >
          {/* Cancel / switch to chat */}
      <button
        className="control-btn"
        onClick={() => setChatMode(true)}
        aria-label="Switch to chat mode"
        title="Chat mode"
        type="button"
      >
        <X size={17} aria-hidden="true" />
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
        type="button"
      >
        <Mic size={20} aria-hidden="true" />
      </button>

      {/* Settings */}
      <button
        className="control-btn"
        onClick={onSettingsClick}
        aria-label="Open settings"
        title="Settings"
        type="button"
      >
        <Settings size={17} aria-hidden="true" />
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
              onClick={() => { onInterrupt("pause"); setIsPaused(true); }}
              aria-label='Pause execution'
              type="button"
            >
              <Pause size={13} aria-hidden="true" />
            </button>
          ) : (
            <button
              className="interrupt-btn interrupt-resume"
              onClick={() => { onInterrupt("resume"); setIsPaused(false); }}
              aria-label='Resume execution'
              type="button"
            >
              <Play size={13} aria-hidden="true" />
            </button>
          )}
          <button
            className="interrupt-btn interrupt-stop"
            onClick={() => { onInterrupt("stop"); setIsPaused(false); }}
            aria-label='Stop execution'
            type="button"
          >
            <Square size={13} aria-hidden="true" />
          </button>
        </div>
      )}
        </div>
      </BorderGlow>
    </div>
  );
};

export default VoiceControls;