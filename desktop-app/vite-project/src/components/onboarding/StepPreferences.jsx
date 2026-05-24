// StepPreferences.jsx — Fixed:
//  • Language-aware TTS
//  • When user picks Arabic, immediately calls screenReader.setLanguage("ar")
//    so all subsequent pages speak in Egyptian Arabic (req 5)

import React, { useEffect, useMemo, useState } from "react";
import BlurText from "./BlurText";
import ShinyText from "./ShinyText";
import screenReader from "../../utils/ScreenReader";

const languages = ["English", "العربية"];
const voices    = ["Gacrux", "orpheus-english", "orpheus-arabic"];
const themes    = ["dark", "light", "auto"];
const builtInProviders = [
  { id: "openai", name: "OpenAI", logo: "https://cdn.simpleicons.org/openai" },
  { id: "anthropic", name: "Anthropic", logo: "https://cdn.simpleicons.org/anthropic" },
  { id: "groq", name: "Groq", logo: "https://cdn.simpleicons.org/groq" },
  { id: "ollama", name: "Ollama (Local)", logo: "https://ollama.com/public/ollama.png" },
];

const StepPreferences = ({ onNext, data, setData, lang = "en" }) => {
  const isAr = lang === "ar" || data?.preferences?.language === "العربية";
  const [showApiKey, setShowApiKey] = useState(false);
  const [providerTab, setProviderTab] = useState("builtin");
  const [customDraft, setCustomDraft] = useState({ name: "", baseUrl: "", model: "", apiKey: "" });

  const llmPrefs = useMemo(() => {
    return {
      provider: data?.preferences?.llm?.provider || "ollama",
      model: data?.preferences?.llm?.model || "llama3.1:8b",
      apiKey: data?.preferences?.llm?.apiKey || "",
      baseUrl: data?.preferences?.llm?.baseUrl || "http://localhost:11434",
      customModels: data?.preferences?.llm?.customModels || [],
      customProviders: data?.preferences?.llm?.customProviders || [],
    };
  }, [data]);

  useEffect(() => {
    const textEn = "Your preferences! Customize AURA to feel right for you. You can always change these later in Settings.";
    const textAr = "تفضيلاتك! خلي أورا تحس إنها ليك. تقدر تغير دي بعدين في الإعدادات.";
    screenReader.stop();
    screenReader.speak(isAr ? textAr : textEn);
    return () => screenReader.stop();
  }, [isAr]);

  const toggle = (key, value) => {
    const updated = { ...data, preferences: { ...data.preferences, [key]: value } };
    setData(updated);

    // Req 5: immediately update TTS language when user picks a language
    if (key === "language") {
      const newLang = value === "العربية" ? "ar" : "en";
      screenReader.setLanguage(newLang);
    }
  };

  const patchLLM = (patch) => {
    setData({
      ...data,
      preferences: {
        ...data.preferences,
        llm: {
          ...llmPrefs,
          ...patch,
        },
      },
    });
  };

  const addCustomProvider = () => {
    const name = customDraft.name.trim();
    const baseUrl = customDraft.baseUrl.trim();
    const model = customDraft.model.trim();
    if (!name || !baseUrl || !model) return;

    const customProvider = {
      id: name.toLowerCase().replace(/\s+/g, "-"),
      name,
      baseUrl,
      model,
      apiKey: customDraft.apiKey.trim(),
      logo: "",
    };

    patchLLM({
      customProviders: [...llmPrefs.customProviders, customProvider],
      customModels: [...llmPrefs.customModels, model],
      provider: customProvider.id,
      model,
      baseUrl,
      apiKey: customDraft.apiKey.trim(),
    });

    setCustomDraft({ name: "", baseUrl: "", model: "", apiKey: "" });
    setProviderTab("builtin");
  };

  const launchGoogleHumanCheck = async () => {
    try {
      await window.electronAPI?.openExternalUrl?.("https://accounts.google.com/");
      setData({
        ...data,
        preferences: {
          ...data.preferences,
          google_human_verified: true,
        },
      });
    } catch {
      // no-op
    }
  };

  const labelMap = isAr
    ? {
      lang: "اللغة المفضلة",
      theme: "المظهر",
      voice: "الصوت",
      llm: "مزود الذكاء الاصطناعي",
      continue: "كمل →",
      humanCheck: "إثبات إنك إنسان",
    }
    : {
      lang: "Preferred language",
      theme: "Theme",
      voice: "Voice",
      llm: "LLM Provider",
      continue: "Continue →",
      humanCheck: "Prove You're Human",
    };

  const themeLabels = isAr
    ? { dark: "داكن", light: "فاتح", auto: "تلقائي" }
    : { dark: "Dark", light: "Light", auto: "Auto" };

  return (
    <div className="onboarding-step">
      <h2 className="onboarding-title">
        <BlurText text={isAr ? "تفضيلاتك" : "Your preferences"} delay={50} />
      </h2>
      <p className="onboarding-subtitle">
        {isAr
          ? "خلي أورا تحس إنها ليك. تقدر تغير دي بعدين في الإعدادات."
          : "Customize AURA to feel right for you. You can always change these later in Settings."}
      </p>

      <div className="pref-group">
        <label className="pref-label">{labelMap.lang}</label>
        <div className="pref-options">
          {languages.map((l) => (
            <button
              key={l}
              className={`pref-chip ${data.preferences.language === l ? "selected" : ""}`}
              onClick={() => toggle("language", l)}
            >
              {l}
            </button>
          ))}
        </div>
      </div>

      <div className="pref-group">
        <label className="pref-label">{labelMap.theme}</label>
        <div className="pref-options">
          {themes.map((t) => (
            <button
              key={t}
              className={`pref-chip ${data.preferences.theme === t ? "selected" : ""}`}
              onClick={() => toggle("theme", t)}
            >
              {themeLabels[t]}
            </button>
          ))}
        </div>
      </div>

      <div className="pref-group">
        <label className="pref-label">{labelMap.voice}</label>
        <div className="pref-options">
          {voices.map((v) => (
            <button
              key={v}
              className={`pref-chip ${data.preferences.voice === v ? "selected" : ""}`}
              onClick={() => toggle("voice", v)}
            >
              {v}
            </button>
          ))}
        </div>
      </div>

      <div className="pref-group" role="region" aria-label={labelMap.llm}>
        <label className="pref-label">{labelMap.llm}</label>

        <div className="pref-options" style={{ marginBottom: 8 }}>
          <button className={`pref-chip ${providerTab === "builtin" ? "selected" : ""}`} onClick={() => setProviderTab("builtin")}>
            {isAr ? "مزودات جاهزة" : "Built-in Providers"}
          </button>
          <button className={`pref-chip ${providerTab === "custom" ? "selected" : ""}`} onClick={() => setProviderTab("custom")}>
            {isAr ? "أضف API/Model" : "Add API/Model"}
          </button>
        </div>

        {providerTab === "builtin" && (
          <>
            <div className="pref-options" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))", gap: 10 }}>
              {[...builtInProviders, ...llmPrefs.customProviders].map((provider) => (
                <button
                  key={provider.id}
                  className={`pref-chip ${llmPrefs.provider === provider.id ? "selected" : ""}`}
                  onClick={() => {
                    patchLLM({
                      provider: provider.id,
                      model: provider.model || llmPrefs.model,
                      baseUrl: provider.baseUrl || llmPrefs.baseUrl,
                      apiKey: provider.apiKey || llmPrefs.apiKey,
                    });
                  }}
                  style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 8 }}
                >
                  {provider.logo ? (
                    <img src={provider.logo} alt="" width={16} height={16} style={{ borderRadius: 4 }} />
                  ) : (
                    <span aria-hidden="true">⚙️</span>
                  )}
                  <span>{provider.name}</span>
                </button>
              ))}
            </div>

            <div className="voice-input-wrapper" style={{ marginTop: 10 }}>
              <label className="onboarding-input-label" htmlFor="llm-model-input">
                {isAr ? "الموديل" : "Model"}
              </label>
              <div className="voice-input-container">
                <input
                  id="llm-model-input"
                  className="onboarding-input"
                  value={llmPrefs.model}
                  onChange={(e) => patchLLM({ model: e.target.value })}
                  placeholder={isAr ? "مثال: gpt-4.1-mini" : "e.g. gpt-4.1-mini"}
                />
              </div>
            </div>

            <div className="voice-input-wrapper" style={{ marginTop: 10 }}>
              <label className="onboarding-input-label" htmlFor="llm-key-input">
                {isAr ? "API Key (اختياري)" : "API Key (optional)"}
              </label>
              <div className="voice-input-container">
                <input
                  id="llm-key-input"
                  type={showApiKey ? "text" : "password"}
                  className="onboarding-input"
                  value={llmPrefs.apiKey}
                  onChange={(e) => patchLLM({ apiKey: e.target.value })}
                  placeholder={isAr ? "اتركه فاضي لاستخدام Ollama المحلي" : "Leave empty to use local Ollama"}
                />
                <button className="voice-input-btn" type="button" onClick={() => setShowApiKey((v) => !v)}>
                  {showApiKey ? (isAr ? "إخفاء" : "Hide") : (isAr ? "إظهار" : "Show")}
                </button>
              </div>
            </div>
          </>
        )}

        {providerTab === "custom" && (
          <div style={{ display: "grid", gap: 10 }}>
            <div className="voice-input-wrapper">
              <label className="onboarding-input-label" htmlFor="custom-provider-name">{isAr ? "اسم المزود" : "Provider Name"}</label>
              <div className="voice-input-container">
                <input id="custom-provider-name" className="onboarding-input" value={customDraft.name} onChange={(e) => setCustomDraft({ ...customDraft, name: e.target.value })} />
              </div>
            </div>
            <div className="voice-input-wrapper">
              <label className="onboarding-input-label" htmlFor="custom-provider-url">{isAr ? "Base URL" : "Base URL"}</label>
              <div className="voice-input-container">
                <input id="custom-provider-url" className="onboarding-input" value={customDraft.baseUrl} onChange={(e) => setCustomDraft({ ...customDraft, baseUrl: e.target.value })} />
              </div>
            </div>
            <div className="voice-input-wrapper">
              <label className="onboarding-input-label" htmlFor="custom-provider-model">{isAr ? "الموديل" : "Model"}</label>
              <div className="voice-input-container">
                <input id="custom-provider-model" className="onboarding-input" value={customDraft.model} onChange={(e) => setCustomDraft({ ...customDraft, model: e.target.value })} />
              </div>
            </div>
            <div className="voice-input-wrapper">
              <label className="onboarding-input-label" htmlFor="custom-provider-key">{isAr ? "API Key" : "API Key"}</label>
              <div className="voice-input-container">
                <input id="custom-provider-key" className="onboarding-input" value={customDraft.apiKey} onChange={(e) => setCustomDraft({ ...customDraft, apiKey: e.target.value })} />
              </div>
            </div>
            <button type="button" className="onboarding-btn secondary" onClick={addCustomProvider}>
              {isAr ? "إضافة المزود" : "Add Provider"}
            </button>
          </div>
        )}
      </div>

      <div className="pref-group" role="region" aria-label={labelMap.humanCheck}>
        <label className="pref-label">{labelMap.humanCheck}</label>
        <p className="onboarding-subtitle" style={{ marginBottom: 8 }}>
          {isAr
            ? "قبل الأتمتة، افتح حساب Google في تبويب Chrome عادي مرة واحدة لتفادي كشف البوت."
            : "Before automation, log in to Google once in a regular Chrome tab to reduce bot-detection friction."}
        </p>
        <button type="button" className="onboarding-btn secondary" onClick={launchGoogleHumanCheck}>
          {isAr ? "🚀 خطوة إثبات الإنسانية" : "🚀 Prove You're Human"}
        </button>
        {data?.preferences?.google_human_verified && (
          <p className="onboarding-hint success">{isAr ? "✓ تم التحقق بنجاح" : "✓ Human check completed"}</p>
        )}
      </div>

      <button className="onboarding-btn primary" onClick={onNext}>
        <ShinyText text={labelMap.continue} speed={3} />
      </button>
    </div>
  );
};

export default StepPreferences;