#!/usr/bin/env python3
"""
email_agent.py - Gmail API Integration with OAuth2

Features:
- OAuth 2.0 authentication with token refresh
- Send emails with MIME formatting
- Read unread emails
- Extract OTP codes and magic links from emails
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
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.exceptions import RefreshError
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
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
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.send", "https://www.googleapis.com/auth/gmail.readonly"]
ENCRYPTION_KEY = os.environ.get("ENCRYPTION_KEY", Fernet.generate_key().decode())
EMAIL_CREDENTIAL_FALLBACK_USER_ID = os.environ.get("EMAIL_CREDENTIAL_FALLBACK_USER_ID", "").strip()

# MongoDB connection
MONGODB_URI = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
MONGO_DB = "yusr_db"
AURA_DB = "aura_db"

try:
    mongo_client = MongoClient(MONGODB_URI)
    mongo_client.admin.command('ping')
    logger.info("✅ MongoDB connected for email agent")
except Exception as e:
    logger.error(f"❌ MongoDB connection failed: {e}")
    mongo_client = None

# ============================================================================
# DATA MODELS
# ============================================================================

class EmailTask(BaseModel):
    """Task format for email operations"""
    task_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    operation: str  # "send", "read", "extract_otp", "extract_links"
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
    
    class Config:
        use_enum_values = True


class EmailResult(BaseModel):
    """Result from email operation"""
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
                logger.warning("⚠️ ENCRYPTION_KEY not set, using temporary key (not recommended for production)")
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
            logger.error(f"❌ Token decryption failed: {e}")
            return None


# ============================================================================
# EMAIL AGENT
# ============================================================================

class EmailAgent:
    """Gmail API agent with OAuth token management"""
    
    def __init__(self):
        self.encryptor = TokenEncryptor()
        self.gmail_service = None
        self.credentials_cache = {}  # user_id -> Credentials object
        logger.info("✅ Email Agent initialized")
    
    def get_mongodb_collection(self, collection_name: str):
        """Get MongoDB collection"""
        if mongo_client is None:
            logger.error("❌ MongoDB not available")
            return None
        db = mongo_client[MONGO_DB]
        return db[collection_name]
    
    # ────────────────────────────────────────────────────────────────────────
    # OAuth Token Management
    # ────────────────────────────────────────────────────────────────────────

    def _create_oauth_flow(self) -> InstalledAppFlow:
        """Create an OAuth flow from client secrets file or env vars."""
        configured_path = (os.environ.get("GMAIL_CLIENT_SECRETS_FILE") or "gmail_credentials.json").strip()
        backend_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        candidate_paths = [
            configured_path,
            os.path.join(os.getcwd(), configured_path),
            os.path.join(backend_root, configured_path),
        ]

        for path in candidate_paths:
            if path and os.path.isfile(path):
                return InstalledAppFlow.from_client_secrets_file(path, scopes=GMAIL_SCOPES)

        if GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET:
            client_config = {
                "installed": {
                    "client_id": GMAIL_CLIENT_ID,
                    "client_secret": GMAIL_CLIENT_SECRET,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": [GMAIL_REDIRECT_URI],
                }
            }
            return InstalledAppFlow.from_client_config(client_config, scopes=GMAIL_SCOPES)

        raise FileNotFoundError(
            "Gmail OAuth client config not found. Set GMAIL_CLIENT_SECRETS_FILE to a valid file "
            "or set both GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET."
        )
    
    async def initiate_oauth_flow(self, user_id: str) -> Tuple[str, str]:
        """
        Initiate OAuth flow for user
        Returns (auth_url, state)
        """
        try:
            flow = self._create_oauth_flow()
        except Exception as e:
            logger.error(f"❌ Failed to create OAuth flow: {e}")
            return None, None
        
        flow.redirect_uri = GMAIL_REDIRECT_URI
        auth_url, state = flow.authorization_url(access_type='offline', prompt='consent')
        
        logger.info(f"🔐 OAuth flow initiated for user {user_id}")
        return auth_url, state
    
    async def handle_oauth_callback(self, user_id: str, auth_code: str) -> bool:
        """
        Handle OAuth callback with authorization code
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
            
            logger.info(f"✅ OAuth callback processed for user {user_id}")
            return True
        
        except Exception as e:
            logger.error(f"❌ OAuth callback failed: {e}")
            return False
    
    async def _store_credentials_mongodb(self, user_id: str, creds: Credentials):
        """Store OAuth credentials in MongoDB with encryption"""
        collection = self.get_mongodb_collection("user_credentials_email")
        if collection is None:
            logger.error("❌ Cannot store credentials: MongoDB unavailable")
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
            
            logger.info(f"✅ Credentials stored for {user_id} ({email_address})")
        
        except Exception as e:
            logger.error(f"❌ Failed to store credentials: {e}")
    
    async def _get_credentials_mongodb(self, user_id: str) -> Optional[Credentials]:
        """Retrieve and refresh credentials from MongoDB"""
        # Check cache first
        if user_id in self.credentials_cache:
            creds = self.credentials_cache[user_id]
            if creds.valid:
                return creds
        
        collection = self.get_mongodb_collection("user_credentials_email")
        if collection is None:
            logger.error("❌ Cannot retrieve credentials: MongoDB unavailable")
            return None
        
        try:
            doc = collection.find_one({'user_id': user_id})
            if not doc:
                logger.warning(f"⚠️ No credentials found for user {user_id}")
                return None
            
            # Decrypt refresh token
            encrypted_token = doc.get('encrypted_refresh_token')
            if not encrypted_token:
                logger.error(f"❌ No refresh token for user {user_id}")
                return None
            
            refresh_token = self.encryptor.decrypt(encrypted_token)
            if not refresh_token:
                logger.error(f"❌ Failed to decrypt refresh token for user {user_id}")
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
                    logger.info(f"✅ Access token refreshed for {user_id}")
                except RefreshError as e:
                    logger.error(f"❌ Token refresh failed: {e}")
                    return None
            
            # Cache
            self.credentials_cache[user_id] = creds
            return creds
        
        except Exception as e:
            logger.error(f"❌ Failed to retrieve credentials: {e}")
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
            
            logger.info(f"✅ Credentials revoked for {user_id}")
        except Exception as e:
            logger.error(f"❌ Failed to revoke credentials: {e}")

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
                f"⚠️ No credentials for user {user_id}; mapped to credential owner {mapped_user} from DB"
            )
            mapped_creds = await self._get_credentials_mongodb(mapped_user)
            if mapped_creds:
                return mapped_creds, mapped_user

        fallback_user = EMAIL_CREDENTIAL_FALLBACK_USER_ID
        if fallback_user and fallback_user != user_id:
            logger.warning(
                f"⚠️ No credentials for user {user_id}; trying fallback credential owner {fallback_user}"
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
            if not user_doc:
                return None

            candidates: List[str] = []
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

            return None
        except Exception as e:
            logger.warning(f"⚠️ Failed resolving credential owner for {user_id}: {e}")
            return None
    
    # ────────────────────────────────────────────────────────────────────────
    # Email Operations
    # ────────────────────────────────────────────────────────────────────────
    
    async def send_email(self, user_id: str, to: str, subject: str, body: str, 
                         attachments: Optional[List[Dict]] = None) -> EmailResult:
        """Send email via Gmail API"""
        task_id = str(uuid.uuid4())
        
        try:
            creds, credential_user_id = await self._get_credentials_with_fallback(user_id)
            if not creds:
                return EmailResult(
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
                        logger.warning(f"⚠️ Failed to attach {att.get('name')}: {e}")
            
            # Send
            raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
            send_message = {'raw': raw_message}
            
            service.users().messages().send(userId='me', body=send_message).execute()
            
            logger.info(
                f"✅ Email sent to {to} for user {user_id} using credentials from {credential_user_id}"
            )
            
            return EmailResult(
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
            logger.error(f"❌ Failed to send email: {e}")
            return EmailResult(
                task_id=task_id,
                status="failed",
                operation="send",
                error=str(e)
            )
    
    async def read_unread_emails(self, user_id: str, max_results: int = 10, 
                                  query: str = "is:unread") -> EmailResult:
        """Read unread emails from Gmail"""
        task_id = str(uuid.uuid4())
        
        try:
            creds, credential_user_id = await self._get_credentials_with_fallback(user_id)
            if not creds:
                return EmailResult(
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
                    logger.warning(f"⚠️ Failed to process message {msg['id']}: {e}")
            
            logger.info(
                f"✅ Retrieved {len(email_data)} unread emails for user {user_id} using credentials from {credential_user_id}"
            )
            
            return EmailResult(
                task_id=task_id,
                status="success",
                operation="read",
                result=email_data,
                message_count=len(email_data)
            )
        
        except Exception as e:
            logger.error(f"❌ Failed to read emails: {e}")
            return EmailResult(
                task_id=task_id,
                status="failed",
                operation="read",
                error=str(e)
            )
    
    async def extract_otp_codes(self, user_id: str, max_results: int = 5) -> EmailResult:
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
            
            logger.info(f"✅ Extracted {len(otp_codes)} OTP codes for user {user_id}")
            
            return EmailResult(
                task_id=task_id,
                status="success",
                operation="extract_otp",
                result=otp_codes,
                message_count=len(otp_codes)
            )
        
        except Exception as e:
            logger.error(f"❌ Failed to extract OTP codes: {e}")
            return EmailResult(
                task_id=task_id,
                status="failed",
                operation="extract_otp",
                error=str(e)
            )
    
    async def extract_magic_links(self, user_id: str, max_results: int = 5) -> EmailResult:
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
            
            logger.info(f"✅ Extracted {len(magic_links)} magic links for user {user_id}")
            
            return EmailResult(
                task_id=task_id,
                status="success",
                operation="extract_links",
                result=magic_links,
                message_count=len(magic_links)
            )
        
        except Exception as e:
            logger.error(f"❌ Failed to extract magic links: {e}")
            return EmailResult(
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
            logger.warning(f"⚠️ Failed to cache email: {e}")


# ============================================================================
# AGENT STARTUP
# ============================================================================

async def start_email_agent(broker_instance=None):
    """Start email agent and subscribe to broker channels"""
    
    if broker_instance is None:
        broker_instance = broker
    
    print("=" * 70)
    print("📧 EMAIL AGENT - READY (GMAIL API)!")
    print("=" * 70)
    print("Waiting for email tasks...\n")
    
    agent = EmailAgent()
    
    async def handle_email_task(message: dict):
        """Handle email task from coordinator"""
        try:
            payload = message.payload if hasattr(message, 'payload') else message.get('payload', {})
            message_receiver = getattr(message, 'receiver', payload.get('receiver'))
            operation = str(payload.get('operation', '')).strip().lower()

            # Safety net: ignore anything that is not an explicit email-targeted operation.
            valid_ops = {"send", "read", "extract_otp", "extract_links"}
            if operation not in valid_ops:
                logger.debug(f"⏭️ Ignoring non-email payload (operation='{operation}')")
                return

            if message_receiver is not None:
                receiver_value = str(getattr(message_receiver, 'value', message_receiver)).strip().lower()
            else:
                receiver_value = None

            if receiver_value and receiver_value not in {AgentType.EMAIL.value, AgentType.EMAIL.name.lower(), "email"}:
                logger.debug(f"⏭️ Ignoring payload not addressed to email agent (receiver='{message_receiver}')")
                return
            
            session_id = message.session_id if hasattr(message, 'session_id') else payload.get('session_id')
            user_id = payload.get('user_id', 'unknown_user')
            incoming_task_id = getattr(message, 'task_id', None) or payload.get('task_id') or str(uuid.uuid4())
            
            logger.info(f"📧 Email task received: {operation} for user {user_id}")
            
            # Route to appropriate operation
            if operation == 'send':
                result = await agent.send_email(
                    user_id=user_id,
                    to=payload.get('to'),
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
            
            else:
                result = EmailResult(
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
                        logger.error(f"❌ Failed to initiate OAuth flow for {user_id}: {oauth_err}")
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
                sender=AgentType.EMAIL,
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
            
            await broker_instance.publish(Channels.EMAIL_TO_COORDINATOR, response)
            logger.info(f"✅ Email result published: {result.status}")
        
        except Exception as e:
            logger.error(f"❌ Email task handling failed: {e}", exc_info=True)
    
    # Subscribe only to dedicated coordinator->email channel.
    broker_instance.subscribe(Channels.COORDINATOR_TO_EMAIL, handle_email_task)
    logger.info("✅ Email agent subscribed to coordinator.to.email")
    
    # Keep agent running
    try:
        while True:
            await asyncio.sleep(60)
    except asyncio.CancelledError:
        logger.info("📧 Email agent shutting down...")
