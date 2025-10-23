import React, { useState, useEffect, useRef, useCallback } from "react";
import { Mic, MicOff } from "lucide-react";

const SpeechInput = ({ onTranscript }) => {
  const [isListening, setIsListening] = useState(false);
  const [permissionGranted, setPermissionGranted] = useState(false);
  const [intensity, setIntensity] = useState(0); // 0.0 to 1.0 (RMS amplitude)
  const [transcript, setTranscript] = useState("");
  const analyserRef = useRef(null);
  const audioContextRef = useRef(null);
  const sourceRef = useRef(null);
  const dataArrayRef = useRef(null);
  const mediaRecorderRef = useRef(null);
  const audioChunksRef = useRef([]);
  const micButtonSize = "100px";

  // 🎙️ Setup audio input for visualization and recording
  useEffect(() => {
    const setupAudio = async () => {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        const AudioContext = window.AudioContext || window.webkitAudioContext;
        const audioCtx = new AudioContext();
        const analyser = audioCtx.createAnalyser();
        analyser.fftSize = 1024;
        const source = audioCtx.createMediaStreamSource(stream);
        source.connect(analyser);
        const dataArray = new Uint8Array(analyser.frequencyBinCount);

        audioContextRef.current = audioCtx;
        analyserRef.current = analyser;
        sourceRef.current = source;
        dataArrayRef.current = dataArray;
        setPermissionGranted(true);
      } catch (e) {
        console.error("Microphone access denied:", e);
        setPermissionGranted(false);
      }
    };
    setupAudio();

    return () => {
      audioContextRef.current?.close().catch(() => {});
    };
  }, []);

  // 🌊 Mic visualizer intensity loop
  useEffect(() => {
    let raf;
    const tick = () => {
      const analyser = analyserRef.current;
      const dataArray = dataArrayRef.current;

      if (analyser && dataArray) {
        analyser.getByteTimeDomainData(dataArray);
        let sum = 0;
        for (let i = 0; i < dataArray.length; i++) {
          const v = (dataArray[i] - 128) / 128;
          sum += v * v;
        }
        const rms = Math.sqrt(sum / dataArray.length);
        setIntensity((prev) => prev * 0.8 + rms * 0.7);
      } else {
        setIntensity((prev) => prev * 0.95);
      }

      raf = requestAnimationFrame(tick);
    };

    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);

  // 🧠 Function placeholder: Send audio blob to your future STT API (Azure, Whisper, etc.)
  const sendToSTTAPI = async (audioBlob) => {
    try {
      console.log("Sending audio to STT API...");
      // Example placeholder:
      // const response = await fetch("YOUR_STT_API_ENDPOINT", {
      //   method: "POST",
      //   body: audioBlob,
      // });
      // const data = await response.json();
      // const text = data.transcript;
      const text = "[Placeholder transcript from STT API]";
      setTranscript(text);
      onTranscript?.(text);
    } catch (error) {
      console.error("Error sending to STT API:", error);
    }
  };

  // 🎧 Start/Stop Recording
  const handleToggleListening = useCallback(async () => {
    if (!permissionGranted) return;

    if (isListening) {
      // Stop recording
      setIsListening(false);
      mediaRecorderRef.current?.stop();
    } else {
      // Start recording
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mediaRecorder = new MediaRecorder(stream);
      audioChunksRef.current = [];

      mediaRecorder.ondataavailable = (event) => {
        if (event.data.size > 0) {
          audioChunksRef.current.push(event.data);
        }
      };

      mediaRecorder.onstop = () => {
        const audioBlob = new Blob(audioChunksRef.current, { type: "audio/webm" });
        sendToSTTAPI(audioBlob);
      };

      mediaRecorder.start();
      mediaRecorderRef.current = mediaRecorder;
      setIsListening(true);
    }
  }, [isListening, permissionGranted]);

  // 🌈 Mic visualization colors
  const palette = [
    [74, 0, 224],
    [142, 45, 226],
    [255, 0, 166],
    [255, 255, 255],
  ];

  const lerpColor = (a, b, t) => [
    Math.round(a[0] + (b[0] - a[0]) * t),
    Math.round(a[1] + (b[1] - a[1]) * t),
    Math.round(a[2] + (b[2] - a[2]) * t),
  ];

  const t = Math.min(1, intensity * 2.5);
  const innerGlowColor = lerpColor(palette[1], palette[2], t);
  const outerRippleColor = lerpColor(palette[2], palette[3], t);

  const scale = 1 + Math.min(0.2, intensity * 1.5);
  const boxShadow = isListening
    ? `0 0 ${20 + intensity * 60}px rgba(${outerRippleColor.join(",")}, ${0.4 + intensity * 0.5})`
    : `0 0 10px rgba(0, 0, 0, 0.4)`;

  const micFill = `linear-gradient(135deg,
      rgba(${palette[0].join(",")}, 1) 0%,
      rgba(${innerGlowColor.join(",")}, 1) 100%)`;

  return (
    <div className="flex flex-col items-center gap-4">
      {/* Mic Button */}
      <button
        onClick={handleToggleListening}
        disabled={!permissionGranted}
        className="relative flex items-center justify-center rounded-full transition-all duration-300 transform outline-none focus:ring-4 focus:ring-indigo-500/50 cursor-pointer"
        style={{
          width: micButtonSize,
          height: micButtonSize,
          transform: isListening ? `scale(${scale})` : "scale(1)",
          boxShadow: boxShadow,
          background: micFill,
        }}
        aria-label={isListening ? "Stop Listening" : "Start Listening"}
      >
        {isListening ? (
          <Mic className="text-white z-10" size={36} />
        ) : (
          <MicOff className="text-white z-10 opacity-70" size={36} />
        )}
      </button>

      {/* Status + Transcript */}
      <div className="text-center mt-2 w-full max-w-sm">
        <p
          className={`text-sm font-semibold ${
            isListening ? "text-pink-400" : "text-gray-400"
          }`}
        >
          {isListening
            ? "Listening... Say your command now"
            : "Tap to speak your next step."}
        </p>

        {transcript && (
          <div className="mt-2 p-2 bg-white/10 rounded-lg max-h-24 overflow-y-auto">
            <p className="text-sm text-white/90 italic">{transcript}</p>
          </div>
        )}

        {!permissionGranted && (
          <p className="text-xs text-red-400 mt-2">
            Microphone access is required for the visualizer and recording.
          </p>
        )}
      </div>
    </div>
  );
};

export default SpeechInput;
