// ThinkingIndicator.jsx — Clean, accessible thinking display
import React from "react";

const ThinkingIndicator = ({ steps = [] }) => {
  return (
    <div
      className="thinking-container"
      role="status"
      aria-live="polite"
      aria-atomic="false"
      aria-label="AURA is thinking"
    >
      <div className="thinking-content">
        <div className="thinking-header">
          <span className="thinking-label">Thinking</span>
          <div className="thinking-dots" aria-hidden="true">
            <span />
            <span />
            <span />
          </div>
        </div>

        {steps.length > 0 && (
          <ul
            className="thinking-steps"
            role="list"
            aria-label="Current thinking steps"
          >
            {steps.map((step, idx) => {
              const isActive = idx === steps.length - 1;
              const isCompleted = idx < steps.length - 1;
              return (
                <li
                  key={idx}
                  className={`thinking-step ${
                    isCompleted ? "completed" : isActive ? "active" : ""
                  }`}
                  role="listitem"
                  aria-current={isActive ? "true" : undefined}
                >
                  <span className="step-icon" aria-hidden="true">
                    {isCompleted ? "✓" : "●"}
                  </span>
                  <span className="step-text">
                    {typeof step === "string" ? step : JSON.stringify(step)}
                  </span>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
};

export default ThinkingIndicator;