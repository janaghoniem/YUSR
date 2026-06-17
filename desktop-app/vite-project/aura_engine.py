#!/usr/bin/env python3
# aura_engine.py – Uses openwakeword library with automatic model download if missing

import argparse
import json
import os
import queue
import sys
import threading
import time
import subprocess
import numpy as np
import psutil

try:
    import openwakeword
    from openwakeword.model import Model
    from openwakeword.utils import download_models
except ImportError:
    print(json.dumps({"type": "error", "message": "openwakeword not installed. Run: pip install openwakeword"}), flush=True)
    sys.exit(1)

try:
    import sounddevice as sd
except Exception as exc:
    print(json.dumps({"type": "error", "message": f"Failed to import sounddevice: {exc}"}), flush=True)
    sys.stdout.flush()
    raise

# ─── Configuration ──────────────────────────────────────────────────────────
SAMPLE_RATE = 16000
CHUNK_SIZE  = 1280                     # 80 ms at 16 kHz (optimal for openwakeword)
WAKE_WORD_THRESHOLD = 0.7              # Default is 0.5; raise to reduce false positives
WAKE_COOLDOWN_SECONDS = 3.0
SUPPRESS_AFTER_WAKE_SECONDS = 5.0
ARMED_TIMEOUT_SECONDS = 30.0

# Energy gate – skip inference on silence
RMS_THRESHOLD = 25.0

_OWN_PID    = os.getpid()
_PARENT_PID = os.getppid()

# ── helpers ──────────────────────────────────────────────────────────────────

def get_model_path():
    base = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(base, "resources", "hey_mycroft.onnx"),
        os.path.join(base, "resources", "hey_aura.onnx"),
        os.path.join(base, "hey_mycroft.onnx"),
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    raise FileNotFoundError("No openWakeWord model found. Put a .onnx file in resources/")

def emit(payload: dict):
    print(json.dumps(payload, ensure_ascii=False), flush=True)

# ── Engine ──────────────────────────────────────────────────────────────────

class AuraEngine:
    def __init__(self, lang: str = "en"):
        self.lang = lang
        self.armed = False
        self.stop_event = threading.Event()
        self.last_wake_time = 0.0
        self.armed_start_time = float('inf')
        self.suppress_until = 0.0
        self.js_recording = False

        # Diagnostic
        self._cb_count = 0
        self._last_diag_time = time.monotonic()

        # ── Ensure the melspectrogram model exists; download if missing ──
        base_dir = os.path.dirname(openwakeword.__file__)
        mel_path = os.path.join(base_dir, "resources", "models", "melspectrogram.onnx")
        if not os.path.exists(mel_path):
            print("[AURA] Missing melspectrogram.onnx – downloading all default models...", file=sys.stderr)
            try:
                download_models()  # This downloads melspectrogram.onnx and other base models
                print("[AURA] Default models downloaded successfully.", file=sys.stderr)
            except Exception as e:
                print(f"[AURA] Failed to download models: {e}", file=sys.stderr)
                sys.exit(1)

        # ── Load wake‑word model using openwakeword ──
        model_path = get_model_path()
        print(f"[AURA] Loading openwakeword model from {model_path}", file=sys.stderr)
        self.model = Model(wakeword_models=[model_path])
        # Store the model key for safe access
        self.model_key = list(self.model.models.keys())
        if not self.model_key:
            raise ValueError("No models loaded!")
        print(f"[AURA] Model key: {self.model_key[0]}", file=sys.stderr)

        # ── Audio queue for processing ──
        self._audio_queue: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=8)
        self._worker_thread = threading.Thread(
            target=self._process_worker, daemon=True, name="aura-process"
        )
        self._worker_thread.start()

    def _process_worker(self):
        """Continuously process audio chunks with openwakeword."""
        while not self.stop_event.is_set():
            try:
                chunk = self._audio_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self._process_chunk(chunk)
            except Exception as e:
                print(f"[AURA] Process error: {e}", file=sys.stderr)

    def _process_chunk(self, audio_chunk: np.ndarray):
        """Run inference on a single chunk."""
        # Energy gate
        rms = float(np.sqrt(np.mean(audio_chunk.astype(np.float32) ** 2)))
        if rms < RMS_THRESHOLD:
            return  # too quiet – skip

        # Predict
        prediction = self.model.predict(audio_chunk)

        # Pull the score using the safe model_key we stored during __init__
        score = prediction.get(self.model_key[0], 0.0)

        # Log active scores over 0.05 to see how well it hears you
        if score > 0.05:
            print(f"[AURA-DBG] score={score:.3f} rms={rms:.1f}", file=sys.stderr)

        if score >= WAKE_WORD_THRESHOLD:
            self.handle_wake_detected()

    def handle_wake_detected(self):
        now = time.monotonic()
        if now - self.last_wake_time < WAKE_COOLDOWN_SECONDS:
            return
        if self.armed:
            return
        if now < self.suppress_until:
            return
        if self.js_recording:
            return

        self.last_wake_time = now
        self.suppress_until = now + SUPPRESS_AFTER_WAKE_SECONDS
        self._flush_audio()

        # Check if the main app is already running
        if not self.is_app_running():
            self.launch_app()
            return

        emit({"type": "wake_word", "text": "hey aura", "lang": self.lang, "action": "trigger"})
        self.armed = True
        self.armed_start_time = now
        emit({"type": "status", "state": "armed", "lang": self.lang})

        # ── Reset the model's internal buffer to avoid immediate re‑trigger ──
        self.model.reset()

    def _flush_audio(self):
        """Clear the audio queue."""
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except queue.Empty:
                break

    def check_armed_timeout(self):
        if self.armed and (time.monotonic() - self.armed_start_time) > ARMED_TIMEOUT_SECONDS:
            self.disarm()

    def disarm(self):
        if self.armed:
            self.armed = False
            self.armed_start_time = float('inf')
            self.suppress_until = time.monotonic() + SUPPRESS_AFTER_WAKE_SECONDS
            self._flush_audio()
            emit({"type": "status", "state": "listening", "lang": self.lang})

    # ── App lifecycle ──────────────────────────────────────────────────────────

    def is_app_running(self):
        excluded = {_OWN_PID, _PARENT_PID}
        try:
            for proc in psutil.process_iter(["pid", "name", "cmdline"]):
                pid = proc.info.get("pid")
                if pid in excluded:
                    continue
                name = (proc.info.get("name") or "").lower()
                cmdline = " ".join(proc.info.get("cmdline") or []).lower()
                if ("electron" in name or "aura" in name) and (
                    "electron" in cmdline or "aura" in cmdline
                ) and "python" not in name and "node" not in name:
                    return True
        except Exception:
            pass
        return False

    def launch_app(self):
        if getattr(sys, "frozen", False):
            base = os.path.dirname(sys.executable)
            exe = os.path.join(base, "AURA.exe")
            if not os.path.exists(exe):
                exe = os.path.join(base, "AURA.app", "Contents", "MacOS", "AURA")
            if os.path.exists(exe):
                subprocess.Popen([exe], shell=False)
                emit({"type": "status", "state": "launching_app", "lang": self.lang})
            else:
                emit({"type": "error", "message": f"App executable not found at {exe}"})
        else:
            print("[AURA] Dev mode: Electron not detected; emitting wake_word.", file=sys.stderr)
            emit({"type": "wake_word", "text": "hey aura", "lang": self.lang, "action": "trigger"})
            self.armed = True
            self.armed_start_time = time.monotonic()
            emit({"type": "status", "state": "armed", "lang": self.lang})

    # ─── Audio callback ──────────────────────────────────────────────────────

    def audio_callback(self, indata, frames, time_info, status):
        if status:
            print(f"[AURA] Audio status: {status}", file=sys.stderr)

        audio_np = np.frombuffer(bytes(indata), dtype=np.int16)
        self._cb_count += 1

        # ── Diagnostic every 10 seconds ──
        now_diag = time.monotonic()
        if now_diag - self._last_diag_time >= 10.0:
            rms = float(np.sqrt(np.mean(audio_np.astype(np.float32) ** 2)))
            qsize = self._audio_queue.qsize()
            print(
                f"[AURA-DIAG] callbacks={self._cb_count} "
                f"queue_size={qsize} "
                f"rms={rms:.1f} "
                f"armed={self.armed} suppress_remaining={max(0.0, self.suppress_until - now_diag):.1f}s",
                file=sys.stderr,
            )
            self._cb_count = 0
            self._last_diag_time = now_diag

        # ── Only enqueue if not suppressed and not recording ──
        if time.monotonic() < self.suppress_until or self.js_recording:
            return

        try:
            self._audio_queue.put_nowait(audio_np)
        except queue.Full:
            # Drop chunk if queue is full – worker is busy
            pass

        self.check_armed_timeout()

    # ── stdin command listener ──────────────────────────────────────────────

    def cmd_listener(self):
        while not self.stop_event.is_set():
            try:
                line = sys.stdin.readline()
                if not line:
                    break
                cmd = line.strip().lower()
                if cmd == "disarm":
                    self.disarm()
                elif cmd == "recording_start":
                    self.js_recording = True
                    self._flush_audio()
                elif cmd == "recording_end":
                    self.js_recording = False
                    self.disarm()
            except Exception:
                break

    # ── main loop ────────────────────────────────────────────────────────────

    def run_continuous(self):
        threading.Thread(target=self.cmd_listener, daemon=True, name="aura-cmd").start()
        emit({"type": "status", "state": "listening", "lang": self.lang})
        try:
            with sd.RawInputStream(
                samplerate=SAMPLE_RATE,
                blocksize=CHUNK_SIZE,
                dtype="int16",
                channels=1,
                callback=self.audio_callback,
            ):
                while not self.stop_event.is_set():
                    sd.sleep(200)
        except Exception as exc:
            emit({"type": "error", "message": f"Microphone error: {exc}"})
            raise

# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(description="AURA sidecar – openWakeWord")
    parser.add_argument("--lang", choices=["ar", "en"], default="en")
    return parser.parse_args()

def main():
    args = parse_args()
    engine = AuraEngine(lang=args.lang)
    engine.run_continuous()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        emit({"type": "status", "state": "stopped"})
    except Exception as exc:
        emit({"type": "error", "message": str(exc)})
        raise