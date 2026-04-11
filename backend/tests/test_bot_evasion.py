#!/usr/bin/env python3
"""
test_bot_evasion.py - Unit Tests for Bot Evasion Techniques

Tests:
- Behavioral randomization (delays, scroll patterns)
- Proxy rotation with session-bound consistency
- Bot block detection
- Fingerprint spoofing
"""

import pytest
import time
from unittest.mock import MagicMock, patch, AsyncMock

from agents.execution_agent.RAG.web.bot_evasion import (
    BotEvasion,
    ProxyRotator,
    ProxyRotationStrategy
)


class TestBotEvasion:
    """Test BotEvasion techniques"""
    
    def test_random_delay(self):
        """Test that random delays are within expected range"""
        min_delay = 0.5
        max_delay = 2.5
        
        delay = BotEvasion.random_delay(min_seconds=min_delay, max_seconds=max_delay)
        
        # Allow for Gaussian noise
        assert 0.3 < delay < 3.0
    
    def test_random_delay_consistency(self):
        """Test that multiple delays vary"""
        delays = [BotEvasion.random_delay() for _ in range(5)]
        
        # All delays should be different (very unlikely to be identical)
        assert len(set(delays)) > 1
    
    def test_human_scroll_pattern(self):
        """Test scroll pattern generation"""
        pattern = BotEvasion.human_scroll_pattern()
        
        assert len(pattern) > 0
        # Should have mix of numbers (scroll amounts) and pause strings
        has_scroll = any(isinstance(x, int) for x in pattern)
        has_pause = any(isinstance(x, str) and 'pause' in x for x in pattern)
        
        assert has_scroll
        assert has_pause
    
    def test_mouse_jitter(self):
        """Test mouse jitter generation"""
        jitter_x, jitter_y = BotEvasion.random_mouse_jitter()
        
        assert -5 <= jitter_x <= 5
        assert -5 <= jitter_y <= 5
    
    def test_bezier_curve_path(self):
        """Test Bezier curve path generation"""
        start = (100, 100)
        end = (500, 500)
        
        path = BotEvasion.bezier_curve_path(start, end, steps=50)
        
        # Path should have multiple points
        assert len(path) > 1
        
        # Should start near start and end near end
        assert path[0] == start
        assert path[-1] == end
        
        # All points should be tuples of integers
        for point in path:
            assert isinstance(point, tuple)
            assert len(point) == 2
            assert all(isinstance(x, int) for x in point)
    
    def test_bezier_curve_smoothness(self):
        """Test that Bezier curve is smooth (no huge jumps)"""
        start = (0, 0)
        end = (1000, 1000)
        
        path = BotEvasion.bezier_curve_path(start, end, steps=100)
        
        # Check that consecutive points don't jump too much
        max_jump = 0
        for i in range(len(path) - 1):
            x1, y1 = path[i]
            x2, y2 = path[i + 1]
            distance = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
            max_jump = max(max_jump, distance)
        
        # Max jump should be reasonable (not huge)
        assert max_jump < 100
    
    def test_fingerprint_spoof_script(self):
        """Test fingerprint spoofing script generation"""
        script = BotEvasion.get_fingerprint_spoof_script()
        
        # Script should contain anti-detection code
        assert 'navigator' in script
        assert 'webdriver' in script
        assert 'defineProperty' in script
    
    def test_user_agent_randomization(self):
        """Test user agent randomization"""
        ua1 = BotEvasion.get_user_agent()
        ua2 = BotEvasion.get_user_agent()
        
        # Both should be valid user agents
        assert 'Mozilla' in ua1
        assert 'Mozilla' in ua2
        
        # Should vary sometimes
        user_agents = [BotEvasion.get_user_agent() for _ in range(10)]
        assert len(set(user_agents)) > 1
    
    @pytest.mark.asyncio
    async def test_google_bot_block_detection_false(self):
        """Test bot block detection returns False for normal page"""
        mock_page = AsyncMock()
        mock_page.content.return_value = "Normal Google search results"
        
        is_blocked = await BotEvasion.detect_google_bot_block(mock_page)
        
        assert is_blocked is False
    
    @pytest.mark.asyncio
    async def test_google_bot_block_detection_true(self):
        """Test bot block detection returns True for blocked page"""
        mock_page = AsyncMock()
        mock_page.content.return_value = "Please try again in a few moments. We're sorry, but you have sent too many requests lately."
        
        is_blocked = await BotEvasion.detect_google_bot_block(mock_page)
        
        assert is_blocked is True
    
    def test_rate_limit_detection_429(self):
        """Test rate limit detection for 429 status"""
        is_rate_limited = BotEvasion.detect_rate_limit(429)
        
        assert is_rate_limited is True
    
    def test_rate_limit_detection_403(self):
        """Test rate limit detection for 403 status"""
        is_rate_limited = BotEvasion.detect_rate_limit(403)
        
        assert is_rate_limited is True
    
    def test_rate_limit_detection_200(self):
        """Test rate limit detection returns False for normal status"""
        is_rate_limited = BotEvasion.detect_rate_limit(200)
        
        assert is_rate_limited is False


class TestProxyRotator:
    """Test proxy rotation functionality"""
    
    def test_initialization_with_proxies(self):
        """Test initializing rotator with proxy pool"""
        proxy_pool = ['http://proxy1:8080', 'http://proxy2:8080', 'http://proxy3:8080']
        rotator = ProxyRotator(proxy_pool=proxy_pool)
        
        assert len(rotator.proxy_pool) == 3
        assert rotator.current_index == 0
    
    def test_initialization_empty_pool(self):
        """Test initializing rotator with empty pool"""
        rotator = ProxyRotator(proxy_pool=[])
        
        assert len(rotator.proxy_pool) == 0
    
    def test_add_proxy(self):
        """Test adding proxy to pool"""
        rotator = ProxyRotator(proxy_pool=['http://proxy1:8080'])
        rotator.add_proxy('http://proxy2:8080')
        
        assert len(rotator.proxy_pool) == 2
        assert 'http://proxy2:8080' in rotator.proxy_pool
    
    def test_remove_proxy(self):
        """Test removing proxy from pool"""
        proxy_pool = ['http://proxy1:8080', 'http://proxy2:8080']
        rotator = ProxyRotator(proxy_pool=proxy_pool)
        
        rotator.remove_proxy('http://proxy1:8080')
        
        assert len(rotator.proxy_pool) == 1
        assert 'http://proxy2:8080' in rotator.proxy_pool
    
    def test_session_bound_consistency(self):
        """Test that same session always gets same proxy"""
        proxy_pool = ['http://proxy1:8080', 'http://proxy2:8080', 'http://proxy3:8080']
        rotator = ProxyRotator(proxy_pool=proxy_pool)
        
        session_id = 'session_123'
        
        proxy1 = rotator.get_proxy_for_session(session_id)
        proxy2 = rotator.get_proxy_for_session(session_id)
        proxy3 = rotator.get_proxy_for_session(session_id)
        
        # Same session should get same proxy
        assert proxy1 == proxy2 == proxy3
    
    def test_round_robin_rotation(self):
        """Test round-robin proxy rotation"""
        proxy_pool = ['proxy1', 'proxy2', 'proxy3']
        rotator = ProxyRotator(proxy_pool=proxy_pool, strategy=ProxyRotationStrategy.ROUND_ROBIN)
        
        # Get proxies for different sessions
        proxies = []
        for i in range(9):  # Get 9 proxies (3 cycles)
            session_id = f'session_{i}'
            proxy = rotator.get_proxy_for_session(session_id)
            proxies.append(proxy)
        
        # Should cycle through proxies
        assert proxies[0] == 'proxy1'
        assert proxies[1] == 'proxy2'
        assert proxies[2] == 'proxy3'
        assert proxies[3] == 'proxy1'
    
    def test_random_rotation(self):
        """Test random proxy rotation"""
        proxy_pool = ['proxy1', 'proxy2', 'proxy3', 'proxy4', 'proxy5']
        rotator = ProxyRotator(proxy_pool=proxy_pool, strategy=ProxyRotationStrategy.RANDOM)
        
        proxies = []
        for i in range(20):
            session_id = f'session_{i}'
            proxy = rotator.get_proxy_for_session(session_id)
            proxies.append(proxy)
        
        # With random selection, should have variation
        assert len(set(proxies)) > 1
    
    def test_least_used_rotation(self):
        """Test least-used proxy selection"""
        proxy_pool = ['proxy1', 'proxy2', 'proxy3']
        rotator = ProxyRotator(proxy_pool=proxy_pool, strategy=ProxyRotationStrategy.LEAST_USED)
        
        proxies = []
        for i in range(6):
            session_id = f'session_{i}'
            proxy = rotator.get_proxy_for_session(session_id)
            proxies.append(proxy)
        
        # First 3 should be one of each
        assert set(proxies[:3]) == {'proxy1', 'proxy2', 'proxy3'}
    
    def test_report_success(self):
        """Test reporting success"""
        proxy_pool = ['proxy1']
        rotator = ProxyRotator(proxy_pool=proxy_pool)
        
        session_id = 'session_1'
        rotator.get_proxy_for_session(session_id)
        rotator.report_success(session_id)
        
        # Should not raise error
        assert True
    
    def test_report_failure(self):
        """Test reporting failure"""
        proxy_pool = ['proxy1']
        rotator = ProxyRotator(proxy_pool=proxy_pool)
        
        session_id = 'session_1'
        rotator.get_proxy_for_session(session_id)
        rotator.report_failure(session_id, error_code=429)
        
        # Stats should record failure
        assert rotator.proxy_stats['proxy1']['failures'] > 0
    
    def test_rotate_session(self):
        """Test rotating to new proxy for session"""
        proxy_pool = ['proxy1', 'proxy2']
        rotator = ProxyRotator(proxy_pool=proxy_pool)
        
        session_id = 'session_1'
        proxy1 = rotator.get_proxy_for_session(session_id)
        
        # Rotate
        rotator.rotate_session(session_id)
        
        # Session should no longer have proxy mapped
        assert session_id not in rotator.session_proxy_map
    
    def test_get_stats(self):
        """Test getting proxy statistics"""
        proxy_pool = ['proxy1', 'proxy2']
        rotator = ProxyRotator(proxy_pool=proxy_pool)
        
        rotator.get_proxy_for_session('session_1')
        stats = rotator.get_stats()
        
        assert 'pool_size' in stats
        assert 'proxies' in stats
        assert 'sessions' in stats
        assert stats['pool_size'] == 2
        assert stats['sessions'] == 1


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
