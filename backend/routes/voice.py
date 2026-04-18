import base64
import logging
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from gtts import gTTS

from core.dependencies import genai_client, logger

router = APIRouter()


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

        tts = gTTS(text=text, lang=lang, tld=tld, slow=False)
        tts.save(tmp_path)

        try:
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
    Accepts base64-encoded audio bytes.
    """
    try:
        logger.info("🎤 Received audio transcription request")

        if not genai_client:
            logger.error("❌ Google Gemini client not initialized")
            raise HTTPException(status_code=500, detail="Transcription service not available - GEMINI_API_KEY not set")

        data = await request.json()
        audio_data = data.get("audio_data", "")
        session_id = data.get("session_id", "default")

        if not audio_data:
            raise HTTPException(status_code=400, detail="Missing 'audio_data'")

        audio_bytes = base64.b64decode(audio_data)
        logger.info(f"📦 Received {len(audio_bytes)} bytes of audio data")

        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
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
