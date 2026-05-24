// ArtifactCard.jsx — Claude-style file/link display cards in the chat area
import React from "react";
import {
  FileText, File, Table, Presentation, Code, Globe, ExternalLink,
  Download, FileImage, Music, Video, Archive, FileJson
} from "lucide-react";

/**
 * Returns the correct icon component + accent color for a given artifact type/extension.
 */
function getArtifactMeta(artifact) {
  if (artifact.type === "url") {
    return { Icon: Globe, color: "#38bdf8", label: "Link", bg: "rgba(56,189,248,0.12)", border: "rgba(56,189,248,0.25)" };
  }

  const ext = (artifact.value || "").split(".").pop()?.toLowerCase() || "";

  const map = {
    pdf:   { Icon: FileText,     color: "#f87171", label: "PDF",          bg: "rgba(248,113,113,0.10)", border: "rgba(248,113,113,0.22)" },
    doc:   { Icon: FileText,     color: "#60a5fa", label: "Word Doc",     bg: "rgba(96,165,250,0.10)",  border: "rgba(96,165,250,0.22)" },
    docx:  { Icon: FileText,     color: "#60a5fa", label: "Word Doc",     bg: "rgba(96,165,250,0.10)",  border: "rgba(96,165,250,0.22)" },
    xls:   { Icon: Table,        color: "#34d399", label: "Spreadsheet",  bg: "rgba(52,211,153,0.10)",  border: "rgba(52,211,153,0.22)" },
    xlsx:  { Icon: Table,        color: "#34d399", label: "Spreadsheet",  bg: "rgba(52,211,153,0.10)",  border: "rgba(52,211,153,0.22)" },
    csv:   { Icon: Table,        color: "#34d399", label: "CSV",          bg: "rgba(52,211,153,0.10)",  border: "rgba(52,211,153,0.22)" },
    ppt:   { Icon: Presentation, color: "#fb923c", label: "Presentation", bg: "rgba(251,146,60,0.10)",  border: "rgba(251,146,60,0.22)" },
    pptx:  { Icon: Presentation, color: "#fb923c", label: "Presentation", bg: "rgba(251,146,60,0.10)",  border: "rgba(251,146,60,0.22)" },
    py:    { Icon: Code,         color: "#c084fc", label: "Python",       bg: "rgba(192,132,252,0.10)", border: "rgba(192,132,252,0.22)" },
    js:    { Icon: Code,         color: "#fbbf24", label: "JavaScript",   bg: "rgba(251,191,36,0.10)",  border: "rgba(251,191,36,0.22)" },
    ts:    { Icon: Code,         color: "#60a5fa", label: "TypeScript",   bg: "rgba(96,165,250,0.10)",  border: "rgba(96,165,250,0.22)" },
    html:  { Icon: Code,         color: "#f97316", label: "HTML",         bg: "rgba(249,115,22,0.10)",  border: "rgba(249,115,22,0.22)" },
    css:   { Icon: Code,         color: "#38bdf8", label: "CSS",          bg: "rgba(56,189,248,0.10)",  border: "rgba(56,189,248,0.22)" },
    json:  { Icon: FileJson,     color: "#a3e635", label: "JSON",         bg: "rgba(163,230,53,0.10)",  border: "rgba(163,230,53,0.22)" },
    md:    { Icon: FileText,     color: "#e2e8f0", label: "Markdown",     bg: "rgba(226,232,240,0.07)", border: "rgba(226,232,240,0.15)" },
    txt:   { Icon: FileText,     color: "#d1d5db", label: "Text",         bg: "rgba(209,213,219,0.07)", border: "rgba(209,213,219,0.15)" },
    png:   { Icon: FileImage,    color: "#f472b6", label: "Image",        bg: "rgba(244,114,182,0.10)", border: "rgba(244,114,182,0.22)" },
    jpg:   { Icon: FileImage,    color: "#f472b6", label: "Image",        bg: "rgba(244,114,182,0.10)", border: "rgba(244,114,182,0.22)" },
    jpeg:  { Icon: FileImage,    color: "#f472b6", label: "Image",        bg: "rgba(244,114,182,0.10)", border: "rgba(244,114,182,0.22)" },
    gif:   { Icon: FileImage,    color: "#f472b6", label: "Image",        bg: "rgba(244,114,182,0.10)", border: "rgba(244,114,182,0.22)" },
    webp:  { Icon: FileImage,    color: "#f472b6", label: "Image",        bg: "rgba(244,114,182,0.10)", border: "rgba(244,114,182,0.22)" },
    mp3:   { Icon: Music,        color: "#a78bfa", label: "Audio",        bg: "rgba(167,139,250,0.10)", border: "rgba(167,139,250,0.22)" },
    mp4:   { Icon: Video,        color: "#67e8f9", label: "Video",        bg: "rgba(103,232,249,0.10)", border: "rgba(103,232,249,0.22)" },
    zip:   { Icon: Archive,      color: "#fbbf24", label: "Archive",      bg: "rgba(251,191,36,0.10)",  border: "rgba(251,191,36,0.22)" },
  };

  return map[ext] || { Icon: File, color: "#94a3b8", label: "File", bg: "rgba(148,163,184,0.08)", border: "rgba(148,163,184,0.18)" };
}

/**
 * Single artifact card — shown inline in the chat response area.
 */
export function ArtifactCard({ artifact, onOpen }) {
  const { Icon, color, label, bg, border } = getArtifactMeta(artifact);
  const isUrl = artifact.type === "url";

  const handleOpen = () => {
    if (isUrl) {
      // Use Electron shell if available, else window.open
      if (typeof window !== "undefined" && window.electronAPI?.openExternalUrl) {
        window.electronAPI.openExternalUrl(artifact.value);
      } else {
        window.open(artifact.value, "_blank", "noopener,noreferrer");
      }
    }
    onOpen?.(artifact);
  };

  const displayName = artifact.label || artifact.title || (isUrl ? artifact.value : artifact.value.split(/[/\\]/).pop());
  const truncated = displayName.length > 48 ? displayName.slice(0, 46) + "…" : displayName;

  return (
    <div
      className="artifact-card"
      style={{ background: bg, borderColor: border }}
      role="article"
      aria-label={`${label}: ${displayName}`}
    >
      <div className="artifact-card-icon" style={{ color, background: `${color}18` }} aria-hidden="true">
        <Icon size={18} />
      </div>
      <div className="artifact-card-info">
        <span className="artifact-card-label" style={{ color }} aria-label={`File type: ${label}`}>
          {label}
        </span>
        <span className="artifact-card-name" title={displayName}>
          {truncated}
        </span>
      </div>
      <button
        className="artifact-card-btn"
        onClick={handleOpen}
        aria-label={isUrl ? `Open link: ${displayName}` : `Open file: ${displayName}`}
        title={isUrl ? "Open in browser" : "Open file"}
        type="button"
      >
        {isUrl ? <ExternalLink size={14} aria-hidden="true" /> : <Download size={14} aria-hidden="true" />}
        <span>{isUrl ? "Open" : "Open"}</span>
      </button>
    </div>
  );
}

/**
 * Group of artifact cards — shown below an assistant message.
 */
export function ArtifactGroup({ artifacts, onOpen }) {
  if (!artifacts || artifacts.length === 0) return null;

  return (
    <div
      className="artifact-group"
      role="list"
      aria-label={`${artifacts.length} created ${artifacts.length === 1 ? "file" : "files or links"}`}
    >
      {artifacts.map(artifact => (
        <div key={artifact.id} role="listitem">
          <ArtifactCard artifact={artifact} onOpen={onOpen} />
        </div>
      ))}
    </div>
  );
}

export default ArtifactCard;