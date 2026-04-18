import { useCallback, useEffect, useRef, useState } from "react";
import { requestTranscription } from "../utils/transcribeClient";

export function useSpeechRecognition({
  lang = "en-US",
  onResult,
  onEnd,
  onError,
} = {}) {
  const [listening, setListening] = useState(false);
  const [transcript, setTranscript] = useState("");
  const [supported] = useState(() => {
    const hasMedia = !!navigator?.mediaDevices?.getUserMedia;
    const hasAudioContext = typeof window !== "undefined" && !!window.AudioContext;
    return hasMedia && hasAudioContext;
  });

  const contextRef = useRef(null);
  const streamRef = useRef(null);
  const sourceNodeRef = useRef(null);
  const processorNodeRef = useRef(null);
  const chunksRef = useRef([]);
  const sampleRateRef = useRef(16000);
  const userSpokeRef = useRef(false);
  const lastSpeechAtRef = useRef(0);
  const noSpeechTimerRef = useRef(null);
  const stoppingRef = useRef(false);
  const onResultRef = useRef(onResult);
  const onEndRef = useRef(onEnd);
  const onErrorRef = useRef(onError);

  useEffect(() => {
    onResultRef.current = onResult;
  }, [onResult]);

  useEffect(() => {
    onEndRef.current = onEnd;
  }, [onEnd]);

  useEffect(() => {
    onErrorRef.current = onError;
  }, [onError]);

  const tearDown = useCallback(async () => {
    if (noSpeechTimerRef.current) {
      clearTimeout(noSpeechTimerRef.current);
      noSpeechTimerRef.current = null;
    }

    try {
      if (sourceNodeRef.current && processorNodeRef.current) {
        sourceNodeRef.current.disconnect(processorNodeRef.current);
      }
    } catch {
      // no-op
    }

    try {
      processorNodeRef.current?.disconnect();
    } catch {
      // no-op
    }

    if (streamRef.current) {
      for (const track of streamRef.current.getTracks()) {
        try {
          track.stop();
        } catch {
          // no-op
        }
      }
    }

    sourceNodeRef.current = null;
    processorNodeRef.current = null;
    streamRef.current = null;

    const ctx = contextRef.current;
    contextRef.current = null;
    if (ctx && ctx.state !== "closed") {
      try {
        await ctx.close();
      } catch {
        // no-op
      }
    }
  }, []);

  const concatFloat32 = (chunks) => {
    const total = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
    const merged = new Float32Array(total);
    let offset = 0;
    for (const chunk of chunks) {
      merged.set(chunk, offset);
      offset += chunk.length;
    }
    return merged;
  };

  const downsampleTo16k = (samples, inputRate) => {
    const targetRate = 16000;
    if (!samples.length || inputRate <= targetRate) {
      return { samples, sampleRate: inputRate };
    }

    const ratio = inputRate / targetRate;
    const outLength = Math.max(1, Math.round(samples.length / ratio));
    const out = new Float32Array(outLength);
    let inputIndex = 0;

    for (let i = 0; i < outLength; i++) {
      const nextIndex = Math.min(samples.length, Math.round((i + 1) * ratio));
      let sum = 0;
      let count = 0;
      for (let j = inputIndex; j < nextIndex; j++) {
        sum += samples[j];
        count++;
      }
      out[i] = count ? sum / count : 0;
      inputIndex = nextIndex;
    }

    return { samples: out, sampleRate: targetRate };
  };

  const hasHumanSpeech = (samples) => {
    if (!samples?.length) return false;
    const windowSize = 1024;
    let speechWindows = 0;
    for (let i = 0; i + windowSize <= samples.length; i += windowSize) {
      let energy = 0;
      for (let j = i; j < i + windowSize; j++) {
        const v = samples[j];
        energy += v * v;
      }
      const rms = Math.sqrt(energy / windowSize);
      if (rms > 0.012) {
        speechWindows++;
        if (speechWindows >= 3) return true;
      }
    }
    return false;
  };

  const encodeWavPcm16 = (samples, sampleRate) => {
    const byteRate = sampleRate * 2;
    const blockAlign = 2;
    const buffer = new ArrayBuffer(44 + samples.length * 2);
    const view = new DataView(buffer);

    const writeString = (offset, text) => {
      for (let i = 0; i < text.length; i++) {
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
    view.setUint32(28, byteRate, true);
    view.setUint16(32, blockAlign, true);
    view.setUint16(34, 16, true);
    writeString(36, "data");
    view.setUint32(40, samples.length * 2, true);

    let offset = 44;
    for (let i = 0; i < samples.length; i++, offset += 2) {
      const clamped = Math.max(-1, Math.min(1, samples[i]));
      view.setInt16(offset, clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff, true);
    }

    return new Uint8Array(buffer);
  };

  const bytesToBase64 = (bytes) => {
    let binary = "";
    const chunkSize = 0x8000;
    for (let i = 0; i < bytes.length; i += chunkSize) {
      const chunk = bytes.subarray(i, i + chunkSize);
      binary += String.fromCharCode(...chunk);
    }
    return btoa(binary);
  };

  const stopInternal = useCallback(async ({ autoSubmit = false, noSpeechDetected = false } = {}) => {
    if (stoppingRef.current) return;
    stoppingRef.current = true;

    const chunks = [...chunksRef.current];
    chunksRef.current = [];

    await tearDown();
    setListening(false);

    if (!autoSubmit || noSpeechDetected || !userSpokeRef.current || chunks.length === 0) {
      onEndRef.current?.();
      stoppingRef.current = false;
      return;
    }

    try {
      const merged = concatFloat32(chunks);
      const { samples, sampleRate } = downsampleTo16k(merged, sampleRateRef.current);
      if (!hasHumanSpeech(samples)) {
        onErrorRef.current?.("no-speech");
        onEndRef.current?.();
        stoppingRef.current = false;
        return;
      }

      const preferredLang = String(lang || "en-US").toLowerCase().startsWith("ar") ? "ar" : "en";
      const payload = await requestTranscription({
        audio_data: bytesToBase64(encodeWavPcm16(samples, sampleRate)),
        audio_mime_type: "audio/wav",
        session_id: "default",
        lang: preferredLang,
      });
      const text = String(payload?.transcript || "").trim();
      if (text && !/^couldn't catch that/i.test(text)) {
        setTranscript(text);
        onResultRef.current?.(text, true);
      } else {
        onErrorRef.current?.("no-speech");
      }
    } catch (error) {
      onErrorRef.current?.(error?.message || "stt-error");
    } finally {
      onEndRef.current?.();
      stoppingRef.current = false;
    }
  }, [lang, tearDown]);

  const start = useCallback(async () => {
    if (!supported || listening) return;

    setTranscript("");
    setListening(true);
    userSpokeRef.current = false;
    chunksRef.current = [];
    lastSpeechAtRef.current = Date.now();
    stoppingRef.current = false;

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });

      const context = new window.AudioContext();
      const source = context.createMediaStreamSource(stream);
      const processor = context.createScriptProcessor(4096, 1, 1);

      streamRef.current = stream;
      contextRef.current = context;
      sourceNodeRef.current = source;
      processorNodeRef.current = processor;
      sampleRateRef.current = context.sampleRate;

      source.connect(processor);
      processor.connect(context.destination);

      processor.onaudioprocess = (event) => {
        if (stoppingRef.current) return;

        const channelData = event.inputBuffer.getChannelData(0);
        chunksRef.current.push(new Float32Array(channelData));

        let energy = 0;
        for (let i = 0; i < channelData.length; i++) {
          energy += channelData[i] * channelData[i];
        }
        const rms = Math.sqrt(energy / channelData.length);
        const now = Date.now();

        if (rms > 0.015) {
          userSpokeRef.current = true;
          lastSpeechAtRef.current = now;
        }

        if (userSpokeRef.current && now - lastSpeechAtRef.current >= 5000) {
          void stopInternal({ autoSubmit: true });
        }
      };

      noSpeechTimerRef.current = setTimeout(() => {
        if (!userSpokeRef.current) {
          void stopInternal({ autoSubmit: true, noSpeechDetected: true });
        }
      }, 12000);
    } catch (error) {
      onErrorRef.current?.(error?.message || "mic-error");
      await tearDown();
      setListening(false);
    }
  }, [listening, stopInternal, supported, tearDown]);

  const stop = useCallback(() => {
    void stopInternal({ autoSubmit: false });
  }, [stopInternal]);

  useEffect(() => {
    return () => {
      void tearDown();
    };
  }, [tearDown]);

  return {
    listening,
    transcript,
    supported,
    networkUnavailable: false,
    start,
    stop,
    reset: () => setTranscript(""),
  };
}
