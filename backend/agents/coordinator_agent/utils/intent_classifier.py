"""
FIX 3 — Corrected Intent Classifier (drop-in replacement for intent_classifier.py)
=====================================================================================
Root cause of "I didn't quite understand. Could you clarify?":

The original classifier was not recognising:
  • "give me top 5 results when searching for lion king on youtube"
  → should be  mode=API, target_agent=email, operation=youtube_search

The request contains NO navigation verb ("go to", "open", etc.) so it must
default to API mode — but the old regex patterns were too narrow and missed
multi-word phrasings like "give me top N results … on youtube".

This file is a COMPLETE replacement for:
    agents/coordinator_agent/utils/intent_classifier.py

Just copy it over (or import get_routing_decision from here in coordinator_agent.py).
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


# ═══════════════════════════════════════════════════════════════════════════════
# EXECUTION MODE ENUM
# ═══════════════════════════════════════════════════════════════════════════════

class ExecutionMode(str, Enum):
    API          = "api"          # Pure API call — no browser
    BROWSER      = "browser"      # User wants to SEE a UI
    HYBRID       = "hybrid"       # API first, then browser interaction
    API_FALLBACK = "api_fallback"  # Ambiguous — safe default to API


# ═══════════════════════════════════════════════════════════════════════════════
# STRICT BROWSER NAVIGATION TRIGGERS
# Only these patterns should open a real browser window.
# ═══════════════════════════════════════════════════════════════════════════════

_BROWSER_PATTERNS: List[re.Pattern] = [
    re.compile(r"\b(go\s+to|navigate\s+to|open\s+up|take\s+me\s+to|visit|browse\s+to)\b", re.I),
    # "open youtube" but NOT "open youtube and search" (hybrid handled separately)
    re.compile(r"\bopen\s+(youtube|google|facebook|instagram|twitter|gmail|drive|calendar)\s*$", re.I),
    re.compile(r"\b(show\s+(it|them|me)\s+(visually|in\s+the\s+browser|on\s+screen))\b", re.I),
    re.compile(r"\blet\s+me\s+browse\b", re.I),
    re.compile(r"\bi\s+want\s+to\s+see\s+it\s+myself\b", re.I),
]

# Words that look like navigation but are NOT (they are API/task intent)
_FALSE_BROWSER_WORDS = re.compile(
    r"\b(watch|play|listen|stream|read|check|find|get|give|fetch|show\s+me|tell\s+me|what|list|search\s+for|look\s+up)\b",
    re.I,
)


# ═══════════════════════════════════════════════════════════════════════════════
# HYBRID PATTERNS — API step followed by browser interaction
# ═══════════════════════════════════════════════════════════════════════════════

_HYBRID_PATTERNS: List[re.Pattern] = [
    # "search X and play/open the first result"
    re.compile(r"\b(search|find|look\s+up).+\b(play|open|click|watch)\b.*(first|top|result)", re.I),
    # "find restaurants and show on map"
    re.compile(r"\bfind\b.+\bshow\b.+\bmap\b", re.I),
    # "get results and navigate to the first one"
    re.compile(r"\b(get|fetch|find)\b.+\b(navigate|go\s+to)\b", re.I),
]


# ═══════════════════════════════════════════════════════════════════════════════
# API OPERATION DETECTORS
# ═══════════════════════════════════════════════════════════════════════════════

# ── YouTube ──────────────────────────────────────────────────────────────────
_YT_SEARCH_PATTERNS: List[re.Pattern] = [
    # Explicit YouTube mentions
    re.compile(r"\b(search|find|look\s+up|give\s+me|show\s+me|get)\b.{0,60}\byoutube\b", re.I),
    re.compile(r"\byoutube\b.{0,60}\b(search|results?|videos?|find|look\s+up)\b", re.I),
    re.compile(r"\b(top|best|first)\s+\d+\b.{0,40}\byoutube\b", re.I),
    re.compile(r"\byoutube\b.{0,40}\b(top|best|first)\s+\d+\b", re.I),
    re.compile(r"\b(results?\s+(?:when\s+)?(?:searching?\s+)?(?:for\s+)?)\b.{0,60}\byoutube\b", re.I),
    re.compile(r"\bsearch\s+(?:youtube|yt)\b", re.I),
    re.compile(r"\byoutube\s+search\b", re.I),
    # Generic video/movie search (no "youtube" required)
    re.compile(r"\b(search|find|look\s+up)\b.{0,40}\b(movies?|videos?|films?|clips?|trailers?|episodes?|songs?)\b", re.I),
    re.compile(r"\b(top|best|first)\s+\d+\s+.{0,40}\b(results?|movies?|videos?)\b", re.I),
    re.compile(r"\b(search\s+for|find\s+me|give\s+me|show\s+me)\b.{0,60}\b(top|best|first)\s+\d+\s+(results?)\b", re.I),
]

_YT_VIDEO_INFO_PATTERNS: List[re.Pattern] = [
    re.compile(r"(youtube\.com|youtu\.be)/", re.I),
    re.compile(r"\b(video\s+info|video\s+details|how\s+many\s+views|statistics\s+(?:of|for|on)\s+(?:a|this|the)?\s*video)\b", re.I),
]

# ── Open Nth result from previous YouTube search ──────────────────────────────
_OPEN_NTH_RESULT_PATTERNS: List[re.Pattern] = [
    # "open the first video", "play the second result", "watch the third one"
    re.compile(r"\b(open|play|watch|click|go\s+to)\s+(?:the\s+)?(first|second|third|fourth|fifth|1st|2nd|3rd|4th|5th|last)\b", re.I),
    # "open result number 2", "play video number 3"
    re.compile(r"\b(open|play|watch|click)\s+(?:result|video)?\s*(?:number|#|no\.?)\s*(\d+)\b", re.I),
    # "open number 2", "play #3"
    re.compile(r"\b(open|play|watch)\s+#?(\d+)\b", re.I),
]

# ── Play / Open / Watch a video (browser) ─────────────────────────────────────
_PLAY_VIDEO_PATTERNS: List[re.Pattern] = [
    # "play the barbie princess charm one", "open the lion king video"
    re.compile(r"\b(play|watch|open|stream)\b.{0,60}\b(video|movie|clip|episode|song|trailer|one|it)\b", re.I),
    # "play barbie princess charm school"  (but NOT "open google and search for X")
    re.compile(r"\b(play|watch|stream)\s+(?:the\s+)?(?:one\s+(?:named|called|titled)\s+)?(.{3,60})$", re.I),
]

# ── Gmail / Email ─────────────────────────────────────────────────────────────
_EMAIL_SEND_PATTERNS: List[re.Pattern] = [
    re.compile(r"\b(send|compose|draft|write)\s+(?:an?\s+)?(?:email|mail|message)\b", re.I),
    re.compile(r"\bemail\b.+\bto\b.+@", re.I),
]
_EMAIL_READ_PATTERNS: List[re.Pattern] = [
    re.compile(r"\b(read|check|show|get|fetch|who\s+(?:was|is)\s+my\s+last)\b.{0,40}\b(emails?|mail|inbox|messages?)\b", re.I),
    re.compile(r"\b(latest|recent|unread|new)\s+(emails?|mail|messages?)\b", re.I),
    re.compile(r"\bwhat\s+did\s+(?:my\s+last|the\s+last)\s+email\b", re.I),
    re.compile(r"\bmy\s+(latest|recent|unread|new|last)\s+(emails?|mail|messages?)\b", re.I),
]

# ── Calendar ──────────────────────────────────────────────────────────────────
_CAL_CREATE_PATTERNS: List[re.Pattern] = [
    re.compile(r"\b(create|add|schedule|set\s+up|make)\b.{0,40}\b(event|meeting|appointment|reminder|calendar)\b", re.I),
]
_CAL_LIST_PATTERNS: List[re.Pattern] = [
    re.compile(r"\b(list|show|get|what\s+are|check)\b.{0,40}\b(events?|meetings?|appointments?|calendar)\b", re.I),
    re.compile(r"\bmy\s+(upcoming|schedule|calendar|events?)\b", re.I),
]

# ── Drive ─────────────────────────────────────────────────────────────────────
_DRIVE_UPLOAD_PATTERNS: List[re.Pattern] = [
    re.compile(r"\b(upload|save\s+to|add\s+to)\b.{0,30}\b(drive|google\s+drive)\b", re.I),
]
_DRIVE_LIST_PATTERNS: List[re.Pattern] = [
    re.compile(r"\b(list|show|get)\b.{0,30}\b(files?|drive|google\s+drive)\b", re.I),
    re.compile(r"\bmy\s+(drive\s+files?|files?\s+on\s+drive|google\s+drive)\b", re.I),
]

# ── Google Search (browser) ────────────────────────────────────────────────────
_GOOGLE_SEARCH_PATTERNS: List[re.Pattern] = [
    re.compile(r"\b(search|find|look\s+up|give\s+me|show\s+me|get)\b.{0,60}\bon\s+google\b", re.I),
    re.compile(r"\bgoogle\b.{0,60}\b(search|results?|find|look\s+up)\b", re.I),
    re.compile(r"\b(top|best|first)\s+\d+\b.{0,40}\bgoogle\b", re.I),
    re.compile(r"\bsearch\s+google\b", re.I),
    re.compile(r"\bgoogle\s+search\b", re.I),
    re.compile(r"\bgoogle\b.{0,30}\bfor\b", re.I),
]

# ── OTP / Magic Links ──────────────────────────────────────────────────────────
_OTP_PATTERNS: List[re.Pattern] = [
    re.compile(r"\b(otp|verification\s+code|one[- ]time\s+(?:password|code))\b", re.I),
]
_MAGIC_LINK_PATTERNS: List[re.Pattern] = [
    re.compile(r"\b(magic\s+link|verification\s+link|confirm(?:ation)?\s+link)\b", re.I),
]


# ═══════════════════════════════════════════════════════════════════════════════
# HELPER UTILITIES
# ═══════════════════════════════════════════════════════════════════════════════

def _matches_any(text: str, patterns: List[re.Pattern]) -> bool:
    return any(p.search(text) for p in patterns)


def _extract_url(text: str) -> Optional[str]:
    m = re.search(r"https?://[^\s'\"<>]+", text)
    return m.group(0) if m else None


def _extract_navigation_target(text: str) -> Optional[str]:
    """
    Extract an explicit URL or well-known domain from navigation requests.
    Returns a full URL string or None.
    """
    url = _extract_url(text)
    if url:
        return url

    # Known domains the user might say "go to X"
    _DOMAIN_MAP: Dict[str, str] = {
        "google":    "https://www.google.com",
        "youtube":   "https://www.youtube.com",
        "gmail":     "https://mail.google.com",
        "facebook":  "https://www.facebook.com",
        "instagram": "https://www.instagram.com",
        "twitter":   "https://www.twitter.com",
        "x.com":     "https://www.x.com",
        "amazon":    "https://www.amazon.com",
        "netflix":   "https://www.netflix.com",
        "linkedin":  "https://www.linkedin.com",
        "reddit":    "https://www.reddit.com",
        "drive":     "https://drive.google.com",
        "calendar":  "https://calendar.google.com",
    }
    for keyword, url in _DOMAIN_MAP.items():
        if re.search(rf"\b{re.escape(keyword)}\b", text, re.I):
            return url
    return None


def _extract_google_query(text: str) -> str:
    """Pull the search query out of a Google-search-related sentence."""
    cleaned = re.sub(r"\bon\s+google\b|\bgoogle\b", "", text, flags=re.I).strip()
    # Reuse the same extraction logic as YouTube
    for pat in [
        r"searc\w*\s+for\s+(.+)",
        r"(?:find|look\s+up|about|of)\s+(.+)",
        r"results?\s+(?:when\s+)?\S*\s*(?:for|of|about)\s+(.+)",
        r"top\s+\d+\s+(?:results?\s+)?(?:when\s+)?(?:\S+\s+)?(?:for\s+)?(.+)",
        r"(?:search|find)\s+(.+)",
    ]:
        m = re.search(pat, cleaned, re.I)
        if m:
            q = m.group(1).strip().rstrip(".,!?")
            q = re.sub(r"^(?:results?\s+)?(?:when\s+)?\S*\s*(?:for|of|about)\s+", "", q, flags=re.I).strip()
            # Strip trailing filler like "and give me top 3 results"
            q = re.sub(r"\s+and\s+(give|show|get|tell)\b.*$", "", q, flags=re.I).strip()
            if q:
                return q
    fallback = re.sub(
        r"\b(give|me|the|top|best|first|\d+|results?|when|you|for|get|show|find|search\w*|and)\b",
        "", cleaned, flags=re.I
    ).strip().rstrip(".,!?")
    fallback = re.sub(r"\s+", " ", fallback).strip()
    return fallback if fallback else cleaned.strip().rstrip(".,!?")


def _extract_youtube_query(text: str) -> str:
    """Pull the search query out of a YouTube-related sentence."""
    # Remove the "on youtube / youtube" part first
    cleaned = re.sub(r"\bon\s+youtube\b|\byoutube\b", "", text, flags=re.I).strip()

    # Ordered patterns — most specific first
    for pat in [
        # "searching for X", "search for X" (tolerates typos like "searcging")
        r"searc\w*\s+for\s+(.+)",
        r"(?:find|look\s+up|about|of)\s+(.+)",
        # "results for X", "results when ... for X"
        r"results?\s+(?:when\s+)?\S*\s*(?:for|of|about)\s+(.+)",
        # "top 5 X" — capture after the number, stripping filler words
        r"top\s+\d+\s+(?:results?\s+)?(?:when\s+)?(?:\S+\s+)?(?:for\s+)?(.+)",
        r"(?:search|find)\s+(.+)",
    ]:
        m = re.search(pat, cleaned, re.I)
        if m:
            q = m.group(1).strip().rstrip(".,!?")
            # Remove leading filler like "results when searcging for"
            q = re.sub(r"^(?:results?\s+)?(?:when\s+)?\S*\s*(?:for|of|about)\s+", "", q, flags=re.I).strip()
            # Remove trailing filler like "and give me top 4 results" or "and give top 3 results"
            q = re.sub(r"\s+and\s+(?:give|show|get|tell)(?:\s+me)?.*$", "", q, flags=re.I).strip()
            q = re.sub(r"\s+(?:top|best|first)\s+\d+\s*(?:results?)?$", "", q, flags=re.I).strip()
            if q:
                return q

    # Strip common filler words from the fallback
    fallback = re.sub(
        r"\b(give|me|the|top|best|first|\d+|results?|when|you|for|get|show|find|search\w*)\b",
        "", cleaned, flags=re.I
    ).strip().rstrip(".,!?")
    fallback = re.sub(r"\s+", " ", fallback).strip()
    return fallback if fallback else cleaned.strip().rstrip(".,!?")


def _extract_max_results(text: str, default: int = 5) -> int:
    m = re.search(r"\b(\d+)\b", text)
    if m:
        n = int(m.group(1))
        return max(1, min(n, 50))
    for word, val in {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                      "ten": 10, "twenty": 20}.items():
        if re.search(rf"\b{word}\b", text, re.I):
            return val
    return default


_ORDINAL_MAP = {
    "first": 0, "second": 1, "third": 2, "fourth": 3, "fifth": 4,
    "1st": 0, "2nd": 1, "3rd": 2, "4th": 3, "5th": 4, "last": -1,
}

def _extract_result_index(text: str) -> Optional[int]:
    """Return a 0-based index from 'open the first/second/3rd/number 2' style requests."""
    lower = text.lower()
    for word, idx in _ORDINAL_MAP.items():
        if re.search(rf"\b{re.escape(word)}\b", lower):
            return idx
    m = re.search(r"(?:number|#|no\.?)\s*(\d+)", lower)
    if m:
        return int(m.group(1)) - 1
    m = re.search(r"\b(open|play|watch)\s+#?(\d+)\b", lower)
    if m:
        return int(m.group(2)) - 1
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN ROUTING FUNCTION
# ═══════════════════════════════════════════════════════════════════════════════

def get_routing_decision(user_request: str) -> Dict[str, Any]:
    """
    Deterministic intent classification.

    Priority order (as per project spec):
        1. HYBRID  (explicit API + browser follow-up)
        2. BROWSER (strict navigation verbs only)
        3. API     (any task-oriented request)
        4. API_FALLBACK (truly ambiguous)

    Returns a dict consumed by coordinator_agent.decompose_task_to_actions().
    """
    text = (user_request or "").strip()
    if not text:
        return _fallback("Empty request")

    lower = text.lower()

    # ── 1. HYBRID ─────────────────────────────────────────────────────────────
    if _matches_any(text, _HYBRID_PATTERNS):
        # Determine the API phase
        if _matches_any(text, _YT_SEARCH_PATTERNS):
            query = _extract_youtube_query(text)
            return {
                "mode":         ExecutionMode.HYBRID,
                "phase_1": {
                    "target_agent": "email",
                    "operation":    "youtube_search",
                    "query":        query,
                    "max_results":  _extract_max_results(text, 5),
                },
                "phase_2": {
                    "target_agent":    "action",
                    "context":         "web",
                    "platform_target": "youtube",
                },
                "confidence": 0.85,
                "reasoning":  "Hybrid: YouTube search API then browser playback",
            }
        # Generic hybrid fallback
        return {
            "mode":       ExecutionMode.HYBRID,
            "confidence": 0.70,
            "reasoning":  "Hybrid: API fetch then browser interaction",
        }

    # ── 2. BROWSER (strict) ───────────────────────────────────────────────────
    has_nav_verb = _matches_any(text, _BROWSER_PATTERNS)
    has_api_verb = _FALSE_BROWSER_WORDS.search(text)

    if has_nav_verb and not has_api_verb:
        nav_target = _extract_navigation_target(text)
        return {
            "mode":             ExecutionMode.BROWSER,
            "target_agent":     "action",
            "context":          "web",
            "navigation_target": nav_target or "https://www.google.com",
            "requires_oauth":   True,
            "inject_cookies":   True,
            "confidence":       0.90,
            "reasoning":        f"Explicit browser navigation to {nav_target}",
        }

    # Edge case: "go to google and search for X" is browser navigation + search
    if has_nav_verb and has_api_verb:
        nav_target = _extract_navigation_target(text)
        if nav_target:
            # If the target is Google and user wants to search, build direct search URL
            search_query = None
            if 'google' in (nav_target or '').lower():
                search_query = _extract_google_query(text)
                if search_query:
                    nav_target = f"https://www.google.com/search?q={search_query.replace(' ', '+')}"
            return {
                "mode":              ExecutionMode.BROWSER,
                "target_agent":      "action",
                "context":           "web",
                "navigation_target": nav_target,
                "search_query":      search_query,
                "requires_oauth":    False,
                "inject_cookies":    True,
                "confidence":        0.85,
                "reasoning":         f"Navigation + search: direct to {nav_target}",
            }

    # ── 3. API ────────────────────────────────────────────────────────────────

    # YouTube search  (most common miss — fix the order: check BEFORE video info)
    if _matches_any(text, _YT_SEARCH_PATTERNS):
        query        = _extract_youtube_query(text)
        max_results  = _extract_max_results(text, 5)
        return {
            "mode":          ExecutionMode.API,
            "target_agent":  "email",
            "operation":     "youtube_search",
            "query":         query,
            "max_results":   max_results,
            "context":       "local",
            "requires_oauth": True,
            "confidence":    0.92,
            "reasoning":     f"YouTube search API for: '{query}' (max {max_results})",
        }

    # YouTube video info
    if _matches_any(text, _YT_VIDEO_INFO_PATTERNS):
        video_url = _extract_url(text) or ""
        return {
            "mode":          ExecutionMode.API,
            "target_agent":  "email",
            "operation":     "youtube_video_info",
            "video_url":     video_url,
            "context":       "local",
            "requires_oauth": True,
            "confidence":    0.88,
            "reasoning":     "YouTube video info API",
        }

    # Email — send
    if _matches_any(text, _EMAIL_SEND_PATTERNS):
        return {
            "mode":          ExecutionMode.API,
            "target_agent":  "email",
            "operation":     "send",
            "context":       "local",
            "requires_oauth": True,
            "confidence":    0.93,
            "reasoning":     "Gmail send API",
        }

    # Email — read
    if _matches_any(text, _EMAIL_READ_PATTERNS):
        return {
            "mode":          ExecutionMode.API,
            "target_agent":  "email",
            "operation":     "read",
            "context":       "local",
            "requires_oauth": True,
            "confidence":    0.90,
            "reasoning":     "Gmail read API",
        }

    # OTP extraction
    if _matches_any(text, _OTP_PATTERNS):
        return {
            "mode":          ExecutionMode.API,
            "target_agent":  "email",
            "operation":     "extract_otp",
            "context":       "local",
            "requires_oauth": True,
            "confidence":    0.88,
            "reasoning":     "Gmail OTP extraction",
        }

    # Magic link extraction
    if _matches_any(text, _MAGIC_LINK_PATTERNS):
        return {
            "mode":          ExecutionMode.API,
            "target_agent":  "email",
            "operation":     "extract_links",
            "context":       "local",
            "requires_oauth": True,
            "confidence":    0.87,
            "reasoning":     "Gmail magic link extraction",
        }

    # Calendar — create
    if _matches_any(text, _CAL_CREATE_PATTERNS):
        return {
            "mode":          ExecutionMode.API,
            "target_agent":  "email",
            "operation":     "calendar_create",
            "context":       "local",
            "requires_oauth": True,
            "confidence":    0.89,
            "reasoning":     "Google Calendar create event API",
        }

    # Calendar — list
    if _matches_any(text, _CAL_LIST_PATTERNS):
        return {
            "mode":          ExecutionMode.API,
            "target_agent":  "email",
            "operation":     "calendar_list",
            "context":       "local",
            "requires_oauth": True,
            "confidence":    0.89,
            "reasoning":     "Google Calendar list events API",
        }

    # Drive — upload
    if _matches_any(text, _DRIVE_UPLOAD_PATTERNS):
        return {
            "mode":          ExecutionMode.API,
            "target_agent":  "email",
            "operation":     "drive_upload",
            "context":       "local",
            "requires_oauth": True,
            "confidence":    0.87,
            "reasoning":     "Google Drive upload API",
        }

    # Drive — list
    if _matches_any(text, _DRIVE_LIST_PATTERNS):
        return {
            "mode":          ExecutionMode.API,
            "target_agent":  "email",
            "operation":     "drive_list",
            "context":       "local",
            "requires_oauth": True,
            "confidence":    0.87,
            "reasoning":     "Google Drive list API",
        }

    # Open Nth result from previous YouTube/search results → OPEN_NTH_RESULT
    # The coordinator will resolve the actual URL from cached results
    if _matches_any(text, _OPEN_NTH_RESULT_PATTERNS):
        idx = _extract_result_index(text)
        return {
            "mode":           ExecutionMode.BROWSER,
            "target_agent":   "action",
            "context":        "web",
            "operation":      "open_nth_result",
            "result_index":   idx if idx is not None else 0,
            "requires_oauth": False,
            "inject_cookies": True,
            "confidence":     0.90,
            "reasoning":      f"Open result #{(idx or 0) + 1} from cached search results",
        }

    # Google Search → browser/web (Playwright), NOT desktop
    # NOTE: Must be checked BEFORE play-video patterns, because "open google
    #        and search for X" would otherwise match the broad "open <anything>"
    #        video pattern and route to YouTube instead of Google.
    if _matches_any(text, _GOOGLE_SEARCH_PATTERNS):
        query = _extract_google_query(text)
        max_results = _extract_max_results(text, 5)
        nav_target = f"https://www.google.com/search?q={query.replace(' ', '+')}"
        return {
            "mode":              ExecutionMode.BROWSER,
            "target_agent":      "action",
            "context":           "web",
            "navigation_target": nav_target,
            "search_query":      query,
            "max_results":       max_results,
            "requires_oauth":    False,
            "inject_cookies":    True,
            "confidence":        0.88,
            "reasoning":         f"Google web search via Playwright for: '{query}'",
        }

    # Play / Watch / Open a video → browser (Playwright opens YouTube URL)
    if _matches_any(text, _PLAY_VIDEO_PATTERNS):
        # Extract what the user wants to play
        play_query = re.sub(r"\b(play|watch|open|stream|the|one|named|called|titled|it)\b", "", text, flags=re.I).strip()
        play_query = re.sub(r"\s+", " ", play_query).strip().rstrip(".,!?")
        if not play_query:
            play_query = text
        # Build a YouTube search URL as the target
        nav_target = f"https://www.youtube.com/results?search_query={play_query.replace(' ', '+')}"
        return {
            "mode":              ExecutionMode.BROWSER,
            "target_agent":      "action",
            "context":           "web",
            "navigation_target": nav_target,
            "search_query":      play_query,
            "requires_oauth":    False,
            "inject_cookies":    True,
            "confidence":        0.82,
            "reasoning":         f"Play/watch video via Playwright: '{play_query}'",
        }

    # ── 4. GENERIC SEARCH FALLBACK (browser) ──────────────────────────────────
    # Any remaining "search for X" / "find X" / "look up X" → browser search
    _generic_search = re.compile(r"\b(search\s+for|find\s+me|look\s+up|give\s+me\s+results)\b", re.I)
    if _generic_search.search(text):
        query = re.sub(r"\b(search\s+for|find\s+me|look\s+up|give\s+me\s+results|and\s+give\s+me|top\s+\d+\s+results?)\b", "", text, flags=re.I).strip()
        query = re.sub(r"\s+", " ", query).strip().rstrip(".,!?")
        if not query:
            query = text
        nav_target = f"https://www.google.com/search?q={query.replace(' ', '+')}"
        return {
            "mode":              ExecutionMode.BROWSER,
            "target_agent":      "action",
            "context":           "web",
            "navigation_target": nav_target,
            "search_query":      query,
            "requires_oauth":    False,
            "inject_cookies":    True,
            "confidence":        0.60,
            "reasoning":         f"Generic search fallback via browser: '{query}'",
        }

    # ── 5. API_FALLBACK ───────────────────────────────────────────────────────
    return _fallback(f"No specific pattern matched for: '{text[:60]}'")


def _fallback(reason: str) -> Dict[str, Any]:
    return {
        "mode":         ExecutionMode.API_FALLBACK,
        "target_agent": "action",
        "context":      "local",
        "confidence":   0.40,
        "reasoning":    reason,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# QUICK SELF-TEST  (run: python fix_3_intent_classifier.py)
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    test_cases = [
        # Should be API youtube_search
        ("give me top 5 results when searching for lion king on youtube", "youtube_search"),
        ("search youtube for cat videos",                                 "youtube_search"),
        ("top 3 youtube results for blush makeup",                        "youtube_search"),
        ("find videos about python programming on youtube",               "youtube_search"),

        # Should be BROWSER
        ("go to google",                                                  "browser"),
        ("navigate to youtube.com",                                       "browser"),
        ("open google",                                                   "browser"),

        # Should be HYBRID
        ("search youtube for cats and play the first video",              "hybrid"),

        # Should be API email
        ("send an email to test@example.com",                             "send"),
        ("who was my last email from and what did it say",                "read"),

        # Should be API calendar
        ("create a meeting for tomorrow at 3pm",                          "calendar_create"),
        ("list my upcoming events",                                       "calendar_list"),
    ]

    print(f"{'REQUEST':<60} {'EXPECTED':<20} {'RESULT':<25} {'OK'}")
    print("─" * 115)
    all_pass = True
    for request, expected_key in test_cases:
        decision = get_routing_decision(request)
        mode      = decision.get("mode", "")
        operation = decision.get("operation", "")
        result_key = operation if operation else str(mode).replace("ExecutionMode.", "")
        ok = (
            expected_key in result_key
            or expected_key == str(mode).split(".")[-1].lower()
        )
        if not ok:
            all_pass = False
        icon = "✅" if ok else "❌"
        print(f"{request[:58]:<60} {expected_key:<20} {result_key:<25} {icon}")
        if not ok:
            print(f"    Full decision: {decision}")
    print("\n" + ("✅ All tests passed!" if all_pass else "❌ Some tests failed — check above."))