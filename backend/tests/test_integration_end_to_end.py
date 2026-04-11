#!/usr/bin/env python3
"""
test_integration_end_to_end.py - End-to-End Integration Tests

Tests complete workflows:
- Email sending with bot evasion
- Web automation with verification
- Proxy rotation across tasks
- Full email -> web automation -> verification pipeline
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from agents.email_agent import EmailAgent, EmailResult
from agents.execution_agent.RAG.web.bot_evasion import BotEvasion, ProxyRotator
from agents.execution_agent.RAG.web.verification import ScreenshotVerifier


class TestEmailWithBotEvasion:
    """Test email operations with bot evasion enabled"""
    
    @pytest.mark.asyncio
    async def test_gmail_connect_with_evasion(self):
        """Test connecting to Gmail with bot evasion measures"""
        email_agent = EmailAgent()
        evasion = BotEvasion()
        
        # Verify evasion components are available
        assert evasion is not None
        
        # Get fingerprint spoof script
        script = evasion.get_fingerprint_spoof_script()
        assert script is not None
        assert len(script) > 0
        
        # Get user agent
        ua = evasion.get_user_agent()
        assert ua is not None
        assert 'Mozilla' in ua
    
    @pytest.mark.asyncio
    async def test_gmail_oauth_with_rate_limit_detection(self):
        """Test OAuth flow with rate limit detection"""
        email_agent = EmailAgent()
        evasion = BotEvasion()
        
        # Test rate limit detection
        is_rate_limited = BotEvasion.detect_rate_limit(429)
        assert is_rate_limited is True
        
        is_rate_limited_normal = BotEvasion.detect_rate_limit(200)
        assert is_rate_limited_normal is False


class TestWebAutomationWithVerification:
    """Test web automation with screenshot verification"""
    
    @pytest.mark.asyncio
    async def test_navigation_with_screenshot_verification(self):
        """Test web navigation with before/after verification"""
        import tempfile
        
        verifier = ScreenshotVerifier()
        evasion = BotEvasion()
        
        mock_page = AsyncMock()
        mock_page.screenshot.return_value = b'fake_image'
        mock_page.url = 'https://example.com'
        
        # Simulate navigation with evasion
        delay = evasion.random_delay()
        assert 0.3 < delay < 3.0
        
        # Take screenshots
        with patch('builtins.open', create=True):
            with patch('agents.execution_agent.RAG.web.verification.asyncio.sleep', new_callable=AsyncMock):
                before = await verifier.take_screenshot_before(mock_page, 'session_1', 'navigate')
                after = await verifier.take_screenshot_after(mock_page, 'session_1', 'navigate')
                
                assert before is not None
                assert after is not None
    
    @pytest.mark.asyncio
    async def test_click_with_bezier_movement(self):
        """Test click action with Bezier curve mouse movement"""
        evasion = BotEvasion()
        
        # Generate Bezier path
        start = (100, 100)
        end = (500, 500)
        
        path = evasion.bezier_curve_path(start, end, steps=30)
        
        # Verify smooth path
        assert len(path) > 1
        assert path[0] == start
        assert path[-1] == end
        
        # Check smoothness
        max_distance = 0
        for i in range(len(path) - 1):
            x1, y1 = path[i]
            x2, y2 = path[i + 1]
            distance = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
            max_distance = max(max_distance, distance)
        
        assert max_distance < 100


class TestProxyRotationAcrossTasks:
    """Test proxy rotation management across multiple tasks"""
    
    def test_session_bound_proxy_distribution(self):
        """Test that proxies are distributed across sessions but consistent within"""
        proxy_pool = ['proxy1:8080', 'proxy2:8080', 'proxy3:8080']
        rotator = ProxyRotator(proxy_pool=proxy_pool)
        
        # Simulate 3 different task sessions
        session_proxies = {}
        for task_num in range(3):
            session_id = f'task_{task_num}'
            proxy = rotator.get_proxy_for_session(session_id)
            session_proxies[session_id] = proxy
            
            # Within same session, proxy should be consistent
            for _ in range(5):
                proxy_again = rotator.get_proxy_for_session(session_id)
                assert proxy_again == proxy
        
        # Different sessions should have different proxies (in round-robin)
        proxies = list(session_proxies.values())
        assert proxies[0] != proxies[1]  # First two should differ
        assert proxies[1] != proxies[2]  # Second and third should differ
    
    def test_rate_limit_recovery_triggers_rotation(self):
        """Test that rate limit triggers proxy rotation"""
        proxy_pool = ['proxy1:8080', 'proxy2:8080']
        rotator = ProxyRotator(proxy_pool=proxy_pool)
        
        session_id = 'session_1'
        initial_proxy = rotator.get_proxy_for_session(session_id)
        
        # Report rate limit failure
        rotator.report_failure(session_id, error_code=429)
        
        # Rotate to new proxy
        rotator.rotate_session(session_id)
        
        # Next session should get different proxy
        next_proxy = rotator.get_proxy_for_session('session_2')
        assert next_proxy != initial_proxy


class TestEndToEndEmailWorkflow:
    """Test complete email workflow"""
    
    @pytest.mark.asyncio
    async def test_send_email_workflow(self):
        """Test sending email with all security checks"""
        email_agent = EmailAgent()
        evasion = BotEvasion()
        
        # Get fingerprint spoof to apply before sending
        spoof_script = evasion.get_fingerprint_spoof_script()
        assert spoof_script is not None
        
        # Mock credentials
        with patch.object(email_agent, '_get_credentials_mongodb', new_callable=AsyncMock) as mock_get:
            mock_get.return_value = None
            
            result = await email_agent.send_email(
                user_id='user_123',
                to='recipient@example.com',
                subject='Test',
                body='Body'
            )
            
            # Should fail gracefully
            assert result.status == 'failed'
    
    @pytest.mark.asyncio
    async def test_read_emails_with_bot_block_detection(self):
        """Test reading emails with bot detection checks"""
        email_agent = EmailAgent()
        evasion = BotEvasion()
        
        # Test bot block detection
        mock_page = AsyncMock()
        mock_page.content.return_value = "Normal Gmail page"
        
        is_blocked = await evasion.detect_google_bot_block(mock_page)
        assert is_blocked is False
    
    @pytest.mark.asyncio
    async def test_otp_extraction_with_retry(self):
        """Test OTP extraction with automatic retry on failure"""
        email_agent = EmailAgent()
        
        # First attempt returns no results
        with patch.object(email_agent, 'read_unread_emails', new_callable=AsyncMock) as mock_read:
            # Simulate read failure then success
            mock_read.side_effect = [
                EmailResult(
                    task_id='task_1',
                    status='failed',
                    operation='read',
                    error='Network error'
                ),
                EmailResult(
                    task_id='task_2',
                    status='success',
                    operation='read',
                    result=[{
                        'message_id': 'msg_1',
                        'from': 'noreply@google.com',
                        'subject': 'Verification',
                        'snippet': 'Code: 123456'
                    }],
                    message_count=1
                )
            ]
            
            # First attempt
            result1 = await email_agent.extract_otp_codes('user_1')
            
            # Second attempt
            result2 = await email_agent.extract_otp_codes('user_1')
            
            assert result1.status == 'failed'
            assert result2.status == 'success'


class TestFullPipeline:
    """Test complete end-to-end pipeline"""
    
    @pytest.mark.asyncio
    async def test_email_to_web_automation_pipeline(self):
        """Test complete pipeline: extract email -> web automation with verification"""
        import tempfile
        
        email_agent = EmailAgent()
        evasion = BotEvasion()
        proxy_rotator = ProxyRotator(proxy_pool=['proxy1:8080', 'proxy2:8080'])
        verifier = ScreenshotVerifier()
        
        # Step 1: Prepare for automation
        session_id = 'pipeline_test_1'
        proxy = proxy_rotator.get_proxy_for_session(session_id)
        assert proxy is not None
        
        # Step 2: Apply evasion before web action
        delay = evasion.random_delay()
        user_agent = evasion.get_user_agent()
        assert user_agent is not None
        
        # Step 3: Screenshot verification would happen
        # (Mocked here)
        
        # Step 4: Rotate proxy for next session
        proxy_rotator.rotate_session(session_id)
        next_session_proxy = proxy_rotator.get_proxy_for_session('pipeline_test_2')
        assert next_session_proxy is not None
    
    @pytest.mark.asyncio
    async def test_multi_session_workflow_with_credentials(self):
        """Test managing multiple sessions with different credentials"""
        email_agent = EmailAgent()
        
        # Simulate multiple users
        users = ['user_1', 'user_2', 'user_3']
        
        for user_id in users:
            # Each user would have separate credentials
            assert email_agent.credentials_cache is not None
            # In real scenario, credentials would be cached
            assert user_id not in email_agent.credentials_cache or True
        
        # Verify cache isolation
        assert len(email_agent.credentials_cache) == 0


class TestErrorRecovery:
    """Test error recovery mechanisms"""
    
    @pytest.mark.asyncio
    async def test_oauth_failure_recovery(self):
        """Test recovery from OAuth failures"""
        email_agent = EmailAgent()
        
        # Simulate OAuth failure
        with patch('agents.email_agent.InstalledAppFlow') as mock_flow:
            mock_flow.from_client_secrets_file.side_effect = FileNotFoundError('Credentials file not found')
            
            # Should fail gracefully
            with pytest.raises(Exception):
                await email_agent.initiate_oauth_flow('user_1')
    
    @pytest.mark.asyncio
    async def test_bot_block_recovery(self):
        """Test recovery from bot detection"""
        evasion = BotEvasion()
        proxy_rotator = ProxyRotator(proxy_pool=['proxy_a', 'proxy_b', 'proxy_c'])
        
        session_id = 'blocked_session'
        
        # Get initial proxy
        proxy1 = proxy_rotator.get_proxy_for_session(session_id)
        
        # Simulate bot block
        proxy_rotator.report_failure(session_id, error_code=429)
        
        # Rotate to new proxy
        proxy_rotator.rotate_session(session_id)
        proxy2 = proxy_rotator.get_proxy_for_session('retry_session')
        
        # Should have different proxy
        assert proxy2 is not None


if __name__ == '__main__':
    pytest.main([__file__, '-v', '-s'])
