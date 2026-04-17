#!/usr/bin/env python3
"""
bot_evasion.py - Bot Detection Evasion and Proxy Rotation

Features:
- Behavioral randomization (wait times, scroll patterns)
- Bezier curve mouse movement for human-like interactions
- Fingerprint spoofing (navigator.webdriver, canvas noise, timezone randomization)
- Proxy rotation with session-bound strategy
- Bot block detection (429, 403, "unusual traffic")
"""

import random
import time
import math
import logging
from typing import Dict, List, Optional, Tuple
from datetime import datetime
from enum import Enum
import uuid

try:
    import bezier
except ImportError:
    bezier = None
    logging.warning("⚠️ bezier module not installed, mouse movement will use linear interpolation")

logger = logging.getLogger(__name__)

# ============================================================================
# PROXY ROTATION STRATEGY
# ============================================================================

class ProxyRotationStrategy(str, Enum):
    """Proxy rotation strategies"""
    ROUND_ROBIN = "round_robin"
    RANDOM = "random"
    LEAST_USED = "least_used"
    GEOGRAPHIC = "geographic"


class ProxyRotator:
    """
    Manages proxy rotation with session-bound consistency.
    
    Strategy:
    - All requests within a single task session use the SAME proxy
    - After task completion, switch to next proxy for next task
    - If rate limit detected (429/403), signal coordinator for fallback rotation
    """
    
    def __init__(self, proxy_pool: Optional[List[str]] = None, 
                 strategy: ProxyRotationStrategy = ProxyRotationStrategy.ROUND_ROBIN):
        """
        Initialize proxy rotator
        
        Args:
            proxy_pool: List of proxy URLs (format: http://user:pass@host:port or http://host:port)
            strategy: Rotation strategy
        """
        self.proxy_pool = proxy_pool or []
        self.strategy = strategy
        self.current_index = 0
        
        # Session-to-proxy mapping: task_id -> proxy_url
        self.session_proxy_map: Dict[str, str] = {}
        
        # Proxy statistics: proxy_url -> {"uses": int, "failures": int, "last_used": datetime}
        self.proxy_stats: Dict[str, Dict] = {
            proxy: {"uses": 0, "failures": 0, "last_used": None}
            for proxy in self.proxy_pool
        }
        
        logger.info(f"✅ ProxyRotator initialized with {len(self.proxy_pool)} proxies (strategy: {strategy})")
    
    def add_proxy(self, proxy: str):
        """Add proxy to pool"""
        if proxy not in self.proxy_pool:
            self.proxy_pool.append(proxy)
            self.proxy_stats[proxy] = {"uses": 0, "failures": 0, "last_used": None}
            logger.info(f"➕ Proxy added: {proxy}")
    
    def remove_proxy(self, proxy: str):
        """Remove proxy from pool"""
        if proxy in self.proxy_pool:
            self.proxy_pool.remove(proxy)
            self.proxy_stats.pop(proxy, None)
            logger.warning(f"➖ Proxy removed: {proxy}")
    
    def get_proxy_for_session(self, session_id: str) -> Optional[str]:
        """
        Get proxy for session (session-bound consistency)
        Returns same proxy for same session_id
        """
        if not self.proxy_pool:
            logger.warning("⚠️ No proxies available")
            return None
        
        # Return existing proxy for this session
        if session_id in self.session_proxy_map:
            proxy = self.session_proxy_map[session_id]
            if proxy in self.proxy_pool:
                return proxy
            # Proxy was removed, assign new one
            del self.session_proxy_map[session_id]
        
        # Assign new proxy based on strategy
        proxy = self._select_proxy()
        self.session_proxy_map[session_id] = proxy
        
        self.proxy_stats[proxy]["uses"] += 1
        self.proxy_stats[proxy]["last_used"] = datetime.now()
        
        logger.info(f"🔄 Assigned proxy {proxy} to session {session_id}")
        return proxy
    
    def _select_proxy(self) -> Optional[str]:
        """Select proxy based on strategy"""
        if not self.proxy_pool:
            return None
        
        if self.strategy == ProxyRotationStrategy.ROUND_ROBIN:
            proxy = self.proxy_pool[self.current_index % len(self.proxy_pool)]
            self.current_index += 1
            return proxy
        
        elif self.strategy == ProxyRotationStrategy.RANDOM:
            return random.choice(self.proxy_pool)
        
        elif self.strategy == ProxyRotationStrategy.LEAST_USED:
            return min(self.proxy_pool, key=lambda p: self.proxy_stats[p]["uses"])
        
        return self.proxy_pool[0]
    
    def report_success(self, session_id: str):
        """Report successful request with proxy"""
        if session_id not in self.session_proxy_map:
            return
        proxy = self.session_proxy_map[session_id]
        logger.info(f"✅ Proxy success: {proxy}")
    
    def report_failure(self, session_id: str, error_code: Optional[int] = None):
        """Report failed request with proxy"""
        if session_id not in self.session_proxy_map:
            return
        
        proxy = self.session_proxy_map[session_id]
        self.proxy_stats[proxy]["failures"] += 1
        
        if error_code in [429, 403]:
            logger.warning(f"⚠️ Rate limit ({error_code}) detected with proxy {proxy}")
        
        logger.warning(f"❌ Proxy failure: {proxy} (total failures: {self.proxy_stats[proxy]['failures']})")
    
    def rotate_session(self, session_id: str):
        """Rotate to new proxy for next task (clearoldassignment)"""
        if session_id in self.session_proxy_map:
            old_proxy = self.session_proxy_map.pop(session_id)
            logger.info(f"🔄 Session {session_id} rotated away from {old_proxy}")
    
    def get_stats(self) -> Dict:
        """Get proxy statistics"""
        return {
            "pool_size": len(self.proxy_pool),
            "proxies": self.proxy_stats,
            "sessions": len(self.session_proxy_map)
        }


# ============================================================================
# BOT EVASION TECHNIQUES
# ============================================================================

class BotEvasion:
    """
    Bot evasion techniques to avoid detection
    """
    
    # ────────────────────────────────────────────────────────────────────────
    # Behavioral Randomization
    # ────────────────────────────────────────────────────────────────────────
    
    @staticmethod
    def random_delay(min_seconds: float = 0.5, max_seconds: float = 2.5) -> float:
        """
        Generate random delay with human-like distribution
        (humans rarely wait exactly 1 second)
        """
        delay = random.uniform(min_seconds, max_seconds)
        # Add Gaussian noise for more natural feel
        delay += random.gauss(0, 0.1)
        return max(min_seconds, delay)
    
    @staticmethod
    async def human_like_delay():
        """Apply human-like random delay"""
        delay = BotEvasion.random_delay()
        logger.debug(f"⏳ Delay: {delay:.2f}s")
        await asyncio.sleep(delay) if 'asyncio' in globals() else time.sleep(delay)
    
    @staticmethod
    def human_scroll_pattern() -> List[int]:
        """
        Generate human-like scroll pattern
        Humans don't scroll smoothly -- they scroll in chunks with pauses
        """
        scroll_steps = random.randint(3, 8)  # 3-8 scroll steps
        pattern = []
        
        for _ in range(scroll_steps):
            # Each scroll chunk: 100-500 pixels
            scroll_amount = random.randint(100, 500)
            pattern.append(scroll_amount)
            # Random pause between scrolls
            pause = random.uniform(0.2, 1.0)
            pattern.append(f"pause_{pause}")
        
        return pattern
    
    @staticmethod
    def random_mouse_jitter() -> Tuple[int, int]:
        """
        Small random jitter in mouse position
        Real humans wiggle the mouse slightly
        """
        jitter_x = random.randint(-5, 5)
        jitter_y = random.randint(-5, 5)
        return (jitter_x, jitter_y)
    
    # ────────────────────────────────────────────────────────────────────────
    # Mouse Movement - Bezier Curves
    # ────────────────────────────────────────────────────────────────────────
    
    @staticmethod
    def bezier_curve_path(start: Tuple[int, int], end: Tuple[int, int], 
                         steps: int = 50) -> List[Tuple[int, int]]:
        """
        Generate smooth Bezier curve path from start to end
        Human eyes follow curves, not straight lines
        """
        if bezier is None:
            # Fallback: linear interpolation
            return BotEvasion._linear_path(start, end, steps)
        
        try:
            # Create control points for Bezier curve
            control_x = [start[0], 
                        start[0] + random.randint(-100, 100),
                        end[0] + random.randint(-100, 100),
                        end[0]]
            control_y = [start[1],
                        start[1] + random.randint(-100, 100),
                        end[1] + random.randint(-100, 100),
                        end[1]]
            
            # Create Bezier curve
            curve = bezier.Curve(
                [control_x, control_y],
                degree=3
            )
            
            # Evaluate curve at regular intervals
            path = []
            for i in range(steps + 1):
                t = i / steps
                s = curve.evaluate(t)
                path.append((int(s[0][0]), int(s[1][0])))
            
            return path
        
        except Exception as e:
            logger.warning(f"⚠️ Bezier curve generation failed: {e}, falling back to linear")
            return BotEvasion._linear_path(start, end, steps)
    
    @staticmethod
    def _linear_path(start: Tuple[int, int], end: Tuple[int, int], 
                    steps: int = 50) -> List[Tuple[int, int]]:
        """Fallback linear path generation"""
        path = []
        for i in range(steps + 1):
            t = i / steps
            x = int(start[0] + (end[0] - start[0]) * t)
            y = int(start[1] + (end[1] - start[1]) * t)
            path.append((x, y))
        return path
    
    # ────────────────────────────────────────────────────────────────────────
    # Fingerprint Spoofing (for browser automation detection)
    # ────────────────────────────────────────────────────────────────────────
    
    @staticmethod
    def get_fingerprint_spoof_script() -> str:
        """
        JavaScript to spoof browser fingerprint
        Prevents detection via navigator.webdriver, canvas fingerprinting, etc.
        """
        timezone_offset = random.randint(-720, 720)  # UTC offset in minutes
        screen_width = random.choice([1920, 1680, 1600, 1440, 1366, 1280, 1024])
        screen_height = random.choice([1080, 1050, 1024, 900, 768, 768, 600])
        
        script = f"""
        // Spoof navigator.webdriver detection
        Object.defineProperty(navigator, 'webdriver', {{
            get: () => undefined,
        }});
        
        // Spoof navigator.plugins
        Object.defineProperty(navigator, 'plugins', {{
            get: () => [1, 2, 3],
        }});
        
        // Spoof navigator.languages
        Object.defineProperty(navigator, 'languages', {{
            get: () => ['en-US', 'en'],
        }});
        
        // Canvas fingerprinting protection
        const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
        HTMLCanvasElement.prototype.toDataURL = function() {{
            if (this.width === 139 && this.height === 14) {{
                // WebGL fingerprinting canvas
                const canvas = document.createElement('canvas');
                const ctx = canvas.getContext('2d');
                ctx.fillText('', 0, 0);
                return originalToDataURL.call(this);
            }}
            return originalToDataURL.call(this);
        }};
        
        // Spoof screen dimensions
        Object.defineProperty(screen, 'width', {{
            get: () => {screen_width},
        }});
        
        Object.defineProperty(screen, 'height', {{
            get: () => {screen_height},
        }});
        
        // Randomize timezone
        const date = new Date();
        // Note: timezone can't actually be spoofed, but we can make other fingerprints inconsistent
        """
        
        return script
    
    @staticmethod
    def get_user_agent() -> str:
        """
        Get randomized user agent (rotate between common ones)
        """
        user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.131 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:89.0) Gecko/20100101 Firefox/89.0',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        ]
        return random.choice(user_agents)
    
    # ────────────────────────────────────────────────────────────────────────
    # Bot Block Detection
    # ────────────────────────────────────────────────────────────────────────
    
    @staticmethod
    async def detect_google_bot_block(page) -> bool:
        """
        Detect Google's "unusual traffic" bot detection page
        Returns True if bot block detected
        """
        try:
            # Get page content
            content = await page.content()
            
            # Google bot block indicators
            bot_block_indicators = [
                'Please try again in a few moments',
                'unusual traffic from your computer network',
                'if you are seeing this message',
                'why have i been blocked',
                'gl=us',  # Google's geo parameter on error pages
            ]
            
            content_lower = content.lower()
            
            for indicator in bot_block_indicators:
                if indicator in content_lower:
                    logger.warning(f"🚨 Google bot block detected: {indicator}")
                    return True
            
            return False
        
        except Exception as e:
            logger.warning(f"⚠️ Bot block detection failed: {e}")
            return False
    
    @staticmethod
    async def detect_rate_limit(response_status: int) -> bool:
        """
        Detect rate limiting by HTTP status code
        429 = Too Many Requests
        403 = Forbidden (sometimes used for rate limiting)
        """
        if response_status == 429:
            logger.warning("🚨 Rate limit detected (429 Too Many Requests)")
            return True
        elif response_status == 403:
            logger.warning("🚨 Potential rate limit detected (403 Forbidden)")
            return True
        
        return False
    
    # ────────────────────────────────────────────────────────────────────────
    # reCAPTCHA Handling (Placeholder for 2Captcha integration)
    # ────────────────────────────────────────────────────────────────────────
    
    @staticmethod
    def get_recaptcha_solver_config() -> Dict:
        """
        Configuration for reCAPTCHA solving via 2Captcha
        To be integrated when reCAPTCHA is detected
        """
        return {
            "provider": "2captcha",
            "api_key": "__2CAPTCHA_API_KEY__",  # Set via environment
            "timeout": 180,  # 3 minutes max wait
            "polling_interval": 3,  # Check every 3 seconds
        }


# ============================================================================
# IMPORTS FOR ASYNC SUPPORT
# ============================================================================

import asyncio
