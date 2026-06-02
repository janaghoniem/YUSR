// App.jsx
import React, { useState, useRef, useEffect, useCallback, useMemo } from "react";
import Sidebar from "./components/SideBar";
import HeaderContent from "./components/HeaderContent";
import VoiceControls from "./components/VoiceControls";
import SettingsModal from "./components/SettingsModal";
import OnboardingPage from "./components/onboarding/OnboardingPage";
import LoginPage from "./components/onboarding/LoginPage";
import ChatHistory from "./components/ChatHistory";
import TitleBar from "./components/TitleBar";
import Aurora from "./components/onboarding/Aurora";
import { TaskQueuesPopover } from "./components/TaskQueuesPopover";
import { DraftPanel } from "./components/DraftPanel";
import screenReader from "./utils/ScreenReader";
import { requestTranscription } from "./utils/transcribeClient";
import {
  classifyInterruptSemantic,
  classifyPolarIntent,
  isReadAloudIntentSemantic,
} from "./utils/semanticIntent";
import { Mic, Pause, Square, X, ArrowUpRight, Sparkles, Cpu, Waves } from "lucide-react";

// --- Device ID helpers ---
const getOS = () => {
  if (typeof navigator === "undefined") return "desktop";
  // Use userAgentData if available (modern), fall back to navigator.platform
  const platform = (navigator.userAgentData?.platform || navigator.platform || "").toLowerCase();
  if (platform.includes("win")) return "windows";
  if (platform.includes("mac")) return "mac";
  if (platform.includes("linux")) return "linux";
  return "desktop";
};

const getOrCreateDeviceId = () => {
  if (typeof localStorage === "undefined") {
    return `desktop-${Math.random().toString(36).substring(2, 8)}`;
  }

  const stored = localStorage.getItem("desktopDeviceId");
  if (stored && stored !== "desktop") return stored;

  const os = getOS();
  const suffix = Math.random().toString(36).substring(2, 8);
  const newDeviceId = `${os}-${suffix}`;
  localStorage.setItem("desktopDeviceId", newDeviceId);
  console.log("[Device] Created new device ID:", newDeviceId);
  return newDeviceId;
};

function App() {
  /* ---------- STATE ---------- */
  const [orbState, setOrbState] = useState("idle");
  const [userMessage, setUserMessage] = useState("");
  const [assistantMessage, setAssistantMessage] = useState("");
  // user ID

// App.jsx - Fix userId initialization
  const [userId, setUserId] = useState(() => {
      // Only use stored userId if onboarding is NOT complete
      // If we're logged out, we need a new ID for new accounts
      const onboardingComplete = localStorage.getItem("onboardingComplete") === "true";
      const stored = localStorage.getItem("userId");
      
      if (onboardingComplete && stored) {
          console.log("[Auth] Using existing user ID:", stored);
          return stored;
      }
      
      // Generate new user ID for new accounts
      const newUserId = `user_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
      console.log("[Auth] Created new user ID for new account:", newUserId);
      return newUserId;
  });

  
  // ✅ AUTH STATE — "app" | "login" | "onboard"
  const [authState, setAuthState] = useState(() => {
    const onboardingComplete = localStorage.getItem("onboardingComplete") === "true";
    const storedUserId = localStorage.getItem("userId");
    if (onboardingComplete && storedUserId) return "app";
    return "login";
  });
  // ✅ SESSION ID - Can be changed when switching chats or creating new chat
  const [sessionId, setSessionId] = useState(() => {
      const stored = localStorage.getItem("currentSessionId");
      if (stored) {
          console.log("[Session] Using existing session:", stored);
          return stored;
      }
      
      const newSessionId = `session_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
      localStorage.setItem("currentSessionId", newSessionId);
      console.log("[Session] Created new session:", newSessionId);
      return newSessionId;
  });
  const [deviceId, setDeviceId] = useState(() => getOrCreateDeviceId());
  const [clarificationResponseToId, setClarificationResponseToId] = useState(null);
  const [isRecording, setIsRecording] = useState(false);
  const [chatMode, setChatMode] = useState(false);
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(true); // ✅ Default collapsed
  const [showSettings, setShowSettings] = useState(false);
  const [deviceType, setDeviceType] = useState("desktop");
  const [ttsVoice, setTtsVoice] = useState(() => localStorage.getItem("ttsVoice") || "Gacrux");
  const [preferredLanguage, setPreferredLanguage] = useState(() => localStorage.getItem("preferredLanguage") || localStorage.getItem("appLanguage") || localStorage.getItem("userLanguage") || "en");
  const [screenSize, setScreenSize] = useState("desktop");
  const [userName, setUserName] = useState(() => {
    const stored = localStorage.getItem("userName");
    console.log("[App] Initializing userName from localStorage:", stored);
    return stored || "User";
  });
  const [thinkingSteps, setThinkingSteps] = useState([]);
  const [isThinking, setIsThinking] = useState(false);
  const [coordinatorActive, setCoordinatorActive] = useState(false);
  const [auraStatus, setAuraStatus] = useState("starting");
  const [wakePulse, setWakePulse] = useState(false);
  // True when server-provided SSE thinking stream is connected
  const [sseConnected, setSseConnected] = useState(false);
  const [chats, setChats] = useState([]);
  const [chatTitle, setChatTitle] = useState("New Chat");
  const [viewingChat, setViewingChat] = useState(null); // { sessionId, title, messages }
  const [loadingHistory, setLoadingHistory] = useState(false);
  const hasSpokenHeaderWelcomeRef = useRef(false);

  // WebSocket state
  const wsRef = useRef(null);
  const [wsConnected, setWsConnected] = useState(false);
  const wsReconnectTimer = useRef(null);

  // Execution mode: normal, transparent, or widget
  const [executionMode, setExecutionMode] = useState("normal");
  const [widgetText, setWidgetText] = useState("");
  const [contextSnapshot, setContextSnapshot] = useState(null);

  // Structured response state
  const [structuredResponse, setStructuredResponse] = useState(null);
  const [offerReadAloud, setOfferReadAloud] = useState(false);
  const [queueSnapshot, setQueueSnapshot] = useState({ active: [], pending: [], deferred: [] });
  const [workspaceArtifacts, setWorkspaceArtifacts] = useState([]);
  const [activeWorkspacePanelId, setActiveWorkspacePanelId] = useState(null);
  const [draftFlow, setDraftFlow] = useState({
    active: false,
    awaitingListenChoice: false,
    awaitingPageApproval: false,
    awaitingFinalApproval: false,
    pageIndex: 0,
    pages: [],
    fullContent: "",
    confirmationId: null,
  });

  // Completed tasks tracking for the new UI
  const [completedQueuedTasks, setCompletedQueuedTasks] = useState([]);
  const [completedScheduledTasks, setCompletedScheduledTasks] = useState([]);
  const [liveCaptionPage, setLiveCaptionPage] = useState(0);
  const [typedCaption, setTypedCaption] = useState("");
  const prevQueueSnapshotRef = useRef({ active: [], pending: [], deferred: [] });
  
  // ✅ Cross-platform session-scoped tasks
  const [sessionScopedTasks, setSessionScopedTasks] = useState([]);

  const [userLanguage, setUserLanguage] = useState(() => localStorage.getItem("userLanguage") || null);
  const userLanguageRef = useRef(localStorage.getItem("userLanguage") || null);
  const preferredLanguageRef = useRef(localStorage.getItem("preferredLanguage") || localStorage.getItem("appLanguage") || localStorage.getItem("userLanguage") || "en");
  // Whether to vocalize thinking steps
  const [vocalizeSteps, setVocalizeSteps] = useState(true);
  // Ref to track the last spoken step index (avoid re-speaking)
  const lastSpokenStepRef = useRef(-1);
  // Ref for vocalizeStep so WS/SSE closures always get the latest version
  const vocalizeStepRef = useRef(null);
  const thinkingSpeechQueueRef = useRef([]);
  const thinkingSpeechRunningRef = useRef(false);
  const wakeWatchdogRef = useRef(null);
  const wakePulseTimerRef = useRef(null);
  const manualCaptureCancelledRef = useRef(false);
  const silenceFrameRef = useRef(null);
  const noSpeechTimeoutRef = useRef(null);
  const userSpokeRef = useRef(false);

  const mediaStreamRef = useRef(null);
  const audioContextRef = useRef(null);
  const sourceNodeRef = useRef(null);
  const processorNodeRef = useRef(null);
  const processorSinkRef = useRef(null);
  const pcmChunksRef = useRef([]);
  const sampleRateRef = useRef(16000);
  const recordingActiveRef = useRef(false);
  const speechDetectedRef = useRef(false);
  const lastSpeechAtRef = useRef(0);
  const recordingStartedAtRef = useRef(0);
  const audioRef = useRef(new Audio());
  const draftDecisionTimerRef = useRef(null);
  const draftFlowRef = useRef(draftFlow);

  // Track completed tasks from queue changes
  useEffect(() => {
    const prev = prevQueueSnapshotRef.current;
    const currentActive = queueSnapshot.active || [];
    const currentPending = queueSnapshot.pending || [];
    const currentDeferred = queueSnapshot.deferred || [];
    const prevActive = prev.active || [];
    const prevPending = prev.pending || [];
    const prevDeferred = prev.deferred || [];
    
    const allCurrent = [...currentActive, ...currentPending, ...currentDeferred];
    const allPrev = [...prevActive, ...prevPending, ...prevDeferred];
    
    const completed = allPrev.filter(prevTask => 
      !allCurrent.some(currTask => currTask.task_id === prevTask.task_id)
    );
    
    if (completed.length > 0) {
      setCompletedQueuedTasks(prevCompleted => [
        ...completed.map(t => ({ ...t, completedAt: Date.now() })),
        ...prevCompleted
      ].slice(0, 50));
    }
    
    prevQueueSnapshotRef.current = { active: currentActive, pending: currentPending, deferred: currentDeferred };
  }, [queueSnapshot]);

  // Similar for scheduled tasks if you have them (from separate state)
  // ...

  useEffect(() => {
    draftFlowRef.current = draftFlow;
  }, [draftFlow]);

  const playWakePing = useCallback(() => {
    try {
      if (!window.AudioContext) return;
      const ctx = new window.AudioContext();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "sine";
      osc.frequency.setValueAtTime(920, ctx.currentTime);
      gain.gain.setValueAtTime(0.0001, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.08, ctx.currentTime + 0.015);
      gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.24);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start();
      osc.stop(ctx.currentTime + 0.25);
      osc.onended = () => ctx.close().catch(() => {});
    } catch (error) {
      console.warn("[Wake] Ping sound failed:", error);
    }
  }, []);

  const isArabicText = useCallback((text) => /[\u0600-\u06FF]/.test(text || ""), []);
  const detectLanguageFromText = useCallback((text) => {
    if (!text || typeof text !== "string") return userLanguage || "en";
    return isArabicText(text) ? "ar" : "en";
  }, [isArabicText, userLanguage]);
  const t = useCallback((en, ar) => (userLanguage === "ar" ? ar : en), [userLanguage]);
  const rememberUserLanguageFromText = useCallback((text) => {
    const detected = detectLanguageFromText(text);
    if (detected && (detected === "ar" || !userLanguage)) {
      userLanguageRef.current = detected;
      setUserLanguage(detected);
      localStorage.setItem("userLanguage", detected);
    }
    return detected;
  }, [detectLanguageFromText, userLanguage]);

  useEffect(() => {
    userLanguageRef.current = userLanguage;
  }, [userLanguage]);

  useEffect(() => {
    preferredLanguageRef.current = preferredLanguage;
    localStorage.setItem("preferredLanguage", preferredLanguage);
    screenReader.setLanguage(preferredLanguage);
    
    // ✅ Set HTML document direction for RTL/LTR support
    const direction = preferredLanguage === "ar" ? "rtl" : "ltr";
    document.documentElement.dir = direction;
    document.documentElement.lang = preferredLanguage;
  }, [preferredLanguage]);

  useEffect(() => {
    if (authState !== "app") {
      hasSpokenHeaderWelcomeRef.current = false;
    }
  }, [authState]);

  // ✅ Fetch session-scoped cross-platform tasks from backend
  useEffect(() => {
    if (!sessionId || !userId || authState !== "app") {
      setSessionScopedTasks([]);
      return;
    }

    const fetchFromEndpoint = async (url) => {
      const response = await fetch(url, {
        method: "GET",
        headers: { "Content-Type": "application/json" },
      });

      if (!response.ok) {
        return null;
      }

      return response.json();
    };

    const fetchSessionTasks = async () => {
      try {
        const crossPlatformUrl = `http://localhost:8000/device/cross-platform-tasks?user_id=${encodeURIComponent(userId)}&device_id=${encodeURIComponent(deviceId)}&session_id=${encodeURIComponent(sessionId)}`;
        const fallbackUrl = `http://localhost:8000/device/${encodeURIComponent(deviceId)}/pending-actions`;

        let data = await fetchFromEndpoint(crossPlatformUrl);
        let tasks = data?.tasks || data?.actions || [];

        if (!data) {
          data = await fetchFromEndpoint(fallbackUrl);
          tasks = data?.actions || data?.tasks || [];
        }

        // Filter to only session-scoped tasks (already filtered by backend)
        setSessionScopedTasks(tasks.map((task, idx) => ({
          id: task.task_id || `xp-${idx}`,
          name: task.task_payload?.description || task.original_request || `Cross-platform task ${idx + 1}`,
          info: task.status || "pending",
          status: task.status,
          device: task.source_platform || "unknown",
        })));

        if (data) {
          console.log(`[SessionTasks] Fetched ${tasks.length} session-scoped tasks`);
        }
      } catch (err) {
        console.warn("[SessionTasks] Failed to fetch:", err);
        // Silently fail - not critical
      }
    };

    // Fetch immediately and then poll periodically
    fetchSessionTasks();
    const pollInterval = setInterval(fetchSessionTasks, 5000); // Poll every 5 seconds

    return () => clearInterval(pollInterval);
  }, [sessionId, userId, authState, deviceId]);

  const normalizeThinkingStep = useCallback((step) => {
    return (step || "").toLowerCase().replace(/[\u{1F300}-\u{1FAFF}]/gu, " ").replace(/[.]{3,}/g, "").replace(/[!؟?]+$/g, "").replace(/\s+/g, " ").trim();
  }, []);
  const translateThinkingStep = useCallback((step) => {
    if (!step || userLanguageRef.current !== "ar") return step;
    const normalized = normalizeThinkingStep(step);
    const map = { "processing input": "جاري معالجة الإدخال...", "analyzing your request": "جاري تحليل طلبك...", "checking your preferences": "جاري التحقق من تفضيلاتك...", "processing your request": "جاري تنفيذ طلبك...", "preparing for coordinator": "جاري التحضير للمنسق...", "received your request": "استلمت طلبك...", "preparing tasks": "جاري تجهيز الخطوات...", "creating execution plan": "جاري إنشاء خطة التنفيذ...", "searching": "جاري البحث...", "analyzing": "جاري التحليل...", "processing": "جاري المعالجة...", "responding": "جاري تجهيز الرد...", "thinking": "جاري التفكير..." };
    return map[normalized] || step;
  }, [normalizeThinkingStep]);

  const extractReadableText = useCallback((value) => {
    if (value == null) return "";
    if (typeof value === "string") {
      const trimmed = value.trim();
      if (!trimmed) return "";
      if ((trimmed.startsWith("{") && trimmed.endsWith("}")) || (trimmed.startsWith("[") && trimmed.endsWith("]"))) {
        try { const parsed = JSON.parse(trimmed); return extractReadableText(parsed); } catch { return trimmed; }
      }
      return trimmed;
    }
    if (Array.isArray(value)) return value.map((item) => extractReadableText(item)).filter(Boolean).join("\n\n");
    if (typeof value === "object") {
      const preferredKeys = ["full_content", "content", "spoken_text", "text", "response", "message", "summary", "result"];
      for (const key of preferredKeys) { if (value[key]) { const extracted = extractReadableText(value[key]); if (extracted) return extracted; } }
      if (Array.isArray(value.details)) { const detailText = value.details.map((d) => extractReadableText(d?.content || d?.text || d?.result || d)).filter(Boolean).join("\n\n"); if (detailText) return detailText; }
      return "";
    }
    return String(value);
  }, []);

  const registerWorkspaceArtifacts = useCallback((rawValue, sourceLabel = "Result") => {
    const sourceText = extractReadableText(rawValue);
    if (!sourceText) return;
    const urlMatches = sourceText.match(/https?:\/\/[^\s<>"')]+/gi) || [];
    const fileMatches = sourceText.match(/(?:[A-Za-z]:[\\/][^\n\r<>:"|?*]+?\.[A-Za-z0-9]{1,8}|(?:\.{0,2}[\\/])?(?:[\w\- ]+[\\/])*[\w\- ]+\.(?:txt|md|doc|docx|pdf|csv|json|xlsx|xls|ppt|pptx|py|js|ts|html|css))/gi) || [];
    const uniqueUrls = [...new Set(urlMatches.map((item) => item.trim()))].slice(0, 12);
    const uniqueFiles = [...new Set(fileMatches.map((item) => item.trim()))].slice(0, 12);
    if (!uniqueUrls.length && !uniqueFiles.length) return;
    setWorkspaceArtifacts((prev) => {
      const existingKeys = new Set(prev.map((item) => `${item.type}:${item.value}`));
      const additions = [];
      uniqueUrls.forEach((url, index) => { const key = `url:${url}`; if (existingKeys.has(key)) return; additions.push({ id: `artifact-url-${Date.now()}-${index}`, type: "url", title: `${sourceLabel} URL`, label: url, value: url, sourceLabel }); });
      uniqueFiles.forEach((filePath, index) => { const key = `file:${filePath}`; if (existingKeys.has(key)) return; const normalized = filePath.replace(/\\/g, "/"); const name = normalized.split("/").pop() || normalized; additions.push({ id: `artifact-file-${Date.now()}-${index}`, type: "file", title: `${sourceLabel} File`, label: name, value: filePath, sourceLabel }); });
      return additions.length ? [...additions, ...prev].slice(0, 40) : prev;
    });
  }, [extractReadableText]);

  const stopThinkingSpeech = useCallback(() => { thinkingSpeechQueueRef.current = []; thinkingSpeechRunningRef.current = false; screenReader.stop(); }, []);
  // const clearDraftDecisionTimer = useCallback(() => { if (draftDecisionTimerRef.current) { clearTimeout(draftDecisionTimerRef.current); draftDecisionTimerRef.current = null; } }, []);


  const speakAssistantResponse = useCallback(async (text, languageHint = null) => {
    const normalizedText = extractReadableText(text);
    if (!normalizedText) return;

    const logicalLang = detectLanguageFromText(normalizedText) || languageHint || userLanguageRef.current || "en";
    const lang = logicalLang.startsWith("ar") ? "ar-EG" : "en-US";
    rememberUserLanguageFromText(normalizedText);

    stopThinkingSpeech();
    setOrbState("speaking");

    try {
      const res = await fetch("http://localhost:8000/text-to-speech", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: normalizedText, lang }),
      });

      const data = await res.json();
      if (!res.ok || !data?.audio_data) {
        throw new Error(data?.detail || "TTS generation failed");
      }

      const audioEl = audioRef.current;
      audioEl.pause();
      audioEl.src = `data:audio/mp3;base64,${data.audio_data}`;
      audioEl.currentTime = 0;

      await new Promise((resolve, reject) => {
        audioEl.onended = () => resolve();
        audioEl.onerror = () => reject(new Error("Audio playback failed"));
        audioEl.play().catch(reject);
      });
    } catch (error) {
      console.warn("[TTS] Google speech failed; local fallback is disabled for assistant prompts:", error);
    } finally {
      setOrbState("idle");
      setExecutionMode((prev) => (prev === "transparent" ? "normal" : prev));
    }
  }, [detectLanguageFromText, extractReadableText, rememberUserLanguageFromText, stopThinkingSpeech]);

  const clearDraftDecisionTimer = useCallback(() => {
    if (draftDecisionTimerRef.current) {
      clearTimeout(draftDecisionTimerRef.current);
      draftDecisionTimerRef.current = null;
    }
  }, []);

  useEffect(() => {
    return () => {
      clearDraftDecisionTimer();
    };
  }, [clearDraftDecisionTimer]);

  const sendClarificationAnswer = useCallback(async (answerText, clarificationIdOverride = null) => {
    const clarificationId = clarificationIdOverride || clarificationResponseToId;
    if (!clarificationId) return;

    const payload = {
      type: "clarification_response",
      user_id: userId,
      device_type: deviceType,
      device_id: deviceId,
      clarification_id: clarificationId,
      answer: answerText,
      user_language: userLanguageRef.current || "en",
    };

    setClarificationResponseToId(null);

    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(payload));
      return;
    }

    await fetch("http://localhost:8000/process", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: sessionId,
        user_id: userId,
        input: answerText,
        is_clarification: true,
        clarification_id: clarificationId,
        device_type: deviceType,
        device_id: deviceId,
        user_language: userLanguageRef.current || "en",
      }),
    });
  }, [clarificationResponseToId, deviceId, deviceType, sessionId, userId]);

  const buildDraftPages = useCallback((payload) => {
    const fallbackContent = extractReadableText(payload?.full_content || payload?.draft_content || payload?.question || "");
    const sourcePages = Array.isArray(payload?.content_pages) ? payload.content_pages : [];

    const normalizedPages = sourcePages
      .map((page, index) => {
        const content = extractReadableText(page?.content || page?.page_content || page?.text || "");
        return {
          page_number: Number(page?.page_number || index + 1),
          content,
        };
      })
      .filter((page) => !!page.content);

    if (normalizedPages.length > 0) {
      return {
        pages: normalizedPages,
        fullContent: extractReadableText(payload?.full_content || fallbackContent),
      };
    }

    if (!fallbackContent) {
      return { pages: [], fullContent: "" };
    }

    const paragraphs = fallbackContent
      .split(/\n{2,}/)
      .map((segment) => segment.trim())
      .filter(Boolean);

    const pages = [];
    let cursor = "";
    for (const paragraph of paragraphs) {
      const candidate = cursor ? `${cursor}\n\n${paragraph}` : paragraph;
      if (candidate.length > 950 && cursor) {
        pages.push({ page_number: pages.length + 1, content: cursor });
        cursor = paragraph;
      } else {
        cursor = candidate;
      }
    }
    if (cursor) {
      pages.push({ page_number: pages.length + 1, content: cursor });
    }

    return {
      pages,
      fullContent: fallbackContent,
    };
  }, [extractReadableText]);

  const promptDraftPage = useCallback((targetIndex = null) => {
    clearDraftDecisionTimer();

    const current = draftFlowRef.current;
    const nextIndex = typeof targetIndex === "number" ? targetIndex : current.pageIndex;
    const page = current.pages[nextIndex];

    if (!page) {
      const finalPrompt = t(
        "That is the full draft. Do you approve this content?",
        "ده كامل المحتوى. هل توافق عليه؟"
      );
      setDraftFlow((prev) => ({
        ...prev,
        pageIndex: prev.pages.length > 0 ? prev.pages.length - 1 : 0,
        awaitingPageApproval: false,
        awaitingFinalApproval: true,
      }));
      setAssistantMessage(finalPrompt);
      void speakAssistantResponse(finalPrompt, userLanguageRef.current || "en");
      return;
    }

    const pageLabel = t(
      `Page ${nextIndex + 1} of ${current.pages.length}`,
      `الصفحة ${nextIndex + 1} من ${current.pages.length}`
    );
    const approvePrompt = t(
      "Do you approve this content? Say yes to approve now, or no to continue.",
      "هل توافق على هذا المحتوى؟ قل نعم للموافقة الآن، أو لا للمتابعة."
    );

    setDraftFlow((prev) => ({
      ...prev,
      pageIndex: nextIndex,
      awaitingPageApproval: true,
      awaitingFinalApproval: false,
    }));

    const visibleText = `${pageLabel}\n\n${page.content}\n\n${approvePrompt}`;
    setAssistantMessage(visibleText);
    void speakAssistantResponse(`${pageLabel}. ${page.content}. ${approvePrompt}`, userLanguageRef.current || "en");

    draftDecisionTimerRef.current = setTimeout(() => {
      const latest = draftFlowRef.current;
      if (!latest.awaitingPageApproval) return;

      const upcoming = latest.pageIndex + 1;
      if (upcoming < latest.pages.length) {
        setAssistantMessage(t(
          "No response detected. Continuing to the next page.",
          "لا يوجد رد خلال 10 ثواني. سأكمل للصفحة التالية."
        ));
        promptDraftPage(upcoming);
      } else {
        const finalPrompt = t(
          "That is the full draft. Do you approve this content?",
          "ده كامل المحتوى. هل توافق عليه؟"
        );
        setDraftFlow((prev) => ({
          ...prev,
          awaitingPageApproval: false,
          awaitingFinalApproval: true,
        }));
        setAssistantMessage(finalPrompt);
        void speakAssistantResponse(finalPrompt, userLanguageRef.current || "en");
      }
    }, 10000);
  }, [clearDraftDecisionTimer, speakAssistantResponse, t]);

  // Speech recognition (wake-word)
  const [listening,          setListening]           = useState(false);
  const useElectronWakeWord = false;

  const wakeStoppedRef      = useRef(false); // true = we deliberately stopped
 
  // Start one STT session for wake-word detection
  const startWakeWordListening = useCallback(() => {
    // Vosk wake-word sidecar disabled: Google/Web Speech only for now.
    setListening(false);
  }, [
    useElectronWakeWord,
  ]);

  useEffect(() => {
    if (!useElectronWakeWord || authState !== "app") return;

    const offWake = window.electronAPI.onAuraWakeWord((payload) => {
      const wakeText = String(payload?.text || "")
        .toLowerCase()
        .replace(/[^a-z\s]/g, " ")
        .replace(/\s+/g, " ")
        .trim();
      if (!(wakeText === "hey aura" || wakeText.startsWith("hey aura "))) {
        return;
      }

      const detectedLang = payload?.lang === "ar" ? "ar" : "en";
      setAuraStatus("armed");
      setOrbState("listening");
      setIsThinking(false);
      setWakePulse(true);
      playWakePing();
      if (wakePulseTimerRef.current) {
        clearTimeout(wakePulseTimerRef.current);
      }
      wakePulseTimerRef.current = setTimeout(() => setWakePulse(false), 1400);

      // Capture the command with Google Web Speech (en-US / ar-EG), not Vosk.
      if (orbState !== "processing" && orbState !== "speaking" && !isRecording) {
        void startRecording({ fromWake: true });
      }
    });

    const offPartial = window.electronAPI.onAuraPartialText((partial) => {
      if (typeof partial === "string" && partial.trim()) {
        setUserMessage(partial.trim());
      }
    });

    const offStatus = window.electronAPI.onAuraStatus((status) => {
      const state = typeof status === "string" ? status : status?.state;
      if (status?.lang) {
        const lang = status.lang === "ar" ? "ar" : "en";
        setPreferredLanguage(lang);
        preferredLanguageRef.current = lang;
      }
      if (state) {
        setAuraStatus(state);
      }
      if (state === "error" || state === "stopped") {
        setListening(false);
      }
      if (state === "idle" || state === "ready" || state === "started" || state === "listening" || state === "armed") {
        setListening(true);
        if (orbState !== "processing" && orbState !== "speaking") {
          setOrbState("idle");
        }
      } else if (state === "listening") {
        setListening(true);
        setOrbState("listening");
      }
    });

    return () => {
      try { offWake?.(); } catch { /* no-op */ }
      try { offPartial?.(); } catch { /* no-op */ }
      try { offStatus?.(); } catch { /* no-op */ }
    };
  }, [authState, useElectronWakeWord, orbState, playWakePing, isRecording]);

  useEffect(() => {
    if (!useElectronWakeWord || authState !== "app") return;
    // Vosk wake-word sidecar disabled: no init call.
  }, [authState, useElectronWakeWord]);

  useEffect(() => {
    return () => {
      wakeStoppedRef.current = true;
      if (wakePulseTimerRef.current) {
        clearTimeout(wakePulseTimerRef.current);
      }
    };
  }, []);

  /* ---------- DEVICE DETECTION & RESPONSIVE LAYOUT ---------- */
  useEffect(() => {
    const handleResize = () => {
      const width = window.innerWidth;
      if (width < 768) {
        setScreenSize("mobile");
        setIsSidebarCollapsed(true);
      } else {
        setScreenSize("desktop");
      }
    };

    handleResize();
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, []);

  /* ---------- LOAD USERNAME FROM LOCALSTORAGE ---------- */
  // useEffect(() => {
  //   const savedName = localStorage.getItem("userName");
  //   if (savedName) {
  //     setUserName(savedName);
  //   }
  // }, []);

  // Add this after your other useEffects (around line 120-150, before the WebSocket useEffect)
  useEffect(() => {
    if (authState === "app") {
      const storedName = localStorage.getItem("userName");
      if (storedName && storedName !== userName) {
        console.log("[App] Syncing userName after auth change:", storedName);
        setUserName(storedName);
      }
    }
  }, [authState]);

  /* ---------- LOAD CHAT LIST ---------- */
  useEffect(() => {
    if (authState !== "app") return; // don't load before login
    const loadChats = async () => {
      try {
        const response = await fetch(`http://localhost:8000/chats/${userId}`);
        if (response.ok) {
          const data = await response.json();
          setChats(data.chats || []);
          console.log("[Chats] Loaded", data.chats?.length || 0, "chats");
        }
      } catch (error) {
        console.warn("[Chats] Failed to load chats:", error);
      }
    };
    
    loadChats();
  }, [userId, authState]);

    /* ---------- CONNECT TO THINKING STREAM ---------- */
    /* ---------- CONNECT TO WEBSOCKET (primary) + SSE FALLBACK ---------- */
  const connectWebSocket = useCallback(() => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) return;

    const wsUrl = `ws://localhost:8000/ws/${sessionId}?user_id=${encodeURIComponent(userId)}&device_id=${encodeURIComponent(deviceId)}&platform=desktop`;
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      console.log('[WS] Connected');
      setWsConnected(true);
      if (wsReconnectTimer.current) {
        clearTimeout(wsReconnectTimer.current);
        wsReconnectTimer.current = null;
      }
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        console.log('[WS] Message:', msg.type);

        switch (msg.type) {
          case 'thinking_step':
          case 'thinking': // server alias
          {
            const stepKey = (msg.step_key || "").toString();
            const coordinatorSteps = new Set([
              "preparing_for_coordinator",
              "preparing_tasks",
              "queued_request",
              "creating_execution_plan",
              "executing_task",
              "finalizing",
            ]);
            if (!coordinatorSteps.has(stepKey)) {
              break;
            }
            const localizedStep = translateThinkingStep(msg.step);
            setThinkingSteps(prev => {
              if (prev[prev.length - 1] === localizedStep) return prev;
              if (prev.includes(localizedStep) && stepKey !== "executing_task") {
                return prev;
              }
              return [...prev, localizedStep];
            });

            // Enter widget as soon as Language hands off to Coordinator.
            if (stepKey === 'preparing_for_coordinator') {
              setCoordinatorActive(true);
              setOrbState("processing");
              if (!autoWidgetTriggeredRef.current) {
                window.electronAPI?.enterWidgetMode?.();
                setExecutionMode("widget");
                autoWidgetTriggeredRef.current = true;
              }
            }

            if (vocalizeStepRef.current) vocalizeStepRef.current(localizedStep);
            setIsThinking(true);
            break;
          }

          case 'task_progress': {
            if (msg.stage && msg.stage !== 'coordinator') {
              break;
            }

            if (msg.queue_snapshot) {
              setQueueSnapshot(msg.queue_snapshot);
            }

            // Handle task result with success/failure message
            if (msg.task_result) {
              const taskResult = msg.task_result;
              const message = taskResult.status === 'success' 
                ? taskResult.success_message 
                : taskResult.failure_message;

              registerWorkspaceArtifacts(
                taskResult?.content || taskResult?.details || taskResult?.metadata || "",
                taskResult?.task_id || msg.task_id || "Task"
              );
              
              if (message) {
                // Add to thinking steps and vocalize
                setThinkingSteps(prev => [...prev, message]);
                if (vocalizeStepRef.current) {
                  vocalizeStepRef.current(message);
                }
              }
            }

            const phase = (msg.phase || '').toString().toLowerCase();
            const terminalPhases = new Set([
              'execution_finished',
              'execution_stopped',
              'finished',
              'stopped',
              'done',
            ]);
            const active =
              typeof msg.active === 'boolean'
                ? msg.active
                : !terminalPhases.has(phase);
            setCoordinatorActive(active);

            if (active) {
              setOrbState("processing");
              setIsThinking(true);
              if (!autoWidgetTriggeredRef.current) {
                window.electronAPI?.enterWidgetMode?.();
                setExecutionMode("widget");
                autoWidgetTriggeredRef.current = true;
              }
            } else {
              setIsThinking(false);
              setThinkingSteps([]);
              setQueueSnapshot({ active: [], pending: [], deferred: [] });
              setOrbState((prev) => (prev === "speaking" ? prev : "idle"));
              if (autoWidgetTriggeredRef.current) {
                window.electronAPI?.exitWidgetMode?.();
                setExecutionMode("normal");
                autoWidgetTriggeredRef.current = false;
              }
            }
            break;
          }

          case 'thinking_clear':
            setThinkingSteps([]);
            setIsThinking(false);
            stopThinkingSpeech();
            break;

          case 'clarification':
          case 'clarification_needed': // server alias
          case 'confirmation_needed':
            setThinkingSteps([]);
            setIsThinking(false);
            setCoordinatorActive(false);
            setClarificationResponseToId(msg.response_id);
            const promptText = msg.question || msg.full_content || msg.draft_content || "";
            setAssistantMessage(promptText);
            if (msg.user_language) {
              setUserLanguage(msg.user_language);
              localStorage.setItem("userLanguage", msg.user_language);
            }
            if (msg.question) {
              rememberUserLanguageFromText(msg.question);
            }

            if (msg.type === 'confirmation_needed') {
              const { pages, fullContent } = buildDraftPages(msg);
              const shouldOfferReadFlow = msg.offer_read_aloud === true || pages.length > 0 || !!fullContent;

              if (shouldOfferReadFlow) {
                setStructuredResponse({
                  full_content: fullContent,
                  content_pages: pages,
                  offer_read_aloud: true,
                });
                setOfferReadAloud(true);
                setDraftFlow({
                  active: true,
                  awaitingListenChoice: true,
                  awaitingPageApproval: false,
                  awaitingFinalApproval: false,
                  pageIndex: 0,
                  pages,
                  fullContent,
                  confirmationId: msg.response_id || null,
                });
              }
            }

            if (msg.type === 'confirmation_needed') {
              setOrbState("speaking");
              screenReader.speak((promptText || ""), {
                onComplete: () => setOrbState("idle"),
              });
            } else {
              speakAssistantResponse(msg.question, msg.user_language || userLanguage);
            }

            // If the server provided a local authorize URL for missing API credentials,
            // open it externally so the user is taken directly to the API allow page.
            try {
              const oauthUrl = msg?.metadata?.oauth_authorize_url || msg?.metadata?.api_allow_url;
              if (oauthUrl && window?.electronAPI?.openExternal) {
                // small delay to let the UI update before opening external browser
                setTimeout(() => {
                  window.electronAPI.openExternal(oauthUrl).catch((e) => console.warn('[openExternal] failed', e));
                }, 200);
              }
            } catch (err) {
              console.warn('[App] Failed to trigger oauth external open:', err);
            }
            break;

          case 'processing':
            // Language-agent processing acknowledgements should not trigger coordinator visuals.
            setOrbState("idle");
            setIsThinking(false);
            if (msg.text) setAssistantMessage(msg.text);
            break;

          case 'completion':
          case 'structured_response':
          case 'response_complete': // server alias
          {
            setThinkingSteps([]);
            setIsThinking(false);
            setCoordinatorActive(false);
            setClarificationResponseToId(null);
            clearDraftDecisionTimer();
            setDraftFlow({
              active: false,
              awaitingListenChoice: false,
              awaitingPageApproval: false,
              awaitingFinalApproval: false,
              pageIndex: 0,
              pages: [],
              fullContent: "",
              confirmationId: null,
            });
            
            const responseText = msg.spoken_text || msg.response || msg.text || t("Task completed", "تم تنفيذ المهمة بنجاح");
            const cleanResponseText = extractReadableText(responseText) || t("Task completed", "تم تنفيذ المهمة بنجاح");
            setAssistantMessage(cleanResponseText);
            if (msg.user_language) {
              setUserLanguage(msg.user_language);
              localStorage.setItem("userLanguage", msg.user_language);
            }
            rememberUserLanguageFromText(cleanResponseText);

            // Handle structured response
            if (msg.structured_response || msg.type === 'structured_response') {
              const sr = msg.structured_response || msg;
              const cleanFullContent = extractReadableText(sr.full_content || sr.content || sr.result || "");
              setStructuredResponse({ ...sr, full_content: cleanFullContent });
              setOfferReadAloud(sr.offer_read_aloud === true && !!cleanFullContent);
              registerWorkspaceArtifacts(sr, "Structured result");
            }

            registerWorkspaceArtifacts(cleanResponseText, "Completion");

            speakAssistantResponse(cleanResponseText, msg.user_language || userLanguage);
            break;
          }

          case 'interrupt_ack':
            console.log('[WS] Interrupt acknowledged:', msg.command);
            if (msg.command === 'stop') {
              screenReader.stop();
              clearDraftDecisionTimer();
              setOrbState("idle");
              setIsThinking(false);
              setCoordinatorActive(false);
              setThinkingSteps([]);
              setDraftFlow({
                active: false,
                awaitingListenChoice: false,
                awaitingPageApproval: false,
                awaitingFinalApproval: false,
                pageIndex: 0,
                pages: [],
                fullContent: "",
                confirmationId: null,
              });
              setAssistantMessage(t("Stopped. Task cancelled.", "تم الإيقاف. تم إلغاء المهمة."));
              // Exit widget if auto-triggered
              if (autoWidgetTriggeredRef.current) {
                window.electronAPI?.exitWidgetMode?.();
                autoWidgetTriggeredRef.current = false;
              }
              setExecutionMode("normal");
            } else if (msg.command === 'pause') {
              setAssistantMessage(t("Paused. Say 'AURA resume' to continue.", "تم الإيقاف المؤقت. قل 'أورا استمر' للمتابعة."));
            } else if (msg.command === 'resume') {
              setAssistantMessage(t("Resuming...", "جاري المتابعة..."));
            }
            break;

          case 'context_snapshot':
            if (msg.snapshot) {
              setContextSnapshot(msg.snapshot);
              console.log('[WS] Context snapshot saved for undo');
            }
            break;

          case 'proactive_prompt':
            setThinkingSteps([]);
            setIsThinking(false);
            setClarificationResponseToId(null);
            setAssistantMessage(extractReadableText(msg.spoken_text || msg.text));
            speakAssistantResponse(msg.spoken_text || msg.text, userLanguage);
            break;

          case 'error':
            setThinkingSteps([]);
            setIsThinking(false);
            setCoordinatorActive(false);
            setAssistantMessage(msg.detail || t("An error occurred", "حدث خطأ"));
            setOrbState("idle");
            break;

          default:
            console.log('[WS] Unknown message type:', msg.type);
        }
      } catch (err) {
        console.warn('[WS] Failed to parse message:', err);
      }
    };

    ws.onclose = () => {
      console.log('[WS] Disconnected');
      setWsConnected(false);
      setCoordinatorActive(false);
      setQueueSnapshot({ active: [], pending: [], deferred: [] });
      // Auto-reconnect after 3s
      wsReconnectTimer.current = setTimeout(connectWebSocket, 3000);
    };

    ws.onerror = (err) => {
      console.warn('[WS] Error:', err);
      ws.close();
    };
  }, [sessionId, userId, deviceId, translateThinkingStep, stopThinkingSpeech, rememberUserLanguageFromText, speakAssistantResponse, t, extractReadableText, userLanguage, buildDraftPages, clearDraftDecisionTimer, registerWorkspaceArtifacts]);

  useEffect(() => {
    connectWebSocket();
    return () => {
      if (wsRef.current) wsRef.current.close();
      if (wsReconnectTimer.current) clearTimeout(wsReconnectTimer.current);
    };
  }, [connectWebSocket]);

  // SSE Fallback - only used when WebSocket is not connected
  useEffect(() => {
    if (wsConnected) return; // Skip SSE when WS is active

    const eventSource = new EventSource(`http://localhost:8000/thinking-stream/${sessionId}`);

    // Robust SSE handler: normalize payloads to plain strings
    eventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        const stepKey = (data.step_key || data?.step?.step_key || "").toString();
        const coordinatorSteps = new Set([
          "preparing_for_coordinator",
          "preparing_tasks",
          "queued_request",
          "creating_execution_plan",
          "executing_task",
          "finalizing",
        ]);
        // Handle explicit clear events from server
        if (data.action === 'thinking_clear') {
          setThinkingSteps([]);
          setIsThinking(false);
          stopThinkingSpeech();
          return;
        }

        // Server sends { step: { action, step, session_id } }
        if (data.step) {
          if (!coordinatorSteps.has(stepKey)) {
            return;
          }
          const localizedStep = translateThinkingStep(data.step);
          setThinkingSteps(prev => {
            if (prev[prev.length - 1] === localizedStep) return prev;
            return [...prev, localizedStep];
          });
          setIsThinking(true);
          if (stepKey === 'preparing_for_coordinator') {
            setCoordinatorActive(true);
            setOrbState("processing");
            if (!autoWidgetTriggeredRef.current) {
              window.electronAPI?.enterWidgetMode?.();
              setExecutionMode("widget");
              autoWidgetTriggeredRef.current = true;
            }
          }
          if (vocalizeStepRef.current) vocalizeStepRef.current(localizedStep);
        } else if (Array.isArray(data.steps)) {
          const localizedSteps = data.steps.map(translateThinkingStep);
          setThinkingSteps((prev) => {
            const merged = [...prev];
            for (const s of localizedSteps) {
              if (!merged.includes(s)) merged.push(s);
            }
            return merged;
          });
          setIsThinking(localizedSteps.length > 0);
          // Speak only the last step from batch
          if (localizedSteps.length > 0 && vocalizeStepRef.current) vocalizeStepRef.current(localizedSteps[localizedSteps.length - 1]);
        }
      } catch (err) {
        // Fallback: plain text from server
        console.warn("[UI] Non-JSON SSE payload:", event.data);
        if (event.data && typeof event.data === 'string' && event.data.trim().length > 0 && coordinatorActive) {
          const localizedStep = translateThinkingStep(event.data);
          setThinkingSteps(prev => {
            if (prev[prev.length - 1] === localizedStep) return prev;
            return [...prev, localizedStep];
          });
          setIsThinking(true);
          if (vocalizeStepRef.current) vocalizeStepRef.current(localizedStep);
        }
      }
    };

    eventSource.onerror = (err) => {
      console.warn('[SSE] Thinking stream disconnected or errored:', err);
      setSseConnected(false);
      eventSource.close();
    };

    return () => {
      setSseConnected(false);
      eventSource.close();
    };
  }, [sessionId, wsConnected, coordinatorActive, stopThinkingSpeech, translateThinkingStep]);

  /* ---------- VOCALIZE THINKING STEP (local TTS) ---------- */
  const vocalizeStep = useCallback((text) => {
    if (!vocalizeSteps || !text) return;
    if (thinkingSpeechQueueRef.current[thinkingSpeechQueueRef.current.length - 1] !== text) {
      thinkingSpeechQueueRef.current.push(text);
    }

    if (thinkingSpeechRunningRef.current) return;

    thinkingSpeechRunningRef.current = true;
    (async () => {
      try {
        while (thinkingSpeechQueueRef.current.length > 0) {
          const nextStep = thinkingSpeechQueueRef.current.shift();
          const lang = isArabicText(nextStep) ? "ar" : (userLanguage || "en");
          await screenReader.speak(nextStep, {
            onStart: () => {
              const voices = window.speechSynthesis.getVoices();
              const matchVoice = voices.find(v => v.lang.startsWith(lang)) || voices[0];
              if (matchVoice) screenReader.setVoice(matchVoice);
            },
          });
          lastSpokenStepRef.current += 1;
        }
      } catch (e) {
        console.warn("[TTS-Step] Failed:", e);
      } finally {
        thinkingSpeechRunningRef.current = false;
      }
    })();
  }, [isArabicText, userLanguage, vocalizeSteps]);

  // Keep ref in sync so WS/SSE closures always use the latest
  useEffect(() => { vocalizeStepRef.current = vocalizeStep; }, [vocalizeStep]);

  /* ---------- HANDLE THINKING UPDATES ---------- */
  const handleThinkingUpdate = (step) => {
    console.log("[UI] Updating thinking step:", step);
    setThinkingSteps(prev => {
      // Avoid duplicates
      if (prev.includes(step)) return prev;
      return [...prev, step];
    });
    vocalizeStep(step);
    // Ensure thinking indicator is visible
    setIsThinking(true);
  };

  /* ---------- UI ACTIONS ---------- */
  const handleCancel = () => {
    console.log("[UI] Cancel pressed -> switching to chat mode");
    setOrbState("idle");
    setUserMessage("");
    setChatMode(true);
  };

  const handleSwitchChat = async (chatSessionId, chatTitle) => {
    console.log("[UI] Switching to chat:", chatSessionId);
    
    // Update session ID and title
    setSessionId(chatSessionId);
    setChatTitle(chatTitle);
    localStorage.setItem("currentSessionId", chatSessionId);
    
    // Clear UI state
    setUserMessage("");
    setAssistantMessage("");
    setThinkingSteps([]);
    setIsThinking(false);
    setChatMode(false);
    
    console.log("[Session] Switched to:", chatSessionId);
  };

  /* ---------- VIEW CHAT HISTORY ---------- */
    const handleViewChat = async (chatSessionId, title) => {
      setLoadingHistory(true);
      try {
        const res = await fetch(
          `http://localhost:8000/chat-messages/${chatSessionId}?user_id=${userId}`
        );
        if (!res.ok) throw new Error("Failed to fetch messages");
        const data = await res.json();
        setViewingChat({
          sessionId: chatSessionId,
          title: title,
          messages: data.messages || [],
        });
      } catch (err) {
        console.error("[ChatHistory] Failed to load:", err);
      } finally {
        setLoadingHistory(false);
      }
    };

    // const handleNewChat = async () => {

  // const handleNewChat = () => {
  //   console.log("[UI] New chat started");
  //   setUserMessage("");
  //   setAssistantMessage("");
  //   setThinkingSteps([]);
  //   setIsThinking(false);
  //   setChatMode(false);
  // };

  const handleNewChat = async () => {
    console.log("[UI] New chat started");
    
    // ✅ Generate NEW session ID (but keep same user ID)
    const newSessionId = `session_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
    localStorage.setItem("currentSessionId", newSessionId);
    console.log("[Session] New session created:", newSessionId);
    
    // Clear UI state
    setUserMessage("");
    setAssistantMessage("");
    setThinkingSteps([]);
    setIsThinking(false);
    setChatMode(false);
    setChatTitle("New Chat");
    
    // ✅ Notify backend to initialize new session
    try {
        const response = await fetch("http://localhost:8000/new-chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                session_id: newSessionId,
                user_id: userId,
            }),
        });
        
        if (response.ok) {
            console.log("✅ Backend session initialized");
        } else {
            console.error("⚠️ Backend response not OK");
        }
    } catch (error) {
        console.error("❌ Failed to notify backend:", error);
    }
    
    // Reload chat list so the new chat appears in the sidebar
    try {
      const chatsResp = await fetch(`http://localhost:8000/chats/${userId}`);
      if (chatsResp.ok) {
        const chatsData = await chatsResp.json();
        setChats(chatsData.chats || []);
        console.log("[Chats] Reloaded chat list after creating new chat");
      }
    } catch (err) {
      console.warn("[Chats] Failed to reload chats:", err);
    }

    // ✅ UPDATE SESSION ID STATE (no page reload!)
    setSessionId(newSessionId);
    console.log("[Session] Session state updated to:", newSessionId);
};

  /* ---------- THINKING STEPS SIMULATION ---------- */
  const startThinkingSequence = async () => {
    // If server is sending real-time thinking updates, do not simulate locally
    if (sseConnected) {
      console.info('[Thinking] Server-side thinking active; skipping local simulation');
      return;
    }

    setThinkingSteps([]);
    setIsThinking(true);
    const steps = [
      t("Searching...", "جاري البحث..."),
      t("Analyzing...", "جاري التحليل..."),
      t("Processing...", "جاري المعالجة..."),
      t("Responding...", "جاري تجهيز الرد...")
    ];
    
    for (let i = 0; i < steps.length; i++) {
      setThinkingSteps(prev => [...prev, steps[i]]);
      vocalizeStep(steps[i]);
      await new Promise(resolve => setTimeout(resolve, 800));
    }
    
    setIsThinking(false);
  };

  const cleanupRecorderResources = useCallback(() => {
    recordingActiveRef.current = false;

    if (processorNodeRef.current) {
      try {
        processorNodeRef.current.disconnect();
      } catch {
        // no-op
      }
      processorNodeRef.current.onaudioprocess = null;
      processorNodeRef.current = null;
    }

    if (sourceNodeRef.current) {
      try {
        sourceNodeRef.current.disconnect();
      } catch {
        // no-op
      }
      sourceNodeRef.current = null;
    }

    if (processorSinkRef.current) {
      try {
        processorSinkRef.current.disconnect();
      } catch {
        // no-op
      }
      processorSinkRef.current = null;
    }

    if (audioContextRef.current) {
      try {
        audioContextRef.current.close();
      } catch {
        // no-op
      }
      audioContextRef.current = null;
    }

    if (mediaStreamRef.current) {
      try {
        mediaStreamRef.current.getTracks().forEach((track) => track.stop());
      } catch {
        // no-op
      }
      mediaStreamRef.current = null;
    }
  }, []);

  const mergePcmChunks = useCallback((chunks) => {
    const totalLength = chunks.reduce((acc, chunk) => acc + chunk.length, 0);
    const merged = new Float32Array(totalLength);
    let offset = 0;
    for (const chunk of chunks) {
      merged.set(chunk, offset);
      offset += chunk.length;
    }
    return merged;
  }, []);

  const encodeWavFromFloat32 = useCallback((samples, sampleRate) => {
    const buffer = new ArrayBuffer(44 + samples.length * 2);
    const view = new DataView(buffer);

    const writeString = (offset, text) => {
      for (let i = 0; i < text.length; i += 1) {
        view.setUint8(offset + i, text.charCodeAt(i));
      }
    };

    writeString(0, "RIFF");
    view.setUint32(4, 36 + samples.length * 2, true);
    writeString(8, "WAVE");
    writeString(12, "fmt ");
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, 1, true);
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, sampleRate * 2, true);
    view.setUint16(32, 2, true);
    view.setUint16(34, 16, true);
    writeString(36, "data");
    view.setUint32(40, samples.length * 2, true);

    let offset = 44;
    for (let i = 0; i < samples.length; i += 1) {
      const clamped = Math.max(-1, Math.min(1, samples[i]));
      const int16 = clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;
      view.setInt16(offset, int16, true);
      offset += 2;
    }

    return new Blob([buffer], { type: "audio/wav" });
  }, []);

  const blobToBase64 = useCallback(async (blob) => {
    const bytes = new Uint8Array(await blob.arrayBuffer());
    let binary = "";
    const chunkSize = 0x8000;
    for (let i = 0; i < bytes.length; i += chunkSize) {
      binary += String.fromCharCode(...bytes.subarray(i, i + chunkSize));
    }
    return btoa(binary);
  }, []);

  const isServerUnreachableError = useCallback((error) => {
    const message = String(error?.message || error || "").toLowerCase();
    return (
      message.includes("failed to fetch") ||
      message.includes("networkerror") ||
      message.includes("network request failed") ||
      message.includes("err_connection_refused") ||
      message.includes("load failed") ||
      message.includes("timeout") ||
      message.includes("aborted")
    );
  }, []);

  /* ---------- AUDIO RECORDING ---------- */
  const startRecording = async (options = {}) => {
    if (!navigator.mediaDevices?.getUserMedia || !window.AudioContext) {
      setAssistantMessage(t("Microphone recording is unavailable", "تسجيل الميكروفون غير متاح"));
      return;
    }
    if (isRecording || recordingActiveRef.current) return;

    manualCaptureCancelledRef.current = false;
    pcmChunksRef.current = [];
    speechDetectedRef.current = false;
    userSpokeRef.current = false;
    lastSpeechAtRef.current = 0;
    recordingStartedAtRef.current = Date.now();
    recordingActiveRef.current = true;
    setIsRecording(true);
    setOrbState("listening");
    setUserMessage(t("Listening...", "أستمع الآن..."));

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      mediaStreamRef.current = stream;

      const audioContext = new window.AudioContext({ sampleRate: 16000 });
      audioContextRef.current = audioContext;
      sampleRateRef.current = audioContext.sampleRate;

      const source = audioContext.createMediaStreamSource(stream);
      sourceNodeRef.current = source;

      const processor = audioContext.createScriptProcessor(2048, 1, 1);
      processorNodeRef.current = processor;

      const sink = audioContext.createGain();
      sink.gain.value = 0;
      processorSinkRef.current = sink;

      source.connect(processor);
      processor.connect(sink);
      sink.connect(audioContext.destination);

      processor.onaudioprocess = (event) => {
        if (!recordingActiveRef.current) return;

        const input = event.inputBuffer.getChannelData(0);
        const copied = new Float32Array(input.length);
        copied.set(input);
        pcmChunksRef.current.push(copied);

        let energy = 0;
        for (let i = 0; i < copied.length; i += 1) {
          energy += copied[i] * copied[i];
        }
        const rms = Math.sqrt(energy / copied.length);

        const now = Date.now();
        const isSpeechFrame = rms > 0.013;
        if (isSpeechFrame) {
          speechDetectedRef.current = true;
          userSpokeRef.current = true;
          lastSpeechAtRef.current = now;
        }

        const recordingElapsed = now - recordingStartedAtRef.current;
        if (!speechDetectedRef.current && recordingElapsed >= 12000) {
          stopRecording();
          return;
        }

        if (speechDetectedRef.current && now - lastSpeechAtRef.current >= 5000) {
          stopRecording();
          return;
        }

        if (recordingElapsed >= 60000) {
          stopRecording();
        }
      };
    } catch (error) {
      console.error("[Audio] Recorder start failed:", error);
      setOrbState("idle");
      setAssistantMessage(t("Transcription failed", "فشل تحويل الصوت إلى نص"));
      setIsRecording(false);
      recordingActiveRef.current = false;
      cleanupRecorderResources();
    }
  };

  const stopRecording = ({ cancel = false } = {}) => {
    if (!recordingActiveRef.current) {
      if (cancel) {
        setIsRecording(false);
        setOrbState("idle");
      }
      return;
    }

    manualCaptureCancelledRef.current = cancel;
    recordingActiveRef.current = false;

    const pcmSnapshot = pcmChunksRef.current.slice();
    const hadSpeech = speechDetectedRef.current;
    const sampleRate = sampleRateRef.current;

    cleanupRecorderResources();

    if (cancel) {
      setIsRecording(false);
      setOrbState("idle");
      pcmChunksRef.current = [];
      speechDetectedRef.current = false;
      userSpokeRef.current = false;
      return;
    }

    const finalizeRecording = async () => {
      try {
        if (manualCaptureCancelledRef.current) return;

        if (!hadSpeech || pcmSnapshot.length === 0) {
          setOrbState("idle");
          setAssistantMessage(t("Couldn't catch that. Try again.", "مش سامعك كويس، جرّب تاني."));
          return;
        }

        const mergedPcm = mergePcmChunks(pcmSnapshot);
        if (!mergedPcm.length) {
          setOrbState("idle");
          setAssistantMessage(t("Couldn't catch that. Try again.", "مش سامعك كويس، جرّب تاني."));
          return;
        }

        setOrbState("processing");
        const wavBlob = encodeWavFromFloat32(mergedPcm, sampleRate);
        const audioData = await blobToBase64(wavBlob);

        const sttData = await requestTranscription(
          {
            audio_data: audioData,
            audio_mime_type: "audio/wav",
            session_id: sessionId,
          },
          { timeoutMs: 20000 }
        );

        const transcriptText = String(sttData?.transcript || "").trim();
        if (!transcriptText || transcriptText.toLowerCase().includes("couldn't catch")) {
          setOrbState("idle");
          setAssistantMessage(t("Couldn't catch that. Try again.", "مش سامعك كويس، جرّب تاني."));
          return;
        }

        setUserMessage(transcriptText);
        rememberUserLanguageFromText(transcriptText);
        await processText(transcriptText);
      } catch (error) {
        console.error("[Audio] Server STT transcription failed:", error?.message || error);
        setOrbState("idle");
        if (isServerUnreachableError(error)) {
          setAssistantMessage(t("Server unreachable. Please try again.", "الخادم غير متاح حالياً. حاول مرة أخرى."));
        } else {
          setAssistantMessage(t(`Transcription failed: ${error?.message || "unknown error"}`, "فشل تحويل الصوت إلى نص"));
        }
      } finally {
        setIsRecording(false);
        pcmChunksRef.current = [];
        speechDetectedRef.current = false;
        userSpokeRef.current = false;
      }
    };

    void finalizeRecording();
  };

  const handleMicClick = () => {
    console.log("[UI] Mic clicked. State:", orbState);
    // Allow mic during processing/speaking for interrupt commands
    isRecording ? stopRecording() : startRecording();
  };

  /* ---------- DIRECT TEXT (SKIP STT) ---------- */
  const handleTextSubmit = async (text) => {
    try {
      console.log("[UI] Text submitted:", text);

      setOrbState("processing");
      setUserMessage(text);
      setThinkingSteps([]);
      setIsThinking(false);

      // Skip STT completely → go directly to agent
      await processText(text);

    } catch (error) {
      console.error("[UI] Text submit error:", error);
      setOrbState("idle");
      setAssistantMessage(t("Failed to send message", "فشل إرسال الرسالة"));
    }
  };

  /* ---------- SETTINGS & STOP DETECTION ---------- */
  const handleSettingsClick = () => {
    setShowSettings(!showSettings);
  };


  // In App.jsx - Update handleSettingsSave function
  const handleSettingsSave = (profileData) => {
    console.log("[Settings] Saving profile:", profileData);
    console.log("[Settings] Current userName before update:", userName);
    
    if (profileData.username) {
      localStorage.setItem("userName", profileData.username);
      setUserName(profileData.username);
      console.log("[Settings] Updated userName to:", profileData.username);
    }
    if (profileData.voice) {
      localStorage.setItem("ttsVoice", profileData.voice);
      setTtsVoice(profileData.voice);
    }
    if (profileData.language) {
      const nextLanguage = profileData.language === "ar" ? "ar" : "en";
      localStorage.setItem("preferredLanguage", nextLanguage);
      localStorage.setItem("appLanguage", nextLanguage);
      preferredLanguageRef.current = nextLanguage;
      setPreferredLanguage(nextLanguage);
    }
  };


  /* ---------- ONBOARDING COMPLETE ---------- */
  // const handleOnboardingComplete = ({ username, preferences }) => {
  //     setUserName(username);
  //     if (preferences?.voice) setTtsVoice(preferences.voice);
  //     setAuthState("app");
  // };


  const handleOnboardingComplete = ({ userId: newUserId, username, preferences }) => {
      if (newUserId) {
        setUserId(newUserId);
        localStorage.setItem("userId", newUserId);
      }
      setUserName(username);
      if (preferences?.voice) setTtsVoice(preferences.voice);
      if (preferences?.language) {
        const nextLanguage = preferences.language === "ar" ? "ar" : "en";
        preferredLanguageRef.current = nextLanguage;
        setPreferredLanguage(nextLanguage);
        localStorage.setItem("preferredLanguage", nextLanguage);
        localStorage.setItem("appLanguage", nextLanguage);
      }
      if (preferences?.llm) {
        localStorage.setItem("llmConfig", JSON.stringify(preferences.llm));
      }
      if (typeof preferences?.google_human_verified === "boolean") {
        localStorage.setItem("googleHumanVerified", String(preferences.google_human_verified));
      }
      localStorage.setItem("onboardingComplete", "true");
      setAuthState("app");
      wakeStoppedRef.current = false;
  };
  /* ---------- LOGOUT ---------- */
  // In App.jsx, update the handleLogout function
  const handleLogout = () => {
      // Clear auth state
      localStorage.removeItem("onboardingComplete");
      localStorage.removeItem("currentSessionId");
      localStorage.removeItem("userName");
      localStorage.removeItem("ttsVoice");
      localStorage.removeItem("authMethod");
      
      // IMPORTANT: Clear the userId so a new one is generated for next account
      // But don't remove it completely - we'll let the useState generate a new one
      // Actually, let's remove it so the next signup gets a fresh ID
      localStorage.removeItem("userId");
      
      // Reset userId state to a new value
      const newUserId = `user_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
      setUserId(newUserId);
      
      // Reset other states
      setUserName("User");
      setAuthState("login");
      
      console.log("[Auth] Logged out, new user ID generated for next signup:", newUserId);
  };
  /* ---------- INTERRUPT COMMANDS ---------- */
  const sendInterrupt = useCallback((command) => {
    // ── Req 11: Local TTS for immediate control feedback — zero latency, no network ──
    // System control confirmations ("Stopped", "Paused") use the local Web Speech API
    // so the user gets instant audio feedback even if the backend is slow or offline.
    const LOCAL_FEEDBACK = {
      stop:   userLanguageRef.current === "ar" ? "تم الإيقاف." : "Stopped.",
      pause:  userLanguageRef.current === "ar" ? "تم الإيقاف المؤقت." : "Paused.",
      resume: userLanguageRef.current === "ar" ? "جاري المتابعة." : "Resuming.",
      undo:   userLanguageRef.current === "ar" ? "تم التراجع." : "Undone.",
      retry:  userLanguageRef.current === "ar" ? "جاري إعادة المحاولة." : "Retrying.",
    };
  
    const feedbackText = LOCAL_FEEDBACK[command];
    if (feedbackText) {
      // Local Web Speech API — no HTTP call needed (Req 11)
      screenReader.speak(feedbackText);
    }
  
    // Stop any playing audio / thinking speech immediately
    if (command === "stop") {
      audioRef.current?.pause?.();
      if (audioRef.current) audioRef.current.currentTime = 0;
      stopThinkingSpeech();
      screenReader.stop();
      clearDraftDecisionTimer();
    } else if (command === "pause") {
      screenReader.pause();
    } else if (command === "resume") {
      screenReader.resume();
    }
  
    // Send to backend for actual task control
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(
        JSON.stringify({ type: "interrupt", command, user_id: userId })
      );
    } else {
      // HTTP fallback
      fetch("http://localhost:8000/process", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id:  sessionId,
          user_id:     userId,
          input:       `AURA ${command}`,
          device_type: deviceType,
        }),
      }).catch((err) => console.warn("[Interrupt] HTTP fallback failed:", err));
    }
  }, [deviceId, sessionId, userId, deviceType, stopThinkingSpeech, userLanguage, clearDraftDecisionTimer]);


  /* ---------- READ ALOUD FULL CONTENT ---------- */
  const handleReadAloud = useCallback(() => {
    const readableContent = extractReadableText(structuredResponse?.full_content);
    if (readableContent) {
      setOrbState("speaking");
      screenReader.speak(readableContent, {
        onProgress: (current, total) => {
          console.log(`[TTS] Reading sentence ${current}/${total}`);
        },
        onComplete: () => setOrbState("idle")
      });
    }
  }, [extractReadableText, structuredResponse]);

  /* ---------- TEXT → AGENT ---------- */
  const processText = async (text) => {
    try {
      console.log("[Agent] Processing input:", text);
      const rawText = text || "";
      const normalized = rawText.trim();

      rememberUserLanguageFromText(normalized);
      const currentUserLanguage = userLanguageRef.current || detectLanguageFromText(normalized) || "en";

      const lowerText = normalized.toLowerCase();
      const polarIntent = classifyPolarIntent(normalized, currentUserLanguage);
      const readAloudIntent = isReadAloudIntentSemantic(normalized);
      const interruptCommand = classifyInterruptSemantic(normalized);
      const activeDraftFlow = draftFlowRef.current;

      if (activeDraftFlow.active && activeDraftFlow.awaitingListenChoice) {
        if (polarIntent === "negative") {
          clearDraftDecisionTimer();
          setDraftFlow({
            active: false,
            awaitingListenChoice: false,
            awaitingPageApproval: false,
            awaitingFinalApproval: false,
            pageIndex: 0,
            pages: [],
            fullContent: "",
            confirmationId: null,
          });
          await sendClarificationAnswer("yes", activeDraftFlow.confirmationId);
          const msg = t("Skipping read-aloud and approving the drafted content now.", "هتخطى القراءة وهوافق على المحتوى دلوقتي.");
          setAssistantMessage(msg);
          await speakAssistantResponse(msg, currentUserLanguage);
          return;
        }

        if (polarIntent === "affirmative" || readAloudIntent) {
          setDraftFlow((prev) => ({ ...prev, awaitingListenChoice: false, awaitingPageApproval: true }));
          if (activeDraftFlow.pages.length > 0) {
            promptDraftPage(0);
          } else {
            const fallback = extractReadableText(activeDraftFlow.fullContent || structuredResponse?.full_content || "");
            if (fallback) {
              setAssistantMessage(fallback);
              await speakAssistantResponse(fallback, currentUserLanguage);
            }
            const finalPrompt = t("That is the full draft. Do you approve this content?", "ده كامل المحتوى. هل توافق عليه؟");
            setDraftFlow((prev) => ({ ...prev, awaitingPageApproval: false, awaitingFinalApproval: true }));
            setAssistantMessage(finalPrompt);
            await speakAssistantResponse(finalPrompt, currentUserLanguage);
          }
          return;
        }

        const listenPrompt = t("Please answer with yes to listen, or no to skip reading.", "جاوب بنعم لو تحب أقرأ، أو لا لتخطي القراءة.");
        setAssistantMessage(listenPrompt);
        await speakAssistantResponse(listenPrompt, currentUserLanguage);
        return;
      }

      if (activeDraftFlow.active && activeDraftFlow.awaitingPageApproval) {
        if (polarIntent === "affirmative") {
          clearDraftDecisionTimer();
          await sendClarificationAnswer("yes", activeDraftFlow.confirmationId);
          setDraftFlow({
            active: false,
            awaitingListenChoice: false,
            awaitingPageApproval: false,
            awaitingFinalApproval: false,
            pageIndex: 0,
            pages: [],
            fullContent: "",
            confirmationId: null,
          });
          const approved = t("Great, approved. Executing now.", "ممتاز، تمت الموافقة. هكمل التنفيذ الآن.");
          setAssistantMessage(approved);
          await speakAssistantResponse(approved, currentUserLanguage);
          return;
        }

        if (polarIntent === "negative") {
          clearDraftDecisionTimer();
          const nextPage = activeDraftFlow.pageIndex + 1;
          if (nextPage < activeDraftFlow.pages.length) {
            promptDraftPage(nextPage);
          } else {
            const finalPrompt = t("That is the full draft. Do you approve this content?", "ده كامل المحتوى. هل توافق عليه؟");
            setDraftFlow((prev) => ({ ...prev, awaitingPageApproval: false, awaitingFinalApproval: true }));
            setAssistantMessage(finalPrompt);
            await speakAssistantResponse(finalPrompt, currentUserLanguage);
          }
          return;
        }

        const pagePrompt = t("Say yes to approve now, or no so I keep reading.", "قل نعم للموافقة، أو لا عشان أكمل القراءة.");
        setAssistantMessage(pagePrompt);
        await speakAssistantResponse(pagePrompt, currentUserLanguage);
        return;
      }

      if (activeDraftFlow.active && activeDraftFlow.awaitingFinalApproval) {
        if (polarIntent === "affirmative" || polarIntent === "negative") {
          clearDraftDecisionTimer();
          await sendClarificationAnswer(polarIntent === "affirmative" ? "yes" : "no", activeDraftFlow.confirmationId);
          setDraftFlow({
            active: false,
            awaitingListenChoice: false,
            awaitingPageApproval: false,
            awaitingFinalApproval: false,
            pageIndex: 0,
            pages: [],
            fullContent: "",
            confirmationId: null,
          });
          return;
        }

        const finalPrompt = t("Please answer yes to approve or no to reject.", "يرجى الرد بنعم للموافقة أو لا للرفض.");
        setAssistantMessage(finalPrompt);
        await speakAssistantResponse(finalPrompt, currentUserLanguage);
        return;
      }

      if (offerReadAloud && structuredResponse?.full_content && readAloudIntent) {
        setClarificationResponseToId(null);
        handleReadAloud();
        setAssistantMessage(t("Reading the results now.", "حسنًا، سأقرأ النتائج الآن."));
        return;
      }

      if (polarIntent === "affirmative") {
        if (offerReadAloud && structuredResponse?.full_content) {
          setClarificationResponseToId(null);
          handleReadAloud();
          setAssistantMessage(t("Reading the results now.", "حسنًا، سأقرأ النتائج الآن."));
          return;
        }

        const followUpPrompt = userLanguage === "ar"
          ? "تمام، ماذا تريد أن أفعل بعد ذلك؟"
          : "Sure — what would you like me to do next?";
        setAssistantMessage(followUpPrompt);
        await speakAssistantResponse(followUpPrompt, userLanguage || detectLanguageFromText(followUpPrompt));
        return;
      }

      if (interruptCommand) {
        console.log(`[Agent] Semantic interrupt command detected: ${interruptCommand}`);
        sendInterrupt(interruptCommand);
        if (interruptCommand === "stop") {
          handleStopSequence();
        }
        return;
      }

      // Detect settings request
      if (
        lowerText.includes("settings") ||
        lowerText.includes("open settings") ||
        lowerText.includes("show settings")
      ) {
        console.log("[Agent] Settings request detected");
        setShowSettings(true);
        setAssistantMessage(t("Opening settings for you", "جاري فتح الإعدادات لك"));
        return;
      }
      console.log("[Agent] Clarification mode:", !!clarificationResponseToId);
      setOrbState("processing");
      // coordinatorActive and task_progress events control thinking/widget visuals.
      setIsThinking(false);

      // Send via WebSocket if connected, fallback to HTTP
      if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
        const msgType = clarificationResponseToId ? "clarification_response" : "user_input";
        const payload = {
          type: msgType,
          user_id: userId,
          device_type: deviceType,
          device_id: deviceId,
          user_language: currentUserLanguage,
        };
        if (clarificationResponseToId) {
          payload.answer = text;
          payload.clarification_id = clarificationResponseToId;
        } else {
          payload.text = text;
        }
        wsRef.current.send(JSON.stringify(payload));
        console.log(`[WS] Sent ${msgType}:`, text);
      } else {
        // HTTP fallback
        console.log("[Agent] Using HTTP fallback (WS not connected)");
        setThinkingSteps([]);
        setIsThinking(false);

        const res = await fetch("http://localhost:8000/process", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            session_id: sessionId,
            user_id: userId,
            input: text,
            is_clarification: !!clarificationResponseToId,
            clarification_id: clarificationResponseToId || null,
            device_type: deviceType,
            device_id: deviceId,
            user_language: currentUserLanguage,
          }),
        });

        console.log("[Agent] Status:", res.status);
        const data = await res.json();

        if (!res.ok) throw new Error(data.detail || "Backend error");

        // Update chat title if provided (first message generates title)
        if (data.chat_title && data.chat_title !== "Chat") {
          console.log("[Chat] Received chat title:", data.chat_title);
          setChatTitle(data.chat_title);

          try {
            await fetch("http://localhost:8000/update-chat-title", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                session_id: sessionId,
                user_id: userId,
                title: data.chat_title,
              }),
            });

            const chatsResponse = await fetch(`http://localhost:8000/chats/${userId}`);
            if (chatsResponse.ok) {
              const chatsData = await chatsResponse.json();
              setChats(chatsData.chats || []);
              console.log("[Chats] Reloaded chat list");
            }
          } catch (error) {
            console.warn("[Chat] Failed to update chat title:", error);
          }
        }

        setThinkingSteps([]);
        setIsThinking(false);
        setCoordinatorActive(false);

        if (data.status === "clarification_needed") {
          const questionText = extractReadableText(data.question) || t("Could you clarify?", "هل يمكنك التوضيح؟");
          setClarificationResponseToId(data.response_id);
          setAssistantMessage(questionText);

          if (data.user_language) {
            userLanguageRef.current = data.user_language;
            setUserLanguage(data.user_language);
            localStorage.setItem("userLanguage", data.user_language);
          }

          rememberUserLanguageFromText(questionText);
          await speakAssistantResponse(
            questionText,
            data.user_language || userLanguageRef.current || detectLanguageFromText(questionText)
          );
        } else {
          const responseText = data.spoken_text || data.text || data.result?.response || data.result || t("Task completed", "تم تنفيذ المهمة بنجاح");
          const cleanResponseText = extractReadableText(responseText) || t("Task completed", "تم تنفيذ المهمة بنجاح");
          setClarificationResponseToId(null);
          setAssistantMessage(cleanResponseText);

          if (data.user_language) {
            userLanguageRef.current = data.user_language;
            setUserLanguage(data.user_language);
            localStorage.setItem("userLanguage", data.user_language);
          }

          const sr = data.structured_response || null;
          if (sr) {
            const cleanFullContent = extractReadableText(sr.full_content || sr.content || sr.result || "");
            setStructuredResponse({ ...sr, full_content: cleanFullContent });
            setOfferReadAloud(sr.offer_read_aloud === true && !!cleanFullContent);
          }

          rememberUserLanguageFromText(cleanResponseText);
          await speakAssistantResponse(cleanResponseText, data.user_language || userLanguage || detectLanguageFromText(cleanResponseText));
        }
      }
    } catch (error) {
      console.error("[Agent] Error:", error);
      setOrbState("idle");
      setAssistantMessage(t("Backend error", "خطأ في الخادم"));
      setThinkingSteps([]);
      setIsThinking(false);
      setCoordinatorActive(false);
      setExecutionMode("normal");
    }
  };

  /* ---------- STOP SEQUENCE ---------- */
  const handleStopSequence = () => {
    console.log("[System] Executing stop sequence");
    audioRef.current?.pause?.();
    if (audioRef.current) {
      audioRef.current.currentTime = 0;
    }
    stopThinkingSpeech();
    screenReader.stop(); // Stop client-side TTS
    stopRecording({ cancel: true });
    setOrbState("idle");
    setUserMessage("");
    setAssistantMessage(t("Stop sequence initiated", "تم بدء إيقاف التنفيذ"));
    setIsRecording(false);
    setChatMode(false);
    setShowSettings(false);
    setThinkingSteps([]);
    setIsThinking(false);
    clearDraftDecisionTimer();
    setDraftFlow({
      active: false,
      awaitingListenChoice: false,
      awaitingPageApproval: false,
      awaitingFinalApproval: false,
      pageIndex: 0,
      pages: [],
      fullContent: "",
      confirmationId: null,
    });
    setStructuredResponse(null);
    setOfferReadAloud(false);
    // Exit widget if auto-triggered
    if (autoWidgetTriggeredRef.current) {
      window.electronAPI?.exitWidgetMode?.();
      autoWidgetTriggeredRef.current = false;
    }
    setExecutionMode("normal");
  };

  /* ---------- EXECUTION MODE TOGGLE ---------- */
  const toggleExecutionMode = useCallback(() => {
    setExecutionMode(prev => prev === "normal" ? "transparent" : "normal");
  }, []);

  const enterWidgetMode = useCallback(() => {
    window.electronAPI?.enterWidgetMode?.();
    setExecutionMode("widget");
  }, []);

  const exitWidgetMode = useCallback(() => {
    window.electronAPI?.exitWidgetMode?.();
    setExecutionMode("normal");
  }, []);

  /* ---------- TEXT → SPEECH (Client-side via ScreenReader) ---------- */
  const speakResponse = async (text) => {
    try {
      console.log("[TTS] Speaking via client-side ScreenReader:", text?.substring(0, 50));
      setOrbState("speaking");
      await screenReader.speak(text, {
        onComplete: () => {
          console.log("[TTS] Playback finished");
          setOrbState("idle");
        }
      });
    } catch (error) {
      console.error("[TTS] Error:", error);
      setOrbState("idle");
    }
  };


  /* ---------- AUTO WIDGET / FULLSCREEN TOGGLE ---------- */
  // Track previous orbState to detect transitions
  const prevOrbStateRef = useRef(orbState);
  const autoWidgetTriggeredRef = useRef(false);

  useEffect(() => {
    prevOrbStateRef.current = orbState;
  }, [orbState]);

  // Auto-exit widget when execution finishes (only if we auto-entered)
  useEffect(() => {
    const isCoordinatorDone = !coordinatorActive;
    if (isCoordinatorDone && executionMode === "widget" && autoWidgetTriggeredRef.current) {
      console.log("[Auto-Widget] Execution done → exiting widget mode");
      window.electronAPI?.exitWidgetMode?.();
      setExecutionMode("normal");
      autoWidgetTriggeredRef.current = false;
    }
  }, [coordinatorActive, executionMode]);

  /* ---------- RENDER ---------- */

  const handleDeleteChat = async (chatSessionId) => {
    try {
      const currentUserId = localStorage.getItem("userId") || "test_user";
      const res = await fetch(`http://localhost:8000/chats/${chatSessionId}?user_id=${currentUserId}`, {
        method: "DELETE",
      });
      if (res.ok) {
        setChats(prev => prev.filter(c => (c.session_id || c.id) !== chatSessionId));
        if (sessionId === chatSessionId) {
          handleNewChat();
        }
      } else {
        console.error("Failed to delete chat:", await res.text());
      }
    } catch (e) {
      console.error("Failed to delete chat:", e);
    }
};

  const handleHeaderContentReady = useCallback(({ greeting, headline, currentDate }) => {
    if (authState !== "app" || hasSpokenHeaderWelcomeRef.current) return;

    const safeGreeting = (greeting || "Welcome back")
      .replace(/[\u{1F300}-\u{1FAFF}]/gu, "")
      .trim();
    const safeHeadline = (headline || "How can I help you today?").trim();
    const safeDate = (currentDate || "today").trim();

    hasSpokenHeaderWelcomeRef.current = true;
    setOrbState("speaking");

    screenReader.speak(`${safeGreeting}. Today is ${safeDate}. ${safeHeadline}`, {
      onComplete: () => setOrbState("idle"),
    });
  }, [authState]);

  
/* ---------- RENDER ---------- */
  // const isExecuting = orbState === "processing" || isThinking;
  // const appClassName = [
  //   "app-root",
  //   executionMode === "transparent" && isExecuting ? "transparent-mode" : "",
  //   executionMode === "widget" ? "widget-mode" : "",
  // ].filter(Boolean).join(" ");
  // const liveCaptionText =
  //   (userMessage && (!assistantMessage || orbState === "listening" || isRecording))
  //     ? userMessage
  //     : (assistantMessage || (isThinking
  //         ? t("Thinking...", "بفكر...")
  //         : (listening
  //             ? t("Listening for your voice...", "أستمع لصوتك...")
  //             : t("Tap the mic to speak", "اضغط على الميكروفون للتحدث"))));

  const queueItems = useMemo(() => {
    const activeItems = (queueSnapshot?.active || []).map((task, index) => ({
      id: `active-${task.task_id || index}`,
      title: task.task || task.description || t("Current task", "المهمة الحالية"),
      state: "active",
    }));
    const pendingItems = (queueSnapshot?.pending || []).map((task, index) => ({
      id: `pending-${task.task_id || index}`,
      title: task.task || task.description || t("Queued task", "مهمة بالانتظار"),
      state: "pending",
    }));
    const deferredItems = (queueSnapshot?.deferred || []).map((task, index) => ({
      id: `deferred-${task.task_id || index}`,
      title: task.task || task.description || t("Deferred task", "مهمة مؤجلة"),
      state: "deferred",
    }));

    return [...activeItems, ...pendingItems, ...deferredItems].slice(0, 80);
  }, [queueSnapshot, t]);

  const scheduledQueueItems = useMemo(() => {
    const schedulePattern = /(wait\s+for|scheduled?|in\s+\d+\s+(minute|minutes|hour|hours)|at\s+\d)/i;
    return queueItems.filter((item) => schedulePattern.test(item.title || ""));
  }, [queueItems]);

  const workspacePanels = useMemo(() => {
    const panels = [];

    if (queueItems.length > 0 || coordinatorActive) {
      panels.push({
        id: "panel-queue",
        kind: "queue",
        title: t("Queued Tasks", "المهام في قائمة الانتظار"),
      });
    }

    if (scheduledQueueItems.length > 0) {
      panels.push({
        id: "panel-scheduled",
        kind: "scheduled",
        title: t("Scheduled Tasks", "المهام المجدولة"),
      });
    }

    if (draftFlow.active) {
      panels.push({
        id: "panel-draft",
        kind: "draft",
        title: t("Draft Confirmation", "تأكيد المسودة"),
      });
    }

    if (structuredResponse?.full_content) {
      panels.push({
        id: "panel-result",
        kind: "result",
        title: t("Generated Content", "المحتوى المُنشأ"),
      });
    }

    workspaceArtifacts.forEach((artifact) => {
      panels.push({
        id: `panel-${artifact.id}`,
        kind: artifact.type,
        title: artifact.label,
        subtitle: artifact.sourceLabel,
        artifact,
      });
    });

    return panels;
  }, [queueItems.length, coordinatorActive, scheduledQueueItems.length, draftFlow.active, structuredResponse, workspaceArtifacts, t]);

  useEffect(() => {
    if (workspacePanels.length === 0) {
      setActiveWorkspacePanelId(null);
      return;
    }
    if (!activeWorkspacePanelId || !workspacePanels.some((panel) => panel.id === activeWorkspacePanelId)) {
      setActiveWorkspacePanelId(workspacePanels[0].id);
    }
  }, [workspacePanels, activeWorkspacePanelId]);

  const activeWorkspacePanel = useMemo(
    () => workspacePanels.find((panel) => panel.id === activeWorkspacePanelId) || null,
    [workspacePanels, activeWorkspacePanelId]
  );

  const renderLineWithLinks = useCallback((line) => {
    const matcher = /(https?:\/\/[^\s]+)/g;
    const parts = line.split(matcher);
    return parts.map((part, idx) => {
      if (/^https?:\/\//i.test(part)) {
        return (
          <a key={`${part}-${idx}`} href={part} target="_blank" rel="noreferrer" className="workspace-link">
            {part}
          </a>
        );
      }
      return <span key={`${part}-${idx}`}>{part}</span>;
    });
  }, []);

  // Helper to ensure queue snapshot always has expected structure
  const normalizeQueueSnapshot = useCallback((snapshot) => {
    if (!snapshot) return { active: [], pending: [], deferred: [] };
    return {
      active: (snapshot.active || []).map((t, idx) => ({
        task_id: t.task_id || `active-${idx}`,
        task: t.title || t.task || t.description || t.ai_prompt || t.name || 'Unnamed task',
        description: t.description || t.task || t.title || t.ai_prompt || t.name || 'Unnamed task',
      })),
      pending: (snapshot.pending || []).map((t, idx) => ({
        task_id: t.task_id || `pending-${idx}`,
        task: t.title || t.task || t.description || t.ai_prompt || t.name || 'Unnamed task',
        description: t.description || t.task || t.title || t.ai_prompt || t.name || 'Unnamed task',
      })),
      deferred: (snapshot.deferred || []).map((t, idx) => ({
        task_id: t.task_id || `deferred-${idx}`,
        task: t.title || t.task || t.description || t.ai_prompt || t.name || 'Unnamed task',
        description: t.description || t.task || t.title || t.ai_prompt || t.name || 'Unnamed task',
      })),
    };
  }, []);

  const renderWorkspaceText = useCallback((content) => {
    const normalized = extractReadableText(content);
    if (!normalized) return null;
    return normalized.split("\n").map((line, index) => (
      <p key={`line-${index}`} className="workspace-text-line">
        {renderLineWithLinks(line)}
      </p>
    ));
  }, [extractReadableText, renderLineWithLinks]);

  // Build queue items for popover
  const queuedTasksForPopover = useMemo(() => {
    const normalized = normalizeQueueSnapshot(queueSnapshot);
    const activeItems = (normalized.active || []).map((task, idx) => ({
      id: task.task_id || `active-${idx}`,
      name: task.task || task.description || t("Current task", "المهمة الحالية"),
      info: t("Active", "نشط"),
      status: "active"
    }));
    const pendingItems = (normalized.pending || []).map((task, idx) => ({
      id: task.task_id || `pending-${idx}`,
      name: task.task || task.description || t("Queued", "في الانتظار"),
      info: t("Pending", "معلق"),
      status: "pending"
    }));
    const deferredItems = (normalized.deferred || []).map((task, idx) => ({
      id: task.task_id || `deferred-${idx}`,
      name: task.task || task.description || t("Deferred", "مؤجل"),
      info: t("Deferred", "مؤجل"),
      status: "deferred"
    }));
    
    // ✅ Add session-scoped cross-platform tasks (only if no local queue exists)
    const sessionItems = (!activeItems.length && !pendingItems.length && !deferredItems.length) 
      ? sessionScopedTasks.filter(t => t.status === 'pending')
      : [];
    
    return [...activeItems, ...pendingItems, ...deferredItems, ...sessionItems];
  }, [queueSnapshot, t, normalizeQueueSnapshot, sessionScopedTasks]);

  const scheduledTasksForPopover = useMemo(() => {
    // Extract scheduled tasks from your existing scheduledQueueItems
    const schedulePattern = /(wait\s+for|scheduled?|in\s+\d+\s+(minute|minutes|hour|hours)|at\s+\d)/i;
    const scheduled = (queueSnapshot.active || [])
      .concat(queueSnapshot.pending || [])
      .filter(task => schedulePattern.test(task.task || task.description || ""));
    return scheduled.map((task, idx) => ({
      id: task.task_id || `sched-${idx}`,
      name: task.task || task.description || t("Scheduled task", "مهمة مجدولة"),
      info: t("Scheduled", "مجدول"),
      status: "scheduled"
    }));
  }, [queueSnapshot, t]);

  const handleCompleteTask = useCallback((taskId, type) => {
    if (type === 'queued') {
      const task = queuedTasksForPopover.find(t => t.id === taskId);
      if (task && !completedQueuedTasks.find(ct => ct.id === taskId)) {
        setCompletedQueuedTasks(prev => [{ ...task, completedAt: Date.now() }, ...prev].slice(0, 50));
      }
    } else if (type === 'scheduled') {
      const task = scheduledTasksForPopover.find(t => t.id === taskId);
      if (task && !completedScheduledTasks.find(ct => ct.id === taskId)) {
        setCompletedScheduledTasks(prev => [{ ...task, completedAt: Date.now() }, ...prev].slice(0, 50));
      }
    }
  }, [queuedTasksForPopover, scheduledTasksForPopover, completedQueuedTasks, completedScheduledTasks]);

  // Prepare completed task lists for popover
  const completedQueuedForPopover = useMemo(() => completedQueuedTasks.map(t => ({ ...t, info: t.info || t.status, status: 'completed' })), [completedQueuedTasks]);
  const completedScheduledForPopover = useMemo(() => completedScheduledTasks.map(t => ({ ...t, info: t.info || t.status, status: 'completed' })), [completedScheduledTasks]);

  const isExecuting = orbState === "processing" || isThinking;
  const appClassName = ["app-root", executionMode === "transparent" && isExecuting ? "transparent-mode" : "", executionMode === "widget" ? "widget-mode" : ""].filter(Boolean).join(" ");
  const liveCaptionText = (userMessage && (!assistantMessage || orbState === "listening" || isRecording)) ? userMessage : (assistantMessage || (isThinking ? t("Thinking...", "بفكر...") : (listening ? t("Listening for your voice...", "أستمع لصوتك...") : t("Tap the mic to speak", "اضغط على الميكروفون للتحدث"))));

  const liveCaptionPages = useMemo(() => {
    const content = extractReadableText(liveCaptionText).trim();
    if (!content) return [];

    const segments = content.match(/[^\n.!?。！？]+[\n.!?。！？]*/g) || [content];
    const pages = [];
    let current = "";
    const maxChars = 280;

    for (const segment of segments) {
      const part = segment.trim();
      if (!part) continue;

      const next = current ? `${current} ${part}` : part;
      if (next.length > maxChars && current) {
        pages.push(current);
        current = part;
      } else {
        current = next;
      }
    }

    if (current) {
      pages.push(current);
    }

    return pages.length > 0 ? pages : [content];
  }, [extractReadableText, liveCaptionText]);

  useEffect(() => {
    setLiveCaptionPage(0);
  }, [liveCaptionText]);

  useEffect(() => {
    if (liveCaptionPages.length === 0) return;
    if (liveCaptionPage >= liveCaptionPages.length) {
      setLiveCaptionPage(liveCaptionPages.length - 1);
    }
  }, [liveCaptionPage, liveCaptionPages.length]);

  // Return JSX with new UI
  return (
    <>
      {executionMode !== "widget" && (
        <TitleBar
          transparent={authState !== "app"}
          showExtraControls={authState === "app"}
          isExecuting={isExecuting}
          executionMode={executionMode}
          onToggleExecutionMode={() => setExecutionMode(prev => prev === "normal" ? "widget" : "normal")}
          onEnterWidgetMode={() => { window.electronAPI?.enterWidgetMode?.(); setExecutionMode("widget"); }}
        />
      )}

      {authState === "login" && (
        <LoginPage
          onLogin={({ userId: realId, username, preferences }) => {
            localStorage.setItem("userId", realId);
            localStorage.setItem("userName", username);
            setUserId(realId);
            setUserName(username);
            if (preferences?.voice) setTtsVoice(preferences.voice);
            if (preferences?.language) {
              const nextLanguage = preferences.language === "ar" ? "ar" : "en";
              preferredLanguageRef.current = nextLanguage;
              setPreferredLanguage(nextLanguage);
              localStorage.setItem("preferredLanguage", nextLanguage);
              localStorage.setItem("appLanguage", nextLanguage);
            }
            setAuthState("app");
            wakeStoppedRef.current = false;
          }}
          onSignUp={() => setAuthState("onboard")}
        />
      )}

      {authState === "onboard" && (
        <OnboardingPage 
          userId={userId} 
          onComplete={handleOnboardingComplete} 
          onBack={() => setAuthState("login")}
        />
      )}

      {authState === "app" && (
        <div className={appClassName}>

          {/* ===== Widget mini-player ===== */}
          {executionMode === "widget" && (
            <div className="widget-player">
              <div className="widget-drag-strip" />

              <div className="widget-left">
                <div className={`widget-state-badge orb-${orbState}`}>
                  {orbState === "processing" && <Cpu size={15} />}
                  {orbState === "speaking" && <Waves size={15} />}
                  {orbState === "listening" && <Mic size={15} />}
                  {orbState === "idle" && (
                    <img src="/aura_icon_white.png" alt="" className="widget-state-icon" />
                  )}
                </div>
                <div className="widget-status-text">
                  {isExecuting
                    ? (isThinking
                        ? (thinkingSteps.length > 0 ? thinkingSteps[thinkingSteps.length - 1] : "Thinking...")
                        : assistantMessage
                          ? (assistantMessage.length > 40 ? assistantMessage.slice(0, 40) + "…" : assistantMessage)
                          : "Processing...")
                    : "AURA"}
                </div>
              </div>

              <div className="widget-input-area">
                {!isExecuting ? (
                  <>
                    <input className="widget-text-input" type="text" placeholder="Ask AURA..." value={widgetText} onChange={(e) => setWidgetText(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && widgetText.trim()) { handleTextSubmit(widgetText); setWidgetText(""); } }} />
                    <button className="widget-mic-btn" onClick={handleMicClick} title={isRecording ? "Stop recording" : "Voice input"}><Mic size={16} /></button>
                  </>
                ) : (
                  <div className="widget-exec-controls">
                    <button className="widget-action-btn widget-pause" onClick={() => sendInterrupt("pause")} title="Pause"><Pause size={14} /></button>
                    <button className="widget-action-btn widget-stop" onClick={() => sendInterrupt("stop")} title="Stop"><Square size={14} /></button>
                  </div>
                )}
              </div>
              <div className="widget-window-controls">
                <button className="widget-win-btn" onClick={() => { window.electronAPI?.exitWidgetMode?.(); setExecutionMode("normal"); }} title="Expand"><ArrowUpRight size={14} /></button>
                <button className="widget-win-btn widget-win-close" onClick={() => window.electronAPI?.closeWindow?.()} title="Close"><X size={14} /></button>
              </div>
            </div>
          )}

          <Sidebar
            collapsed={isSidebarCollapsed || executionMode === "widget"}
            onToggle={() => setIsSidebarCollapsed(p => !p)}
            onSettingsClick={() => setShowSettings(true)}
            onNewChat={handleNewChat}
            chats={chats}
            onSwitchChat={handleSwitchChat}
            onViewChat={handleViewChat}
            onDeleteChat={handleDeleteChat}
            currentSessionId={sessionId}
          />
          <main className={`main-area ${isSidebarCollapsed && screenSize === "mobile" ? "mobile-sidebar-open" : ""}`}>
            <div className="main-bg-layer" aria-hidden="true">
              <Aurora />
              <img src="/aura_icon_white.png" alt="AURA Logo" style={{ position: "absolute", width: "400px", height: "400px", top: "50%", left: "50%", transform: "translate(-50%, -50%)", zIndex: 1, opacity: 0.05, pointerEvents: "none", objectFit: "contain" }} />
              <iframe src="/aura-cinematic-bg.html"
                style={{ position: "absolute", width: "100%", height: "100%", border: "none", pointerEvents: "none", zIndex: 0 }}
                title="Cinematic Background"
              />            </div>

            <div className="main-overlay">
              <div className="workspace-toolbar">
                <div className="workspace-toolbar-copy">
                  <span className="workspace-toolbar-label">{t("Task queues", "قوائم المهام")}</span>
                </div>

                <div className="task-buttons-container">
                <TaskQueuesPopover
                  type="queued"
                  title={t("Queued Tasks", "المهام في قائمة الانتظار")}
                  activeTasks={queuedTasksForPopover}
                  completedTasks={completedQueuedForPopover}
                  onCompleteTask={(taskId) => handleCompleteTask(taskId, 'queued')}
                />
                <TaskQueuesPopover
                  type="scheduled"
                  title={t("Scheduled Tasks", "المهام المجدولة")}
                  activeTasks={scheduledTasksForPopover}
                  completedTasks={completedScheduledForPopover}
                  onCompleteTask={(taskId) => handleCompleteTask(taskId, 'scheduled')}
                />
                </div>
              </div>

              <div style={{ justifyContent: 'center', alignItems: 'center', display: 'flex', flexDirection: 'column', height: '100%' }}>
              <HeaderContent userName={userName} chatTitle={chatTitle} onContentReady={({ greeting, headline, currentDate }) => {
                if (authState !== "app" || hasSpokenHeaderWelcomeRef.current) return;
                const safeGreeting = (greeting || "Welcome back").replace(/[\u{1F300}-\u{1FAFF}]/gu, "").trim();
                const safeHeadline = (headline || "How can I help you today?").trim();
                const safeDate = (currentDate || "today").trim();
                hasSpokenHeaderWelcomeRef.current = true;
                setOrbState("speaking");
                screenReader.speak(`${safeGreeting}. Today is ${safeDate}. ${safeHeadline}`, { onComplete: () => setOrbState("idle") });
              }} />

              <section className="workspace-caption-stage" role="status" aria-live="polite" aria-atomic="true" aria-label="Live conversation text" tabIndex={0}>
                <div className="workspace-caption-scroll">
                  <p className={`workspace-caption-text ${liveCaptionPages.length === 0 ? "is-empty" : ""}`}>
                    {liveCaptionPages[liveCaptionPage] || liveCaptionText}
                  </p>
                </div>

                {liveCaptionPages.length > 1 && (
                  <div className="workspace-caption-pagination" aria-label="Caption pagination controls">
                    <button
                      type="button"
                      className="workspace-caption-page-btn"
                      onClick={() => setLiveCaptionPage((prev) => Math.max(prev - 1, 0))}
                      disabled={liveCaptionPage === 0}
                      aria-label="Previous text page"
                    >
                      ←
                    </button>
                    <span className="workspace-caption-page-meta">
                      {liveCaptionPage + 1} / {liveCaptionPages.length}
                    </span>
                    <button
                      type="button"
                      className="workspace-caption-page-btn"
                      onClick={() => setLiveCaptionPage((prev) => Math.min(prev + 1, liveCaptionPages.length - 1))}
                      disabled={liveCaptionPage >= liveCaptionPages.length - 1}
                      aria-label="Next text page"
                    >
                      →
                    </button>
                  </div>
                )}
              </section>

              {/* Draft Panel - slides from right */}
              <DraftPanel
                isOpen={draftFlow.active}
                draftFlow={draftFlow}
                onApprove={() => processText(userLanguageRef.current === "ar" ? "نعم" : "yes")}
                onReject={() => processText(userLanguageRef.current === "ar" ? "لا" : "no")}
                onContinue={() => {
                  const nextPage = draftFlow.pageIndex + 1;
                  if (nextPage < draftFlow.pages.length) promptDraftPage(nextPage);
                  else setDraftFlow(prev => ({ ...prev, awaitingPageApproval: false, awaitingFinalApproval: true }));
                }}
                onListenChoice={(listen) => {
                  if (listen) setDraftFlow(prev => ({ ...prev, awaitingListenChoice: false, awaitingPageApproval: true }));
                  else sendClarificationAnswer("yes", draftFlow.confirmationId);
                }}
                t={t}
              />

              <div style={{display: "flex", justifyContent: "center", width: "100%", zIndex: 10, alignItems: "center", marginTop: "20px",}}>
                  <VoiceControls
                  isRecording={isRecording}
                  orbState={orbState}
                  wakePulse={wakePulse || auraStatus === "armed"}
                  onMicClick={handleMicClick}
                  onCancel={() => { setOrbState("idle"); setUserMessage(""); setChatMode(true); }}
                  chatMode={chatMode}
                  setChatMode={setChatMode}
                  onSendText={handleTextSubmit}
                  onSettingsClick={() => setShowSettings(true)}
                  isExecuting={isExecuting}
                  onInterrupt={sendInterrupt}
                />
              </div>
              </div>
            </div>
          </main>

          {showSettings && (<SettingsModal onClose={() => setShowSettings(false)} onSave={(profileData) => {
            if (profileData.username) { localStorage.setItem("userName", profileData.username); setUserName(profileData.username); }
            if (profileData.voice) { localStorage.setItem("ttsVoice", profileData.voice); setTtsVoice(profileData.voice); }
            if (profileData.language) { const nextLanguage = profileData.language === "ar" ? "ar" : "en"; localStorage.setItem("preferredLanguage", nextLanguage); localStorage.setItem("appLanguage", nextLanguage); preferredLanguageRef.current = nextLanguage; setPreferredLanguage(nextLanguage); }
          }} onDeviceIdChange={(newId) => {
            const trimmed = (newId || "").trim();
            if (!trimmed) return;
            localStorage.setItem("desktopDeviceId", trimmed);
            setDeviceId(trimmed);
          }} onLogout={() => {
            localStorage.removeItem("onboardingComplete"); localStorage.removeItem("currentSessionId"); localStorage.removeItem("userName"); localStorage.removeItem("ttsVoice"); localStorage.removeItem("authMethod"); localStorage.removeItem("userId"); const newUserId = `user_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`; setUserId(newUserId); setUserName("User"); setAuthState("login");
          }} initialName={userName} initialVoice={ttsVoice} initialLanguage={preferredLanguage} initialDeviceId={deviceId} />)}

          {viewingChat && (<ChatHistory messages={viewingChat.messages} chatTitle={viewingChat.title} onClose={() => setViewingChat(null)} />)}
          {loadingHistory && (<div style={{ position: "fixed", inset: 0, zIndex: 2999, display: "flex", alignItems: "center", justifyContent: "center", background: "rgba(0,0,0,0.5)", backdropFilter: "blur(4px)" }}><div style={{ color: "white", fontSize: "14px", opacity: 0.7 }}>Loading chat...</div></div>)}
        </div>
      )}
    </>
  );
}

export default App;