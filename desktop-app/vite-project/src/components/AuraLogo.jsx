// AuraLogo.jsx — AURA brand mark component
// Shape: radial pulse — core dot + two halos + 6 orbital satellites
// Scales cleanly from 16px favicon to 96px hero

import React from "react";

const AuraLogo = ({
  size = 32,
  color = "#FF3D9A",
  className = "",
  animated = false,
  "aria-hidden": ariaHidden,
  "aria-label": ariaLabel,
}) => {
  // All values proportional to size=32 base
  const s = size / 32;
  const cx = size / 2;
  const cy = size / 2;

  const outerR   = 14 * s;
  const midR     = 9.5 * s;
  const coreR    = 4.8 * s;
  const pupilR   = 1.8 * s;
  const dotR     = 1.1 * s;
  const spoke    = midR + 2 * s; // where satellites orbit

  const satellites = Array.from({ length: 6 }, (_, i) => {
    const angle = (i * Math.PI * 2) / 6 - Math.PI / 2;
    return {
      x: cx + Math.cos(angle) * spoke,
      y: cy + Math.sin(angle) * spoke,
      lx: cx + Math.cos(angle) * (midR + 0.5 * s),
      ly: cy + Math.sin(angle) * (midR + 0.5 * s),
    };
  });

  return (
    <svg
      width={size}
      height={size}
      viewBox={`0 0 ${size} ${size}`}
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      aria-hidden={ariaHidden}
      aria-label={ariaLabel}
      role={ariaLabel ? "img" : undefined}
      style={animated ? { "--logo-color": color } : undefined}
    >
      {animated && (
        <style>{`
          @keyframes aura-spin {
            from { transform: rotate(0deg); }
            to   { transform: rotate(360deg); }
          }
          @keyframes aura-pulse {
            0%, 100% { opacity: 0.18; }
            50% { opacity: 0.32; }
          }
          @media (prefers-reduced-motion: no-preference) {
            .aura-orbit { 
              transform-origin: ${cx}px ${cy}px;
              animation: aura-spin 12s linear infinite;
            }
            .aura-outer { animation: aura-pulse 3s ease-in-out infinite; }
          }
        `}</style>
      )}

      {/* Outer halo */}
      <circle
        cx={cx} cy={cy} r={outerR}
        stroke={color}
        strokeWidth={0.7 * s}
        opacity={0.22}
        className="aura-outer"
      />

      {/* Mid halo */}
      <circle
        cx={cx} cy={cy} r={midR}
        stroke={color}
        strokeWidth={1 * s}
        opacity={0.5}
      />

      {/* Spokes + satellites — optionally orbiting */}
      <g className={animated ? "aura-orbit" : undefined}>
        {satellites.map((sat, i) => (
          <g key={i}>
            <line
              x1={sat.lx} y1={sat.ly}
              x2={sat.x}  y2={sat.y}
              stroke={color}
              strokeWidth={0.7 * s}
              opacity={0.38}
            />
            <circle
              cx={sat.x} cy={sat.y}
              r={dotR}
              fill={color}
              opacity={0.72}
            />
          </g>
        ))}
      </g>

      {/* Core */}
      <circle cx={cx} cy={cy} r={coreR} fill={color} />

      {/* Pupil / highlight */}
      <circle cx={cx} cy={cy} r={pupilR} fill="white" opacity={0.9} />
    </svg>
  );
};

export default AuraLogo;
