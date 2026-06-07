"""
memory_layer.py
===============
AURA Unified Async Memory Layer
---------------------------------
Replaces the blocking PyMongo + synchronous Mem0 pattern with:

  1. Redis sub-millisecond hot cache  (conversation state + preference cache)
  2. PyMongo AsyncMongoClient          (non-blocking MongoDB for persistence)
  3. AsyncMemory (Mem0)                (non-blocking vector search / writes)
  4. Background fire-and-forget writes (preference storage never blocks response)

Usage
-----
At application startup (lifespan or startup event):

    from memory_layer import memory_layer
    await memory_layer.initialize()

Then in agents:

    # Conversation history
    messages = await memory_layer.load_conversation(session_id, user_id)
    await memory_layer.save_conversation(session_id, user_id, messages, metadata)

    # Preferences (cached, async, background writes)
    prefs = await memory_layer.get_relevant_preferences(user_id, query)
    memory_layer.add_preference_background(user_id, text, metadata)  # fire-and-forget
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

REDIS_URL       = os.getenv("REDIS_URL", "redis://localhost:6379/0")
MONGODB_URI     = os.getenv("MONGODB_URI", "")
MONGO_DB_NAME   = os.getenv("MONGO_DB_NAME", "yusr_db")

CONV_CACHE_TTL  = int(os.getenv("CONV_CACHE_TTL_SECONDS",  "3600"))   # 1 hour
PREF_CACHE_TTL  = int(os.getenv("PREF_CACHE_TTL_SECONDS",  "120"))    # 2 min
PREF_QUEUE_SIZE = int(os.getenv("PREF_WRITE_QUEUE_SIZE",   "200"))


# ── Redis helper ─────────────────────────────────────────────────────────────

class RedisCache:
    """Thin async Redis wrapper — silently degrades if Redis is unavailable."""

    def __init__(self, url: str):
        self._url = url
        self._redis: Optional[Any] = None

    async def connect(self) -> bool:
        try:
            import redis.asyncio as aioredis  # pip install redis[asyncio]
            self._redis = await aioredis.from_url(
                self._url,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=1,
            )
            await self._redis.ping()
            logger.info("✅ Redis connected: %s", self._url)
            return True
        except Exception as exc:
            logger.warning(
                "⚠️ Redis unavailable (%s) — cache disabled, falling back to MongoDB only", exc
            )
            self._redis = None
            return False

    @property
    def available(self) -> bool:
        return self._redis is not None

    async def get(self, key: str) -> Optional[str]:
        if not self.available:
            return None
        try:
            return await self._redis.get(key)
        except Exception:
            return None

    async def set(self, key: str, value: str, ttl: int) -> None:
        if not self.available:
            return
        try:
            await self._redis.set(key, value, ex=ttl)
        except Exception:
            pass

    async def delete(self, key: str) -> None:
        if not self.available:
            return
        try:
            await self._redis.delete(key)
        except Exception:
            pass

    async def close(self) -> None:
        if self._redis:
            await self._redis.aclose()


# ── Async MongoDB helper ──────────────────────────────────────────────────────

class AsyncMongoStore:
    """
    Async MongoDB access.
    Tries native AsyncMongoClient (pymongo >= 4.7) first.
    Falls back to wrapping sync pymongo in asyncio.to_thread if not available.
    """

    def __init__(self, uri: str, db_name: str):
        self._uri = uri
        self._db_name = db_name
        self._client: Optional[Any] = None
        self._db: Optional[Any] = None

    async def connect(self) -> bool:
        try:
            from pymongo import AsyncMongoClient  # pymongo >= 4.7
            self._client = AsyncMongoClient(self._uri)
            self._db = self._client[self._db_name]
            await self._client.admin.command("ping")
            logger.info("✅ Async MongoDB connected (native AsyncMongoClient)")
            return True
        except ImportError:
            logger.warning(
                "⚠️ pymongo AsyncMongoClient not available (upgrade to pymongo>=4.7). "
                "Using asyncio.to_thread wrapper as fallback."
            )
            from pymongo import MongoClient
            _sync_client = MongoClient(self._uri)
            _sync_db = _sync_client[self._db_name]

            class _AsyncCollectionShim:
                def __init__(self, collection):
                    self._col = collection

                async def find_one(self, *args, **kwargs):
                    return await asyncio.to_thread(self._col.find_one, *args, **kwargs)

                async def find(self, *args, **kwargs):
                    def _fetch():
                        return list(self._col.find(*args, **kwargs))
                    return await asyncio.to_thread(_fetch)

                async def update_one(self, *args, **kwargs):
                    return await asyncio.to_thread(self._col.update_one, *args, **kwargs)

                async def insert_one(self, *args, **kwargs):
                    return await asyncio.to_thread(self._col.insert_one, *args, **kwargs)

                async def delete_many(self, *args, **kwargs):
                    return await asyncio.to_thread(self._col.delete_many, *args, **kwargs)

                async def count_documents(self, *args, **kwargs):
                    return await asyncio.to_thread(self._col.count_documents, *args, **kwargs)

            class _AsyncDbShim:
                def __init__(self, db):
                    self._db = db
                def __getitem__(self, name):
                    return _AsyncCollectionShim(self._db[name])

            self._db = _AsyncDbShim(_sync_db)
            self._client = _sync_client
            logger.info("✅ Sync PyMongo wrapped in asyncio.to_thread (upgrade to pymongo>=4.7 for better perf)")
            return True
        except Exception as exc:
            logger.error("❌ MongoDB connection failed: %s", exc)
            return False

    def collection(self, name: str):
        if self._db is None:
            raise RuntimeError("MongoDB not connected. Call connect() first.")
        return self._db[name]

    async def close(self) -> None:
        if self._client:
            try:
                await self._client.close()
            except Exception:
                pass


# ── Async Mem0 preference manager ────────────────────────────────────────────

class AsyncPreferenceManager:
    """
    Wraps Mem0 in async interface.
    Uses AsyncMemory if available (mem0 >= 0.1.40), otherwise
    wraps synchronous Memory in asyncio.to_thread.
    """

    def __init__(self, user_id: str):
        self._user_id = user_id
        self._mem: Optional[Any] = None
        self._is_async = False

    async def initialize(self) -> None:
        MONGODB_URI = os.getenv("MONGODB_URI", "")
        GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
        config = {
            "vector_store": {
                "provider": "mongodb",
                "config": {
                    "mongo_uri": MONGODB_URI,
                    "db_name": "yusr_db",
                    "collection_name": "mem0_preferences",
                    "embedding_model_dims": 384,
                },
            },
            "llm": {
                "provider": "groq",
                "config": {
                    "model": "llama-3.3-70b-versatile",
                    "temperature": 0.1,
                    "max_tokens": 2000,
                    "api_key": GROQ_API_KEY,
                },
            },
            "embedder": {
                "provider": "huggingface",
                "config": {
                    "model": "sentence-transformers/all-MiniLM-L6-v2",
                },
            },
        }
        try:
            from mem0 import AsyncMemory  # mem0 >= 0.1.40
            self._mem = AsyncMemory.from_config(config)
            self._is_async = True
            logger.info("✅ Using AsyncMemory (Groq LLM + local embeddings) for user %s", self._user_id)
        except (ImportError, AttributeError):
            try:
                from mem0 import Memory
                self._mem = Memory.from_config(config)
                self._is_async = False
                logger.info(
                    "✅ Using sync Memory (Groq LLM + local embeddings) wrapped in to_thread for user %s",
                    self._user_id
                )
            except Exception as exc:
                logger.error("❌ Could not initialize Mem0: %s", exc)

    async def search(self, query: str, limit: int = 5) -> List[Dict]:
        if self._mem is None:
            return []
        try:
            if self._is_async:
                result = await self._mem.search(query, user_id=self._user_id, limit=limit)
            else:
                result = await asyncio.to_thread(
                    self._mem.search, query, user_id=self._user_id, limit=limit
                )
            if isinstance(result, dict):
                return result.get("results", result.get("memories", []))
            return result if isinstance(result, list) else []
        except Exception as exc:
            logger.warning("⚠️ Mem0 search failed: %s", exc)
            return []

    async def add(self, text: str, metadata: Optional[Dict] = None) -> None:
        if self._mem is None:
            return
        try:
            kwargs: Dict = {"user_id": self._user_id}
            if metadata:
                kwargs["metadata"] = metadata
            if self._is_async:
                await self._mem.add(text, **kwargs)
            else:
                await asyncio.to_thread(self._mem.add, text, **kwargs)
        except Exception as exc:
            logger.warning("⚠️ Mem0 add failed: %s", exc)

    async def get_all(self) -> List[Dict]:
        if self._mem is None:
            return []
        try:
            if self._is_async:
                result = await self._mem.get_all(user_id=self._user_id)
            else:
                result = await asyncio.to_thread(self._mem.get_all, user_id=self._user_id)
            if isinstance(result, dict):
                return result.get("results", result.get("memories", []))
            return result if isinstance(result, list) else []
        except Exception as exc:
            logger.warning("⚠️ Mem0 get_all failed: %s", exc)
            return []

    async def delete(self, memory_id: str) -> bool:
        if self._mem is None:
            return False
        try:
            if self._is_async:
                await self._mem.delete(memory_id=memory_id)
            else:
                await asyncio.to_thread(self._mem.delete, memory_id=memory_id)
            return True
        except Exception as exc:
            logger.warning("⚠️ Mem0 delete failed: %s", exc)
            return False


# ── Preference write queue (background fire-and-forget) ─────────────────────

class PreferenceWriteQueue:
    """
    Enqueue preference writes so they never block the response path.
    A background asyncio task drains the queue.
    """

    def __init__(self, maxsize: int = PREF_QUEUE_SIZE):
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._task: Optional[asyncio.Task] = None
        self._managers: Dict[str, AsyncPreferenceManager] = {}

    def start(self) -> None:
        self._task = asyncio.create_task(self._drain(), name="pref_write_queue")
        logger.info("✅ Preference write queue started")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def enqueue(self, user_id: str, text: str, metadata: Optional[Dict] = None) -> None:
        """Non-blocking enqueue. Drops if queue is full (preference loss is acceptable)."""
        try:
            self._queue.put_nowait({"user_id": user_id, "text": text, "metadata": metadata})
        except asyncio.QueueFull:
            logger.debug("⚠️ Preference queue full — dropping write for user %s", user_id)

    async def _drain(self) -> None:
        while True:
            try:
                item = await self._queue.get()
                user_id = item["user_id"]
                mgr = self._managers.get(user_id)
                if mgr is None:
                    mgr = AsyncPreferenceManager(user_id)
                    await mgr.initialize()
                    self._managers[user_id] = mgr
                await mgr.add(item["text"], item.get("metadata"))
                self._queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("⚠️ Preference write queue error: %s", exc)
                await asyncio.sleep(1)


# ── Main MemoryLayer facade ──────────────────────────────────────────────────

class MemoryLayer:
    """
    Single facade that every agent should use for memory operations.
    Thread-safety: all methods are async and safe for concurrent coroutines.
    """

    def __init__(self):
        self.cache = RedisCache(REDIS_URL)
        self.mongo = AsyncMongoStore(MONGODB_URI, MONGO_DB_NAME)
        self.pref_queue = PreferenceWriteQueue()
        self._pref_managers: Dict[str, AsyncPreferenceManager] = {}
        self._initialized = False

    async def initialize(self) -> None:
        if self._initialized:
            return
        await self.cache.connect()
        await self.mongo.connect()
        self.pref_queue.start()
        self._initialized = True
        logger.info("✅ MemoryLayer initialized")

    async def close(self) -> None:
        await self.pref_queue.stop()
        await self.cache.close()
        await self.mongo.close()

    # ── Conversation history ─────────────────────────────────────────────────

    def _conv_key(self, session_id: str, user_id: str) -> str:
        return f"conv:{user_id}:{session_id}"

    async def load_conversation(
        self,
        session_id: str,
        user_id: str,
        system_prompt: Optional[Dict] = None,
    ) -> List[Dict]:
        """
        Load conversation messages.
        Cache hit: <1 ms.  Cache miss: async MongoDB query.
        """
        key = self._conv_key(session_id, user_id)

        # 1. Try Redis cache first
        cached = await self.cache.get(key)
        if cached:
            try:
                data = json.loads(cached)
                messages = data.get("messages", [])
                logger.debug("📗 Conversation loaded from Redis cache (session=%s)", session_id)
                return messages
            except Exception:
                pass

        # 2. Fall back to MongoDB
        try:
            col = self.mongo.collection("language_agent_conversations")
            doc = await col.find_one(
                {"session_id": session_id, "user_id": user_id},
                sort=[("timestamp", -1)],
            )
            if doc and "messages" in doc:
                messages = doc["messages"]
                await self.cache.set(key, json.dumps({"messages": messages}), CONV_CACHE_TTL)
                logger.debug("📗 Conversation loaded from MongoDB (session=%s)", session_id)
                return messages
        except Exception as exc:
            logger.error("❌ load_conversation MongoDB error: %s", exc)

        # 3. Fresh start
        base = [system_prompt] if system_prompt else []
        return base

    async def save_conversation(
        self,
        session_id: str,
        user_id: str,
        messages: List[Dict],
        extra: Optional[Dict] = None,
    ) -> None:
        """
        Save conversation.
        Step 1 (fast): write to Redis immediately (<1 ms).
        Step 2 (background): persist to MongoDB asynchronously.
        """
        key = self._conv_key(session_id, user_id)
        payload = {"messages": messages, **(extra or {})}

        await self.cache.set(key, json.dumps(payload), CONV_CACHE_TTL)

        asyncio.create_task(
            self._persist_conversation(session_id, user_id, messages, extra),
            name=f"persist_conv_{session_id}",
        )

    async def _persist_conversation(
        self,
        session_id: str,
        user_id: str,
        messages: List[Dict],
        extra: Optional[Dict],
    ) -> None:
        try:
            col = self.mongo.collection("language_agent_conversations")
            update_doc: Dict = {
                "messages": messages,
                "timestamp": time.time(),
                "last_updated": int(time.time()),
            }
            if extra:
                update_doc.update(extra)
            await col.update_one(
                {"session_id": session_id, "user_id": user_id},
                {"$set": update_doc},
                upsert=True,
            )
        except Exception as exc:
            logger.error("❌ Background conversation persist failed: %s", exc)

    # ── Preferences ──────────────────────────────────────────────────────────

    def _pref_search_key(self, user_id: str, query: str) -> str:
        import hashlib
        q_hash = hashlib.md5(query.encode()).hexdigest()[:12]
        return f"pref_search:{user_id}:{q_hash}"

    async def _get_pref_manager(self, user_id: str) -> AsyncPreferenceManager:
        if user_id not in self._pref_managers:
            mgr = AsyncPreferenceManager(user_id)
            await mgr.initialize()
            self._pref_managers[user_id] = mgr
        return self._pref_managers[user_id]

    async def get_relevant_preferences(
        self,
        user_id: str,
        query: str,
        limit: int = 5,
    ) -> str:
        """
        Retrieve preferences relevant to a query.
        Cache hit  : <1 ms.
        Cache miss : async Mem0 vector search (non-blocking).
        Returns formatted string ready for LLM prompt injection.
        """
        if not query or not query.strip():
            return "No user preferences available."

        key = self._pref_search_key(user_id, query)

        # 1. Redis cache
        cached = await self.cache.get(key)
        if cached:
            logger.debug("⚡ Preference cache HIT for user %s", user_id)
            return cached

        # 2. Async Mem0 vector search
        try:
            mgr = await self._get_pref_manager(user_id)
            results = await mgr.search(query, limit=limit)

            lines: List[str] = []
            for i, item in enumerate(results, 1):
                if isinstance(item, dict):
                    text = item.get("memory") or item.get("text") or str(item)
                else:
                    text = str(item)
                lines.append(f"{i}. {text.strip()}")

            formatted = "\n".join(lines) if lines else "No user preferences available."

            await self.cache.set(key, formatted, PREF_CACHE_TTL)
            logger.debug("📙 Preference search result cached for user %s", user_id)
            return formatted

        except Exception as exc:
            logger.warning("⚠️ get_relevant_preferences failed: %s", exc)
            return "No user preferences available."

    def add_preference_background(
        self,
        user_id: str,
        text: str,
        metadata: Optional[Dict] = None,
    ) -> None:
        """
        Queue a preference write. Returns immediately — never blocks the response.
        Also invalidates the preference cache for this user so next search is fresh.
        """
        self.pref_queue.enqueue(user_id, text, metadata)
        asyncio.create_task(
            self._invalidate_pref_cache(user_id),
            name=f"invalidate_pref_{user_id}",
        )

    async def _invalidate_pref_cache(self, user_id: str) -> None:
        """Invalidate all preference search cache entries for a user."""
        if not self.cache.available:
            return
        try:
            pattern = f"pref_search:{user_id}:*"
            async for key in self.cache._redis.scan_iter(pattern):
                await self.cache.delete(key)
        except Exception as exc:
            logger.debug("⚠️ Pref cache invalidation error: %s", exc)

    async def get_all_preferences(self, user_id: str) -> List[Dict]:
        """Get all stored preferences for a user (for admin/display)."""
        mgr = await self._get_pref_manager(user_id)
        return await mgr.get_all()

    async def delete_preference(self, user_id: str, memory_id: str) -> bool:
        mgr = await self._get_pref_manager(user_id)
        result = await mgr.delete(memory_id)
        if result:
            await self._invalidate_pref_cache(user_id)
        return result


# ── Module-level singleton ───────────────────────────────────────────────────

memory_layer = MemoryLayer()


async def get_memory_layer() -> MemoryLayer:
    """Returns the initialized singleton. Safe to call multiple times."""
    if not memory_layer._initialized:
        await memory_layer.initialize()
    return memory_layer