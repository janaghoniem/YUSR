// StepAPIConfig.jsx — BYOK (Bring Your Own Key) step in onboarding
import React, { useState, useRef, useEffect } from "react";
import { Key, Eye, EyeOff, CheckCircle, ChevronDown, Check } from "lucide-react";

const PROVIDERS = [
  { id: "openai",    label: "OpenAI",          models: ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo"] },
  { id: "anthropic", label: "Anthropic",        models: ["claude-opus-4-6", "claude-sonnet-4-6", "claude-haiku-4-5-20251001"] },
  { id: "google",    label: "Google Gemini",    models: ["gemini-2.0-flash", "gemini-1.5-pro", "gemini-1.5-flash"] },
  { id: "groq",      label: "Groq",             models: ["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "mixtral-8x7b-32768"] },
];

/* ── Reusable dark-themed custom dropdown ── */
const DarkSelect = ({ value, onChange, options, placeholder, id }) => {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  // Close on outside click
  useEffect(() => {
    if (!open) return;
    const handler = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  // Close on Escape
  useEffect(() => {
    if (!open) return;
    const handler = (e) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [open]);

  const selected = options.find((o) => o.value === value);

  return (
    <div ref={ref} style={{ position: "relative", width: "100%" }}>
      {/* Trigger button */}
      <button
        id={id}
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="listbox"
        aria-expanded={open}
        style={{
          width: "100%",
          display: "flex", alignItems: "center", justifyContent: "space-between",
          padding: "10px 14px",
          background: "var(--bg-overlay)",
          border: `1px solid ${open ? "rgba(255,61,154,0.38)" : "rgba(255,255,255,0.07)"}`,
          borderRadius: "var(--r-sm)",
          color: selected ? "var(--text-primary)" : "var(--text-disabled)",
          fontSize: 14, fontFamily: "inherit", cursor: "pointer",
          boxShadow: open ? "0 0 0 3px rgba(255,61,154,0.09)" : "none",
          transition: "border-color 120ms, box-shadow 120ms",
        }}
      >
        <span>{selected ? selected.label : placeholder}</span>
        <ChevronDown
          size={15}
          style={{
            color: "var(--text-muted)",
            transform: open ? "rotate(180deg)" : "rotate(0deg)",
            transition: "transform 180ms",
            flexShrink: 0,
          }}
        />
      </button>

      {/* Dropdown panel */}
      {open && (
        <ul
          role="listbox"
          aria-label={placeholder}
          style={{
            position: "absolute", top: "calc(100% + 6px)", left: 0, right: 0,
            zIndex: 9999,
            background: "#1e1610",
            border: "1px solid rgba(255,255,255,0.12)",
            borderRadius: "var(--r-md)",
            boxShadow: "0 16px 48px rgba(0,0,0,0.65)",
            padding: "6px",
            margin: 0,
            listStyle: "none",
            maxHeight: 220,
            overflowY: "auto",
            scrollbarWidth: "thin",
            scrollbarColor: "var(--bg-muted) transparent",
          }}
        >
          {options.map((opt) => {
            const isActive = opt.value === value;
            return (
              <li
                key={opt.value}
                role="option"
                aria-selected={isActive}
                onClick={() => { onChange(opt.value); setOpen(false); }}
                style={{
                  display: "flex", alignItems: "center", justifyContent: "space-between",
                  padding: "9px 12px",
                  borderRadius: "var(--r-sm)",
                  cursor: "pointer",
                  color: isActive ? "var(--pink-200)" : "var(--text-primary)",
                  background: isActive ? "rgba(255,61,154,0.12)" : "transparent",
                  fontSize: 13.5,
                  transition: "background 80ms, color 80ms",
                }}
                onMouseEnter={(e) => {
                  if (!isActive) e.currentTarget.style.background = "rgba(255,255,255,0.06)";
                }}
                onMouseLeave={(e) => {
                  if (!isActive) e.currentTarget.style.background = "transparent";
                }}
              >
                <span>{opt.label}</span>
                {isActive && <Check size={14} style={{ color: "var(--pink-300)", flexShrink: 0 }} />}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
};

const StepAPIConfig = ({ onNext, data, setData, userId, lang = "en" }) => {
  const [provider,   setProvider]   = useState(data?.preferences?.llm?.provider   || "");
  const [apiKey,     setApiKey]     = useState(data?.preferences?.llm?.api_key    || "");
  const [model,      setModel]      = useState(data?.preferences?.llm?.model      || "");
  const [showKey,    setShowKey]    = useState(false);
  const [saving,     setSaving]     = useState(false);
  const [saved,      setSaved]      = useState(false);
  const [error,      setError]      = useState("");

  const t = (en, ar) => (lang === "ar" ? ar : en);

  const selectedProvider = PROVIDERS.find((p) => p.id === provider);

  const handleProviderChange = (pid) => {
    setProvider(pid);
    setModel("");
    setSaved(false);
    setError("");
  };

  const handleSave = async () => {
    if (!provider || !apiKey.trim()) {
      setError(t("Please select a provider and enter your API key.", "يرجى اختيار مزود وإدخال مفتاح API."));
      return;
    }
    setSaving(true);
    setError("");
    try {
      const currentUserId = localStorage.getItem("userId") || userId || "";
      const res = await fetch("http://localhost:8000/byok/keys", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          user_id:  currentUserId,
          provider,
          api_key:  apiKey.trim(),
          model:    model || selectedProvider?.models[0] || "",
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || t("Failed to save key.", "فشل حفظ المفتاح."));
      }
      // Persist into formData.preferences.llm so create-account sends it
      setData((prev) => ({
        ...prev,
        preferences: {
          ...prev.preferences,
          llm: { provider, api_key: apiKey.trim(), model: model || selectedProvider?.models[0] || "" },
        },
      }));
      setSaved(true);
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  };

  const handleSkip = () => {
    // Clear any partial llm config and proceed
    setData((prev) => ({
      ...prev,
      preferences: { ...prev.preferences, llm: null },
    }));
    onNext();
  };

  return (
    <div className="onboarding-step" dir={lang === "ar" ? "rtl" : "ltr"}>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 6 }}>
        <div
          style={{
            width: 42, height: 42, borderRadius: 12,
            background: "rgba(255,61,154,0.14)",
            border: "1px solid rgba(255,61,154,0.28)",
            display: "flex", alignItems: "center", justifyContent: "center",
            color: "var(--pink-300)", flexShrink: 0,
          }}
        >
          <Key size={20} />
        </div>
        <div>
          <h2 className="onboarding-title" style={{ marginBottom: 0 }}>
            {t("Your AI Provider (optional)", "مزود الذكاء الاصطناعي (اختياري)")}
          </h2>
        </div>
      </div>

      <p className="onboarding-subtitle">
        {t(
          "Bring your own API key to use your preferred LLM. AURA works out-of-the-box — skip this if you're not sure.",
          "أضف مفتاح API الخاص بك لاستخدام نموذج الذكاء الاصطناعي المفضل لديك. يعمل AURA بشكل افتراضي — تخطَّ هذه الخطوة إذا لم تكن متأكدًا."
        )}
      </p>

      {/* Provider selector */}
      <div className="pref-group" style={{ marginBottom: 14 }}>
        <label className="settings-label" style={{ fontSize: 12.5, color: "var(--text-secondary)", fontWeight: 500, marginBottom: 6, display: "block" }}>
          {t("Provider", "المزود")}
        </label>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
          {PROVIDERS.map((p) => (
            <button
              key={p.id}
              type="button"
              onClick={() => handleProviderChange(p.id)}
              className={`pref-chip${provider === p.id ? " selected" : ""}`}
              aria-pressed={provider === p.id}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

      {/* Model selector — only shown once provider is picked */}
      {selectedProvider && (
        <div className="pref-group" style={{ marginBottom: 14 }}>
          <label
            htmlFor="byok-model"
            className="settings-label"
            style={{ fontSize: 12.5, color: "var(--text-secondary)", fontWeight: 500, marginBottom: 6, display: "block" }}
          >
            {t("Model", "النموذج")}
          </label>
          <DarkSelect
            id="byok-model"
            value={model}
            onChange={(v) => { setModel(v); setSaved(false); }}
            placeholder={t("Default (recommended)", "افتراضي (موصى به)")}
            options={[
              { value: "", label: t("Default (recommended)", "افتراضي (موصى به)") },
              ...selectedProvider.models.map((m) => ({ value: m, label: m })),
            ]}
          />
        </div>
      )}

      {/* API Key input */}
      {selectedProvider && (
        <div className="pref-group" style={{ marginBottom: 18 }}>
          <label
            htmlFor="byok-apikey"
            className="settings-label"
            style={{ fontSize: 12.5, color: "var(--text-secondary)", fontWeight: 500, marginBottom: 6, display: "block" }}
          >
            {t("API Key", "مفتاح API")}
          </label>
          <div className="voice-input-container" style={{ gap: 0 }}>
            <input
              id="byok-apikey"
              type={showKey ? "text" : "password"}
              className="onboarding-input"
              placeholder={t("Paste your API key here", "الصق مفتاح API هنا")}
              value={apiKey}
              onChange={(e) => { setApiKey(e.target.value); setSaved(false); setError(""); }}
              autoComplete="off"
              spellCheck={false}
              aria-describedby={error ? "byok-error" : undefined}
            />
            <button
              type="button"
              className="voice-input-btn"
              onClick={() => setShowKey((v) => !v)}
              aria-label={showKey ? t("Hide key", "إخفاء المفتاح") : t("Show key", "إظهار المفتاح")}
              title={showKey ? t("Hide", "إخفاء") : t("Show", "إظهار")}
            >
              {showKey ? <EyeOff size={16} /> : <Eye size={16} />}
            </button>
          </div>
          <p className="onboarding-hint" style={{ marginTop: 6 }}>
            {t(
              "Keys are encrypted and stored securely. They never leave your account.",
              "تُشفَّر المفاتيح وتُخزَّن بأمان. لن تغادر حسابك أبدًا."
            )}
          </p>
        </div>
      )}

      {/* Error */}
      {error && (
        <p id="byok-error" className="onboarding-error" role="alert" aria-live="assertive" style={{ marginBottom: 12 }}>
          {error}
        </p>
      )}

      {/* Actions */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 10, marginTop: 4, alignItems: "center" }}>
        {selectedProvider && !saved && (
          <button
            type="button"
            className="onboarding-btn primary"
            onClick={handleSave}
            disabled={saving || !apiKey.trim()}
            aria-busy={saving}
          >
            {saving
              ? t("Saving…", "جارٍ الحفظ…")
              : t("Save & Continue", "احفظ وتابع")}
          </button>
        )}

        {saved && (
          <button
            type="button"
            className="onboarding-btn primary"
            onClick={onNext}
            style={{ display: "flex", alignItems: "center", gap: 8 }}
          >
            <CheckCircle size={16} />
            {t("Continue", "تابع")}
          </button>
        )}

        <button
          type="button"
          className="onboarding-btn secondary"
          onClick={handleSkip}
        >
          {t("Skip for now", "تخطَّ الآن")}
        </button>
      </div>

      {saved && (
        <p className="onboarding-hint success" role="status" aria-live="polite" style={{ marginTop: 10 }}>
          ✓ {t("API key saved successfully.", "تم حفظ مفتاح API بنجاح.")}
        </p>
      )}
    </div>
  );
};

export default StepAPIConfig;