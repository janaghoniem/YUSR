/**
 * ScreenReader - Client-side TTS using Web Speech API SpeechSynthesis.
 * Replaces server-side gTTS with zero-latency browser-native speech.
 * Supports pause/resume/stop, sentence chunking, and progress callbacks.
 */

class ScreenReader {
  constructor() {
    this.synth = window.speechSynthesis;
    this.utterances = [];
    this.currentIndex = 0;
    this.isPaused = false;
    this.isSpeaking = false;
    this.onProgress = null;   // (currentSentence, totalSentences) => void
    this.onComplete = null;   // () => void
    this.onStart = null;      // () => void
    this._voice = null;
    this._rate = 1.0;
    this._pitch = 1.0;
    this._volume = 1.0;
  }

  /**
   * Get available voices, optionally filtered by language
   * @param {string} lang - Language code filter (e.g., 'en', 'ar')
   * @returns {SpeechSynthesisVoice[]}
   */
  getVoices(lang = null) {
    const voices = this.synth.getVoices();
    if (!lang) return voices;
    return voices.filter(v => v.lang.startsWith(lang));
  }

  /**
   * Set the voice to use for speech
   * @param {SpeechSynthesisVoice|string} voice - Voice object or voice name
   */
  setVoice(voice) {
    if (typeof voice === 'string') {
      const voices = this.synth.getVoices();
      this._voice = voices.find(v => v.name === voice) || null;
    } else {
      this._voice = voice;
    }
  }

  /**
   * Configure speech parameters
   * @param {{ rate?: number, pitch?: number, volume?: number }} opts
   */
  configure({ rate, pitch, volume } = {}) {
    if (rate !== undefined) this._rate = Math.max(0.1, Math.min(10, rate));
    if (pitch !== undefined) this._pitch = Math.max(0, Math.min(2, pitch));
    if (volume !== undefined) this._volume = Math.max(0, Math.min(1, volume));
  }

  /**
   * Split text into sentences for chunked speech with progress tracking
   * @param {string} text
   * @returns {string[]}
   */
  _splitIntoSentences(text) {
    if (!text) return [];
    // Split on sentence-ending punctuation, keeping the delimiter
    const sentences = text
      .split(/(?<=[.!?؟،])\s+/)
      .map(s => s.trim())
      .filter(s => s.length > 0);
    
    // If no sentence breaks found, split on commas or chunks of ~100 chars
    if (sentences.length <= 1 && text.length > 150) {
      const chunks = [];
      let remaining = text;
      while (remaining.length > 0) {
        if (remaining.length <= 150) {
          chunks.push(remaining);
          break;
        }
        // Find nearest break point (comma, semicolon, or space near 150 chars)
        let breakPoint = remaining.lastIndexOf(',', 150);
        if (breakPoint < 50) breakPoint = remaining.lastIndexOf(' ', 150);
        if (breakPoint < 50) breakPoint = 150;
        chunks.push(remaining.substring(0, breakPoint + 1).trim());
        remaining = remaining.substring(breakPoint + 1).trim();
      }
      return chunks;
    }
    return sentences;
  }

  /**
   * Detect language of text (basic heuristic for Arabic vs English)
   * @param {string} text
   * @returns {'ar'|'en'}
   */
  _detectLanguage(text) {
    const arabicPattern = /[\u0600-\u06FF\u0750-\u077F]/g;
    const arabicChars = (text.match(arabicPattern) || []).length;
    return arabicChars > text.length * 0.3 ? 'ar' : 'en';
  }

  _selectBestVoice(lang) {
    const voices = this.getVoices(lang);
    if (!voices || voices.length === 0) return null;

    const preferredNames = lang === 'ar'
    ? ['Google Arabic', 'Microsoft Hoda', 'Egypt', 'Arabic']
    : ['Google US English', 'Microsoft Aria', 'Microsoft Jenny', 'Microsoft Zira', 'Samantha', 'Daniel'];

    for (const nameHint of preferredNames) {
      const found = voices.find(v => (v.name || '').toLowerCase().includes(nameHint.toLowerCase()));
      if (found) return found;
    }

    return voices.find(v => v.default) || voices[0] || null;
  }

  /**
   * Speak text with sentence chunking and progress tracking
   * @param {string} text - Text to read aloud
   * @param {{ onProgress?: Function, onComplete?: Function, onStart?: Function }} callbacks
   * @returns {Promise<void>}
   */
  speak(text, { onProgress, onComplete, onStart } = {}) {
    return new Promise((resolve) => {
      // Cancel any ongoing speech
      this.stop();

      if (!text || text.trim().length === 0) {
        resolve();
        return;
      }

      this.onProgress = onProgress || this.onProgress;
      this.onComplete = onComplete || this.onComplete;
      this.onStart = onStart || this.onStart;

      const sentences = this._splitIntoSentences(text);
      this.utterances = [];
      this.currentIndex = 0;
      this.isSpeaking = true;
      this.isPaused = false;

      // Detect language and try to find an appropriate voice
      const lang = this._detectLanguage(text);
      let voice = this._voice;
      if (!voice) {
        voice = this._selectBestVoice(lang);
      }

      // Create utterances for each sentence
      sentences.forEach((sentence, index) => {
        const utterance = new SpeechSynthesisUtterance(sentence);
        utterance.rate = this._rate;
        utterance.pitch = this._pitch;
        utterance.volume = this._volume;
        if (voice) utterance.voice = voice;
        utterance.lang = lang === 'ar' ? 'ar-EG' : 'en-US';

        utterance.onstart = () => {
          if (index === 0 && this.onStart) this.onStart();
          if (this.onProgress) this.onProgress(index + 1, sentences.length);
        };

        utterance.onend = () => {
          this.currentIndex = index + 1;
          if (index === sentences.length - 1) {
            this.isSpeaking = false;
            if (this.onComplete) this.onComplete();
            resolve();
          }
        };

        utterance.onerror = (e) => {
          if (e.error !== 'interrupted' && e.error !== 'canceled') {
            console.warn(`ScreenReader error on sentence ${index}:`, e.error);
          }
          // IMPORTANT: Even if interrupted, if we are the last utterance OR getting interrupted entirely,
          // we must ensure the promise sequence resolves.
          if (index === sentences.length - 1) {
            this.isSpeaking = false;
            // Note: If deliberate stop was called, we rely on the NEW speech calling its own onComplete.
            resolve();
          }
        };

        this.utterances.push(utterance);
      });

      // Start speaking first sentence
      if (this.utterances.length > 0) {
        // Chrome requires user gesture for first speech - queue all
        this.utterances.forEach(u => this.synth.speak(u));
      } else {
        this.isSpeaking = false;
        resolve();
      }
    });
  }

  /**
   * Pause speech
   */
  pause() {
    if (this.isSpeaking && !this.isPaused) {
      this.synth.pause();
      this.isPaused = true;
    }
  }

  /**
   * Resume paused speech
   */
  resume() {
    if (this.isPaused) {
      this.synth.resume();
      this.isPaused = false;
    }
  }

  /**
   * Stop all speech immediately
   */
  stop() {
    this.synth.cancel();
    this.utterances = [];
    this.currentIndex = 0;
    this.isSpeaking = false;
    this.isPaused = false;
  }

  /**
   * Check if currently speaking
   * @returns {boolean}
   */
  get speaking() {
    return this.isSpeaking;
  }

  /**
   * Check if paused
   * @returns {boolean}
   */
  get paused() {
    return this.isPaused;
  }
}

// Singleton instance
const screenReader = new ScreenReader();

// Load voices when available (Chrome loads them async)
if (typeof window !== 'undefined' && window.speechSynthesis) {
  window.speechSynthesis.onvoiceschanged = () => {
    screenReader.getVoices(); // trigger cache
  };
}

export default screenReader;
export { ScreenReader };
