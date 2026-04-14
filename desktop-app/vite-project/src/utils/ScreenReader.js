/**
 * ScreenReader.js — Browser-native TTS using Web Speech API SpeechSynthesis.
 * Features:
 *  - Egyptian Arabic (ar-EG) and English (en-US) support
 *  - Language-aware voice selection
 *  - Sentence chunking with progress callbacks
 *  - Pause / resume / stop
 *  - Singleton export + language setter for onboarding integration
 */

class ScreenReader {
  constructor() {
    this.synth     = window.speechSynthesis;
    this.utterances    = [];
    this.currentIndex  = 0;
    this.isPaused      = false;
    this.isSpeaking    = false;
    this.onProgress    = null;
    this.onComplete    = null;
    this.onStart       = null;
    this._voice        = null;
    this._rate         = 1.0;
    this._pitch        = 1.0;
    this._volume       = 1.0;
    // Language override — set externally when the user picks a language.
    // "ar" → Egyptian Arabic; "en" → US English; null → auto-detect.
    this._langOverride = null;
  }

  /** Set forced language ("ar" | "en" | null for auto-detect) */
  setLanguage(lang) {
    this._langOverride = lang ? lang.toLowerCase().slice(0, 2) : null;
  }

  /** Return available voices, optionally filtered by language prefix */
  getVoices(lang = null) {
    const voices = this.synth.getVoices();
    if (!lang) return voices;
    return voices.filter((v) => v.lang.startsWith(lang));
  }

  /** Override the voice used for TTS */
  setVoice(voice) {
    if (typeof voice === "string") {
      const voices = this.synth.getVoices();
      this._voice = voices.find((v) => v.name === voice) || null;
    } else {
      this._voice = voice;
    }
  }

  /** Adjust rate / pitch / volume */
  configure({ rate, pitch, volume } = {}) {
    if (rate   !== undefined) this._rate   = Math.max(0.1, Math.min(10, rate));
    if (pitch  !== undefined) this._pitch  = Math.max(0,   Math.min(2, pitch));
    if (volume !== undefined) this._volume = Math.max(0,   Math.min(1, volume));
  }

  /** Split text into speakable sentence chunks */
  _splitIntoSentences(text) {
    if (!text) return [];
    const sentences = text
      .split(/(?<=[.!?؟،\n])\s+/)
      .map((s) => s.trim())
      .filter((s) => s.length > 0);

    if (sentences.length <= 1 && text.length > 150) {
      const chunks = [];
      let remaining = text;
      while (remaining.length > 0) {
        if (remaining.length <= 150) { chunks.push(remaining); break; }
        let bp = remaining.lastIndexOf(",", 150);
        if (bp < 50) bp = remaining.lastIndexOf(" ", 150);
        if (bp < 50) bp = 150;
        chunks.push(remaining.substring(0, bp + 1).trim());
        remaining = remaining.substring(bp + 1).trim();
      }
      return chunks;
    }
    return sentences;
  }

  /** Heuristic language detection (Arabic vs English) */
  _detectLanguage(text) {
    if (this._langOverride) return this._langOverride;
    const arabicChars = (text.match(/[\u0600-\u06FF\u0750-\u077F]/g) || []).length;
    return arabicChars > text.length * 0.25 ? "ar" : "en";
  }

  /** Pick the best available voice for a given 2-letter language code */
  _selectBestVoice(lang) {
    const voices = this.getVoices(lang);
    if (!voices || voices.length === 0) return null;

    // Preferred voices: Egyptian Arabic first, then generic Arabic; for EN, prefer common quality voices
    const preferredNames =
      lang === "ar"
        ? ["Google Arabic", "Microsoft Hoda", "Egypt", "Arabic", "ar-EG"]
        : [
            "Google US English",
            "Microsoft Aria Online",
            "Microsoft Jenny Online",
            "Microsoft Zira",
            "Samantha",
            "Daniel",
            "Karen",
          ];

    for (const hint of preferredNames) {
      const found = voices.find((v) =>
        (v.name || "").toLowerCase().includes(hint.toLowerCase())
      );
      if (found) return found;
    }
    return voices.find((v) => v.default) || voices[0] || null;
  }

  /**
   * Speak text aloud.
   * @param {string} text
   * @param {{ onProgress?, onComplete?, onStart? }} callbacks
   * @returns {Promise<void>}
   */
  speak(text, { onProgress, onComplete, onStart } = {}) {
    return new Promise((resolve) => {
      this.stop();

      if (!text || text.trim().length === 0) { resolve(); return; }

      this.onProgress = onProgress || this.onProgress;
      this.onComplete = onComplete || this.onComplete;
      this.onStart    = onStart    || this.onStart;

      const sentences = this._splitIntoSentences(text);
      this.utterances   = [];
      this.currentIndex = 0;
      this.isSpeaking   = true;
      this.isPaused     = false;

      const lang  = this._detectLanguage(text);
      const bcp47 = lang === "ar" ? "ar-EG" : "en-US";
      const voice = this._voice || this._selectBestVoice(lang);

      sentences.forEach((sentence, index) => {
        const utt      = new SpeechSynthesisUtterance(sentence);
        utt.rate       = this._rate;
        utt.pitch      = this._pitch;
        utt.volume     = this._volume;
        utt.lang       = bcp47;
        if (voice) utt.voice = voice;

        utt.onstart = () => {
          if (index === 0 && this.onStart) this.onStart();
          if (this.onProgress) this.onProgress(index + 1, sentences.length);
        };

        utt.onend = () => {
          this.currentIndex = index + 1;
          if (index === sentences.length - 1) {
            this.isSpeaking = false;
            if (this.onComplete) this.onComplete();
            resolve();
          }
        };

        utt.onerror = (e) => {
          if (e.error !== "interrupted" && e.error !== "canceled") {
            console.warn(`[ScreenReader] utterance error on sentence ${index}:`, e.error);
          }
          if (index === sentences.length - 1) {
            this.isSpeaking = false;
            resolve();
          }
        };

        this.utterances.push(utt);
      });

      if (this.utterances.length > 0) {
        this.utterances.forEach((u) => this.synth.speak(u));
      } else {
        this.isSpeaking = false;
        resolve();
      }
    });
  }

  pause()  { if (this.isSpeaking && !this.isPaused) { this.synth.pause();  this.isPaused = true;  } }
  resume() { if (this.isPaused)                      { this.synth.resume(); this.isPaused = false; } }

  stop() {
    this.synth.cancel();
    this.utterances   = [];
    this.currentIndex = 0;
    this.isSpeaking   = false;
    this.isPaused     = false;
  }

  get speaking() { return this.isSpeaking; }
  get paused()   { return this.isPaused;   }
}

// ── Singleton ────────────────────────────────────────────────────────────────
const screenReader = new ScreenReader();

// Chrome loads voices asynchronously — prime the cache when ready
if (typeof window !== "undefined" && window.speechSynthesis) {
  const prime = () => screenReader.getVoices();
  window.speechSynthesis.onvoiceschanged = prime;
  prime();
}

export default screenReader;
export { ScreenReader };