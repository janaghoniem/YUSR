import React from "react";
import "../styles/animations.css";

const GradientBlobs = () => {
  return (
    <div className="fixed inset-0 w-screen h-screen -z-10 pointer-events-none overflow-hidden">
      {/* Blob 1 */}
      <div
        className="absolute w-[90vmin] h-[90vmin] left-[5%] top-[10%] blur-3xl animate-blob-slow opacity-70"
        style={{
          background:
            "radial-gradient(circle at 30% 30%, rgba(198,75,248,0.6), rgba(255,102,196,0.25) 60%, rgba(0,0,0,0) 80%)",
        }}
      />
      {/* Blob 2 */}
      <div
        className="absolute w-[100vmin] h-[100vmin] right-[8%] top-[20%] blur-3xl animate-blob-medium opacity-60"
        style={{
          background:
            "radial-gradient(circle at 70% 40%, rgba(255,59,59,0.5), rgba(255,138,214,0.2) 55%, rgba(0,0,0,0) 80%)",
        }}
      />
      {/* Blob 3 */}
      <div
        className="absolute w-[85vmin] h-[85vmin] left-[20%] bottom-[10%] blur-2xl animate-blob-fast opacity-50"
        style={{
          background:
            "radial-gradient(circle at 50% 50%, rgba(255,255,255,0.35), rgba(178,59,224,0.25) 50%, rgba(0,0,0,0) 80%)",
        }}
      />
    </div>
  );
};

export default GradientBlobs;
