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
import subprocess
import threading
import time as _time_module
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
import hashlib
from .web_rag_sandbox import rag_sandbox
from .bot_evasion import BotEvasion, ProxyRotator
from .verification import ScreenshotVerifier

# ✅ Phase 4: OAuth integration for cookie injection
try:
    from agents.api_agent import ApiAgent
    _API_AGENT_AVAILABLE = True
except ImportError:
    _API_AGENT_AVAILABLE = False

logger = logging.getLogger(__name__)

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

    # Launch + fallback strategy
    google_launch_mode: str = field(default_factory=lambda: os.getenv("GOOGLE_LAUNCH_MODE", "installed_chrome").strip().lower())
    google_auth_safe_mode: bool = field(default_factory=lambda: os.getenv("GOOGLE_AUTH_SAFE_MODE", "true").strip().lower() in {"1", "true", "yes", "on"})
    enable_visual_fallback: bool = field(default_factory=lambda: os.getenv("ENABLE_VISUAL_FALLBACK", "true").strip().lower() in {"1", "true", "yes", "on"})
    omniparser_confidence_threshold: float = field(default_factory=lambda: float(os.getenv("OMNIPARSER_CONFIDENCE_THRESHOLD", "0.65")))
    google_recovery_max_retries: int = field(default_factory=lambda: int(os.getenv("GOOGLE_RECOVERY_MAX_RETRIES", "2")))
    google_manual_login_timeout_ms: int = field(default_factory=lambda: int(os.getenv("GOOGLE_MANUAL_LOGIN_TIMEOUT_MS", "45000")))

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
        elif 'arxiv.org' in url:
            platform = 'arxiv'
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
                    const contentType = document.contentType || '';
                    const hasVideo = !!document.querySelector('video');
                    const hasAudio = !!document.querySelector('audio');
                    const hasSearch = !!document.querySelector('[type="search"], [role="search"], input[placeholder*="search" i]');
                    const hasCart = !!document.querySelector('[data-testid*="cart" i], [aria-label*="cart" i], .cart, #cart, [id*="cart" i]');
                    const hasPrices = !!document.querySelector('[data-price], .price, [class*="price" i], [aria-label*="price" i]');
                    const hasProducts = !!document.querySelector('[data-product], [class*="product" i], [data-testid*="product" i]');
                    const isPdf = contentType.toLowerCase().includes('pdf') || window.location.pathname.toLowerCase().endsWith('.pdf') || window.location.pathname.toLowerCase().includes('/pdf/');
                    
                    return {
                        hasVideo,
                        hasAudio,
                        hasSearch,
                        hasCart,
                        hasPrices,
                        hasProducts,
                        isPdf
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

        if site_info.get('isPdf') or url.endswith('.pdf') or '/pdf/' in url:
            site_type = 'pdf'
            capabilities.append('pdf_viewer')
        else:
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
            'url': page.url,
            'isPdf': site_info.get('isPdf', False)
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
                const isVisible = (el) => {
                    if (!el) return false;
                    const r = el.getBoundingClientRect();
                    const s = getComputedStyle(el);
                    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0';
                };

                const modalCandidates = Array.from(document.querySelectorAll(
                    '[role="dialog"], [aria-modal="true"], .modal, [class*="modal"], [class*="overlay"], [class*="popup"], [class*="pop-up"], [class*="lightbox"], [class*="backdrop"]'
                ));
                const visibleModals = modalCandidates.filter(isVisible);

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
                        hasModals: visibleModals.length > 0,
                        modalCount: modalCandidates.length,
                        visibleModalCount: visibleModals.length,
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
        state['isPdf'] = bool(site_info.get('isPdf')) or 'pdf_viewer' in state.get('capabilities', [])
        
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
        'modal_changed': False,
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

    interactive_before = before.get('interactive') or {}
    interactive_after = after.get('interactive') or {}
    modal_before = interactive_before.get('visibleModalCount')
    modal_after = interactive_after.get('visibleModalCount')
    if modal_before is not None and modal_after is not None:
        changes['modal_changed'] = modal_before != modal_after
        changes['modal_details'] = {
            'before': modal_before,
            'after': modal_after,
        }
    elif isinstance(interactive_before, dict) and isinstance(interactive_after, dict):
        has_modals_before = interactive_before.get('hasModals')
        has_modals_after = interactive_after.get('hasModals')
        if has_modals_before is not None and has_modals_after is not None:
            changes['modal_changed'] = has_modals_before != has_modals_after
            changes['modal_details'] = {
                'before': has_modals_before,
                'after': has_modals_after,
            }
    
    changes['any_change'] = any([
        changes['url_changed'],
        changes['media_state_changed'],
        changes['focus_changed'],
        changes['modal_changed'],
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

0. **CRITICAL — IN-PAGE NAVIGATION**:
   The browser is ALREADY on URL: {page_context.get('url', 'unknown')}
   If the task's target site matches the current domain, do NOT call page.goto().
   Instead, interact with the page: type in search boxes, click links, press Enter.
   For back-navigation, use: await page.go_back()
   After any click that triggers navigation: await page.wait_for_load_state('domcontentloaded', timeout=10000)

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

    @staticmethod
    def get_auth_safe_launch_args() -> List[str]:
        """Get a safer launch profile for authentication-heavy flows."""
        base_args = StealthBrowser.get_stealth_launch_args()
        remove_args = {
            '--disable-web-security',
            '--disable-features=IsolateOrigins,site-per-process',
            '--disable-gpu',
            '--disable-gpu-sandbox',
            '--disable-gpu-compositing',
        }
        return [arg for arg in base_args if arg not in remove_args]

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
        
        # ✅ Bot Evasion & Verification
        self.bot_evasion = BotEvasion()
        self.proxy_rotator = self._initialize_proxy_rotator()
        self.screenshot_verifier = ScreenshotVerifier(output_dir=config.screenshot_dir)
        self.active_launch_mode = "unknown"
        self.last_google_auth_block_reason = None
        self._google_profile_client = None
        self._google_profile_collection = None
        self._session_google_auth_hints: Dict[str, Dict[str, str]] = {}
        self._init_google_profile_store()

        # Lightweight telemetry to tune fallback behavior safely.
        self.stats = {
            "dom_success": 0,
            "visual_fallback_used": 0,
            "google_block_detected": 0,
            "google_recovery_attempted": 0,
            "google_recovery_succeeded": 0,
        }

        self.omniparser_detector = None
        if self.config.enable_visual_fallback:
            try:
                from agents.execution_agent.fallback.omniparser_detector import OmniParserDetector
                self.omniparser_detector = OmniParserDetector(logger)
                logger.info("✅ OmniParser visual fallback enabled")
            except Exception as e:
                logger.warning(f"⚠️ OmniParser fallback unavailable: {e}")
                self.omniparser_detector = None
        else:
            logger.info("ℹ️ OmniParser visual fallback disabled by config")
        
        Path(self.config.screenshot_dir).mkdir(parents=True, exist_ok=True)

    def _init_google_profile_store(self):
        """Initialize MongoDB-backed store for per-user Google defaults."""
        mongo_uri = os.getenv("MONGODB_URI", "").strip()
        if not mongo_uri:
            logger.info("ℹ️ MONGODB_URI not set; Google default-email persistence disabled")
            return

        try:
            from pymongo import MongoClient
        except Exception as e:
            logger.warning(f"⚠️ PyMongo unavailable; Google default-email persistence disabled: {e}")
            return

        try:
            db_name = os.getenv("MONGODB_DB_NAME", "yusr_db").strip() or "yusr_db"
            collection_name = os.getenv("GOOGLE_PROFILE_COLLECTION", "google_user_profiles").strip() or "google_user_profiles"
            self._google_profile_client = MongoClient(mongo_uri, serverSelectionTimeoutMS=3000)
            self._google_profile_collection = self._google_profile_client[db_name][collection_name]
            self._google_profile_collection.create_index("user_key", unique=True)
            logger.info(f"✅ Google profile store ready: {db_name}.{collection_name}")
        except Exception as e:
            logger.warning(f"⚠️ Failed to initialize Google profile store: {e}")
            self._google_profile_client = None
            self._google_profile_collection = None

    def _resolve_google_user_key(self, task: Dict[str, Any], session_id: str) -> str:
        """Resolve stable user key for Google account preferences."""
        extra_params = task.get("extra_params") or {}
        if not isinstance(extra_params, dict):
            extra_params = {}

        candidate = str(extra_params.get("user_id") or task.get("user_id") or "").strip()
        if candidate and candidate.lower() not in {"unknown", "default_user", "none", "null"}:
            return candidate

        if session_id and session_id != "default":
            return f"session:{session_id}"

        return str(getattr(self, "current_user", "") or get_current_system_user())

    def _is_switch_account_prompt(self, prompt_text: str) -> bool:
        prompt = (prompt_text or "").lower()
        switch_markers = [
            "different account",
            "another account",
            "switch account",
            "use another gmail",
            "use another google account",
            "login with a different",
            "log in with a different",
            "sign in with a different",
        ]
        return any(marker in prompt for marker in switch_markers)

    def _is_set_default_account_prompt(self, prompt_text: str) -> bool:
        prompt = (prompt_text or "").lower()
        set_default_markers = [
            "set as default",
            "make this default",
            "use this as default",
            "save this account as default",
            "remember this account",
        ]
        return any(marker in prompt for marker in set_default_markers)

    def _extract_google_credentials(self, task: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
        """Extract email/password hints from task payload and prompt text."""
        extra_params = task.get("extra_params") or {}
        if not isinstance(extra_params, dict):
            extra_params = {}
        web_params = task.get("web_params") or {}
        if not isinstance(web_params, dict):
            web_params = {}

        email = None
        password = None

        for key in ("google_email", "email", "username", "login_email"):
            value = extra_params.get(key)
            if isinstance(value, str) and "@" in value:
                email = value.strip()
                break

        for key in ("google_password", "password", "pass", "pwd"):
            value = extra_params.get(key)
            if isinstance(value, str) and value.strip():
                password = value.strip()
                break

        prompt_text = str(task.get("ai_prompt", ""))
        prompt_text_lower = prompt_text.lower()
        web_action = str(web_params.get("action", "")).strip().lower()
        web_text = str(web_params.get("text", "")).strip()

        # Some login decompositions pass credentials as web_params.text (fill/type tasks).
        if web_text:
            if not email and "@" in web_text:
                email_match = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", web_text)
                if email_match:
                    email = email_match.group(0).strip()

            if not password and web_action in {"fill", "type"} and "password" in prompt_text_lower:
                password = web_text

        if not email:
            email_match = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", prompt_text)
            if email_match:
                email = email_match.group(0).strip()

        if not password:
            password_patterns = [
                r"password\s*(?:is|=|:)\s*['\"]([^'\"]+)['\"]",
                r"password\s*(?:is|=|:)\s*([^\s,;]+)",
                r"pass(?:word)?\s*['\"]([^'\"]+)['\"]",
                r"password(?:\s+field)?\s+(?:with|as)\s*['\"]?([^'\"\s,;]+)",
                r"with\s+password\s*['\"]?([^'\"\s,;]+)",
            ]
            for pattern in password_patterns:
                match = re.search(pattern, prompt_text, re.IGNORECASE)
                if match and match.group(1).strip():
                    password = match.group(1).strip()
                    break

        return email, password

    def _update_session_google_auth_hints(
        self,
        session_id: str,
        email: Optional[str] = None,
        password: Optional[str] = None,
    ) -> None:
        """Store partial Google auth hints across clarification turns."""
        if not session_id:
            return
        hints = self._session_google_auth_hints.get(session_id, {})
        if isinstance(email, str) and "@" in email:
            hints["email"] = email.strip()
        if isinstance(password, str) and password.strip():
            hints["password"] = password.strip()
        if hints:
            self._session_google_auth_hints[session_id] = hints

    def _clear_session_google_auth_hints(self, session_id: str, clear_email: bool = False) -> None:
        """Clear cached auth hints for a session (always clears password)."""
        if not session_id:
            return
        hints = self._session_google_auth_hints.get(session_id)
        if not hints:
            return
        hints.pop("password", None)
        if clear_email:
            hints.pop("email", None)
        if hints:
            self._session_google_auth_hints[session_id] = hints
        else:
            self._session_google_auth_hints.pop(session_id, None)

    async def _get_google_cookies_for_user(self, user_id: Optional[str]) -> Optional[List[Dict[str, Any]]]:
        """
        ✅ Phase 4a: Get Google session cookies from OAuth token via ApiAgent.
        
        Returns Playwright-compatible cookie list if OAuth tokens available, else None.
        This enables seamless browser automation on Google without requiring login prompts.
        """
        if not user_id or not _API_AGENT_AVAILABLE:
            return None
        
        try:
            agent = ApiAgent()
            result = await agent.get_browser_cookies(user_id)
            
            if result.get('status') == 'success' and result.get('cookies'):
                logger.info(f"✅ Retrieved {len(result['cookies'])} Google OAuth cookies for user {user_id}")
                return result['cookies']
            else:
                error = result.get('error', 'Unknown error')
                logger.debug(f"⚠️ Could not get OAuth cookies for user {user_id}: {error}")
                return None
        except Exception as e:
            logger.debug(f"⚠️ Exception getting OAuth cookies for user {user_id}: {e}")
            return None

    async def _refresh_google_cookies_if_needed(self, session_id: str, user_id: Optional[str], page) -> bool:
        """
        ✅ Phase 4c: Refresh Google OAuth cookies if >45 minutes old.
        
        Returns True if cookies were refreshed, False otherwise.
        """
        if not session_id or not user_id:
            return False
        
        hints = self._session_google_auth_hints.get(session_id, {})
        injected_at_str = hints.get("oauth_injected_at")
        
        if not injected_at_str:
            return False
        
        try:
            injected_at = datetime.fromisoformat(injected_at_str)
            age_minutes = (datetime.now() - injected_at).total_seconds() / 60
            
            if age_minutes < 45:
                logger.debug(f"✅ OAuth cookies still fresh ({age_minutes:.1f} min old)")
                return False
            
            logger.info(f"🔄 OAuth cookies expired ({age_minutes:.1f} min old) - refreshing")
            oauth_cookies = await self._get_google_cookies_for_user(user_id)
            
            if oauth_cookies:
                try:
                    await self.context.clear_cookies()
                    await self.context.add_cookies(oauth_cookies)
                    # Re-navigate to re-authenticate with new cookies
                    try:
                        await page.goto('https://www.google.com', wait_until='load', timeout=10000)
                        await page.wait_for_timeout(300)
                    except Exception as e:
                        logger.debug(f"Could not re-navigate after cookie refresh: {e}")
                    
                    self._update_session_google_auth_hints(session_id, oauth_injected_at=datetime.now().isoformat())
                    logger.info(f"✅ Refreshed Google OAuth cookies for user {user_id}")
                    return True
                except Exception as e:
                    logger.debug(f"⚠️ Could not apply refreshed OAuth cookies: {e}")
                    return False
            else:
                logger.debug(f"⚠️ Could not get refreshed OAuth cookies for user {user_id}")
                return False
        except Exception as e:
            logger.debug(f"⚠️ Exception checking/refreshing OAuth cookies: {e}")
            return False

    async def _detect_google_identifier_hint(self, page) -> Optional[str]:
        """Try to infer the active Google identifier from the sign-in page state."""
        try:
            candidates = await page.evaluate(
                """() => {
                    const out = [];
                    const push = (v) => {
                        if (typeof v === 'string') {
                            const s = v.trim();
                            if (s.includes('@')) out.push(s);
                        }
                    };

                    const emailInput = document.querySelector('input[type="email"], #identifierId, input[name="identifier"]');
                    if (emailInput) {
                        push(emailInput.value);
                        push(emailInput.getAttribute('value'));
                    }

                    const passwordInput = document.querySelector('input[type="password"], input[name="Passwd"]');
                    if (passwordInput) {
                        const chips = document.querySelectorAll('[data-email], [data-identifier], [aria-label]');
                        chips.forEach((el) => {
                            push(el.getAttribute('data-email'));
                            push(el.getAttribute('data-identifier'));
                            push(el.getAttribute('aria-label'));
                        });
                    }

                    return out.slice(0, 20);
                }"""
            )
        except Exception:
            return None

        email_regex = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
        for candidate in candidates or []:
            match = email_regex.search(str(candidate))
            if match:
                return match.group(0).strip().lower()
        return None

    def _google_secure_browser_auth_required_message(self) -> str:
        return (
            "AUTH_REQUIRED: Google blocked automated sign-in with 'This browser or app may not be secure'. "
            "Please complete Google login manually in the opened browser window, then tell me to continue."
        )

    async def _detect_google_secure_browser_warning(self, page) -> bool:
        """Detect Google's secure-browser warning on sign-in flows."""
        try:
            current_url = (page.url or "").lower()
            if "google" not in current_url:
                return False

            title = ((await page.title()) or "").lower()
            page_text = await page.evaluate("() => document.body && document.body.innerText || ''")
            text = (page_text or "").lower()

            indicators = [
                "this browser or app may not be secure",
                "try using a different browser",
                "you can try again to sign in",
                "browser not secure",
            ]

            return any(indicator in text for indicator in indicators) or any(indicator in title for indicator in indicators)
        except Exception as e:
            logger.debug(f"⚠️ Secure-browser warning detection skipped: {e}")
            return False

    async def _is_google_password_step(self, page) -> bool:
        """Return True when Google sign-in password field is truly visible and interactable."""
        try:
            return bool(
                await page.evaluate(
                    """() => {
                        const pw = document.querySelector('input[type="password"], input[name="Passwd"]');
                        if (!pw) return false;
                        const style = window.getComputedStyle(pw);
                        return style.display !== 'none' && style.visibility !== 'hidden' && pw.offsetWidth > 0 && pw.offsetHeight > 0;
                    }"""
                )
            )
        except Exception:
            return False

    async def _type_google_field_like_user(self, page, selector: str, text: str, timeout: int = 8000) -> bool:
        """Type into Google auth fields in a way that triggers frontend listeners reliably."""
        try:
            await page.wait_for_selector(selector, state='visible', timeout=timeout)
            locator = page.locator(selector).first
            await locator.click()
            await page.wait_for_timeout(250)
            await locator.press('Control+A')
            await locator.press('Backspace')
            await locator.type(text, delay=80)
            await page.wait_for_timeout(450)
            return True
        except Exception:
            return False

    async def _read_google_field_value(self, page, selector: str) -> str:
        """Best-effort read of current input value for Google auth fields."""
        try:
            selectors = [s.strip() for s in selector.split(',') if s.strip()]
            for sel in selectors:
                try:
                    loc = page.locator(sel).first
                    if await loc.count() > 0 and await loc.is_visible():
                        return (await loc.input_value()) or ""
                except Exception:
                    continue

            # Fallback to first locator if visibility checks fail.
            return (await page.locator(selectors[0]).first.input_value()) if selectors else ""
        except Exception:
            return ""

    async def _set_google_field_value_verified(
        self,
        page,
        selector: str,
        expected: str,
        is_email: bool = False,
        timeout: int = 8000,
    ) -> bool:
        """Set a Google input field and verify the value really landed."""
        target = (expected or "").strip()
        if not target:
            return False

        selectors = [s.strip() for s in selector.split(',') if s.strip()]
        if not selectors:
            return False

        resolved_selector = None
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if await loc.count() > 0 and await loc.is_visible():
                    resolved_selector = sel
                    break
            except Exception:
                continue
        if not resolved_selector:
            resolved_selector = selectors[0]

        def _matches(actual: str) -> bool:
            a = (actual or "").strip()
            if is_email:
                return a.lower() == target.lower()
            return a == target

        # Attempt 1: Human-like typing.
        await self._type_google_field_like_user(page, resolved_selector, target, timeout=timeout)
        current = await self._read_google_field_value(page, resolved_selector)
        if _matches(current):
            # Blur the field to trigger validation and animations
            await page.locator(resolved_selector).first.blur()
            await page.wait_for_timeout(500)  # Wait for animations/transitions
            return True

        # Attempt 2: Direct fill.
        try:
            await page.wait_for_selector(resolved_selector, state='visible', timeout=timeout)
            loc = page.locator(resolved_selector).first
            await loc.click()
            await loc.fill(target)
            await page.wait_for_timeout(300)
        except Exception:
            pass
        current = await self._read_google_field_value(page, resolved_selector)
        if _matches(current):
            # Blur the field to trigger validation and animations
            await page.locator(resolved_selector).first.blur()
            await page.wait_for_timeout(500)  # Wait for animations/transitions
            return True

        # Attempt 3: JS value set + event dispatch.
        try:
            await page.evaluate(
                """([sel, val]) => {
                    const el = document.querySelector(sel);
                    if (!el) return false;
                    el.focus();
                    el.value = val;
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    el.blur();  // Blur to trigger validation
                    return true;
                }""",
                [resolved_selector, target],
            )
            await page.wait_for_timeout(500)  # Wait for animations/transitions
        except Exception:
            pass

        current = await self._read_google_field_value(page, resolved_selector)
        if not _matches(current):
            logger.warning(
                f"⚠️ Google field value mismatch for selector={resolved_selector}. expected={target!r}, actual={current!r}"
            )
            return False
        return True

    async def _advance_google_identifier_step(self, page) -> bool:
        """
        Click Next/submit on Google identifier step and confirm progress.

        Google's bot detection can silently neutralise Playwright's synthetic
        mouse-click events, leaving the page completely frozen (buttons rendered
        but non-functional).  We therefore try FOUR independent strategies in
        order, stopping as soon as the password step appears:

          1. JavaScript dispatchEvent (MouseEvent) — bypasses Playwright CDP layer
          2. Tab-to-button + Enter via keyboard — pure keyboard, no mouse events
          3. page.evaluate form.submit() — submits the underlying form directly
          4. Original locator.click() — kept as last-resort for edge cases
        """

        async def _password_appeared() -> bool:
            """Poll until password field is visible (up to 20 s)."""
            for _ in range(20):
                if await self._is_google_password_step(page):
                    return True
                await page.wait_for_timeout(1000)
            return False

        async def _check_for_errors() -> bool:
            """Return True if Google is showing a login-error message."""
            error_selectors = [
                '.Ekjuhf',           # Common Google error class
                '[data-error]',
                '.GQ8Pgb',
                'div[role="alert"]',
            ]
            for error_sel in error_selectors:
                try:
                    error_el = page.locator(error_sel).first
                    if await error_el.count() > 0 and await error_el.is_visible():
                        error_text = await error_el.text_content()
                        if error_text and error_text.strip():
                            logger.warning(f"⚠️ Google login error detected: {error_text.strip()}")
                            return True
                except Exception:
                    continue
            return False

        # ── Strategy 1: JS dispatchEvent on #identifierNext ───────────────
        logger.info("🔑 Google Next: trying JS dispatchEvent strategy")
        try:
            dispatched = await page.evaluate("""
                () => {
                    const btn = document.querySelector('#identifierNext') ||
                                document.querySelector('#identifierNext button') ||
                                Array.from(document.querySelectorAll('button')).find(
                                    b => b.textContent.trim().toLowerCase() === 'next'
                                );
                    if (!btn) return false;
                    // Simulate the full pointer-event chain Google listens to
                    ['pointerover','pointerenter','mouseover','mouseenter',
                     'pointermove','mousemove',
                     'pointerdown','mousedown',
                     'pointerup','mouseup',
                     'click'].forEach(evtName => {
                        btn.dispatchEvent(new MouseEvent(evtName, {
                            bubbles: true, cancelable: true, view: window
                        }));
                    });
                    return true;
                }
            """)
            if dispatched:
                await page.wait_for_timeout(1500)
                if not await _check_for_errors() and await _password_appeared():
                    logger.info("✅ Google Next: JS dispatchEvent succeeded")
                    return True
        except Exception as e:
            logger.debug(f"Strategy 1 (JS dispatch) failed: {e}")

        # ── Strategy 2: Tab to the Next button, then press Enter ──────────
        logger.info("🔑 Google Next: trying Tab+Enter keyboard strategy")
        try:
            # Focus the email field first so Tab moves to Next
            email_field = page.locator('#identifierId, input[name="identifier"], input[type="email"]').first
            if await email_field.count() > 0:
                await email_field.click()
                await page.wait_for_timeout(200)
            await page.keyboard.press('Tab')
            await page.wait_for_timeout(300)
            await page.keyboard.press('Enter')
            await page.wait_for_timeout(1500)
            if not await _check_for_errors() and await _password_appeared():
                logger.info("✅ Google Next: Tab+Enter succeeded")
                return True
        except Exception as e:
            logger.debug(f"Strategy 2 (Tab+Enter) failed: {e}")

        # ── Strategy 3: Submit the underlying form via JS ─────────────────
        logger.info("🔑 Google Next: trying JS form.requestSubmit() strategy")
        try:
            submitted = await page.evaluate("""
                () => {
                    const input = document.querySelector('#identifierId') ||
                                  document.querySelector('input[type="email"]');
                    if (!input) return false;
                    const form = input.closest('form');
                    if (!form) return false;
                    // requestSubmit fires validation; fallback to submit()
                    if (typeof form.requestSubmit === 'function') {
                        form.requestSubmit();
                    } else {
                        form.submit();
                    }
                    return true;
                }
            """)
            if submitted:
                await page.wait_for_timeout(1500)
                if not await _check_for_errors() and await _password_appeared():
                    logger.info("✅ Google Next: form.requestSubmit() succeeded")
                    return True
        except Exception as e:
            logger.debug(f"Strategy 3 (form submit) failed: {e}")

        # ── Strategy 4: Classic Playwright locator.click() (original) ─────
        logger.info("🔑 Google Next: falling back to locator.click()")
        next_selectors = [
            '#identifierNext button',
            '#identifierNext',
            'button:has-text("Next")',
        ]
        clicked = False
        for next_selector in next_selectors:
            try:
                next_btn = page.locator(next_selector).first
                if await next_btn.count() > 0:
                    await next_btn.click(timeout=5000)
                    clicked = True
                    break
            except Exception:
                continue

        if not clicked:
            try:
                await page.keyboard.press('Enter')
            except Exception:
                return False

        await page.wait_for_timeout(1000)
        if await _check_for_errors():
            logger.warning("⚠️ Error message detected after Next click")
            return False

        result = await _password_appeared()
        if result:
            logger.info("✅ Google Next: locator.click() fallback succeeded")
        else:
            logger.warning("⚠️ All four Next-button strategies exhausted without reaching password step")
        return result

    async def _submit_google_login_with_credentials(self, page, email: Optional[str], password: str) -> bool:
        """Fill Google signin fields and submit using provided credentials."""
        try:
            self.last_google_auth_block_reason = None

            if "accounts.google.com" not in (page.url or "").lower():
                await page.goto('https://accounts.google.com/signin', wait_until='domcontentloaded', timeout=15000)
                await page.wait_for_timeout(500)

            if await self._detect_google_secure_browser_warning(page):
                logger.warning("⚠️ Google secure-browser warning detected before credential submission")
                self.last_google_auth_block_reason = "browser_not_secure"
                return False

            # Prioritize Google's canonical selectors first to avoid hidden/auxiliary fields.
            email_selector = '#identifierId, input[name="identifier"], input[type="email"]'
            password_selector = 'input[name="Passwd"], input[type="password"]'

            if email:
                try:
                    email_set = await self._set_google_field_value_verified(
                        page,
                        email_selector,
                        email,
                        is_email=True,
                        timeout=8000,
                    )
                    if not email_set:
                        logger.warning("⚠️ Could not reliably set Google email field before Next")
                        return False

                    # Wait for any UI animations/transitions after setting email
                    await page.wait_for_timeout(1000)

                    progressed = await self._advance_google_identifier_step(page)
                    if not progressed:
                        # One retry: re-apply email and attempt step advance again.
                        logger.warning("⚠️ Google did not advance after first Next click; retrying identifier step")
                        email_set_retry = await self._set_google_field_value_verified(
                            page,
                            email_selector,
                            email,
                            is_email=True,
                            timeout=8000,
                        )
                        if not email_set_retry:
                            return False
                        # Wait again for animations
                        await page.wait_for_timeout(1000)
                        progressed = await self._advance_google_identifier_step(page)
                        if not progressed:
                            logger.warning("⚠️ Google identifier step still did not advance after retry")
                            return False

                    await page.wait_for_timeout(2000)
                    try:
                        await page.wait_for_load_state('networkidle', timeout=10000)
                    except Exception:
                        pass

                    await page.wait_for_function(
                        """() => {
                            const pw = document.querySelector('input[type="password"], input[name="Passwd"]');
                            if (!pw) return false;
                            const style = window.getComputedStyle(pw);
                            return style.display !== 'none' && style.visibility !== 'hidden' && pw.offsetWidth > 0 && pw.offsetHeight > 0;
                        }""",
                        timeout=15000,
                    )

                    if await self._detect_google_secure_browser_warning(page):
                        logger.warning("⚠️ Google secure-browser warning detected after email step")
                        self.last_google_auth_block_reason = "browser_not_secure"
                        return False
                except Exception:
                    # If email field is skipped (e.g., already on password step), continue.
                    pass

            await page.wait_for_function(
                """() => {
                    const pw = document.querySelector('input[type="password"], input[name="Passwd"]');
                    if (!pw) return false;
                    const style = window.getComputedStyle(pw);
                    return style.display !== 'none' && style.visibility !== 'hidden' && pw.offsetWidth > 0 && pw.offsetHeight > 0;
                }""",
                timeout=15000,
            )
            password_set = await self._set_google_field_value_verified(
                page,
                password_selector,
                password,
                is_email=False,
                timeout=9000,
            )
            if not password_set:
                logger.warning("⚠️ Could not reliably set Google password field")
                return False
            await page.keyboard.press('Enter')
            await page.wait_for_timeout(700)

            if await self._detect_google_secure_browser_warning(page):
                logger.warning("⚠️ Google secure-browser warning detected after password submission")
                self.last_google_auth_block_reason = "browser_not_secure"
                return False

            for _ in range(12):
                if await self._detect_google_secure_browser_warning(page):
                    logger.warning("⚠️ Google secure-browser warning detected while waiting for auth completion")
                    self.last_google_auth_block_reason = "browser_not_secure"
                    return False
                if await self._is_google_login_complete(page):
                    try:
                        await page.goto('https://www.google.com', wait_until='domcontentloaded', timeout=15000)
                    except Exception:
                        pass
                    return True
                await page.wait_for_timeout(1000)

            return False
        except Exception as e:
            logger.warning(f"⚠️ Automated Google login with credentials failed: {e}")
            return False

    def _get_saved_google_default_email(self, user_key: str) -> Optional[str]:
        if self._google_profile_collection is None:
            return None
        try:
            doc = self._google_profile_collection.find_one(
                {"user_key": user_key},
                {"_id": 0, "default_email": 1}
            )
            email = (doc or {}).get("default_email")
            if isinstance(email, str) and "@" in email:
                return email.strip()
        except Exception as e:
            logger.warning(f"⚠️ Failed reading saved Google email for '{user_key}': {e}")
        return None

    def _save_google_default_email(self, user_key: str, email: str, source: str = "manual_login") -> bool:
        if self._google_profile_collection is None:
            return False
        normalized = (email or "").strip().lower()
        if "@" not in normalized:
            return False
        try:
            self._google_profile_collection.update_one(
                {"user_key": user_key},
                {
                    "$set": {
                        "default_email": normalized,
                        "updated_at": datetime.now().isoformat(),
                        "source": source,
                    }
                },
                upsert=True,
            )
            logger.info(f"💾 Saved default Google email for '{user_key}': {normalized}")
            return True
        except Exception as e:
            logger.warning(f"⚠️ Failed saving default Google email for '{user_key}': {e}")
            return False

    async def _prefill_google_email(self, page, email: str) -> bool:
        """Auto-fill saved email in Google sign-in page and continue to password step."""
        try:
            selector = 'input[type="email"], #identifierId, input[name="identifier"]'
            typed = await self._type_google_field_like_user(page, selector, email, timeout=7000)
            if not typed:
                await page.wait_for_selector(selector, state='visible', timeout=7000)
                await page.locator(selector).first.fill(email)
            await page.keyboard.press('Enter')
            await page.wait_for_timeout(1200)
            logger.info(f"✍️ Auto-filled saved Google email: {email}")
            return True
        except Exception as e:
            logger.debug(f"Could not auto-fill saved email: {e}")
            return False

    async def _google_has_session_cookie(self) -> bool:
        """Check for known Google auth cookies in current browser context."""
        if not self.context:
            return False
        try:
            cookies = await self.context.cookies(["https://accounts.google.com", "https://www.google.com"])
            names = {c.get("name", "") for c in cookies}
            auth_names = {
                "SID", "HSID", "SSID", "SAPISID", "APISID",
                "__Secure-1PSID", "__Secure-3PSID",
            }
            return any(name in names for name in auth_names)
        except Exception:
            return False

    async def _is_google_login_complete(self, page) -> bool:
        """Return True when we appear to be authenticated with Google."""
        try:
            current_url = (page.url or "").lower()

            has_auth_cookie = await self._google_has_session_cookie()
            if has_auth_cookie and "accounts.google.com" not in current_url:
                return True

            if "accounts.google.com" in current_url and "signin" in current_url:
                has_login_form = await page.evaluate(
                    """() => !!document.querySelector('input[type="email"], input[type="password"], #identifierId')"""
                )
                if has_login_form:
                    return False

            account_ui = await page.evaluate(
                """() => {
                    const selectors = [
                        'a[aria-label*="Google Account" i]',
                        'a[href*="SignOutOptions"]',
                        '[data-ogsr-up]'
                    ];
                    return selectors.some(s => !!document.querySelector(s));
                }"""
            )

            return bool(account_ui or has_auth_cookie)
        except Exception:
            return False

    async def _wait_for_manual_google_login(self, page, timeout_ms: int) -> bool:
        """Wait for user to complete Google sign-in manually."""
        timeout_ms = max(3000, int(timeout_ms))
        deadline = asyncio.get_event_loop().time() + (timeout_ms / 1000.0)

        while asyncio.get_event_loop().time() < deadline:
            if await self._is_google_login_complete(page):
                return True
            await page.wait_for_timeout(1000)

        return False

    async def _capture_logged_in_google_email(self, page) -> Optional[str]:
        """Try to capture signed-in Google account email from current/account pages."""
        probe_urls = [
            "https://accounts.google.com/AccountChooser",
            "https://myaccount.google.com/",
        ]

        email_regex = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

        for url in probe_urls:
            try:
                await page.goto(url, wait_until='domcontentloaded', timeout=15000)
                await page.wait_for_timeout(700)
                candidates = await page.evaluate(
                    """() => {
                        const found = new Set();
                        const push = (value) => {
                            if (typeof value === 'string' && value.includes('@')) {
                                found.add(value.trim());
                            }
                        };

                        document.querySelectorAll('[data-email], [data-identifier]').forEach(el => {
                            push(el.getAttribute('data-email'));
                            push(el.getAttribute('data-identifier'));
                        });

                        document.querySelectorAll('[aria-label]').forEach(el => {
                            push(el.getAttribute('aria-label'));
                        });

                        const bodyText = (document.body && document.body.innerText) || '';
                        const parts = bodyText.split(/\\s+/).filter(Boolean);
                        for (const p of parts) {
                            if (p.includes('@')) {
                                push(p);
                            }
                        }

                        return Array.from(found).slice(0, 40);
                    }"""
                )
            except Exception:
                continue

            if not candidates:
                continue

            for candidate in candidates:
                match = email_regex.search(str(candidate))
                if match:
                    return match.group(0).lower()

        return None
    
    async def _apply_bot_evasion_before_request(self, page, session_id: str):
        """Apply bot evasion techniques before making requests"""
        try:
            # Apply behavioral randomization
            if random.random() < 0.7:  # 70% chance
                delay = BotEvasion.random_delay()
                await page.wait_for_timeout(int(delay * 1000))
                logger.debug(f"⏳ Applied behavioral delay: {delay:.2f}s")
            
            # Apply fingerprint spoofing script
            try:
                spoof_script = BotEvasion.get_fingerprint_spoof_script()
                await page.evaluate(spoof_script)
                logger.debug("✅ Applied fingerprint spoofing script")
            except Exception as e:
                logger.debug(f"⚠️ Fingerprint spoofing failed (non-critical): {e}")
            
            # Apply proxy if rotator is configured
            if self.proxy_rotator:
                proxy = self.proxy_rotator.get_proxy_for_session(session_id)
                if proxy:
                    logger.info(f"🔄 Using proxy: {proxy}")
                    # Note: Proxy must be set via Playwright context, not per-page
                    # This would require recreating the context with new proxy
                    # For now, just track it for logging
        
        except Exception as e:
            logger.warning(f"⚠️ Bot evasion setup failed: {e}")
    
    async def _apply_mouse_evasion(self, page, selector: str):
        """Apply Bezier curve mouse movement before clicking"""
        try:
            locator = page.locator(selector)
            box = await locator.bounding_box()
            if not box:
                return
            
            # Calculate element center
            target_x = int(box['x'] + box['width'] / 2)
            target_y = int(box['y'] + box['height'] / 2)
            
            # Get current cursor position (if possible)
            try:
                pos = await page.evaluate("() => ({x: window.innerWidth * 0.5, y: window.innerHeight * 0.5})")
                start_x, start_y = int(pos['x']), int(pos['y'])
            except:
                start_x, start_y = 0, 0
            
            # Generate Bezier path
            path = BotEvasion.bezier_curve_path((start_x, start_y), (target_x, target_y), steps=30)
            
            # Move mouse along path
            for x, y in path[::3]:  # Sample every 3rd point
                await page.mouse.move(x, y)
                await page.wait_for_timeout(random.randint(10, 30))
            
            logger.debug(f"✅ Applied Bezier mouse movement to {selector}")
        
        except Exception as e:
            logger.debug(f"⚠️ Mouse evasion failed (non-critical): {e}")
    
    async def _check_bot_block(self, page) -> bool:
        """Check if page shows bot detection/rate limiting"""
        try:
            # Check for Google's bot block page
            if await BotEvasion.detect_google_bot_block(page):
                logger.warning("🚨 Google bot block detected!")
                return True
            
            # Check HTTP status
            status = getattr(page, 'response', None)
            if status and hasattr(status, 'status'):
                if await BotEvasion.detect_rate_limit(status.status):
                    logger.warning("🚨 Rate limiting detected!")
                    return True
            
            return False
        
        except Exception as e:
            logger.debug(f"⚠️ Bot block detection failed: {e}")
            return False

    def _is_google_auth_prompt(self, prompt_text: str) -> bool:
        prompt = (prompt_text or "").lower()
        auth_keywords = [
            "sign in",
            "login",
            "log in",
            "authenticate",
            "account",
            "gmail login",
            "google login",
        ]
        return any(keyword in prompt for keyword in auth_keywords)

    async def _detect_google_interstitial(self, page) -> bool:
        """Detect common Google block/interstitial states that require bounded recovery."""
        try:
            current_url = (page.url or "").lower()
            page_text = await page.evaluate("() => document.body && document.body.innerText || ''")
            text = (page_text or "").lower()

            indicators = [
                "unusual traffic",
                "about this page",
                "having trouble accessing google search",
                "our systems have detected unusual traffic",
                "sorry/index",
                "verify you are human",
            ]

            if any(indicator in text for indicator in indicators):
                return True

            if "google.com/sorry" in current_url or "/sorry/index" in current_url:
                return True

            return False
        except Exception as e:
            logger.debug(f"⚠️ Google interstitial detection skipped: {e}")
            return False

    async def _recover_google_interstitial(self, page) -> bool:
        """Bounded recovery sequence for Google interstitial pages."""
        max_retries = max(0, int(self.config.google_recovery_max_retries))
        if max_retries == 0:
            return False

        self.stats["google_recovery_attempted"] += 1
        for attempt in range(1, max_retries + 1):
            try:
                await page.wait_for_timeout(random.randint(1500, 3000))
                await page.goto('https://www.google.com', wait_until='domcontentloaded', timeout=15000)
                await page.wait_for_timeout(random.randint(800, 1500))

                blocked = await self._detect_google_interstitial(page)
                if not blocked:
                    self.stats["google_recovery_succeeded"] += 1
                    logger.info(f"✅ Google recovery succeeded on attempt {attempt}/{max_retries}")
                    return True
            except Exception as e:
                logger.debug(f"⚠️ Google recovery attempt {attempt} failed: {e}")

        return False

    async def _try_visual_fallback_action(self, page, task: Dict[str, Any], task_id: str) -> Optional[Dict[str, Any]]:
        """Use OmniParser only when DOM execution fails."""
        if not self.config.enable_visual_fallback or self.omniparser_detector is None:
            return None

        web_params = task.get('web_params', {}) or {}
        action_type = str(web_params.get('action', 'unknown')).lower()
        if action_type not in {"click", "fill", "type", "submit"}:
            return None

        target_text = str(web_params.get('text') or web_params.get('selector') or task.get('ai_prompt', '')).strip()
        if not target_text:
            return None

        try:
            screenshot_path = os.path.join(
                self.config.screenshot_dir,
                f"{task_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_visual_fallback.png"
            )
            await page.screenshot(path=screenshot_path)

            loop = asyncio.get_event_loop()
            vision_result = await loop.run_in_executor(
                None,
                lambda: self.omniparser_detector.detect_element_by_text(target_text, screenshot_path)
            )

            if not vision_result or not getattr(vision_result, "success", False):
                return None

            confidence = float(getattr(vision_result, "confidence", 0.0) or 0.0)
            threshold = float(self.config.omniparser_confidence_threshold)
            coords = getattr(vision_result, "coordinates", None)

            auth_sensitive = any(k in (task.get('ai_prompt', '').lower()) for k in ["password", "otp", "verification", "2fa", "sign in", "login"])
            if confidence < threshold:
                if auth_sensitive:
                    return {
                        "success": False,
                        "error": f"Visual fallback confidence too low for auth action ({confidence:.2f} < {threshold:.2f}). Manual intervention required."
                    }
                return None

            if not coords or len(coords) < 2:
                return None

            x, y = int(coords[0]), int(coords[1])
            await page.mouse.click(x, y)
            await page.wait_for_timeout(random.randint(200, 450))

            typed_text = str(web_params.get('text') or '')
            if action_type in {"fill", "type"} and typed_text:
                await page.keyboard.type(typed_text, delay=random.randint(40, 90))
                await page.keyboard.press("Enter")

            self.stats["visual_fallback_used"] += 1
            return {
                "success": True,
                "output": "EXECUTION_SUCCESS (visual_fallback)",
                "extracted_data": {
                    "method": "omniparser",
                    "confidence": confidence,
                    "coordinates": [x, y],
                    "target_text": target_text,
                }
            }
        except Exception as e:
            logger.warning(f"⚠️ Visual fallback failed: {e}")
            return None

    def _is_dismiss_popup_prompt(self, prompt_text: str) -> bool:
        text = (prompt_text or "").lower()
        return any(k in text for k in ["dismiss", "close", "popup", "pop up", "modal", "overlay", "banner", "no thanks", "not now"])

    async def _dom_has_close_control(self, page) -> bool:
        if not page:
            return False
        try:
            return await page.evaluate("""
                () => {
                    const keywords = ['close', 'dismiss', 'no thanks', 'not now', 'cancel'];
                    const isVisible = (el) => {
                        if (!el) return false;
                        const r = el.getBoundingClientRect();
                        const s = getComputedStyle(el);
                        return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0';
                    };
                    const nodes = Array.from(document.querySelectorAll('button, [role="button"], [aria-label], [title], [data-action], [data-testid]'));
                    for (const el of nodes) {
                        if (!isVisible(el)) continue;
                        const text = (el.innerText || el.textContent || el.getAttribute('aria-label') || el.getAttribute('title') || '').trim().toLowerCase();
                        if (!text) continue;
                        if (keywords.some(k => text.includes(k)) || text === 'x') {
                            return true;
                        }
                    }
                    return false;
                }
            """)
        except Exception:
            return False

    async def _try_visual_fallback_popup_dismiss(self, page, task: Dict[str, Any], task_id: str) -> Optional[Dict[str, Any]]:
        if not self.config.enable_visual_fallback or self.omniparser_detector is None:
            return None

        candidates = ["dismiss", "close", "no thanks", "not now", "x"]
        for candidate in candidates:
            task_override = dict(task)
            task_override["web_params"] = dict(task.get("web_params", {}) or {})
            task_override["web_params"]["text"] = candidate
            logger.info(f"🔍 Visual fallback popup dismiss using target '{candidate}'")
            result = await self._try_visual_fallback_action(page, task_override, task_id)
            if result is not None:
                return result

        return None
    
    def _initialize_proxy_rotator(self) -> Optional[ProxyRotator]:
        """Initialize proxy rotator if proxies are configured"""
        try:
            proxy_pool_str = os.getenv("PROXY_POOL", "").strip()
            if not proxy_pool_str:
                logger.info("ℹ️ No proxies configured (PROXY_POOL not set)")
                return None
            
            proxy_pool = [p.strip() for p in proxy_pool_str.split(",") if p.strip()]
            if not proxy_pool:
                logger.warning("⚠️ PROXY_POOL env var is empty")
                return None
            
            strategy_str = os.getenv("PROXY_STRATEGY", "round_robin").lower()
            from .bot_evasion import ProxyRotationStrategy
            try:
                strategy = ProxyRotationStrategy[strategy_str.upper()]
            except KeyError:
                logger.warning(f"⚠️ Unknown proxy strategy '{strategy_str}', using ROUND_ROBIN")
                strategy = ProxyRotationStrategy.ROUND_ROBIN
            
            rotator = ProxyRotator(proxy_pool=proxy_pool, strategy=strategy)
            logger.info(f"✅ ProxyRotator initialized with {len(proxy_pool)} proxies")
            return rotator
        
        except Exception as e:
            logger.warning(f"⚠️ Failed to initialize proxy rotator: {e}")
            return None
    
    async def initialize(self):
        """Mark pipeline as ready — actual browser launch is deferred to first use."""
        self._initialized = False
        logger.info("✅ Web pipeline ready (browser will launch on first task)")

    async def _ensure_browser(self):
        """Lazy-launch the browser on first use."""
        if self._initialized and self.context is not None:
            return
        self._initialized = False
        try:
            await self._do_initialize()
            self._initialized = True
        except Exception as e:
            logger.error(f"❌ Failed to initialize Playwright: {e}")
            self._initialized = False
            self.context = None
            self.browser = None
            raise

    async def _do_initialize(self):
        """Initialize Playwright — launch real Chrome via CDP or fall back."""
        try:
            from playwright.async_api import async_playwright
            import subprocess
            import socket
            
            logger.info("🚀 Initializing Playwright...")
            
            self.playwright = await async_playwright().start()

            self.current_user = get_current_system_user()
            self.profile_dir = Path(os.getenv(
                "CHROME_PROFILE_DIR",
                str(Path(__file__).parent / 'chrome_profile')
            ))
            # Keep cookies_path for backward compatibility (other code checks hasattr)
            self.cookies_path = self.profile_dir / 'playwright_state.json'
            self._chrome_process = None  # Track Chrome subprocess for cleanup

            # ── CDP MODE: Launch REAL Chrome, connect via DevTools Protocol ──
            # This is a genuine Chrome process — zero automation fingerprints.
            # Google cannot distinguish it from a human-launched Chrome.
            # On first run, chrome_profile/ is created automatically — user logs
            # in manually once, and the session persists for all future runs.
            cdp_port = int(os.getenv("CHROME_CDP_PORT", "9222"))
            cdp_launched = False

            # Always try CDP — create profile dir if it doesn't exist yet
            self.profile_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"📂 Launching real Chrome with profile at {self.profile_dir}")
            try:
                # Find Chrome executable
                chrome_path = os.getenv("CHROME_EXECUTABLE_PATH", "").strip()
                if not chrome_path:
                    # Standard Chrome install paths on Windows
                    for candidate in [
                        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
                        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
                        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
                    ]:
                        if os.path.isfile(candidate):
                            chrome_path = candidate
                            break

                if chrome_path and os.path.isfile(chrome_path):
                    # Check if CDP port is already in use
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    port_in_use = sock.connect_ex(('127.0.0.1', cdp_port)) == 0
                    sock.close()

                    if not port_in_use:
                        chrome_args = [
                            chrome_path,
                            f'--remote-debugging-port={cdp_port}',
                            f'--user-data-dir={self.profile_dir}',
                            '--no-first-run',
                            '--no-default-browser-check',
                        ]
                        logger.info(f"🔧 Starting Chrome: {chrome_path} on CDP port {cdp_port}")
                        self._chrome_process = subprocess.Popen(
                            chrome_args,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                        # Wait for Chrome to start and open the debugging port
                        for _attempt in range(30):
                            await asyncio.sleep(0.5)
                            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                            if sock.connect_ex(('127.0.0.1', cdp_port)) == 0:
                                sock.close()
                                break
                            sock.close()
                        else:
                            logger.warning("⚠️ Chrome did not open CDP port in time")

                    # Connect Playwright to the real Chrome via CDP
                    self.browser = await self.playwright.chromium.connect_over_cdp(
                        f'http://127.0.0.1:{cdp_port}'
                    )
                    # Get the default context (the one Chrome created)
                    if self.browser.contexts:
                        self.context = self.browser.contexts[0]
                    else:
                        self.context = await self.browser.new_context()

                    # Fix: configure browser-wide download behaviour for the CDP
                    # session so that manual *and* automated downloads save to the
                    # real Downloads folder with their original filenames instead of
                    # being intercepted by Playwright into a temp dir with UUIDs.
                    try:
                        _downloads_dir = str(Path.home() / 'Downloads')
                        os.makedirs(_downloads_dir, exist_ok=True)
                        _cdp = await self.browser.new_browser_cdp_session()
                        await _cdp.send('Browser.setDownloadBehavior', {
                            'behavior': 'allow',
                            'downloadPath': _downloads_dir,
                            'eventsEnabled': True,
                        })

                        # Track downloads and auto-open when complete
                        _cdp_downloads: dict = {}

                        def _on_download_will_begin(event):
                            guid = event.get('guid', '')
                            filename = event.get('suggestedFilename', 'download')
                            filepath = os.path.join(_downloads_dir, filename)
                            _cdp_downloads[guid] = filepath
                            logger.info(f"🔽 CDP Download starting: {filename}")

                        def _on_download_progress(event):
                            guid = event.get('guid', '')
                            state = event.get('state', '')
                            if state == 'completed' and guid in _cdp_downloads:
                                filepath = _cdp_downloads.pop(guid)
                                logger.info(f"✅ CDP Download completed: {filepath}")
                                # Track in all active sessions
                                for sid in list(self.sessions.keys()):
                                    if sid not in self.session_downloads:
                                        self.session_downloads[sid] = []
                                    self.session_downloads[sid].append(filepath)
                                # Open the file in a background thread after waiting for the OS
                                # to finish writing it (CDP 'completed' fires before file is flushed)
                                def _wait_and_open(fp):
                                    deadline = _time_module.time() + 15
                                    while _time_module.time() < deadline:
                                        if os.path.exists(fp) and os.path.getsize(fp) > 0:
                                            break
                                        _time_module.sleep(0.5)
                                    if not os.path.exists(fp):
                                        logger.warning(f"⚠️ Download file not found: {fp}")
                                        return
                                    if os.path.getsize(fp) == 0:
                                        logger.warning(f"⚠️ Download file still 0 bytes after wait: {fp}")
                                        return
                                    try:
                                        subprocess.run(
                                            ['cmd', '/c', 'start', '', os.path.normpath(fp)],
                                            shell=False, check=False,
                                        )
                                        logger.info(f"📂 Opened downloaded file: {fp}")
                                    except Exception as _open_err:
                                        logger.warning(f"⚠️ Could not open downloaded file: {_open_err}")
                                threading.Thread(target=_wait_and_open, args=(filepath,), daemon=True).start()

                        _cdp.on('Browser.downloadWillBegin', _on_download_will_begin)
                        _cdp.on('Browser.downloadProgress', _on_download_progress)
                        logger.info(f"✅ CDP download behaviour → {_downloads_dir}")
                    except Exception as _dl_err:
                        logger.warning(f"⚠️ Could not configure CDP downloads: {_dl_err}")

                    self.active_launch_mode = "real_chrome_cdp"
                    cdp_launched = True
                    logger.info(f"✅ Connected to real Chrome via CDP on port {cdp_port}")
                else:
                    logger.warning(f"⚠️ Chrome executable not found; falling back to Playwright launch")
            except Exception as e:
                logger.warning(f"⚠️ CDP launch failed ({e}); falling back to Playwright launch")
                self.context = None
                self.browser = None
                cdp_launched = False

            # Fallback: standard Playwright launch (fresh browser, no real profile)
            if not cdp_launched:
                logger.warning(
                    "⚠️ CDP not available — falling back to Playwright-managed browser. "
                    "Google may block with bot detection."
                )
                launch_args = (
                    self.stealth.get_auth_safe_launch_args()
                    if self.config.google_auth_safe_mode
                    else self.stealth.get_stealth_launch_args()
                )
                launch_kwargs = {
                    'headless': self.config.headless,
                    'slow_mo': self.config.slow_mo,
                    'args': launch_args,
                }
                launch_mode = (self.config.google_launch_mode or "installed_chrome").lower()
                if launch_mode == "installed_chrome":
                    chrome_channel = os.getenv("CHROME_CHANNEL", "chrome").strip() or "chrome"
                    self.browser = await self.playwright.chromium.launch(**launch_kwargs, channel=chrome_channel)
                else:
                    self.browser = await self.playwright.chromium.launch(**launch_kwargs)
                self.active_launch_mode = "fallback_fresh_browser"
                logger.info(f"✅ Browser launch mode active: {self.active_launch_mode}")

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
                self.context = await self.browser.new_context(**context_options)
            
            # Inject essential cookies (YouTube/Google consent) immediately
            # so they're available for ALL navigations in this context
            try:
                import time as _ess_time
                await self.context.add_cookies([
                    {
                        'name': 'SOCS',
                        'value': 'CAESEwgDEgk2MTkwNTcyNjAaAmVuIAEaBgiA_LyaBg',
                        'domain': '.youtube.com',
                        'path': '/',
                        'expires': float(_ess_time.time() + 63072000),
                        'secure': True,
                        'sameSite': 'Lax',
                    },
                    {
                        'name': 'SOCS',
                        'value': 'CAESEwgDEgk2MTkwNTcyNjAaAmVuIAEaBgiA_LyaBg',
                        'domain': '.google.com',
                        'path': '/',
                        'expires': float(_ess_time.time() + 63072000),
                        'secure': True,
                        'sameSite': 'Lax',
                    },
                    {
                        'name': 'PREF',
                        'value': 'tz=America.New_York&f6=40000000&f7=100',
                        'domain': '.youtube.com',
                        'path': '/',
                        'expires': float(_ess_time.time() + 63072000),
                    },
                ])
                logger.info("✅ Injected essential Google/YouTube consent cookies into context")
            except Exception as _ess_err:
                logger.debug(f"⚠️ Could not inject essential cookies at context creation: {_ess_err}")
            
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
                current_domain_lower = current_domain.lower()

                # Keep Google authentication traffic close to native browser behavior.
                is_google_auth_host = (
                    'accounts.google.com' in current_domain_lower
                    or 'myaccount.google.com' in current_domain_lower
                )
                if self.config.google_auth_safe_mode and is_google_auth_host:
                    await route.continue_(headers=headers)
                    return
                
                # ✅ FIX: Google search requires Referer header to not trigger bot detection
                # If referer is missing on search operations, set Google's homepage as referer
                if not referer or not referer_domain:
                    if 'google' in current_domain_lower and '/search' in current_url:
                        headers['Referer'] = 'https://www.google.com/'
                    elif 'google' in current_domain_lower:
                        headers['Referer'] = 'https://www.google.com/'
                # Only set Referer if cross-site; otherwise let browser default
                elif referer_domain and referer_domain != current_domain:
                    headers['Referer'] = referer  # Preserve original cross-site referer
                
                # ✅ FIX: Add critical headers Google checks for real browsers
                headers['Origin'] = '/'.join(current_url.split('/')[:3])
                
                # Ensure Google sees real Accept headers
                if 'google' in current_domain_lower:
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
            
            # In CDP mode, Chrome handles headers/referer natively — no interception needed.
            # Stealth scripts are also counterproductive: real Chrome already passes all checks.
            if self.active_launch_mode == "real_chrome_cdp":
                logger.info("✅ Real Chrome via CDP — skipping stealth scripts and request interception (not needed)")
            else:
                await self.context.route('**/*', handle_route)
                
                if self.config.use_stealth_plugin:
                    await self.stealth.inject_stealth_scripts(self.context)
                
                logger.info("✅ Stealth Playwright initialized (route interception + stealth scripts active)")
            
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

    async def _inject_essential_cookies(self, page):
        """
        Inject cookies that Google/YouTube require for basic functionality
        in a fresh browser context. Without these, YouTube shows a consent
        wall and pages render as blank skeletons.
        """
        import time as _time
        try:
            essential = [
                # YouTube/Google consent cookie — bypasses GDPR consent wall
                {
                    'name': 'SOCS',
                    'value': 'CAESEwgDEgk2MTkwNTcyNjAaAmVuIAEaBgiA_LyaBg',
                    'domain': '.youtube.com',
                    'path': '/',
                    'expires': float(_time.time() + 63072000),
                    'secure': True,
                    'sameSite': 'Lax',
                },
                {
                    'name': 'SOCS',
                    'value': 'CAESEwgDEgk2MTkwNTcyNjAaAmVuIAEaBgiA_LyaBg',
                    'domain': '.google.com',
                    'path': '/',
                    'expires': float(_time.time() + 63072000),
                    'secure': True,
                    'sameSite': 'Lax',
                },
                # Preference cookie — tells YouTube we accept English, no consent needed
                {
                    'name': 'PREF',
                    'value': 'tz=America.New_York&f6=40000000&f7=100',
                    'domain': '.youtube.com',
                    'path': '/',
                    'expires': float(_time.time() + 63072000),
                },
            ]
            await self.context.add_cookies(essential)
            logger.debug("✅ Injected essential YouTube/Google consent cookies")
        except Exception as e:
            logger.debug(f"⚠️ Could not inject essential cookies: {e}")

    async def get_or_create_page(self, session_id: str):
        """Get existing page for session or create new one with async download handling"""
        # Lazy-launch browser on first use
        await self._ensure_browser()

        existing = self.sessions.get(session_id)
        page_truly_closed = await self._is_page_truly_closed(existing)

        # ── FIX (Bug 1): When the stored page is dead but a CDP context
        # already has open pages, reuse the *last* (foreground) page instead
        # of opening a brand-new about:blank tab — this preserves session
        # cookies, history, and avoids bot-detection.
        if page_truly_closed and self.context and self.context.pages:
            # Pick the last page (most recently focused tab)
            candidate = self.context.pages[-1]
            if not await self._is_page_truly_closed(candidate):
                logger.info(f"♻️ Reusing existing foreground tab for session {session_id}: {candidate.url}")
                self.sessions[session_id] = candidate
                return candidate

        if page_truly_closed:
            try:
                page = await self.context.new_page()
            except Exception as e:
                if 'closed' in str(e).lower():
                    logger.warning(f"⚠️ Browser context dead — reconnecting...")
                    self._initialized = False
                    self.context = None
                    self.browser = None
                    await self._ensure_browser()
                    page = await self.context.new_page()
                else:
                    raise
            
            # Apply playwright-stealth only in non-CDP mode (real Chrome doesn't need it)
            if self.active_launch_mode != "real_chrome_cdp":
                try:
                    try:
                        from playwright_stealth import Stealth
                        stealth = Stealth()
                        await stealth.apply_stealth_async(page)
                        logger.info("✅ playwright-stealth (v2 API) injected successfully")
                    except Exception:
                        from playwright_stealth import stealth_async
                        await stealth_async(page)
                        logger.info("✅ playwright-stealth (v1 API) injected successfully")
                except ImportError:
                    logger.warning("⚠️  playwright-stealth not installed. Install with: pip install playwright-stealth")
                except Exception as e:
                    logger.warning(f"⚠️  Failed to inject playwright-stealth: {e}")
            
            # Async download handler — save_as() is a Playwright coroutine
            async def handle_download_async(download):
                """Async handler for downloads"""
                logger.info(f"🔽 DOWNLOAD TRIGGERED: {download.suggested_filename}")
                
                try:
                    downloads_dir = str(Path.home() / 'Downloads')
                    os.makedirs(downloads_dir, exist_ok=True)
                    
                    filename = download.suggested_filename
                    if not filename:
                        filename = f"download_{int(datetime.now().timestamp())}"
                    
                    filepath = os.path.join(downloads_dir, filename)
                    
                    logger.info(f"💾 Saving to: {filepath}")
                    
                    # save_as is an async Playwright method — await it directly
                    await download.save_as(filepath)
                    
                    logger.info(f"✅ Downloaded successfully: {filepath}")
                    
                    # Verify file exists and is non-empty (save_as may return before OS flush)
                    _dl_deadline = _time_module.time() + 5
                    while _time_module.time() < _dl_deadline:
                        if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
                            break
                        await asyncio.sleep(0.5)

                    if os.path.exists(filepath):
                        filesize = os.path.getsize(filepath)
                        if filesize == 0:
                            logger.warning(f"⚠️ File is 0 bytes after wait: {filepath}")
                        else:
                            logger.info(f"✅ File verified: {filepath} ({filesize} bytes)")
                        
                        # Track download for this session
                        if session_id not in self.session_downloads:
                            self.session_downloads[session_id] = []
                        self.session_downloads[session_id].append(filepath)

                        # Open the file with the default system application
                        if filesize > 0:
                            try:
                                subprocess.run(
                                    ['cmd', '/c', 'start', '', os.path.normpath(os.path.abspath(filepath))],
                                    shell=False, check=False,
                                )
                                logger.info(f"📂 Opened downloaded file: {filepath}")
                            except Exception as _open_err:
                                logger.warning(f"⚠️ Could not auto-open downloaded file: {_open_err}")
                    else:
                        logger.warning(f"⚠️ File not found after save: {filepath}")
                        
                except Exception as e:
                    logger.error(f"❌ Download failed: {e}", exc_info=True)
            
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

            # ── FAST-PATH: Direct URL navigation ─────────────────────────
            # When we already have an explicit URL (e.g. YouTube video link,
            # Google search results page), navigate directly.  Skip for
            # generic homepages that would need further interaction.
            _wp = task.get('web_params') or {}
            if _wp.get('action') == 'navigate' and _wp.get('url'):
                direct_url = _wp['url']
                # Only fast-path for specific destination URLs, not bare homepages
                _GENERIC_HOMEPAGES = ['://www.google.com', '://google.com', '://www.bing.com', '://bing.com']
                _is_bare_homepage = any(direct_url.rstrip('/').endswith(h.rstrip('/')) for h in _GENERIC_HOMEPAGES)
                _is_search_results = 'search?q=' in direct_url or 'results?search_query=' in direct_url
                _skip_fast_path = _is_bare_homepage and not _is_search_results
                if _skip_fast_path:
                    logger.info(f"⏭️ Skipping fast-path for generic homepage: {direct_url}")
                else:
                    logger.info(f"🚀 FAST-PATH: Direct navigation to {direct_url}")
                    try:
                        # Inject essential cookies for Google/YouTube before navigating
                        await self._inject_essential_cookies(page)
                        
                        await page.goto(direct_url, wait_until='load', timeout=30000)
                        # YouTube is a SPA — wait for the video player to actually render
                        if 'youtube.com' in direct_url or 'youtu.be' in direct_url:
                            try:
                                await page.wait_for_selector('video, #movie_player, ytd-player', timeout=8000)
                                logger.info("✅ FAST-PATH: YouTube video player detected")
                            except Exception:
                                logger.info("⚠️ FAST-PATH: YouTube player selector not found, but page loaded")
                        else:
                            await page.wait_for_timeout(1000)
                        
                        final_url = page.url or direct_url
                        logger.info(f"✅ FAST-PATH: Arrived at {final_url}")
                        return WebExecutionResult(
                            validation_passed=True,
                            security_passed=True,
                            output=f"Navigated to {final_url}",
                            execution_time=(datetime.now() - start_time).total_seconds()
                        )
                    except Exception as fast_err:
                        logger.warning(f"⚠️ FAST-PATH navigation failed ({fast_err}), falling through to full pipeline")
                        # Fall through to the normal pipeline as a safety net

            prompt_text = task.get('ai_prompt', '')
            ai_prompt = prompt_text.lower()
            is_auth_prompt = self._is_google_auth_prompt(ai_prompt)
            current_url_lower = (page.url or "").lower()
            # Treat as Google task for explicit Google/Gmail/YouTube/Calendar/Drive intent OR Google domain
            _GOOGLE_DOMAINS_BROAD = [
                'google.', 'gmail.', 'youtube.', 'youtu.be',
                'calendar.google', 'drive.google', 'docs.google',
                'accounts.google',
            ]
            _GOOGLE_PROMPT_KEYWORDS = [
                'google', 'gmail', 'youtube', 'google calendar',
                'google drive', 'google docs',
            ]
            is_google_task = (
                any(kw in ai_prompt for kw in _GOOGLE_PROMPT_KEYWORDS)
                or any(d in current_url_lower for d in _GOOGLE_DOMAINS_BROAD)
            )
            google_user_key = self._resolve_google_user_key(task, session_id)
            saved_default_email = self._get_saved_google_default_email(google_user_key)
            force_switch_account = self._is_switch_account_prompt(ai_prompt)
            set_default_requested = self._is_set_default_account_prompt(ai_prompt)

            is_google_authenticated = False
            if is_google_task and not force_switch_account:
                try:
                    is_google_authenticated = await self._is_google_login_complete(page)
                    if is_google_authenticated:
                        logger.info("✅ Google login detected in persistent profile")
                except Exception as e:
                    logger.debug(f"Could not resolve live Google auth state before bootstrap: {e}")

            # ✅ Phase 4b: ALWAYS try OAuth cookie injection for Google tasks
            # This happens BEFORE any bootstrap decision or navigation
            if is_google_task and not force_switch_account and not is_google_authenticated:
                logger.info("🔐 Google task detected - attempting OAuth cookie injection...")
                oauth_cookies = await self._get_google_cookies_for_user(google_user_key)
                if oauth_cookies:
                    logger.info("🔑 OAuth credentials available - injecting Google session cookies")
                    try:
                        # Sanitize cookies for Playwright compatibility
                        sanitized = []
                        for c in oauth_cookies:
                            sc = {
                                'name': str(c.get('name', '')),
                                'value': str(c.get('value', '')),
                                'domain': str(c.get('domain', '.google.com')),
                                'path': str(c.get('path', '/')),
                            }
                            if not sc['name'] or not sc['value']:
                                continue
                            if 'expires' in c:
                                try:
                                    sc['expires'] = float(c['expires'])
                                except (ValueError, TypeError):
                                    pass
                            if c.get('secure'):
                                sc['secure'] = True
                            if c.get('httpOnly'):
                                sc['httpOnly'] = True
                            ss = str(c.get('sameSite', '')).capitalize()
                            if ss in ('Strict', 'Lax', 'None'):
                                sc['sameSite'] = ss
                            sanitized.append(sc)
                        if sanitized:
                            # Log what we're injecting for diagnostics
                            cookie_names = [c['name'] for c in sanitized]
                            logger.info(f"🍪 Injecting {len(sanitized)} sanitized cookies: {cookie_names}")
                            await self.context.add_cookies(sanitized)
                            is_google_authenticated = True
                            # ✅ Phase 4c: Store timestamp for cookie refresh tracking
                            self._update_session_google_auth_hints(
                                session_id,
                                email="oauth_authenticated",
                            )
                            hints = self._session_google_auth_hints.get(session_id, {})
                            hints["oauth_injected_at"] = datetime.now().isoformat()
                            self._session_google_auth_hints[session_id] = hints
                            logger.info(f"✅ Injected {len(sanitized)} Google OAuth session cookies - user should now be authenticated")
                    except Exception as e:
                        logger.warning(f"⚠️ Could not inject OAuth cookies: {e}")
                        oauth_cookies = None
                else:
                    logger.warning(f"⚠️ No OAuth credentials found for user {google_user_key} - will show login page")
            
            # ✅ Phase 4c: Check if OAuth cookies need refresh (>45 min old)
            if is_google_task and not force_switch_account and is_google_authenticated:
                hints = self._session_google_auth_hints.get(session_id, {})
                if hints.get("oauth_injected_at"):
                    await self._refresh_google_cookies_if_needed(session_id, google_user_key, page)

            # ✅ SMART AUTH CHECK: Only block if the task genuinely REQUIRES auth
            # YouTube videos are public — auth is nice-to-have, not required.
            # Gmail, Calendar, Drive genuinely need authentication.
            _AUTH_REQUIRED_KEYWORDS = [
                'gmail', 'email', 'calendar', 'drive', 'docs', 'sheets',
                'send', 'upload', 'download',
            ]
            _PUBLIC_GOOGLE_DOMAINS = ['youtube.com', 'youtu.be']
            prompt_lower = ai_prompt.lower()
            target_url = (task.get('web_params') or {}).get('url', '') or ''
            
            is_public_google_page = any(d in target_url.lower() for d in _PUBLIC_GOOGLE_DOMAINS)
            task_strictly_requires_auth = any(kw in prompt_lower for kw in _AUTH_REQUIRED_KEYWORDS)
            
            # Only require bootstrap if:
            # 1. It's a Google task that STRICTLY requires auth (Gmail, Drive, Calendar)
            # 2. AND we don't have OAuth cookies injected
            # Public pages (YouTube) proceed without auth — they work fine unsigned-in.
            needs_google_bootstrap = (
                is_google_task
                and not is_public_google_page
                and (force_switch_account or (task_strictly_requires_auth and not is_google_authenticated))
            )

            if needs_google_bootstrap:
                # ══════════════════════════════════════════════════════════════
                # POLICY: NEVER attempt Google login via browser automation.
                # Google detects automated logins and blocks them. All Google
                # auth MUST use stored OAuth tokens / session cookies.
                # ══════════════════════════════════════════════════════════════
                logger.warning(
                    "🚫 Google login required but browser-based login is BLOCKED by policy. "
                    "OAuth cookies must be configured. Returning auth error."
                )
                return WebExecutionResult(
                    validation_passed=False,
                    security_passed=True,
                    error=(
                        "Google authentication is not available. "
                        "Please ensure your Google account is connected via OAuth tokens in the app settings. "
                        "Browser-based Google login is disabled because Google blocks automated sign-in attempts."
                    ),
                    execution_time=(datetime.now() - start_time).total_seconds()
                )

            # ── GOOGLE BOT-DETECTION ERROR PAGE RECOVERY ─────────────────────
            # When Google shows "having trouble accessing Google Search", the page
            # is still alive (not closed) but it is completely empty / broken.
            # The next task then runs on about:blank (wrong page) or tries to
            # interact with the error page and crashes.  Detect this state here
            # and reload back to google.com so the existing session is preserved.
            if is_google_task:
                try:
                    if await self._detect_google_secure_browser_warning(page):
                        return WebExecutionResult(
                            validation_passed=False,
                            security_passed=True,
                            error=self._google_secure_browser_auth_required_message(),
                            execution_time=(datetime.now() - start_time).total_seconds()
                        )

                    blocked = await self._detect_google_interstitial(page)
                    if blocked:
                        self.stats["google_block_detected"] += 1
                        logger.warning("⚠️ Google interstitial detected — starting bounded recovery")
                        recovered = await self._recover_google_interstitial(page)
                        if not recovered:
                            return WebExecutionResult(
                                validation_passed=False,
                                security_passed=True,
                                error="Google interstitial detected and recovery failed after bounded retries. Please wait and retry.",
                                execution_time=(datetime.now() - start_time).total_seconds()
                            )
                except Exception as _recovery_err:
                    logger.debug(f"Bot-detection recovery check skipped: {_recovery_err}")

            # ── Strip any error context that was appended by the bridge on
            #    retry so it never contaminates the original prompt.
            # The bridge appends " | Previous errors: ..." — remove that part.
            clean_prompt = re.split(r'\s*\|\s*Previous errors?:', prompt_text, flags=re.IGNORECASE)[0].strip()
            
            # ✅ INJECT INPUT_CONTENT FROM CROSS-AGENT DATA BRIDGE
            extra_params = task.get('extra_params', {})
            if not isinstance(extra_params, dict):
                extra_params = {}
            input_content = extra_params.get('input_content')
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

            downloads_before = set(self.session_downloads.get(session_id, []))

            # ✅ FIXED: action_type comes from web_params, never from ai_prompt
            # ✅ FIXED: Extract action_type with fallback to ai_prompt inference
            web_params = task.get('web_params') or {}
            action_type = web_params.get('action', 'unknown')
            
            # Safety net: infer action_type from ai_prompt if missing
            if action_type == 'unknown':
                prompt_lower = task.get('ai_prompt', '').lower()
                if any(w in prompt_lower for w in ['navigate', 'go to', 'open', 'visit']):
                    action_type = 'navigate'
                elif any(w in prompt_lower for w in ['fill', 'type', 'enter', 'search']):
                    action_type = 'fill'
                elif any(w in prompt_lower for w in ['click', 'press', 'submit', 'tap']):
                    action_type = 'click'
                elif any(w in prompt_lower for w in ['extract', 'get', 'read', 'scrape']):
                    action_type = 'extract'
                if action_type != 'unknown':
                    logger.info(f"📝 Inferred action_type='{action_type}' from ai_prompt (web_params missing)")
            
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

            # ✅ PDF VIEWER DOWNLOAD SHORT-CIRCUIT
            download_intent = self._is_download_intent(clean_prompt, action_type, web_params)
            is_pdf_viewer = False
            if page_state_before:
                is_pdf_viewer = (
                    page_state_before.get('isPdf')
                    or page_state_before.get('siteType') == 'pdf'
                    or 'pdf_viewer' in page_state_before.get('capabilities', [])
                )
            if download_intent and (is_pdf_viewer or self._looks_like_pdf_url(page.url)):
                logger.info("📄 PDF viewer detected with download intent - using direct download")
                pdf_result = await self._handle_pdf_viewer_download(page, session_id)
                if pdf_result.get('success'):
                    filepath = pdf_result.get('filepath')
                    output_msg = f"Downloaded PDF to {filepath}" if filepath else "PDF download completed"
                    return WebExecutionResult(
                        validation_passed=True,
                        security_passed=True,
                        output=output_msg,
                        page_url=page.url,
                        page_title=await page.title(),
                        page_state_before=page_state_before,
                        execution_time=(datetime.now() - start_time).total_seconds()
                    )

                return WebExecutionResult(
                    validation_passed=False,
                    security_passed=True,
                    error=pdf_result.get('error') or 'PDF download failed',
                    page_state_before=page_state_before,
                    execution_time=(datetime.now() - start_time).total_seconds()
                )
            
            # Already extracted above, no need to re-extract
            # action_type = task.get('web_params', {}).get('action', 'unknown')
            
            # Check if navigation (invalidate cache)
            if action_type == 'navigate':
                self.context_cache.invalidate(session_id)
                
                # ✅ DIRECT NAVIGATION HANDLER - Don't use LLM for simple navigation!
                # Extract URL from extra_params or ai_prompt
                target_url = task.get('extra_params', {}).get('url')
                
                if not target_url:
                    # Try to extract from prompt like "Navigate to https://www.google.com"
                    import re as _re
                    url_match = _re.search(r'https?://[^\s\'"<>]+', clean_prompt)
                    if url_match:
                        target_url = url_match.group(0)
                
                if target_url:
                    # ── FIX (Bug 3): If we're already on the target domain,
                    # skip page.goto() and fall through to in-page interaction
                    # so the LLM generates type-in-searchbox / click code instead.
                    from urllib.parse import urlparse
                    _target_domain = urlparse(target_url).netloc.replace('www.', '')
                    _current_domain = urlparse(page.url or '').netloc.replace('www.', '')
                    _already_on_domain = (
                        _target_domain and _current_domain
                        and _target_domain == _current_domain
                    )
                    # Still navigate if it's a specific deep URL (has path/query),
                    # but skip if it's just the bare homepage and we're already there.
                    _target_path = urlparse(target_url).path.strip('/')
                    _target_query = urlparse(target_url).query
                    _is_deep_url = bool(_target_path) or bool(_target_query)

                    if _already_on_domain and not _is_deep_url:
                        logger.info(f"♻️ Already on {_current_domain} — skipping page.goto(), will interact in-page")
                    else:
                        logger.info(f"🌐 Direct navigation to: {target_url}")
                        try:
                            # Navigate directly without LLM generation
                            await page.goto(target_url, wait_until='domcontentloaded', timeout=15000)
                            await page.wait_for_timeout(500)  # Let page settle
                            
                            logger.info(f"✅ Successfully navigated to {target_url}")
                            
                            return WebExecutionResult(
                                validation_passed=True,
                                security_passed=True,
                                error=None,
                                execution_time=(datetime.now() - start_time).total_seconds()
                            )
                        except Exception as nav_error:
                            logger.warning(f"⚠️ Navigation failed: {nav_error}")
                        # Fall through to LLM-generated code as fallback
            
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
            _pages_before_exec = len(self.context.pages) if self.context else 0
            result = await self._execute_generated_code(page, generated_code, task_id)

            if result.get('success'):
                self.stats["dom_success"] += 1
            else:
                # ✅ FIX: Try DOM-based fallback link click BEFORE OmniParser visual fallback.
                # The page inspector already extracted all link texts from the DOM — use that
                # instead of OmniParser image captioning which can't read text.
                dom_fallback_tried = False
                popup_visual_tried = False
                dismiss_intent = self._is_dismiss_popup_prompt(clean_prompt)
                if action_type == 'click' and dismiss_intent:
                    dom_has_close = await self._dom_has_close_control(page)
                    popup_visual_tried = True
                    if dom_has_close:
                        logger.info("🔧 Close control detected in DOM; trying visual fallback for popup dismiss anyway")
                    else:
                        logger.info("🔧 No close control found in DOM; trying visual fallback for popup dismiss")
                    visual_result = await self._try_visual_fallback_popup_dismiss(page, task, task_id)
                    if visual_result is not None:
                        result = visual_result
                        if result.get('success'):
                            logger.info("✅ Visual fallback dismiss succeeded")

                if not result.get('success') and action_type == 'click' and not dismiss_intent and any(kw in clean_prompt.lower() for kw in ('link', 'click', 'open', 'result')):
                    logger.info(f"🔧 RAG code failed — trying DOM-based fallback link click")
                    try:
                        fallback_code = self._generate_fallback_link_click(clean_prompt)
                        result = await self._execute_generated_code(page, fallback_code, task_id)
                        dom_fallback_tried = True
                        if result.get('success'):
                            logger.info("✅ DOM fallback link click succeeded")
                    except Exception as fb_err:
                        logger.warning(f"⚠️ DOM fallback link click failed: {fb_err}")

                # Only try OmniParser if DOM fallback didn't work
                if not result.get('success') and not dom_fallback_tried and not popup_visual_tried:
                    visual_result = await self._try_visual_fallback_action(page, task, task_id)
                    if visual_result is not None:
                        result = visual_result
                        if result.get('success'):
                            logger.info("✅ Visual fallback succeeded after DOM failure")
                        else:
                            logger.warning(f"⚠️ Visual fallback returned failure: {result.get('error')}")

            # ✅ NEW TAB DETECTION: if a click opened a new tab, switch the session to it
            if action_type in ('click', 'navigate') and result.get('success'):
                try:
                    all_pages = self.context.pages if self.context else []
                    if len(all_pages) > _pages_before_exec:
                        new_page = all_pages[-1]
                        try:
                            await new_page.wait_for_load_state('domcontentloaded', timeout=5000)
                        except Exception:
                            pass
                        self.sessions[session_id] = new_page
                        page = new_page
                        logger.info(f"✅ New tab detected — switched session to: {new_page.url}")
                except Exception as _nt_err:
                    logger.debug(f"New tab check skipped: {_nt_err}")

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
                downloads_after = set(self.session_downloads.get(session_id, []))
                new_downloads = downloads_after - downloads_before
                # Compare states
                if page_state_before and page_state_after:
                    changes = await compare_states(page_state_before, page_state_after)
                    
                    if changes['any_change']:
                        verification_message = f"✅ Page state changed as expected: {changes}"
                        logger.info(f"✅ Verification: State changed")
                    else:
                        # ℹ️ For click actions: no URL change could mean download, modal, or off-page action
                        if action_type == 'click':
                            dismiss_intent = self._is_dismiss_popup_prompt(clean_prompt)
                            if dismiss_intent:
                                before_interactive = (page_state_before or {}).get('interactive', {})
                                after_interactive = (page_state_after or {}).get('interactive', {})
                                before_visible = before_interactive.get('visibleModalCount')
                                after_visible = after_interactive.get('visibleModalCount')

                                if before_visible is not None and after_visible is not None:
                                    if after_visible < before_visible:
                                        verification_message = f"✅ Popup dismissed (visible modals {before_visible}→{after_visible})"
                                        logger.info("✅ Verification: Popup dismissed (visible modals reduced)")
                                    elif before_interactive.get('hasModals') and not after_interactive.get('hasModals'):
                                        verification_message = "✅ Popup dismissed (no visible modals)"
                                        logger.info("✅ Verification: Popup dismissed (no visible modals)")
                                    else:
                                        verification_message = "⚠️ Popup dismiss not verified (no modal reduction)"
                                        logger.warning("⚠️ Verification: Popup dismiss not verified")
                                elif before_interactive.get('hasModals') and not after_interactive.get('hasModals'):
                                    verification_message = "✅ Popup dismissed (no visible modals)"
                                    logger.info("✅ Verification: Popup dismissed (no visible modals)")
                                else:
                                    verification_message = "⚠️ Popup dismiss not verified (no modal signal)"
                                    logger.warning("⚠️ Verification: Popup dismiss not verified (no modal signal)")
                            else:
                                if new_downloads:
                                    verification_message = f"✅ Download detected ({len(new_downloads)} file(s))"
                                    logger.info("✅ Verification: Download detected after click")
                                elif download_intent:
                                    verification_message = "⚠️ Download intent but no download detected"
                                    logger.warning("⚠️ Verification: Download intent but no download detected")
                                else:
                                # Check if this was a navigation intent (link/open/go to)
                                 _nav_keywords = ('link', 'open', 'click', 'go to', 'visit', 'navigate')
                                 _is_nav_intent = any(kw in clean_prompt.lower() for kw in _nav_keywords)
                                if _is_nav_intent:
                                    # Wait briefly and re-check URL — gives navigation time to happen
                                    await page.wait_for_timeout(1500)
                                    _url_after_wait = page.url
                                    _pages_after_wait = len(self.context.pages) if self.context else 1
                                    if _url_after_wait != _url_before_exec:
                                        verification_message = f"✅ Navigation confirmed: {_url_after_wait}"
                                        logger.info(f"✅ Verification: URL changed after wait → {_url_after_wait}")
                                    elif _pages_after_wait > _pages_before_exec:
                                        verification_message = "✅ New tab opened after click"
                                        logger.info(f"✅ Verification: New tab opened")
                                    else:
                                        # No navigation happened — the click was a no-op, mark as failure
                                        result['success'] = False
                                        result['error'] = 'Click executed but page did not navigate — element may not have been found or was the wrong element'
                                        verification_message = "❌ Click did not cause navigation"
                                        logger.warning(f"❌ Verification: Click reported success but URL unchanged after wait")
                                else:
                                    verification_message = "✅ Click executed (no navigation expected)"
                                    logger.info(f"✅ Verification: Click action executed (non-navigation click)")
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
            generated_code = self._auto_await_async_calls(generated_code)
            
            if not generated_code:
                raise ValueError("RAG system returned empty code")
            
            logger.info(f"✅ RAG generated {len(generated_code)} chars of code")
            
            return generated_code
            
        except Exception as e:
            logger.error(f"❌ RAG code generation failed: {e}")
            raise

    def _auto_await_async_calls(self, code: str) -> str:
        """
        Ensure common async Playwright actions are awaited.
        This guards against generated sync-style calls that do nothing at runtime.
        """
        if not code:
            return code

        async_methods = [
            "click",
            "dblclick",
            "hover",
            "fill",
            "type",
            "press",
            "scroll_into_view_if_needed",
            "check",
            "uncheck",
            "select_option",
            "set_input_files",
            "tap",
            "drag_to",
            "wait_for_selector",
            "wait_for_load_state",
            "wait_for_timeout",
            "wait_for",
            "goto",
            "evaluate",
            "evaluate_handle",
            "text_content",
            "inner_text",
            "input_value",
            "get_attribute",
            "count",
            "is_visible",
            "is_hidden",
            "is_enabled",
            "is_disabled",
            "is_checked",
        ]

        method_group = "|".join(re.escape(m) for m in async_methods)
        call_pattern = re.compile(
            rf"^(?P<expr>(?:[A-Za-z_][\w]*)(?:\.[A-Za-z_][\w]*)*\.(?:{method_group})\s*\(.*\))(?P<comment>\s*#.*)?$"
        )

        fixed_lines = []
        for line in code.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                fixed_lines.append(line)
                continue
            if "await " in line:
                fixed_lines.append(line)
                continue

            if stripped.startswith("return "):
                remainder = stripped[len("return "):].strip()
                match = call_pattern.match(remainder)
                if match:
                    indent = line[:len(line) - len(line.lstrip())]
                    expr = match.group("expr")
                    comment = match.group("comment") or ""
                    fixed_lines.append(f"{indent}return await {expr}{comment}")
                    continue

            match = call_pattern.match(stripped)
            if match:
                indent = line[:len(line) - len(line.lstrip())]
                expr = match.group("expr")
                comment = match.group("comment") or ""
                fixed_lines.append(f"{indent}await {expr}{comment}")
                continue

            fixed_lines.append(line)

        return "\n".join(fixed_lines)

    def _is_download_intent(self, prompt: str, action_type: str, web_params: Dict[str, Any]) -> bool:
        if action_type in {'download', 'save', 'export'}:
            return True
        if isinstance(web_params, dict) and web_params.get('download'):
            return True
        prompt_lower = (prompt or '').lower()
        download_keywords = ['download', 'save pdf', 'save file', 'export', 'get pdf', 'pdf']
        return any(k in prompt_lower for k in download_keywords)

    def _looks_like_pdf_url(self, url: str) -> bool:
        if not url:
            return False
        url_lower = url.lower()
        return url_lower.endswith('.pdf') or '/pdf/' in url_lower or 'format=pdf' in url_lower

    def _guess_pdf_filename(self, headers: Dict[str, str], url: str) -> str:
        from urllib.parse import urlparse, unquote

        content_disposition = (headers or {}).get('content-disposition', '')
        filename = None

        if content_disposition:
            match = re.search(r"filename\*=UTF-8''([^;]+)", content_disposition, re.IGNORECASE)
            if match:
                filename = unquote(match.group(1))
            else:
                match = re.search(r"filename=\"?([^\";]+)\"?", content_disposition, re.IGNORECASE)
                if match:
                    filename = match.group(1)

        if not filename:
            parsed = urlparse(url)
            filename = os.path.basename(parsed.path)

        filename = os.path.basename(filename) if filename else ''
        if not filename:
            filename = f"download_{int(datetime.now().timestamp())}.pdf"
        if not filename.lower().endswith('.pdf'):
            filename = f"{filename}.pdf"

        return filename

    async def _download_pdf_via_request(self, page, pdf_url: str, session_id: str) -> Dict[str, Any]:
        try:
            response = await page.context.request.get(pdf_url)
            status = response.status
            if status < 200 or status >= 300:
                return {'success': False, 'error': f"HTTP {status} for {pdf_url}"}

            body = await response.body()
            if not body:
                return {'success': False, 'error': "Empty response body"}

            headers = response.headers
            filename = self._guess_pdf_filename(headers, pdf_url)
            downloads_dir = str(Path.home() / 'Downloads')
            os.makedirs(downloads_dir, exist_ok=True)
            filepath = os.path.join(downloads_dir, filename)

            with open(filepath, 'wb') as f:
                f.write(body)

            if session_id not in self.session_downloads:
                self.session_downloads[session_id] = []
            self.session_downloads[session_id].append(filepath)

            logger.info(f"✅ Saved PDF via request: {filepath}")
            return {'success': True, 'filepath': filepath}

        except Exception as e:
            logger.warning(f"⚠️ Request-based PDF download failed: {e}")
            return {'success': False, 'error': str(e)}

    async def _wait_for_new_download(self, session_id: str, before: set, timeout_ms: int = 8000) -> Optional[str]:
        start_time = _time_module.time()
        while (_time_module.time() - start_time) * 1000 < timeout_ms:
            downloads = self.session_downloads.get(session_id, [])
            for path in downloads:
                if path not in before and os.path.exists(path):
                    return path
            await asyncio.sleep(0.25)
        return None

    async def _handle_pdf_viewer_download(self, page, session_id: str) -> Dict[str, Any]:
        pdf_url = page.url
        if not self._looks_like_pdf_url(pdf_url):
            pdf_url = page.url

        logger.info(f"📄 PDF viewer download path: {pdf_url}")

        # Prefer direct request download to avoid non-DOM PDF toolbar issues
        request_result = await self._download_pdf_via_request(page, pdf_url, session_id)
        if request_result.get('success'):
            return request_result

        # Fallback: attempt browser save shortcut (may or may not trigger a download)
        before = set(self.session_downloads.get(session_id, []))
        try:
            await page.keyboard.press('Control+S')
        except Exception:
            pass
        try:
            await page.keyboard.press('Meta+S')
        except Exception:
            pass

        new_download = await self._wait_for_new_download(session_id, before)
        if new_download:
            return {'success': True, 'filepath': new_download}

        return {
            'success': False,
            'error': 'PDF viewer download failed: no toolbar DOM and no direct download response'
        }
    
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
        count = await locator.count()
        if count > 0:
            # CRITICAL: Scroll into view BEFORE clicking (handles arXiv, long pages, etc.)
            await locator.first.scroll_into_view_if_needed()
            await page.wait_for_timeout(300)
            await locator.first.click()
            print('EXECUTION_SUCCESS')
            return
    except Exception as e:
        pass  # Strategy 1 failed, try next
    
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
            
            # ✅ FIX: Sanitize 'await page.locator(...)' without chained action
            # LLMs sometimes generate 'await page.locator(selector)' which is wrong —
            # page.locator() is synchronous, only the action (.click(), .fill(), etc.) is async.
            # Match 'await page.locator(...)' NOT followed by .click/.fill/.count/.text_content/etc.
            code = re.sub(
                r'await\s+(page\.locator\([^)]*\))\s*$',
                r'\1',
                code,
                flags=re.MULTILINE
            )
            # Also fix 'await page.get_by_...(...)' bare calls
            code = re.sub(
                r'await\s+(page\.get_by_\w+\([^)]*\))\s*$',
                r'\1',
                code,
                flags=re.MULTILINE
            )
            
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
                'context': self.context,  # ✅ Required for context.expect_page() patterns
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
        
        # If stdout has content but no success marker, code ran but didn't confirm success
        if stdout.strip():
            return True, "Code executed (no explicit success marker)"
        
        # Empty stdout means the code ran silently — likely failed without printing anything
        return False, "No output generated — execution may have failed silently"
    
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
    
    async def auto_login_google(self, page, user_key: str) -> bool:
        """
        Backward-compatible login helper.
        Uses saved default email, then waits for user to complete password/2FA manually.
        """
        try:
            saved_default_email = self._get_saved_google_default_email(user_key)
            if not saved_default_email:
                logger.info(f"ℹ️ No saved default Google email for '{user_key}'")
                return False

            await page.goto('https://accounts.google.com/signin', wait_until='domcontentloaded', timeout=15000)
            await page.wait_for_timeout(400)
            if not await self._prefill_google_email(page, saved_default_email):
                return False

            logged_in = await self._wait_for_manual_google_login(
                page,
                timeout_ms=self.config.google_manual_login_timeout_ms,
            )
            if not logged_in:
                return False

            try:
                await page.goto('https://www.google.com', wait_until='domcontentloaded', timeout=15000)
            except Exception:
                pass
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
            
            # Close persistent context (auto-saves profile to disk)
            if self.context:
                try:
                    await self.context.close()
                    logger.info(f"✅ Closed browser context for user '{self.current_user}'")
                except Exception as e:
                    logger.warning(f"⚠️  Could not close context: {e}")
            
            if self.browser:
                try:
                    await self.browser.close()
                except Exception as e:
                    logger.debug(f"Browser close: {e}")
            
            if self.playwright:
                await self.playwright.stop()

            # Terminate the Chrome subprocess we launched for CDP
            if hasattr(self, '_chrome_process') and self._chrome_process is not None:
                try:
                    self._chrome_process.terminate()
                    self._chrome_process.wait(timeout=5)
                    logger.info("✅ Chrome subprocess terminated")
                except Exception as e:
                    logger.debug(f"Chrome subprocess cleanup: {e}")
                    try:
                        self._chrome_process.kill()
                    except Exception:
                        pass

            if self._google_profile_client is not None:
                try:
                    self._google_profile_client.close()
                except Exception:
                    pass
            
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
                error="Not a web action task — 'api' target tasks must be routed to API Agent via COORDINATOR_TO_API channel, not web automation"
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
                    'extra_params': task.extra_params,
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