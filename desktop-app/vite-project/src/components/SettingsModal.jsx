// SettingsModal.jsx
import React, { useState, useEffect } from "react";
import { X, User, Brain, Trash2, RefreshCw, Eye, EyeOff, LogOut } from "lucide-react";

const SettingsModal = ({ onClose, onSave, onLogout, initialName = "User", initialVoice = "Gacrux" }) => {
  const [activeSection, setActiveSection] = useState("profile");
  const [profileData, setProfileData] = useState({
    username: initialName,
    email: "",
    theme: "dark",
    language: "en",
    voice: initialVoice,
  });
  const [profileLoading, setProfileLoading] = useState(false);
  const [profileStatus, setProfileStatus] = useState("");
  
  // ✅ Long-term memory (preferences) stats
  const [memoryStats, setMemoryStats] = useState({
    total_preferences: 0,
    personal_info_count: 0,
    app_preferences_count: 0,
    storage_size_mb: 0,
  });
  
  // ✅ Actual stored preferences (for viewing)
  const [preferences, setPreferences] = useState([]);
  const [showPreferences, setShowPreferences] = useState(false);
  
  const [loading, setLoading] = useState(false);
  const [statusMessage, setStatusMessage] = useState("");

  // Fetch memory stats when Memory tab is opened
  useEffect(() => {
    if (activeSection === "memory") {
      fetchMemoryStats();
    }
  }, [activeSection]);

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

  // ✅ ADD THIS AT THE TOP OF THE FILE (after imports, before component)
  const API_BASE_URL = "";

  // ✅ THEN UPDATE THE fetchMemoryStats FUNCTION
  const fetchMemoryStats = async () => {
    try {
      setLoading(true);
      setStatusMessage(""); // Clear previous messages
      
      const userId = localStorage.getItem("userId") || "test_user";
      
      console.log("📡 Fetching preferences from:", `${API_BASE_URL}/api/memory/preferences`);
      
      // Get preference stats
      const response = await fetch(
        `${API_BASE_URL}/api/memory/preferences?user_id=${userId}&limit=100`,
        {
          method: "GET",
          headers: {
            "Content-Type": "application/json",
          },
        }
      );
      
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }
      
      const data = await response.json();
      console.log("✅ Received data:", data);
      
      // Calculate stats
      const prefs = data.preferences || [];
      const personalInfo = prefs.filter(p => p.category === "personal_info");
      const appPrefs = prefs.filter(p => p.category === "app_usage");
      
      setMemoryStats({
        total_preferences: prefs.length,
        personal_info_count: personalInfo.length,
        app_preferences_count: appPrefs.length,
        storage_size_mb: ((JSON.stringify(prefs).length) / (1024 * 1024)).toFixed(2)
      });
      
      setPreferences(prefs);
      setStatusMessage("✅ Memory stats loaded successfully");
      
    } catch (error) {
      console.error("❌ Failed to fetch memory stats:", error);
      setStatusMessage(`❌ Failed to load memory: ${error.message}`);
    } finally {
      setLoading(false);
    }
  };

  const handleClearLongTermMemory = async () => {
    if (!window.confirm("🚨 WARNING: This will DELETE ALL your learned preferences and personal information permanently. Your conversation history will remain. Are you sure?")) {
      return;
    }

    try {
      setLoading(true);
      setStatusMessage("🗑️ Clearing long-term memory...");
      
      const userId = localStorage.getItem("userId") || "test_user";
      
      console.log("📡 Clearing preferences for user:", userId);
      
      const response = await fetch(
        `${API_BASE_URL}/api/memory/clear-preferences?user_id=${userId}`,
        {
          method: "DELETE",
          headers: {
            "Content-Type": "application/json",
          },
        }
      );

      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }

      const result = await response.json();
      console.log("✅ Clear result:", result);
      
      setStatusMessage(`✅ Long-term memory cleared! Deleted ${result.preferences_deleted} preferences.`);
      
      // Refresh stats
      await fetchMemoryStats();
      
    } catch (error) {
      console.error("❌ Clear memory failed:", error);
      setStatusMessage(`❌ Failed to clear memory: ${error.message}`);
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
      // Update localStorage with new username
      if (profileData.username) {
        localStorage.setItem("userName", profileData.username);
        console.log("[SettingsModal] Saved username to localStorage:", profileData.username);
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

  return (
    <div className="settings-overlay" role="presentation">
      <div className="settings-modal" role="dialog" aria-modal="true" aria-labelledby="settings-title">
        <button className="settings-close-btn" onClick={onClose} aria-label="Close settings">
          <X size={22} />
        </button>

        <div className="settings-container">
          {/* Left Sidebar */}
          <div className="settings-sidebar">
            <h2 className="settings-title">Settings</h2>
            <nav className="settings-nav">
              <button
                className={`settings-nav-item ${activeSection === "profile" ? "active" : ""}`}
                onClick={() => setActiveSection("profile")}
              >
                <User size={16} />
                <span>Profile</span>
              </button>

              <button
                className={`settings-nav-item ${activeSection === "memory" ? "active" : ""}`}
                onClick={() => setActiveSection("memory")}
              >
                <Brain size={16} />
                <span>Long-Term Memory</span>
              </button>
            </nav>

            {/* Logout at bottom of sidebar */}
            <button
              className="settings-nav-item"
              onClick={onLogout}
              style={{ marginTop: "auto", color: "#ff4d6d", borderColor: "rgba(255,77,109,0.3)" }}
            >
              <LogOut size={16} />
              <span>Log Out</span>
            </button>
          </div>

          {/* Right Content */}
          <div className="settings-content">
            {activeSection === "profile" && (
              <div className="settings-section">
                <h3 className="section-title" id="settings-title">Profile Settings</h3>
                <div className="settings-group">
                  <label className="settings-label">
                    Username
                    <input
                      id="settings-username"
                      type="text"
                      className="settings-input"
                      aria-label="Username"
                      autoFocus
                      value={profileData.username}
                      onChange={(e) => handleProfileChange("username", e.target.value)}
                    />
                  </label>

                  <label className="settings-label">
                    Email
                    <input
                      type="email"
                      className="settings-input"
                      value={profileData.email}
                      onChange={(e) => handleProfileChange("email", e.target.value)}
                    />
                  </label>

                  <label className="settings-label">
                    Theme
                    <select
                      className="settings-select"
                      value={profileData.theme}
                      onChange={(e) => handleProfileChange("theme", e.target.value)}
                    >
                      <option value="dark">Dark</option>
                      <option value="light">Light</option>
                      <option value="auto">Auto</option>
                    </select>
                  </label>

                  <label className="settings-label">
                    Language
                    <select
                      className="settings-select"
                      value={profileData.language}
                      onChange={(e) => handleProfileChange("language", e.target.value)}
                    >
                      <option value="en">English</option>
                      <option value="ar">العربية</option>
                      <option value="es">Español</option>
                    </select>
                  </label>

                  <label className="settings-label">
                    TTS Voice
                    <select
                      className="settings-select"
                      value={profileData.voice}
                      onChange={(e) => handleProfileChange("voice", e.target.value)}
                    >
                      <option value="Gacrux">Gacrux (default)</option>
                      <option value="orpheus-english">Orpheus English</option>
                      <option value="orpheus-arabic">Orpheus Arabic</option>
                    </select>
                  </label>
                </div>
              </div>
            )}

            {activeSection === "memory" && (
              <div className="settings-section">
                <h3 className="section-title">Long-Term Memory</h3>
                <p style={{ fontSize: "14px", color: "rgba(255,255,255,0.7)", marginBottom: "20px" }}>
                  Your AI learns your preferences over time. This includes your name, app choices, and work patterns.
                </p>
                
                {/* ✅ Memory Statistics */}
                <div className="memory-stats-card">
                  <h4 style={{ fontSize: "16px", marginBottom: "12px", color: "rgba(255,255,255,0.9)" }}>
                    Stored Preferences
                  </h4>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px" }}>
                    <div style={{ background: "rgba(255,255,255,0.05)", padding: "12px", borderRadius: "8px" }}>
                      <div style={{ fontSize: "24px", fontWeight: "bold", color: "#ff4d6d" }}>
                        {memoryStats.total_preferences}
                      </div>
                      <div style={{ fontSize: "12px", color: "rgba(255,255,255,0.6)" }}>
                        Total Preferences
                      </div>
                    </div>
                    <div style={{ background: "rgba(255,255,255,0.05)", padding: "12px", borderRadius: "8px" }}>
                      <div style={{ fontSize: "24px", fontWeight: "bold", color: "#7a1fa2" }}>
                        {memoryStats.personal_info_count}
                      </div>
                      <div style={{ fontSize: "12px", color: "rgba(255,255,255,0.6)" }}>
                        Personal Info
                      </div>
                    </div>
                    <div style={{ background: "rgba(255,255,255,0.05)", padding: "12px", borderRadius: "8px" }}>
                      <div style={{ fontSize: "24px", fontWeight: "bold", color: "#38bdf8" }}>
                        {memoryStats.app_preferences_count}
                      </div>
                      <div style={{ fontSize: "12px", color: "rgba(255,255,255,0.6)" }}>
                        App Preferences
                      </div>
                    </div>
                    <div style={{ background: "rgba(255,255,255,0.05)", padding: "12px", borderRadius: "8px" }}>
                      <div style={{ fontSize: "24px", fontWeight: "bold", color: "#fbbf24" }}>
                        {memoryStats.storage_size_mb} MB
                      </div>
                      <div style={{ fontSize: "12px", color: "rgba(255,255,255,0.6)" }}>
                        Storage Used
                      </div>
                    </div>
                  </div>
                  <button
                    onClick={fetchMemoryStats}
                    disabled={loading}
                    style={{
                      marginTop: "12px",
                      padding: "8px 16px",
                      background: "rgba(255,255,255,0.08)",
                      border: "1px solid rgba(255,255,255,0.2)",
                      borderRadius: "8px",
                      color: "white",
                      cursor: loading ? "not-allowed" : "pointer",
                      display: "flex",
                      alignItems: "center",
                      gap: "8px",
                      fontSize: "13px"
                    }}
                  >
                    <RefreshCw size={14} />
                    Refresh Stats
                  </button>
                </div>

                {/* ✅ View Preference Examples */}
                <div style={{ marginTop: "24px" }}>
                  <button
                    onClick={() => setShowPreferences(!showPreferences)}
                    style={{
                      width: "100%",
                      padding: "12px",
                      background: "rgba(255,255,255,0.05)",
                      border: "1px solid rgba(255,255,255,0.1)",
                      borderRadius: "10px",
                      color: "white",
                      cursor: "pointer",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "space-between",
                      fontSize: "14px",
                      fontWeight: "500"
                    }}
                  >
                    <span style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                      {showPreferences ? <EyeOff size={16} /> : <Eye size={16} />}
                      {showPreferences ? "Hide" : "View"} Stored Preferences
                    </span>
                    <span style={{ fontSize: "12px", color: "rgba(255,255,255,0.6)" }}>
                      {preferences.length} items
                    </span>
                  </button>

                  {showPreferences && (
                    <div style={{
                      marginTop: "12px",
                      maxHeight: "300px",
                      overflowY: "auto",
                      background: "rgba(0,0,0,0.2)",
                      border: "1px solid rgba(255,255,255,0.1)",
                      borderRadius: "10px",
                      padding: "12px"
                    }}>
                      {preferences.length === 0 ? (
                        <p style={{ fontSize: "13px", color: "rgba(255,255,255,0.5)", textAlign: "center", padding: "20px" }}>
                          No preferences stored yet. Use the system and it will learn your habits!
                        </p>
                      ) : (
                        preferences.map((pref, idx) => (
                          <div 
                            key={idx}
                            style={{
                              padding: "10px 12px",
                              background: "rgba(255,255,255,0.03)",
                              borderRadius: "6px",
                              marginBottom: "8px",
                              borderLeft: `3px solid ${
                                pref.category === "personal_info" ? "#ff4d6d" :
                                pref.category === "app_usage" ? "#7a1fa2" :
                                "#38bdf8"
                              }`
                            }}
                          >
                            <div style={{ fontSize: "13px", color: "rgba(255,255,255,0.9)" }}>
                              {pref.text}
                            </div>
                            <div style={{ 
                              fontSize: "11px", 
                              color: "rgba(255,255,255,0.5)", 
                              marginTop: "4px",
                              display: "flex",
                              gap: "12px"
                            }}>
                              <span>📁 {pref.category}</span>
                              {pref.timestamp && (
                                <span>🕒 {new Date(pref.timestamp).toLocaleDateString()}</span>
                              )}
                            </div>
                          </div>
                        ))
                      )}
                    </div>
                  )}
                </div>

                {/* ✅ Clear Memory Action */}
                <div style={{ marginTop: "24px" }}>
                  <div style={{
                    background: "rgba(220, 38, 38, 0.1)",
                    border: "1px solid rgba(220, 38, 38, 0.3)",
                    borderRadius: "10px",
                    padding: "16px",
                    marginBottom: "12px"
                  }}>
                    <h4 style={{ fontSize: "14px", color: "#ef4444", marginBottom: "8px", fontWeight: "600" }}>
                      ⚠️ Danger Zone
                    </h4>
                    <p style={{ fontSize: "12px", color: "rgba(255,255,255,0.7)", marginBottom: "12px" }}>
                      Clear all learned preferences. This will make the AI forget your name, app choices, and work patterns. 
                      <strong> Your conversation history will NOT be deleted.</strong>
                    </p>
                    <button
                      onClick={handleClearLongTermMemory}
                      disabled={loading}
                      style={{
                        padding: "10px 20px",
                        background: "rgba(220, 38, 38, 0.2)",
                        border: "1px solid rgba(220, 38, 38, 0.5)",
                        borderRadius: "8px",
                        color: "#ef4444",
                        cursor: loading ? "not-allowed" : "pointer",
                        display: "flex",
                        alignItems: "center",
                        gap: "8px",
                        fontSize: "13px",
                        fontWeight: "500"
                      }}
                    >
                      <Trash2 size={16} />
                      {loading ? "Clearing..." : "Clear Long-Term Memory"}
                    </button>
                  </div>

                  {/* Status Message */}
                  {statusMessage && (
                    <div style={{
                      padding: "12px",
                      background: "rgba(255, 255, 255, 0.05)",
                      border: "1px solid rgba(255, 255, 255, 0.1)",
                      borderRadius: "8px",
                      fontSize: "13px",
                      color: "rgba(255, 255, 255, 0.9)"
                    }}>
                      {statusMessage}
                    </div>
                  )}
                </div>

                {/* Info Box */}
                <div style={{
                  marginTop: "20px",
                  padding: "12px",
                  background: "rgba(56, 189, 248, 0.1)",
                  border: "1px solid rgba(56, 189, 248, 0.3)",
                  borderRadius: "8px",
                  fontSize: "12px",
                  color: "rgba(255,255,255,0.8)"
                }}>
                  <strong>💡 How Long-Term Memory Works:</strong>
                  <ul style={{ marginTop: "8px", paddingLeft: "20px" }}>
                    <li>Learns from successful tasks (e.g., "User prefers Chrome")</li>
                    <li>Stores personal info when mentioned (e.g., your name)</li>
                    <li>Remembers across sessions (permanent until cleared)</li>
                    <li>Separate from conversation history (chat messages)</li>
                  </ul>
                </div>
              </div>
            )}

            {profileStatus && (
              <div style={{ padding: "10px", fontSize: "13px", color: "rgba(255,255,255,0.9)" }}>
                {profileStatus}
              </div>
            )}
            <div className="settings-actions">
              <button className="settings-btn-save" onClick={handleSave} disabled={profileLoading}>
                {profileLoading ? "Saving..." : "Save Changes"}
              </button>
              <button className="settings-btn-cancel" onClick={onClose}>
                Cancel
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default SettingsModal;