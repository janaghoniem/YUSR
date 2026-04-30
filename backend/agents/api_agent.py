#!/usr/bin/env python3
"""
api_agent.py - Google API Integration (Gmail, YouTube, Calendar, Drive)

Features:
- OAuth 2.0 authentication with token refresh
- Send emails with MIME formatting
- Read unread emails
- Extract OTP codes and magic links from emails
- YouTube search/video info
- Google Calendar create/list events
- Google Drive upload/list files
- Browser cookie injection for seamless Google web automation
- Message broker integration for async operations
- Encrypted token storage (Fernet)
"""

import os
import json
import re
import uuid
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any, Tuple
from base64 import b64decode, b64encode
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.utils import formatdate, make_msgid
from email import encoders
import base64

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from google.auth.exceptions import RefreshError
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import httplib2
from pydantic import BaseModel, Field
from cryptography.fernet import Fernet
from pymongo import MongoClient
from dotenv import load_dotenv

from agents.utils.protocol import Channels, AgentMessage, MessageType, AgentType
from agents.utils.broker import broker

load_dotenv()
logger = logging.getLogger(__name__)

# ============================================================================
# CONFIGURATION
# ============================================================================

GMAIL_CLIENT_ID = os.environ.get("GMAIL_CLIENT_ID")
GMAIL_CLIENT_SECRET = os.environ.get("GMAIL_CLIENT_SECRET")
GMAIL_REDIRECT_URI = os.environ.get("GMAIL_REDIRECT_URI", "http://localhost:8000/api/email/oauth/callback")
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.send", 
                "https://www.googleapis.com/auth/gmail.readonly",
                "https://www.googleapis.com/auth/youtube.readonly",
                "https://www.googleapis.com/auth/calendar",
                "https://www.googleapis.com/auth/drive.file",
                "openid",
                "https://www.googleapis.com/auth/userinfo.email",
                "https://www.googleapis.com/auth/userinfo.profile"
                ]
ENCRYPTION_KEY = os.environ.get("ENCRYPTION_KEY", Fernet.generate_key().decode())
EMAIL_CREDENTIAL_FALLBACK_USER_ID = os.environ.get("EMAIL_CREDENTIAL_FALLBACK_USER_ID", "").strip()

# MongoDB connection
MONGODB_URI = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
MONGO_DB = "yusr_db"
AURA_DB = "aura_db"

try:
    mongo_client = MongoClient(MONGODB_URI)
    mongo_client.admin.command('ping')
    logger.info("âœ… MongoDB connected for email agent")
except Exception as e:
    logger.error(f"âŒ MongoDB connection failed: {e}")
    mongo_client = None

# ============================================================================
# DATA MODELS
# ============================================================================

class ApiTask(BaseModel):
    """Task format for Google API operations (Gmail, YouTube, Calendar, Drive)"""
    task_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    operation: str  # "send", "read", "extract_otp", "extract_links", "youtube_search", "youtube_video_info", "calendar_create", "calendar_list", "drive_upload", "drive_list", "get_browser_cookies"
    user_id: str
    email_address: Optional[str] = None
    
    # For send operation
    to: Optional[str] = None
    subject: Optional[str] = None
    body: Optional[str] = None
    attachments: Optional[List[Dict[str, str]]] = None
    
    # For read operation
    max_results: int = 10
    query: str = "is:unread"
    
    # For extraction
    search_pattern: Optional[str] = None
    
    # For YouTube operations
    search_query: Optional[str] = None
    video_url: Optional[str] = None
    
    # For Calendar operations
    title: Optional[str] = None
    start_time: Optional[str] = None  # ISO format
    end_time: Optional[str] = None    # ISO format
    description: Optional[str] = None
    
    # For Drive operations
    file_path: Optional[str] = None
    parent_folder_id: Optional[str] = None
    
    class Config:
        use_enum_values = True


class ApiResult(BaseModel):
    """Result from Google API operation"""
    task_id: str
    status: str  # "success", "failed", "pending"
    operation: str
    result: Optional[Any] = None
    error: Optional[str] = None
    message_count: int = 0
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())


class GmailCredential(BaseModel):
    """Gmail credential storage model"""
    user_id: str
    gmail_address: str
    encrypted_refresh_token: str
    access_token_expiry: Optional[str] = None
    scope: List[str] = Field(default_factory=lambda: GMAIL_SCOPES)
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    revoked_at: Optional[str] = None


# ============================================================================
# ENCRYPTION UTILITIES
# ============================================================================

class TokenEncryptor:
    """Handles encryption/decryption of OAuth tokens"""
    
    def __init__(self, key: Optional[str] = None):
        if not key:
            key = os.environ.get("ENCRYPTION_KEY")
            if not key:
                logger.warning("âš ï¸ ENCRYPTION_KEY not set, using temporary key (not recommended for production)")
                key = Fernet.generate_key().decode()
        
        if isinstance(key, str):
            key = key.encode()
        
        self.cipher = Fernet(key)
    
    def encrypt(self, token: str) -> str:
        """Encrypt a refresh token"""
        return self.cipher.encrypt(token.encode()).decode()
    
    def decrypt(self, encrypted_token: str) -> str:
        """Decrypt a refresh token"""
        try:
            return self.cipher.decrypt(encrypted_token.encode()).decode()
        except Exception as e:
            logger.error(f"âŒ Token decryption failed: {e}")
            return None


# ============================================================================
# GLOBAL OAUTH STATE CACHE (persists across requests)
# ============================================================================

_oauth_state_cache = {}  # state -> user_id mapping for active OAuth flows


# ============================================================================
# EMAIL AGENT
# ============================================================================

class ApiAgent:
    """Google API agent â€” Gmail, YouTube, Calendar, Drive, OAuth token management"""
    
    def __init__(self):
        self.encryptor = TokenEncryptor()
        self.gmail_service = None
        self.credentials_cache = {}  # user_id -> Credentials object
        logger.info("âœ… API Agent initialized")
    
    def get_mongodb_collection(self, collection_name: str):
        """Get MongoDB collection"""
        if mongo_client is None:
            logger.error("âŒ MongoDB not available")
            return None
        db = mongo_client[MONGO_DB]
        return db[collection_name]
    
    # â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # OAuth Token Management
    # â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _create_oauth_flow(self) -> Flow:
        """Create an OAuth flow from environment variables (web application client)."""
        if not GMAIL_CLIENT_ID or not GMAIL_CLIENT_SECRET:
            raise ValueError("GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET must be set in .env")
        
        client_config = {
            "web": {
                "client_id": GMAIL_CLIENT_ID,
                "client_secret": GMAIL_CLIENT_SECRET,
                "redirect_uris": [GMAIL_REDIRECT_URI],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token"
            }
        }
        
        return Flow.from_client_config(client_config, scopes=GMAIL_SCOPES)
    
    async def initiate_oauth_flow(self, user_id: str) -> Tuple[str, str]:
        """
        Initiate OAuth flow for user (web application client)
        Returns (auth_url, state) and stores state->user_id mapping
        """
        global _oauth_state_cache
        
        try:
            flow = self._create_oauth_flow()
            flow.redirect_uri = GMAIL_REDIRECT_URI
            
            logger.info(f"ðŸ” OAuth Flow Config:")
            logger.info(f"   Client ID: {GMAIL_CLIENT_ID[:20]}...")
            logger.info(f"   Redirect URI: {GMAIL_REDIRECT_URI}")
            logger.info(f"   Scopes: {GMAIL_SCOPES}")
            
            # Generate authorization URL
            auth_url, state = flow.authorization_url(access_type='offline', prompt='consent')
            
            # Store mapping of state -> user_id in global cache for retrieval on callback
            _oauth_state_cache[state] = user_id
            logger.info(f"âœ… Stored state->{user_id} mapping in cache")
            
            logger.info(f"âœ… Authorization URL generated (length: {len(auth_url)})")
            logger.info(f"ðŸ” OAuth flow initiated for user {user_id}")
            return auth_url, state
        
        except Exception as e:
            logger.error(f"âŒ Failed to initiate OAuth flow: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None, None
    
    async def handle_oauth_callback(self, user_id: str, auth_code: str) -> bool:
        """
        Handle OAuth callback with authorization code (web application client)
        """
        try:
            flow = self._create_oauth_flow()
            flow.redirect_uri = GMAIL_REDIRECT_URI
            
            flow.fetch_token(code=auth_code)
            creds = flow.credentials
            
            if not creds:
                raise RuntimeError("OAuth callback returned no credentials")
            
            # Store credentials in MongoDB
            await self._store_credentials_mongodb(user_id, creds)
            
            # Cache credentials
            self.credentials_cache[user_id] = creds
            
            logger.info(f"âœ… OAuth callback processed for user {user_id}")
            return True
        
        except Exception as e:
            logger.error(f"âŒ OAuth callback failed: {e}")
            return False
    
    async def _store_credentials_mongodb(self, user_id: str, creds: Credentials):
        """Store OAuth credentials in MongoDB with encryption"""
        collection = self.get_mongodb_collection("user_credentials_email")
        if collection is None:
            logger.error("âŒ Cannot store credentials: MongoDB unavailable")
            return
        
        try:
            # Serialize credentials
            creds_dict = {
                'token': creds.token,
                'refresh_token': creds.refresh_token,
                'token_uri': creds.token_uri,
                'client_id': creds.client_id,
                'client_secret': creds.client_secret,
                'scopes': creds.scopes
            }
            
            # Encrypt refresh token
            encrypted_refresh = self.encryptor.encrypt(creds.refresh_token) if creds.refresh_token else None
            
            # Get email address from token
            gmail_service = build('gmail', 'v1', credentials=creds)
            profile = gmail_service.users().getProfile(userId='me').execute()
            email_address = profile.get('emailAddress', user_id)
            
            # Prepare document
            doc = {
                'user_id': user_id,
                'gmail_address': email_address,
                'encrypted_refresh_token': encrypted_refresh,
                'access_token': creds.token,
                'access_token_expiry': creds.expiry.isoformat() if creds.expiry else None,
                'scope': GMAIL_SCOPES,
                'updated_at': datetime.now().isoformat()
            }
            
            # Upsert
            collection.update_one(
                {'user_id': user_id},
                {'$set': doc},
                upsert=True
            )
            
            logger.info(f"âœ… Credentials stored for {user_id} ({email_address})")
        
        except Exception as e:
            logger.error(f"âŒ Failed to store credentials: {e}")
    
    async def _get_credentials_mongodb(self, user_id: str) -> Optional[Credentials]:
        """Retrieve and refresh credentials from MongoDB"""
        # Check cache first
        if user_id in self.credentials_cache:
            creds = self.credentials_cache[user_id]
            if creds.valid:
                return creds
        
        collection = self.get_mongodb_collection("user_credentials_email")
        if collection is None:
            logger.error("âŒ Cannot retrieve credentials: MongoDB unavailable")
            return None
        
        try:
            doc = collection.find_one({'user_id': user_id})
            if not doc:
                logger.warning(f"âš ï¸ No credentials found for user {user_id}")
                return None
            
            # Decrypt refresh token
            encrypted_token = doc.get('encrypted_refresh_token')
            if not encrypted_token:
                logger.error(f"âŒ No refresh token for user {user_id}")
                return None
            
            refresh_token = self.encryptor.decrypt(encrypted_token)
            if not refresh_token:
                logger.error(f"âŒ Failed to decrypt refresh token for user {user_id}")
                return None
            
            # Reconstruct credentials
            creds_dict = {
                'token': doc.get('access_token'),
                'refresh_token': refresh_token,
                'token_uri': 'https://oauth2.googleapis.com/token',
                'client_id': GMAIL_CLIENT_ID,
                'client_secret': GMAIL_CLIENT_SECRET,
                'scopes': doc.get('scope', GMAIL_SCOPES)
            }
            
            creds = Credentials.from_authorized_user_info(creds_dict, GMAIL_SCOPES)
            
            # Refresh if expired
            if not creds.valid and creds.refresh_token:
                try:
                    creds.refresh(Request())
                    # Update MongoDB with new access token
                    collection.update_one(
                        {'user_id': user_id},
                        {'$set': {
                            'access_token': creds.token,
                            'access_token_expiry': creds.expiry.isoformat() if creds.expiry else None,
                            'updated_at': datetime.now().isoformat()
                        }}
                    )
                    logger.info(f"âœ… Access token refreshed for {user_id}")
                except RefreshError as e:
                    logger.error(f"âŒ Token refresh failed: {e}")
                    return None
            
            # Cache
            self.credentials_cache[user_id] = creds
            return creds
        
        except Exception as e:
            logger.error(f"âŒ Failed to retrieve credentials: {e}")
            return None
    
    async def revoke_credentials(self, user_id: str):
        """Revoke stored credentials"""
        collection = self.get_mongodb_collection("user_credentials_email")
        if collection is None:
            return
        
        try:
            collection.update_one(
                {'user_id': user_id},
                {'$set': {'revoked_at': datetime.now().isoformat()}}
            )
            
            # Remove from cache
            self.credentials_cache.pop(user_id, None)
            
            logger.info(f"âœ… Credentials revoked for {user_id}")
        except Exception as e:
            logger.error(f"âŒ Failed to revoke credentials: {e}")

    async def _get_credentials_with_fallback(self, user_id: str) -> Tuple[Optional[Credentials], str]:
        """
        Resolve credentials for requested user, using DB identity mapping first,
        then optional env fallback owner.
        Returns (credentials_or_none, effective_user_id).
        """
        creds = await self._get_credentials_mongodb(user_id)
        if creds:
            return creds, user_id

        mapped_user = self._resolve_credential_owner_from_db(user_id)
        if mapped_user and mapped_user != user_id:
            logger.warning(
                f"âš ï¸ No credentials for user {user_id}; mapped to credential owner {mapped_user} from DB"
            )
            mapped_creds = await self._get_credentials_mongodb(mapped_user)
            if mapped_creds:
                return mapped_creds, mapped_user

        fallback_user = EMAIL_CREDENTIAL_FALLBACK_USER_ID
        if fallback_user and fallback_user != user_id:
            logger.warning(
                f"âš ï¸ No credentials for user {user_id}; trying fallback credential owner {fallback_user}"
            )
            fallback_creds = await self._get_credentials_mongodb(fallback_user)
            if fallback_creds:
                return fallback_creds, fallback_user

        return None, user_id

    def _resolve_credential_owner_from_db(self, user_id: str) -> Optional[str]:
        """
        Map runtime user_id to credential owner from DB.
        Priority:
        1) direct user_id
        2) aura_db.users.username for this user_id
        3) aura_db.users.email for this user_id
        4) local-part of that email
        """
        collection = self.get_mongodb_collection("user_credentials_email")
        if collection is None:
            return None

        try:
            # Already checked caller user_id in _get_credentials_with_fallback,
            # but keeping this here makes the resolver safe as a standalone utility.
            if collection.find_one({'user_id': user_id}, {'_id': 0, 'user_id': 1}):
                return user_id

            if mongo_client is None:
                return None

            users_col = mongo_client[AURA_DB]["users"]
            user_doc = users_col.find_one({'user_id': user_id}, {'_id': 0, 'username': 1, 'email': 1})

            candidates: List[str] = []
            if user_doc:
                username = str(user_doc.get('username') or '').strip()
                email = str(user_doc.get('email') or '').strip().lower()
                email_local = email.split('@', 1)[0] if '@' in email else ''

                if username:
                    candidates.append(username)
                if email:
                    candidates.append(email)
                if email_local:
                    candidates.append(email_local)

                for candidate in candidates:
                    if collection.find_one({'user_id': candidate}, {'_id': 0, 'user_id': 1}):
                        return candidate

                # Search credentials by gmail_address containing the username
                # e.g., user_id "user_173..." â†’ aura_db.users has email "hala@..." â†’ search
                # credentials where gmail_address starts with that local-part
                for candidate in candidates:
                    gmail_match = collection.find_one(
                        {'gmail_address': {'$regex': f'^{re.escape(candidate)}@', '$options': 'i'}},
                        {'_id': 0, 'user_id': 1}
                    )
                    if gmail_match:
                        return gmail_match['user_id']

            # Absolute last resort: if only one set of credentials exists, use it
            # This covers cases where aura_db.users has no record for this user_id
            total_creds = collection.count_documents({})
            if total_creds == 1:
                single_doc = collection.find_one({}, {'_id': 0, 'user_id': 1})
                if single_doc:
                    logger.info(f"ðŸ”‘ Single credential fallback: using {single_doc['user_id']}")
                    return single_doc['user_id']

            return None
        except Exception as e:
            logger.warning(f"âš ï¸ Failed resolving credential owner for {user_id}: {e}")
            return None
    
    # â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # Email Operations
    # â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    
    async def send_email(self, user_id: str, to: str, subject: str, body: str, 
                         attachments: Optional[List[Dict]] = None) -> ApiResult:
        """Send email via Gmail API"""
        task_id = str(uuid.uuid4())
        
        try:
            creds, credential_user_id = await self._get_credentials_with_fallback(user_id)
            if not creds:
                return ApiResult(
                    task_id=task_id,
                    status="failed",
                    operation="send",
                    error=f"No credentials for user {user_id}"
                )
            
            service = build('gmail', 'v1', credentials=creds)

            sender_email = None
            try:
                profile = service.users().getProfile(userId='me').execute()
                sender_email = profile.get('emailAddress')
            except Exception:
                # Non-fatal: Gmail API will still send as authenticated account.
                sender_email = None
            
            # Create a standards-friendly message structure to improve deliverability.
            message = MIMEMultipart('mixed')
            message['to'] = to
            message['subject'] = subject
            message['date'] = formatdate(localtime=True)
            message['message-id'] = make_msgid()
            if sender_email:
                message['from'] = sender_email

            html_body = body or ""
            text_body = re.sub(r'<[^>]+>', ' ', html_body)
            text_body = re.sub(r'\s+', ' ', text_body).strip()
            if not text_body:
                text_body = html_body

            alternative = MIMEMultipart('alternative')
            alternative.attach(MIMEText(text_body, 'plain', 'utf-8'))
            alternative.attach(MIMEText(html_body, 'html', 'utf-8'))
            message.attach(alternative)
            
            # Add attachments if provided
            if attachments:
                for att in attachments:
                    try:
                        part = MIMEBase('application', 'octet-stream')
                        with open(att['path'], 'rb') as fh:
                            part.set_payload(fh.read())
                        encoders.encode_base64(part)
                        part.add_header('Content-Disposition', f'attachment; filename= {att.get("name", att["path"])}')
                        message.attach(part)
                    except Exception as e:
                        logger.warning(f"âš ï¸ Failed to attach {att.get('name')}: {e}")
            
            # Send
            raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
            send_message = {'raw': raw_message}
            
            service.users().messages().send(userId='me', body=send_message).execute()
            
            logger.info(
                f"âœ… Email sent to {to} for user {user_id} using credentials from {credential_user_id}"
            )
            
            return ApiResult(
                task_id=task_id,
                status="success",
                operation="send",
                result={
                    "to": to,
                    "subject": subject,
                    "from": sender_email,
                    "credential_user_id": credential_user_id,
                }
            )
        
        except Exception as e:
            logger.error(f"âŒ Failed to send email: {e}")
            return ApiResult(
                task_id=task_id,
                status="failed",
                operation="send",
                error=str(e)
            )
    
    async def read_unread_emails(self, user_id: str, max_results: int = 10, 
                                  query: str = "is:unread") -> ApiResult:
        """Read unread emails from Gmail"""
        task_id = str(uuid.uuid4())
        
        try:
            creds, credential_user_id = await self._get_credentials_with_fallback(user_id)
            if not creds:
                return ApiResult(
                    task_id=task_id,
                    status="failed",
                    operation="read",
                    error=f"No credentials for user {user_id}"
                )
            
            service = build('gmail', 'v1', credentials=creds)
            
            # List messages
            results = service.users().messages().list(
                userId='me',
                q=query,
                maxResults=max_results
            ).execute()
            
            messages = results.get('messages', [])
            email_data = []
            
            for msg in messages:
                try:
                    message = service.users().messages().get(
                        userId='me',
                        id=msg['id'],
                        format='full'
                    ).execute()
                    
                    headers = message['payload'].get('headers', [])
                    from_addr = next((h['value'] for h in headers if h['name'] == 'From'), 'Unknown')
                    subject = next((h['value'] for h in headers if h['name'] == 'Subject'), '(No Subject)')
                    snippet = message.get('snippet', '')
                    
                    email_data.append({
                        'message_id': msg['id'],
                        'from': from_addr,
                        'subject': subject,
                        'snippet': snippet,
                        'timestamp': message.get('internalDate')
                    })
                    
                    # Cache email
                    await self._cache_email(user_id, msg['id'], from_addr, subject, snippet)
                
                except Exception as e:
                    logger.warning(f"âš ï¸ Failed to process message {msg['id']}: {e}")
            
            logger.info(
                f"âœ… Retrieved {len(email_data)} unread emails for user {user_id} using credentials from {credential_user_id}"
            )
            
            return ApiResult(
                task_id=task_id,
                status="success",
                operation="read",
                result=email_data,
                message_count=len(email_data)
            )
        
        except Exception as e:
            logger.error(f"âŒ Failed to read emails: {e}")
            return ApiResult(
                task_id=task_id,
                status="failed",
                operation="read",
                error=str(e)
            )
    
    async def extract_otp_codes(self, user_id: str, max_results: int = 5) -> ApiResult:
        """Extract OTP codes from recent emails"""
        task_id = str(uuid.uuid4())
        
        try:
            result = await self.read_unread_emails(user_id, max_results=max_results)
            
            if result.status != "success":
                return result
            
            emails = result.result or []
            otp_codes = []
            
            # OTP patterns: 4-8 digit codes, with optional dashes/spaces
            otp_patterns = [
                r'\b(\d{6})\b',  # 6-digit code
                r'\b(\d{4})\s*-\s*(\d{4})\b',  # XXXX-XXXX
                r'\bOTP[\s:]+(\d{6})\b',  # OTP: XXXXXX
                r'\bcode[\s:]+(\d{6})\b',  # code: XXXXXX
                r'\bverification[\s:]+(\d{6})\b',  # verification: XXXXXX
            ]
            
            for email in emails:
                snippet = email.get('snippet', '').lower()
                for pattern in otp_patterns:
                    matches = re.findall(pattern, snippet, re.IGNORECASE)
                    for match in matches:
                        if isinstance(match, tuple):
                            code = ''.join(match)
                        else:
                            code = match
                        
                        if len(code) >= 4 and code not in otp_codes:
                            otp_codes.append({
                                'code': code,
                                'from': email['from'],
                                'subject': email['subject'],
                                'timestamp': email.get('timestamp')
                            })
            
            logger.info(f"âœ… Extracted {len(otp_codes)} OTP codes for user {user_id}")
            
            return ApiResult(
                task_id=task_id,
                status="success",
                operation="extract_otp",
                result=otp_codes,
                message_count=len(otp_codes)
            )
        
        except Exception as e:
            logger.error(f"âŒ Failed to extract OTP codes: {e}")
            return ApiResult(
                task_id=task_id,
                status="failed",
                operation="extract_otp",
                error=str(e)
            )
    
    async def extract_magic_links(self, user_id: str, max_results: int = 5) -> ApiResult:
        """Extract magic links from recent emails"""
        task_id = str(uuid.uuid4())
        
        try:
            result = await self.read_unread_emails(user_id, max_results=max_results)
            
            if result.status != "success":
                return result
            
            emails = result.result or []
            magic_links = []
            
            # Link patterns
            link_patterns = [
                r'(https?://[^\s<>"{}|\\^`\[\]]*)',  # URLs
                r'(www\.[^\s<>"{}|\\^`\[\]]*)',  # www. URLs
            ]
            
            for email in emails:
                snippet = email.get('snippet', '')
                for pattern in link_patterns:
                    matches = re.findall(pattern, snippet)
                    for link in matches:
                        if any(kw in link.lower() for kw in ['confirm', 'verify', 'token', 'reset', 'login']):
                            magic_links.append({
                                'link': link,
                                'from': email['from'],
                                'subject': email['subject'],
                                'timestamp': email.get('timestamp')
                            })
            
            logger.info(f"âœ… Extracted {len(magic_links)} magic links for user {user_id}")
            
            return ApiResult(
                task_id=task_id,
                status="success",
                operation="extract_links",
                result=magic_links,
                message_count=len(magic_links)
            )
        
        except Exception as e:
            logger.error(f"âŒ Failed to extract magic links: {e}")
            return ApiResult(
                task_id=task_id,
                status="failed",
                operation="extract_links",
                error=str(e)
            )
    
    async def _cache_email(self, user_id: str, message_id: str, from_addr: str, 
                          subject: str, snippet: str):
        """Cache email metadata in MongoDB"""
        collection = self.get_mongodb_collection("email_cache")
        if collection is None:
            return
        
        try:
            collection.update_one(
                {'user_id': user_id, 'message_id': message_id},
                {'$set': {
                    'email_from': from_addr,
                    'subject': subject,
                    'snippet': snippet,
                    'timestamp': datetime.now().isoformat()
                }},
                upsert=True
            )
            # Set TTL to 1 hour
            collection.create_index('timestamp', expireAfterSeconds=3600)
        except Exception as e:
            logger.warning(f"âš ï¸ Failed to cache email: {e}")
    
    # â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # Google APIs: Browser Cookie Bridge
    # â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    
    async def get_browser_cookies(self, user_id: str) -> Dict[str, Any]:
        """
        Get Google session cookies for browser injection via Playwright
        Uses OAuth refresh token to exchange for session cookies
        """
        task_id = str(uuid.uuid4())
        
        try:
            # Get credentials with full fallback resolution (direct â†’ DB mapping â†’ env fallback)
            creds, effective_user_id = await self._get_credentials_with_fallback(user_id)
            if not creds:
                return {
                    'status': 'failed',
                    'error': f'No credentials for user {user_id}',
                    'cookies': []
                }
            if effective_user_id != user_id:
                logger.info(f"ðŸ”‘ Browser cookies: resolved {user_id} â†’ credential owner {effective_user_id}")
            
            # Refresh access token if needed
            if not creds.valid and creds.refresh_token:
                creds.refresh(Request())
                logger.info(f"âœ… Access token refreshed for cookie bridge")
            
            access_token = creds.token
            if not access_token:
                return {
                    'status': 'failed',
                    'error': 'No access token available',
                    'cookies': []
                }
            
            # Exchange access token for session cookies via OAuthLogin endpoint
            cookies_list = []
            try:
                import requests
                import time as _time
                
                session = requests.Session()
                session.headers.update({
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36'
                })
                
                # Step 1: OAuthLogin â€” exchange access token for uberauth token
                oauth_login_url = "https://accounts.google.com/OAuthLogin?source=ChromiumBrowser&issueuberauth=1"
                response1 = session.get(
                    oauth_login_url,
                    headers={'Authorization': f'Bearer {access_token}'},
                    allow_redirects=False
                )
                logger.info(f"ðŸ”‘ OAuthLogin response: status={response1.status_code}, body_len={len(response1.text)}, cookies={list(session.cookies.keys())}")
                
                uberauth_token = response1.text.strip()
                
                # Step 2: MergeSession â€” convert uberauth to real session cookies (SID, HSID, etc.)
                if uberauth_token and len(uberauth_token) < 500:
                    merge_url = f"https://accounts.google.com/MergeSession?uberauth={uberauth_token}&continue=https://www.google.com/"
                    response2 = session.get(merge_url, allow_redirects=True)
                    logger.info(f"ðŸ”‘ MergeSession response: status={response2.status_code}, cookies={list(session.cookies.keys())}")
                else:
                    logger.warning(f"âš ï¸ OAuthLogin did not return a valid uberauth token (len={len(uberauth_token)})")
                
                # Step 3: Extract ALL session cookies from the requests session jar
                # These are the real Google session cookies (SID, HSID, SSID, etc.)
                important_cookies = {'SID', 'HSID', 'SSID', 'APISID', 'SAPISID', 'LSID', 'NID', '__Secure-1PSID', '__Secure-3PSID', '__Secure-1PAPISID', '__Secure-3PAPISID'}
                for cookie in session.cookies:
                    cookie_dict = {
                        'name': cookie.name,
                        'value': cookie.value,
                        'domain': cookie.domain or '.google.com',
                        'path': cookie.path or '/',
                    }
                    if cookie.expires:
                        cookie_dict['expires'] = float(cookie.expires)
                    else:
                        cookie_dict['expires'] = float(_time.time() + 86400)
                    if cookie.secure:
                        cookie_dict['secure'] = True
                    # Mark important auth cookies
                    if cookie.name in important_cookies:
                        logger.info(f"  ðŸª Got auth cookie: {cookie.name} (domain={cookie.domain})")
                    cookies_list.append(cookie_dict)
                
                # Also parse raw Set-Cookie headers for any we missed
                for resp in [response1] + (list(getattr(response2, 'history', [])) if 'response2' in dir() else []):
                    raw_sc = resp.headers.get('Set-Cookie', '')
                    if raw_sc:
                        parsed = self._parse_cookies_from_headers(resp.headers)
                        for pc in parsed:
                            if not any(c['name'] == pc['name'] and c['domain'] == pc['domain'] for c in cookies_list):
                                cookies_list.append(pc)
                
                found_session = any(c['name'] in important_cookies for c in cookies_list)
                logger.info(f"âœ… Retrieved {len(cookies_list)} Google cookies for user {user_id} (has_session_cookies={found_session})")
                if not found_session:
                    logger.warning(f"âš ï¸ No critical session cookies (SID/HSID/SSID) found â€” Google auth injection will likely not work")
                
                return {
                    'status': 'success',
                    'cookies': cookies_list,
                    'user_id': user_id,
                    'cookie_count': len(cookies_list),
                    'has_session_cookies': found_session
                }
            
            except Exception as e:
                logger.error(f"âŒ Failed to exchange token for cookies: {e}")
                return {
                    'status': 'failed',
                    'error': str(e),
                    'cookies': []
                }
        
        except Exception as e:
            logger.error(f"âŒ Failed to get browser cookies: {e}")
            return {
                'status': 'failed',
                'error': str(e),
                'cookies': []
            }
    
    def _parse_cookies_from_headers(self, headers: Dict) -> List[Dict]:
        """Parse cookie headers into Playwright-compatible format"""
        cookies = []
        cookie_header = headers.get('Set-Cookie', '')
        
        if isinstance(cookie_header, list):
            cookie_headers = cookie_header
        else:
            cookie_headers = [cookie_header] if cookie_header else []
        
        for cookie_str in cookie_headers:
            parts = cookie_str.split(';')
            if not parts:
                continue
            
            name_value = parts[0].strip()
            if '=' not in name_value:
                continue
            
            name, value = name_value.split('=', 1)
            cookie_dict = {
                'name': name.strip(),
                'value': value.strip(),
                'domain': '.google.com',
                'path': '/'
            }
            
            # Parse additional attributes
            for attr in parts[1:]:
                attr = attr.strip()
                if attr.lower().startswith('expires='):
                    try:
                        from email.utils import parsedate_to_datetime
                        dt = parsedate_to_datetime(attr.split('=', 1)[1].strip())
                        cookie_dict['expires'] = float(dt.timestamp())
                    except Exception:
                        # Fallback: expire in 1 hour
                        import time
                        cookie_dict['expires'] = float(time.time() + 3600)
                elif attr.lower().startswith('max-age='):
                    try:
                        import time
                        cookie_dict['expires'] = float(time.time() + int(attr.split('=', 1)[1]))
                    except Exception:
                        pass
                elif attr.lower() == 'secure':
                    cookie_dict['secure'] = True
                elif attr.lower() == 'httponly':
                    cookie_dict['httpOnly'] = True
                elif attr.lower().startswith('samesite='):
                    raw_ss = attr.split('=', 1)[1].strip().capitalize()
                    if raw_ss in ('Strict', 'Lax', 'None'):
                        cookie_dict['sameSite'] = raw_ss
                    else:
                        cookie_dict['sameSite'] = 'Lax'
            
            cookies.append(cookie_dict)
        
        return cookies
    
    # â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # Google APIs: YouTube
    # â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    
    async def youtube_search(self, user_id: str, query: str, max_results: int = 10) -> ApiResult:
        """Search YouTube videos"""
        try:
            creds = await self._get_credentials_mongodb(user_id)
            if not creds:
                return ApiResult(
                    task_id=str(uuid.uuid4()),
                    status="failed",
                    operation="youtube_search",
                    error=f"No credentials for user {user_id}"
                )
            
            # Build YouTube service
            youtube = build('youtube', 'v3', credentials=creds)
            
            # Search for videos
            request = youtube.search().list(
                q=query,
                part='snippet',
                type='video',
                maxResults=min(max_results, 50),
                order='relevance',
                fields='items(id,snippet(title,description,thumbnails,channelTitle,publishedAt))'
            )
            
            response = request.execute()
            
            results = []
            for item in response.get('items', []):
                video_id = item['id'].get('videoId', '')
                snippet = item['snippet']
                results.append({
                    'video_id': video_id,
                    'title': snippet['title'],
                    'description': snippet['description'],
                    'channel': snippet['channelTitle'],
                    'published_at': snippet['publishedAt'],
                    'thumbnail': snippet['thumbnails'].get('default', {}).get('url', ''),
                    'url': f'https://www.youtube.com/watch?v={video_id}'
                })
            
            logger.info(f"âœ… YouTube search returned {len(results)} results for '{query}'")
            
            return ApiResult(
                task_id=str(uuid.uuid4()),
                status="success",
                operation="youtube_search",
                result={"videos": results}
            )
        
        except HttpError as e:
            logger.error(f"âŒ YouTube API error: {e}")
            return ApiResult(
                task_id=str(uuid.uuid4()),
                status="failed",
                operation="youtube_search",
                error=f"YouTube API error: {str(e)}"
            )
        except Exception as e:
            logger.error(f"âŒ YouTube search failed: {e}")
            return ApiResult(
                task_id=str(uuid.uuid4()),
                status="failed",
                operation="youtube_search",
                error=str(e)
            )
    
    async def youtube_video_info(self, user_id: str, video_url: str) -> ApiResult:
        """Get detailed info about a YouTube video"""
        try:
            # Extract video ID from URL
            import re
            video_id_match = re.search(r'(?:youtube\.com\/watch\?v=|youtu\.be\/)([^&\n?#]+)', video_url)
            if not video_id_match:
                return ApiResult(
                    task_id=str(uuid.uuid4()),
                    status="failed",
                    operation="youtube_video_info",
                    error="Invalid YouTube URL"
                )
            
            video_id = video_id_match.group(1)
            
            creds = await self._get_credentials_mongodb(user_id)
            if not creds:
                return ApiResult(
                    task_id=str(uuid.uuid4()),
                    status="failed",
                    operation="youtube_video_info",
                    error=f"No credentials for user {user_id}"
                )
            
            youtube = build('youtube', 'v3', credentials=creds)
            
            request = youtube.videos().list(
                id=video_id,
                part='snippet,statistics,contentDetails',
                fields='items(id,snippet,statistics,contentDetails)'
            )
            
            response = request.execute()
            
            if not response.get('items'):
                return ApiResult(
                    task_id=str(uuid.uuid4()),
                    status="failed",
                    operation="youtube_video_info",
                    error="Video not found"
                )
            
            item = response['items'][0]
            snippet = item['snippet']
            stats = item['statistics']
            content = item['contentDetails']
            
            result = {
                'video_id': video_id,
                'title': snippet['title'],
                'description': snippet['description'],
                'channel': snippet['channelTitle'],
                'published_at': snippet['publishedAt'],
                'views': int(stats.get('viewCount', 0)),
                'likes': int(stats.get('likeCount', 0)),
                'comments': int(stats.get('commentCount', 0)),
                'duration': content.get('duration', ''),
                'url': f'https://www.youtube.com/watch?v={video_id}'
            }
            
            logger.info(f"âœ… Retrieved info for video: {result['title']}")
            
            return ApiResult(
                task_id=str(uuid.uuid4()),
                status="success",
                operation="youtube_video_info",
                result={"info": result}
            )
        
        except Exception as e:
            logger.error(f"âŒ YouTube video info failed: {e}")
            return ApiResult(
                task_id=str(uuid.uuid4()),
                status="failed",
                operation="youtube_video_info",
                error=str(e)
            )
    
    # â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # Google APIs: Calendar
    # â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    
    async def calendar_create(self, user_id: str, title: str, start_time: str, 
                             end_time: str, description: str = "",
                             all_day: bool = False) -> ApiResult:
        """Create a Google Calendar event"""
        try:
            creds = await self._get_credentials_mongodb(user_id)
            if not creds:
                return ApiResult(
                    task_id=str(uuid.uuid4()),
                    status="failed",
                    operation="calendar_create",
                    error=f"No credentials for user {user_id}"
                )
            
            calendar = build('calendar', 'v3', credentials=creds)
            
            # Get timezone from environment, default to UTC
            timezone = os.getenv("CALENDAR_TIMEZONE", "UTC")
            
            # All-day events use 'date' (YYYY-MM-DD), timed events use 'dateTime'
            if all_day or (len(start_time) == 10 and 'T' not in start_time):
                event = {
                    'summary': title,
                    'description': description,
                    'start': {'date': start_time[:10]},
                    'end': {'date': end_time[:10]},
                }
            else:
                event = {
                    'summary': title,
                    'description': description,
                    'start': {
                        'dateTime': start_time,
                        'timeZone': timezone
                    },
                    'end': {
                        'dateTime': end_time,
                        'timeZone': timezone
                    },
                }
            
            result = calendar.events().insert(calendarId='primary', body=event).execute()
            
            logger.info(f"âœ… Calendar event created: {result['id']}")
            
            return ApiResult(
                task_id=str(uuid.uuid4()),
                status="success",
                operation="calendar_create",
                result={
                    "event": {
                        'id': result['id'],
                        'summary': result.get('summary', ''),
                        'start': result.get('start', {}),
                        'end': result.get('end', {}),
                        'htmlLink': result.get('htmlLink', '')
                    }
                }
            )
        
        except HttpError as e:
            logger.error(f"âŒ Calendar API error: {e}")
            return ApiResult(
                task_id=str(uuid.uuid4()),
                status="failed",
                operation="calendar_create",
                error=f"Calendar API error: {str(e)}"
            )
        except Exception as e:
            logger.error(f"âŒ Calendar event creation failed: {e}")
            return ApiResult(
                task_id=str(uuid.uuid4()),
                status="failed",
                operation="calendar_create",
                error=str(e)
            )
    
    async def calendar_list(self, user_id: str, max_results: int = 10) -> ApiResult:
        """List upcoming Google Calendar events"""
        try:
            creds = await self._get_credentials_mongodb(user_id)
            if not creds:
                return ApiResult(
                    task_id=str(uuid.uuid4()),
                    status="failed",
                    operation="calendar_list",
                    error=f"No credentials for user {user_id}"
                )
            
            calendar = build('calendar', 'v3', credentials=creds)
            
            now = datetime.now().isoformat() + 'Z'
            
            request = calendar.events().list(
                calendarId='primary',
                timeMin=now,
                maxResults=max_results,
                singleEvents=True,
                orderBy='startTime',
                fields='items(id,summary,start,end,description)'
            )
            
            response = request.execute()
            
            events = []
            for event in response.get('items', []):
                start = event['start'].get('dateTime', event['start'].get('date', ''))
                end = event['end'].get('dateTime', event['end'].get('date', ''))
                events.append({
                    'id': event['id'],
                    'summary': event['summary'],
                    'start': start,
                    'end': end,
                    'description': event.get('description', '')
                })
            
            logger.info(f"âœ… Retrieved {len(events)} calendar events for user {user_id}")
            
            return ApiResult(
                task_id=str(uuid.uuid4()),
                status="success",
                operation="calendar_list",
                result={"events": events}
            )
        
        except Exception as e:
            logger.error(f"âŒ Calendar list failed: {e}")
            return ApiResult(
                task_id=str(uuid.uuid4()),
                status="failed",
                operation="calendar_list",
                error=str(e)
            )
    
    # â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # Google APIs: Drive
    # â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    
    async def drive_upload(self, user_id: str, file_path: str, parent_folder_id: str = None) -> ApiResult:
        """Upload a file to Google Drive"""
        
        try:
            creds = await self._get_credentials_mongodb(user_id)
            if not creds:
                return ApiResult(
                    task_id=str(uuid.uuid4()),
                    status="failed",
                    operation="drive_upload",
                    error=f"No credentials for user {user_id}"
                )
            
            if not os.path.isfile(file_path):
                return ApiResult(
                    task_id=str(uuid.uuid4()),
                    status="failed",
                    operation="drive_upload",
                    error=f"File not found: {file_path}"
                )
            
            drive = build('drive', 'v3', credentials=creds)
            
            file_metadata = {'name': os.path.basename(file_path)}
            if parent_folder_id:
                file_metadata['parents'] = [parent_folder_id]
            
            media = None
            from googleapiclient.http import MediaFileUpload
            media = MediaFileUpload(file_path, resumable=True)
            
            file = drive.files().create(body=file_metadata, media_body=media, fields='id,webViewLink').execute()
            
            logger.info(f"âœ… File uploaded to Drive: {file['id']}")
            
            return ApiResult(
                task_id=str(uuid.uuid4()),
                status="success",
                operation="drive_upload",
                result={"file": {'file_id': file['id'], 'file_link': file.get('webViewLink', '')}}
            )
        
        except Exception as e:
            logger.error(f"âŒ Drive upload failed: {e}")
            return ApiResult(
                task_id=str(uuid.uuid4()),
                status="failed",
                operation="drive_upload",
                error=str(e)
            )
    
    async def drive_list(self, user_id: str, max_results: int = 10) -> ApiResult:
        """List files in Google Drive"""
        
        try:
            creds = await self._get_credentials_mongodb(user_id)
            if not creds:
                return ApiResult(
                    task_id=str(uuid.uuid4()),
                    status="failed",
                    operation="drive_list",
                    error=f"No credentials for user {user_id}"
                )
            
            drive = build('drive', 'v3', credentials=creds)
            
            results = drive.files().list(
                pageSize=max_results,
                fields='files(id,name,mimeType,modifiedTime,webViewLink)',
                orderBy='modifiedTime desc'
            ).execute()
            
            files = []
            for file in results.get('files', []):
                files.append({
                    'id': file['id'],
                    'name': file['name'],
                    'type': file['mimeType'],
                    'modified': file['modifiedTime'],
                    'link': file.get('webViewLink', '')
                })
            
            logger.info(f"âœ… Retrieved {len(files)} files from Drive for user {user_id}")
            
            return ApiResult(
                task_id=str(uuid.uuid4()),
                status="success",
                operation="drive_list",
                result={"files": files}
            )
        
        except Exception as e:
            logger.error(f"âŒ Drive list failed: {e}")
            return ApiResult(
                task_id=str(uuid.uuid4()),
                status="failed",
                operation="drive_list",
                error=str(e)
            )

    async def drive_search(self, user_id: str, query: str, max_results: int = 50) -> ApiResult:
        """Search files in Google Drive using Drive query syntax (q)."""

        try:
            creds = await self._get_credentials_mongodb(user_id)
            if not creds:
                return ApiResult(
                    task_id=str(uuid.uuid4()),
                    status="failed",
                    operation="drive_search",
                    error=f"No credentials for user {user_id}"
                )

            drive = build('drive', 'v3', credentials=creds)

            effective_query = (query or "").strip() or "trashed = false"
            if "trashed" not in effective_query.lower():
                effective_query = f"({effective_query}) and trashed = false"

            results = drive.files().list(
                q=effective_query,
                pageSize=max_results,
                fields='files(id,name,mimeType,modifiedTime,webViewLink)',
                orderBy='name_natural'
            ).execute()

            files = []
            for file in results.get('files', []):
                files.append({
                    'id': file.get('id', ''),
                    'name': file.get('name', ''),
                    'type': file.get('mimeType', ''),
                    'modified': file.get('modifiedTime', ''),
                    'link': file.get('webViewLink', ''),
                })

            logger.info(f"âœ… Drive search returned {len(files)} files for user {user_id}")

            return ApiResult(
                task_id=str(uuid.uuid4()),
                status="success",
                operation="drive_search",
                result={"files": files, "query": effective_query},
                message_count=len(files)
            )

        except HttpError as e:
            logger.error(f"âŒ Drive API search error: {e}")
            return ApiResult(
                task_id=str(uuid.uuid4()),
                status="failed",
                operation="drive_search",
                error=f"Drive API error: {str(e)}"
            )
        except Exception as e:
            logger.error(f"âŒ Drive search failed: {e}")
            return ApiResult(
                task_id=str(uuid.uuid4()),
                status="failed",
                operation="drive_search",
                error=str(e)
            )



async def start_api_agent(broker_instance=None):
    """Start API agent and subscribe to broker channels"""
    
    if broker_instance is None:
        broker_instance = broker
    
    print("=" * 70)
    print("ï¿½ API AGENT - READY (Google APIs: Gmail, YouTube, Calendar, Drive)!")
    print("=" * 70)
    print("Waiting for email tasks...\n")
    
    agent = ApiAgent()
    
    async def handle_email_task(message: dict):
        """Handle email task from coordinator"""
        try:
            payload = message.payload if hasattr(message, 'payload') else message.get('payload', {})
            message_receiver = getattr(message, 'receiver', payload.get('receiver'))
            operation = str(payload.get('operation', '')).strip().lower()

            # Safety net: only route known API operations handled by this agent.
            valid_ops = {
                # Gmail
                "send", "read", "extract_otp", "extract_links",
                "list_emails", "read_email", "search_emails",
                "delete_email", "mark_read", "reply_email",
                # YouTube
                "youtube_search", "youtube_video_info", "youtube_subscriptions",
                # Calendar
                "calendar_create", "calendar_list", "calendar_delete",
                "calendar_update", "calendar_get",
                # Drive
                "drive_upload", "drive_list", "drive_search",
                "drive_download", "drive_delete", "drive_share",
                "drive_create_folder", "drive_rename", "drive_move",
                # Auth
                "get_browser_cookies"
            }
            if operation not in valid_ops:
                logger.info(f"⏭️ Ignoring non-api payload (operation='{operation}')")
                return

            if message_receiver is not None:
                receiver_value = str(getattr(message_receiver, 'value', message_receiver)).strip().lower()
            else:
                receiver_value = None

            if receiver_value and receiver_value not in {"email", "api"}:
                logger.info(f"⏭️ Ignoring payload not addressed to api agent (receiver='{message_receiver}')")
                return
            
            session_id = message.session_id if hasattr(message, 'session_id') else payload.get('session_id')
            user_id = payload.get('user_id', 'unknown_user')
            incoming_task_id = getattr(message, 'task_id', None) or payload.get('task_id') or str(uuid.uuid4())
            
            logger.info(f"📨 API task received: {operation} for user {user_id}")
            
            # Route to appropriate operation
            if operation == 'send':
                result = await agent.send_email(
                    user_id=user_id,
                    to=payload.get('to') or payload.get('recipient'),  # Accept both field names
                    subject=payload.get('subject'),
                    body=payload.get('body'),
                    attachments=payload.get('attachments')
                )
            
            elif operation == 'read':
                result = await agent.read_unread_emails(
                    user_id=user_id,
                    max_results=payload.get('max_results', 10),
                    query=payload.get('query', 'is:unread')
                )
            
            elif operation == 'extract_otp':
                result = await agent.extract_otp_codes(
                    user_id=user_id,
                    max_results=payload.get('max_results', 5)
                )
            
            elif operation == 'extract_links':
                result = await agent.extract_magic_links(
                    user_id=user_id,
                    max_results=payload.get('max_results', 5)
                )
            
            elif operation == 'youtube_search':
                result = await agent.youtube_search(
                    user_id=user_id,
                    query=payload.get('query', '') or payload.get('search_query', ''),
                    max_results=payload.get('max_results', 10)
                )
            
            elif operation == 'youtube_video_info':
                result = await agent.youtube_video_info(
                    user_id=user_id,
                    video_url=payload.get('video_url', '')
                )
            
            elif operation == 'calendar_create':
                result = await agent.calendar_create(
                    user_id=user_id,
                    title=payload.get('title', ''),
                    start_time=payload.get('start_time', '') or payload.get('start_date', ''),
                    end_time=payload.get('end_time', '') or payload.get('end_date', ''),
                    all_day=payload.get('all_day', False),
                    description=payload.get('description', '')
                )
            
            elif operation == 'calendar_list':
                result = await agent.calendar_list(
                    user_id=user_id,
                    max_results=payload.get('max_results', 10)
                )
            
            elif operation == 'drive_upload':
                result = await agent.drive_upload(
                    user_id=user_id,
                    file_path=payload.get('file_path', ''),
                    parent_folder_id=payload.get('parent_folder_id')
                )
            
            elif operation == 'drive_list':
                result = await agent.drive_list(
                    user_id=user_id,
                    max_results=payload.get('max_results', 10)
                )

            elif operation == 'drive_search':
                result = await agent.drive_search(
                    user_id=user_id,
                    query=payload.get('query', ''),
                    max_results=payload.get('max_results', 50)
                )
            
            elif operation == 'get_browser_cookies':
                cookie_result = await agent.get_browser_cookies(user_id=user_id)
                result = ApiResult(
                    task_id=str(uuid.uuid4()),
                    status=cookie_result['status'],
                    operation='get_browser_cookies',
                    result=cookie_result.get('cookies', []),
                    error=cookie_result.get('error')
                )
            
            else:
                result = ApiResult(
                    task_id=str(uuid.uuid4()),
                    status="failed",
                    operation=operation,
                    error=f"Unknown operation: {operation}"
                )

            response_metadata = {
                "operation": result.operation,
                "message_count": result.message_count,
                "timestamp": result.timestamp,
                "email_result": result.model_dump()
            }
            needs_clarification = False
            clarification_question = None
            clarification_type = None
            recoverable = False

            if result.status == "failed":
                error_text = (result.error or "").lower()
                if "no credentials for user" in error_text:
                    needs_clarification = True
                    clarification_type = "email_oauth_required"
                    recoverable = True
                    response_metadata["email_api_credentials_missing"] = True
                    response_metadata["requires_oauth"] = True
                    response_metadata["oauth_redirect_uri"] = GMAIL_REDIRECT_URI
                    response_metadata["oauth_scopes"] = GMAIL_SCOPES

                    auth_url = None
                    oauth_state = None
                    try:
                        auth_url, oauth_state = await agent.initiate_oauth_flow(user_id)
                    except Exception as oauth_err:
                        logger.error(f"âŒ Failed to initiate OAuth flow for {user_id}: {oauth_err}")
                        response_metadata["oauth_error"] = str(oauth_err)

                    if auth_url:
                        response_metadata["oauth_auth_url"] = auth_url
                    if oauth_state:
                        response_metadata["oauth_state"] = oauth_state

                    if auth_url:
                        clarification_question = (
                            "I need Gmail authorization before I can use the Gmail API. "
                            f"Please connect your account here: {auth_url} "
                            "Then tell me to retry your email request."
                        )
                    else:
                        clarification_question = (
                            "I need Gmail authorization before I can use the Gmail API, but I could not create the OAuth link automatically. "
                            "Please configure Gmail OAuth credentials and then tell me to retry your email request."
                        )
            
            # Publish result
            response = AgentMessage(
                message_type=MessageType.EXECUTION_RESPONSE,
                sender=AgentType.API,
                receiver=AgentType.COORDINATOR,
                session_id=session_id,
                task_id=incoming_task_id,
                response_to=message.message_id if hasattr(message, 'message_id') else None,
                payload={
                    "status": result.status,
                    "content": json.dumps(result.result, default=str) if result.result is not None else "",
                    "details": f"email:{result.operation}",
                    "error": result.error,
                    "metadata": response_metadata,
                    "needs_clarification": needs_clarification,
                    "clarification_question": clarification_question,
                    "clarification_type": clarification_type,
                    "recoverable": recoverable,
                }
            )
            
            await broker_instance.publish(Channels.API_TO_COORDINATOR, response)
            logger.info(f"âœ… API result published: {result.status}")
        
        except Exception as e:
            logger.error(f"âŒ Email task handling failed: {e}", exc_info=True)
    
    # Subscribe to coordinator->api channel
    broker_instance.subscribe(Channels.COORDINATOR_TO_API, handle_email_task)
    logger.info("✅ API agent subscribed to coordinator.to.api")
    
    # Keep agent running
    try:
        while True:
            await asyncio.sleep(60)
    except asyncio.CancelledError:
        logger.info("ï¿½ API agent shutting down...")


# ---------------------------------------------------------------------------
# Backward-compatibility aliases â€” existing imports won't break
# ---------------------------------------------------------------------------
EmailAgent = ApiAgent
EmailTask = ApiTask
EmailResult = ApiResult
start_email_agent = start_api_agent
