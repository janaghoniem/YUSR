#!/usr/bin/env python3
# aura_engine.py – openWakeWord, overflow-safe (inference on background thread)

import argparse
import json
import os
import queue
import sys
import threading
import time
import subprocess
import numpy as np
import onnxruntime as ort
import collections
import librosa
import psutil

try:
    import sounddevice as sd
except Exception as exc:
    print(json.dumps({"type": "error", "message": f"Failed to import sounddevice: {exc}"}), flush=True)
    sys.stdout.flush()
    raise

SAMPLE_RATE            = 16000
# Large block = short callback = no overflow.
# At 16 kHz mono int16, 8192 samples ≈ 512 ms per callback.
CHUNK_SIZE             = 8192
WAKE_WORD_THRESHOLD    = 0.65
WAKE_COOLDOWN_SECONDS  = 2.5
# After a wake fires, ignore the microphone for this long so the
# "hey aura" utterance itself cannot re-trigger a second wake.
SUPPRESS_AFTER_WAKE_SECONDS = 2.5
ARMED_TIMEOUT_SECONDS  = 20.0

N_MELS         = 16
N_FRAMES       = 96
HOP_LENGTH     = 160
WINDOW_SAMPLES = N_FRAMES * HOP_LENGTH   # 15 360 samples ≈ 0.96 s


# ── helpers ──────────────────────────────────────────────────────────────────

def get_model_path():
    base = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(base, "resources", "hey_mycroft.onnx"),
        os.path.join(base, "resources", "hey_aura.onnx"),
        os.path.join(base, "hey_mycroft.onnx"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    raise FileNotFoundError("No openWakeWord model found. Put a .onnx file in resources/")


def emit(payload: dict):
    """Thread-safe JSON line to stdout (read by Electron main process)."""
    print(json.dumps(payload, ensure_ascii=False), flush=True)


# ── engine ───────────────────────────────────────────────────────────────────

class AuraEngine:
    def __init__(self, lang: str = "en"):
        self.lang = lang

        print("[AURA] Loading openWakeWord ONNX model...", file=sys.stderr)
        model_path = get_model_path()
        self.ort_session = ort.InferenceSession(
            model_path, providers=["CPUExecutionProvider"]
        )
        self.input_name = self.ort_session.get_inputs()[0].name
        print(f"[AURA] Model input: {self.input_name}", file=sys.stderr)

        self.armed           = False
        self.stop_event      = threading.Event()
        # Rolling audio accumulator – capped at one inference window.
        self.audio_buffer    = collections.deque(maxlen=WINDOW_SAMPLES)
        self.last_wake_time  = 0.0
        self.armed_start_time = 0.0
        self.suppress_until  = 0.0

        # ── KEY FIX: inference runs on its own thread ──────────────────────
        # The audio callback just copies samples and enqueues a snapshot.
        # maxsize=1 → if inference is still running when the next window
        # arrives we drop the new window rather than queue it (avoids drift).
        self._infer_queue: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=1)
        self._infer_thread = threading.Thread(
            target=self._inference_worker, daemon=True, name="aura-infer"
        )
        self._infer_thread.start()

    # ── inference worker (runs on its own thread) ─────────────────────────

    def _inference_worker(self):
        """Consume audio windows from the queue and run ONNX inference."""
        while not self.stop_event.is_set():
            try:
                window = self._infer_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self._run_wake_inference(window)
            except Exception as exc:
                print(f"[AURA] Inference error: {exc}", file=sys.stderr)

    def _run_wake_inference(self, audio_chunk: np.ndarray):
        """Full mel + ONNX inference – called only from _inference_worker."""
        if len(audio_chunk) < WINDOW_SAMPLES:
            return
        audio_chunk = audio_chunk[-WINDOW_SAMPLES:]
        audio_float = audio_chunk.astype(np.float32) / 32768.0

        mel = librosa.feature.melspectrogram(
            y=audio_float, sr=SAMPLE_RATE,
            n_mels=N_MELS, n_fft=512, hop_length=HOP_LENGTH, power=2.0,
        )
        log_mel = librosa.power_to_db(mel)

        if log_mel.shape[1] < N_FRAMES:
            pad = N_FRAMES - log_mel.shape[1]
            log_mel = np.pad(log_mel, ((0, 0), (0, pad)), mode="constant")
        else:
            log_mel = log_mel[:, :N_FRAMES]

        input_tensor = log_mel.reshape(1, N_MELS, N_FRAMES).astype(np.float32)
        outputs = self.ort_session.run(None, {self.input_name: input_tensor})
        out = outputs[0]

        if out.ndim == 0:
            score = float(out)
        elif out.ndim == 1:
            score = float(out[0])
        else:
            score = float(out[0][0])

        if score >= WAKE_WORD_THRESHOLD:
            self.handle_wake_detected()

    # ── wake detection ────────────────────────────────────────────────────

    def handle_wake_detected(self):
        now = time.monotonic()
        if now - self.last_wake_time < WAKE_COOLDOWN_SECONDS:
            return
        if self.armed:
            return
        if now < self.suppress_until:
            return

        self.last_wake_time = now
        self.suppress_until = now + SUPPRESS_AFTER_WAKE_SECONDS

        # Clear buffer and inference queue
        self.audio_buffer.clear()
        while not self._infer_queue.empty():
            try:
                self._infer_queue.get_nowait()
            except queue.Empty:
                break

        if not self.is_app_running():
            self.launch_app()
            return

        emit({"type": "wake_word", "text": "hey aura", "lang": self.lang, "action": "trigger"})
        self.armed = True
        self.armed_start_time = now
        emit({"type": "status", "state": "armed", "lang": self.lang})

    def check_armed_timeout(self):
        if self.armed and (time.monotonic() - self.armed_start_time) > ARMED_TIMEOUT_SECONDS:
            self.armed = False
            emit({"type": "status", "state": "listening", "lang": self.lang})

    def disarm(self):
        if self.armed:
            self.armed = False
            emit({"type": "status", "state": "listening", "lang": self.lang})

    # ── app lifecycle helpers ─────────────────────────────────────────────

    def is_app_running(self):
        try:
            for proc in psutil.process_iter(["pid", "name", "cmdline"]):
                name = (proc.info["name"] or "").lower()
                cmdline = " ".join(proc.info.get("cmdline") or []).lower()
                if ("aura" in name or "electron" in name) and (
                    "aura" in cmdline or "electron" in cmdline
                ):
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
            print(
                "[AURA] Development mode: wake word detected, but Electron not running.",
                file=sys.stderr,
            )
            emit({"type": "status", "state": "dev_mode_waiting", "lang": self.lang})

    # ── audio callback (runs on sounddevice's internal thread) ───────────
    # MUST return in << one block period.  Do NOT run inference here.

    def audio_callback(self, indata, frames, time_info, status):
        if status:
            # Only print overflow once per run to avoid flooding stderr.
            print(f"[AURA] Audio status: {status}", file=sys.stderr)

        audio_np = np.frombuffer(bytes(indata), dtype=np.int16)
        self.audio_buffer.extend(audio_np)

        if len(self.audio_buffer) >= WINDOW_SAMPLES:
            # Only submit to the inference thread when not in suppress window.
            if time.monotonic() >= self.suppress_until:
                window = np.array(self.audio_buffer, dtype=np.int16)
                # Non-blocking put: drop frame if inference thread is busy.
                try:
                    self._infer_queue.put_nowait(window)
                except queue.Full:
                    pass  # inference still running – skip this window

            # Slide the accumulator forward by half a window so adjacent
            # windows overlap and we don't miss wake words at block boundaries.
            discard = WINDOW_SAMPLES // 2
            for _ in range(min(len(self.audio_buffer), discard)):
                self.audio_buffer.popleft()

        # Armed timeout check is O(1) – safe inside the callback.
        self.check_armed_timeout()

    # ── stdin command listener ────────────────────────────────────────────

    def cmd_listener(self):
        """Read single-line commands from Electron via stdin."""
        while not self.stop_event.is_set():
            try:
                line = sys.stdin.readline()
                if not line:
                    break
                cmd = line.strip().lower()
                if cmd == "disarm":
                    self.disarm()
            except Exception:
                break

    # ── main loop ─────────────────────────────────────────────────────────

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
    parser = argparse.ArgumentParser(description="AURA sidecar – openWakeWord only")
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