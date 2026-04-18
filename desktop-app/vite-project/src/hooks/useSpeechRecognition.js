/**
 * useSpeechRecognition.js
 *
 * A robust wrapper around the Web Speech API that:
 *  1. Handles "network" errors gracefully — stops retrying instead of infinite-looping
 *  2. Supports Egyptian Arabic (ar-EG) and English (en-US)
 *  3. Provides a simple callback-based API
 *  4. Automatically disables itself when the environment doesn't support STT
 *
 * IMPORTANT ABOUT NETWORK ERRORS IN ELECTRON
 * -------------------------------------------
 * Chrome's SpeechRecognition normally sends audio to Google's servers.
 * In Electron (offline or without Google API access) this always fails
 * with error: "network".  When this happens we:
 *  a) Stop retrying (no infinite loop)
 *  b) Set `networkUnavailable = true` so the UI can show a "type instead" message
 *  c) Still expose transcript="" / listening=false so nothing breaks
 */

import { useRef, useState, useCallback, useEffect } from "react";

const SpeechRecognitionAPI =
  typeof window !== "undefined"
    ? window.SpeechRecognition || window.webkitSpeechRecognition
    : null;

/**
 * @param {object} options
 * @param {string}   [options.lang="en-US"]       BCP-47 language tag
 * @param {boolean}  [options.continuous=false]    Keep listening after each result?
 * @param {boolean}  [options.interimResults=true] Report partial results?
 * @param {number}   [options.maxNetworkRetries=0] How many times to retry on network error (0 = never)
 * @param {function} [options.onResult]            Called with (transcript: string, isFinal: boolean)
 * @param {function} [options.onEnd]               Called when recognition session ends
 * @param {function} [options.onError]             Called with (errorCode: string)
 */
export function useSpeechRecognition({
  lang = "en-US",
  continuous = false,
  interimResults = true,
  maxNetworkRetries = 0,
  onResult,
  onEnd,
  onError,
} = {}) {
  const [listening,          setListening]          = useState(false);
  const [transcript,         setTranscript]         = useState("");
  const [networkUnavailable, setNetworkUnavailable] = useState(false);
  const [supported]  = useState(!!SpeechRecognitionAPI);

  const recRef          = useRef(null);
  const voskLoopRef     = useRef(null);
  const networkRetries  = useRef(0);
  const stoppedRef      = useRef(true);  // true = we deliberately stopped
  const onResultRef     = useRef(onResult);
  const onEndRef        = useRef(onEnd);
  const onErrorRef      = useRef(onError);

  // Keep callback refs current without re-creating the recognition object
  useEffect(() => { onResultRef.current = onResult; }, [onResult]);
  useEffect(() => { onEndRef.current   = onEnd;     }, [onEnd]);
  useEffect(() => { onErrorRef.current = onError;   }, [onError]);

  const stop = useCallback(() => {
    stoppedRef.current = true;
    setListening(false);
    if (voskLoopRef.current) {
      clearTimeout(voskLoopRef.current);
      voskLoopRef.current = null;
    }
    if (recRef.current) {
      try { recRef.current.stop(); } catch { /* no-op */ }
    }
  }, []);

  const start = useCallback(() => {
    const hasElectronVosk = !!window?.electronAPI?.transcribeOnce;

    if (hasElectronVosk) {
      stoppedRef.current = false;
      setTranscript("");
      setListening(true);

      const loop = async () => {
        if (stoppedRef.current) {
          setListening(false);
          return;
        }

        try {
          const logicalLang = String(lang || "en-US").toLowerCase().startsWith("ar") ? "ar" : "en";
          const result = await window.electronAPI.transcribeOnce({ lang: logicalLang, timeoutMs: 6000 });
          const text = (result?.text || "").trim();
          if (text) {
            setTranscript(text);
            onResultRef.current?.(text, true);
          }
        } catch (err) {
          console.warn("[VOSK-HOOK] transcribeOnce failed:", err?.message || err);
          onErrorRef.current?.("vosk-error");
        }

        onEndRef.current?.();
        if (continuous && !stoppedRef.current) {
          voskLoopRef.current = setTimeout(loop, 120);
        } else {
          setListening(false);
        }
      };

      void loop();
      return;
    }

    if (!supported) return;
    if (networkUnavailable) {
      console.warn("[STT] Network unavailable — STT disabled, use text input.");
      return;
    }

    stoppedRef.current = false;
    setTranscript("");

    // Re-create each session (Chrome doesn't allow restarting same instance)
    if (recRef.current) {
      try { recRef.current.abort(); } catch { /* no-op */ }
    }

    const rec = new SpeechRecognitionAPI();
    rec.lang            = lang;
    rec.continuous      = continuous;
    rec.interimResults  = interimResults;
    recRef.current      = rec;

    rec.onstart = () => {
      networkRetries.current = 0;
      setListening(true);
    };

    rec.onresult = (event) => {
      let interim = "";
      let final_  = "";
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const t = event.results[i][0].transcript;
        if (event.results[i].isFinal) final_ += t;
        else interim += t;
      }
      const combined = (final_ || interim).trim();
      if (combined) {
        setTranscript(combined);
        onResultRef.current?.(combined, !!final_);
      }
    };

    rec.onerror = (event) => {
      const code = event.error;

      if (code === "network") {
        // Google STT endpoint unreachable (common in offline Electron)
        if (networkRetries.current < maxNetworkRetries) {
          networkRetries.current++;
          console.warn(`[STT] Network error — retry ${networkRetries.current}/${maxNetworkRetries}`);
          // Will retry on onend
        } else {
          console.warn("[STT] Network STT unavailable. Switching to text-input mode.");
          setNetworkUnavailable(true);
          stoppedRef.current = true;
          setListening(false);
          onErrorRef.current?.(code);
        }
        return;
      }

      if (code === "not-allowed" || code === "service-not-allowed") {
        console.error("[STT] Microphone permission denied.");
        stoppedRef.current = true;
        setListening(false);
        onErrorRef.current?.(code);
        return;
      }

      if (code === "no-speech" || code === "aborted") {
        // Non-fatal — onend will handle restart if still active
        return;
      }

      console.warn("[STT] Recognition error:", code);
      onErrorRef.current?.(code);
    };

    rec.onend = () => {
      setListening(false);
      onEndRef.current?.();

      // Auto-restart only if:
      //  - continuous mode requested
      //  - we weren't deliberately stopped
      //  - network isn't broken
      if (continuous && !stoppedRef.current && !networkUnavailable) {
        setTimeout(() => {
          if (!stoppedRef.current) start();
        }, 300);
      }
    };

    try {
      rec.start();
    } catch (err) {
      console.warn("[STT] Could not start recognition:", err.message);
      setListening(false);
    }
  }, [lang, continuous, interimResults, maxNetworkRetries, supported, networkUnavailable]);

  // Clean up on unmount
  useEffect(() => {
    return () => {
      stoppedRef.current = true;
      if (voskLoopRef.current) {
        clearTimeout(voskLoopRef.current);
        voskLoopRef.current = null;
      }
      try { recRef.current?.abort(); } catch { /* no-op */ }
    };
  }, []);

  return {
    listening,
    transcript,
    supported,
    networkUnavailable,
    start,
    stop,
    reset: () => setTranscript(""),
  };
}