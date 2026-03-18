// App.jsx
import React, { useState, useRef, useEffect } from "react";
import SpeechRecognition, { useSpeechRecognition } from "react-speech-recognition";
import Sidebar from "./components/SideBar";
import HeaderContent from "./components/HeaderContent";
import VoiceControls from "./components/VoiceControls";
import SettingsModal from "./components/SettingsModal";
import ThinkingIndicator from "./components/ThinkingIndicator";
import OnboardingPage from "./components/onboarding/OnboardingPage";
import LoginPage from "./components/onboarding/LoginPage";

function App() {
  /* ---------- STATE ---------- */
  const [orbState, setOrbState] = useState("idle");
  const [userMessage, setUserMessage] = useState("");
  const [assistantMessage, setAssistantMessage] = useState("");
  // user ID

  const [userId, setUserId] = useState(() => {
      const stored = localStorage.getItem("userId");
      if (stored) {
          console.log("[Auth] Using existing user ID:", stored);
          return stored;
      }
      const newUserId = `user_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
      localStorage.setItem("userId", newUserId);
      console.log("[Auth] Created new user ID:", newUserId);
      return newUserId;
  });

  
  // ✅ AUTH STATE — "app" | "login" | "onboard"
  const [authState, setAuthState] = useState(() => {
      if (localStorage.getItem("onboardingComplete") === "true") return "app";
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
  const [screenSize, setScreenSize] = useState("desktop");
  const [userName, setUserName] = useState(() => {
      return localStorage.getItem("userName") || "Labubu";
  });
  const [thinkingSteps, setThinkingSteps] = useState([]);
  const [isThinking, setIsThinking] = useState(false);
  // True when server-provided SSE thinking stream is connected
  const [sseConnected, setSseConnected] = useState(false);
  const [chats, setChats] = useState([]);
  const [chatTitle, setChatTitle] = useState("New Chat");

  const mediaRecorderRef = useRef(null);
  const audioChunksRef = useRef([]);
  const audioRef = useRef(new Audio());
  const audioContextRef = useRef(null);

  // Speech recognition (wake-word)
  const { transcript, interimTranscript, finalTranscript, resetTranscript, listening, browserSupportsSpeechRecognition } = useSpeechRecognition();

  // Ensure continuous listening starts on mount (if supported)
  useEffect(() => {
    if (!browserSupportsSpeechRecognition) {
      console.warn('[Wake] SpeechRecognition not supported by this browser');
      return;
    }

    try {
      SpeechRecognition.startListening({ continuous: true, language: 'en-US', interimResults: true });
      console.log('[Wake] Continuous wake-word listening started (mount)');

      // Quick sanity-check: if recognition does not start within 1s, log a hint
      setTimeout(() => {
        if (!listening) {
          console.warn('[Wake] SpeechRecognition did not report listening=true. Browser may not allow continuous recognition in this context.');
        }
      }, 1000);
    } catch (e) {
      console.warn('[Wake] Failed to start continuous listening on mount:', e);
    }

    return () => {
      try { SpeechRecognition.stopListening(); } catch (e) {}
    };
  }, [browserSupportsSpeechRecognition]);

  // Detect wake word in interim or final transcripts (word-boundary aware)
  useEffect(() => {
    const combined = `${interimTranscript || ''} ${finalTranscript || ''} ${transcript || ''}`.toLowerCase();
    if (/\baura\b/.test(combined)) {
      console.log('[Wake] Wake word detected in speech transcript');
      resetTranscript();
      if (!isRecording && orbState !== 'processing' && orbState !== 'speaking') {
        startRecording();
      }
    }
  }, [interimTranscript, finalTranscript, transcript]);

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

  /* ---------- LOAD CHAT LIST ---------- */
  useEffect(() => {
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
  }, [userId]);

    /* ---------- CONNECT TO THINKING STREAM ---------- */
  useEffect(() => {
    const eventSource = new EventSource(`http://localhost:8000/thinking-stream/${sessionId}`);

    // Robust SSE handler: normalize payloads to plain strings
    eventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);

        // Server sends { step: { action, step, session_id } }
        if (data.step) {
          const payload = data.step;
          let stepText = null;

          if (typeof payload === "string") stepText = payload;
          else if (payload && typeof payload === "object") {
            // Prefer the human-friendly 'step' field inside payload
            stepText = payload.step || payload.action || JSON.stringify(payload);
          }

          if (stepText && stepText.trim().length > 0) {
            setThinkingSteps(prev => [...prev, stepText]);
            setIsThinking(true);
          }

        } else if (Array.isArray(data.steps)) {
          // Normalize array entries to strings
          const arr = data.steps.map(s => (typeof s === 'string' ? s : (s && s.step) || JSON.stringify(s)));
          setThinkingSteps(arr);
          setIsThinking(arr.length > 0);
        }
      } catch (err) {
        // Fallback: plain text from server
        console.warn("[UI] Non-JSON SSE payload:", event.data);
        if (event.data && typeof event.data === 'string' && event.data.trim().length > 0) {
          setThinkingSteps(prev => [...prev, event.data.trim()]);
          setIsThinking(true);
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
  }, [sessionId]);

  /* ---------- HANDLE THINKING UPDATES ---------- */
  const handleThinkingUpdate = (step) => {
    console.log("[UI] Updating thinking step:", step);
    setThinkingSteps(prev => {
      // Avoid duplicates
      if (prev.includes(step)) return prev;
      return [...prev, step];
    });
    
    // Ensure thinking indicator is visible
    setIsThinking(true);
  };

  /* ---------- UI ACTIONS ---------- */
  const handleCancel = () => {
    console.log("[UI] Cancel pressed → switching to chat mode");
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
    const steps = ["Searching...", "Analyzing...", "Processing...", "Responding..."];
    
    for (let i = 0; i < steps.length; i++) {
      setThinkingSteps(prev => [...prev, steps[i]]);
      await new Promise(resolve => setTimeout(resolve, 800));
    }
    
    setIsThinking(false);
  };

  /* ---------- AUDIO RECORDING ---------- */
  const startRecording = async () => {
    try {
      console.log("[Audio] Starting recording...");
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);

      mediaRecorderRef.current = recorder;
      audioChunksRef.current = [];

      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) {
          audioChunksRef.current.push(e.data);
        }
      };

      recorder.onstop = () => {
        const blob = new Blob(audioChunksRef.current, { type: "audio/webm" });
        console.log(`[Audio] Recording stopped. Size: ${blob.size} bytes`);
        stream.getTracks().forEach((t) => t.stop());

        // Stop and clear audio context if used
        if (audioContextRef.current) {
          try { audioContextRef.current.close(); } catch (e) {}
          audioContextRef.current = null;
        }

        processAudio(blob);

        // Resume wake-word listening after processing audio
        try {
          if (SpeechRecognition.browserSupportsSpeechRecognition()) {
            SpeechRecognition.startListening({ continuous: true, language: 'en-US' });
            console.log('[Wake] Resumed wake-word listening');
          }
        } catch (e) {
          console.warn('[Wake] Failed to resume listening:', e);
        }
      };

      recorder.start();
      setIsRecording(true);
      setOrbState("listening");
      setUserMessage("Listening...");

      // Silence detection using Web Audio API
      try {
        const AudioCtx = window.AudioContext || window.webkitAudioContext;
        const audioCtx = new AudioCtx();
        audioContextRef.current = audioCtx;
        const sourceNode = audioCtx.createMediaStreamSource(stream);
        const analyser = audioCtx.createAnalyser();
        analyser.fftSize = 2048;
        sourceNode.connect(analyser);
        const bufferLength = analyser.fftSize;
        const dataArray = new Uint8Array(bufferLength);
        let silentStart = null;

        const checkSilence = () => {
          analyser.getByteTimeDomainData(dataArray);
          let sum = 0;
          for (let i = 0; i < bufferLength; i++) {
            const v = (dataArray[i] - 128) / 128;
            sum += v * v;
          }
          const rms = Math.sqrt(sum / bufferLength);
          if (rms < 0.01) {
            if (silentStart === null) silentStart = Date.now();
            else if (Date.now() - silentStart > 5000) {
              console.log('[Audio] Silence detected >5s, stopping recording');
              if (mediaRecorderRef.current && mediaRecorderRef.current.state === 'recording') {
                mediaRecorderRef.current.stop();
              }
            }
          } else {
            silentStart = null;
          }
          if (mediaRecorderRef.current && mediaRecorderRef.current.state === 'recording') {
            requestAnimationFrame(checkSilence);
          } else {
            try { audioCtx.close(); } catch (e) {}
          }
        };

        requestAnimationFrame(checkSilence);
      } catch (e) {
        console.warn('[Audio] Silence detection not available:', e);
      }
    } catch (error) {
      console.error("[Audio] Microphone access failed:", error);
      setAssistantMessage("Microphone access denied");
    }
  };

  const stopRecording = () => {
    if (mediaRecorderRef.current && isRecording) {
      console.log("[Audio] Stopping recording...");
      mediaRecorderRef.current.stop();
      setIsRecording(false);
    }
  };

  const handleMicClick = () => {
    console.log("[UI] Mic clicked. State:", orbState);
    if (orbState === "processing" || orbState === "speaking") return;
    isRecording ? stopRecording() : startRecording();
  };

  /* ---------- AUDIO → TEXT ---------- */
  const processAudio = async (blob) => {
    try {
      setOrbState("processing");
      setUserMessage("Processing...");
      console.log("[STT] Transcribing audio...");

      const reader = new FileReader();
      reader.readAsDataURL(blob);

      reader.onloadend = async () => {
        const base64 = reader.result.split(",")[1];

        const res = await fetch("http://localhost:8000/transcribe", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            audio_data: base64,
            session_id: sessionId,
            user_id: userId,
          }),
        });

        const data = await res.json();
        console.log("[STT] Response:", data);

        if (!res.ok) {
          throw new Error(data.detail || "Transcription failed");
        }

        console.log(`[STT] Transcript: "${data.transcript}"`);
        setUserMessage(data.transcript);
        await processText(data.transcript);
      };
    } catch (error) {
      console.error("[STT] Error:", error);
      setOrbState("idle");
      setAssistantMessage("Transcription failed");
    }
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
      setAssistantMessage("Failed to send message");
    }
  };

  /* ---------- SETTINGS & STOP DETECTION ---------- */
  const handleSettingsClick = () => {
    setShowSettings(!showSettings);
  };

  const handleSettingsSave = (profileData) => {
    console.log("[Settings] Saving profile:", profileData);
    localStorage.setItem("userName", profileData.username);
    setUserName(profileData.username);

    // Persist TTS voice if provided
    if (profileData.voice) {
      localStorage.setItem("ttsVoice", profileData.voice);
      setTtsVoice(profileData.voice);
    }
  };


  /* ---------- ONBOARDING COMPLETE ---------- */
  const handleOnboardingComplete = ({ username, preferences }) => {
      setUserName(username);
      if (preferences?.voice) setTtsVoice(preferences.voice);
      setAuthState("app");
  };

  /* ---------- LOGOUT ---------- */
  const handleLogout = () => {
      // Clear auth state but preserve the remembered username hint
      const rememberedUsername = localStorage.getItem("userName") || "";
      localStorage.removeItem("onboardingComplete");
      localStorage.removeItem("currentSessionId");
      // Keep userId so data isn't orphaned, keep userName so login can prefill
      setAuthState("login");
      setUserName("Labubu");
      console.log("[Auth] Logged out");
  };

  /* ---------- TEXT → AGENT ---------- */
  const processText = async (text) => {
    try {
      console.log("[Agent] Processing input:", text);

      // Detect "stop" command
      if (text.toLowerCase().includes("stop")) {
        console.log("[Agent] STOP command detected - initiating stop sequence");
        handleStopSequence();
        return;
      }

      // Detect settings request
      if (
        text.toLowerCase().includes("settings") ||
        text.toLowerCase().includes("open settings") ||
        text.toLowerCase().includes("show settings")
      ) {
        console.log("[Agent] Settings request detected");
        setShowSettings(true);
        setAssistantMessage("Opening settings for you");
        return;
      }

      console.log("[Agent] Clarification mode:", !!clarificationResponseToId);

      // Start thinking sequence (local simulation only when SSE not available)
      if (!sseConnected) await startThinkingSequence();

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
          }),
      });

      console.log("[Agent] Status:", res.status);
      const data = await res.json();
      console.log("[Agent] Full response:", data);

      if (!res.ok) {
        throw new Error(data.detail || "Backend error");
      }

      setThinkingSteps([]);
      setIsThinking(false);

      // ✅ Update chat title if provided (first message generates title)
      if (data.chat_title && data.chat_title !== "Chat") {
        console.log("[Chat] Received chat title:", data.chat_title);
        setChatTitle(data.chat_title);
        
        // Update backend chat metadata
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
          console.log("[Chat] Chat title updated on backend");
          
          // Reload chat list to show updated title
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

      if (data.status === "clarification_needed") {
        console.log("[Agent] Clarification requested:", data.question);
        setClarificationResponseToId(data.response_id);
        setAssistantMessage(data.question);
        await speakResponse(data.question);
      } else {
        // Normalize various possible response shapes into a safe string
        let responseText = "Task completed";

        if (data.text && typeof data.text === "string") {
          responseText = data.text;
        } else if (data.result) {
          if (typeof data.result === "string") {
            responseText = data.result;
          } else if (data.result.response && typeof data.result.response === "string") {
            responseText = data.result.response;
          } else if (data.result.text && typeof data.result.text === "string") {
            responseText = data.result.text;
          } else if (data.result && typeof data.result === 'object') {
            // Prefer brief 'details' or fallback to JSON summary
            responseText = data.result.details || data.result.summary || JSON.stringify(data.result);
          }
        }

        console.log("[Agent] Final response:", responseText);
        setClarificationResponseToId(null);
        setAssistantMessage(responseText);
        if (responseText && typeof responseText === 'string') await speakResponse(responseText);
      }
    } catch (error) {
      console.error("[Agent] Error:", error);
      setOrbState("idle");
      setAssistantMessage("Backend error");
      setThinkingSteps([]);
      setIsThinking(false);
    }
  };

  /* ---------- STOP SEQUENCE ---------- */
  const handleStopSequence = () => {
    console.log("[System] Executing stop sequence");
    stopRecording();
    setOrbState("idle");
    setUserMessage("");
    setAssistantMessage("Stop sequence initiated");
    setIsRecording(false);
    setChatMode(false);
    setShowSettings(false);
    setThinkingSteps([]);
    setIsThinking(false);
  };

  /* ---------- TEXT → SPEECH ---------- */
  const speakResponse = async (text) => {
  try {
    console.log("[TTS] Generating speech for:", text);

    const res = await fetch("http://localhost:8000/text-to-speech", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text,
        voice_name: ttsVoice,
      }),
    });

    const data = await res.json();
    console.log("[TTS] Response received:", {
      status: data.status,
      format: data.format,
      language: data.language,
      provider: data.provider,
      audioDataLength: data.audio_data?.length
    });

    if (!res.ok) {
      throw new Error(data.detail || "TTS failed");
    }

    // ✅ FIX 1: Use the format from the response
    const format = data.format || 'mp3';
    console.log("[TTS] Audio format:", format);

    // ✅ FIX 2: Convert base64 to blob correctly
    const binaryString = atob(data.audio_data);
    const bytes = new Uint8Array(binaryString.length);
    for (let i = 0; i < binaryString.length; i++) {
      bytes[i] = binaryString.charCodeAt(i);
    }
    
    // ✅ FIX 3: Use correct MIME type based on format
    const mimeType = format === 'mp3' ? 'audio/mpeg' : 'audio/wav';
    const audioBlob = new Blob([bytes], { type: mimeType });
    console.log("[TTS] Audio blob created:", {
      size: audioBlob.size,
      type: audioBlob.type
    });

    const url = URL.createObjectURL(audioBlob);
    console.log("[TTS] Audio blob URL:", url);
    
    // ✅ FIX 4: Set source and add better error handling
    audioRef.current.src = url;

    // ✅ FIX 5: Add all event listeners BEFORE loading
    audioRef.current.onloadeddata = () => {
      console.log("[TTS] Audio loaded, duration:", audioRef.current.duration);
    };

    audioRef.current.oncanplaythrough = async () => {
      console.log("[TTS] Audio ready to play");
      setOrbState("speaking");
      
      try {
        await audioRef.current.play();
        console.log("[TTS] Playback started successfully");
      } catch (playError) {
        console.error("[TTS] Play error:", playError);
        
        // ✅ FIX 6: Handle autoplay blocking
        if (playError.name === 'NotAllowedError') {
          console.warn("[TTS] Autoplay blocked");
        }
        setOrbState("idle");
      }
    };

    audioRef.current.onended = () => {
      console.log("[TTS] Playback finished");
      URL.revokeObjectURL(url);
      setOrbState("idle");
    };

    audioRef.current.onerror = (err) => {
      console.error("[TTS] Audio error:", err);
      console.error("[TTS] Error details:", {
        code: audioRef.current.error?.code,
        message: audioRef.current.error?.message
      });
      URL.revokeObjectURL(url);
      setOrbState("idle");
    };

    // ✅ FIX 7: Load the audio
    console.log("[TTS] Loading audio...");
    audioRef.current.load();

  } catch (error) {
    console.error("[TTS] Error:", error);
    setOrbState("idle");
  }
};


  /* ---------- RENDER ---------- */

  return (
      <>
        {authState === "login" && (
          <LoginPage
            onLogin={({ userId: realId, username, preferences }) => {
              localStorage.setItem("userId", realId);
              localStorage.setItem("userName", username);
              setUserId(realId);
              setUserName(username);
              if (preferences?.voice) setTtsVoice(preferences.voice);
              setAuthState("app");
            }}
            onSignUp={() => setAuthState("onboard")}
          />
        )}

        {authState === "onboard" && (
          <OnboardingPage userId={userId} onComplete={handleOnboardingComplete} />
        )}

        {authState === "app" && (
          <div className="app-root">
            <Sidebar
              collapsed={isSidebarCollapsed}
              onToggle={() => {
                console.log("[UI] Sidebar toggled");
                setIsSidebarCollapsed((p) => !p);
              }}
              onSettingsClick={handleSettingsClick}
              onNewChat={handleNewChat}
              chats={chats}
              onSwitchChat={handleSwitchChat}
              currentSessionId={sessionId}
            />

            <main className={`main-area ${isSidebarCollapsed && screenSize === "mobile" ? "mobile-sidebar-open" : ""}`}>
              <video autoPlay muted loop playsInline>
                <source src="/Background3.mp4" type="video/mp4" />
              </video>
              
              <div className="main-overlay">
                <HeaderContent userName={userName} chatTitle={chatTitle} />

                {isThinking && <ThinkingIndicator steps={thinkingSteps} />}

                {assistantMessage && !isThinking && (
                  <div className="response-container" role="status" aria-live="polite" aria-atomic="true" aria-label="Assistant response">
                    <div className="response-message">
                      {assistantMessage}
                    </div>
                  </div>
                )}

                <VoiceControls
                  isRecording={isRecording}
                  orbState={orbState}
                  onMicClick={handleMicClick}
                  onCancel={handleCancel}
                  chatMode={chatMode}
                  setChatMode={setChatMode}
                  onSendText={handleTextSubmit}
                  onSettingsClick={handleSettingsClick}
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
              />
            )}
          </div>
        )}
      </>
    );
}

export default App;