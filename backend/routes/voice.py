import base64
import io
import logging
import tempfile
import audioop
import wave
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from gtts import gTTS

from core.dependencies import genai_client, logger

router = APIRouter()

try:
    import webrtcvad
except Exception:  # pragma: no cover - optional dependency
    webrtcvad = None


MIME_TO_EXTENSION = {
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/wave": ".wav",
    "audio/webm": ".webm",
    "audio/ogg": ".ogg",
    "audio/opus": ".opus",
    "audio/mp3": ".mp3",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/aac": ".aac",
    "audio/flac": ".flac",
}


def _normalize_audio_base64(audio_data: str) -> str:
    """Accept raw base64 or data URLs and return raw base64 payload."""
    if not audio_data:
        return ""
    payload = audio_data.strip()
    if payload.startswith("data:") and "," in payload:
        payload = payload.split(",", 1)[1]
    return payload


def _guess_audio_suffix(mime_type: str | None) -> str:
    if not mime_type:
        return ".wav"
    return MIME_TO_EXTENSION.get(mime_type.lower().strip(), ".wav")


def _extract_pcm16_mono_16k(wav_bytes: bytes) -> bytes | None:
    """Convert WAV bytes to mono 16-bit PCM @ 16kHz for speech gating."""
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            channels = wf.getnchannels()
            sample_width = wf.getsampwidth()
            sample_rate = wf.getframerate()
            pcm = wf.readframes(wf.getnframes())
    except Exception:
        return None

    if not pcm:
        return None

    try:
        if channels > 1:
            pcm = audioop.tomono(pcm, sample_width, 0.5, 0.5)
            channels = 1
        if sample_width != 2:
            pcm = audioop.lin2lin(pcm, sample_width, 2)
            sample_width = 2
        if sample_rate != 16000:
            pcm, _ = audioop.ratecv(pcm, 2, channels, sample_rate, 16000, None)
        return pcm
    except Exception:
        return None


def _contains_human_speech(wav_bytes: bytes) -> bool:
    """Detect speech to avoid sending silent audio to Gemini STT."""
    pcm = _extract_pcm16_mono_16k(wav_bytes)
    if not pcm:
        return False

    frame_bytes = 960  # 30ms @16kHz mono 16-bit PCM
    frames = [pcm[i:i + frame_bytes] for i in range(0, len(pcm), frame_bytes)]
    frames = [frame for frame in frames if len(frame) == frame_bytes]
    if not frames:
        return False

    if webrtcvad is not None:
        try:
            vad = webrtcvad.Vad(1)
            voiced = sum(1 for frame in frames if vad.is_speech(frame, 16000))
            return voiced >= 2
        except Exception:
            pass

    voiced = 0
    for frame in frames:
        if audioop.rms(frame, 2) >= 200:
            voiced += 1
            if voiced >= 2:
                return True
    return False


@router.post("/text-to-speech")
async def text_to_speech(request: Request):
    """
    Convert text to speech using neural voices when available.

    ✅ Supports both Arabic and English
    ✅ Auto-detects language
    ✅ Always works (no API quota issues)
    ✅ Returns base64-encoded audio
    """
    try:
        logger.info("🔊 Received TTS request")

        data = await request.json()
        text = data.get("text", "").strip()
        lang = data.get("lang", None)

        if not text:
            logger.error("❌ No text provided for TTS")
            raise HTTPException(status_code=400, detail="Missing 'text' field")

        logger.info(f"🗣️ Generating speech for: '{text[:50]}...'")

        arabic_chars = sum(1 for ch in text if '\u0600' <= ch <= '\u06FF')
        total_chars = max(len(text.replace(' ', '')), 1)
        arabic_ratio = arabic_chars / total_chars

        if arabic_ratio > 0.15:
            lang = 'ar'
        elif arabic_ratio == 0.0:
            lang = 'en'
        else:
            hint = (lang or 'en').lower().strip()
            lang = 'ar' if hint.startswith('ar') else 'en'

        # Google TTS only: prefer Egyptian domain for Arabic and US domain for English.
        tld = 'com.eg' if lang == 'ar' else 'us'
        logger.info(
            f"🌐 TTS language: {lang} "
            f"(arabic_ratio={arabic_ratio:.2f}, caller_hint={data.get('lang', 'none')})"
        )

        provider = "google_gtts"

        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp_audio:
            tmp_path = tmp_audio.name

        try:
            tts = gTTS(text=text, lang=lang, tld=tld, slow=False)
            tts.save(tmp_path)

            with open(tmp_path, "rb") as audio_file:
                audio_bytes = audio_file.read()

            logger.info(f"✅ Generated {len(audio_bytes)} bytes of audio")
            base64_audio = base64.b64encode(audio_bytes).decode('utf-8')
            logger.info(f"✅ Encoded {len(base64_audio)} base64 characters")

            return {
                "status": "success",
                "audio_data": base64_audio,
                "format": "mp3",
                "language": lang,
                "provider": provider,
            }

        finally:
            try:
                Path(tmp_path).unlink()
                logger.info(f"🗑️ Cleaned up temp file: {tmp_path}")
            except Exception as exc:
                logger.warning(f"⚠️ Failed to delete temp file: {exc}")

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"❌ TTS error: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"TTS failed: {str(exc)}")


@router.post("/transcribe")
async def transcribe_audio(request: Request):
    """
    Transcribe audio using Google Gemini with file upload.
    Supports bilingual Arabic/English transcription.
    Accepts base64-encoded audio bytes and optional audio MIME type.
    """
    try:
        logger.info("🎤 Received audio transcription request")

        if not genai_client:
            logger.error("❌ Google Gemini client not initialized")
            raise HTTPException(status_code=500, detail="Transcription service not available - GEMINI_API_KEY not set")

        data = await request.json()
        audio_data = _normalize_audio_base64(data.get("audio_data", ""))
        audio_mime_type = data.get("audio_mime_type")
        session_id = data.get("session_id", "default")

        if not audio_data:
            raise HTTPException(status_code=400, detail="Missing 'audio_data'")

        try:
            audio_bytes = base64.b64decode(audio_data, validate=False)
        except Exception as exc:
            logger.error(f"❌ Invalid base64 audio payload: {exc}")
            raise HTTPException(status_code=400, detail="Invalid 'audio_data' base64 payload")

        logger.info(f"📦 Received {len(audio_bytes)} bytes of audio data")
        if audio_mime_type:
            logger.info(f"🎧 Audio MIME type from client: {audio_mime_type}")

        if _guess_audio_suffix(audio_mime_type) == ".wav":
            if not _contains_human_speech(audio_bytes):
                logger.info("🔇 Speech gate rejected empty/silent audio")
                return {
                    "status": "success",
                    "transcript": "Couldn't catch that. Please try again.",
                    "session_id": session_id,
                }

        with tempfile.NamedTemporaryFile(delete=False, suffix=_guess_audio_suffix(audio_mime_type)) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name

        try:
            logger.info(f"📤 Uploading audio file to Gemini: {tmp_path}")
            uploaded_file = genai_client.files.upload(file=tmp_path)
            logger.info(f"✅ File uploaded with URI: {uploaded_file.uri}")

            prompt = """
You are a speech transcription system designed for bilingual users (Arabic and English).

Your task:
1. Accurately transcribe the audio exactly as spoken.
2. The speech may contain a mix of Arabic and English words.
3. If the speaker mentions a command such as "open calculator", "open WhatsApp", "افتح calculator", etc.:
   - Detect the app or command name, even if the rest of the sentence is Arabic.
   - Keep the **app or command name in English**, exactly as said (for example: "افتح calculator").
4. Do NOT translate any Arabic words.
5. Do NOT invent or guess missing words.
6. If the audio is silent, unclear, or contains no recognizable speech, respond exactly with:
   "Couldn't catch that. Please try again."

Formatting rules:
- Return ONLY the final transcript text, nothing else.
- No punctuation or additional commentary.
- Keep mixed-language sentences exactly as spoken.

Examples:
Arabic only → "افتح الكاميرا"
Mixed → "افتح calculator"
English → "open calendar"
Silent → "Couldn't catch that. Please try again."
"""

            logger.info("🔄 Sending to Gemini for transcription...")
            response = genai_client.models.generate_content(
                model="gemini-2.5-flash",
                contents=[prompt, uploaded_file]
            )

            transcript = response.text.strip() if response.text else ""

            if not transcript or len(transcript) < 2:
                logger.warning("⚠️ Empty or silent audio detected")
                transcript = "Couldn't catch that. Please try again."

            logger.info(f"📝 TRANSCRIBED TEXT: '{transcript}'")

            return {"status": "success", "transcript": transcript, "session_id": session_id}

        finally:
            try:
                Path(tmp_path).unlink()
                logger.info(f"🗑️ Cleaned up temp file: {tmp_path}")
            except Exception as exc:
                logger.warning(f"⚠️ Failed to delete temp file: {exc}")

    except Exception as exc:
        logger.error(f"❌ Transcription error: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Transcription failed: {str(exc)}")