#!/usr/bin/env python3
"""
test_api_agent.py - Unit Tests for API Agent (Gmail, YouTube, Calendar, Drive)

Tests:
- OAuth flow initialization
- Credential storage and retrieval
- Email sending
- Email reading
- OTP extraction
- Magic link extraction
"""

import pytest
import asyncio
import os
import json
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta

# Import api agent components
from agents.api_agent import (
    ApiAgent,
    ApiTask,
    ApiResult,
    TokenEncryptor,
    GmailCredential
)
# Backward-compat aliases (also verifies they exist)
EmailAgent = ApiAgent
EmailTask = ApiTask
EmailResult = ApiResult


class TestTokenEncryptor:
    """Test token encryption/decryption"""
    
    def test_token_encryption(self):
        """Test that tokens can be encrypted and decrypted"""
        encryptor = TokenEncryptor()
        original_token = "refresh_token_abc123def456"
        
        encrypted = encryptor.encrypt(original_token)
        assert encrypted != original_token
        assert isinstance(encrypted, str)
        
        decrypted = encryptor.decrypt(encrypted)
        assert decrypted == original_token
    
    def test_invalid_decryption(self):
        """Test decryption with wrong key fails gracefully"""
        encryptor1 = TokenEncryptor(key=__import__('cryptography').fernet.Fernet.generate_key().decode())
        encryptor2 = TokenEncryptor(key=__import__('cryptography').fernet.Fernet.generate_key().decode())
        
        token = "test_token"
        encrypted = encryptor1.encrypt(token)
        
        # Decryption with different key should fail
        decrypted = encryptor2.decrypt(encrypted)
        assert decrypted is None


class TestApiAgent:
    """Test ApiAgent class"""
    
    @pytest.fixture
    def agent(self):
        """Fixture: Initialize email agent"""
        return ApiAgent()
    
    @pytest.mark.asyncio
    async def test_agent_initialization(self, agent):
        """Test that email agent initializes correctly"""
        assert agent is not None
        assert agent.encryptor is not None
        assert agent.credentials_cache == {}
    
    @pytest.mark.asyncio
    async def test_oauth_flow_initiation(self, agent):
        """Test OAuth flow initiation"""
        with patch.dict(os.environ, {'GMAIL_CLIENT_ID': 'test_client_id'}):
            with patch('agents.api_agent.InstalledAppFlow') as mock_flow:
                mock_instance = MagicMock()
                mock_flow.from_client_secrets_file.return_value = mock_instance
                mock_instance.authorization_url.return_value = ('https://auth.url', 'state123')
                
                auth_url, state = await agent.initiate_oauth_flow('test_user')
                
                # OAuth URL should be generated
                assert auth_url == 'https://auth.url'
                assert state == 'state123'
    
    @pytest.mark.asyncio
    async def test_send_email_success(self, agent):
        """Test successful email sending"""
        with patch('agents.api_agent.build') as mock_build:
            # Mock Gmail service
            mock_service = MagicMock()
            mock_build.return_value = mock_service
            
            # Mock credentials retrieval
            with patch.object(agent, '_get_credentials_mongodb', new_callable=AsyncMock) as mock_get_creds:
                mock_creds = MagicMock()
                mock_creds.token = 'access_token'
                mock_get_creds.return_value = mock_creds
                
                # Mock send
                mock_users = MagicMock()
                mock_messages = MagicMock()
                mock_send = MagicMock()
                mock_service.users.return_value = mock_users
                mock_users.messages.return_value = mock_messages
                mock_messages.send.return_value = mock_send
                mock_send.execute.return_value = {'id': 'msg_123'}
                
                result = await agent.send_email(
                    user_id='test_user',
                    to='recipient@example.com',
                    subject='Test Subject',
                    body='Test Body'
                )
                
                assert result.status == 'success'
                assert result.operation == 'send'
    
    @pytest.mark.asyncio
    async def test_read_emails_success(self, agent):
        """Test reading unread emails"""
        with patch('agents.api_agent.build') as mock_build:
            mock_service = MagicMock()
            mock_build.return_value = mock_service
            
            with patch.object(agent, '_get_credentials_mongodb', new_callable=AsyncMock) as mock_get_creds:
                mock_creds = MagicMock()
                mock_creds.token = 'access_token'
                mock_get_creds.return_value = mock_creds
                
                # Mock list and get
                mock_users = MagicMock()
                mock_messages = MagicMock()
                mock_list = MagicMock()
                mock_get = MagicMock()
                
                mock_service.users.return_value = mock_users
                mock_users.messages.return_value = mock_messages
                mock_messages.list.return_value = mock_list
                mock_list.execute.return_value = {
                    'messages': [{'id': 'msg_1', 'threadId': 'thread_1'}]
                }
                mock_messages.get.return_value = mock_get
                mock_get.execute.return_value = {
                    'id': 'msg_1',
                    'payload': {
                        'headers': [
                            {'name': 'From', 'value': 'sender@example.com'},
                            {'name': 'Subject', 'value': 'Test Email'}
                        ]
                    },
                    'snippet': 'This is a test email'
                }
                
                result = await agent.read_unread_emails(user_id='test_user', max_results=10)
                
                assert result.status == 'success'
                assert result.operation == 'read'
                assert result.message_count >= 0
    
    @pytest.mark.asyncio
    async def test_extract_otp_codes(self, agent):
        """Test OTP code extraction from emails"""
        with patch.object(agent, 'read_unread_emails', new_callable=AsyncMock) as mock_read:
            # Mock email with OTP code
            mock_read.return_value = EmailResult(
                task_id='test_task',
                status='success',
                operation='read',
                result=[
                    {
                        'message_id': 'msg_1',
                        'from': 'noreply@google.com',
                        'subject': 'Verification Code',
                        'snippet': 'Your verification code is 123456'
                    }
                ],
                message_count=1
            )
            
            result = await agent.extract_otp_codes(user_id='test_user')
            
            assert result.status == 'success'
            assert result.operation == 'extract_otp'
            # OTP should be extracted
            if result.result:
                assert any('123456' in str(item) for item in result.result)
    
    @pytest.mark.asyncio
    async def test_extract_magic_links(self, agent):
        """Test magic link extraction from emails"""
        with patch.object(agent, 'read_unread_emails', new_callable=AsyncMock) as mock_read:
            # Mock email with magic link
            mock_read.return_value = EmailResult(
                task_id='test_task',
                status='success',
                operation='read',
                result=[
                    {
                        'message_id': 'msg_1',
                        'from': 'auth@example.com',
                        'subject': 'Verify Your Email',
                        'snippet': 'Click here to verify: https://example.com/verify?token=xyz789'
                    }
                ],
                message_count=1
            )
            
            result = await agent.extract_magic_links(user_id='test_user')
            
            assert result.status == 'success'
            assert result.operation == 'extract_links'
    
    @pytest.mark.asyncio
    async def test_credential_revocation(self, agent):
        """Test revoking stored credentials"""
        with patch.object(agent, 'get_mongodb_collection') as mock_get_col:
            mock_collection = MagicMock()
            mock_get_col.return_value = mock_collection
            mock_collection.update_one.return_value = MagicMock(matched_count=1)
            
            await agent.revoke_credentials(user_id='test_user')
            
            # Verify update was called
            mock_collection.update_one.assert_called_once()
            # Cache should be cleared
            assert 'test_user' not in agent.credentials_cache


class TestApiTask:
    """Test ApiTask model"""
    
    def test_email_task_creation(self):
        """Test creating email task"""
        task = EmailTask(
            operation='send',
            user_id='user123',
            to='test@example.com',
            subject='Test',
            body='Body'
        )
        
        assert task.operation == 'send'
        assert task.user_id == 'user123'
        assert task.to == 'test@example.com'
        assert hasattr(task, 'task_id')
    
    def test_email_task_for_read(self):
        """Test creating read task"""
        task = EmailTask(
            operation='read',
            user_id='user123',
            max_results=5,
            query='from:important@example.com'
        )
        
        assert task.operation == 'read'
        assert task.max_results == 5
        assert task.query == 'from:important@example.com'


class TestApiResult:
    """Test ApiResult model"""
    
    def test_success_result(self):
        """Test successful result"""
        result = EmailResult(
            task_id='task_123',
            status='success',
            operation='send',
            result={'to': 'test@example.com'},
            message_count=1
        )
        
        assert result.status == 'success'
        assert result.error is None
    
    def test_failure_result(self):
        """Test failure result"""
        result = EmailResult(
            task_id='task_123',
            status='failed',
            operation='read',
            error='Credentials not found'
        )
        
        assert result.status == 'failed'
        assert result.error == 'Credentials not found'


class TestIntegration:
    """Integration tests"""
    
    @pytest.mark.asyncio
    async def test_email_workflow_no_credentials(self):
        """Test that operations fail gracefully without credentials"""
        agent = ApiAgent()
        
        with patch.object(agent, '_get_credentials_mongodb', new_callable=AsyncMock) as mock_creds:
            mock_creds.return_value = None
            
            result = await agent.send_email(
                user_id='unknown_user',
                to='test@example.com',
                subject='Test',
                body='Test'
            )
            
            assert result.status == 'failed'
            assert 'No credentials' in result.error


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
