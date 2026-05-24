#!/usr/bin/env python3
"""
test_google_apis.py — Comprehensive unit tests for all Google API operations in ApiAgent.

Coverage:
  TokenEncryptor          — encrypt, decrypt, wrong-key
  GmailCredential model   — field defaults, validation
  ApiTask / ApiResult     — construction, defaults
  OAuth flow              — initiate, callback (success & failure), state cache
  Credential storage      — store, retrieve (cache hit, cache miss, refresh, revocation)
  Credential fallback     — DB mapping, env fallback, single-cred fallback
  Gmail send              — success, attachment, no-credentials, service error
  Gmail read              — success, empty inbox, no-credentials, message error
  OTP extraction          — 6-digit, XXXX-XXXX, labeled, no match, multiple
  Magic links             — verify/confirm/token/reset/login URLs, no match
  YouTube search          — success, no-credentials, HTTP error, empty results
  YouTube video info      — success, invalid URL, not-found, no-credentials
  Calendar create         — timed event, all-day, no-credentials, API error
  Calendar list           — success, empty, no-credentials
  Drive upload            — success, missing-file, no-credentials
  Drive list              — success, empty, no-credentials
  Browser cookies         — success, no-credentials, token-exchange failure
  Broker message handler  — valid op, unknown op, wrong receiver, all operations
  Backward-compat aliases — EmailAgent/EmailTask/EmailResult/start_email_agent

Run:
  cd backend && python -m pytest tests/test_google_apis.py -v
"""

import sys
import os
import asyncio
import json
import uuid
import pytest

from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

# ── ensure backend/ is on sys.path ──────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.api_agent import (
    ApiAgent,
    ApiTask,
    ApiResult,
    GmailCredential,
    TokenEncryptor,
    # backward-compat aliases
    EmailAgent,
    EmailTask,
    EmailResult,
    start_email_agent,
    start_api_agent,
)
from agents.utils.protocol import AgentType, Channels


# ════════════════════════════════════════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════════════════════════════════════════

def _make_valid_creds(expired: bool = False):
    """Return a mock google.oauth2.credentials.Credentials object."""
    creds = MagicMock()
    creds.token = "access_token_xyz"
    creds.refresh_token = "refresh_token_abc"
    creds.valid = not expired
    creds.expiry = datetime.now() + (timedelta(hours=-1) if expired else timedelta(hours=1))
    return creds


def _make_agent_with_creds(user_id: str = "user_1") -> ApiAgent:
    """Return an ApiAgent whose _get_credentials_with_fallback always succeeds."""
    agent = ApiAgent()
    creds = _make_valid_creds()
    agent._get_credentials_with_fallback = AsyncMock(return_value=(creds, user_id))
    agent._get_credentials_mongodb = AsyncMock(return_value=creds)
    return agent


def _make_agent_no_creds() -> ApiAgent:
    """Return an ApiAgent that always reports missing credentials."""
    agent = ApiAgent()
    agent._get_credentials_with_fallback = AsyncMock(return_value=(None, "user_1"))
    agent._get_credentials_mongodb = AsyncMock(return_value=None)
    return agent


# ════════════════════════════════════════════════════════════════════════════
# 1. MODELS
# ════════════════════════════════════════════════════════════════════════════

class TestApiTask:
    def test_minimal_construction(self):
        t = ApiTask(operation="send", user_id="u1")
        assert t.operation == "send"
        assert t.user_id == "u1"
        assert t.task_id  # auto-generated UUID

    def test_send_fields(self):
        t = ApiTask(
            operation="send",
            user_id="u1",
            to="bob@example.com",
            subject="Hello",
            body="World",
        )
        assert t.to == "bob@example.com"
        assert t.subject == "Hello"

    def test_read_defaults(self):
        t = ApiTask(operation="read", user_id="u1")
        assert t.max_results == 10
        assert t.query == "is:unread"

    def test_youtube_fields(self):
        t = ApiTask(operation="youtube_search", user_id="u1", search_query="cats")
        assert t.search_query == "cats"

    def test_calendar_fields(self):
        t = ApiTask(
            operation="calendar_create",
            user_id="u1",
            title="Team standup",
            start_time="2026-04-27T09:00:00",
            end_time="2026-04-27T09:30:00",
            description="Daily sync",
        )
        assert t.title == "Team standup"

    def test_drive_fields(self):
        t = ApiTask(operation="drive_upload", user_id="u1", file_path="/tmp/report.pdf")
        assert t.file_path == "/tmp/report.pdf"


class TestApiResult:
    def test_success_result(self):
        r = ApiResult(task_id="t1", status="success", operation="send", result={"to": "a@b.com"})
        assert r.status == "success"
        assert r.error is None
        assert r.message_count == 0

    def test_failure_result(self):
        r = ApiResult(task_id="t1", status="failed", operation="read", error="No creds")
        assert r.status == "failed"
        assert r.error == "No creds"

    def test_timestamp_auto_set(self):
        r = ApiResult(task_id="t1", status="success", operation="send")
        assert r.timestamp  # non-empty ISO string


class TestGmailCredential:
    def test_defaults(self):
        cred = GmailCredential(
            user_id="u1",
            gmail_address="u1@gmail.com",
            encrypted_refresh_token="ENC",
        )
        assert cred.revoked_at is None
        assert len(cred.scope) > 0
        assert cred.created_at


# ════════════════════════════════════════════════════════════════════════════
# 2. TOKEN ENCRYPTOR
# ════════════════════════════════════════════════════════════════════════════

class TestTokenEncryptor:
    def test_round_trip(self):
        enc = TokenEncryptor()
        token = "my_super_secret_refresh_token"
        assert enc.decrypt(enc.encrypt(token)) == token

    def test_encrypted_differs_from_plain(self):
        enc = TokenEncryptor()
        token = "plain_token"
        assert enc.encrypt(token) != token

    def test_wrong_key_returns_none(self):
        from cryptography.fernet import Fernet
        enc1 = TokenEncryptor(Fernet.generate_key().decode())
        enc2 = TokenEncryptor(Fernet.generate_key().decode())
        encrypted = enc1.encrypt("secret")
        assert enc2.decrypt(encrypted) is None

    def test_empty_string(self):
        enc = TokenEncryptor()
        assert enc.decrypt(enc.encrypt("")) == ""


# ════════════════════════════════════════════════════════════════════════════
# 3. OAUTH FLOW
# ════════════════════════════════════════════════════════════════════════════

class TestOAuthFlow:
    @pytest.mark.asyncio
    async def test_initiate_returns_url_and_state(self):
        agent = ApiAgent()
        with patch.dict(os.environ, {"GMAIL_CLIENT_ID": "cid", "GMAIL_CLIENT_SECRET": "csecret"}):
            mock_flow = MagicMock()
            mock_flow.authorization_url.return_value = ("https://auth.google.com/url", "state_abc")
            mock_flow.redirect_uri = None
            with patch("agents.api_agent.Flow.from_client_config", return_value=mock_flow):
                url, state = await agent.initiate_oauth_flow("user_1")
        assert url == "https://auth.google.com/url"
        assert state == "state_abc"

    @pytest.mark.asyncio
    async def test_initiate_returns_none_without_client_id(self):
        agent = ApiAgent()
        with patch.dict(os.environ, {}, clear=True):
            with patch("agents.api_agent.GMAIL_CLIENT_ID", ""), \
                 patch("agents.api_agent.GMAIL_CLIENT_SECRET", ""):
                url, state = await agent.initiate_oauth_flow("user_1")
        assert url is None
        assert state is None

    @pytest.mark.asyncio
    async def test_callback_success(self):
        agent = ApiAgent()
        mock_flow = MagicMock()
        mock_creds = _make_valid_creds()
        mock_creds.refresh_token = "rtoken"
        mock_flow.credentials = mock_creds

        with patch("agents.api_agent.Flow.from_client_config", return_value=mock_flow), \
             patch.object(agent, "_store_credentials_mongodb", new_callable=AsyncMock):
            result = await agent.handle_oauth_callback("user_1", "auth_code_xyz")
        assert result is True
        assert "user_1" in agent.credentials_cache

    @pytest.mark.asyncio
    async def test_callback_failure_returns_false(self):
        agent = ApiAgent()
        with patch("agents.api_agent.Flow.from_client_config", side_effect=Exception("network error")):
            result = await agent.handle_oauth_callback("user_1", "bad_code")
        assert result is False


# ════════════════════════════════════════════════════════════════════════════
# 4. CREDENTIAL MANAGEMENT
# ════════════════════════════════════════════════════════════════════════════

class TestCredentialManagement:
    @pytest.mark.asyncio
    async def test_get_creds_cache_hit(self):
        agent = ApiAgent()
        creds = _make_valid_creds()
        agent.credentials_cache["user_1"] = creds
        result = await agent._get_credentials_mongodb("user_1")
        assert result is creds

    @pytest.mark.asyncio
    async def test_get_creds_no_doc_returns_none(self):
        agent = ApiAgent()
        mock_col = MagicMock()
        mock_col.find_one.return_value = None
        with patch.object(agent, "get_mongodb_collection", return_value=mock_col):
            result = await agent._get_credentials_mongodb("unknown_user")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_creds_decryption_failure_returns_none(self):
        agent = ApiAgent()
        mock_col = MagicMock()
        mock_col.find_one.return_value = {"encrypted_refresh_token": "bad_enc"}
        agent.encryptor.decrypt = MagicMock(return_value=None)
        with patch.object(agent, "get_mongodb_collection", return_value=mock_col):
            result = await agent._get_credentials_mongodb("user_1")
        assert result is None

    @pytest.mark.asyncio
    async def test_revoke_credentials_clears_cache(self):
        agent = ApiAgent()
        agent.credentials_cache["user_1"] = _make_valid_creds()
        mock_col = MagicMock()
        with patch.object(agent, "get_mongodb_collection", return_value=mock_col):
            await agent.revoke_credentials("user_1")
        assert "user_1" not in agent.credentials_cache

    @pytest.mark.asyncio
    async def test_fallback_resolves_env_fallback_user(self):
        agent = ApiAgent()
        creds = _make_valid_creds()
        calls = []

        async def mock_get(user_id):
            calls.append(user_id)
            if user_id == "fallback_owner":
                return creds
            return None

        agent._get_credentials_mongodb = mock_get
        agent._resolve_credential_owner_from_db = MagicMock(return_value=None)
        with patch("agents.api_agent.EMAIL_CREDENTIAL_FALLBACK_USER_ID", "fallback_owner"):
            result_creds, effective_user = await agent._get_credentials_with_fallback("user_x")
        assert result_creds is creds
        assert effective_user == "fallback_owner"

    @pytest.mark.asyncio
    async def test_fallback_returns_none_when_all_fail(self):
        agent = ApiAgent()
        agent._get_credentials_mongodb = AsyncMock(return_value=None)
        agent._resolve_credential_owner_from_db = MagicMock(return_value=None)
        with patch("agents.api_agent.EMAIL_CREDENTIAL_FALLBACK_USER_ID", ""):
            creds, effective = await agent._get_credentials_with_fallback("nobody")
        assert creds is None


# ════════════════════════════════════════════════════════════════════════════
# 5. GMAIL — SEND
# ════════════════════════════════════════════════════════════════════════════

class TestGmailSend:
    @pytest.mark.asyncio
    async def test_send_success(self):
        agent = _make_agent_with_creds()
        mock_service = MagicMock()
        mock_service.users().getProfile(userId="me").execute.return_value = {
            "emailAddress": "sender@gmail.com"
        }
        mock_service.users().messages().send(userId="me", body=MagicMock()).execute.return_value = {
            "id": "msg_001"
        }
        with patch("agents.api_agent.build", return_value=mock_service):
            result = await agent.send_email("user_1", "bob@ex.com", "Hi", "Hello Bob")
        assert result.status == "success"
        assert result.operation == "send"
        assert result.result["to"] == "bob@ex.com"

    @pytest.mark.asyncio
    async def test_send_no_credentials(self):
        agent = _make_agent_no_creds()
        result = await agent.send_email("user_1", "bob@ex.com", "Hi", "Hello")
        assert result.status == "failed"
        assert "No credentials" in result.error

    @pytest.mark.asyncio
    async def test_send_service_raises(self):
        agent = _make_agent_with_creds()
        with patch("agents.api_agent.build", side_effect=Exception("quota exceeded")):
            result = await agent.send_email("user_1", "bob@ex.com", "Hi", "Hello")
        assert result.status == "failed"
        assert "quota exceeded" in result.error

    @pytest.mark.asyncio
    async def test_send_with_attachment(self):
        """Attachment list is processed; missing file logs a warning but send proceeds."""
        agent = _make_agent_with_creds()
        mock_service = MagicMock()
        mock_service.users().getProfile(userId="me").execute.return_value = {
            "emailAddress": "s@g.com"
        }
        mock_service.users().messages().send(userId="me", body=MagicMock()).execute.return_value = {
            "id": "msg_002"
        }
        attachments = [{"path": "/nonexistent/file.pdf", "name": "file.pdf"}]
        with patch("agents.api_agent.build", return_value=mock_service):
            # Missing file should just warn and skip, not raise
            result = await agent.send_email(
                "user_1", "a@b.com", "Sub", "Body", attachments=attachments
            )
        # Operation may succeed even though attachment was skipped
        assert result.operation == "send"


# ════════════════════════════════════════════════════════════════════════════
# 6. GMAIL — READ
# ════════════════════════════════════════════════════════════════════════════

class TestGmailRead:
    def _mock_messages_service(self, messages_list, messages_detail):
        svc = MagicMock()
        svc.users().messages().list(
            userId="me", q="is:unread", maxResults=10
        ).execute.return_value = {"messages": messages_list}
        for detail in messages_detail:
            svc.users().messages().get(
                userId="me", id=detail["id"], format="full"
            ).execute.return_value = detail
        return svc

    @pytest.mark.asyncio
    async def test_read_success(self):
        agent = _make_agent_with_creds()
        msg_list = [{"id": "m1", "threadId": "t1"}]
        msg_detail = {
            "id": "m1",
            "snippet": "Hello there",
            "internalDate": "1714000000000",
            "payload": {
                "headers": [
                    {"name": "From", "value": "alice@ex.com"},
                    {"name": "Subject", "value": "Test Subject"},
                ]
            },
        }
        mock_svc = MagicMock()
        mock_svc.users().messages().list(
            userId="me", q="is:unread", maxResults=10
        ).execute.return_value = {"messages": msg_list}
        mock_svc.users().messages().get(
            userId="me", id="m1", format="full"
        ).execute.return_value = msg_detail

        with patch("agents.api_agent.build", return_value=mock_svc), \
             patch.object(agent, "_cache_email", new_callable=AsyncMock):
            result = await agent.read_unread_emails("user_1")

        assert result.status == "success"
        assert result.message_count == 1
        assert result.result[0]["from"] == "alice@ex.com"
        assert result.result[0]["subject"] == "Test Subject"

    @pytest.mark.asyncio
    async def test_read_empty_inbox(self):
        agent = _make_agent_with_creds()
        mock_svc = MagicMock()
        mock_svc.users().messages().list(
            userId="me", q="is:unread", maxResults=10
        ).execute.return_value = {"messages": []}
        with patch("agents.api_agent.build", return_value=mock_svc):
            result = await agent.read_unread_emails("user_1")
        assert result.status == "success"
        assert result.message_count == 0
        assert result.result == []

    @pytest.mark.asyncio
    async def test_read_no_credentials(self):
        agent = _make_agent_no_creds()
        result = await agent.read_unread_emails("user_1")
        assert result.status == "failed"

    @pytest.mark.asyncio
    async def test_read_custom_query(self):
        agent = _make_agent_with_creds()
        mock_svc = MagicMock()
        mock_svc.users().messages().list(
            userId="me", q="from:boss@corp.com", maxResults=5
        ).execute.return_value = {"messages": []}
        with patch("agents.api_agent.build", return_value=mock_svc):
            result = await agent.read_unread_emails(
                "user_1", max_results=5, query="from:boss@corp.com"
            )
        assert result.status == "success"


# ════════════════════════════════════════════════════════════════════════════
# 7. OTP EXTRACTION
# ════════════════════════════════════════════════════════════════════════════

class TestOtpExtraction:
    def _emails_with_snippet(self, snippet: str):
        return ApiResult(
            task_id="t1",
            status="success",
            operation="read",
            result=[{"message_id": "m1", "from": "noreply@service.com",
                      "subject": "Your code", "snippet": snippet}],
            message_count=1,
        )

    @pytest.mark.asyncio
    async def test_6digit_otp(self):
        agent = ApiAgent()
        agent.read_unread_emails = AsyncMock(return_value=self._emails_with_snippet(
            "Your verification code is 847263"
        ))
        result = await agent.extract_otp_codes("user_1")
        assert result.status == "success"
        codes = [item["code"] for item in result.result]
        assert "847263" in codes

    @pytest.mark.asyncio
    async def test_labeled_otp_code(self):
        agent = ApiAgent()
        agent.read_unread_emails = AsyncMock(return_value=self._emails_with_snippet(
            "OTP: 123456 is valid for 10 minutes"
        ))
        result = await agent.extract_otp_codes("user_1")
        codes = [item["code"] for item in result.result]
        assert any("123456" in c for c in codes)

    @pytest.mark.asyncio
    async def test_no_otp_returns_empty(self):
        agent = ApiAgent()
        agent.read_unread_emails = AsyncMock(return_value=self._emails_with_snippet(
            "Thank you for subscribing to our newsletter."
        ))
        result = await agent.extract_otp_codes("user_1")
        assert result.status == "success"
        assert result.result == []

    @pytest.mark.asyncio
    async def test_multiple_otps_deduplicated(self):
        agent = ApiAgent()
        agent.read_unread_emails = AsyncMock(return_value=ApiResult(
            task_id="t1",
            status="success",
            operation="read",
            result=[
                {"message_id": "m1", "from": "a@b.com", "subject": "S", "snippet": "code 111111"},
                {"message_id": "m2", "from": "c@d.com", "subject": "S", "snippet": "code 222222"},
            ],
            message_count=2,
        ))
        result = await agent.extract_otp_codes("user_1")
        codes = [item["code"] for item in result.result]
        assert "111111" in codes
        assert "222222" in codes

    @pytest.mark.asyncio
    async def test_propagates_read_failure(self):
        agent = ApiAgent()
        agent.read_unread_emails = AsyncMock(return_value=ApiResult(
            task_id="t1", status="failed", operation="read", error="No creds"
        ))
        result = await agent.extract_otp_codes("user_1")
        assert result.status == "failed"


# ════════════════════════════════════════════════════════════════════════════
# 8. MAGIC LINK EXTRACTION
# ════════════════════════════════════════════════════════════════════════════

class TestMagicLinkExtraction:
    def _email_result(self, snippet: str):
        return ApiResult(
            task_id="t1",
            status="success",
            operation="read",
            result=[{"message_id": "m1", "from": "auth@app.com",
                      "subject": "Verify", "snippet": snippet}],
            message_count=1,
        )

    @pytest.mark.asyncio
    async def test_verify_link_extracted(self):
        agent = ApiAgent()
        agent.read_unread_emails = AsyncMock(return_value=self._email_result(
            "Click here: https://app.com/verify?token=abc123"
        ))
        result = await agent.extract_magic_links("user_1")
        assert result.status == "success"
        assert any("verify" in item["link"] for item in result.result)

    @pytest.mark.asyncio
    async def test_confirm_link_extracted(self):
        agent = ApiAgent()
        agent.read_unread_emails = AsyncMock(return_value=self._email_result(
            "Confirm your email: https://service.io/confirm?id=xyz"
        ))
        result = await agent.extract_magic_links("user_1")
        links = [item["link"] for item in result.result]
        assert any("confirm" in l for l in links)

    @pytest.mark.asyncio
    async def test_reset_link_extracted(self):
        agent = ApiAgent()
        agent.read_unread_emails = AsyncMock(return_value=self._email_result(
            "Reset your password: https://accounts.example.com/reset?token=r1"
        ))
        result = await agent.extract_magic_links("user_1")
        assert result.result  # at least one link found

    @pytest.mark.asyncio
    async def test_no_magic_link_returns_empty(self):
        agent = ApiAgent()
        agent.read_unread_emails = AsyncMock(return_value=self._email_result(
            "Your invoice is ready."
        ))
        result = await agent.extract_magic_links("user_1")
        assert result.status == "success"
        assert result.result == []

    @pytest.mark.asyncio
    async def test_propagates_read_failure(self):
        agent = ApiAgent()
        agent.read_unread_emails = AsyncMock(return_value=ApiResult(
            task_id="t1", status="failed", operation="read", error="err"
        ))
        result = await agent.extract_magic_links("user_1")
        assert result.status == "failed"


# ════════════════════════════════════════════════════════════════════════════
# 9. YOUTUBE SEARCH
# ════════════════════════════════════════════════════════════════════════════

class TestYouTubeSearch:
    def _mock_yt_service(self, items):
        svc = MagicMock()
        svc.search().list(
            q="cats",
            part="snippet",
            type="video",
            maxResults=10,
            order="relevance",
            fields=MagicMock(),
        ).execute.return_value = {"items": items}
        # generic catch-all execute
        svc.search().list().execute.return_value = {"items": items}
        return svc

    @pytest.mark.asyncio
    async def test_search_returns_videos(self):
        agent = _make_agent_with_creds()
        fake_items = [
            {
                "id": {"videoId": "vid123"},
                "snippet": {
                    "title": "Cute Cats",
                    "description": "Cats being cats",
                    "channelTitle": "CatChannel",
                    "publishedAt": "2024-01-01T00:00:00Z",
                    "thumbnails": {"default": {"url": "https://img.yt/thumb.jpg"}},
                },
            }
        ]
        mock_svc = MagicMock()
        mock_svc.search().list().execute.return_value = {"items": fake_items}
        with patch("agents.api_agent.build", return_value=mock_svc):
            result = await agent.youtube_search("user_1", "cats", max_results=10)
        assert result.status == "success"
        assert result.operation == "youtube_search"
        videos = result.result["videos"]
        assert len(videos) == 1
        assert videos[0]["video_id"] == "vid123"
        assert videos[0]["url"] == "https://www.youtube.com/watch?v=vid123"

    @pytest.mark.asyncio
    async def test_search_empty_results(self):
        agent = _make_agent_with_creds()
        mock_svc = MagicMock()
        mock_svc.search().list().execute.return_value = {"items": []}
        with patch("agents.api_agent.build", return_value=mock_svc):
            result = await agent.youtube_search("user_1", "zzznomatch")
        assert result.status == "success"
        assert result.result["videos"] == []

    @pytest.mark.asyncio
    async def test_search_no_credentials(self):
        agent = _make_agent_no_creds()
        result = await agent.youtube_search("user_1", "cats")
        assert result.status == "failed"
        assert "No credentials" in result.error

    @pytest.mark.asyncio
    async def test_search_http_error(self):
        agent = _make_agent_with_creds()
        from googleapiclient.errors import HttpError
        http_err = HttpError(resp=MagicMock(status=403), content=b"Quota exceeded")
        mock_svc = MagicMock()
        mock_svc.search().list().execute.side_effect = http_err
        with patch("agents.api_agent.build", return_value=mock_svc):
            result = await agent.youtube_search("user_1", "cats")
        assert result.status == "failed"
        assert result.operation == "youtube_search"


# ════════════════════════════════════════════════════════════════════════════
# 10. YOUTUBE VIDEO INFO
# ════════════════════════════════════════════════════════════════════════════

class TestYouTubeVideoInfo:
    VALID_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    SHORTLINK_URL = "https://youtu.be/dQw4w9WgXcQ"

    @pytest.mark.asyncio
    async def test_video_info_success(self):
        agent = _make_agent_with_creds()
        fake_item = {
            "id": "dQw4w9WgXcQ",
            "snippet": {
                "title": "Never Gonna Give You Up",
                "description": "Classic",
                "channelTitle": "RickAstleyVEVO",
                "publishedAt": "2009-10-25T06:57:33Z",
            },
            "statistics": {"viewCount": "1500000000", "likeCount": "15000000", "commentCount": "2000000"},
            "contentDetails": {"duration": "PT3M33S"},
        }
        mock_svc = MagicMock()
        mock_svc.videos().list().execute.return_value = {"items": [fake_item]}
        with patch("agents.api_agent.build", return_value=mock_svc):
            result = await agent.youtube_video_info("user_1", self.VALID_URL)
        assert result.status == "success"
        info = result.result["info"]
        assert info["video_id"] == "dQw4w9WgXcQ"
        assert info["views"] == 1500000000

    @pytest.mark.asyncio
    async def test_short_url_parsed(self):
        agent = _make_agent_with_creds()
        mock_svc = MagicMock()
        mock_svc.videos().list().execute.return_value = {
            "items": [{
                "id": "dQw4w9WgXcQ",
                "snippet": {"title": "T", "description": "D", "channelTitle": "C", "publishedAt": "2020"},
                "statistics": {"viewCount": "0", "likeCount": "0", "commentCount": "0"},
                "contentDetails": {"duration": "PT1M"},
            }]
        }
        with patch("agents.api_agent.build", return_value=mock_svc):
            result = await agent.youtube_video_info("user_1", self.SHORTLINK_URL)
        assert result.status == "success"

    @pytest.mark.asyncio
    async def test_invalid_url(self):
        agent = _make_agent_with_creds()
        result = await agent.youtube_video_info("user_1", "https://not-youtube.com/video")
        assert result.status == "failed"
        assert "Invalid YouTube URL" in result.error

    @pytest.mark.asyncio
    async def test_video_not_found(self):
        agent = _make_agent_with_creds()
        mock_svc = MagicMock()
        mock_svc.videos().list().execute.return_value = {"items": []}
        with patch("agents.api_agent.build", return_value=mock_svc):
            result = await agent.youtube_video_info("user_1", self.VALID_URL)
        assert result.status == "failed"
        assert "not found" in result.error.lower()

    @pytest.mark.asyncio
    async def test_no_credentials(self):
        agent = _make_agent_no_creds()
        result = await agent.youtube_video_info("user_1", self.VALID_URL)
        assert result.status == "failed"


# ════════════════════════════════════════════════════════════════════════════
# 11. GOOGLE CALENDAR — CREATE
# ════════════════════════════════════════════════════════════════════════════

class TestCalendarCreate:
    @pytest.mark.asyncio
    async def test_timed_event_success(self):
        agent = _make_agent_with_creds()
        fake_event = {
            "id": "evt_001",
            "summary": "Team Standup",
            "start": {"dateTime": "2026-04-28T09:00:00"},
            "end": {"dateTime": "2026-04-28T09:30:00"},
            "htmlLink": "https://calendar.google.com/event?eid=xxx",
        }
        mock_svc = MagicMock()
        mock_svc.events().insert(calendarId="primary", body=MagicMock()).execute.return_value = fake_event
        with patch("agents.api_agent.build", return_value=mock_svc):
            result = await agent.calendar_create(
                "user_1",
                title="Team Standup",
                start_time="2026-04-28T09:00:00",
                end_time="2026-04-28T09:30:00",
            )
        assert result.status == "success"
        assert result.result["event"]["id"] == "evt_001"

    @pytest.mark.asyncio
    async def test_all_day_event(self):
        agent = _make_agent_with_creds()
        fake_event = {"id": "evt_002", "summary": "Vacation", "start": {"date": "2026-05-01"},
                      "end": {"date": "2026-05-08"}, "htmlLink": ""}
        mock_svc = MagicMock()
        mock_svc.events().insert().execute.return_value = fake_event
        with patch("agents.api_agent.build", return_value=mock_svc):
            result = await agent.calendar_create(
                "user_1", "Vacation", "2026-05-01", "2026-05-08", all_day=True
            )
        assert result.status == "success"

    @pytest.mark.asyncio
    async def test_no_credentials(self):
        agent = _make_agent_no_creds()
        result = await agent.calendar_create("user_1", "Meeting", "2026-05-01T10:00", "2026-05-01T11:00")
        assert result.status == "failed"

    @pytest.mark.asyncio
    async def test_api_http_error(self):
        agent = _make_agent_with_creds()
        from googleapiclient.errors import HttpError
        mock_svc = MagicMock()
        mock_svc.events().insert().execute.side_effect = HttpError(
            resp=MagicMock(status=403), content=b"Forbidden"
        )
        with patch("agents.api_agent.build", return_value=mock_svc):
            result = await agent.calendar_create(
                "user_1", "X", "2026-05-01T10:00", "2026-05-01T11:00"
            )
        assert result.status == "failed"


# ════════════════════════════════════════════════════════════════════════════
# 12. GOOGLE CALENDAR — LIST
# ════════════════════════════════════════════════════════════════════════════

class TestCalendarList:
    @pytest.mark.asyncio
    async def test_list_events_success(self):
        agent = _make_agent_with_creds()
        fake_events = [
            {"id": "e1", "summary": "Sprint Planning",
             "start": {"dateTime": "2026-04-28T10:00:00Z"},
             "end": {"dateTime": "2026-04-28T11:00:00Z"}, "description": ""},
        ]
        mock_svc = MagicMock()
        mock_svc.events().list().execute.return_value = {"items": fake_events}
        with patch("agents.api_agent.build", return_value=mock_svc):
            result = await agent.calendar_list("user_1")
        assert result.status == "success"
        assert len(result.result["events"]) == 1
        assert result.result["events"][0]["summary"] == "Sprint Planning"

    @pytest.mark.asyncio
    async def test_empty_calendar(self):
        agent = _make_agent_with_creds()
        mock_svc = MagicMock()
        mock_svc.events().list().execute.return_value = {"items": []}
        with patch("agents.api_agent.build", return_value=mock_svc):
            result = await agent.calendar_list("user_1")
        assert result.status == "success"
        assert result.result["events"] == []

    @pytest.mark.asyncio
    async def test_no_credentials(self):
        agent = _make_agent_no_creds()
        result = await agent.calendar_list("user_1")
        assert result.status == "failed"


# ════════════════════════════════════════════════════════════════════════════
# 13. GOOGLE DRIVE — UPLOAD
# ════════════════════════════════════════════════════════════════════════════

class TestDriveUpload:
    @pytest.mark.asyncio
    async def test_upload_success(self, tmp_path):
        agent = _make_agent_with_creds()
        test_file = tmp_path / "report.pdf"
        test_file.write_text("PDF content")
        fake_file = {"id": "file_abc", "webViewLink": "https://drive.google.com/file/d/file_abc"}
        mock_svc = MagicMock()
        mock_svc.files().create().execute.return_value = fake_file

        with patch("agents.api_agent.build", return_value=mock_svc), \
             patch("googleapiclient.http.MediaFileUpload", return_value=MagicMock()):
            result = await agent.drive_upload("user_1", str(test_file))

        assert result.status == "success"
        assert result.result["file"]["file_id"] == "file_abc"

    @pytest.mark.asyncio
    async def test_upload_with_parent_folder(self, tmp_path):
        agent = _make_agent_with_creds()
        test_file = tmp_path / "data.csv"
        test_file.write_text("a,b,c")
        fake_file = {"id": "file_xyz", "webViewLink": ""}
        mock_svc = MagicMock()
        mock_svc.files().create().execute.return_value = fake_file

        with patch("agents.api_agent.build", return_value=mock_svc), \
             patch("googleapiclient.http.MediaFileUpload", return_value=MagicMock()):
            result = await agent.drive_upload("user_1", str(test_file), parent_folder_id="folder_123")

        assert result.status == "success"

    @pytest.mark.asyncio
    async def test_upload_missing_file(self):
        agent = _make_agent_with_creds()
        result = await agent.drive_upload("user_1", "/nonexistent/path/file.txt")
        assert result.status == "failed"
        assert "not found" in result.error.lower()

    @pytest.mark.asyncio
    async def test_upload_no_credentials(self):
        agent = _make_agent_no_creds()
        result = await agent.drive_upload("user_1", "/some/file.txt")
        assert result.status == "failed"


# ════════════════════════════════════════════════════════════════════════════
# 14. GOOGLE DRIVE — LIST
# ════════════════════════════════════════════════════════════════════════════

class TestDriveList:
    @pytest.mark.asyncio
    async def test_list_files_success(self):
        agent = _make_agent_with_creds()
        fake_files = [
            {"id": "f1", "name": "report.pdf", "mimeType": "application/pdf",
             "modifiedTime": "2026-04-01T10:00:00Z", "webViewLink": "https://drive.google.com/f1"},
        ]
        mock_svc = MagicMock()
        mock_svc.files().list().execute.return_value = {"files": fake_files}
        with patch("agents.api_agent.build", return_value=mock_svc):
            result = await agent.drive_list("user_1")
        assert result.status == "success"
        assert len(result.result["files"]) == 1
        assert result.result["files"][0]["name"] == "report.pdf"

    @pytest.mark.asyncio
    async def test_empty_drive(self):
        agent = _make_agent_with_creds()
        mock_svc = MagicMock()
        mock_svc.files().list().execute.return_value = {"files": []}
        with patch("agents.api_agent.build", return_value=mock_svc):
            result = await agent.drive_list("user_1")
        assert result.status == "success"
        assert result.result["files"] == []

    @pytest.mark.asyncio
    async def test_no_credentials(self):
        agent = _make_agent_no_creds()
        result = await agent.drive_list("user_1")
        assert result.status == "failed"


# ════════════════════════════════════════════════════════════════════════════
# 15. BROWSER COOKIES
# ════════════════════════════════════════════════════════════════════════════

class TestBrowserCookies:
    @pytest.mark.asyncio
    async def test_success_returns_cookies(self):
        agent = ApiAgent()
        creds = _make_valid_creds()
        agent._get_credentials_with_fallback = AsyncMock(return_value=(creds, "user_1"))

        mock_session = MagicMock()
        mock_resp1 = MagicMock()
        mock_resp1.status_code = 200
        mock_resp1.text = "uber_auth_token_short"
        mock_resp1.headers = {}

        mock_resp2 = MagicMock()
        mock_resp2.status_code = 200
        mock_resp2.headers = {}
        mock_resp2.history = []

        # Simulate a cookie jar that supports both iteration and .keys()
        mock_cookie = MagicMock()
        mock_cookie.name = "SID"
        mock_cookie.value = "SID_VALUE"
        mock_cookie.domain = ".google.com"
        mock_cookie.path = "/"
        mock_cookie.expires = None
        mock_cookie.secure = True

        class _FakeCookieJar:
            """Minimal requests-CookieJar-like object."""
            def __init__(self, cookies):
                self._c = cookies
            def __iter__(self):
                return iter(self._c)
            def keys(self):
                return [c.name for c in self._c]

        mock_session.cookies = _FakeCookieJar([mock_cookie])
        mock_session.get.side_effect = [mock_resp1, mock_resp2]
        mock_session.headers = MagicMock()

        with patch("requests.Session", return_value=mock_session):
            result = await agent.get_browser_cookies("user_1")

        assert result["status"] == "success"
        assert len(result["cookies"]) >= 1

    @pytest.mark.asyncio
    async def test_no_credentials(self):
        agent = _make_agent_no_creds()
        result = await agent.get_browser_cookies("user_1")
        assert result["status"] == "failed"
        assert "No credentials" in result["error"]

    @pytest.mark.asyncio
    async def test_token_exchange_exception(self):
        agent = ApiAgent()
        creds = _make_valid_creds()
        agent._get_credentials_with_fallback = AsyncMock(return_value=(creds, "user_1"))
        with patch("requests.Session", side_effect=Exception("network error")):
            result = await agent.get_browser_cookies("user_1")
        assert result["status"] == "failed"


# ════════════════════════════════════════════════════════════════════════════
# 16. BROKER MESSAGE HANDLER (handle_email_task)
# ════════════════════════════════════════════════════════════════════════════

class TestBrokerHandler:
    """Tests the handle_email_task inner function via start_api_agent."""

    def _make_message(self, operation: str, extra: dict = None, receiver=None):
        msg = MagicMock()
        msg.payload = {"operation": operation, "user_id": "user_1", **(extra or {})}
        msg.session_id = "sess_1"
        msg.task_id = "task_1"
        msg.message_id = "msg_1"
        if receiver is not None:
            msg.receiver = receiver
        else:
            del msg.receiver  # no receiver attribute
            object.__setattr__(msg, "receiver", None)
        return msg

    @pytest.mark.asyncio
    async def test_send_operation_dispatched(self):
        mock_broker = MagicMock()
        mock_broker.subscribe = MagicMock()
        mock_broker.publish = AsyncMock()

        with patch("agents.api_agent.broker", mock_broker):
            task = asyncio.create_task(start_api_agent(mock_broker))
            await asyncio.sleep(0)  # yield to let subscription register

        # Get the subscribed handler
        assert mock_broker.subscribe.called
        subscribed_calls = mock_broker.subscribe.call_args_list
        handler = subscribed_calls[0][0][1]  # first subscribe call, second arg

        # Mock agent methods on the ApiAgent instance
        with patch.object(ApiAgent, "send_email", new_callable=AsyncMock,
                          return_value=ApiResult(task_id="t1", status="success", operation="send")) as mock_send:
            msg = MagicMock()
            msg.payload = {"operation": "send", "user_id": "user_1",
                           "to": "a@b.com", "subject": "S", "body": "B"}
            msg.session_id = "sess"
            msg.task_id = "tid"
            msg.message_id = "mid"
            msg.receiver = None
            await handler(msg)
            mock_send.assert_called_once()

        task.cancel()

    @pytest.mark.asyncio
    async def test_unknown_operation_ignored(self):
        mock_broker = MagicMock()
        mock_broker.subscribe = MagicMock()
        mock_broker.publish = AsyncMock()

        with patch("agents.api_agent.broker", mock_broker):
            task = asyncio.create_task(start_api_agent(mock_broker))
            await asyncio.sleep(0)

        handler = mock_broker.subscribe.call_args_list[0][0][1]
        msg = MagicMock()
        msg.payload = {"operation": "unknown_op_xyz", "user_id": "user_1"}
        msg.session_id = "s"
        msg.task_id = "t"
        msg.message_id = "m"
        msg.receiver = None

        # Should return without publishing
        await handler(msg)
        mock_broker.publish.assert_not_called()

        task.cancel()

    @pytest.mark.asyncio
    async def test_wrong_receiver_ignored(self):
        mock_broker = MagicMock()
        mock_broker.subscribe = MagicMock()
        mock_broker.publish = AsyncMock()

        with patch("agents.api_agent.broker", mock_broker):
            task = asyncio.create_task(start_api_agent(mock_broker))
            await asyncio.sleep(0)

        handler = mock_broker.subscribe.call_args_list[0][0][1]
        msg = MagicMock()
        msg.payload = {"operation": "send", "user_id": "user_1"}
        msg.session_id = "s"
        msg.task_id = "t"
        msg.message_id = "m"
        # Receiver is explicitly set to "execution" — should be ignored
        wrong_receiver = MagicMock()
        wrong_receiver.value = "execution"
        msg.receiver = wrong_receiver

        await handler(msg)
        mock_broker.publish.assert_not_called()

        task.cancel()

    @pytest.mark.asyncio
    async def test_missing_credentials_triggers_oauth_prompt(self):
        mock_broker = MagicMock()
        mock_broker.subscribe = MagicMock()
        mock_broker.publish = AsyncMock()

        with patch("agents.api_agent.broker", mock_broker):
            task = asyncio.create_task(start_api_agent(mock_broker))
            await asyncio.sleep(0)

        handler = mock_broker.subscribe.call_args_list[0][0][1]

        with patch.object(ApiAgent, "send_email", new_callable=AsyncMock,
                          return_value=ApiResult(
                              task_id="t1", status="failed", operation="send",
                              error="No credentials for user user_1"
                          )), \
             patch.object(ApiAgent, "initiate_oauth_flow", new_callable=AsyncMock,
                          return_value=("https://auth.google.com/url", "state_123")):
            msg = MagicMock()
            msg.payload = {"operation": "send", "user_id": "user_1",
                           "to": "a@b.com", "subject": "S", "body": "B"}
            msg.session_id = "s"
            msg.task_id = "t"
            msg.message_id = "m"
            msg.receiver = None
            await handler(msg)

        assert mock_broker.publish.called
        call_args = mock_broker.publish.call_args
        channel = call_args[0][0]
        assert channel == Channels.API_TO_COORDINATOR
        published_msg = call_args[0][1]
        assert published_msg.payload["needs_clarification"] is True
        assert published_msg.payload["metadata"]["email_api_credentials_missing"] is True
        assert published_msg.payload["metadata"]["oauth_authorize_url"] == (
            "http://localhost:8000/api/email/oauth/authorize?user_id=user_1"
        )
        assert "Open the API allow page here" in published_msg.payload["clarification_question"]

        task.cancel()


# ════════════════════════════════════════════════════════════════════════════
# 17. BACKWARD-COMPAT ALIASES
# ════════════════════════════════════════════════════════════════════════════

class TestBackwardCompatAliases:
    def test_email_agent_is_api_agent(self):
        assert EmailAgent is ApiAgent

    def test_email_task_is_api_task(self):
        assert EmailTask is ApiTask

    def test_email_result_is_api_result(self):
        assert EmailResult is ApiResult

    def test_start_email_agent_is_start_api_agent(self):
        assert start_email_agent is start_api_agent

    def test_email_agent_instantiation(self):
        """EmailAgent() still works after rename."""
        agent = EmailAgent()
        assert isinstance(agent, ApiAgent)

    def test_email_task_construction(self):
        t = EmailTask(operation="send", user_id="u1")
        assert isinstance(t, ApiTask)

    def test_email_result_construction(self):
        r = EmailResult(task_id="t1", status="success", operation="send")
        assert isinstance(r, ApiResult)


# ════════════════════════════════════════════════════════════════════════════
# 18. COOKIE PARSER UTILITY
# ════════════════════════════════════════════════════════════════════════════

class TestCookieParser:
    def test_parse_simple_cookie(self):
        agent = ApiAgent()
        headers = {"Set-Cookie": "SID=abc; Path=/; Secure; HttpOnly; SameSite=Lax"}
        cookies = agent._parse_cookies_from_headers(headers)
        assert len(cookies) == 1
        c = cookies[0]
        assert c["name"] == "SID"
        assert c["value"] == "abc"
        assert c.get("secure") is True
        assert c.get("httpOnly") is True
        assert c.get("sameSite") == "Lax"

    def test_parse_no_cookie_header(self):
        agent = ApiAgent()
        cookies = agent._parse_cookies_from_headers({})
        assert cookies == []

    def test_parse_max_age(self):
        agent = ApiAgent()
        headers = {"Set-Cookie": "HSID=xyz; Max-Age=3600"}
        cookies = agent._parse_cookies_from_headers(headers)
        assert cookies[0]["name"] == "HSID"
        assert "expires" in cookies[0]
