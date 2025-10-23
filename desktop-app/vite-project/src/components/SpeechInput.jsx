import React, { useState, useEffect, useRef, useCallback } from "react";
import { Mic, MicOff } from "lucide-react";

// 🎨 Voice Visualizer Component
const PALETTE = {
  IDLE_COLOR_RGB: [74, 0, 224],
};

const lerpColor = (a, b, t) =>
  `rgb(${Math.round(a[0] + (b[0] - a[0]) * t)}, ${Math.round(
    a[1] + (b[1] - a[1]) * t
  )}, ${Math.round(a[2] + (b[2] - a[2]) * t)})`;

const VoiceVisualizer = ({ t, glowColor, pulseColor }) => {
  const visualizerStyle = {
    transform: `scale(${1 + t * 0.1})`,
    transition: "all 0.1s ease-out",
    background: `radial-gradient(circle at 50% 50%, 
        ${glowColor} 0%, 
        ${lerpColor(PALETTE.IDLE_COLOR_RGB, [0, 0, 0], t * 0.5)} 100%)`,
    boxShadow: `0 0 ${10 + t * 40}px ${pulseColor}, 
                0 0 ${5 + t * 20}px ${glowColor} inset`,
    opacity: 0.8 + t * 0.2,
  };

  return (
    <div className="flex items-center justify-center">
      <style jsx="true">{`
        @keyframes liquid-flow {
          0% {
            transform: translate(-15%, -15%) rotate(0deg) scale(1.1);
          }
          50% {
            transform: translate(15%, 15%) rotate(180deg) scale(1.0);
          }
          100% {
            transform: translate(-15%, -15%) rotate(360deg) scale(1.1);
          }
        }
        .visualizer-base {
          position: relative;
          width: 180px;
          height: 180px;
          border-radius: 50%;
          background-color: transparent;
          filter: saturate(1.5) contrast(1.2);
        }
        .visualizer-base::before {
          content: '';
          position: absolute;
          inset: 10px;
          border-radius: 50%;
          background: radial-gradient(
            circle at 50% 50%,
            rgba(255, 255, 255, 0.2) 0%,
            rgba(0, 255, 255, 0.3) 30%,
            rgba(74, 0, 224, 0) 70%
          );
          animation: liquid-flow ${4 - t * 3}s linear infinite; 
          mix-blend-mode: soft-light;
          opacity: 0.8;
        }
      `}</style>

      <div className="visualizer-base" style={visualizerStyle}></div>
    </div>
  );
};

// 🎤 Main Speech Input Component
const SpeechInput = ({ onTranscript }) => {
  const [isListening, setIsListening] = useState(false);
  const [permissionGranted, setPermissionGranted] = useState(false);
  const [intensity, setIntensity] = useState(0);
  const [transcript, setTranscript] = useState("");
  const analyserRef = useRef(null);
  const audioContextRef = useRef(null);
  const sourceRef = useRef(null);
  const dataArrayRef = useRef(null);
  const mediaRecorderRef = useRef(null);
  const audioChunksRef = useRef([]);

  // 🎙️ Setup audio input
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

  // 🌊 Audio Intensity Loop
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

  // 🧠 Placeholder: Send to STT API
  const sendToSTTAPI = async (audioBlob) => {
    try {
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
      setIsListening(false);
      mediaRecorderRef.current?.stop();
    } else {
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

  // 🌈 Colors based on intensity
  const palette = [
    [74, 0, 224],
    [142, 45, 226],
    [255, 0, 166],
    [255, 255, 255],
  ];

  const t = Math.min(1, intensity * 2.5);
  const glowColor = lerpColor(palette[1], palette[2], t);
  const pulseColor = lerpColor(palette[2], palette[3], t);

  return (
    <div className="flex flex-col items-center gap-4">
      {/* Voice Visualizer as Mic Button */}
      <div
        onClick={handleToggleListening}
        className="cursor-pointer"
        title={isListening ? "Stop Listening" : "Start Listening"}
      >
        <VoiceVisualizer t={t} glowColor={glowColor} pulseColor={pulseColor} />
        <div className="absolute flex items-center justify-center top-0 left-0 w-full h-full pointer-events-none">
          {isListening ? (
            <Mic className="text-white z-10 opacity-80" size={48} />
          ) : (
            <MicOff className="text-white z-10 opacity-50" size={48} />
          )}
        </div>
      </div>

      {/* Status + Transcript */}
      <div className="text-center mt-2 w-full max-w-sm">
        <p
          className={`text-sm font-semibold ${
            isListening ? "text-pink-400" : "text-gray-400"
          }`}
        >
          {isListening ? "Listening... Say your command now" : "Tap to speak your next step."}
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
