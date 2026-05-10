"""
Mem0 Integration for Long-Term Preference Management
FIXED: Lowered threshold, added query expansion, improved retrieval, UPDATE support
"""

from importlib import metadata
import os
from importlib_metadata import metadata
from typing import List, Dict, Optional
from mem0 import Memory
from dotenv import load_dotenv
import logging
import hashlib
from datetime import datetime
from pymongo import MongoClient

load_dotenv()
logger = logging.getLogger(__name__)

class Mem0PreferenceManager:
    """Manages long-term user preferences using Mem0 with MongoDB Atlas backend"""
    
    def __init__(self, user_id: str, zero_token_mode: bool = False):
        self.user_id = user_id
        self.zero_token_mode = zero_token_mode

        MONGODB_URI = os.getenv("MONGODB_URI")
        if not MONGODB_URI:
            raise ValueError("MONGODB_URI not found in environment variables")
        
        config = {
            "vector_store": {
                "provider": "mongodb",
                "config": {
                    "mongo_uri": MONGODB_URI,
                    "db_name": "yusr_db",
                    "collection_name": "mem0_preferences",
                    "embedding_model_dims": 384
                    # ✅ index_name removed - not needed in latest Mem0
                }
            },
            "llm": {
                "provider": "groq",
                "config": {
                    "model": "llama-3.3-70b-versatile",
                    "temperature": 0.1,
                    "max_tokens": 2000,
                    "api_key": os.getenv("GROQ_API_KEY")
                }
            },
            "embedder": {
                "provider": "huggingface",
                "config": {
                    "model": "sentence-transformers/all-MiniLM-L6-v2"
                }
            }
        }
        
        try:
            self.memory = Memory.from_config(config)
            logger.info(f"✅ Mem0 initialized for user {user_id} with MongoDB Atlas")
        except Exception as e:
            logger.error(f"❌ Mem0 initialization failed: {e}")
            raise
        
        # TTL cache for get_relevant_preferences — avoids redundant vector searches
        # within the same session for the same query (TC44)
        self._search_cache: dict = {}
        self._CACHE_TTL: float = 300.0  # 5 minutes

    # Credential markers — preferences containing these are never stored
    _CREDENTIAL_MARKERS = [
        "password", "passwd", "pwd", "api key", "apikey", "api_key",
        "secret key", "secret", "token", "private key", "passphrase",
    ]
    # Patterns that indicate a credential value is being given (not just mentioned)
    _CREDENTIAL_PATTERNS = [
        r'(?:password|passwd|pwd)\s+(?:is|:)\s*\S+',
        r'api\s*key\s+(?:is|:)\s*\S+',
        r'secret\s+(?:is|:)\s*\S+',
        r'token\s+(?:is|:)\s*\S+',
        r'my\s+(?:password|passwd|pwd|pin)\s+is\s+\S+',
        r'gsk_[A-Za-z0-9]{20,}',
        r'sk-[A-Za-z0-9]{20,}',
    ]

    def _is_credential(self, text: str) -> bool:
        """Return True if text contains a credential that must not be stored."""
        import re
        t = text.lower()
        for pattern in self._CREDENTIAL_PATTERNS:
            if re.search(pattern, t, re.IGNORECASE):
                return True
        return False

    # def add_preference(self, preference: str, metadata: Optional[Dict] = None) -> str:
    #     """Store a user preference. Returns 'BLOCKED_CREDENTIAL' if credential detected.
    #     Falls back to zero-token (local embedding) write when Groq is rate-limited."""
    #     # TC57: block credentials before they reach Mem0
    #     if self._is_credential(preference):
    #         logger.warning(f"🚫 Credential blocked — not stored: '{preference[:60]}'")
    #         return "BLOCKED_CREDENTIAL"
    #     try:
    #         messages = [{"role": "user", "content": preference}]
    #         result = self.memory.add(
    #             messages=messages,
    #             user_id=self.user_id,
    #             metadata=metadata or {}
    #         )
    #         logger.info(f"✅ Stored preference for {self.user_id}: {preference[:50]}...")
    #         return result
    #     except Exception as e:
    #         error_str = str(e)
    #         # Groq rate limit (429) or token exhaustion — fall back to zero-token write
    #         if "429" in error_str or "rate_limit" in error_str.lower() or "tokens per day" in error_str.lower():
    #             logger.warning(
    #                 f"⚠️ Groq rate-limited — falling back to zero-token write for: '{preference[:50]}'"
    #             )
    #             return self.add_preference_zero_token(preference, metadata)
    #         logger.error(f"❌ Failed to store preference: {e}")
    #         return None

    def add_preference(self, preference: str, metadata: Optional[Dict] = None) -> str:
        """Store a user preference. Returns 'BLOCKED_CREDENTIAL' if credential detected.
        In zero_token_mode, skips Groq entirely and writes via local embeddings.
        Always checks for existing similar entries before writing (dedup guard)."""
        # Block credentials
        if self._is_credential(preference):
            logger.warning(f"🚫 Credential blocked — not stored: '{preference[:60]}'")
            return "BLOCKED_CREDENTIAL"

        # ── Dedup guard: skip if a sufficiently similar memory already exists ──
# Detects name/identity conflicts using smarter value extraction.
        # Extracts the actual name value after "name is", "name:" etc.
        # so "User's name is Layla" correctly extracts "layla" not "is".
        _IDENTITY_KEYS_TC59 = ["name", "age", "nationality", "profession", "email", "username"]
        _pi_lower_tc59 = preference.lower()
        _is_identity_claim_tc59 = any(k in _pi_lower_tc59 for k in _IDENTITY_KEYS_TC59)
        if _is_identity_claim_tc59:
            try:
                import re as _re_tc59b

                def _extract_identity_value(text: str, key: str) -> str:
                    """
                    Extract the actual value for an identity key from a sentence.
                    Handles patterns like:
                      "user's name is Layla"  → "layla"
                      "name: admin"           → "admin"
                      "name=shahd"            → "shahd"
                    Skips filler words like 'is', 'are', 'the', 'a', 'an'.
                    """
                    _FILLERS = {"is", "are", "was", "be", "the", "a", "an", "my", "your",
                                "their", "our", "has", "have", "had", "and", "or", "not"}
                    # Build and apply the pattern correctly — key must be first arg to search
                    pattern = rf"\b{_re_tc59b.escape(key)}\b[^a-z0-9]*([a-z0-9@._\-]+(?:\s+[a-z0-9@._\-]+)*)"
                    match = _re_tc59b.search(pattern, text, _re_tc59b.IGNORECASE)
                    if match:
                        tokens = match.group(1).split()
                        # Skip filler words, return first meaningful token
                        for tok in tokens:
                            if tok.lower() not in _FILLERS and len(tok) >= 2:
                                return tok.lower()
                    return ""
                # Smarter approach: use word-after-"is" pattern specifically
                def _extract_name_value(text: str) -> str:
                    """Extract name value: text after 'name is/:/=' ignoring fillers."""
                    _FILLERS = {"is", "are", "a", "an", "the", "my", "your"}
                    # Try "name is X" pattern
                    m = re.search(r"\bname\s+(?:is\s+)?([a-z][a-z0-9_\-]*)", text, re.IGNORECASE)
                    if m:
                        val = m.group(1).lower()
                        if val not in _FILLERS:
                            return val
                    return ""

                try:
                    _existing_all = self.get_all_preferences(use_cache=False)
                except Exception:
                    _existing_all = self._direct_mongo_search(preference, limit=10)
                for _emem in _existing_all:
                    _emem_text = _emem.get("memory", "").lower()
                    for _key in _IDENTITY_KEYS_TC59:
                        if _key not in _emem_text or _key not in _pi_lower_tc59:
                            continue
                        # Use smarter extraction for "name", fallback to regex for others
                        if _key == "name":
                            _stored_val = _extract_name_value(_emem_text)
                            _new_val = _extract_name_value(_pi_lower_tc59)
                        else:
                            import re
                            _sv = re.findall(
                                rf"\b{_key}\b[^a-z0-9]*([a-z0-9@._\-]+)", _emem_text
                            )
                            _nv = re.findall(
                                rf"\b{_key}\b[^a-z0-9]*([a-z0-9@._\-]+)", _pi_lower_tc59
                            )
                            _stored_val = _sv[0] if _sv else ""
                            _new_val = _nv[0] if _nv else ""

                        if (
                            _stored_val
                            and _new_val
                            and _stored_val != _new_val
                            # Don't block filler words being "different"
                            and _stored_val not in {"is", "are", "a", "an", "the", "my"}
                            and _new_val not in {"is", "are", "a", "an", "the", "my"}
                        ):
                            logger.warning(
                                f"🚫 TC59: Identity conflict blocked — "
                                f"stored name='{_stored_val}' | "
                                f"attempted name='{_new_val}' | "
                                f"full: '{_emem_text[:60]}' vs '{preference[:60]}'"
                            )
                            return "TC59_BLOCKED"
            except Exception as _tc59_err:
                logger.warning(f"⚠️ TC59 guard check failed — identity conflict check skipped: {_tc59_err}")
        # ── Dedup guard: skip if an identical or near-identical memory already exists ──
        # Uses word overlap to block true duplicates while allowing distinct memories.
        # The guard uses _direct_mongo_search (not vector search) so it works even
        # when the Atlas vector index hasn't propagated yet.
        try:
            _direct_existing = self._direct_mongo_search(preference, limit=3)
            if _direct_existing:
                best = _direct_existing[0]
                existing_text = best.get("memory", "").lower()
                new_text = preference.lower()
                # If the texts are almost identical token-for-token, skip
                _common = sum(1 for w in new_text.split() if w in existing_text.split())
                _total = max(len(new_text.split()), 1)
                _overlap = _common / _total
                if _overlap >= 0.85:
                    logger.info(
                        f"⏭️ Skipping near-duplicate (overlap={_overlap:.2f}): "
                        f"'{existing_text[:60]}'"
                    )
                    return None
        except Exception as _dedup_err:
            logger.debug(f"Dedup check failed (non-fatal): {_dedup_err}")

        # Zero-token mode: bypass Mem0's internal LLM entirely
        if self.zero_token_mode:
            logger.info(f"⚡ Zero-token mode — skipping Groq for: '{preference[:50]}'")
            return self.add_preference_zero_token(preference, metadata)

        try:
            messages = [{"role": "user", "content": preference}]
            result = self.memory.add(
                messages=messages,
                user_id=self.user_id,
                metadata=metadata or {}
            )

            # ── Detect silent Mem0 failure: daily token limit consumed ────────
            # When Groq hits the daily TPD limit, Mem0 logs the error internally
            # but does NOT raise — it returns an empty/null result and writes
            # nothing to MongoDB. We detect this by checking whether the result
            # contains any inserted document IDs. If not, fall back to zero-token.
            _wrote_something = False
            if result is not None:
                if isinstance(result, dict):
                    _results_list = result.get("results", [])
                    _wrote_something = bool(_results_list)
                elif isinstance(result, list):
                    _wrote_something = bool(result)
                else:
                    # Some Mem0 versions return a string ID on success
                    _wrote_something = bool(str(result).strip())

            if not _wrote_something:
                logger.warning(
                    f"⚠️ Mem0 returned empty result (likely daily token limit) — "
                    f"falling back to Mistral write for: '{preference[:50]}'"
                )
                return self._add_preference_mistral_fallback(preference, metadata)

            logger.info(f"✅ Stored preference for {self.user_id}: {preference[:50]}...")
            # Invalidate entire search cache for this user so next retrieval is fresh
            self._search_cache.clear()
            return result
        except Exception as e:
            error_str = str(e)
            if "429" in error_str or "rate_limit" in error_str.lower() or "tokens per day" in error_str.lower():
                logger.warning(
                    f"⚠️ Groq rate-limited — falling back to Mistral write for: '{preference[:50]}'"
                )
                return self._add_preference_mistral_fallback(preference, metadata)
            logger.error(f"❌ Failed to store preference: {e}")
            return None
        




    def _add_preference_mistral_fallback(self, preference: str, metadata: Optional[Dict] = None) -> str:
        """
        Fallback writer that uses Mistral as the LLM provider instead of Groq.
        This is used when Groq is rate-limited or exhausted. Mistral is a real LLM
        so Mem0's dedup/merge logic still fires — unlike zero-token which bypasses it.
        Falls back to zero-token only if Mistral is also unavailable.
        """
        MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")
        MISTRAL_MODEL = os.getenv("MISTRAL_MODEL", "mistral-medium-latest")
        if not MISTRAL_API_KEY:
            logger.warning("⚠️ MISTRAL_API_KEY not set — falling back to zero-token write")
            return self.add_preference_zero_token(preference, metadata)
        try:
            mistral_config = {
                "vector_store": {
                    "provider": "mongodb",
                    "config": {
                        "mongo_uri": os.getenv("MONGODB_URI"),
                        "db_name": "yusr_db",
                        "collection_name": "mem0_preferences",
                        "embedding_model_dims": 384
                    }
                },
                "llm": {
                    "provider": "mistral",
                    "config": {
                        "model": MISTRAL_MODEL,
                        "temperature": 0.1,
                        "max_tokens": 2000,
                        "api_key": MISTRAL_API_KEY
                    }
                },
                "embedder": {
                    "provider": "huggingface",
                    "config": {
                        "model": "sentence-transformers/all-MiniLM-L6-v2"
                    }
                }
            }
            mistral_memory = Memory.from_config(mistral_config)
            messages = [{"role": "user", "content": preference}]
            result = mistral_memory.add(
                messages=messages,
                user_id=self.user_id,
                metadata=metadata or {}
            )
            _wrote = False
            if result is not None:
                if isinstance(result, dict):
                    _wrote = bool(result.get("results", []))
                elif isinstance(result, list):
                    _wrote = bool(result)
                else:
                    _wrote = bool(str(result).strip())
            if not _wrote:
                logger.warning("⚠️ Mistral fallback also returned empty — using zero-token write")
                return self.add_preference_zero_token(preference, metadata)
            self._search_cache.clear()
            logger.info(f"✅ Stored via Mistral fallback for {self.user_id}: {preference[:50]}...")
            return result
        except Exception as e:
            logger.warning(f"⚠️ Mistral fallback failed ({e}) — using zero-token write")
            return self.add_preference_zero_token(preference, metadata)



 
    def add_preference_zero_token(self, preference: str, metadata: Optional[Dict] = None) -> str:
        """
        Store preference WITHOUT Mem0's internal LLM call.
        Uses local embeddings. 0 tokens per write!
        
        This bypasses Mem0's internal Groq call that normally costs ~951 tokens.
        """
        try:
            from sentence_transformers import SentenceTransformer
            
            # Get or create embedder (reuse across calls to avoid reloading)
            if not hasattr(self, '_zero_token_embedder'):
                self._zero_token_embedder = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
                logger.info("✅ Loaded zero-token embedder")
            
            # Generate embedding locally (0 tokens!)
            embedding = self._zero_token_embedder.encode([preference])[0].tolist()
            
            # Create Mem0-compatible document for direct insert
            doc = {
                "embedding": embedding,
                "payload": {
                    "data": preference,
                    "user_id": self.user_id,
                    "memory": preference,
                    "metadata": metadata or {},
                    "hash": hashlib.md5(preference.encode()).hexdigest(),
                    "created_at": datetime.now().isoformat(),
                    "updated_at": datetime.now().isoformat(),
                }
            }
            
            # Direct MongoDB connection (bypass Mem0's internal client)
            mongo_uri = os.getenv("MONGODB_URI")
            client = MongoClient(mongo_uri)
            db = client["yusr_db"]
            collection = db["mem0_preferences"]
            
            # Insert directly
            result = collection.insert_one(doc)
            client.close()

            # Invalidate entire search cache so next retrieval hits MongoDB fresh
            self._search_cache.clear()
            logger.info(f"✅ Stored (0 tokens): {preference[:50]}...")
            return str(result.inserted_id)
            
        except Exception as e:
            logger.error(f"❌ Zero-token storage failed: {e}")
            # Fallback to Mem0's method if direct insert fails
            logger.info("↻ Falling back to Mem0's add_preference (will cost tokens)")
            return self.add_preference(preference, metadata)
   
    #updated code
    def _direct_mongo_search(self, query: str, limit: int = 10, user_id: str = None) -> List[Dict]:
        """
        Direct MongoDB text search fallback used when vector search returns empty results.
        This handles the Atlas vector index propagation delay — data is in the collection
        but the vector index hasn't caught up yet. We query the raw payload.data field
        using regex/text matching so recently-written memories are always retrievable.
        Returns results in the same format as get_relevant_preferences.
        """
        try:
            from pymongo import MongoClient
            import re as _re
            uid = user_id or self.user_id
            client = MongoClient(os.getenv("MONGODB_URI"))
            db = client["yusr_db"]
            col = db["mem0_preferences"]

            # Build keyword list from query (strip stop words, split on spaces)
            _STOP = {"a", "an", "the", "is", "are", "was", "were", "be", "been", "do",
                     "does", "did", "will", "would", "could", "should", "have", "has",
                     "had", "to", "of", "in", "on", "at", "for", "with", "and", "or",
                     "not", "no", "my", "me", "i", "you", "we", "they", "it", "what",
                     "how", "when", "where", "who", "which", "this", "that", "am",
                     "ما", "هو", "هي", "في", "من", "على", "إلى", "هل", "أنا", "أنت"}
            keywords = [
                w.strip().lower() for w in _re.split(r"[\s\?\.،,;:!]+", query)
                if len(w.strip()) >= 3 and w.strip().lower() not in _STOP
            ]
            if not keywords:
                keywords = [query.strip().lower()[:30]]

            # Find all docs for this user in the collection
            # Mem0 stores user_id in payload.user_id for Mem0-managed docs
            # and in payload.payload.user_id for zero-token docs
            all_docs = list(col.find({
                "$or": [
                    {"payload.user_id": uid},
                    {"payload.payload.user_id": uid},
                ]
            }, limit=200))
            client.close()

            scored = []
            for doc in all_docs:
                payload = doc.get("payload", {})
                # Handle both Mem0-managed and zero-token doc structures
                if isinstance(payload.get("payload"), dict):
                    inner = payload["payload"]
                    memory_text = inner.get("memory") or inner.get("data") or ""
                    meta = inner.get("metadata") or {}
                else:
                    memory_text = payload.get("memory") or payload.get("data") or ""
                    meta = payload.get("metadata") or {}

                if not memory_text:
                    continue

                text_lower = memory_text.lower()
                # Score: count how many query keywords appear in the memory text
                hits = sum(1 for kw in keywords if kw in text_lower)
                if hits > 0:
                    score = min(0.5 + (hits / max(len(keywords), 1)) * 0.4, 0.89)
                    scored.append({
                        "memory": memory_text,
                        "score": score,
                        "metadata": meta,
                        "id": str(doc.get("_id", "")),
                        "_source": "direct_mongo_fallback",
                    })

            # Sort by score descending, return top results
            scored.sort(key=lambda x: x["score"], reverse=True)
            return scored[:limit]

        except Exception as e:
            logger.warning(f"⚠️ Direct MongoDB fallback search failed: {e}")
            return []

    def add_preference_safe(self, preference: str, metadata: Optional[Dict] = None,
                        similarity_threshold: float = 0.85) -> Optional[str]:
        """
        Store preference only if no similar one exists.
        Also blocks credential values and identity conflicts (TC57, TC59).
        Returns None if duplicate/conflict detected, 'BLOCKED_CREDENTIAL' if credential.
        """
        import re
        # TC57: block credentials
        if self._is_credential(preference):
            logger.warning(f"🚫 Credential blocked in safe store: '{preference[:60]}'")
            return None

        # TC59: identity conflict detection
        # If the new fact claims a name/identity, check it doesn't conflict
        _IDENTITY_PATTERNS = [r'\bmy name is\b', r'\bname is\b', r'\bi am\b', r'\bmy username is\b']
        is_identity_claim = any(re.search(p, preference, re.IGNORECASE) for p in _IDENTITY_PATTERNS)
        if is_identity_claim:
            existing_identities = self.get_relevant_preferences("what is my name", limit=5)
            if existing_identities:
                logger.warning(
                    f"🚫 Identity conflict rejected: tried to store '{preference[:60]}' "
                    f"but identity already exists: '{existing_identities[0].get('memory','')[:60]}'"
                )
                return None

        try:
            # Check for existing similar preferences
            similar_prefs = self.get_relevant_preferences(
                query=preference,
                limit=3,
                min_score=similarity_threshold
            )

            if similar_prefs:
                logger.info(f"⚠️ Similar preference exists, skipping: {similar_prefs[0].get('memory', '')[:50]}...")
                return None

            # No duplicates found, store it
            return self.add_preference(preference, metadata)

        except Exception as e:
            logger.error(f"❌ Failed safe preference storage: {e}")
            return None
        
    def update_preference(self, old_memory_id: str, new_preference: str, metadata: Optional[Dict] = None) -> bool:
        """
        Update an existing preference by ID
        
        Args:
            old_memory_id: ID of the memory to update
            new_preference: New preference text
            metadata: Updated metadata
        
        Returns:
            True if successful, False otherwise
        """
        try:
            # Delete old preference
            self.memory.delete(memory_id=old_memory_id)
            
            # Add new one
            result = self.add_preference(new_preference, metadata)
            
            logger.info(f"✅ Updated preference {old_memory_id} with new value")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to update preference: {e}")
            return False

    def find_and_update_preference(self, search_query: str, new_preference: str, metadata: Optional[Dict] = None, similarity_threshold: float = 0.7) -> bool:
        """
        Find similar preference and update it, or create new if not found
        
        Args:
            search_query: Query to find similar preference
            new_preference: New preference text to store
            metadata: Metadata for the preference
            similarity_threshold: Minimum similarity to consider a match
        
        Returns:
            True if updated/created, False otherwise
        """
        try:
            # Search for existing similar preference
            similar = self.get_relevant_preferences(
                query=search_query,
                limit=3,
                min_score=similarity_threshold
            )
            
            if similar and len(similar) > 0:
                # Update the most similar one
                old_memory_id = similar[0].get('id') or similar[0].get('memory_id')
                logger.info(f"🔄 Updating existing preference: {similar[0].get('memory', '')[:50]}...")
                return self.update_preference(old_memory_id, new_preference, metadata)
            else:
                # No similar preference found, create new
                logger.info(f"➕ Creating new preference: {new_preference[:50]}...")
                result = self.add_preference(new_preference, metadata)
                return result is not None
                
        except Exception as e:
            logger.error(f"❌ Failed to find and update preference: {e}")
            return False




    def get_relevant_preferences(
        self,
        query: str,
        limit: int = 10,
        min_score: float = 0.25
    ) -> List[Dict]:
        """
        Get preferences relevant to query with hybrid search for identity queries.
        Fixed: Identity queries (name, who am I) now work via exact match + vector search.
        Includes: TTL cache, retry logic, and fallback personal_info retrieval.
        """
        import time
        
        try:
            query_lower = query.lower().strip()
            
            # ── CACHE CHECK ─────────────────────────────────────────────────────
            import re as _re
            _query_normalized = _re.sub(r'[^\w\s]', '', query.lower().strip())[:80]
            cache_key = f"{self.user_id}:{_query_normalized}:{limit}:{min_score}"
            if hasattr(self, '_search_cache') and cache_key in self._search_cache:
                cached_result, cached_time = self._search_cache[cache_key]
                if time.time() - cached_time < self._CACHE_TTL:
                    logger.info(f"✅ Cache hit for query: {query[:50]}...")
                    return cached_result
            
            # ── STEP 1: IDENTITY QUERY DETECTION (EXPANDED) ─────────────────────
            identity_keywords = [
                "name", "who am i", "my name", "what's my name", "what is my name",
                "write my name", "type my name", "show my name", "display my name",
                "say my name", "tell me my name", "what is my username", "my username",
                "what is my user name", "user name", "what is my name called"
            ]
            is_identity_query = any(keyword in query_lower for keyword in identity_keywords)
            
            # Also detect if query contains "my name" even if not at start
            if not is_identity_query:
                is_identity_query = "my name" in query_lower
            
            # ── STEP 2: EXACT MATCH SEARCH for identity queries ─────────────────
            exact_matches = []
            if is_identity_query:
                logger.info(f"🔍 Identity query detected: '{query}' — using exact match")
                try:
                    all_prefs = self.get_all_preferences()
                    for pref in all_prefs:
                        category = pref.get('metadata', {}).get('category', '')
                        if category == 'personal_info':
                            memory_text = pref.get('memory', '').lower()
                            # Match ANY personal_info entry on identity queries.
                            # Previous hardcoded name list missed users whose names
                            # weren't in the list (e.g. "shahd", "layla", etc.)
                            name_related_keywords = ['name', 'username', 'user name', 'اسم', 'اسمي', 'يسمى']
                            if any(keyword in memory_text for keyword in name_related_keywords):
                                exact_matches.append({
                                    'memory': pref.get('memory', ''),
                                    'score': 1.0,
                                    'metadata': pref.get('metadata', {})
                                })
                                logger.info(f"  ✅ Exact match found: {pref.get('memory', '')[:60]}")
                except Exception as e:
                    logger.warning(f"⚠️ Exact match search failed: {e}")
            
            # ── STEP 3: VECTOR SEARCH with retry logic ─────────────────────────
            memories = None
            max_retries = 3
            retry_delay = 0.5
            
            for attempt in range(max_retries):
                try:
                    memories = self.memory.search(
                        query=query,
                        user_id=self.user_id,
                        limit=limit * 2
                    )
                    break
                except Exception as search_error:
                    error_msg = str(search_error).lower()
                    if any(keyword in error_msg for keyword in ["connection reset", "connection refused", "hostunreachable"]):
                        logger.warning(f"⚠️ MongoDB connection error (attempt {attempt+1}/{max_retries}): {search_error}")
                        if attempt < max_retries - 1:
                            time.sleep(retry_delay * (attempt + 1))
                            continue
                    raise

            # Normalise response format
            if isinstance(memories, dict):
                if 'results' in memories:
                    memories = memories['results']
                elif 'memories' in memories:
                    memories = memories['memories']
                elif 'memory' in memories:
                    memories = [memories]
                else:
                    memories = []

            if not isinstance(memories, list):
                memories = []

            # Filter by score threshold
            vector_matches = []
            for mem in memories:
                if mem is None:
                    continue
                score = mem.get('score', 0.0)
                memory_text = mem.get('memory', mem.get('text', 'Unknown'))

                if score >= min_score:
                    vector_matches.append(mem)
                    logger.info(f"  ✅ [Vector Score: {score:.2f}] {memory_text[:60]}")
                else:
                    logger.debug(f"  ⤷ Filtered out (score {score:.2f} < {min_score})")

            # ── STEP 4: MERGE AND DEDUPLICATE RESULTS ────────────────────────────
            formatted_exact = []
            for exact in exact_matches:
                if not any(v.get('memory') == exact.get('memory') for v in vector_matches):
                    formatted_exact.append({
                        'memory': exact.get('memory'),
                        'score': exact.get('score', 1.0),
                        'metadata': exact.get('metadata', {})
                    })
            
            # Combine: exact matches first (priority), then vector matches
            combined_results = formatted_exact + vector_matches
            
            # Sort by score descending
            combined_results.sort(key=lambda x: x.get('score', 0), reverse=True)

            # ── Recency tiebreak for same-topic conflicting memories ───────────
# MongoDB ObjectIds embed a timestamp in their first 4 bytes, making them
            # naturally time-ordered. When two results cover the same topic (e.g. both
            # mention "language"), we rank the one with the newer ObjectId higher.
            # This works for both Mem0-managed docs and zero-token docs.
            if len(combined_results) > 1:
                try:
                    from pymongo import MongoClient as _MC
                    from bson import ObjectId as _ObjId
                    _uid = self.user_id
                    _client2 = _MC(os.getenv("MONGODB_URI"))
                    _col2 = _client2["yusr_db"]["mem0_preferences"]
                    # Build map: memory_text_lower → insertion_timestamp (from ObjectId)
                    _oid_ts_map: dict = {}
                    _raw_docs = list(_col2.find(
                        {"$or": [
                            {"payload.user_id": _uid},
                            {"payload.payload.user_id": _uid},
                        ]},
                        {"_id": 1, "payload": 1}
                    ))
                    _client2.close()
                    for _doc in _raw_docs:
                        _p = _doc.get("payload", {})
                        _inner = _p.get("payload", _p) if isinstance(_p.get("payload"), dict) else _p
                        _mtext = (_inner.get("memory") or _inner.get("data") or "").strip().lower()
                        _oid = _doc.get("_id")
                        if _mtext and _oid and isinstance(_oid, _ObjId):
                            _oid_ts_map[_mtext] = _oid.generation_time.timestamp()

                    _TOPIC_KEYS = [
                        "language", "browser", "email", "wake", "alarm",
                        "theme", "mode", "dark", "font", "color",
                    ]
                    for _topic in _TOPIC_KEYS:
                        _cluster = [
                            r for r in combined_results
                            if _topic in r.get("memory", "").lower()
                        ]
                        if len(_cluster) <= 1:
                            continue
                        # Find the newest doc in this cluster
                        # Primary: metadata timestamp (set at write time, reliable for all doc types)
                        # Fallback: ObjectId generation time (only works for raw ObjectId _id fields)
                        def _ts_for_result(r):
                            _meta_ts = r.get("metadata", {}).get("timestamp", "")
                            if _meta_ts:
                                try:
                                    from datetime import datetime as _dt
                                    return _dt.fromisoformat(_meta_ts).timestamp()
                                except Exception:
                                    pass
                            return _oid_ts_map.get(r.get("memory", "").strip().lower(), 0.0)
                        _newest = max(_cluster, key=_ts_for_result)
                        _newest_text = _newest.get("memory", "")
                        for r in combined_results:
                            if (
                                _topic in r.get("memory", "").lower()
                                and r.get("memory", "") != _newest_text
                            ):
                                r["score"] = 0.01
                                r["_demoted"] = True
                        logger.info(
                            f"🕐 Recency tiebreak on topic '{_topic}': "
                            f"newest='{_newest_text[:50]}' wins"
                        )

                    combined_results.sort(key=lambda x: x.get("score", 0), reverse=True)
                    _active = [r for r in combined_results if not r.get("_demoted")]
                    if _active:
                        combined_results = _active + [r for r in combined_results if r.get("_demoted")]
                except Exception as _rec_err:
                    logger.debug(f"Recency tiebreak failed (non-fatal): {_rec_err}")
            
           
            # ── STEP 5: FALLBACK - Inject relevant personal_info when missing from results ──
            # Atlas vector index has a propagation delay after writes. This means:
            # - A doc written 2 seconds ago may NOT appear in vector search yet
            # - A doc written 10 seconds ago MAY appear in vector search
            # Both cases coexist in the same query result.
            # Strategy: ALWAYS run direct MongoDB search and MERGE with vector results.
            # This ensures recently-written docs (not yet indexed) are always included.
            # Dedup by memory text so the same doc doesn't appear twice.
            try:
                direct_results = self._direct_mongo_search(query, limit=limit)
                if direct_results:
                    # Merge: add any direct result whose text isn't already in combined
                    existing_texts = {
                        r.get("memory", "").strip().lower()
                        for r in combined_results
                    }
                    added = 0
                    for dr in direct_results:
                        dr_text = dr.get("memory", "").strip().lower()
                        if dr_text and dr_text not in existing_texts:
                            combined_results.append(dr)
                            existing_texts.add(dr_text)
                            added += 1
                    if added > 0:
                        logger.info(
                            f"✅ Direct MongoDB gap-fill added {added} result(s) "
                            f"not yet indexed by Atlas vector search"
                        )
                    elif not combined_results:
                        logger.info(
                            f"⚠️ Both vector and direct MongoDB returned 0 results "
                            f"for query: '{query[:60]}'"
                        )
            except Exception as _gap_err:
                logger.debug(f"Direct MongoDB gap-fill failed (non-fatal): {_gap_err}")
                if not combined_results:
                    logger.info(
                        f"⚠️ Vector search returned 0 results and gap-fill failed "
                        f"for query: '{query[:60]}'"
                    )

            # ── STEP 5: FALLBACK - Inject relevant personal_info when missing from results ──
            already_have_personal = any(
                r.get('metadata', {}).get('category') == 'personal_info'
                for r in combined_results
            )       
            if not already_have_personal:
                try:
                    _all_prefs_fallback = all_prefs if ('all_prefs' in locals() and all_prefs) else self.get_all_preferences()
                    _name_keywords = ['name', 'username', 'user name', 'اسم', 'اسمي']
                    _email_keywords = ['email', 'mail', 'إيميل', '@']
                    _is_name_query = any(kw in query_lower for kw in _name_keywords)
                    _is_email_query = any(kw in query_lower for kw in _email_keywords)
                    for pref in _all_prefs_fallback:
                        category = pref.get('metadata', {}).get('category', '')
                        if category != 'personal_info':
                            continue
                        mem_text = pref.get('memory', '').lower()
                        # When query is about name, only inject name-related personal_info
                        # When query is about email, only inject email-related personal_info
                        # When query is general, inject all personal_info
                        _mem_is_name = any(kw in mem_text for kw in _name_keywords)
                        _mem_is_email = any(kw in mem_text for kw in _email_keywords)
                        if _is_name_query and not _mem_is_name:
                            continue
                        if _is_email_query and not _mem_is_email:
                            continue
                        combined_results.insert(0, {
                            'memory': pref.get('memory', ''),
                            'score': 0.95,
                            'metadata': pref.get('metadata', {})
                        })
                        logger.info(f"  ✅ Injected personal info: {pref.get('memory', '')[:60]}")
                except Exception as e:
                    logger.debug(f"Fallback personal_info fetch failed: {e}")
            combined_results = combined_results[:limit]

            logger.info(
                f"✅ Found {len(combined_results)} relevant preferences "
                f"({len(formatted_exact)} exact, {len(vector_matches)} vector) "
                f"for query: {query[:50]}..."
            )

            # ── STORE IN CACHE ──────────────────────────────────────────────────
            if hasattr(self, '_search_cache'):
                self._search_cache[cache_key] = (combined_results, time.time())
                logger.info(f"💾 Cached result for key: {cache_key[:60]}")

            return combined_results

        except Exception as e:
            logger.error(f"❌ Failed to retrieve preferences: {e}", exc_info=True)
            return []

    def get_conversation_history(self, limit: int = 5) -> List:
        """Get recent conversation history"""
        try:
            all_memories = self.memory.get_all(user_id=self.user_id)
            
            conversations = []
            for mem in all_memories:
                if isinstance(mem, dict):
                    category = mem.get('metadata', {}).get('category', '')
                    if category == 'conversation_history':
                        conversations.append(mem)
            
            conversations.sort(
                key=lambda x: x.get('metadata', {}).get('timestamp', ''),
                reverse=True
            )
            
            logger.info(f"✅ Retrieved {len(conversations[:limit])} conversation histories")
            return conversations[:limit]
            
        except Exception as e:
            logger.error(f"❌ Failed to get conversation history: {e}")
            return []
    #here
    def get_all_preferences(self, use_cache: bool = True) -> List[Dict]:
            """
            Get ALL stored preferences for this user in proper dictionary format.
            Results are cached for _CACHE_TTL seconds to avoid repeated MongoDB roundtrips
            within the same request or short time window.

            Returns:
                List[Dict]: Each dict has structure:
                    {
                        "id": "uuid-string",
                        "memory": "The preference text",
                        "metadata": {
                            "category": "personal_info",
                            "timestamp": 1234567890.123,
                            "source": "user_input",
                            ...
                        }
                    }
            """
            import time

            _all_cache_key = f"{self.user_id}:__get_all__"

            # Check cache first
            if use_cache and hasattr(self, '_search_cache') and _all_cache_key in self._search_cache:
                cached_result, cached_time = self._search_cache[_all_cache_key]
                if time.time() - cached_time < self._CACHE_TTL:
                    logger.debug(f"✅ get_all_preferences cache hit for {self.user_id}")
                    return cached_result

            try:
                logger.info(f"📥 Fetching all preferences for user {self.user_id}")
                
                # Mem0's get_all() returns a dict with 'results' key
                response = self.memory.get_all(user_id=self.user_id)
                
                # Extract the actual memories from the response
                if isinstance(response, dict) and "results" in response:
                    raw_memories = response["results"]
                elif isinstance(response, list):
                    raw_memories = response
                else:
                    logger.warning(f"⚠️ Unexpected response format: {type(response)}")
                    return []
                
                # Transform each memory into the expected format
                formatted_memories = []
                for mem in raw_memories:
                    if isinstance(mem, dict):
                        # Mem0 returns: {"id": "...", "memory": "...", "metadata": {...}}
                        formatted_memories.append({
                            "id": mem.get("id", ""),
                            "memory": mem.get("memory", ""),
                            "metadata": mem.get("metadata", {})
                        })
                    elif isinstance(mem, str):
                        # Fallback for string-only memories (shouldn't happen with new Mem0)
                        logger.warning(f"⚠️ Got string-only memory: {mem[:50]}...")
                        formatted_memories.append({
                            "id": "",
                            "memory": mem,
                            "metadata": {"category": "general"}
                        })
                
                logger.info(f"✅ Retrieved {len(formatted_memories)} formatted preferences")

                # Store in cache
                if hasattr(self, '_search_cache'):
                    self._search_cache[_all_cache_key] = (formatted_memories, time.time())

                return formatted_memories
                
            except Exception as e:
                logger.error(f"❌ Failed to get all preferences: {e}", exc_info=True)
                return []


    def delete_preference(self, memory_id: str) -> bool:
        """Delete a specific preference"""
        try:
            self.memory.delete(memory_id=memory_id)
            # Invalidate get_all_preferences cache so next call reflects the deletion
            self._search_cache.pop(f"{self.user_id}:__get_all__", None)
            logger.info(f"✅ Deleted preference {memory_id}")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to delete preference: {e}")
            return False
    
    def format_for_llm(self, preferences: List) -> str:
        """Format preferences for injection into LLM prompt"""
        if not preferences:
            return "No stored user preferences."
        
        formatted = "# USER PREFERENCES (FROM PREVIOUS SESSIONS)\n"
        for i, pref in enumerate(preferences, 1):
            if isinstance(pref, str):
                memory_text = pref
            elif isinstance(pref, dict):
                memory_text = pref.get('memory', pref.get('text', pref.get('content', 'Unknown')))
            else:
                memory_text = str(pref)
            formatted += f"{i}. {memory_text}\n"
        
        return formatted


# ============================================================================
# FACTORY FUNCTION (OUTSIDE THE CLASS)
# ============================================================================

# Factory function
_preference_managers: Dict[str, Mem0PreferenceManager] = {}

def get_preference_manager(user_id: str) -> Mem0PreferenceManager:
    """Get or create preference manager for user"""
    if user_id not in _preference_managers:
        mgr = Mem0PreferenceManager(user_id)
        _preference_managers[user_id] = mgr
        # Pre-warm the personal_info cache so identity queries on new sessions
        # immediately hit the cache instead of doing a cold vector search.
        try:
            _warm = mgr.get_all_preferences()
            _personal = [
                {'memory': p.get('memory', ''), 'score': 1.0, 'metadata': p.get('metadata', {})}
                for p in _warm if p.get('metadata', {}).get('category') == 'personal_info'
            ]
            if _personal:
                import time as _t
                import re as _re
                # Cover a broad set of identity and personal-info query variations
                # so that any new session hits the pre-warmed cache immediately
                _warm_queries = [
                    "what is my name", "my name", "who am i", "do you remember my name",
                    "what is my name do u remeber", "what is my name do you remember",
                    "remember my name", "my name is", "tell me my name", "say my name",
                    "what is my username", "my username", "user name", "what am i called",
                    "what is my email", "my email", "my email address",
                    "my home address", "my address", "where do i live",
                    "my preferences", "what do i prefer", "what browser do i use",
                    "what units do i use", "metric or imperial", "my settings",
                ]
                for _warm_query in _warm_queries:
                    _norm = _re.sub(r'[^\w\s]', '', _warm_query.lower().strip())[:80]
                    _cache_key = f"{user_id}:{_norm}:10:0.25"
                    mgr._search_cache[_cache_key] = (_personal, _t.time())
                logger.info(f"✅ Pre-warmed personal_info cache for {user_id} ({len(_personal)} items)")
        except Exception as _e:
            logger.debug(f"Cache pre-warm failed (non-fatal): {_e}")
    return _preference_managers[user_id]