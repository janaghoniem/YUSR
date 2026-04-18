# aura_engine.py (fixed with VAD)
import argparse
import json
import os
import sys
import threading
import time

try:
    import sounddevice as sd
    from vosk import KaldiRecognizer, Model, SetLogLevel
except Exception as exc:
    print(json.dumps({"type": "error", "message": f"Failed to import audio dependencies: {exc}"}), flush=True)
    sys.stdout.flush()
    raise

# VAD import – optional, but highly recommended
try:
    import webrtcvad
    VAD_AVAILABLE = True
except ImportError:
    VAD_AVAILABLE = False
    print(json.dumps({"type": "warning", "message": "webrtcvad not installed, voice activity detection disabled"}), flush=True)

SetLogLevel(-1)

SAMPLE_RATE = 16000
EN_WAKE_PHRASES = [
    "hey aura",
]
MODEL_CACHE = {}


def emit(payload):
    print(json.dumps(payload, ensure_ascii=False), flush=True)
    sys.stdout.flush()


def base_dir():
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def resolve_model_path(lang: str) -> str:
    root = base_dir()
    candidates = []
    if lang == "en":
        candidates.extend([
            os.path.join(root, "resources", "vosk-model-small-en-us-0.15"),
            os.path.join(root, "resources", "vosk-model-en"),
            os.path.join(root, "vosk-model-small-en-us-0.15"),
            os.path.join(root, "vosk-model-en"),
        ])
    else:
        candidates.extend([
            os.path.join(root, "resources", "vosk-model-ar-mgb2-0.4"),
            os.path.join(root, "resources", "vosk-model-ar"),
            os.path.join(root, "vosk-model-ar-mgb2-0.4"),
            os.path.join(root, "vosk-model-ar"),
        ])

    for candidate in candidates:
        if os.path.isdir(candidate):
            return candidate
    raise FileNotFoundError(f"Could not find a Vosk model for lang={lang}. Checked: {candidates}")


def load_model(lang: str) -> Model:
    normalized = lang if lang in ("ar", "en") else "en"
    if normalized not in MODEL_CACHE:
        MODEL_CACHE[normalized] = Model(resolve_model_path(normalized))
    return MODEL_CACHE[normalized]


class AuraEngine:
    def __init__(self, lang: str = "ar"):
        self.preferred_lang = lang if lang in ("ar", "en") else "en"
        self.models = {}
        for candidate_lang in (self.preferred_lang, "ar" if self.preferred_lang == "en" else "en"):
            try:
                self.models[candidate_lang] = load_model(candidate_lang)
            except FileNotFoundError:
                continue

        if not self.models:
            raise FileNotFoundError("Could not find any Vosk models for wake-word listening")

        self.command_lang = self.preferred_lang if self.preferred_lang in self.models else next(iter(self.models))
        self.wake_lang = "en"
        self.wake_recognizers = self._build_wake_recognizers()
        self.command_recognizer = None
        self.armed = False
        self.active_lang = self.command_lang
        self.command_from_wake_enabled = False
        self.stop_event = threading.Event()
        self.last_partial_emit_at = 0.0
        self.last_partial_text = ""
        self.command_fragments = []
        self.last_command_activity_at = 0.0
        self.last_command_partial = ""
        self.command_silence_seconds = 5.0
        self.last_wake_emit_at = 0.0
        self.wake_cooldown_seconds = 1.4

        # VAD setup
        self.vad = None
        if VAD_AVAILABLE:
            # Higher aggressiveness reduces false triggers on noise.
            self.vad = webrtcvad.Vad(3)

    def _is_speech(self, audio_bytes: bytes) -> bool:
        """Return True if the audio chunk contains human speech using WebRTC VAD."""
        if self.vad is None:
            return True  # VAD disabled, assume speech always present

        # VAD expects 10, 20, or 30ms frames. At 16kHz, 30ms = 480 samples = 960 bytes.
        frame_duration_ms = 30
        frame_size_bytes = int(SAMPLE_RATE * frame_duration_ms / 1000) * 2  # 2 bytes per sample (int16)
        if len(audio_bytes) < frame_size_bytes:
            return False

        # Check each 30ms frame; if any frame contains speech, consider the whole block as speech.
        for offset in range(0, len(audio_bytes) - frame_size_bytes + 1, frame_size_bytes):
            frame = audio_bytes[offset:offset + frame_size_bytes]
            try:
                if self.vad.is_speech(frame, SAMPLE_RATE):
                    return True
            except Exception:
                # In case of malformed frame, skip
                continue
        return False

    def emit_partial_throttled(self, text: str, lang: str, min_interval: float = 0.25):
        text = (text or "").strip()
        if not text:
            return

        now = time.monotonic()
        # Avoid flooding stdout/IPC with near-duplicate partials from every callback frame.
        if text == self.last_partial_text and (now - self.last_partial_emit_at) < min_interval:
            return

        self.last_partial_text = text
        self.last_partial_emit_at = now
        emit({"type": "partial", "text": text, "lang": lang})

    def _build_wake_recognizers(self):
        recognizers = {}
        model = self.models.get(self.wake_lang)
        if model is None:
            return recognizers
        # Do NOT use grammar-constrained wake recognition here.
        # Grammar mode can over-trigger by forcing close phrases into the grammar.
        recognizer = KaldiRecognizer(model, SAMPLE_RATE)
        recognizers[self.wake_lang] = recognizer
        return recognizers

    @staticmethod
    def _normalize_text(text: str) -> str:
        if not text:
            return ""
        normalized = (text or "").strip().lower()
        normalized = normalized.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
        normalized = normalized.replace("ة", "ه").replace("ى", "ي")
        for ch in ["،", ".", ",", "!", "؟", "?", "\"", "'", "؛", "-"]:
            normalized = normalized.replace(ch, " ")
        return " ".join(normalized.split())

    def _is_wake_match(self, text: str, lang: str) -> bool:
        if lang != "en":
            return False
        normalized = self._normalize_text(text)
        if not normalized:
            return False

        phrases = EN_WAKE_PHRASES
        normalized_phrases = [self._normalize_text(phrase) for phrase in phrases]
        # Require wake phrase at utterance start (or exact match) to avoid accidental
        # triggers when the words appear inside unrelated speech.
        return any(
            normalized == phrase or normalized.startswith(f"{phrase} ")
            for phrase in normalized_phrases
            if phrase
        )

    def reset_command_recognizer(self):
        self.command_lang = self.active_lang if self.active_lang in self.models else self.command_lang
        model = self.models[self.command_lang]
        self.command_recognizer = KaldiRecognizer(model, SAMPLE_RATE)
        self.command_recognizer.SetWords(True)
        self.command_fragments = []
        self.last_command_activity_at = time.monotonic()
        self.last_command_partial = ""

    def reset_wake_recognizers(self):
        self.wake_recognizers = self._build_wake_recognizers()

    def handle_wake_result(self, text: str, lang: str):
        normalized = self._normalize_text(text)
        if self._is_wake_match(normalized, lang):
            now = time.monotonic()
            if (now - self.last_wake_emit_at) < self.wake_cooldown_seconds:
                return
            self.last_wake_emit_at = now
            self.active_lang = lang if lang in self.models else self.command_lang
            emit({"type": "wake_word", "text": normalized, "lang": self.active_lang})
            self.armed = False
            emit({"type": "status", "state": "armed", "lang": self.active_lang, "text": normalized})

    def handle_command_result(self, text: str):
        text = (text or "").strip()
        if text:
            emit({"type": "final", "text": text, "lang": self.command_lang})
        self.armed = False
        self.command_recognizer = None
        self.active_lang = self.command_lang
        self.reset_wake_recognizers()
        emit({"type": "status", "state": "listening", "lang": self.command_lang})

    def _flush_command_on_silence(self):
        if not self.armed:
            return

        now = time.monotonic()
        if self.last_command_activity_at <= 0:
            return

        if (now - self.last_command_activity_at) < self.command_silence_seconds:
            return

        combined = " ".join(self.command_fragments).strip()
        if not combined and self.last_command_partial:
            combined = self.last_command_partial.strip()

        if combined:
            self.handle_command_result(combined)

    def callback(self, indata, frames, time_info, status):
        if status and not getattr(status, "input_overflow", False):
            print(status, file=sys.stderr)

        audio_bytes = bytes(indata)

        # VAD gate: skip this block if no speech is detected
        if not self._is_speech(audio_bytes):
            return

        try:
            if self.command_from_wake_enabled and self.armed:
                if self.command_recognizer is None:
                    self.reset_command_recognizer()

                if self.command_recognizer.AcceptWaveform(audio_bytes):
                    result = json.loads(self.command_recognizer.Result())
                    final_text = (result.get("text") or "").strip()
                    if final_text:
                        self.command_fragments.append(final_text)
                        self.last_command_partial = ""
                        self.last_command_activity_at = time.monotonic()
                else:
                    partial = json.loads(self.command_recognizer.PartialResult())
                    partial_text = (partial.get("partial") or "").strip()
                    if partial_text:
                        self.last_command_partial = partial_text
                        self.last_command_activity_at = time.monotonic()
                        self.emit_partial_throttled(partial_text, self.command_lang)

                self._flush_command_on_silence()
            else:
                best_partial = None
                for lang, recognizer in self.wake_recognizers.items():
                    if recognizer.AcceptWaveform(audio_bytes):
                        result = json.loads(recognizer.Result())
                        self.handle_wake_result(result.get("text", ""), lang)
                        return

                    partial = json.loads(recognizer.PartialResult())
                    partial_text = (partial.get("partial") or "").strip()
                    if partial_text:
                        if best_partial is None or len(partial_text) > len(best_partial[1]):
                            best_partial = (lang, partial_text)

                if best_partial:
                    self.emit_partial_throttled(best_partial[1], best_partial[0])
        except Exception as exc:
            emit({"type": "error", "message": str(exc)})
            self.stop_event.set()

    def run_continuous(self):
        if not self.wake_recognizers:
            emit({
                "type": "error",
                "message": "English wake-word model is required but was not found",
            })
            raise RuntimeError("English wake-word model is required")

        emit({"type": "status", "state": "listening", "lang": self.command_lang})

        try:
            # Use callback mode so wake-word and command recognizers share one audio pipeline.
            with sd.RawInputStream(
                samplerate=SAMPLE_RATE,
                blocksize=8000,
                dtype="int16",
                channels=1,
                latency="high",
                callback=self.callback,
            ):
                while not self.stop_event.is_set():
                    sd.sleep(200)
        except Exception as exc:
            emit({"type": "error", "message": f"Microphone Error: {str(exc)}"})
            raise

    def run_once(self, timeout_ms: int = 10000):
        recognizer = KaldiRecognizer(self.models[self.command_lang], SAMPLE_RATE)
        recognizer.SetWords(True)
        deadline = time.monotonic() + max(timeout_ms, 1000) / 1000.0
        final_text = ""

        emit({"type": "status", "state": "listening_once", "lang": self.command_lang})

        def once_callback(indata, frames, time_info, status):
            nonlocal final_text
            if status and not getattr(status, "input_overflow", False):
                print(status, file=sys.stderr)

            audio_bytes = bytes(indata)
            # VAD gate for one‑shot mode as well
            if not self._is_speech(audio_bytes):
                return

            try:
                if recognizer.AcceptWaveform(audio_bytes):
                    result = json.loads(recognizer.Result())
                    final_text = (result.get("text") or "").strip()
                    if final_text:
                        emit({"type": "final", "text": final_text, "lang": self.command_lang})
                        self.stop_event.set()
                else:
                    partial = json.loads(recognizer.PartialResult())
                    if partial.get("partial"):
                        self.emit_partial_throttled(partial["partial"], self.command_lang)
            except Exception as exc:
                emit({"type": "error", "message": str(exc)})
                self.stop_event.set()

        try:
            with sd.RawInputStream(
                samplerate=SAMPLE_RATE,
                blocksize=8000,
                dtype="int16",
                channels=1,
                callback=once_callback,
            ):
                while not self.stop_event.is_set() and time.monotonic() < deadline:
                    sd.sleep(200)
        except Exception as exc:
            emit({"type": "error", "message": str(exc)})
            raise

        if not final_text:
            emit({"type": "error", "message": "No speech detected"})
            return None
        return final_text


def parse_args():
    parser = argparse.ArgumentParser(description="AURA Vosk sidecar")
    parser.add_argument("--lang", choices=["ar", "en"], default="en")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--timeout-ms", type=int, default=10000)
    return parser.parse_args()


def main():
    args = parse_args()
    engine = AuraEngine(lang=args.lang)

    if args.once:
        engine.stop_event.clear()
        engine.run_once(timeout_ms=args.timeout_ms)
        emit({"type": "status", "state": "stopped", "mode": "once", "lang": engine.command_lang})
        return

    engine.stop_event.clear()
    engine.run_continuous()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        emit({"type": "status", "state": "stopped"})
    except Exception as exc:
        emit({"type": "error", "message": str(exc)})
        raise