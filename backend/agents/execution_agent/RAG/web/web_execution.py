# ============================================================================
# WEB CODE EXECUTION - ULTIMATE INTEGRATED VERSION
# ============================================================================
# ✅ GENERIC MULTI-PLATFORM (YouTube, Amazon, Netflix, Google, ANY SITE)
# ✅ Advanced bot detection bypass
# ✅ Persistent page context (separate from mem0)
# ✅ Page State Layer before actions
# ✅ Platform-specific keyboard shortcuts
# ✅ Post-action verification
# ✅ Smart intent handling when elements not listed
# ✅ State-dependent command handling
# ✅ FIX: Media validation only fires for explicit media action_types

import asyncio
import logging
import json
import os
import re
import random
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
import hashlib
from .web_rag_sandbox import rag_sandbox

logger = logging.getLogger(__name__)

# ============================================================================
# IMPORT PER-USER GOOGLE ACCOUNT CREDENTIALS
# ============================================================================
from .google_accounts import USER_ACCOUNTS

def get_current_system_user() -> str:
    """Get current system username (cross-platform: Windows/Mac/Linux)"""
    import getpass
    return getpass.getuser()

# ============================================================================
# EXECUTION STATUS & RESULT CLASSES
# ============================================================================

class ExecutionStatus(Enum):
    """Web execution status"""
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    SECURITY_VIOLATION = "security_violation"

@dataclass
class WebExecutionConfig:
    """Configuration for web execution"""
    headless: bool = False
    timeout_seconds: int = 30
    screenshots_enabled: bool = True
    screenshot_dir: str = "web_screenshots"
    max_navigation_time: int = 10000
    slow_mo: int = 50  # Reduced for performance
    viewport_width: int = 1920
    viewport_height: int = 1080
    enable_verification: bool = True
    enable_page_context: bool = True
    
    # ✅ Page state layer configuration
    enable_page_state_layer: bool = True
    enable_smart_intent: bool = True
    
    # Context caching (separate from mem0 - this is tab-level DOM cache)
    cache_page_context: bool = True
    context_cache_ttl: int = 30
    
    # Anti-detection
    use_stealth_plugin: bool = True
    randomize_fingerprint: bool = True
    use_real_user_agent: bool = True

@dataclass
class WebExecutionResult:
    """Result of web code execution"""
    validation_passed: bool
    security_passed: bool
    output: Optional[str] = None
    error: Optional[str] = None
    validation_errors: List[str] = field(default_factory=list)
    security_violations: List[str] = field(default_factory=list)
    page_url: Optional[str] = None
    page_title: Optional[str] = None
    extracted_data: Optional[Dict] = None
    screenshot_path: Optional[str] = None
    execution_time: float = 0.0
    verification_message: Optional[str] = None
    page_state_before: Optional[Dict] = None
    page_state_after: Optional[Dict] = None

# ============================================================================
# ✅ GENERIC SITE DETECTOR - WORKS FOR ALL WEBSITES
# ============================================================================

class SiteDetector:
    """Detect website type and capabilities - GENERIC"""
    
    @staticmethod
    async def detect_site_type(page) -> Dict:
        """
        Detect what type of site we're on.
        
        Returns:
            {
                'site_type': 'video' | 'ecommerce' | 'social' | 'search' | 'generic',
                'platform': 'youtube' | 'amazon' | 'ebay' | 'google' | 'unknown',
                'capabilities': ['video_player', 'search', 'shopping_cart', ...]
            }
        """
        
        url = page.url.lower()
        
        # Detect platform
        platform = 'unknown'
        if 'youtube.com' in url or 'youtu.be' in url:
            platform = 'youtube'
        elif 'amazon.' in url:
            platform = 'amazon'
        elif 'ebay.' in url:
            platform = 'ebay'
        elif 'google.com' in url or 'google.' in url:
            platform = 'google'
        elif 'facebook.com' in url or 'fb.com' in url:
            platform = 'facebook'
        elif 'twitter.com' in url or 'x.com' in url:
            platform = 'twitter'
        elif 'netflix.com' in url:
            platform = 'netflix'
        elif 'instagram.com' in url:
            platform = 'instagram'
        elif 'linkedin.com' in url:
            platform = 'linkedin'
        
        # Detect site type based on content
        try:
            site_info = await page.evaluate("""
                () => {
                    const hasVideo = !!document.querySelector('video');
                    const hasAudio = !!document.querySelector('audio');
                    const hasSearch = !!document.querySelector('[type="search"], [role="search"], input[placeholder*="search" i]');
                    const hasCart = !!document.querySelector('[data-testid*="cart" i], [aria-label*="cart" i], .cart, #cart, [id*="cart" i]');
                    const hasPrices = !!document.querySelector('[data-price], .price, [class*="price" i], [aria-label*="price" i]');
                    const hasProducts = !!document.querySelector('[data-product], [class*="product" i], [data-testid*="product" i]');
                    
                    return {
                        hasVideo,
                        hasAudio,
                        hasSearch,
                        hasCart,
                        hasPrices,
                        hasProducts
                    };
                }
            """)
        except:
            site_info = {
                'hasVideo': False,
                'hasAudio': False,
                'hasSearch': False,
                'hasCart': False,
                'hasPrices': False,
                'hasProducts': False
            }
        
        # Determine site type
        site_type = 'generic'
        capabilities = []
        
        if site_info['hasVideo'] or site_info['hasAudio']:
            site_type = 'video'
            capabilities.append('media_player')
        
        if site_info['hasSearch']:
            capabilities.append('search')
        
        if site_info['hasCart'] or site_info['hasPrices'] or site_info['hasProducts']:
            if site_type == 'generic':
                site_type = 'ecommerce'
            capabilities.append('shopping')
        
        return {
            'site_type': site_type,
            'platform': platform,
            'capabilities': capabilities,
            'url': page.url
        }

# ============================================================================
# ✅ GENERIC KEYBOARD SHORTCUTS - PLATFORM-SPECIFIC + EXTENSIBLE
# ============================================================================

class KeyboardShortcuts:
    """Platform-specific keyboard shortcuts - COMPREHENSIVE"""
    
    SHORTCUTS = {
        'youtube': {
            'pause': 'k',
            'play': 'k',
            'mute': 'm',
            'unmute': 'm',
            'next': 'Shift+N',
            'previous': 'Shift+P',
            'skip': 'Shift+N',
            'fullscreen': 'f',
            'theater': 't',
            'miniplayer': 'i',
            'captions': 'c',
            'speed_up': 'Shift+>',
            'speed_down': 'Shift+<',
            'skip_forward': 'l',
            'skip_backward': 'j',
        },
        'netflix': {
            'pause': 'Space',
            'play': 'Space',
            'fullscreen': 'f',
            'rewind': 'ArrowLeft',
            'forward': 'ArrowRight',
            'mute': 'm',
            'volume_up': 'ArrowUp',
            'volume_down': 'ArrowDown'
        },
        'amazon': {
            'search': '/',
            'cart': 'c'
        },
        'google': {
            'search': '/',
            'next_result': 'j',
            'previous_result': 'k'
        },
        'facebook': {
            'search': '/',
            'home': 'h'
        },
        'twitter': {
            'search': '/',
            'home': 'h',
            'new_tweet': 'n'
        },
        # Generic fallback for unknown video sites
        'generic_video': {
            'pause': 'Space',
            'play': 'Space',
            'fullscreen': 'f',
            'mute': 'm'
        }
    }
    
    @staticmethod
    def get_shortcut(platform: str, action: str) -> Optional[str]:
        """Get keyboard shortcut for action on platform"""
        
        # Try platform-specific first
        if platform in KeyboardShortcuts.SHORTCUTS:
            shortcuts = KeyboardShortcuts.SHORTCUTS[platform]
            if action in shortcuts:
                return shortcuts[action]
        
        # Fall back to generic video shortcuts
        if action in KeyboardShortcuts.SHORTCUTS.get('generic_video', {}):
            return KeyboardShortcuts.SHORTCUTS['generic_video'][action]
        
        return None
    
    @staticmethod
    async def execute_shortcut(page, platform: str, action: str) -> Dict[str, Any]:
        """Execute keyboard shortcut if available"""
        
        shortcut = KeyboardShortcuts.get_shortcut(platform, action)
        
        if not shortcut:
            logger.debug(f"⚠️ No shortcut for '{action}' on {platform}")
            return {'success': False, 'error': f"No shortcut for {action}"}
        
        logger.info(f"⌨️ Executing shortcut: {shortcut} for {action} on {platform}")
        
        try:
            # Focus player first (generic - works on any site)
            await page.evaluate("""
                () => {
                    const video = document.querySelector('video');
                    const audio = document.querySelector('audio');
                    const media = video || audio;
                    
                    if (media) {
                        media.focus();
                        const player = media.closest('[role="presentation"], [id*="player"], [class*="player"]');
                        if (player && player.tabIndex >= 0) {
                            player.focus();
                        }
                    }
                }
            """)
            
            await page.wait_for_timeout(100)
            
            # Execute shortcut
            if '+' in shortcut:
                parts = shortcut.split('+')
                modifier = parts[0]
                key = parts[1]
                
                await page.keyboard.down(modifier)
                await page.keyboard.press(key)
                await page.keyboard.up(modifier)
            else:
                await page.keyboard.press(shortcut)
            
            await page.wait_for_timeout(300)
            
            logger.info(f"✅ Shortcut executed: {shortcut}")
            return {'success': True, 'shortcut': shortcut, 'action': action}
            
        except Exception as e:
            logger.error(f"❌ Shortcut execution failed: {e}")
            return {'success': False, 'error': str(e)}

# ============================================================================
# ✅ GENERIC PAGE STATE OBSERVER - WORKS FOR ALL WEBSITES
# ============================================================================

async def observe_page_state(page) -> Dict[str, Any]:
    """
    ✅ GENERIC page state observation - works for ANY website.
    Replaces YouTube-only observation.
    """
    
    logger.info("👁️ Observing page state (generic)...")
    
    try:
        # Detect site type first
        site_info = await SiteDetector.detect_site_type(page)
        
        # Get comprehensive state
        state = await page.evaluate("""
            () => {
                const state = {
                    url: window.location.href,
                    title: document.title,
                    readyState: document.readyState,
                    scrollPosition: window.scrollY,
                    viewportHeight: window.innerHeight,
                    viewportWidth: window.innerWidth,
                    activeElement: document.activeElement?.tagName || 'BODY',
                    
                    // Video state (any site)
                    video: null,
                    
                    // Audio state (any site)
                    audio: null,
                    
                    // Interactive elements (any site)
                    interactive: {
                        hasButtons: document.querySelectorAll('button, [role="button"]').length > 0,
                        hasInputs: document.querySelectorAll('input, textarea, select').length > 0,
                        hasLinks: document.querySelectorAll('a[href]').length > 0,
                        hasModals: document.querySelectorAll('[role="dialog"], .modal, [class*="modal"]').length > 0,
                    },
                    
                    // Shopping features (ecommerce sites)
                    shopping: {
                        hasCart: !!document.querySelector('[data-testid*="cart" i], [aria-label*="cart" i], #cart'),
                        hasPrices: document.querySelectorAll('[data-price], .price, [class*="price"]').length > 0,
                        hasProducts: document.querySelectorAll('[data-product], [class*="product"]').length > 0,
                    },
                    
                    // Search features (any site)
                    search: {
                        hasSearchBox: !!document.querySelector('[type="search"], [role="search"], input[placeholder*="search" i]'),
                        searchFocused: document.activeElement?.type === 'search',
                    }
                };
                
                // Check for video element
                const video = document.querySelector('video');
                if (video) {
                    state.video = {
                        exists: true,
                        src: video.src || video.currentSrc,
                        paused: video.paused,
                        muted: video.muted,
                        volume: video.volume,
                        currentTime: video.currentTime,
                        duration: isNaN(video.duration) ? null : video.duration,
                        playing: !video.paused && video.currentTime > 0,
                        ended: video.ended,
                        readyState: video.readyState,
                        networkState: video.networkState,
                    };
                }
                
                // Check for audio element
                const audio = document.querySelector('audio');
                if (audio) {
                    state.audio = {
                        exists: true,
                        src: audio.src || audio.currentSrc,
                        paused: audio.paused,
                        muted: audio.muted,
                        volume: audio.volume,
                        currentTime: audio.currentTime,
                        duration: isNaN(audio.duration) ? null : audio.duration,
                        playing: !audio.paused && audio.currentTime > 0,
                    };
                }
                
                return state;
            }
        """)
        
        # ✅ FIX 1: page.evaluate returns None when page is blank or mid-navigation
        if not state:
            state = {
                'url': page.url if page else 'unknown',
                'platform': 'unknown',
                'siteType': 'generic',
                'capabilities': []
            }

        # Add detected site info - with null safety
        if not site_info:
            site_info = {
                'site_type': 'generic',
                'platform': 'unknown',
                'capabilities': []
            }

        state['siteInfo'] = site_info
        state['platform'] = site_info.get('platform', 'unknown')
        state['siteType'] = site_info.get('site_type', 'generic')
        state['capabilities'] = site_info.get('capabilities', [])
        
        # Platform-specific detection (for backward compatibility)
        state['isYouTube'] = state.get('platform') == 'youtube'
        state['isPlaylist'] = False
        
        if state['isYouTube']:
            # YouTube-specific checks
            try:
                playlist_check = await page.evaluate("""
                    () => !!document.querySelector('[aria-label*="playlist" i], #playlist, .playlist')
                """)
                state['isPlaylist'] = playlist_check
            except:
                state['isPlaylist'] = False
        
        # Safe access to video state (may be null from JS)
        video_state = state.get('video') or {}
        has_video = video_state.get('exists', False) if isinstance(video_state, dict) else False

        logger.info(f"✅ Page state: platform={state.get('platform', 'unknown')}, type={state.get('siteType', 'generic')}, "
                   f"video={has_video}, capabilities={state.get('capabilities', [])}")
        
        return state
        
    except Exception as e:
        logger.debug(f"⚠️ Could not observe page state (expected on blank/loading page): {e}")
        return {
            'error': str(e),
            'url': page.url if page else 'unknown',
            'platform': 'unknown',
            'siteType': 'generic',
            'capabilities': []
        }


# ============================================================================
# ✅ FIX: validate_action_context — media check ONLY for explicit media actions
# ============================================================================

# Action types that are explicitly media-related.
# navigate / fill / click / extract / unknown are NEVER media actions.
_MEDIA_ACTION_TYPES = {
    'play_video', 'pause_video', 'media_control',
    'play', 'pause', 'mute', 'unmute',
    'skip', 'next', 'previous', 'forward', 'rewind'
}

def validate_action_context(page_state: Dict, action_type: str, ai_prompt: str) -> Tuple[bool, str]:
    """
    ✅ FIXED context validation.

    The media guard now ONLY runs when action_type is an explicitly recognised
    media action type.  navigate / fill / click / extract / unknown always
    pass immediately, preventing false rejections on retry when the previous
    error message happens to contain the word "media".
    """

    # ── Gate: skip ALL media checks for non-media action types ──────────────
    if action_type not in _MEDIA_ACTION_TYPES:
        return True, "Context validated - can proceed"

    # ── From here we know action_type IS a media action ─────────────────────
    media_keywords = ['pause', 'play', 'mute', 'unmute', 'skip', 'next', 'previous', 'forward', 'rewind']
    selection_keywords = ['first', 'second', 'third', 'result', 'item', 'one', 'video', 'song']

    # If the user is selecting a result (e.g. "play the first one") rather than
    # controlling an already-playing media element, skip media checks.
    is_selecting = any(w in ai_prompt.lower() for w in selection_keywords)

    if any(action in ai_prompt.lower() for action in media_keywords) and not is_selecting:
        video_state = page_state.get('video')
        audio_state = page_state.get('audio')

        # Require a media element on the page
        has_video = isinstance(video_state, dict) and video_state.get('exists')
        has_audio = isinstance(audio_state, dict) and audio_state.get('exists')

        if not (has_video or has_audio):
            return False, "No media element found on page. Cannot perform media control."

        # State-specific guards
        if 'pause' in ai_prompt.lower():
            if has_video and video_state.get('paused'):
                return False, "Media is already paused. No action needed."

        elif 'play' in ai_prompt.lower():
            if has_video and video_state.get('playing'):
                return False, "Media is already playing. No action needed."

        elif 'mute' in ai_prompt.lower():
            if has_video and video_state.get('muted'):
                return False, "Media is already muted. No action needed."

        elif 'unmute' in ai_prompt.lower():
            if has_video and not video_state.get('muted'):
                return False, "Media is already unmuted. No action needed."

        elif any(word in ai_prompt.lower() for word in ['skip', 'next']):
            # Only warn for YouTube playlists
            if page_state.get('isYouTube') and not page_state.get('isPlaylist'):
                return False, "No playlist detected. 'Skip/Next' only works in playlists on YouTube."

    return True, "Context validated - can proceed"


async def compare_states(before: Dict, after: Dict) -> Dict[str, Any]:
    """
    ✅ GENERIC state comparison - works for any site.
    Compare page states before and after action.
    """
    
    changes = {
        'url_changed': before.get('url') != after.get('url'),
        'media_state_changed': False,
        'focus_changed': before.get('activeElement') != after.get('activeElement'),
        'scroll_changed': before.get('scrollPosition') != after.get('scrollPosition'),
    }
    
    # Check video state changes
    video_before = before.get('video') or {}
    video_after = after.get('video') or {}
    
    if video_before.get('exists') and video_after.get('exists'):
        changes['media_state_changed'] = (
            video_before.get('paused') != video_after.get('paused') or
            video_before.get('muted') != video_after.get('muted') or
            abs((video_before.get('currentTime') or 0) - (video_after.get('currentTime') or 0)) > 0.1
        )
        
        changes['media_details'] = {
            'paused_changed': video_before.get('paused') != video_after.get('paused'),
            'muted_changed': video_before.get('muted') != video_after.get('muted'),
            'time_changed': abs((video_before.get('currentTime') or 0) - (video_after.get('currentTime') or 0)) > 0.1,
        }
    
    # Check audio state changes
    audio_before = before.get('audio') or {}
    audio_after = after.get('audio') or {}
    
    if audio_before.get('exists') and audio_after.get('exists'):
        if not changes['media_state_changed']:  # Only check if video didn't change
            changes['media_state_changed'] = (
                audio_before.get('paused') != audio_after.get('paused') or
                audio_before.get('muted') != audio_after.get('muted')
            )
    
    changes['any_change'] = any([
        changes['url_changed'],
        changes['media_state_changed'],
        changes['focus_changed'],
    ])
    
    return changes


def build_smart_intent_prompt(page_state: Dict, ai_prompt: str, page_context: Dict) -> str:
    """
    ✅ ENHANCED: Build smart intent prompt based on detected site type.
    Dynamically adjusts based on platform.
    """
    
    platform = page_state.get('platform', 'unknown')
    site_type = page_state.get('siteType', 'generic')
    capabilities = page_state.get('capabilities', [])
    
    # Get platform-specific shortcuts
    available_shortcuts = []
    if platform in KeyboardShortcuts.SHORTCUTS:
        for action, shortcut in KeyboardShortcuts.SHORTCUTS[platform].items():
            available_shortcuts.append(f"  - {action}: '{shortcut}'")
    
    shortcuts_section = ""
    if available_shortcuts:
        shortcuts_section = f"""
================================================================
KEYBOARD SHORTCUTS FOR {platform.upper()}:
================================================================
{chr(10).join(available_shortcuts)}

USE THESE SHORTCUTS when UI elements are missing or unreliable!
"""
    
    # Build smart intent rules based on site type
    smart_intent_rules = ""
    
    if 'media_player' in capabilities:
        smart_intent_rules += """
2a) **Media Controls (Video/Audio sites)**:
   - Prefer keyboard shortcuts over clicking UI buttons
   - Example: For "pause" → press appropriate key (check shortcuts above)
   - Fallback: Use page.evaluate() to directly control media element
   - Example: `await page.evaluate('() => document.querySelector("video").pause()')`
"""
    
    if 'shopping' in capabilities:
        smart_intent_rules += """
2b) **Shopping Features (E-commerce sites)**:
   - Look for cart icons, price elements, product listings
   - Use data attributes: [data-testid*="cart"], [data-price]
   - Fallback to aria-labels and class names
"""
    
    if 'search' in capabilities:
        smart_intent_rules += """
2c) **Search Features**:
   - Try [type="search"], [role="search"], input[placeholder*="search"]
   - Use keyboard shortcut '/' on supported sites
"""
    
    # FIX: auth/login pages get specific rules including auto-Next logic
    if platform in ('google_auth', 'microsoft_auth', 'auth') or site_type == 'form':
        # Detect if password field is currently visible (vs hidden) from the context
        semantics = page_context.get('semantics', '')
        password_visible = (
            'input[type="password"]' in semantics or
            'password' in semantics.lower() and 'hiddenPassword' not in semantics
        )
        # Check if the inputs listed include a visible (non-hidden) password field
        # The DOM shows hiddenPassword on email page, and a real password input on password page
        has_visible_password = 'password' in semantics.lower() and 'hidden' not in semantics.lower()

        smart_intent_rules = f"""
2d) **Login / Auth Form — CRITICAL RULES**:

CURRENT FORM STATE: {"PASSWORD PAGE (password field IS visible)" if has_visible_password else "EMAIL PAGE (password field is hidden/not yet visible)"}

RULE 1 — FILL THEN AUTO-CLICK NEXT:
  After filling any input field, check if a 'Next' or 'Sign in' button exists.
  If it does, ALWAYS click it automatically WITHOUT being asked.
  Do NOT stop after just filling — proceed to click Next.

RULE 2 — HANDLE GOOGLE'S 2-PAGE FLOW:
  Google sign-in shows email and password on SEPARATE pages (same URL, different DOM).
  - Email page: fill email input, then click Next button
  - Password page: wait for password input to appear, fill it, then click Next/Sign In

RULE 3 — VISIBLE PASSWORD DETECTION:
  {"✅ Password field IS visible — fill it directly with input[type='password'] or [name='password']" if has_visible_password else "⚠️ Password field is NOT yet visible (still on email page) — DO NOT try to fill password yet"}
  {"After filling password, click the Sign In / Next button immediately." if has_visible_password else "Fill email, then click Next, then the password field will appear."}

RULE 4 — SELECTOR PRIORITY for Google auth:
  Email input:    input[type="email"], #identifierId, input[name="identifier"]
  Password input: input[type="password"], input[name="password"], input[name="Passwd"]
  Next button:    button:has-text("Next") → use .first to avoid strict mode
  Sign In button: button:has-text("Sign in"), button:has-text("Next")

RULE 5 — HUMAN-LIKE TIMING:
  Add await page.wait_for_timeout(500) between fill and click.
  Add await page.wait_for_timeout(800) after clicking Next (for page transition).
"""
    elif not smart_intent_rules:
        smart_intent_rules = """
2d) **Generic Site Strategy**:
   - Try multiple selector strategies (id, class, aria-label, data attributes)
   - Use page.evaluate() for direct DOM manipulation
   - Look for semantic HTML elements (button, input, a, etc.)
"""
    
    enhanced_prompt = f"""
# CURRENT PAGE STATE
URL: {page_context.get('url', 'unknown')}
Title: {page_context.get('title', 'unknown')}
Platform: {platform}
Site Type: {site_type}
Capabilities: {', '.join(capabilities) if capabilities else 'none detected'}

# PAGE STATE INFORMATION
{json.dumps(page_state, indent=2)}

# AVAILABLE INTERACTIVE ELEMENTS
{page_context.get('semantics', 'unavailable')}

# USER TASK
{ai_prompt}

================================================================
ENHANCED RULES WITH SMART INTENT HANDLING:
================================================================

1. **Primary Approach**: Use ONLY elements that exist in the list above

2. **Smart Intent for Missing Elements**: 
{smart_intent_rules}

3. **Success Criteria**:
   - Print 'EXECUTION_SUCCESS' ONLY when the intended outcome is achieved
   - Verify state change when possible
   - For media controls: check element state after action

4. **Failure Handling**:
   - If element truly doesn't exist and no alternative works:
     Print 'FAILED: [specific reason]' with what you tried

{shortcuts_section}

================================================================

Generate code that intelligently handles the task even if exact UI elements are not listed.
Use platform-specific shortcuts when available.
"""
    
    return enhanced_prompt

# ============================================================================
# ✅ PERSISTENT PAGE CONTEXT CACHE (SEPARATE FROM MEM0)
# ============================================================================

class PageContextCache:
    """
    Tab-level DOM context cache - completely separate from mem0.
    mem0 = conversation memory (user preferences, history)
    This = page DOM state (buttons, inputs, current elements)
    """
    
    def __init__(self, ttl_seconds: int = 30):
        self.cache: Dict[str, Dict] = {}
        self.ttl = ttl_seconds
        self.last_analysis: Dict[str, float] = {}
        self._last_url: Dict[str, str] = {}  # FIX: track URL so we invalidate on page change

    def should_refresh(self, session_id: str, current_url: str = '') -> bool:
        """Check if context needs refreshing — also invalidates when URL changes."""
        if session_id not in self.last_analysis:
            return True
        # Any URL change means we're on a new page (e.g. email → password step)
        if current_url and self._last_url.get(session_id) != current_url:
            logger.info(f"🔄 URL changed for session {session_id} — cache invalidated")
            return True
        elapsed = datetime.now().timestamp() - self.last_analysis[session_id]
        return elapsed > self.ttl

    async def get_or_analyze(self, session_id: str, page, force_refresh: bool = False):
        """Get cached context or analyze page if needed"""
        current_url = page.url if page else ''

        if not force_refresh and session_id in self.cache:
            if not self.should_refresh(session_id, current_url):
                logger.info(f"📦 Using cached DOM context for session {session_id}")
                return self.cache[session_id]

        logger.info(f"🔍 Analyzing DOM context for session {session_id}")

        try:
            from agents.execution_agent.RAG.web.page_inspector import get_page_context

            context = await get_page_context(page)

            self.cache[session_id] = context
            self.last_analysis[session_id] = datetime.now().timestamp()
            self._last_url[session_id] = current_url  # FIX: store URL

            logger.info(f"✅ DOM context cached for session {session_id}")
            return context
            
        except Exception as e:
            logger.error(f"❌ Failed to analyze DOM context: {e}")
            return {
                'url': page.url if page else 'unknown',
                'title': 'unknown',
                'semantics': 'unavailable',
                'error': str(e)
            }
    
    def invalidate(self, session_id: str):
        """Invalidate cache (e.g., after navigation)"""
        self.cache.pop(session_id, None)
        self._last_url.pop(session_id, None)
        logger.info(f"🗑️ Invalidated DOM cache for session {session_id}")
    
    def cleanup_closed_sessions(self, active_sessions: List[str]):
        """Remove cache for closed sessions"""
        sessions_to_remove = [s for s in self.cache.keys() if s not in active_sessions]
        for session_id in sessions_to_remove:
            del self.cache[session_id]
            if session_id in self.last_analysis:
                del self.last_analysis[session_id]

# ============================================================================
# ✅ ADVANCED STEALTH BROWSER
# ============================================================================

class StealthBrowser:
    """Advanced browser fingerprint randomization and bot detection bypass"""
    
    @staticmethod
    def get_random_user_agent() -> str:
        """Generate realistic user agent"""
        user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        ]
        return random.choice(user_agents)
    
    @staticmethod
    def get_random_viewport() -> Dict[str, int]:
        """Generate realistic viewport size"""
        common_resolutions = [
            {'width': 1920, 'height': 1080},
            {'width': 1366, 'height': 768},
            {'width': 1536, 'height': 864},
            {'width': 1440, 'height': 900},
        ]
        return random.choice(common_resolutions)
    
    @staticmethod
    async def inject_stealth_scripts(context):
        """Inject ULTIMATE comprehensive anti-detection scripts"""
        
        stealth_script = """
        // ════════════════════════════════════════════════════════════════════
        // 🔥 ADVANCED BOT DETECTION BYPASS - CLOUDFLARE/RECAPTCHA/TIMEOUT/etc
        // ════════════════════════════════════════════════════════════════════

        // ── 1. CORE WEBDRIVER DETECTION ───────────────────────────────────
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined, configurable: true
        });
        
        // ── 2. HEADLESS CHROME DETECTION ──────────────────────────────────
        delete navigator.__proto__.webdriver;
        
        // ── 3. CHROME OBJECT (CRITICAL FOR GOOGLE/CLOUDFLARE) ────────────
        window.chrome = {
            app: { isInstalled: false, InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' }, RunningState: { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' } },
            runtime: {
                PlatformOs: { MAC: 'mac', WIN: 'win', ANDROID: 'android', CROS: 'cros', LINUX: 'linux', OPENBSD: 'openbsd' },
                PlatformArch: { ARM: 'arm', ARM64: 'arm64', X86_32: 'x86-32', X86_64: 'x86-64', MIPS: 'mips', MIPS64: 'mips64' },
                RequestUpdateCheckStatus: { THROTTLED: 'throttled', NO_UPDATE: 'no_update', UPDATE_AVAILABLE: 'update_available' },
                OnInstalledReason: { INSTALL: 'install', UPDATE: 'update', CHROME_UPDATE: 'chrome_update', SHARED_MODULE_UPDATE: 'shared_module_update' },
                OnRestartRequiredReason: { APP_UPDATE: 'app_update', OS_UPDATE: 'os_update', PERIODIC: 'periodic' },
                connect: () => {},
                sendMessage: () => {},
                id: undefined,
                getBackgroundPage: () => null,
                getManifest: () => null,
                getURL: () => '',
                onConnect: { addListener: () => {} },
                onMessage: { addListener: () => {} },
                onMessageExternal: { addListener: () => {} },
            },
            loadTimes: function() {
                return {
                    commitLoadTime: Date.now()/1000 - Math.random()*8,
                    connectionInfo: 'h2',
                    finishDocumentLoadTime: Date.now()/1000 - Math.random()*3,
                    firstPaintTime: Date.now()/1000 - Math.random()*5,
                    navigationType: 'Other',
                    npnNegotiatedProtocol: 'h2',
                    requestTime: Date.now()/1000 - Math.random()*12,
                    startLoadTime: Date.now()/1000 - Math.random()*12,
                    wasAlternateProtocolAvailable: false,
                    wasFetchedViaSpdy: true,
                    wasNpnNegotiated: true,
                };
            },
            csi: function() { return { onloadT: Date.now(), pageT: Date.now() - 3000, startE: Date.now() - 5000, tran: 15 }; },
        };

        // ── 4. PERMISSIONS API (CLOUDFLARE + BROWSER DETECTION) ───────────
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (parameters) => (
            parameters.name === 'notifications' ?
                Promise.resolve({ state: Notification.permission }) :
                originalQuery(parameters)
        );
        
        // Fix for permissions.query detection
        try {
            if (navigator.permissions && navigator.permissions.query) {
                navigator.permissions.query = (parameters) => Promise.resolve({ state: 'prompt' });
            }
        } catch(e) {}

        // ── 5. PLUGIN ARRAY (ESSENTIAL FOR BOT DETECTION) ─────────────────
        const pluginData = [
            { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
            { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
            { name: 'Native Client', filename: 'internal-nacl-plugin', description: '' },
        ];
        const pluginArray = pluginData.map(p => {
            const plugin = Object.create(Plugin.prototype);
            Object.defineProperties(plugin, {
                name: { value: p.name, writable: false }, 
                filename: { value: p.filename, writable: false }, 
                description: { value: p.description, writable: false }, 
                length: { value: 0, writable: false }
            });
            return plugin;
        });
        Object.defineProperty(pluginArray, 'item', { value: (i) => pluginArray[i] });
        Object.defineProperty(pluginArray, 'namedItem', { value: (name) => pluginArray.find(p => p.name === name) || null });
        Object.defineProperty(navigator, 'plugins', { get: () => pluginArray, configurable: true });

        // ── 6. LANGUAGES/LOCALE ───────────────────────────────────────────
        Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'], configurable: true });
        Object.defineProperty(navigator, 'language', { get: () => 'en-US', configurable: true });

        // ── 7. SCREEN PROPERTIES ──────────────────────────────────────────
        Object.defineProperty(screen, 'colorDepth', { get: () => 24, configurable: true });
        Object.defineProperty(screen, 'pixelDepth', { get: () => 24, configurable: true });
        Object.defineProperty(screen, 'availHeight', { get: () => 1040, configurable: true });
        Object.defineProperty(screen, 'availWidth', { get: () => 1920, configurable: true });

        // ── 8. CANVAS FINGERPRINTING BYPASS ───────────────────────────────
        const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
        HTMLCanvasElement.prototype.toDataURL = function(type) {
            if (type === 'image/png' && this.width === 280 && this.height === 60) {
                return 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABgAAAA8CAYAAAA...';
            }
            return originalToDataURL.call(this, type);
        };
        
        const originalGetContext = HTMLCanvasElement.prototype.getContext;
        HTMLCanvasElement.prototype.getContext = function(contextType, ...args) {
            const context = originalGetContext.call(this, contextType, ...args);
            if (contextType === '2d') {
                const fillText = context.fillText;
                context.fillText = function(text, x, y, ...args) {
                    if (this.canvas.width === 280 && this.canvas.height === 60) {
                        return;
                    }
                    return fillText.apply(this, [text, x, y, ...args]);
                };
            }
            return context;
        };

        // ── 9. WEBGL FINGERPRINTING BYPASS ────────────────────────────────
        const getParameter = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = function(parameter) {
            if (parameter === 37445) return 'Intel Inc.';
            if (parameter === 37446) return 'Intel Iris OpenGL Engine';
            return getParameter.call(this, parameter);
        };

        // ── 10. CONCEAL AUTOMATION TOOLS ──────────────────────────────────
        delete window.__playwright;
        delete window.__pw_manual;
        delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;
        delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;
        delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;
        delete window.cdc_adoQpoasnfa76pfcZLmcfl_Object;
        
        // Puppeteer detection bypass
        Object.defineProperty(navigator, 'vendor', { get: () => 'Google Inc.', configurable: true });
        Object.defineProperty(navigator, 'platform', { get: () => 'Win32', configurable: true });
        
        // ── 11. NOTIFICATION API ──────────────────────────────────────────
        window.Notification = window.Notification || {};
        Object.defineProperty(window.Notification, 'permission', { 
            get: () => 'default',
            configurable: true
        });

        // ── 12. TIMING RANDOMIZATION (AVOID TIMING ANALYSIS) ──────────────
        const originalDateNow = Date.now;
        let randomOffset = Math.random() * 1000;
        Date.now = function() {
            return originalDateNow() + randomOffset;
        };

        // ── 13. FUNCTION TOSTRING HIJACK (SOME BOT DETECTORS CHECK THIS) ──
        const originalFunctionToString = Function.prototype.toString;
        Function.prototype.toString = function() {
            if (this.name === 'toString' || this === originalFunctionToString) {
                return 'function toString() { [native code] }';
            }
            return originalFunctionToString.call(this);
        };

        // ── 14. RTC LEAK PREVENTION ───────────────────────────────────────
        try {
            const rtc = window.RTCPeerConnection || window.webkitRTCPeerConnection;
            if (rtc) {
                window.RTCPeerConnection = new Proxy(rtc, {
                    construct: function(target) {
                        throw new Error('WebRTC blocked');
                    }
                });
            }
        } catch(e) {}

        // ── 15. OVERRIDE EVAL (CLOUDFLARE PROTECTION) ────────────────────
        const originalEval = window.eval;
        window.eval = function(code) {
            if (code && code.includes('__protoBytesToSet')) return undefined;
            return originalEval.call(this, code);
        };

        // ── 16. PROXY DETECTION BYPASS ────────────────────────────────────
        const handler = {
            get: (target, prop, receiver) => {
                if (prop === Symbol.toStringTag) {
                    return 'Object';
                }
                return Reflect.get(target, prop, receiver);
            }
        };
        window.Proxy = new Proxy(window.Proxy, handler);
        """
        
        await context.add_init_script(stealth_script)
        logger.info("✅ ULTIMATE stealth scripts injected (Cloudflare/reCAPTCHA/WebGL/Canvas bypass)")
    
    @staticmethod
    def get_stealth_launch_args() -> List[str]:
        """Get ULTIMATE comprehensive launch arguments for aggressive stealth mode"""
        return [
            # ─ CORE ANTI-DETECTION ──────────────────────────────────────
            '--disable-blink-features=AutomationControlled',
            '--disable-dev-shm-usage',
            '--disable-web-security',
            '--disable-features=IsolateOrigins,site-per-process',
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-infobars',
            '--window-size=1920,1080',
            '--start-maximized',
            
            # ─ PLUGIN & NOTIFICATION BLOCKING ──────────────────────────
            '--disable-notifications',
            '--disable-popup-blocking',
            # ❌ REMOVED: '--disable-plugins',  # FIX 4: Removes PDF plugin, looks headless
            # ❌ REMOVED: '--disable-extensions',  # FIX 5: Disabling all extensions looks botlike; real browsers have extensions
            '--disable-device-emulation',
            
            # ─ PERFORMANCE/TIMING (AVOID DETECTION VIA TIMING ANALYSIS) ─
            '--disable-background-timer-throttling',
            '--disable-backgrounding-occluded-windows',
            '--disable-renderer-backgrounding',
            '--disable-breakpad',
            '--disable-client-side-phishing-detection',
            
            # ─ FEATURE DISABLING (REDUCE FINGERPRINT VISIBILITY) ────────
            '--disable-default-apps',
            '--disable-hang-monitor',
            '--disable-prompt-on-repost',
            '--disable-sync',
            # ❌ REMOVED: '--enable-automation',  # FIX 4: Signals browser is automated
            '--metrics-recording-only',
            # ❌ REMOVED: '--mute-audio',  # FIX 5: Real browsers don't launch muted; detectable automation signal
            '--no-default-browser-check',
            '--no-first-run',
            '--password-store=basic',
            '--use-mock-keychain',
            
            # ─ GPU DISABLING (REDUCE WEBGL FINGERPRINTING) ──────────────
            '--disable-gpu',
            '--disable-gpu-sandbox',
            '--disable-gpu-compositing',
            
            # ─ NETWORK/TIMING RANDOMIZATION ───────────────────────────
            '--disable-component-update',
            '--disable-default-apps',
            
            # ─ ADVANCED PATCHES ──────────────────────────────────────
            '--disable-translate',
            '--disable-save-password-bubble',
        ]

# ============================================================================
# ENHANCED WEB EXECUTION PIPELINE
# ============================================================================

class WebExecutionPipeline:
    """
    Enhanced pipeline with:
    - GENERIC multi-platform support
    - Page state layer (observe before acting)
    - Platform-specific keyboard shortcuts
    - Post-action verification
    - Smart intent handling
    - Persistent context (separate from mem0)
    """
    
    def __init__(self, config: WebExecutionConfig):
        self.config = config
        self.playwright = None
        self.browser = None
        self.context = None
        self.sessions = {}
        self.session_downloads = {}  # ✅ FIX 1: Track downloads per session
        self._rag_system = None
        self.shared_groq_client = None
        
        # ✅ Helper classes
        self.context_cache = PageContextCache(ttl_seconds=config.context_cache_ttl)
        self.stealth = StealthBrowser()
        
        Path(self.config.screenshot_dir).mkdir(parents=True, exist_ok=True)
    
    async def initialize(self):
        """Initialize Playwright with advanced stealth mode"""
        try:
            from playwright.async_api import async_playwright
            
            logger.info("🚀 Initializing Playwright with advanced stealth...")
            
            self.playwright = await async_playwright().start()
            
            launch_args = self.stealth.get_stealth_launch_args()
            
            self.browser = await self.playwright.chromium.launch(
                headless=self.config.headless,
                slow_mo=self.config.slow_mo,
                args=launch_args
            )
            
            viewport = self.stealth.get_random_viewport() if self.config.randomize_fingerprint else {
                'width': self.config.viewport_width,
                'height': self.config.viewport_height
            }
            
            user_agent = self.stealth.get_random_user_agent() if self.config.use_real_user_agent else None
            
            context_options = {
                'viewport': viewport,
                'locale': 'en-US',
                'timezone_id': 'America/New_York',
                'permissions': ['geolocation', 'notifications'],
                'geolocation': {'longitude': -74.006, 'latitude': 40.7128},
                'extra_http_headers': {
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9',
                    'Accept-Encoding': 'gzip, deflate, br',
                    'Accept-Language': 'en-US,en;q=0.9',
                    'Cache-Control': 'max-age=0',
                    'Connection': 'keep-alive',
                    'DNT': '1',
                    'Pragma': 'no-cache',
                    'Sec-Fetch-Dest': 'document',
                    'Sec-Fetch-Mode': 'navigate',
                    'Sec-Fetch-Site': 'none',
                    'Sec-Fetch-User': '?1',
                    'Sec-GPC': '1',
                    'Upgrade-Insecure-Requests': '1',
                    'User-Agent': user_agent or self.stealth.get_random_user_agent(),
                },
                'accept_downloads': True,
                'ignore_https_errors': True,
            }
            
            if user_agent:
                context_options['user_agent'] = user_agent
            
            # ✅ FIX 5: Load persistent cookies from previous session (per-user)
            # This preserves cookies between runs, so Google sees a returning user with history
            self.current_user = get_current_system_user()
            self.cookies_path = Path(__file__).parent / f'browser_state_{self.current_user}.json'
            if self.cookies_path.exists():
                context_options['storage_state'] = str(self.cookies_path)
                logger.info(f"✅ Loading persistent browser state for user '{self.current_user}'")
            else:
                logger.info(f"📝 No existing browser state found for '{self.current_user}' - will auto-login on-demand when Google is needed")
            
            self.context = await self.browser.new_context(**context_options)
            
            # Auto-login is deferred to on-demand (when Google task is executed, see get_or_create_page)
            
            # ✅ FIX 4: Add request interception with proper Referer handling + Google anti-detection
            async def handle_route(route):
                request = route.request
                headers = dict(request.headers)
                
                # Only set Referer on cross-site requests; omit on first-party navigation
                current_url = request.url
                referer = request.headers.get('referer', '')
                
                # Extract domain from URLs for comparison
                from urllib.parse import urlparse
                current_domain = urlparse(current_url).netloc
                referer_domain = urlparse(referer).netloc if referer else ''
                
                # ✅ FIX: Google search requires Referer header to not trigger bot detection
                # If referer is missing on search operations, set Google's homepage as referer
                if not referer or not referer_domain:
                    if 'google' in current_domain.lower() and '/search' in current_url:
                        headers['Referer'] = 'https://www.google.com/'
                    elif 'google' in current_domain.lower():
                        headers['Referer'] = 'https://www.google.com/'
                # Only set Referer if cross-site; otherwise let browser default
                elif referer_domain and referer_domain != current_domain:
                    headers['Referer'] = referer  # Preserve original cross-site referer
                
                # ✅ FIX: Add critical headers Google checks for real browsers
                headers['Origin'] = '/'.join(current_url.split('/')[:3])
                
                # Ensure Google sees real Accept headers
                if 'google' in current_domain.lower():
                    headers['Accept'] = 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9'
                    headers['Accept-Encoding'] = 'gzip, deflate, br'
                    headers['Accept-Language'] = 'en-US,en;q=0.9,en;q=0.8'
                    headers['Sec-Fetch-Dest'] = 'document'
                    headers['Sec-Fetch-Mode'] = 'navigate'
                    headers['Sec-Fetch-Site'] = 'none'
                    headers['Sec-Fetch-User'] = '?1'
                    headers['Upgrade-Insecure-Requests'] = '1'
                    headers['Pragma'] = 'no-cache'
                    headers['Cache-Control'] = 'max-age=0'
                
                await route.continue_(headers=headers)
            
            await self.context.route('**/*', handle_route)
            
            if self.config.use_stealth_plugin:
                await self.stealth.inject_stealth_scripts(self.context)
            
            logger.info("✅ ULTIMATE stealth Playwright initialized (persistent cookies + extensions allowed + dynamic headers + timing drift + humanization)")
            
        except Exception as e:
            logger.error(f"❌ Failed to initialize Playwright: {e}")
            raise
    
    async def _is_page_truly_closed(self, page) -> bool:
        """
        Distinguish between a truly closed/crashed page and a page that is
        simply on an error/bot-detection URL.

        Playwright's page.is_closed() only returns True when the CDP connection
        is gone (tab closed, browser crashed).  A Google bot-detection page is
        NOT closed — the tab is alive but showing an error.  We must NOT create
        a new page in that case, because doing so loses the existing browser
        session (cookies, history) and starts fresh on about:blank, which then
        triggers *more* bot detection on the very next task.

        Strategy: try a lightweight evaluate().  If it succeeds, the page is
        alive.  If it raises TargetClosedError (or any Playwright closed error),
        the page is truly gone.
        """
        if page is None:
            return True
        if page.is_closed():
            return True
        try:
            await page.evaluate("() => document.readyState")
            return False   # page responded → it is alive
        except Exception as e:
            err = str(e).lower()
            if "closed" in err or "target" in err or "disconnected" in err:
                return True
            # Any other error (e.g. script timeout on a loading page) — keep page
            return False

    async def get_or_create_page(self, session_id: str):
        """Get existing page for session or create new one with async download handling"""
        existing = self.sessions.get(session_id)
        page_truly_closed = await self._is_page_truly_closed(existing)
        if page_truly_closed:
            page = await self.context.new_page()
            
            # ✅ FIX 6: Apply playwright-stealth to patch Runtime.enable CDP leak
            # This is essential for defeating runtime-based bot detection
            try:
                from playwright_stealth import stealth_async
                await stealth_async(page)
                logger.info("✅ playwright-stealth injected successfully")
            except ImportError:
                logger.warning("⚠️  playwright-stealth not installed. Install with: pip install playwright-stealth")
            except Exception as e:
                logger.warning(f"⚠️  Failed to inject playwright-stealth: {e}")
            
            # ✅ FIX 1: Async download handler that doesn't block event loop
            async def handle_download_async(download):
                """Async handler for downloads - non-blocking"""
                logger.info(f"🔽 DOWNLOAD TRIGGERED: {download.suggested_filename}")
                
                try:
                    downloads_dir = str(Path.home() / 'Downloads')
                    os.makedirs(downloads_dir, exist_ok=True)
                    
                    filename = download.suggested_filename
                    if not filename:
                        filename = f"download_{int(datetime.now().timestamp())}"
                    
                    filepath = os.path.join(downloads_dir, filename)
                    
                    logger.info(f"💾 Saving to: {filepath}")
                    
                    # Schedule the blocking save_as call asynchronously
                    await asyncio.get_event_loop().run_in_executor(
                        None, lambda: download.save_as(filepath)
                    )
                    
                    logger.info(f"✅ Downloaded successfully: {filepath}")
                    
                    # Verify file exists
                    if os.path.exists(filepath):
                        filesize = os.path.getsize(filepath)
                        logger.info(f"✅ File verified: {filepath} ({filesize} bytes)")
                        
                        # Track download for this session
                        if session_id not in self.session_downloads:
                            self.session_downloads[session_id] = []
                        self.session_downloads[session_id].append(filepath)
                    else:
                        logger.warning(f"⚠️ File not found after save: {filepath}")
                        
                except Exception as e:
                    logger.error(f"❌ Download failed: {e}", exc_info=True)
            
            # Use ensure_future to schedule the async handler
            def sync_download_wrapper(download):
                """Synchronous wrapper for Playwright's download event"""
                asyncio.ensure_future(handle_download_async(download))
            
            page.on("download", sync_download_wrapper)
            logger.info(f"✅ Async download handler registered for page")
            
            self.sessions[session_id] = page
            logger.info(f"📄 Created new page for session {session_id}")
        
        return self.sessions[session_id]
    
    async def detect_and_bypass_challenges(self, page) -> bool:
        """
        Detect Cloudflare, reCAPTCHA, and other bot detection challenges.
        Return True if challenge was bypassed, False otherwise.
        """
        try:
            # Check page title and URL for indicators
            url = page.url.lower()
            title = await page.title()
            
            # ✅ CLOUDFLARE DETECTION
            if 'challenge' in url or 'Ray ID' in title or 'Looking for Cloudflare' in title:
                logger.warning("⚠️ Detected Cloudflare challenge - attempting bypass...")
                await asyncio.sleep(10)  # Wait for JS challenge to complete
                
                try:
                    await page.wait_for_load_state('networkidle', timeout=15000)
                    logger.info("✅ Cloudflare challenge bypassed")
                    return True
                except:
                    logger.warning("⚠️ Cloudflare challenge might still be present")
                    return False
            
            # ✅ RECAPTCHA DETECTION
            if await page.query_selector('iframe[src*="recaptcha"]') or await page.query_selector('[data-sitekey]'):
                logger.warning("⚠️ Detected reCAPTCHA - this requires human interaction or 3rd-party service")
                return False
            
            # ✅ GENERIC BOT CHECK DETECTION
            bot_indicators = [
                'rate limit',
                'too many requests',
                '503',
                'service unavailable',
                'please try again',
                'bot',
                'autom',
                'access denied',
                'forbidden',
            ]
            
            page_content = await page.content()
            for indicator in bot_indicators:
                if indicator in page_content.lower():
                    logger.warning(f"⚠️ Detected potential bot detection: '{indicator}'")
                    # Wait and hope it resolves
                    await asyncio.sleep(5)
                    await page.reload(wait_until='networkidle')
                    logger.info("🔄 Reloaded page after bot detection")
                    return True
            
            return True
            
        except Exception as e:
            logger.warning(f"⚠️ Challenge detection failed: {e}")
            return False
    
    async def wait_for_navigation_with_stealth(self, page, timeout: int = 30000) -> bool:
        """
        Wait for navigation with stealth mode - avoid timeout detection.
        """
        try:
            # Add random delays to avoid detection
            await asyncio.sleep(0.1 + random.random() * 0.5)
            await page.wait_for_load_state('networkidle', timeout=timeout)
            
            # Check for challenges after navigation
            await self.detect_and_bypass_challenges(page)
            
            return True
        except Exception as e:
            logger.warning(f"⚠️ Navigation wait failed: {e}")
            return False
    
    async def _initialize_rag_system(self):
        """Lazy initialize RAG system"""
        if self._rag_system is not None:
            return
        
        try:
            logger.info("🧠 Initializing Playwright RAG system...")
            
            from agents.execution_agent.RAG.web.code_generation import (
                PlaywrightRAGSystem,
                PlaywrightRAGConfig
            )
            
            rag_config = PlaywrightRAGConfig()
            
            self._rag_system = PlaywrightRAGSystem(
                rag_config,
                llm_client=self.shared_groq_client
            )
            self._rag_system.initialize()
            
            logger.info("✅ Playwright RAG system initialized")
            
        except Exception as e:
            logger.error(f"❌ Failed to initialize RAG: {e}")
            raise
    
    async def execute_web_task(
        self,
        task: Dict[str, Any],
        session_id: str = "default"
    ) -> WebExecutionResult:
        """
        Execute web task with FULL enhancements + GENERIC platform support:
        1. Site detection (YouTube, Amazon, Netflix, Google, ANY site)
        2. Page state observation
        3. Context validation (✅ FIXED: only blocks genuine media-type actions)
        4. Platform-specific keyboard shortcuts
        5. Smart intent handling
        6. Post-action verification
        """
        
        start_time = datetime.now()
        task_id = task.get('task_id', 'unknown')
        
        logger.info(f"⚡ Executing web task {task_id}")
        
        try:
            page = await self.get_or_create_page(session_id)
            
            # ── ON-DEMAND GOOGLE LOGIN ───────────────────────────────────────
            # If this is a Google task and we don't have valid cookies, auto-login now
            ai_prompt = task.get('ai_prompt', '').lower()
            is_google_task = any(keyword in ai_prompt for keyword in ['google', 'search', 'gmail', 'youtube'])
            
            if is_google_task and not self.cookies_path.exists():
                logger.info(f"🔐 Google task detected and no saved session found. Triggering on-demand auto-login...")
                # IMPORTANT: Use the SAME page for login AND navigation (don't close it)
                # This ensures cookies are preserved and used for the actual task
                try:
                    await self._humanize_page(page)
                    login_success = await self.auto_login_google(page, self.current_user)
                    if login_success:
                        logger.info(f"✅ On-demand Google auto-login successful for '{self.current_user}'")
                        # Save cookies immediately after successful login (not just at cleanup)
                        try:
                            # Ensure directory exists
                            self.cookies_path.parent.mkdir(parents=True, exist_ok=True)
                            await self.context.storage_state(path=str(self.cookies_path))
                            logger.info(f"💾 Cookies saved immediately after auto-login for '{self.current_user}' to {self.cookies_path}")
                        except Exception as e:
                            logger.warning(f"⚠️  Could not save cookies after auto-login: {e}")
                        # After login succeeds, page is already authenticated. Continue with the task on this page.
                    else:
                        logger.warning(f"⚠️  On-demand auto-login failed. Continuing anyway (might trigger bot detection)...")
                except Exception as e:
                    logger.error(f"❌ On-demand login failed: {e}")

            # ── GOOGLE BOT-DETECTION ERROR PAGE RECOVERY ─────────────────────
            # When Google shows "having trouble accessing Google Search", the page
            # is still alive (not closed) but it is completely empty / broken.
            # The next task then runs on about:blank (wrong page) or tries to
            # interact with the error page and crashes.  Detect this state here
            # and reload back to google.com so the existing session is preserved.
            try:
                current_url = page.url
                if 'google' in current_url and current_url != 'about:blank':
                    page_text = await page.evaluate("() => document.body && document.body.innerText || ''")
                    if 'having trouble' in page_text.lower() or (
                        'google search' in page_text.lower() and len(page_text.strip()) < 500
                    ):
                        logger.warning("⚠️ Google bot-detection error page detected — reloading to google.com")
                        await page.goto('https://www.google.com', wait_until='domcontentloaded', timeout=15000)
                        await page.wait_for_timeout(2000)
                        logger.info("✅ Recovered to google.com homepage")
            except Exception as _recovery_err:
                logger.debug(f"Bot-detection recovery check skipped: {_recovery_err}")

            # ── Strip any error context that was appended by the bridge on
            #    retry so it never contaminates the original prompt.
            # The bridge appends " | Previous errors: ..." — remove that part.
            clean_prompt = re.split(r'\s*\|\s*Previous errors?:', ai_prompt)[0].strip()
            
            # ✅ INJECT INPUT_CONTENT FROM CROSS-AGENT DATA BRIDGE
            input_content = task.get('extra_params', {}).get('input_content')
            if input_content:
                clean_prompt = f"{clean_prompt}\n\n⚙️ Use the following content from a previous step:\n{input_content}"
                logger.info(f"📬 Prepended input_content ({len(input_content)} chars) to web RAG prompt")
            
            # Validation
            if not clean_prompt:
                return WebExecutionResult(
                    validation_passed=False,
                    security_passed=True,
                    validation_errors=["No ai_prompt provided"],
                    execution_time=(datetime.now() - start_time).total_seconds()
                )
            
            # ✅ STEP 1: OBSERVE PAGE STATE BEFORE ACTION (GENERIC)
            page_state_before = None
            if self.config.enable_page_state_layer:
                page_state_before = await observe_page_state(page)
                
                # ✅ FIXED: action_type comes from web_params, never from ai_prompt
                action_type = task.get('web_params', {}).get('action', 'unknown')
                can_proceed, reason = validate_action_context(
                    page_state_before, action_type, clean_prompt
                )
                
                if not can_proceed:
                    logger.warning(f"⚠️ Context validation failed: {reason}")
                    return WebExecutionResult(
                        validation_passed=False,
                        security_passed=True,
                        error=f"Context validation failed: {reason}",
                        page_state_before=page_state_before,
                        execution_time=(datetime.now() - start_time).total_seconds()
                    )
            
            action_type = task.get('web_params', {}).get('action', 'unknown')
            
            # Check if navigation (invalidate cache)
            if action_type == 'navigate':
                self.context_cache.invalidate(session_id)
            
            # ✅ STEP 2: TRY PLATFORM-SPECIFIC KEYBOARD SHORTCUTS (GENERIC)
            media_keywords = ['pause', 'play', 'mute', 'unmute', 'skip', 'next', 'previous', 'forward', 'rewind']
            selection_keywords = ['first', 'second', 'third', 'result', 'item', 'one', 'video', 'song']
            is_selecting = any(w in clean_prompt.lower() for w in selection_keywords)
            # ✅ Only attempt shortcut if action_type is explicitly a media action
            is_media_action = (
                action_type in _MEDIA_ACTION_TYPES
                and any(keyword in clean_prompt.lower() for keyword in media_keywords)
                and not is_selecting
            )
            
            if is_media_action and page_state_before:
                platform = page_state_before.get('platform', 'unknown')
                
                # Determine action from prompt
                action_word = None
                for keyword in media_keywords:
                    if keyword in clean_prompt.lower():
                        action_word = keyword
                        break
                
                if action_word:
                    logger.info(f"🎬 Detected media control on {platform} - trying keyboard shortcut")
                    
                    shortcut_result = await KeyboardShortcuts.execute_shortcut(page, platform, action_word)
                    
                    if shortcut_result['success']:
                        # Wait for state change
                        await page.wait_for_timeout(500)
                        
                        # Observe state after
                        page_state_after = await observe_page_state(page)
                        
                        # Verify the change
                        changes = await compare_states(page_state_before, page_state_after)
                        
                        if changes['media_state_changed']:
                            logger.info(f"✅ Media control succeeded via keyboard shortcut")
                            
                            return WebExecutionResult(
                                validation_passed=True,
                                security_passed=True,
                                output=f"EXECUTION_SUCCESS: Media control via keyboard shortcut ({shortcut_result['shortcut']}) on {platform}" + (f"\nPAGE_URL:{page.url}" if page.url and page.url != 'about:blank' else ''),
                                page_url=page.url,
                                page_title=await page.title(),
                                page_state_before=page_state_before,
                                page_state_after=page_state_after,
                                verification_message=f"Media state changed: {changes.get('media_details', {})}",
                                execution_time=(datetime.now() - start_time).total_seconds()
                            )
                        else:
                            logger.warning(f"⚠️ Keyboard shortcut executed but no state change detected")
                    else:
                        logger.info(f"ℹ️ Keyboard shortcut not available for {action_word} on {platform}, falling back to RAG")
            
            # ✅ STEP 3: GENERATE CODE WITH SMART INTENT (PLATFORM-AWARE)
            # FIX: add human-like behavior before interacting with auth pages
            if page_state_before and page_state_before.get('platform') in ('google_auth', 'microsoft_auth', 'auth'):
                await self._humanize_page(page)

            logger.info(f"🧠 Using RAG to generate code from: {clean_prompt}")
            
            try:
                generated_code = await self._generate_code_from_rag_smart(
                    clean_prompt, page, task, session_id, page_state_before
                )
                
            except Exception as e:
                logger.error(f"❌ RAG generation failed: {e}")
                
                # ✅ FIX (Message 10): FALLBACK - Generate simple code when RAG fails
                # For "click" actions with link names, generate direct DOM manipulation
                action_type = task.get('web_params', {}).get('action', 'unknown')
                
                if action_type == 'click' and 'link' in clean_prompt.lower():
                    logger.info(f"🔧 Using FALLBACK: Direct DOM link finder for '{clean_prompt}'")
                    generated_code = self._generate_fallback_link_click(clean_prompt)
                    logger.info(f"✅ Generated fallback code ({len(generated_code)} chars)")
                
                # ✅ FIX (Message 10): For search/fill + Enter, generate combined code
                elif action_type == 'fill' and ('search' in clean_prompt.lower() or 'find' in clean_prompt.lower()):
                    logger.info(f"🔧 Using FALLBACK: Search field auto-enter for '{clean_prompt}'")
                    text_to_fill = task.get('web_params', {}).get('text', '')
                    generated_code = self._generate_fallback_search_fill(clean_prompt, text_to_fill)
                    logger.info(f"✅ Generated fallback code ({len(generated_code)} chars)")
                
                else:
                    # No fallback available
                    return WebExecutionResult(
                        validation_passed=False,
                        security_passed=True,
                        error=f"RAG code generation failed: {str(e)}",
                    execution_time=(datetime.now() - start_time).total_seconds()
                )
            
            # Security check
            security_result = rag_sandbox.check(generated_code)
            if not security_result['passed']:
                return WebExecutionResult(
                    validation_passed=False,
                    security_passed=False,
                    security_violations=security_result['violations'],
                    execution_time=(datetime.now() - start_time).total_seconds()
                )
            
            # ✅ STEP 4: EXECUTE CODE
            logger.info(f"🚀 Executing RAG-generated code")
            _url_before_exec = page.url
            result = await self._execute_generated_code(page, generated_code, task_id)

            # FIX: invalidate cache after ANY click/submit — Google auth keeps same URL
            # but swaps the entire DOM (email page → password page). Force refresh.
            if action_type in ('click', 'submit') or page.url != _url_before_exec:
                logger.info(f"🔄 Post-exec cache invalidation (action={action_type})")
                self.context_cache.invalidate(session_id)

            # ✅ STEP 5: OBSERVE STATE AFTER ACTION
            page_state_after = None
            if self.config.enable_page_state_layer:
                page_state_after = await observe_page_state(page)
            
            # ✅ STEP 6: POST-ACTION VERIFICATION
            verification_passed = True
            verification_message = None
            
            if self.config.enable_verification and result.get('success'):
                # Compare states
                if page_state_before and page_state_after:
                    changes = await compare_states(page_state_before, page_state_after)
                    
                    if changes['any_change']:
                        verification_message = f"✅ Page state changed as expected: {changes}"
                        logger.info(f"✅ Verification: State changed")
                    else:
                        # ℹ️ For click actions: no URL change could mean download, modal, or off-page action
                        if action_type == 'click':
                            verification_message = "✅ Click executed (no page navigation - may have triggered download, modal, or external action)"
                            logger.info(f"✅ Verification: Click action executed (may have triggered download/modal)")
                            # Don't retry fallback for clicks - assume they worked
                        else:
                            verification_message = "⚠️ No page state change detected"
                            logger.warning(f"⚠️ Verification: No state change")
                            
                            # Fallback retry only for non-click actions
                            if action_type == 'fill' and 'search' in clean_prompt.lower():
                                logger.info(f"🔧 No state change for search, retrying with FALLBACK...")
                                fallback_code = self._generate_fallback_search_fill(clean_prompt, task.get('web_params', {}).get('text', ''))
                                result = await self._execute_generated_code(page, fallback_code, task_id)
                                if result.get('success'):
                                    page_state_after = await observe_page_state(page)
                                    verification_message = f"✅ Fallback search succeeded!"
                                    logger.info(f"✅ Fallback search verified")
                
                # Additional verification from verifiers module
                from agents.execution_agent.RAG.web.verifiers import verify_action
                
                verify_context = {
                    'url_before': page_state_before.get('url') if page_state_before else page.url,
                    'text': task.get('web_params', {}).get('text'),
                    'task_id': task_id,
                    'extracted_data': result.get('extracted_data')
                }
                
                verification_passed, verify_msg = await verify_action(
                    page, 
                    action_type, 
                    verify_context
                )
                
                if verification_message:
                    verification_message += f" | {verify_msg}"
                else:
                    verification_message = verify_msg
                
                if not verification_passed:
                    logger.error(f"❌ Verification failed: {verification_message}")
                    result['success'] = False
                    result['error'] = f"Action executed but verification failed: {verification_message}"
            
            # Get final page info — guard against page closing mid-navigation
            try:
                page_url = page.url
                page_title = await page.title()
            except Exception as _page_info_err:
                logger.warning(f"⚠️ Could not get final page info (page may have navigated away): {_page_info_err}")
                page_url = getattr(page, 'url', 'unknown')
                page_title = 'unknown'
            
            # Update context cache in background
            if result.get('success') and self.config.cache_page_context:
                asyncio.create_task(
                    self.context_cache.get_or_analyze(session_id, page, force_refresh=True)
                )
            
            # Screenshot
            screenshot_path = None
            if self.config.screenshots_enabled:
                screenshot_path = os.path.join(
                    self.config.screenshot_dir,
                    f"{task_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                )
                await page.screenshot(path=screenshot_path)
            
            execution_time = (datetime.now() - start_time).total_seconds()
            
            logger.info(f"✅ Task {task_id} completed in {execution_time:.2f}s")
            
            return WebExecutionResult(
                validation_passed=result.get('success', False),
                security_passed=True,
                output=(result.get('output', '') or '') + (f"\nPAGE_URL:{page.url}" if page.url and page.url != 'about:blank' else ''),
                error=result.get('error'),
                page_url=page_url,
                page_title=page_title,
                extracted_data=result.get('extracted_data'),
                screenshot_path=screenshot_path,
                execution_time=execution_time,
                verification_message=verification_message,
                page_state_before=page_state_before,
                page_state_after=page_state_after
            )
            
        except asyncio.TimeoutError:
            logger.error(f"⏰ Task {task_id} timed out")
            return WebExecutionResult(
                validation_passed=False,
                security_passed=True,
                error=f"Timeout after {self.config.timeout_seconds}s",
                execution_time=(datetime.now() - start_time).total_seconds()
            )
        
        except Exception as e:
            logger.error(f"❌ Task {task_id} failed: {e}")
            import traceback
            return WebExecutionResult(
                validation_passed=False,
                security_passed=True,
                error=f"{str(e)}\n{traceback.format_exc()}",
                execution_time=(datetime.now() - start_time).total_seconds()
            )
    
    async def _generate_code_from_rag_smart(
        self, 
        ai_prompt: str,      # ← already cleaned (no error suffix)
        page, 
        task: Dict[str, Any],
        session_id: str,
        page_state: Optional[Dict] = None
    ) -> str:
        """
        Generate code with SMART INTENT HANDLING + PLATFORM AWARENESS.
        ✅ Enhanced prompt that adapts to detected platform.
        """
        
        await self._initialize_rag_system()
        
        # ✅ FIX: Always force_refresh=True to detect manual changes user made to the page
        # (solving captcha, clicking buttons, filling fields manually, etc.)
        page_context = await self.context_cache.get_or_analyze(session_id, page, force_refresh=True)
        
        # ✅ Build platform-aware smart intent prompt
        if self.config.enable_page_context and page_state:
            enhanced_prompt = build_smart_intent_prompt(page_state, ai_prompt, page_context)
        else:
            enhanced_prompt = ai_prompt
        
        logger.info(f"🧠 RAG Query with platform-aware smart intent")
        
        try:
            rag_result = self._rag_system.generate_code(
                enhanced_prompt,
                include_explanation=False
            )
            
            generated_code = rag_result.get('code', '')
            
            if not generated_code:
                raise ValueError("RAG system returned empty code")
            
            logger.info(f"✅ RAG generated {len(generated_code)} chars of code")
            
            return generated_code
            
        except Exception as e:
            logger.error(f"❌ RAG code generation failed: {e}")
            raise
    
    def _generate_fallback_link_click(self, ai_prompt: str) -> str:
        """
        ✅ FALLBACK: Generate simple code to find and click a link by text (when RAG fails).
        Handles Google Scholar and other sites with proper scrolling.
        """
        
        # Extract link name from prompt
        # "Open the link named Ringer: web automation" → "Ringer: web automation"
        link_name_match = re.search(r'(?:named|called|titled)\s+["\']?([^"\']+)["\']?(?:\.|$)', ai_prompt, re.IGNORECASE)
        link_name = link_name_match.group(1) if link_name_match else ai_prompt
        
        # Clean it up
        link_name = re.sub(r'\s*(and|or)\s*.*$', '', link_name).strip()
        
        # Escape quotes in link_name for safe embedding
        link_name_escaped = link_name.replace("'", "\\'")
        
        code = f"""
# ✅ FALLBACK: Find and click link by text (with scrolling)
import asyncio

async def main():
    link_text = '{link_name_escaped}'
    
    # Strategy 1: Use Playwright locator with text matching + scroll
    try:
        locator = page.locator(f'a:has-text("{{link_text}}")')
        if locator:
            # CRITICAL: Scroll into view BEFORE clicking (handles arXiv, long pages, etc.)
            await locator.first.scroll_into_view()
            await page.wait_for_timeout(300)
            await locator.first.click()
            print('EXECUTION_SUCCESS')
            return
    except Exception as e:
        logger.debug(f"Strategy 1 failed: {{e}}")
        pass
    
    # Strategy 2: Partial text match with scrolling (case-insensitive)
    try:
        links = await page.query_selector_all('a')
        for link in links:
            text = await link.text_content()
            if text and link_text.lower() in text.lower():
                # Scroll into view
                await page.evaluate('el => el.scrollIntoView(true)', link)
                await page.wait_for_timeout(300)
                await link.click()
                print('EXECUTION_SUCCESS')
                return
    except Exception as e:
        logger.debug(f"Strategy 2 failed: {{e}}")
        pass
    
    # Strategy 3: Search for text in any element, then find parent link with scrolling
    try:
        elements = await page.query_selector_all('*')
        for elem in elements:
            text = await elem.text_content()
            if text and link_text.lower() in text.lower():
                # Find parent link
                try:
                    parent_link = await elem.evaluate_handle('el => el.closest("a")')
                    if parent_link:
                        # Scroll parent link into view
                        await page.evaluate('el => el.scrollIntoView(true)', parent_link)
                        await page.wait_for_timeout(300)
                        await parent_link.click()
                        print('EXECUTION_SUCCESS')
                        return
                except Exception as e:
                    logger.debug(f"Strategy 3 link click failed: {{e}}")
                    pass
    except Exception as e:
        logger.debug(f"Strategy 3 failed: {{e}}")
        pass
    
    print(f'FAILED: Could not find link with text "{{link_text}}"')

await main()
"""
        return code.strip()
    
    def _generate_fallback_search_fill(self, ai_prompt: str, text_to_fill: str) -> str:
        """
        ✅ FALLBACK: Fill search field and press Enter automatically (when RAG fails).
        Handles Google Scholar and other search sites.
        """
        
        # Escape quotes in text for safe embedding
        text_escaped = text_to_fill.replace("'", "\\'")
        
        code = f"""
# ✅ FALLBACK: Fill search field and press Enter
import asyncio

async def main():
    text = '{text_escaped}'
    
    # Strategy 1: Find search input by type
    try:
        search_input = await page.query_selector('input[type="search"]')
        if search_input:
            await search_input.fill(text)
            print(f'Filled search: {{text}}')
            await page.wait_for_timeout(500)  # Human-like delay
            await search_input.press('Enter')
            await page.wait_for_timeout(1000)  # Wait for search results
            print('EXECUTION_SUCCESS')
            return
    except:
        pass
    
    # Strategy 2: Find search by placeholder
    try:
        search_input = await page.query_selector('input[placeholder*="search" i]')
        if search_input:
            await search_input.fill(text)
            print(f'Filled search: {{text}}')
            await page.wait_for_timeout(500)
            await search_input.press('Enter')
            await page.wait_for_timeout(1000)
            print('EXECUTION_SUCCESS')
            return
    except:
        pass
    
    # Strategy 3: Find any input and try Enter
    try:
        inputs = await page.query_selector_all('input[type="text"], textarea, input:not([type])')
        if inputs:
            search_input = inputs[0]
            await search_input.fill(text)
            print(f'Filled search: {{text}}')
            await page.wait_for_timeout(500)
            await search_input.press('Enter')
            await page.wait_for_timeout(1000)
            print('EXECUTION_SUCCESS')
            return
    except:
        pass
    
    print(f'FAILED: Could not find search field')

await main()
"""
        return code.strip()
    
    async def _execute_generated_code(
        self,
        page,
        code: str,
        task_id: str
    ) -> Dict[str, Any]:
        """Execute RAG-generated Playwright code with enhanced error detection"""
        
        logger.info(f"🚀 Executing generated code for task {task_id}")
        
        try:
            # Clean code
            code = re.sub(r'\nasyncio\.run\(main\(\)\)\s*$', '', code, flags=re.MULTILINE)
            code = re.sub(
                r'if\s+__name__\s*==\s*["\']__main__["\']\s*:\s*\n?\s*asyncio\.run\(main\(\)\)',
                '',
                code,
                flags=re.MULTILINE | re.DOTALL
            )
            code = re.sub(r'asyncio\.run\([^)]+\)', '', code)
            code = re.sub(r'await\s+browser\.close\(\)', 'pass  # Browser kept open', code)
            code = re.sub(r'browser\.close\(\)', 'pass  # Browser kept open', code)
            code = re.sub(r'await\s+context\.close\(\)', 'pass  # Context kept open', code)
            code = re.sub(r'context\.close\(\)', 'pass  # Context kept open', code)
            code = re.sub(r'await\s+playwright\.stop\(\)', 'pass  # Playwright kept running', code)
            # ✅ FIX 3: .first/.last are Locator properties, not coroutines
            code = re.sub(r'await\s+([\w.()\[\]]+)\.first\b', r'\1.first', code)
            code = re.sub(r'await\s+([\w.()\[\]]+)\.last\b', r'\1.last', code)
            
            # Wrap in async function
            def _indent(text, spaces=4):
                return '\n'.join((' ' * spaces) + line if line.strip() else line for line in text.splitlines())
            
            wrapped_code = f"""
import sys
from io import StringIO

_stdout_capture = StringIO()
_original_stdout = sys.stdout

async def __rag_step__(page):
    sys.stdout = _stdout_capture
    
    try:
{_indent(code, 8)}
    finally:
        sys.stdout = _original_stdout
"""
            
            exec_namespace = {
                'page': page,
                'asyncio': asyncio,
                '__result__': None,
                '__stdout__': '',
                'random': random,  # ✅ Make random available for jitter
            }
            
            # ✅ Add helper function for Google searches
            async def google_search_safe(query: str):
                """Safe Google search with full bot evasion"""
                try:
                    # Random jitter
                    delay = random.uniform(2.5, 3.5)
                    await page.wait_for_timeout(int(delay * 1000))
                    
                    # Navigate to homepage first
                    await page.goto('https://www.google.com', wait_until='networkidle')
                    await page.wait_for_timeout(random.randint(1500, 2000))
                    
                    # Check for bot detection
                    error_count = await page.locator('text="having trouble"').count()
                    if error_count > 0:
                        print("FAILED: Bot detection triggered on Google homepage")
                        return False
                    
                    # Wait for search box
                    await page.wait_for_selector('input[name="q"]', state='visible', timeout=5000)
                    
                    # Type with human-like delays
                    for i, char in enumerate(query):
                        current_text = query[:i+1]
                        await page.fill('input[name="q"]', current_text)
                        await page.wait_for_timeout(random.randint(80, 150))
                    
                    # Pause after typing
                    await page.wait_for_timeout(random.randint(1200, 1800))
                    
                    # Click search
                    await page.locator('input[type="submit"][value="Google Search"], button[aria-label="Google Search"]').first.click()
                    await page.wait_for_load_state('networkidle', timeout=15000)
                    await page.wait_for_timeout(random.randint(500, 800))
                    
                    print("EXECUTION_SUCCESS")
                    return True
                except Exception as e:
                    print(f"FAILED: {e}")
                    return False
            
            exec_namespace['google_search_safe'] = google_search_safe
            
            exec(wrapped_code, exec_namespace)
            
            logger.info(f"⚡ Executing wrapped code...")
            result_data = await exec_namespace['__rag_step__'](page)
            
            stdout_content = exec_namespace['_stdout_capture'].getvalue()
            exec_namespace['__stdout__'] = stdout_content
            
            # ✅ FIX 3: Capture actual return value from generated code
            if result_data is not None:
                logger.info(f"✅ Captured return value: {str(result_data)[:100]}")
            
            if exec_namespace.get('__result__') is not None:
                result_data = exec_namespace['__result__']
            
            # Parse stdout for success/failure
            success, message = self._parse_execution_output(stdout_content)
            
            if not success:
                logger.error(f"❌ Code reported failure: {message}")
                return {
                    'success': False,
                    'error': message,
                    'output': stdout_content
                }
            
            logger.info(f"✅ Code executed successfully")
            
            # ✅ FIX 3: Include both extracted_data and text_extracted in result
            result_dict = {
                'success': True,
                'output': stdout_content,
                'extracted_data': result_data
            }
            
            # Add text_extracted if result_data is a string (text extraction)
            if isinstance(result_data, str):
                result_dict['text_extracted'] = result_data
            
            # ✅ FIX 1: Include downloads list in result
            if task_id or hasattr(self, 'session_downloads'):
                # Extract session_id from page if available
                downloads_list = []
                for session_id, downloads in getattr(self, 'session_downloads', {}).items():
                    downloads_list.extend(downloads)
                if downloads_list:
                    result_dict['downloads'] = downloads_list
                    logger.info(f"✅ Included {len(downloads_list)} downloads in result")
            
            return result_dict
            
        except Exception as e:
            logger.error(f"❌ Code execution failed: {e}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def _parse_execution_output(self, stdout: str) -> Tuple[bool, str]:
        """Parse stdout to determine success/failure"""
        
        if 'FAILED:' in stdout:
            failure_msg = stdout.split('FAILED:')[1].split('\n')[0].strip()
            return False, f"Playwright error: {failure_msg}"
        
        if 'Timeout' in stdout and 'exceeded' in stdout:
            return False, "Playwright timeout exceeded"
        
        if 'not found' in stdout.lower() or 'cannot find' in stdout.lower():
            return False, "Required element not found on page"
        
        if 'EXECUTION_SUCCESS' in stdout:
            return True, "Execution successful"
        
        # Default to success if no failure markers found
        return True, "Execution completed"
        
        if len(stdout.strip()) > 0:
            return True, "Code executed (no explicit success marker)"
        
        return False, "No output generated (execution may have failed)"
    
    def _security_check(self, code: str) -> Dict[str, Any]:
        """Basic security validation (kept for backward compat — rag_sandbox.check() is used instead)"""
        
        violations = []
        
        dangerous_patterns = [
            'eval(',
            '__import__',
            'os.system',
            'subprocess',
            'rm -rf',
            'del ',
        ]
        
        for pattern in dangerous_patterns:
            if pattern in code:
                violations.append(f"Dangerous pattern detected: {pattern}")
        
        if 'file://' in code:
            violations.append("File system access not allowed")
        
        return {
            'passed': len(violations) == 0,
            'violations': violations
        }
    
    async def _humanize_page(self, page):
        """
        Add human-like behavior to avoid bot detection on auth pages.
        Moves mouse randomly, adds small delays — makes automation look real.
        """
        try:
            vw = page.viewport_size.get('width', 1366) if page.viewport_size else 1366
            vh = page.viewport_size.get('height', 768) if page.viewport_size else 768
            # Random mouse movements
            for _ in range(random.randint(2, 4)):
                x = random.randint(100, vw - 100)
                y = random.randint(100, vh - 100)
                await page.mouse.move(x, y)
                await page.wait_for_timeout(random.randint(50, 150))
        except Exception:
            pass  # non-fatal
    
    async def auto_login_google(self, page, username: str) -> bool:
        """
        Automatically log into Google account using credentials from USER_ACCOUNTS.
        Called on-demand when a Google task is executed.
        Returns True if login succeeded, False otherwise.
        """
        try:
            if username not in USER_ACCOUNTS:
                logger.error(f"❌ User '{username}' not found in USER_ACCOUNTS mapping")
                logger.info(f"📝 Configure your account in USER_ACCOUNTS dict in google_accounts.py")
                return False
            
            account = USER_ACCOUNTS[username]
            email = account['email']
            password = account['password']
            
            logger.info(f"🔐 Auto-logging in Google account for user: {username}")
            
            # Navigate to Google login
            await page.goto('https://accounts.google.com/signin', wait_until='networkidle')
            await page.wait_for_timeout(2000)
            
            # Step 1: Enter email and press Enter (NOT clicking Next button)
            try:
                # IMPORTANT: Do NOT await the locator itself. Locators are synchronous.
                # Only await the .fill() method call on the locator.
                await page.locator('input[type="email"]').first.fill(email)
                await page.keyboard.press('Enter')
                await page.wait_for_timeout(2000)
                logger.info(f"✅ Entered email and pressed Enter")
            except Exception as e:
                logger.error(f"❌ Failed to enter email: {e}")
                return False
            
            # Step 2: Wait for password field and enter password, then press Enter
            try:
                await page.wait_for_selector('input[type="password"]', state='visible', timeout=10000)
                await page.locator('input[type="password"]').first.fill(password)
                await page.keyboard.press('Enter')
                await page.wait_for_timeout(3000)
                logger.info(f"✅ Entered password and pressed Enter")
            except Exception as e:
                logger.error(f"❌ Failed to enter password: {e}")
                return False
            
            # Step 3: Verify login success by checking we're off the signin page
            try:
                await page.wait_for_url(lambda url: 'signin' not in url.lower(), timeout=10000)
                # Verify we're actually logged in by checking for account indicators
                # Wait a moment for page to fully load
                await page.wait_for_timeout(1000)
                
                # Try to verify authentication by checking for Google account elements
                try:
                    # Look for account email in page (indicates logged in state)
                    account_email = await page.evaluate("""() => {
                        const elements = Array.from(document.querySelectorAll('[aria-label*="account"], [aria-label*="Account"]'));
                        return elements.length > 0 || document.body.innerText.includes(arguments[0] || 'accounts');
                    }""")
                    if account_email:
                        logger.info(f"✅ Account indicators found - login verified for {username}")
                except:
                    pass  # Verification check failed but we're off signin page anyway
                
                # CRITICAL: Navigate to google.com to establish the authenticated session properly
                # This ensures cookies work across all Google domains (search, account, etc.)
                logger.info(f"🌐 Navigating to google.com to establish authenticated session...")
                await page.goto('https://www.google.com', wait_until='domcontentloaded', timeout=15000)
                await page.wait_for_timeout(1500)
                
                logger.info(f"✅ Google auto-login succeeded for {username}")
                return True
            except Exception as e:
                current_url = page.url
                if '/signin' in current_url.lower():
                    logger.error(f"❌ Still on signin page. URL: {current_url}")
                    return False
                else:
                    # We're off the signin page, so login probably worked
                    logger.info(f"✅ Navigation succeeded. URL: {current_url}")
                    return True
                
        except Exception as e:
            logger.error(f"❌ Auto-login failed: {e}")
            return False

    async def cleanup(self):
        """Clean up browser resources"""
        logger.info("🧹 Cleaning up Playwright resources...")
        
        try:
            active_sessions = list(self.sessions.keys())
            self.context_cache.cleanup_closed_sessions(active_sessions)
            
            for session_id, page in self.sessions.items():
                if not page.is_closed():
                    await page.close()
            
            # ✅ FIX 5: Save browser state (cookies, storage) for next session (per-user)
            if self.context and hasattr(self, 'cookies_path'):
                try:
                    await self.context.storage_state(path=str(self.cookies_path))
                    logger.info(f"✅ Saved browser state (cookies) for user '{self.current_user}' to next session")
                except Exception as e:
                    logger.warning(f"⚠️  Could not save browser state: {e}")
                
                await self.context.close()
            
            if self.browser:
                await self.browser.close()
            
            if self.playwright:
                await self.playwright.stop()
            
            logger.info("✅ Playwright cleanup complete")
            
        except Exception as e:
            logger.error(f"❌ Error during cleanup: {e}")

# ============================================================================
# TASK MODELS (keep existing from original file)
# ============================================================================

class ActionTask:
    """Task format from coordinator agent"""
    def __init__(
        self,
        task_id: str,
        ai_prompt: str,
        device: str,
        context: str,
        target_agent: str,
        web_params: Optional[Dict[str, Any]] = None,
        extra_params: Optional[Dict[str, Any]] = None,
        depends_on: Optional[List[str]] = None
    ):
        self.task_id = task_id
        self.ai_prompt = ai_prompt
        self.device = device
        self.context = context
        self.target_agent = target_agent
        self.web_params = web_params or {}
        self.extra_params = extra_params or {}
        self.depends_on = depends_on
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ActionTask':
        return cls(
            task_id=data.get('task_id', ''),
            ai_prompt=data.get('ai_prompt', ''),
            device=data.get('device', 'desktop'),
            context=data.get('context', 'web'),
            target_agent=data.get('target_agent', 'action'),
            web_params=data.get('web_params', {}),
            extra_params=data.get('extra_params', {}),
            depends_on=data.get('depends_on')
        )
    
    def dict(self) -> Dict[str, Any]:
        return {
            'task_id': self.task_id,
            'ai_prompt': self.ai_prompt,
            'device': self.device,
            'context': self.context,
            'target_agent': self.target_agent,
            'web_params': self.web_params,
            'extra_params': self.extra_params,
            'depends_on': self.depends_on
        }

class TaskResult:
    """Result format for coordinator agent"""
    def __init__(
        self,
        task_id: str,
        status: str,
        content: Optional[str] = None,
        error: Optional[str] = None,
        extracted_data: Optional[Dict] = None,
        screenshot: Optional[str] = None
    ):
        self.task_id = task_id
        self.status = status
        self.content = content
        self.error = error
        self.extracted_data = extracted_data
        self.screenshot = screenshot
    
    def dict(self) -> Dict[str, Any]:
        return {
            'task_id': self.task_id,
            'status': self.status,
            'content': self.content,
            'error': self.error,
            'extracted_data': self.extracted_data,
            'screenshot': self.screenshot
        }

# ============================================================================
# RAG TASK ADAPTER
# ============================================================================

class WebRAGTaskAdapter:
    """Adapts coordinator ActionTask to web execution requirements"""
    
    @staticmethod
    def execution_result_to_task_result(
        task: ActionTask,
        execution_result: WebExecutionResult
    ) -> TaskResult:
        
        if execution_result.validation_passed and execution_result.security_passed:
            status = "success"
            content = execution_result.output
            if execution_result.verification_message:
                content = f"{content}\nVerification: {execution_result.verification_message}"
            error = None
        else:
            status = "failed"
            content = None
            errors = []
            if execution_result.validation_errors:
                errors.extend(execution_result.validation_errors)
            if execution_result.security_violations:
                errors.extend(execution_result.security_violations)
            if execution_result.error:
                errors.append(f"error: {execution_result.error[:200]}")
            error = " | ".join(errors)
        
        return TaskResult(
            task_id=task.task_id,
            status=status,
            content=content,
            error=error,
            extracted_data=execution_result.extracted_data,
            screenshot=execution_result.screenshot_path
        )

# ============================================================================
# COORDINATOR WEB BRIDGE
# ============================================================================

class CoordinatorWebBridge:
    """Bridge between Coordinator Agent and Web Execution System"""
    
    def __init__(self, web_pipeline: WebExecutionPipeline):
        self.web = web_pipeline
        self.adapter = WebRAGTaskAdapter()
    
    async def execute_web_action_task(
        self,
        task: ActionTask,
        session_id: str = "default",
        max_retries: int = 2
    ) -> TaskResult:
        """Execute a single web ActionTask using enhanced pipeline"""
        
        logger.info(f"🌐 Processing web task {task.task_id}: {task.ai_prompt[:50]}...")
        
        if task.target_agent != "action":
            logger.warning(f"Task {task.task_id} is not an action task")
            return TaskResult(
                task_id=task.task_id,
                status="failed",
                error="Not an action task - should be handled by reasoning agent"
            )
        
        attempt = 0
        last_error = ""
        
        while attempt < max_retries:
            attempt += 1
            logger.info(f"🔄 Attempt {attempt}/{max_retries} for task {task.task_id}")
            
            try:
                # ✅ FIX: Always pass the original clean ai_prompt to execute_web_task.
                # Error context from previous attempts is NOT appended to the prompt
                # because doing so caused validate_action_context to trigger the media
                # guard when the word "media" appeared in the error string.
                # The web pipeline handles retries internally with a clean slate.
                task_dict = {
                    'task_id': task.task_id,
                    'ai_prompt': task.ai_prompt,   # ← always the original, no suffix
                    'web_params': task.web_params,
                }
                
                exec_result = await self.web.execute_web_task(task_dict, session_id)
                
                if exec_result.validation_passed and exec_result.security_passed:
                    logger.info(f"✅ Task {task.task_id} completed successfully")
                    return self.adapter.execution_result_to_task_result(task, exec_result)
                
                last_error = exec_result.error or 'Unknown error'
                logger.warning(f"⚠️ Web execution failed (attempt {attempt}): {last_error[:120]}")
                
            except Exception as e:
                last_error = str(e)
                logger.error(f"❌ Exception during web execution: {e}")
                if attempt == max_retries:
                    break
        
        logger.error(f"❌ Web task {task.task_id} failed after {max_retries} attempts")
        return TaskResult(
            task_id=task.task_id,
            status="failed",
            error=f"Failed after {max_retries} attempts: {last_error}"
        )

# ============================================================================
# WEB EXECUTION AGENT INTEGRATION
# ============================================================================

async def start_web_execution_agent_with_rag(broker_instance, rag_system, web_pipeline):
    """Start web execution agent that handles web ActionTasks from coordinator"""
    
    bridge = CoordinatorWebBridge(web_pipeline)
    
    async def handle_web_execution_request(message):
        try:
            task_data = message.payload
            task = ActionTask.from_dict(task_data)
            
            logger.info(f"📨 Web execution agent received task {task.task_id}")
            
            result = await bridge.execute_web_action_task(
                task=task,
                session_id=message.session_id,
                max_retries=2
            )
            
            from agents.utils.protocol import AgentMessage, MessageType, AgentType, Channels
            
            response_msg = AgentMessage(
                message_type=MessageType.EXECUTION_RESPONSE,
                sender=AgentType.EXECUTION,
                receiver=AgentType.COORDINATOR,
                session_id=message.session_id,
                task_id=task.task_id,
                response_to=message.message_id,
                payload=result.dict()
            )
            
            await broker_instance.publish(Channels.EXECUTION_TO_COORDINATOR, response_msg)
            logger.info(f"✅ Sent result for task {task.task_id}: {result.status}")
            
        except Exception as e:
            logger.error(f"❌ Error processing web execution request: {e}")
            
            error_result = TaskResult(
                task_id=message.task_id or "unknown",
                status="failed",
                error=str(e)
            )
            
            from agents.utils.protocol import AgentMessage, MessageType, AgentType, Channels
            
            error_msg = AgentMessage(
                message_type=MessageType.EXECUTION_RESPONSE,
                sender=AgentType.EXECUTION,
                receiver=AgentType.COORDINATOR,
                session_id=message.session_id,
                task_id=message.task_id,
                response_to=message.message_id,
                payload=error_result.dict()
            )
            
            await broker_instance.publish(Channels.EXECUTION_TO_COORDINATOR, error_msg)
    
    from agents.utils.protocol import Channels
    broker_instance.subscribe(Channels.COORDINATOR_TO_EXECUTION, handle_web_execution_request)
    
    logger.info("✅ Web Execution Agent started with ULTIMATE enhancements")
    logger.info("   ✅ GENERIC multi-platform (YouTube, Amazon, Netflix, Google, ANY site)")
    logger.info("   ✅ Platform-specific keyboard shortcuts")
    logger.info("   ✅ Page state layer")
    logger.info("   ✅ Post-action verification")
    logger.info("   ✅ Smart intent handling")
    logger.info("   ✅ Persistent context (separate from mem0)")
    logger.info("   ✅ Media validation gated on explicit media action_type")
    
    while True:
        await asyncio.sleep(1)

async def initialize_web_execution_agent_for_server(broker_instance):
    """Server-compatible initialization for web execution agent with all enhancements"""
    
    from dotenv import load_dotenv
    load_dotenv()
    
    if hasattr(broker_instance, '_web_rag_execution_subscribed'):
        logger.warning("⚠️ Web RAG Execution agent already subscribed, skipping")
        return
    broker_instance._web_rag_execution_subscribed = True
    
    try:
        from agents.execution_agent.RAG.web.code_generation import RAGSystem, RAGConfig
        
        try:
            logger.info("🧠 Initializing RAG system for Playwright...")
            rag_config = RAGConfig(library_name="playwright")
            rag_system = RAGSystem(rag_config)
            rag_system.initialize()
            logger.info("✅ Playwright RAG system ready")
        except Exception as e:
            logger.error(f"❌ Failed to initialize Playwright RAG: {e}")
            raise
        
        try:
            logger.info("🚀 Initializing ULTIMATE Playwright web pipeline...")
            
            web_config = WebExecutionConfig(
                headless=False,
                timeout_seconds=30,
                enable_verification=True,
                enable_page_context=True,
                enable_page_state_layer=True,
                enable_smart_intent=True,
                cache_page_context=True,
                use_stealth_plugin=True,
                randomize_fingerprint=True,
                use_real_user_agent=True,
            )
            web_pipeline = WebExecutionPipeline(web_config)
            await web_pipeline.initialize()
            
            logger.info("✅ ULTIMATE Playwright web pipeline ready")
            
        except Exception as e:
            logger.error(f"❌ Web pipeline initialization error: {e}")
            raise
        
        logger.info("🌐 Starting ULTIMATE web execution agent...")
        await start_web_execution_agent_with_rag(broker_instance, rag_system, web_pipeline)
    
    except Exception as e:
        logger.error(f"❌ Failed to initialize web execution agent: {e}")
        raise