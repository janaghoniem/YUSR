// SettingsModal.jsx — Claude-inspired settings, ARIA-first
import React, { useState, useEffect, useRef } from "react";
import { X, User, Brain, Trash2, RefreshCw, Eye, EyeOff, Key } from "lucide-react";
import BYOKPanel from "./BYOKPanel";

const API_BASE_URL = "";

const SettingsModal = ({
  onClose,
  onSave,
  onDeviceIdChange,
  onLogout,
  initialName = "User",
  initialVoice = "Gacrux",
  initialLanguage = "en",
  initialDeviceId = "",
}) => {
  const [activeSection, setActiveSection] = useState("profile");
  const [profileData, setProfileData] = useState({
    username: initialName,
    email: "",
    theme: "dark",
    language: initialLanguage,
    voice: initialVoice,
  });
  const [deviceId, setDeviceId] = useState(initialDeviceId);
  const [profileLoading, setProfileLoading] = useState(false);
  const [profileStatus, setProfileStatus] = useState("");

  // ✅ Long-term memory (preferences) stats
  const [memoryStats, setMemoryStats] = useState({
    total_preferences: 0,
    personal_info_count: 0,
    app_preferences_count: 0,
    storage_size_mb: 0,
  });
  const [preferences, setPreferences] = useState([]);
  const [showPreferences, setShowPreferences] = useState(false);
  const [loading, setLoading] = useState(false);
  const [statusMessage, setStatusMessage] = useState("");

  // Trap focus inside modal
  const modalRef = useRef(null);
  const closeBtnRef = useRef(null);

  useEffect(() => {
    closeBtnRef.current?.focus();
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    setProfileData((prev) => ({
      ...prev,
      username: initialName,
      language: initialLanguage,
      voice: initialVoice,
    }));
  }, [initialName, initialLanguage, initialVoice]);

  useEffect(() => {
    setDeviceId(initialDeviceId);
  }, [initialDeviceId]);

  // Load real profile data on mount
  useEffect(() => {
    const loadProfile = async () => {
      try {
        const userId = localStorage.getItem("userId") || "test_user";
        const res = await fetch(`http://localhost:8000/user/profile?user_id=${userId}`);
        if (res.ok) {
          const data = await res.json();
          setProfileData(prev => ({
            ...prev,
            username: data.username || prev.username,
            email: data.email || "",
          }));
        }
      } catch (e) {
        console.error("Failed to load profile:", e);
      }
    };
    loadProfile();
  }, []);

  useEffect(() => {
    if (activeSection === "memory") fetchMemoryStats();
  }, [activeSection]);

  const fetchMemoryStats = async () => {
    try {
      setLoading(true);
      setStatusMessage("");
      const userId = localStorage.getItem("userId") || "test_user";
      const response = await fetch(
        `${API_BASE_URL}/api/memory/preferences?user_id=${userId}&limit=100`
      );
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      const prefs = data.preferences || [];
      setMemoryStats({
        total_preferences: prefs.length,
        personal_info_count: prefs.filter((p) => p.category === "personal_info").length,
        app_preferences_count: prefs.filter((p) => p.category === "app_usage").length,
        storage_size_mb: (JSON.stringify(prefs).length / (1024 * 1024)).toFixed(2),
      });
      setPreferences(prefs);
      setStatusMessage("Memory loaded successfully.");
    } catch (err) {
      setStatusMessage(`Could not load memory: ${err.message}`);
    } finally {
      setLoading(false);
    }
  };

  const handleClearMemory = async () => {
    if (
      !window.confirm(
        "This will permanently delete all learned preferences. Your conversation history will stay. Continue?"
      )
    )
      return;
    try {
      setLoading(true);
      setStatusMessage("Clearing memory…");
      const userId = localStorage.getItem("userId") || "test_user";
      const res = await fetch(
        `${API_BASE_URL}/api/memory/clear-preferences?user_id=${userId}`,
        { method: "DELETE" }
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const result = await res.json();
      setStatusMessage(`Cleared ${result.preferences_deleted} preferences.`);
      await fetchMemoryStats();
    } catch (err) {
      setStatusMessage(`Failed: ${err.message}`);
    } finally {
      setLoading(false);
    }
  };

  const handleProfileChange = (field, value) => {
    setProfileData({ ...profileData, [field]: value });
  };

  const handleSave = async () => {
    setProfileLoading(true);
    setProfileStatus("");
    try {
      const userId = localStorage.getItem("userId") || "test_user";
      const res = await fetch("http://localhost:8000/user/profile", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          user_id: userId,
          username: profileData.username,
          email: profileData.email,
        }),
      });
      const result = await res.json();
      if (!res.ok) {
        setProfileStatus(`❌ ${result.detail || "Update failed"}`);
        setProfileLoading(false);
        return;
      }
      if (profileData.username) {
        localStorage.setItem("userName", profileData.username);
        console.log("[SettingsModal] Saved username to localStorage:", profileData.username);
      }
      if (onDeviceIdChange && deviceId.trim()) {
        onDeviceIdChange(deviceId.trim());
      }
      setProfileStatus("✅ Profile saved successfully");
      console.log("[SettingsModal] Calling onSave with:", profileData);
      onSave(profileData);
      setTimeout(onClose, 800);
    } catch (e) {
      setProfileStatus(`❌ ${e.message}`);
    } finally {
      setProfileLoading(false);
    }
  };

  const navItems = [
    { id: "profile", label: "Profile",  icon: <User size={15} aria-hidden="true" /> },
    { id: "memory",  label: "Memory",   icon: <Brain size={15} aria-hidden="true" /> },
    { id: "apikeys", label: "API Keys", icon: <Key   size={15} aria-hidden="true" /> },
  ];

  const statCards = [
    { label: "Total preferences",    value: memoryStats.total_preferences,     color: "var(--pink-400)" },
    { label: "Personal info",         value: memoryStats.personal_info_count,   color: "var(--pink-300)" },
    { label: "App preferences",       value: memoryStats.app_preferences_count, color: "var(--pink-200)" },
    { label: "Storage used",          value: `${memoryStats.storage_size_mb} MB`, color: "var(--text-secondary)" },
  ];

  return (
    <div
      className="settings-overlay"
      role="presentation"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div
        ref={modalRef}
        className="settings-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="settings-dialog-title"
      >
        <button
          ref={closeBtnRef}
          className="settings-close-btn"
          onClick={onClose}
          aria-label="Close settings"
          title="Close"
        >
          <X size={16} aria-hidden="true" />
        </button>

        <div className="settings-container">
          {/* ── Left nav ─────────────────────────── */}
          <div className="settings-sidebar" role="navigation" aria-label="Settings sections">
            <p className="settings-title" id="settings-dialog-title">Settings</p>
            <nav className="settings-nav" aria-label="Settings navigation">
              {navItems.map(({ id, label, icon }) => (
                <button
                  key={id}
                  className={`settings-nav-item ${activeSection === id ? "active" : ""}`}
                  onClick={() => setActiveSection(id)}
                  aria-current={activeSection === id ? "page" : undefined}
                  aria-label={`${label} settings`}
                >
                  {icon}
                  <span>{label}</span>
                </button>
              ))}
            </nav>
          </div>

          {/* ── Right content ────────────────────── */}
          <div className="settings-content">

            {/* ── PROFILE ── */}
            {activeSection === "profile" && (
              <section className="settings-section settings-profile-section" aria-labelledby="profile-heading">
                <h2 className="section-title" id="profile-heading">Profile</h2>
                <p className="settings-profile-subtitle">
                  Update how AURA addresses you and which voice/language defaults to use.
                </p>

                <div className="settings-profile-card">
                  <div className="settings-group settings-group-profile">
                    <label className="settings-label" htmlFor="settings-username">
                      <span className="settings-field-label">Display name</span>
                      <input
                        id="settings-username"
                        type="text"
                        className="settings-input"
                        autoFocus
                        autoComplete="name"
                        value={profileData.username}
                        onChange={(e) =>
                          setProfileData({ ...profileData, username: e.target.value })
                        }
                      />
                    </label>

                    <label className="settings-label" htmlFor="settings-email">
                      <span className="settings-field-label">Email</span>
                      <input
                        id="settings-email"
                        type="email"
                        className="settings-input"
                        autoComplete="email"
                        value={profileData.email}
                        onChange={(e) =>
                          setProfileData({ ...profileData, email: e.target.value })
                        }
                        placeholder="name@example.com"
                      />
                    </label>

                    <label className="settings-label" htmlFor="settings-language">
                      <span className="settings-field-label">Language</span>
                      <select
                        id="settings-language"
                        className="settings-select"
                        value={profileData.language}
                        onChange={(e) =>
                          setProfileData({ ...profileData, language: e.target.value })
                        }
                      >
                        <option value="en">English</option>
                        <option value="ar">العربية</option>
                      </select>
                    </label>

                    <label className="settings-label" htmlFor="settings-voice">
                      <span className="settings-field-label">Voice</span>
                      <select
                        id="settings-voice"
                        className="settings-select"
                        value={profileData.voice}
                        onChange={(e) =>
                          setProfileData({ ...profileData, voice: e.target.value })
                        }
                      >
                        <option value="Gacrux">Gacrux (default)</option>
                        <option value="orpheus-english">Orpheus English</option>
                        <option value="orpheus-arabic">Orpheus Arabic</option>
                      </select>
                    </label>

                    <label className="settings-label" htmlFor="settings-device-id">
                      <span className="settings-field-label">Device ID</span>
                      <input
                        id="settings-device-id"
                        type="text"
                        className="settings-input"
                        value={deviceId}
                        onChange={(e) => setDeviceId(e.target.value)}
                        placeholder="e.g., windows-work-laptop"
                      />
                      <small style={{ color: "var(--text-muted)", lineHeight: 1.5 }}>
                        Used for cross-platform task sync. Change only if you know what you're doing.
                      </small>
                    </label>
                  </div>
                </div>
              </section>
            )}

            {/* ── MEMORY ── */}
            {activeSection === "memory" && (
              <section className="settings-section" aria-labelledby="memory-heading">
                <h2 className="section-title" id="memory-heading">Long-term memory</h2>
                <p style={{ fontSize: "14px", color: "var(--text-secondary)", marginBottom: "24px", lineHeight: "1.6" }}>
                  AURA learns your preferences over time — your name, app choices, and work patterns.
                </p>

                {/* Stats grid */}
                <div className="memory-stats-card" aria-label="Memory statistics">
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px", marginBottom: "16px" }}>
                    {statCards.map(({ label, value, color }) => (
                      <div
                        key={label}
                        style={{
                          background: "var(--bg-overlay)",
                          padding: "14px",
                          borderRadius: "var(--r-md)",
                          border: "1px solid rgba(255,255,255,0.05)",
                        }}
                        role="group"
                        aria-label={`${label}: ${value}`}
                      >
                        <div style={{ fontSize: "22px", fontWeight: "600", color }}>{value}</div>
                        <div style={{ fontSize: "12px", color: "var(--text-muted)", marginTop: "3px" }}>{label}</div>
                      </div>
                    ))}
                  </div>
                  <button
                    onClick={fetchMemoryStats}
                    disabled={loading}
                    aria-label="Refresh memory statistics"
                    style={{
                      padding: "8px 14px",
                      background: "var(--bg-overlay)",
                      border: "1px solid rgba(255,255,255,0.1)",
                      borderRadius: "var(--r-md)",
                      color: "var(--text-secondary)",
                      cursor: loading ? "not-allowed" : "pointer",
                      display: "flex",
                      alignItems: "center",
                      gap: "7px",
                      fontSize: "13px",
                      fontFamily: "inherit",
                    }}
                  >
                    <RefreshCw size={13} aria-hidden="true" />
                    Refresh
                  </button>
                </div>

                {/* View preferences */}
                <div style={{ marginTop: "20px" }}>
                  <button
                    onClick={() => setShowPreferences(!showPreferences)}
                    aria-expanded={showPreferences}
                    aria-controls="preferences-list"
                    style={{
                      width: "100%",
                      padding: "12px",
                      background: "var(--bg-overlay)",
                      border: "1px solid rgba(255,255,255,0.08)",
                      borderRadius: "var(--r-md)",
                      color: "var(--text-secondary)",
                      cursor: "pointer",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "space-between",
                      fontSize: "14px",
                      fontFamily: "inherit",
                    }}
                  >
                    <span style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                      {showPreferences
                        ? <EyeOff size={15} aria-hidden="true" />
                        : <Eye size={15} aria-hidden="true" />}
                      {showPreferences ? "Hide" : "View"} stored preferences
                    </span>
                    <span style={{ fontSize: "12px", color: "var(--text-muted)" }}>
                      {preferences.length} items
                    </span>
                  </button>

                  {showPreferences && (
                    <div
                      id="preferences-list"
                      role="list"
                      aria-label="Stored preferences"
                      style={{
                        marginTop: "10px",
                        maxHeight: "280px",
                        overflowY: "auto",
                        background: "var(--bg-overlay)",
                        border: "1px solid rgba(255,255,255,0.06)",
                        borderRadius: "var(--r-md)",
                        padding: "12px",
                      }}
                    >
                      {preferences.length === 0 ? (
                        <p style={{ fontSize: "13px", color: "var(--text-muted)", textAlign: "center", padding: "20px 0" }}>
                          No preferences stored yet. Use AURA and it will learn from you.
                        </p>
                      ) : (
                        preferences.map((pref, idx) => (
                          <div
                            key={idx}
                            role="listitem"
                            style={{
                              padding: "10px 12px",
                              background: "rgba(255,255,255,0.02)",
                              borderRadius: "var(--r-sm)",
                              marginBottom: "6px",
                              borderLeft: `2px solid ${
                                pref.category === "personal_info"
                                  ? "var(--pink-400)"
                                  : pref.category === "app_usage"
                                  ? "var(--pink-600)"
                                  : "var(--bg-muted)"
                              }`,
                            }}
                          >
                            <p style={{ fontSize: "13px", color: "var(--text-primary)", margin: "0 0 4px" }}>
                              {pref.text}
                            </p>
                            <p style={{ fontSize: "11px", color: "var(--text-muted)", margin: 0 }}>
                              {pref.category}
                              {pref.timestamp && ` · ${new Date(pref.timestamp).toLocaleDateString()}`}
                            </p>
                          </div>
                        ))
                      )}
                    </div>
                  )}
                </div>

                {/* Danger zone */}
                <div style={{ marginTop: "24px" }}>
                  <div
                    style={{
                      background: "rgba(224, 88, 88, 0.07)",
                      border: "1px solid rgba(224, 88, 88, 0.2)",
                      borderRadius: "var(--r-md)",
                      padding: "16px",
                      marginBottom: "12px",
                    }}
                    role="group"
                    aria-labelledby="danger-zone-label"
                  >
                    <p
                      id="danger-zone-label"
                      style={{ fontSize: "13px", color: "#e05858", marginBottom: "6px", fontWeight: "600" }}
                    >
                      Danger zone
                    </p>
                    <p style={{ fontSize: "12.5px", color: "var(--text-muted)", marginBottom: "14px", lineHeight: "1.5" }}>
                      Permanently deletes all learned preferences. Conversation history is kept.
                    </p>
                    <button
                      onClick={handleClearMemory}
                      disabled={loading}
                      aria-label="Clear all learned preferences permanently"
                      style={{
                        padding: "9px 18px",
                        background: "rgba(224, 88, 88, 0.12)",
                        border: "1px solid rgba(224, 88, 88, 0.35)",
                        borderRadius: "var(--r-md)",
                        color: "#e05858",
                        cursor: loading ? "not-allowed" : "pointer",
                        display: "flex",
                        alignItems: "center",
                        gap: "7px",
                        fontSize: "13px",
                        fontFamily: "inherit",
                        fontWeight: "500",
                      }}
                    >
                      <Trash2 size={14} aria-hidden="true" />
                      {loading ? "Clearing…" : "Clear memory"}
                    </button>
                  </div>

                  {statusMessage && (
                    <p
                      role="status"
                      aria-live="polite"
                      style={{
                        padding: "10px 14px",
                        background: "var(--bg-overlay)",
                        border: "1px solid rgba(255,255,255,0.07)",
                        borderRadius: "var(--r-md)",
                        fontSize: "13px",
                        color: "var(--text-secondary)",
                      }}
                    >
                      {statusMessage}
                    </p>
                  )}
                </div>
              </section>
            )}

            {/* ── API KEYS (BYOK) ── */}
            {activeSection === "apikeys" && (
              <section className="settings-section" aria-labelledby="apikeys-heading">
                <h2 className="section-title" id="apikeys-heading">API Keys</h2>
                <p style={{ fontSize: "14px", color: "var(--text-secondary)", marginBottom: "20px", lineHeight: "1.6" }}>
                  Connect your own model provider keys. AURA will use them instead of built-in models,
                  routing all requests securely through the server — your key never touches the browser.
                </p>
                <BYOKPanel userId={localStorage.getItem("userId") || ""} />
              </section>
            )}

            {profileStatus && (
              <div style={{ padding: "10px", fontSize: "13px", color: "rgba(255,255,255,0.9)" }}>
                {profileStatus}
              </div>
            )}

            {/* Only show Save/Cancel on profile tab — BYOK has its own save flow */}
            {activeSection !== "apikeys" && (
              <div className="settings-actions">
                {onLogout && (
                  <button className="settings-btn-logout" onClick={onLogout}>
                    Log out
                  </button>
                )}
                <button className="settings-btn-save" onClick={handleSave} disabled={profileLoading}>
                  {profileLoading ? "Saving..." : "Save Changes"}
                </button>
                <button className="settings-btn-cancel" onClick={onClose}>
                  Cancel
                </button>
              </div>
            )}

            {/* On API Keys tab, just show a close button */}
            {activeSection === "apikeys" && (
              <div className="settings-actions">
                {onLogout && (
                  <button className="settings-btn-logout" onClick={onLogout}>
                    Log out
                  </button>
                )}
                <button className="settings-btn-cancel" onClick={onClose}>
                  Done
                </button>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export default SettingsModal;