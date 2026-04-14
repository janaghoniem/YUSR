import React from "react";
import { Minus, Maximize, X, PictureInPicture2, Maximize2, Eye } from "lucide-react";

const TitleBar = ({ 
  transparent = false, 
  title = "AURA",
  showExtraControls = false,
  isExecuting = false,
  executionMode = "normal",
  onToggleExecutionMode = () => {},
  onEnterWidgetMode = () => {}
}) => {
  return (
    <div 
      className="titlebar" 
      style={transparent ? { backgroundColor: "transparent", borderBottom: "none" } : {}}
    >
      <div className="titlebar-drag">
        <span className="titlebar-title">{title}</span>
      </div>
      <div className="titlebar-buttons">
        {showExtraControls && isExecuting && (
          <button
            className="titlebar-btn titlebar-mode"
            onClick={onToggleExecutionMode}
            title={executionMode === "normal" ? "Go transparent" : "Back to normal"}
          >
            {executionMode === "normal" ? <Eye size={14} /> : <Maximize2 size={14} />}
          </button>
        )}
        {showExtraControls && (
          <button className="titlebar-btn" onClick={onEnterWidgetMode} title="Minimize to widget">
            <PictureInPicture2 size={14} />
          </button>
        )}
        <button className="titlebar-btn" onClick={() => window.electronAPI?.minimizeWindow?.()} title="Minimize">
          <Minus size={14} />
        </button>
        <button className="titlebar-btn" onClick={() => window.electronAPI?.maximizeWindow?.()} title="Maximize">
          <Maximize size={14} />
        </button>
        <button className="titlebar-btn titlebar-close" onClick={() => window.electronAPI?.closeWindow?.()} title="Close">
          <X size={14} />
        </button>
      </div>
    </div>
  );
};

export default TitleBar;
