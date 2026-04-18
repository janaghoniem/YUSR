// App.jsx
import React, { useState, useRef, useEffect, useCallback } from "react";
import Sidebar from "./components/SideBar";
import HeaderContent from "./components/HeaderContent";
import VoiceControls from "./components/VoiceControls";
import SettingsModal from "./components/SettingsModal";
import OnboardingPage from "./components/onboarding/OnboardingPage";
import LoginPage from "./components/onboarding/LoginPage";
import ChatHistory from "./components/ChatHistory";
import TitleBar from "./components/TitleBar";
import Aurora from "./components/onboarding/Aurora";
import SplitText from "./components/onboarding/SplitText";
import screenReader from "./utils/ScreenReader";
import { requestTranscription } from "./utils/transcribeClient";
import { Mic, Pause, Square, X, ArrowUpRight, Sparkles, Cpu, Waves } from "lucide-react";

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

    if (onboardingComplete && storedUserId) {
      return "app";
    }

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
  const [clarificationResponseToId, setClarificationResponseToId] = useState(null);
  const [isRecording, setIsRecording] = useState(false);
  const [chatMode, setChatMode] = useState(false);
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(false);
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

  // Interrupt commands (EN + AR)
  const INTERRUPT_COMMANDS = {
    "aura stop": "stop", "aura pause": "pause", "aura undo": "stop",
    "aura resume": "resume", "aura continue": "resume",
    "أورا وقف": "stop", "أورا توقف": "stop", "أورا إيقاف": "stop",
    "أورا استمر": "resume", "أورا كمل": "resume", "أورا تراجع": "stop",
  };

  // Detected user language — set on first voice interaction, persists for session
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
      osc.onended = () => {
        ctx.close().catch(() => {
          // no-op
        });
      };
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
  }, [preferredLanguage]);

  useEffect(() => {
    if (authState !== "app") {
      hasSpokenHeaderWelcomeRef.current = false;
    }
  }, [authState]);

  const normalizeThinkingStep = useCallback((step) => {
    return (step || "")
      .toLowerCase()
      .replace(/[\u{1F300}-\u{1FAFF}]/gu, " ")
      .replace(/[.]{3,}/g, "")
      .replace(/[!؟?]+$/g, "")
      .replace(/\s+/g, " ")
      .trim();
  }, []);

  const translateThinkingStep = useCallback((step) => {
    if (!step || userLanguageRef.current !== "ar") return step;
    const normalized = normalizeThinkingStep(step);
    const map = {
      "processing input": "جاري معالجة الإدخال...",
      "analyzing your request": "جاري تحليل طلبك...",
      "checking your preferences": "جاري التحقق من تفضيلاتك...",
      "processing your request": "جاري تنفيذ طلبك...",
      "preparing for coordinator": "جاري التحضير للمنسق...",
      "received your request": "استلمت طلبك...",
      "preparing tasks": "جاري تجهيز الخطوات...",
      "creating execution plan": "جاري إنشاء خطة التنفيذ...",
      "searching": "جاري البحث...",
      "analyzing": "جاري التحليل...",
      "processing": "جاري المعالجة...",
      "responding": "جاري تجهيز الرد...",
      "thinking": "جاري التفكير..."
    };
    return map[normalized] || step;
  }, [normalizeThinkingStep]);

  const extractReadableText = useCallback((value) => {
    if (value == null) return "";
    if (typeof value === "string") {
      const trimmed = value.trim();
      if (!trimmed) return "";
      if ((trimmed.startsWith("{") && trimmed.endsWith("}")) || (trimmed.startsWith("[") && trimmed.endsWith("]"))) {
        try {
          const parsed = JSON.parse(trimmed);
          return extractReadableText(parsed);
        } catch {
          return trimmed;
        }
      }
      return trimmed;
    }
    if (Array.isArray(value)) {
      return value.map((item) => extractReadableText(item)).filter(Boolean).join("\n\n");
    }
    if (typeof value === "object") {
      const preferredKeys = ["full_content", "content", "spoken_text", "text", "response", "message", "summary", "result"];
      for (const key of preferredKeys) {
        if (value[key]) {
          const extracted = extractReadableText(value[key]);
          if (extracted) return extracted;
        }
      }
      if (Array.isArray(value.details)) {
        const detailText = value.details
          .map((d) => extractReadableText(d?.content || d?.text || d?.result || d))
          .filter(Boolean)
          .join("\n\n");
        if (detailText) return detailText;
      }
      return "";
    }
    return String(value);
  }, []);

  const stopThinkingSpeech = useCallback(() => {
    thinkingSpeechQueueRef.current = [];
    thinkingSpeechRunningRef.current = false;
    screenReader.stop();
  }, []);

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

    const ws = new WebSocket(`ws://localhost:8000/ws/${sessionId}`);
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
            setAssistantMessage(msg.question || msg.full_content || msg.draft_content || "");
            if (msg.user_language) {
              setUserLanguage(msg.user_language);
              localStorage.setItem("userLanguage", msg.user_language);
            }
            if (msg.question) {
              rememberUserLanguageFromText(msg.question);
            }
            if (msg.type === 'confirmation_needed') {
              setOrbState("speaking");
              screenReader.speak((msg.question || msg.full_content || ""), {
                onComplete: () => setOrbState("idle"),
              });
            } else {
              speakAssistantResponse(msg.question, msg.user_language || userLanguage);
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
            }

            speakAssistantResponse(cleanResponseText, msg.user_language || userLanguage);
            break;
          }

          case 'interrupt_ack':
            console.log('[WS] Interrupt acknowledged:', msg.command);
            if (msg.command === 'stop') {
              screenReader.stop();
              setOrbState("idle");
              setIsThinking(false);
              setCoordinatorActive(false);
              setThinkingSteps([]);
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
      // Auto-reconnect after 3s
      wsReconnectTimer.current = setTimeout(connectWebSocket, 3000);
    };

    ws.onerror = (err) => {
      console.warn('[WS] Error:', err);
      ws.close();
    };
  }, [sessionId, translateThinkingStep, stopThinkingSpeech, rememberUserLanguageFromText, speakAssistantResponse, t, extractReadableText, userLanguage, detectLanguageFromText, offerReadAloud, structuredResponse]);

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
    if (command === "AURA stop") {
      audioRef.current?.pause?.();
      if (audioRef.current) audioRef.current.currentTime = 0;
      stopThinkingSpeech();
      screenReader.stop();
    } else if (command === "AURA pause") {
      screenReader.pause();
    } else if (command === "AURA resume") {
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
  }, [sessionId, userId, deviceType, stopThinkingSpeech, userLanguage]);


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

      const isAffirmative = (value) => {
        const v = (value || "").trim().toLowerCase();
        return ["yes", "yeah", "yep", "sure", "ok", "okay", "نعم", "ايوه", "أيوا", "تمام", "موافق"].includes(v);
      };

      const isReadAloudIntent = (value) => {
        const v = (value || "").trim().toLowerCase();
        const normalizedArabic = v
          .replace(/أ|إ|آ/g, "ا")
          .replace(/ى/g, "ي")
          .replace(/ة/g, "ه")
          .replace(/\s+/g, " ")
          .trim();
        return [
          "read them out loud",
          "read it out loud",
          "read them aloud",
          "read it aloud",
          "read aloud",
          "read the results",
          "say it out loud",
          "read results out loud",
          "read the response out loud",
          "read this out loud",
          "read that out loud",
          "explain it",
          "explain the results",
          "اقرأها بصوت عالي",
          "اقراها بصوت عالي",
          "اقرأهم بصوت عالي",
          "اقراهم بصوت عالي",
          "اقرا النتائج بصوت عالي",
          "اقرأ النتائج بصوت عالي",
          "اقرا النتايج بصوت عالي",
          "اقرأ النتايج بصوت عالي",
          "اقرأها بصوت عال",
          "اقراها بصوت عال",
          "اقرأهم بصوت عال",
          "اقراهم بصوت عال",
          "اقرأها لي",
          "اقراها لي",
          "اقريها",
          "اقريهم",
          "اقرأهم لي",
          "اقراهم لي",
          "اقراها",
          "اقراهم",
          "اقرا النتائج",
          "اشرحها",
          "اشرح النتائج"
        ].some((phrase) => {
          const p = phrase
            .toLowerCase()
            .replace(/أ|إ|آ/g, "ا")
            .replace(/ى/g, "ي")
            .replace(/ة/g, "ه")
            .replace(/\s+/g, " ")
            .trim();
          return normalizedArabic === p || normalizedArabic.includes(p);
        });
      };

      if (offerReadAloud && structuredResponse?.full_content && isReadAloudIntent(normalized)) {
        setClarificationResponseToId(null);
        handleReadAloud();
        setAssistantMessage(t("Reading the results now.", "حسنًا، سأقرأ النتائج الآن."));
        return;
      }

      if (isAffirmative(normalized)) {
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

      // Check for interrupt commands in text input too
      const lowerText = normalized.toLowerCase();
      for (const [phrase, command] of Object.entries(INTERRUPT_COMMANDS)) {
        if (lowerText.includes(phrase) || lowerText === command) {
          console.log(`[Agent] Interrupt command in text: "${phrase}" → ${command}`);
          sendInterrupt(command);
          return;
        }
      }

      // Detect "stop" command (legacy)
      if (lowerText === "stop" || lowerText === "aura stop") {
        console.log("[Agent] STOP command detected");
        sendInterrupt("stop");
        handleStopSequence();
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
  const isExecuting = orbState === "processing" || isThinking;
  const appClassName = [
    "app-root",
    executionMode === "transparent" && isExecuting ? "transparent-mode" : "",
    executionMode === "widget" ? "widget-mode" : "",
  ].filter(Boolean).join(" ");
  const liveCaptionText =
    (userMessage && (!assistantMessage || orbState === "listening" || isRecording))
      ? userMessage
      : (assistantMessage || (isThinking
          ? t("Thinking...", "بفكر...")
          : (listening
              ? t("Listening for your voice...", "أستمع لصوتك...")
              : t("Tap the mic to speak", "اضغط على الميكروفون للتحدث"))));

  return (
    <>
      {executionMode !== "widget" && (
        <TitleBar
          transparent={authState !== "app"}
          showExtraControls={authState === "app"}
          isExecuting={isExecuting}
          executionMode={executionMode}
          onToggleExecutionMode={toggleExecutionMode}
          onEnterWidgetMode={enterWidgetMode}
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
                    <input
                      className="widget-text-input"
                      type="text"
                      placeholder="Ask AURA..."
                      value={widgetText}
                      onChange={(e) => setWidgetText(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && widgetText.trim()) {
                          handleTextSubmit(widgetText);
                          setWidgetText("");
                        }
                      }}
                    />
                    <button
                      className="widget-mic-btn"
                      onClick={handleMicClick}
                      title={isRecording ? "Stop recording" : "Voice input"}
                    >
                      <Mic size={16} />
                    </button>
                  </>
                ) : (
                  <div className="widget-exec-controls">
                    <button
                      className="widget-action-btn widget-pause"
                      onClick={() => sendInterrupt("pause")}
                      title="Pause"
                    >
                      <Pause size={14} />
                    </button>
                    <button
                      className="widget-action-btn widget-stop"
                      onClick={() => sendInterrupt("stop")}
                      title="Stop"
                    >
                      <Square size={14} />
                    </button>
                  </div>
                )}
              </div>

              <div className="widget-window-controls">
                <button className="widget-win-btn" onClick={exitWidgetMode} title="Expand">
                  <ArrowUpRight size={14} />
                </button>
                <button className="widget-win-btn widget-win-close" onClick={() => window.electronAPI?.closeWindow?.()} title="Close">
                  <X size={14} />
                </button>
              </div>
            </div>
          )}

          <Sidebar
            collapsed={isSidebarCollapsed || executionMode === "widget"}
            onToggle={() => {
              console.log("[UI] Sidebar toggled");
              setIsSidebarCollapsed((p) => !p);
            }}
            onSettingsClick={handleSettingsClick}
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
              <iframe src="/aura-cinematic-bg.html"
                style={{ position: "absolute", width: "100%", height: "100%", border: "none", pointerEvents: "none", zIndex: 0 }}
                title="Cinematic Background"
              />
              <div className="main-bg-core">
                <img src="/aura_icon_white.png" alt="" className="main-bg-aura-icon" />
                <div className="main-bg-core-ring" />
              </div>
            </div>

            <div className="main-overlay">
              <HeaderContent
                userName={userName}
                chatTitle={chatTitle}
                onContentReady={handleHeaderContentReady}
              />

              <div className="mini-live-caption" role="status" aria-live="polite" aria-atomic="true" aria-label="Live caption">
                <div className="mini-live-caption-kicker">
                  {userMessage && (!assistantMessage || orbState === "listening") ? "You" : "AURA"}
                </div>
                <div className="mini-live-caption-text">
                  <SplitText
                    key={liveCaptionText}
                    text={liveCaptionText}
                    delay={22}
                  />
                </div>
              </div>

              <VoiceControls
                isRecording={isRecording}
                orbState={orbState}
                wakePulse={wakePulse || auraStatus === "armed"}
                onMicClick={handleMicClick}
                onCancel={handleCancel}
                chatMode={chatMode}
                setChatMode={setChatMode}
                onSendText={handleTextSubmit}
                onSettingsClick={handleSettingsClick}
                isExecuting={isExecuting}
                onInterrupt={sendInterrupt}
              />
            </div>
          </main>

          {showSettings && (
            <SettingsModal
              onClose={() => setShowSettings(false)}
              onSave={handleSettingsSave}
              onLogout={handleLogout}
              initialName={userName}
              initialVoice={ttsVoice}
              initialLanguage={preferredLanguage}
            />
          )}

          {viewingChat && (
            <ChatHistory
              messages={viewingChat.messages}
              chatTitle={viewingChat.title}
              onClose={() => setViewingChat(null)}
            />
          )}

          {loadingHistory && (
            <div style={{
              position: "fixed", inset: 0, zIndex: 2999,
              display: "flex", alignItems: "center", justifyContent: "center",
              background: "rgba(0,0,0,0.5)", backdropFilter: "blur(4px)"
            }}>
              <div style={{ color: "white", fontSize: "14px", opacity: 0.7 }}>
                Loading chat...
              </div>
            </div>
          )}

        </div>
      )}
    </>
  );
}

export default App;