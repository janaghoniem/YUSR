"""
backend/routes/api_keys.py
==========================
Bring Your Own Key (BYOK) — secure API key management.

Security model:
- Keys are encrypted with AES-256-GCM before storage.
- The encryption key is derived from a server-side secret (BYOK_SECRET)
  combined with the user_id, so one user's key cannot decrypt another's.
- Keys are NEVER logged or returned to the frontend after initial save.
- Keys are decrypted in-memory only at call time, then immediately released.
- The /proxy endpoint routes model calls to the user's chosen provider
  without forwarding the key to the frontend.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
from typing import Any, Optional

import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core.mongo import get_database

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/byok", tags=["byok"])

# ── Encryption helpers ────────────────────────────────────────────────────────

_BYOK_SECRET = os.environ.get("BYOK_SECRET", "")
if not _BYOK_SECRET:
    logger.warning(
        "⚠️  BYOK_SECRET env var is not set. "
        "A deterministic fallback will be used — set this in production."
    )
    _BYOK_SECRET = "aura_byok_default_secret_change_in_prod_2024"


def _derive_key(user_id: str) -> bytes:
    """
    Derive a 32-byte AES key from the server secret + user_id.
    Using SHA-256 as a simple KDF (HKDF would be better in high-security contexts,
    but SHA-256 here is sufficient given the secret is already high-entropy).
    """
    material = f"{_BYOK_SECRET}:{user_id}".encode()
    return hashlib.sha256(material).digest()          # 32 bytes → AES-256


def _encrypt(plaintext: str, user_id: str) -> str:
    """AES-256-GCM encrypt. Returns base64(nonce + ciphertext + tag)."""
    key = _derive_key(user_id)
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)                            # 96-bit nonce
    ct = aesgcm.encrypt(nonce, plaintext.encode(), None)
    blob = nonce + ct                                 # prepend nonce
    return base64.b64encode(blob).decode()


def _decrypt(blob_b64: str, user_id: str) -> str:
    """Reverse of _encrypt. Raises ValueError on bad key / tampered data."""
    key = _derive_key(user_id)
    aesgcm = AESGCM(key)
    blob = base64.b64decode(blob_b64)
    nonce, ct = blob[:12], blob[12:]
    return aesgcm.decrypt(nonce, ct, None).decode()


# ── Provider catalogue ────────────────────────────────────────────────────────

PROVIDERS: dict[str, dict[str, Any]] = {
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "test_endpoint": "/models",
        "test_method": "GET",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
        "default_model": "gpt-4o",
        "chat_endpoint": "/chat/completions",
    },
    "anthropic": {
        "label": "Anthropic",
        "base_url": "https://api.anthropic.com/v1",
        "test_endpoint": "/models",
        "test_method": "GET",
        "auth_header": "x-api-key",
        "auth_prefix": "",
        "extra_headers": {
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "messages-2023-12-15",
        },
        "default_model": "claude-opus-4-6",
        "chat_endpoint": "/messages",
    },
    "groq": {
        "label": "Groq",
        "base_url": "https://api.groq.com/openai/v1",
        "test_endpoint": "/models",
        "test_method": "GET",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
        "default_model": "llama3-70b-8192",
        "chat_endpoint": "/chat/completions",
    },
    "cohere": {
        "label": "Cohere",
        "base_url": "https://api.cohere.ai/v1",
        "test_endpoint": "/check-api-key",
        "test_method": "POST",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
        "default_model": "command-r-plus",
        "chat_endpoint": "/chat",
    },
    "mistral": {
        "label": "Mistral AI",
        "base_url": "https://api.mistral.ai/v1",
        "test_endpoint": "/models",
        "test_method": "GET",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
        "default_model": "mistral-large-latest",
        "chat_endpoint": "/chat/completions",
    },
    "together": {
        "label": "Together AI",
        "base_url": "https://api.together.xyz/v1",
        "test_endpoint": "/models",
        "test_method": "GET",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
        "default_model": "meta-llama/Llama-3-70b-chat-hf",
        "chat_endpoint": "/chat/completions",
    },
    "google": {
        "label": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "test_endpoint": "/models",
        "test_method": "GET",
        "auth_header": "x-goog-api-key",
        "auth_prefix": "",
        "default_model": "gemini-1.5-pro",
        "chat_endpoint": "/models/{model}:generateContent",
        "api_key_param": True,          # some Google endpoints use ?key=
    },
}


# ── Pydantic models ───────────────────────────────────────────────────────────

class SaveKeyRequest(BaseModel):
    user_id: str
    provider: str
    api_key: str
    model: Optional[str] = None
    label: Optional[str] = None         # friendly name, e.g. "Work OpenAI key"


class TestKeyRequest(BaseModel):
    user_id: str
    provider: str
    api_key: str                         # raw key (never stored by this endpoint)


class ProxyRequest(BaseModel):
    user_id: str
    provider: str
    messages: list[dict]
    model: Optional[str] = None
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = 1024
    system: Optional[str] = None


class DeleteKeyRequest(BaseModel):
    user_id: str
    provider: str


# ── DB helpers ────────────────────────────────────────────────────────────────

def _col():
    db = get_database("aura_db")
    if db is None:
        raise HTTPException(status_code=500, detail="Database unavailable")
    return db["byok_keys"]


async def _get_doc(user_id: str, provider: str) -> Optional[dict]:
    col = _col()
    return await asyncio.to_thread(
        col.find_one, {"user_id": user_id, "provider": provider}
    )


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/providers")
async def list_providers():
    """Return the catalogue of supported providers (no keys)."""
    return {
        "providers": [
            {
                "id": pid,
                "label": cfg["label"],
                "default_model": cfg.get("default_model", ""),
            }
            for pid, cfg in PROVIDERS.items()
        ]
    }


@router.post("/keys/save")
async def save_api_key(req: SaveKeyRequest):
    """
    Encrypt and persist the user's API key.
    The raw key is NEVER stored and not returned.
    """
    if req.provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {req.provider}")

    if not req.api_key or len(req.api_key.strip()) < 8:
        raise HTTPException(status_code=400, detail="API key appears too short")

    encrypted = _encrypt(req.api_key.strip(), req.user_id)

    doc = {
        "user_id": req.user_id,
        "provider": req.provider,
        "encrypted_key": encrypted,
        "model": req.model or PROVIDERS[req.provider].get("default_model", ""),
        "label": req.label or PROVIDERS[req.provider]["label"],
        "enabled": True,
    }

    col = _col()
    await asyncio.to_thread(
        col.update_one,
        {"user_id": req.user_id, "provider": req.provider},
        {"$set": doc},
        upsert=True,
    )

    logger.info(f"[BYOK] Key saved for user={req.user_id} provider={req.provider}")
    return {"status": "ok", "provider": req.provider}


@router.post("/keys/test")
async def test_api_key(req: TestKeyRequest):
    """
    Validate a raw API key against the provider WITHOUT storing it.
    Returns {ok: bool, message: str}.
    """
    if req.provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {req.provider}")

    cfg = PROVIDERS[req.provider]
    url = cfg["base_url"] + cfg["test_endpoint"]
    headers = {cfg["auth_header"]: cfg["auth_prefix"] + req.api_key.strip()}
    headers.update(cfg.get("extra_headers", {}))

    # Never log the key
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            method = cfg.get("test_method", "GET").upper()
            if method == "GET":
                r = await client.get(url, headers=headers)
            else:
                r = await client.post(url, headers=headers, json={})

        if r.status_code in (200, 204):
            return {"ok": True, "message": f"Key validated with {cfg['label']}"}
        if r.status_code == 401:
            return {"ok": False, "message": "Invalid API key — authentication failed"}
        if r.status_code == 403:
            return {"ok": False, "message": "Key valid but lacks required permissions"}
        return {"ok": False, "message": f"Provider returned HTTP {r.status_code}"}

    except httpx.TimeoutException:
        return {"ok": False, "message": "Connection to provider timed out"}
    except Exception as exc:
        logger.warning(f"[BYOK] test_key error: {type(exc).__name__}")
        return {"ok": False, "message": "Could not reach provider — check your network"}


@router.get("/keys/list")
async def list_saved_keys(user_id: str):
    """Return metadata about saved keys (never the raw or encrypted key)."""
    col = _col()
    docs = await asyncio.to_thread(
        lambda: list(col.find({"user_id": user_id}, {"_id": 0, "encrypted_key": 0}))
    )
    return {"keys": docs}


@router.get("/keys/active")
async def get_active_key(user_id: str):
    """Return the active provider config (no key material) for the user."""
    col = _col()
    doc = await asyncio.to_thread(
        col.find_one,
        {"user_id": user_id, "enabled": True},
        {"_id": 0, "encrypted_key": 0},
        # sort by most recently upserted would need a timestamp field; keep simple
    )
    if not doc:
        return {"active": None}
    return {"active": doc}


@router.delete("/keys/delete")
async def delete_api_key(req: DeleteKeyRequest):
    """Permanently remove a stored key."""
    col = _col()
    result = await asyncio.to_thread(
        col.delete_one, {"user_id": req.user_id, "provider": req.provider}
    )
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="No key found for that provider")
    logger.info(f"[BYOK] Key deleted for user={req.user_id} provider={req.provider}")
    return {"status": "ok"}


@router.post("/proxy/chat")
async def proxy_chat(req: ProxyRequest):
    """
    Route a chat completion request to the user's chosen provider using
    their stored encrypted key. The key is decrypted in-memory only.
    """
    if req.provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {req.provider}")

    doc = await _get_doc(req.user_id, req.provider)
    if not doc or not doc.get("encrypted_key"):
        raise HTTPException(
            status_code=404,
            detail=f"No API key stored for provider '{req.provider}'. Save one in Settings → API Keys."
        )

    # Decrypt in memory only
    try:
        raw_key = _decrypt(doc["encrypted_key"], req.user_id)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to decrypt API key — it may be corrupt")

    cfg = PROVIDERS[req.provider]
    model = req.model or doc.get("model") or cfg.get("default_model", "")

    headers = {cfg["auth_header"]: cfg["auth_prefix"] + raw_key}
    headers.update(cfg.get("extra_headers", {}))
    headers["Content-Type"] = "application/json"

    # Build provider-specific payload
    payload: dict[str, Any] = {}
    try:
        if req.provider == "anthropic":
            chat_ep = cfg["chat_endpoint"]
            payload = {
                "model": model,
                "max_tokens": req.max_tokens,
                "messages": req.messages,
            }
            if req.system:
                payload["system"] = req.system

        elif req.provider == "cohere":
            chat_ep = cfg["chat_endpoint"]
            # Cohere uses a different schema
            chat_history = []
            current_message = ""
            for m in req.messages:
                role = "USER" if m["role"] == "user" else "CHATBOT"
                if m == req.messages[-1] and m["role"] == "user":
                    current_message = m["content"]
                else:
                    chat_history.append({"role": role, "message": m["content"]})
            payload = {
                "model": model,
                "message": current_message,
                "chat_history": chat_history,
                "max_tokens": req.max_tokens,
            }
            if req.system:
                payload["preamble"] = req.system

        elif req.provider == "google":
            chat_ep = cfg["chat_endpoint"].replace("{model}", model)
            contents = []
            for m in req.messages:
                role = "user" if m["role"] == "user" else "model"
                contents.append({"role": role, "parts": [{"text": m["content"]}]})
            payload = {
                "contents": contents,
                "generationConfig": {
                    "maxOutputTokens": req.max_tokens,
                    "temperature": req.temperature,
                },
            }
            if req.system:
                payload["systemInstruction"] = {"parts": [{"text": req.system}]}

        else:
            # OpenAI-compatible (openai, groq, mistral, together, etc.)
            chat_ep = cfg["chat_endpoint"]
            msgs = list(req.messages)
            if req.system:
                msgs = [{"role": "system", "content": req.system}] + msgs
            payload = {
                "model": model,
                "messages": msgs,
                "max_tokens": req.max_tokens,
                "temperature": req.temperature,
            }

        url = cfg["base_url"] + chat_ep

        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(url, headers=headers, json=payload)

        # Clear key from memory ASAP (Python GC, best-effort)
        del raw_key

        if r.status_code != 200:
            detail = r.text[:400] if r.text else f"HTTP {r.status_code}"
            raise HTTPException(status_code=r.status_code, detail=detail)

        data = r.json()

        # Normalise response to a common shape
        text = ""
        if req.provider == "anthropic":
            text = data.get("content", [{}])[0].get("text", "")
        elif req.provider == "cohere":
            text = data.get("text", "")
        elif req.provider == "google":
            candidates = data.get("candidates", [{}])
            text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
        else:
            text = data.get("choices", [{}])[0].get("message", {}).get("content", "")

        return {
            "status": "ok",
            "provider": req.provider,
            "model": model,
            "text": text,
            "raw": data,
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"[BYOK] proxy_chat error ({req.provider}): {type(exc).__name__}: {exc}")
        raise HTTPException(status_code=500, detail="Provider request failed")