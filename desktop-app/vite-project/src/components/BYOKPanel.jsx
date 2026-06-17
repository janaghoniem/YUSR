// BYOKPanel.jsx
// "Bring Your Own Key" settings panel — matches AURA glassmorphic design system exactly.
// Drop into SettingsModal as a new section, or use standalone in onboarding.

import React, { useState, useEffect, useCallback, useRef } from "react";
import {
  Key, ChevronDown, Eye, EyeOff, CheckCircle, XCircle,
  Trash2, Plus, Sparkles, Loader, ShieldCheck, AlertTriangle,
  ExternalLink, RefreshCw
} from "lucide-react";

const API = "http://localhost:8000/byok";

// ── Provider icon colours (simple dot badge) ──────────────────────────────────
const PROVIDER_COLOURS = {
  openai:    "#10a37f",
  anthropic: "#cc785c",
  groq:      "#f55036",
  cohere:    "#39594d",
  mistral:   "#ff7000",
  together:  "#6b57ff",
  google:    "#4285f4",
};

// ── Tier badge ────────────────────────────────────────────────────────────────
function ProviderDot({ id }) {
  const colour = PROVIDER_COLOURS[id] ?? "#888";
  return (
    <span
      style={{
        width: 8, height: 8, borderRadius: "50%",
        background: colour, display: "inline-block",
        flexShrink: 0, boxShadow: `0 0 6px ${colour}88`,
      }}
      aria-hidden="true"
    />
  );
}

// ── Status badge ──────────────────────────────────────────────────────────────
function StatusBadge({ status }) {
  const map = {
    ok:      { icon: <CheckCircle size={12} />, label: "Verified",  color: "var(--success)" },
    fail:    { icon: <XCircle     size={12} />, label: "Invalid",   color: "var(--error)"   },
    testing: { icon: <Loader      size={12} className="spin" />, label: "Testing…", color: "var(--text-muted)" },
  };
  const cfg = map[status];
  if (!cfg) return null;
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 5,
      fontSize: 11, color: cfg.color, fontWeight: 600,
      padding: "3px 8px", borderRadius: 999,
      background: `${cfg.color}18`, border: `1px solid ${cfg.color}40`,
    }}>
      {cfg.icon} {cfg.label}
    </span>
  );
}

// ── Main component ────────────────────────────────────────────────────────────
export default function BYOKPanel({ userId, compact = false }) {
  const [providers, setProviders]   = useState([]);
  const [savedKeys, setSavedKeys]   = useState([]);
  const [adding, setAdding]         = useState(false);
  const [selected, setSelected]     = useState("");
  const [rawKey, setRawKey]         = useState("");
  const [model, setModel]           = useState("");
  const [label, setLabel]           = useState("");
  const [showKey, setShowKey]       = useState(false);
  const [testStatus, setTestStatus] = useState(null);   // null | "testing" | "ok" | "fail"
  const [testMsg, setTestMsg]       = useState("");
  const [saving, setSaving]         = useState(false);
  const [saveErr, setSaveErr]       = useState("");
  const [loading, setLoading]       = useState(true);
  const inputRef = useRef(null);

  // Load catalogue + user's saved keys
  const refresh = useCallback(async () => {
    if (!userId) return;
    setLoading(true);
    try {
      const [pRes, kRes] = await Promise.all([
        fetch(`${API}/providers`),
        fetch(`${API}/keys/list?user_id=${encodeURIComponent(userId)}`),
      ]);
      if (pRes.ok) setProviders((await pRes.json()).providers ?? []);
      if (kRes.ok) setSavedKeys((await kRes.json()).keys ?? []);
    } catch (e) {
      console.warn("[BYOK] refresh failed:", e);
    } finally {
      setLoading(false);
    }
  }, [userId]);

  useEffect(() => { refresh(); }, [refresh]);

  // Auto-fill default model when provider changes
  useEffect(() => {
    const p = providers.find(p => p.id === selected);
    if (p) setModel(p.default_model ?? "");
  }, [selected, providers]);

  // Focus the key input when add form opens
  useEffect(() => {
    if (adding) setTimeout(() => inputRef.current?.focus(), 60);
  }, [adding]);

  // ── Test key ──────────────────────────────────────────────────────────────
  const handleTest = async () => {
    if (!selected || !rawKey.trim()) return;
    setTestStatus("testing");
    setTestMsg("");
    try {
      const r = await fetch(`${API}/keys/test`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_id: userId, provider: selected, api_key: rawKey.trim() }),
      });
      const d = await r.json();
      setTestStatus(d.ok ? "ok" : "fail");
      setTestMsg(d.message ?? "");
    } catch {
      setTestStatus("fail");
      setTestMsg("Could not reach the server.");
    }
  };

  // ── Save key ──────────────────────────────────────────────────────────────
  const handleSave = async () => {
    if (!selected || !rawKey.trim() || testStatus !== "ok") return;
    setSaving(true);
    setSaveErr("");
    try {
      const r = await fetch(`${API}/keys/save`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          user_id: userId,
          provider: selected,
          api_key: rawKey.trim(),
          model: model || undefined,
          label: label || undefined,
        }),
      });
      if (!r.ok) throw new Error((await r.json()).detail ?? "Save failed");
      // Reset form
      setRawKey(""); setModel(""); setLabel(""); setSelected("");
      setTestStatus(null); setTestMsg("");
      setAdding(false);
      await refresh();
    } catch (e) {
      setSaveErr(e.message);
    } finally {
      setSaving(false);
    }
  };

  // ── Delete key ────────────────────────────────────────────────────────────
  const handleDelete = async (provider) => {
    if (!window.confirm(`Remove your ${provider} key? This cannot be undone.`)) return;
    try {
      await fetch(`${API}/keys/delete`, {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_id: userId, provider }),
      });
      await refresh();
    } catch (e) {
      console.warn("[BYOK] delete failed:", e);
    }
  };

  // ── Helpers ───────────────────────────────────────────────────────────────
  const canTest = selected && rawKey.trim().length >= 8;
  const canSave = canTest && testStatus === "ok" && !saving;
  const unusedProviders = providers.filter(p => !savedKeys.some(k => k.provider === p.id));

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div className="byok-panel" style={{ display: "flex", flexDirection: "column", gap: 20 }}>

      {/* ── Header ── */}
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12 }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
            <ShieldCheck size={16} color="var(--pink-400)" />
            <span style={{ fontSize: 11, fontWeight: 700, letterSpacing: "0.10em", textTransform: "uppercase", color: "var(--text-muted)" }}>
              Bring Your Own Key
            </span>
          </div>
          <p style={{ fontSize: 13, color: "var(--text-secondary)", lineHeight: 1.65, margin: 0 }}>
            Your keys are encrypted with AES-256 before storage and never leave the server in plaintext.
            AURA proxies requests to your chosen provider — your key is never sent to the browser.
          </p>
        </div>
        <button
          onClick={refresh}
          aria-label="Refresh key list"
          style={{
            width: 30, height: 30, borderRadius: 8, border: "1px solid rgba(255,255,255,0.09)",
            background: "rgba(255,255,255,0.04)", color: "var(--text-muted)",
            display: "flex", alignItems: "center", justifyContent: "center", cursor: "pointer",
            flexShrink: 0,
          }}
        >
          <RefreshCw size={13} />
        </button>
      </div>

      {/* ── Saved keys list ── */}
      {loading ? (
        <div style={{ display: "flex", alignItems: "center", gap: 8, color: "var(--text-muted)", fontSize: 13, padding: "12px 0" }}>
          <Loader size={14} className="spin" /> Loading…
        </div>
      ) : savedKeys.length > 0 ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <p style={{ fontSize: 11, fontWeight: 700, letterSpacing: "0.10em", textTransform: "uppercase", color: "var(--text-disabled)", margin: 0 }}>
            Saved Keys
          </p>
          {savedKeys.map(k => (
            <div key={k.provider} style={{
              display: "flex", alignItems: "center", gap: 12, padding: "11px 14px",
              borderRadius: 12, border: "1px solid rgba(255,255,255,0.07)",
              background: "rgba(255,255,255,0.03)", transition: "background 120ms",
            }}>
              <ProviderDot id={k.provider} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 13.5, fontWeight: 500, color: "var(--text-primary)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                  {k.label}
                </div>
                {k.model && (
                  <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
                    Model: {k.model}
                  </div>
                )}
              </div>
              <span style={{ fontSize: 11.5, color: "var(--success)", display: "flex", alignItems: "center", gap: 4, flexShrink: 0 }}>
                <CheckCircle size={11} /> Active
              </span>
              <button
                onClick={() => handleDelete(k.provider)}
                aria-label={`Remove ${k.label} key`}
                style={{
                  width: 28, height: 28, borderRadius: 7,
                  border: "1px solid rgba(255,77,109,0.28)",
                  background: "rgba(255,77,109,0.08)", color: "#ff86a1",
                  display: "flex", alignItems: "center", justifyContent: "center",
                  cursor: "pointer", flexShrink: 0,
                }}
              >
                <Trash2 size={12} />
              </button>
            </div>
          ))}
        </div>
      ) : (
        <div style={{
          padding: "20px 16px", borderRadius: 12,
          border: "1px dashed rgba(255,255,255,0.08)",
          background: "rgba(255,255,255,0.02)",
          textAlign: "center", color: "var(--text-muted)", fontSize: 13,
        }}>
          No keys saved yet. Add one below to use your own model provider.
        </div>
      )}

      {/* ── Add key form toggle ── */}
      {!adding ? (
        <button
          onClick={() => setAdding(true)}
          disabled={unusedProviders.length === 0}
          style={{
            display: "inline-flex", alignItems: "center", gap: 8,
            padding: "9px 16px", borderRadius: 10,
            border: "1px solid rgba(255,61,154,0.28)",
            background: "rgba(255,61,154,0.08)", color: "var(--pink-300)",
            fontSize: 13.5, fontFamily: "inherit", fontWeight: 500,
            cursor: unusedProviders.length === 0 ? "not-allowed" : "pointer",
            opacity: unusedProviders.length === 0 ? 0.4 : 1,
            alignSelf: "flex-start",
          }}
        >
          <Plus size={15} />
          {unusedProviders.length === 0 ? "All providers added" : "Add API Key"}
        </button>
      ) : (
        <div style={{
          borderRadius: 14, border: "1px solid rgba(255,61,154,0.18)",
          background: "linear-gradient(155deg, rgba(255,255,255,0.04), rgba(255,255,255,0.01))",
          padding: "18px 18px 16px",
          display: "flex", flexDirection: "column", gap: 14,
        }}>

          {/* Provider selector */}
          <label style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <span style={LBL}>Provider</span>
            <div style={{ position: "relative" }}>
              <select
                value={selected}
                onChange={e => { setSelected(e.target.value); setTestStatus(null); setRawKey(""); }}
                style={SELECT}
                aria-label="Select provider"
              >
                <option value="">— choose a provider —</option>
                {unusedProviders.map(p => (
                  <option key={p.id} value={p.id}>{p.label}</option>
                ))}
              </select>
              <ChevronDown size={13} style={{ position: "absolute", right: 12, top: "50%", transform: "translateY(-50%)", color: "var(--text-muted)", pointerEvents: "none" }} />
            </div>
          </label>

          {/* API key input */}
          <label style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <span style={LBL}>API Key</span>
            <div style={{ position: "relative" }}>
              <input
                ref={inputRef}
                type={showKey ? "text" : "password"}
                value={rawKey}
                onChange={e => { setRawKey(e.target.value); setTestStatus(null); }}
                placeholder={selected ? `Paste your ${providers.find(p=>p.id===selected)?.label ?? ""} key…` : "Select a provider first"}
                disabled={!selected}
                autoComplete="off"
                spellCheck={false}
                style={{ ...INPUT, paddingRight: 40, fontFamily: rawKey && !showKey ? "monospace" : "inherit" }}
                aria-label="API key"
              />
              <button
                type="button"
                onClick={() => setShowKey(v => !v)}
                style={{
                  position: "absolute", right: 10, top: "50%", transform: "translateY(-50%)",
                  background: "none", border: "none", color: "var(--text-muted)", cursor: "pointer",
                  display: "flex", alignItems: "center",
                }}
                aria-label={showKey ? "Hide key" : "Reveal key"}
              >
                {showKey ? <EyeOff size={15} /> : <Eye size={15} />}
              </button>
            </div>
          </label>

          {/* Model override */}
          <label style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <span style={LBL}>Model <span style={{ color: "var(--text-disabled)", fontWeight: 400 }}>(optional)</span></span>
            <input
              type="text"
              value={model}
              onChange={e => setModel(e.target.value)}
              placeholder="Leave blank to use provider default"
              style={INPUT}
              aria-label="Model name"
            />
          </label>

          {/* Friendly label */}
          <label style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <span style={LBL}>Label <span style={{ color: "var(--text-disabled)", fontWeight: 400 }}>(optional)</span></span>
            <input
              type="text"
              value={label}
              onChange={e => setLabel(e.target.value)}
              placeholder={providers.find(p=>p.id===selected)?.label ?? "e.g. Work OpenAI key"}
              style={INPUT}
              aria-label="Friendly label"
            />
          </label>

          {/* Actions row */}
          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
            <button
              onClick={handleTest}
              disabled={!canTest || testStatus === "testing"}
              style={{
                ...BTN_SECONDARY,
                opacity: (!canTest || testStatus === "testing") ? 0.45 : 1,
                cursor: (!canTest || testStatus === "testing") ? "not-allowed" : "pointer",
              }}
            >
              {testStatus === "testing"
                ? <><Loader size={13} className="spin" /> Testing…</>
                : <><Sparkles size={13} /> Test Key</>
              }
            </button>

            <button
              onClick={handleSave}
              disabled={!canSave}
              style={{
                ...BTN_PRIMARY,
                opacity: !canSave ? 0.45 : 1,
                cursor: !canSave ? "not-allowed" : "pointer",
              }}
            >
              {saving
                ? <><Loader size={13} className="spin" /> Saving…</>
                : <><Key size={13} /> Save Key</>
              }
            </button>

            <button
              onClick={() => { setAdding(false); setRawKey(""); setSelected(""); setTestStatus(null); setTestMsg(""); setSaveErr(""); }}
              style={BTN_GHOST}
            >
              Cancel
            </button>

            {testStatus && <StatusBadge status={testStatus} />}
          </div>

          {/* Test / save messages */}
          {testMsg && (
            <div style={{
              fontSize: 12.5, lineHeight: 1.55, padding: "9px 12px", borderRadius: 8,
              background: testStatus === "ok" ? "rgba(77,214,140,0.08)" : "rgba(255,107,107,0.08)",
              border: `1px solid ${testStatus === "ok" ? "rgba(77,214,140,0.22)" : "rgba(255,107,107,0.22)"}`,
              color: testStatus === "ok" ? "var(--success)" : "var(--error)",
              display: "flex", alignItems: "center", gap: 7,
            }}>
              {testStatus === "ok"
                ? <CheckCircle size={13} style={{ flexShrink: 0 }} />
                : <AlertTriangle size={13} style={{ flexShrink: 0 }} />
              }
              {testMsg}
            </div>
          )}

          {saveErr && (
            <div style={{
              fontSize: 12.5, padding: "9px 12px", borderRadius: 8,
              background: "rgba(255,107,107,0.08)", border: "1px solid rgba(255,107,107,0.22)",
              color: "var(--error)",
            }}>
              {saveErr}
            </div>
          )}

          {/* Security note */}
          {testStatus === "ok" && (
            <div style={{
              fontSize: 11.5, color: "var(--text-muted)", lineHeight: 1.6,
              display: "flex", alignItems: "flex-start", gap: 7,
              padding: "9px 12px", borderRadius: 8,
              background: "rgba(255,255,255,0.02)", border: "1px solid rgba(255,255,255,0.05)",
            }}>
              <ShieldCheck size={13} color="var(--pink-400)" style={{ flexShrink: 0, marginTop: 2 }} />
              Your key will be encrypted with AES-256-GCM before being stored. It is never logged or returned to the browser.
            </div>
          )}
        </div>
      )}

      {/* ── Security note at bottom ── */}
      <div style={{
        fontSize: 11, color: "var(--text-disabled)", lineHeight: 1.6,
        display: "flex", alignItems: "flex-start", gap: 6,
      }}>
        <ShieldCheck size={11} style={{ flexShrink: 0, marginTop: 2, color: "var(--text-muted)" }} />
        Keys are encrypted server-side and only decrypted in-memory during API calls. They are never stored in plaintext or sent to your browser.
      </div>

      {/* ── CSS-in-JS spin animation ── */}
      <style>{`
        .spin { animation: byok-spin 0.8s linear infinite; }
        @keyframes byok-spin { to { transform: rotate(360deg); } }
      `}</style>
    </div>
  );
}

// ── Inline style constants (match index.css tokens) ──────────────────────────
const LBL = {
  fontSize: 12, letterSpacing: "0.03em", textTransform: "uppercase",
  color: "var(--text-muted)", fontWeight: 500,
};

const INPUT = {
  width: "100%", padding: "10px 14px",
  background: "rgba(255,255,255,0.04)",
  border: "1px solid rgba(255,255,255,0.09)",
  borderRadius: 8, color: "var(--text-primary)",
  fontFamily: "inherit", fontSize: 14, outline: "none",
  transition: "border-color 120ms, box-shadow 120ms",
  boxSizing: "border-box",
};

const SELECT = {
  ...INPUT, appearance: "none", paddingRight: 32, cursor: "pointer",
};

const BTN_BASE = {
  display: "inline-flex", alignItems: "center", gap: 7,
  padding: "8px 16px", borderRadius: 9,
  fontSize: 13, fontFamily: "inherit", fontWeight: 500,
  border: "1px solid", cursor: "pointer",
  transition: "all 120ms",
};

const BTN_PRIMARY = {
  ...BTN_BASE,
  background: "rgba(255,61,154,0.14)",
  borderColor: "rgba(255,128,192,0.55)",
  color: "var(--pink-200)",
};

const BTN_SECONDARY = {
  ...BTN_BASE,
  background: "rgba(255,255,255,0.06)",
  borderColor: "rgba(255,255,255,0.12)",
  color: "var(--text-secondary)",
};

const BTN_GHOST = {
  ...BTN_BASE,
  background: "transparent",
  borderColor: "rgba(255,255,255,0.08)",
  color: "var(--text-muted)",
};