import React from "react";

const BackgroundOverlay = () => {
  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        zIndex: -1,
        pointerEvents: "none",
        background:
          "radial-gradient(ellipse at center, transparent 40%, rgba(15,12,10,0.45) 100%)",
      }}
    />
  );
};

export default BackgroundOverlay;