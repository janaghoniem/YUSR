import React from "react";

const ThinkingIndicator = ({ steps = [] }) => {
  return (
    <div className="thinking-container" role="status" aria-live="polite" aria-label="Thinking progress">
      <div className="thinking-content">
        <div className="thinking-header">
          <span className="thinking-label">Thinking</span>
          <div className="thinking-dots" aria-hidden="true">
            <span></span>
            <span></span>
            <span></span>
          </div>
        </div>
        
        <ul className="thinking-steps" role="list" aria-label="Thinking steps">
          {steps.map((step, idx) => (
            <li
              key={idx}
              role="listitem"
              aria-current={idx === steps.length - 1 ? "true" : "false"}
              className={`thinking-step ${idx < steps.length - 1 ? "completed" : "active"}`}
            >
              <span className="step-icon" aria-hidden="true">
                {idx < steps.length - 1 ? "✓" : "●"}
              </span>
              <span className="step-text">{typeof step === 'string' ? step : JSON.stringify(step)}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
};

export default ThinkingIndicator;