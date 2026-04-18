/* eslint-env node */
import path from "path";
import fs from "fs";
import process from "node:process";
import { app } from "electron";
import { createRequire } from "module";

const require = createRequire(import.meta.url);

let vosk = null;
let micLib = null;
try {
  vosk = require("vosk");
  micLib = require("mic");
} catch (err) {
  console.warn("[VOSK] Dependencies unavailable:", err?.message || err);
}

let initialized = false;
let isTranscribing = false;
let timeoutHandle = null;
let activeLang = "en";

let modelEn = null;
let modelAr = null;
let wakeEn = null;
let wakeAr = null;
let fullEn = null;
let fullAr = null;

let micInstance = null;
let micInputStream = null;
let mainWindowRef = null;

let transcribeOnceRequest = null;

function safeParse(jsonLike) {
  try {
    return JSON.parse(jsonLike);
  } catch {
    return {};
  }
}

function resolveModelPath(basePath, candidates) {
  for (const c of candidates) {
    const p = path.join(basePath, c);
    try {
      if (fs.existsSync(p)) return p;
    } catch {
      // Continue.
    }
  }
  return null;
}

function emit(channel, payload) {
  try {
    if (mainWindowRef && !mainWindowRef.isDestroyed()) {
      mainWindowRef.webContents.send(channel, payload);
    }
  } catch (err) {
    console.warn(`[VOSK] Failed emit on ${channel}:`, err?.message || err);
  }
}

function stopSTT() {
  isTranscribing = false;
  if (timeoutHandle) {
    clearTimeout(timeoutHandle);
    timeoutHandle = null;
  }
  emit("aura-status", "idle");
}

function resetSTTTimeout() {
  if (timeoutHandle) clearTimeout(timeoutHandle);
  timeoutHandle = setTimeout(() => stopSTT(), 5000);
}

function startSTT(lang) {
  isTranscribing = true;
  activeLang = lang === "ar" ? "ar" : "en";
  emit("aura-status", "listening");
  emit("aura-wake-word", { lang: activeLang });
  resetSTTTimeout();
}

function handleTranscribeOnceResult(text, lang) {
  if (!transcribeOnceRequest) return;
  const req = transcribeOnceRequest;
  transcribeOnceRequest = null;
  try {
    if (req.timeout) clearTimeout(req.timeout);
  } catch {
    // Ignore.
  }
  req.resolve({ text, lang });
}

function onAudioData(data) {
  if (!wakeEn || !wakeAr || !fullEn || !fullAr) return;

  if (transcribeOnceRequest) {
    const rec = transcribeOnceRequest.lang === "ar" ? fullAr : fullEn;
    if (rec.acceptWaveform(data)) {
      const result = safeParse(rec.result());
      const text = (result.text || "").trim();
      if (text) {
        handleTranscribeOnceResult(text, transcribeOnceRequest.lang);
      }
    } else {
      const partial = safeParse(rec.partialResult());
      if (partial.partial) {
        emit("aura-partial-text", partial.partial);
      }
    }
    return;
  }

  if (!isTranscribing) {
    if (wakeEn.acceptWaveform(data)) {
      const res = safeParse(wakeEn.result());
      if (res.text && res.text !== "[unk]") startSTT("en");
    }
    if (wakeAr.acceptWaveform(data)) {
      const res = safeParse(wakeAr.result());
      if (res.text && res.text !== "[unk]") startSTT("ar");
    }
    return;
  }

  const rec = activeLang === "ar" ? fullAr : fullEn;
  if (rec.acceptWaveform(data)) {
    const result = safeParse(rec.result());
    const text = (result.text || "").trim();
    if (text) {
      emit("aura-final-command", { text, lang: activeLang });
      stopSTT();
    }
  } else {
    const partial = safeParse(rec.partialResult());
    if (partial.partial) {
      emit("aura-partial-text", partial.partial);
      resetSTTTimeout();
    }
  }
}

export function initAura(mainWindow) {
  mainWindowRef = mainWindow;

  if (initialized) return { ok: true, provider: "vosk" };
  if (!vosk || !micLib) {
    return { ok: false, provider: "none", error: "vosk or mic dependency missing" };
  }

  const { Model, Recognizer } = vosk;
  const resPath = app.isPackaged
    ? process.resourcesPath
    : path.join(process.cwd(), "resources");

  const enModelPath = resolveModelPath(resPath, [
    "vosk-model-en",
    "vosk-model-small-en-us-0.15",
  ]);

  const arModelPath = resolveModelPath(resPath, [
    "vosk-model-ar",
    "vosk-model-ar-mgb2-0.4",
  ]);

  if (!enModelPath || !arModelPath) {
    return {
      ok: false,
      provider: "none",
      error: `Missing model paths in resources folder (${resPath})`,
    };
  }

  modelEn = new Model(enModelPath);
  modelAr = new Model(arModelPath);

  wakeEn = new Recognizer({
    model: modelEn,
    sampleRate: 16000,
    grammar: ["hey aura", "hello aura", "aura", "[unk]"],
  });

  wakeAr = new Recognizer({
    model: modelAr,
    sampleRate: 16000,
    grammar: ["يا اورا", "يا أورا", "اهلا اورا", "اهلا أورا", "اورا", "أورا", "[unk]"],
  });

  fullEn = new Recognizer({ model: modelEn, sampleRate: 16000 });
  fullAr = new Recognizer({ model: modelAr, sampleRate: 16000 });

  micInstance = micLib({
    rate: "16000",
    channels: "1",
    debug: false,
    fileType: "raw",
  });
  micInputStream = micInstance.getAudioStream();
  micInputStream.on("data", onAudioData);
  micInputStream.on("error", (err) => {
    console.warn("[VOSK] Microphone stream error:", err?.message || err);
  });

  micInstance.start();
  initialized = true;
  emit("aura-status", "idle");

  return { ok: true, provider: "vosk" };
}

export function transcribeOnce(lang = "en", timeoutMs = 10000) {
  return new Promise((resolve, reject) => {
    if (!initialized) {
      reject(new Error("Aura wake word service is not initialized"));
      return;
    }

    if (transcribeOnceRequest) {
      reject(new Error("A transcription request is already in progress"));
      return;
    }

    const request = {
      lang: lang === "ar" ? "ar" : "en",
      resolve,
      reject,
      timeout: null,
    };

    request.timeout = setTimeout(() => {
      if (transcribeOnceRequest === request) {
        transcribeOnceRequest = null;
        resolve({ text: "", lang: request.lang, timeout: true });
      }
    }, Math.max(1500, timeoutMs));

    transcribeOnceRequest = request;
  });
}

export function shutdownAura() {
  try {
    if (timeoutHandle) clearTimeout(timeoutHandle);
    timeoutHandle = null;

    if (transcribeOnceRequest && transcribeOnceRequest.timeout) {
      clearTimeout(transcribeOnceRequest.timeout);
      transcribeOnceRequest.resolve({ text: "", lang: transcribeOnceRequest.lang, timeout: true });
      transcribeOnceRequest = null;
    }

    if (micInputStream) {
      try {
        micInputStream.removeListener("data", onAudioData);
      } catch {
        // Ignore.
      }
    }

    if (micInstance) {
      try {
        micInstance.stop();
      } catch {
        // Ignore.
      }
    }
  } finally {
    initialized = false;
  }
}
