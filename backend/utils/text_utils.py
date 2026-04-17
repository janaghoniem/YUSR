import re

INTERRUPT_COMMANDS = {
    # English
    "aura stop": "stop", "aura cancel": "stop", "aura abort": "stop",
    "aura pause": "pause", "aura wait": "pause", "aura hold on": "pause",
    "aura continue": "resume", "aura go on": "resume", "aura resume": "resume",
    "aura undo": "undo", "aura go back": "undo",
    "aura redo": "retry", "aura try again": "retry",
    # Arabic
    "أورا وقف": "stop", "أورا توقف": "stop", "أورا إيقاف": "stop", "أورا إلغاء": "stop",
    "أورا انتظر": "pause", "أورا لحظة": "pause",
    "أورا استمر": "resume", "أورا كمل": "resume",
    "أورا تراجع": "undo", "أورا ارجع": "undo",
    "أورا أعد": "retry", "أورا حاول مجددا": "retry",
}


def detect_language_from_text(text: str) -> str:
    """Detect Arabic vs English from actual input characters."""
    if not text:
        return "en"
    arabic_chars = sum(1 for ch in text if "\u0600" <= ch <= "\u06FF")
    ratio = arabic_chars / max(len(text.replace(" ", "")), 1)
    return "ar" if ratio > 0.15 else "en"


def normalize_arabic(text: str) -> str:
    """Normalize Arabic text for matching and lightweight comparisons."""
    if not text:
        return ""
    text = re.sub(r"[إأآا]", "ا", text)
    text = re.sub(r"ى", "ي", text)
    text = re.sub(r"ة", "ه", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def detect_interrupt(text: str):
    """Detect interrupt command in text (case-insensitive, supports prefix match)."""
    text_lower = (text or "").strip().lower()
    if not text_lower:
        return None

    if text_lower in INTERRUPT_COMMANDS:
        return INTERRUPT_COMMANDS[text_lower]

    for cmd, action in INTERRUPT_COMMANDS.items():
        if text_lower.startswith(cmd):
            return action
    return None
