"""
Mem0 Integration for Long-Term Preference Management
FIXED: Lowered threshold, added query expansion, improved retrieval, UPDATE support
"""

import os
from typing import List, Dict, Optional
from mem0 import Memory
from dotenv import load_dotenv
import logging
import time
from tenacity import retry, stop_after_attempt, wait_exponential

load_dotenv()
logger = logging.getLogger(__name__)

class Mem0PreferenceManager:
    """Manages long-term user preferences using Mem0 with MongoDB Atlas backend"""
    
    def __init__(self, user_id: str):
        self.user_id = user_id
        
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
            self._search_cache = {}
            self._CACHE_TTL = 300  # 5 minutes
            logger.info("✅ Search cache enabled (TTL: 300s)")
        except Exception as e:
            logger.error(f"❌ Mem0 initialization failed: {e}")
            raise

    def add_preference(self, preference: str, metadata: Optional[Dict] = None) -> str:
        """Store a user preference"""
        try:
            messages = [{"role": "user", "content": preference}]
            result = self.memory.add(
                messages=messages,
                user_id=self.user_id,
                metadata=metadata or {}
            )
            logger.info(f"✅ Stored preference for {self.user_id}: {preference[:50]}...")
            return result
        except Exception as e:
            logger.error(f"❌ Failed to store preference: {e}")
            return None


    def add_preference_zero_token(self, preference: str, metadata: Optional[Dict] = None) -> str:
        """
        Store preference WITHOUT Mem0's internal LLM call.
        Uses local embeddings. 0 tokens per write!
        
        This is the key optimization for token exhaustion.
        """
        try:
            from sentence_transformers import SentenceTransformer
            import hashlib
            from datetime import datetime
            
            # Get or create embedder (reuse across calls)
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
            
            # Insert directly into MongoDB (bypass Mem0's LLM)
            result = self.memory._collection.insert_one(doc)
            logger.info(f"✅ Stored (0 tokens): {preference[:50]}...")
            return str(result.inserted_id)
            
        except Exception as e:
            logger.error(f"❌ Zero-token storage failed, falling back to Mem0: {e}")
            # Fallback to Mem0's method if direct insert fails
            return self.add_preference(preference, metadata)


    def add_preference_safe(self, preference: str, metadata: Optional[Dict] = None, 
                        similarity_threshold: float = 0.85) -> Optional[str]:
        """
        Store preference only if no similar one exists
        
        Args:
            preference: Preference text to store
            metadata: Optional metadata
            similarity_threshold: Minimum similarity to consider duplicate (0.85 = 85% similar)
        
        Returns:
            Memory ID if stored, None if duplicate found or error
        """
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


    #updated function
    def get_relevant_preferences(
        self,
        query: str,
        limit: int = 10,
        min_score: float = 0.25
    ) -> List[Dict]:
        """
        Get preferences relevant to query with hybrid search for identity queries.
        Fixed: Identity queries (name, who am I) now work via exact match + vector search.
        Includes TTL cache for repeated queries to reduce latency.
        """
        import time
        
        try:
            query_lower = query.lower().strip()
            
            # ── CACHE CHECK ─────────────────────────────────────────────────────
            # Check cache first for identical queries (saves vector search time)
            cache_key = f"{self.user_id}:{query[:80]}:{limit}:{min_score}"
            if hasattr(self, '_search_cache') and cache_key in self._search_cache:
                cached_result, cached_time = self._search_cache[cache_key]
                if time.time() - cached_time < self._CACHE_TTL:
                    logger.info(f"✅ Cache hit for query: {query[:50]}...")
                    return cached_result
            
            # ── STEP 1: IDENTITY QUERY DETECTION ─────────────────────────────────
            # These queries need exact match on personal_info category
            identity_keywords = ["name", "who am i", "my name", "what's my name", "what is my name"]
            is_identity_query = any(keyword in query_lower for keyword in identity_keywords)
            
            # ── STEP 2: EXACT MATCH SEARCH for identity queries ─────────────────
            exact_matches = []
            if is_identity_query:
                logger.info(f"🔍 Identity query detected: '{query}' — using exact match")
                try:
                    # Get all personal_info memories and filter client-side for exact match
                    all_prefs = self.get_all_preferences()
                    for pref in all_prefs:
                        category = pref.get('metadata', {}).get('category', '')
                        if category == 'personal_info':
                            memory_text = pref.get('memory', '').lower()
                            # Check for name-related content
                            if any(keyword in memory_text for keyword in ['name', 'سارة', 'sara']):
                                exact_matches.append({
                                    'memory': pref.get('memory', ''),
                                    'score': 1.0,  # Give perfect score for exact matches
                                    'metadata': pref.get('metadata', {})
                                })
                                logger.info(f"  ✅ Exact match found: {pref.get('memory', '')[:60]}")
                except Exception as e:
                    logger.warning(f"⚠️ Exact match search failed: {e}")
            
            # ── STEP 3: VECTOR SEARCH (normal) ──────────────────────────────────
            # ── RETRY LOGIC for MongoDB connection issues ──────────────────────
            memories = None
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    memories = self.memory.search(
                        query=query,
                        user_id=self.user_id,
                        limit=limit * 2
                    )
                    break
                except Exception as search_error:
                    if "Connection reset" in str(search_error) or "Connection refused" in str(search_error):
                        logger.warning(f"⚠️ MongoDB connection error (attempt {attempt+1}/{max_retries}): {search_error}")
                        if attempt < max_retries - 1:
                            time.sleep(0.5 * (attempt + 1))  # Exponential backoff
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
            # Convert exact matches to same format as vector matches
            formatted_exact = []
            for exact in exact_matches:
                # Avoid duplicates
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
            combined_results = combined_results[:limit]

            logger.info(
                f"✅ Found {len(combined_results)} relevant preferences "
                f"({len(formatted_exact)} exact, {len(vector_matches)} vector) "
                f"for query: {query[:50]}..."
            )

            # ── STORE IN CACHE ──────────────────────────────────────────────────
            # Cache the result for future identical queries
            if hasattr(self, '_search_cache'):
                self._search_cache[cache_key] = (combined_results, time.time())
                logger.debug(f"💾 Cached result for query: {query[:50]}...")

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
    def get_all_preferences(self) -> List[Dict]:
            """
            Get ALL stored preferences for this user in proper dictionary format.
            
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
                return formatted_memories
                
            except Exception as e:
                logger.error(f"❌ Failed to get all preferences: {e}", exc_info=True)
                return []


    def delete_preference(self, memory_id: str) -> bool:
        """Delete a specific preference"""
        try:
            self.memory.delete(memory_id=memory_id)
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
        _preference_managers[user_id] = Mem0PreferenceManager(user_id)
    return _preference_managers[user_id]