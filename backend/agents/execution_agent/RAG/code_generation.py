# # %%
# %pip install sentence-transformers chromadb tqdm
# %pip install anthropic  dataclasses


# # %%
# %pip install openai torch pandas numpy

# # %%
# %pip install google-generativeai


# # %%
# %pip install groq


# %%
# ============================================================================
# COMPLETE RAG PIPELINE - PART 2: LLM INTEGRATION & CODE GENERATION
# ============================================================================
# This notebook integrates the vector database with LLMs for code generation

import json
import os
from pathlib import Path
from typing import List, Dict, Any, Optional
import chromadb
from sentence_transformers import SentenceTransformer
import openai  # Can also use: anthropic, huggingface, ollama
from anthropic import Anthropic
import torch
from dataclasses import dataclass
from datetime import datetime

from enum import Enum
import requests

class RetrievalMode(Enum):
    API = "api"
    LOCAL = "local"



# %%

# ============================================================================
# CELL 1: Configuration
# ============================================================================

@dataclass
class RAGConfig:
    """Configuration for RAG system"""
    library_name: str = "pyautogui"
    use_rag: bool = False
    # Paths
    #backend\agents\execution_agent\RAG\modelss\pyautogui
    #backend\agents\execution_agent\RAG\vectordbb\pyautogui
    vectordb_dir: Path = Path(r"D:\YUSR\backend\agents\execution_agent\RAG\vectordb")
    models_dir: Path = Path(r"D:\YUSR\backend\agents\execution_agent\RAG\models\pyautogui")
    
    # Retrieval settings
    #top k is 7 but we made  6 as to be eq to the three trials devision
    top_k: int =2  # Number of similar chunks to retrieve
    max_retrieval: int = 6  # ← ADD THIS: Total contexts to retrieve (for 3 retries)

    similarity_threshold: float = 0.3  # Minimum similarity score
    similarity_threshold: float = 0.3  # Minimum similarity scor
    retrieval_mode: str = "local"
    
    
    
    # LLM settings
    llm_provider: str = "groq"  # Options: "anthropic", "openai", "ollama", "huggingface"
    llm_model: str = "moonshotai/kimi-k2-instruct-0905"  # or "gpt-4", "gpt-3.5-turbo"
    temperature: float = 0.4  # Lower = more deterministic
    max_tokens: int = 1024
    
    # Code generation settings
    include_context: bool = True
    include_examples: bool = True
    max_context_length: int = 3000  # characters
    
    def __post_init__(self):
        if self.vectordb_dir is None:
            self.vectordb_dir = Path(f"vectordbbb/{self.library_name}")
        if self.models_dir is None:
            self.models_dir = Path(f"modelsss/{self.library_name}")

config = RAGConfig()
print(f"RAG Configuration for: {config.library_name}")
print(f"LLM Provider: {config.llm_provider}")
print(f"Model: {config.llm_model}")



# %%

# ============================================================================
# CELL 2: Vector Database Interface
# ============================================================================

from  agents.execution_agent.RAG.module_selector import ModuleSelector

class VectorDBInterface:
    """Interface to interact with the vector database"""
    
    def __init__(self, config: RAGConfig, mode: RetrievalMode = RetrievalMode.LOCAL):
        self.config = config
        self.mode = mode  # ← ADD THIS
        self.client = None
        self.collection = None
        self.embedding_model = None
        if self.mode == RetrievalMode.LOCAL:  # ← CHANGE THIS
            self._initialize_local()
        
    def _initialize_local(self):
        """Initialize connection to vector database"""
        print("Connecting to vector database...")
        
        # Load ChromaDB
        self.client = chromadb.PersistentClient(
          path=str(self.config.vectordb_dir / self.config.library_name)
        )
        
        # Get collection
        self.collection = self.client.get_collection(
            name=f"{self.config.library_name}_embeddings"
        )
        
        print(f"Connected to collection: {self.collection.name}")
        print(f"Total documents: {self.collection.count()}")
        
        # Load embedding model
        model_path = self.config.models_dir / "embedding_model"
        self.embedding_model = SentenceTransformer(str(model_path))
        print(f"Embedding model loaded")
        
        
    def _search_api(self, query: str, n_results: int = None) -> Dict:
        
        """Search using API endpoint"""
        if n_results is None:
            n_results = self.config.top_k
        
        payload = {
            "query": query,
            "library_name": self.config.library_name,
            "top_k": n_results,
            "similarity_threshold": self.config.similarity_threshold
        }
        
        response = requests.post(
            "http://44.223.42.183:8000/retrieve",
            json=payload,
            timeout=30
        )
        print("the api response is",response)
        response.raise_for_status()
        return response.json()
    
    def _search_local(self, query: str, n_results: int = None) -> Dict:
        """Search for relevant documents"""
        if n_results is None:
            n_results = self.config.top_k
        
        # Generate query embedding
        query_embedding = self.embedding_model.encode([query])[0]
        
        # Search in vector database
        results = self.collection.query(
            query_embeddings=[query_embedding.tolist()],
            n_results=n_results
        )
        
        return results
    
    def get_relevant_context(self, query: str,max_results: int = None) -> List[Dict]:
        """Get relevant context for a query"""
        if max_results is None:
        # Retrieve MORE contexts for retries (3 attempts × top_k)
            max_results = self.config.max_retrieval  # ← CHANGE: Get 15 instead of 5
            
        if self.mode == RetrievalMode.API:
            api_response = self._search_api(query, n_results=max_results)
            return api_response.get('contexts', [])

        results = self._search_local(query, n_results=max_results)
        
        contexts = []
        for i, (doc, metadata, distance) in enumerate(zip(
            results['documents'][0],
            results['metadatas'][0],
            results['distances'][0]
        )):
            # Filter by similarity threshold
            similarity = 1 - distance
            if similarity >= self.config.similarity_threshold:
                contexts.append({
                    'rank': i + 1,
                    'content': doc,
                    'metadata': metadata,
                    'similarity': similarity,
                    'distance': distance
                })
        
        return contexts



# %%

# ============================================================================
# CELL 3: LLM Interface (Multi-Provider)
# ============================================================================

class LLMInterface:
    """Interface to interact with different LLM providers"""
    
    def __init__(self, config: RAGConfig):
        self.config = config
        self.client = None
        self._initialize_client()
    
    def _initialize_client(self):
        """Initialize LLM client based on provider"""
        if self.config.llm_provider == "anthropic":
            api_key = os.getenv("ANTHROPIC_API_KEY")
            if not api_key:
                print("⚠️  Warning: ANTHROPIC_API_KEY not found in environment")
            self.client = Anthropic(api_key=api_key) if api_key else None
            
        elif self.config.llm_provider == "openai":
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                print("⚠️  Warning: OPENAI_API_KEY not found in environment")
            openai.api_key = api_key
            self.client = "openai"  # Use openai module directly
            
        elif self.config.llm_provider == "ollama":
            # For local Ollama instance
            self.client = "ollama"
            print("Using local Ollama instance")
            
        elif self.config.llm_provider == "gemini":
            self.client = "gemini"
            api_key = os.getenv("GOOGLE_API_KEY")
            if not api_key:
                 print("⚠️  Warning: GOOGLE_API_KEY not found in environment")
                 self.client = None
            else:
                import google.generativeai as genai
                genai.configure(api_key=api_key)
                self.client = genai
        elif self.config.llm_provider == "groq":
            api_key = os.getenv("GROQ_API_KEY")
            if not api_key:
                 print("⚠️  Warning: GROQ_API_KEY not found in environment")
                 self.client = None
            else:
                from groq import Groq
                self.client = Groq(api_key=api_key)
        else:
            raise ValueError(f"Unsupported LLM provider: {self.config.llm_provider}")
    
    def generate(self, prompt: str, system_prompt: str = None) -> str:
        """Generate response from LLM"""
        
        if self.config.llm_provider == "anthropic":
            return self._generate_anthropic(prompt, system_prompt)
        elif self.config.llm_provider == "openai":
            return self._generate_openai(prompt, system_prompt)
        elif self.config.llm_provider == "ollama":
            return self._generate_ollama(prompt, system_prompt)
        elif self.config.llm_provider == "gemini":
            return self._generate_gemini(prompt, system_prompt)
        elif self.config.llm_provider == "groq":
            return self._generate_groq(prompt, system_prompt)

    
    def _generate_anthropic(self, prompt: str, system_prompt: str = None) -> str:
        """Generate using Anthropic Claude"""
        if not self.client:
            return "Error: Anthropic client not initialized. Please set ANTHROPIC_API_KEY."
        
        messages = [{"role": "user", "content": prompt}]
        
        response = self.client.messages.create(
            model=self.config.llm_model,
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
            system=system_prompt if system_prompt else "",
            messages=messages
        )
        
        return response.content[0].text
    
    def _generate_openai(self, prompt: str, system_prompt: str = None) -> str:
        """Generate using OpenAI GPT"""
        messages = []
        
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        
        messages.append({"role": "user", "content": prompt})
        
        response = openai.ChatCompletion.create(
            model=self.config.llm_model,
            messages=messages,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens
        )
        
        return response.choices[0].message.content
    
    def _generate_ollama(self, prompt: str, system_prompt: str = None) -> str:
        """Generate using local Ollama"""
        import requests
        
        url = "http://localhost:11434/api/generate"
        
        full_prompt = prompt
        if system_prompt:
            full_prompt = f"{system_prompt}\n\n{prompt}"
        
        data = {
            "model": self.config.llm_model,  # e.g., "codellama", "llama2"
            "prompt": full_prompt,
            "stream": False
        }
        
        response = requests.post(url, json=data)
        return response.json()['response']
    
    def _generate_gemini(self, prompt: str, system_prompt: str = None) -> str:
        """Generate using Google Gemini 1.5 Flash"""
        if not self.client:
            return "Error: Gemini client not initialized. Please set GOOGLE_API_KEY."
        
        model = self.client.GenerativeModel(self.config.llm_model)
        
        if system_prompt:
            full_prompt = f"{system_prompt}\n\n{prompt}"
        else:
            full_prompt = prompt
        
        response = model.generate_content(
            full_prompt,
            generation_config={
                "temperature": self.config.temperature,
                "max_output_tokens": self.config.max_tokens,
            }
    )       
        
        return response.text
    
    def _generate_groq(self, prompt: str, system_prompt: str = None) -> str:
        
        if not self.client:
            return "Error: Groq client not initialized. Please set GROQ_API_KEY."

        messages = []

        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        messages.append({"role": "user", "content": prompt})

        response = self.client.chat.completions.create(
            model=self.config.llm_model,
            messages=messages,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
        )

        return response.choices[0].message.content





# %%

# ============================================================================
# CELL 4: RAG System - Core
# ============================================================================

class RAGSystem:
    """Complete RAG system for code generation"""
    
    def __init__(self, config: RAGConfig, mode: RetrievalMode = RetrievalMode.LOCAL):
        self.config = config
        self.vectordb = None
        self.mode = mode  # ← ADD THIS LINE
        self.llm = LLMInterface(config)
        self.module_selector = ModuleSelector()  # ← ADD THIS LINE
        self.conversation_history = []
        
    def initialize(self):
        """Initialize RAG system"""
        print("Initializing RAG System...")
                # ✅ Step 2: Initialize Vector Database Interface
        # YOU ALREADY HAVE VectorDBInterface - just use it!
        self.vectordb = VectorDBInterface(self.config,mode=self.mode)
        if self.mode == RetrievalMode.LOCAL:  # ← ADD THIS CHECK
            self.vectordb._initialize_local()
        print("RAG System ready!")

    def generate_code(self, user_query: str, cache_key: str = None,
                    include_explanation: bool = True,
                    conversation_context: List[Dict] = None,
                    start_context_index: int = 0,
                    num_contexts: int = None,
                    use_rag: bool = None, screen_state: Dict = None ) -> Dict:  # ← ADD use_rag parameter
        """
        Generate code based on user query using RAG
        
        Args:
            user_query: The user's request/question
            cache_key: Unique identifier for the query (used for caching)
            include_explanation: Whether to include explanation
            conversation_context: Previous conversation for context
            start_context_index: Which context to start from (for retries)
            num_contexts: How many contexts to use (default: top_k)
            use_rag: Override to enable/disable RAG (None = use config default)
        """
        if num_contexts is None:
            num_contexts = self.config.top_k
        
        # Determine if RAG should be used
        if use_rag is None:
            use_rag = self.config.use_rag  # Use config default
        
        print(f"\n{'='*80}")
        print(f"Query: {user_query}")
        print(f"Cache Key: {cache_key}")
        print(f"RAG Enabled: {use_rag}")
        print(f"{'='*80}")
        
        # ============================================================================
        # STEP 1: Retrieve contexts (ONLY if RAG is enabled)
        # ============================================================================
        contexts = []
        start_context_index = 0
        end_index = 0
        
        if use_rag:
            print(f"\n[1/3] 🔍 RAG ENABLED - Retrieving contexts...")
            
            cache_exists = hasattr(self, '_cached_contexts') and hasattr(self, '_cached_query')
            
            if not cache_exists or self._cached_query != cache_key:
                print(f"       Requesting max_retrieval={self.config.max_retrieval} contexts")
                
                self._cached_contexts = self.vectordb.get_relevant_context(
                    cache_key, 
                    max_results=self.config.max_retrieval
                ) or []
                
                self._cached_query = cache_key
                self._last_context_index = None
                
                print(f"       ✅ Retrieved {len(self._cached_contexts)} contexts from DB")
                
                if self._cached_contexts:
                    print(f"\n       📊 All Retrieved Contexts:")
                    for idx, ctx in enumerate(self._cached_contexts):
                        print(f"          [{idx}] Similarity: {ctx['similarity']:.2%} | {ctx['content'][:60]}...")
            else:
                print(f"       ♻️  Using CACHED contexts ({len(self._cached_contexts)} total)")
            
            # Select context window
            print(f"\n[2/3] 🎯 Selecting context window...")
            print(f"       start_index={start_context_index}, num_contexts={num_contexts}")
            
            if len(self._cached_contexts) == 0:
                print(f"       ⚠️  No relevant contexts found")
                contexts = []
            else:
                if start_context_index >= len(self._cached_contexts):
                    print(f"       ⚠️  Adjusting to last available window")
                    start_context_index = max(0, len(self._cached_contexts) - num_contexts)
                
                end_index = min(start_context_index + num_contexts, len(self._cached_contexts))
                contexts = self._cached_contexts[start_context_index:end_index]
                
                print(f"       📌 Selected Window: [{start_context_index}:{end_index}]")
                print(f"       🔍 Contexts for THIS attempt:")
                
                for i, ctx in enumerate(contexts):
                    global_idx = start_context_index + i
                    print(f"          [{global_idx}] Similarity: {ctx['similarity']:.2%} | {ctx['content'][:60]}...")
        else:
            print(f"\n[1/3] 🚫 RAG DISABLED - Skipping context retrieval")
            print(f"       Will generate code using LLM's general knowledge only")
            contexts = []
        
        # ============================================================================
        # STEP 2: Build prompt (with or without contexts)
        # ============================================================================
        print(f"\n[{'3/3' if use_rag else '2/3'}] 🏗️  Building prompt...")
        if use_rag:
            print(f"       Including {len(contexts)} RAG contexts in prompt")
        else:
            print(f"       Using zero-shot prompt (no RAG contexts)")
            
        print(f"screen_state: {screen_state}")

        
        prompt = self._build_prompt(user_query, contexts, conversation_context, screen_state)

        # ============================================================================
        # STEP 2.5: ENRICH PROMPT WITH MODULE SELECTOR
        # ============================================================================
        print(f"\n[ENRICH] 🎯 Applying module selector guidance...")
        enriched_prompt = self.module_selector.enrich_prompt(prompt)

        if enriched_prompt != prompt:
            print(f"       ✅ Module override injected")
        else:
            print(f"       ➡️  No module override needed")

        print("-" * 80)
        print("📝 PROMPT PREVIEW:")
        print(enriched_prompt[:500] + "..." if len(enriched_prompt) > 500 else enriched_prompt)
        print("-" * 80)

        # ============================================================================
        # STEP 3: Generate code
        # ============================================================================
        print(f"\n[{'4/3' if use_rag else '3/3'}] 🤖 Generating code with LLM...")
        response = self.llm.generate(
            prompt=enriched_prompt,  # ← USE enriched_prompt INSTEAD OF prompt
            system_prompt=self._get_system_prompt()
        )
        
        # Parse response
        result = self._parse_response(response, contexts)
        
        print(f"\n[{'5/3' if use_rag else '4/3'}] ✅ Code generation complete")
        print(f"       Generated {len(result['code'])} characters of code")
        print("THE CODE BLOCK ", result['code'])
        print(f"       RAG contexts used: {result['contexts_used']}")
        if result['contexts_used'] > 0:
            print(f"       Top similarity: {result['top_similarity']:.2%}")
        print("-" * 80)
        
        # Store in conversation history
        self.conversation_history.append({
            'query': user_query,
            'cache_key': cache_key,
            'response': result,
            'context_indices': (start_context_index, end_index),
            'contexts_used': len(contexts),
            'rag_enabled': use_rag,
            'timestamp': datetime.now().isoformat()
        })
        
        return result   
    
    def _build_prompt(self, query: str, contexts: List[Dict], screen_state: Dict = None,
                     conversation_context: List[Dict] = None) -> str:
        """Build the prompt for the LLM"""
        
        prompt_parts = []
        
         # Add screen state if available
        if screen_state:
                    prompt_parts.append(f"""
            CURRENT SCREEN STATE:
            - Active Window: {screen_state['active_window']}
            - Process: {screen_state['process']}
            - Visible Controls: {', '.join(screen_state.get('controls', []))}

            ADAPTIVE BEHAVIOR REQUIRED:
            1. If unexpected popup detected: Close it first (Alt+F4 or Escape)
            2. If target app not active: Activate it (Win key + search)
            3. If controls not visible: Wait up to 5 seconds for them to appear
            4. Try multiple approaches if primary fails
            """)
        
        # # Add conversation context if available
        if conversation_context and isinstance(conversation_context, list):
            prompt_parts.append("## Previous Conversation:")
            for msg in conversation_context[-3:]:  # Last 3 messages
                prompt_parts.append(f"User: {msg.get('query', '')}")
                if 'code' in msg.get('response', {}):
                    prompt_parts.append(f"Assistant: {msg['response']['code'][:200]}...")
            prompt_parts.append("")
        
        if contexts: 
        # Add retrieved context
            prompt_parts.append(f"## Relevant {self.config.library_name} Documentation and Examples:")
            prompt_parts.append("")
            
            total_length = 0
            for i, ctx in enumerate(contexts):
                content = ctx['content']
                
                # Truncate if needed
                if total_length + len(content) > self.config.max_context_length:
                    content = content[:self.config.max_context_length - total_length]
                
                prompt_parts.append(f"### Reference {i+1} (Relevance: {ctx['similarity']:.0%}):")
                prompt_parts.append(content)
                prompt_parts.append("")
                
                total_length += len(content)
                
                if total_length >= self.config.max_context_length:
                    break
        # else:
        #     prompt_parts.append("## Note: No similar examples found, using general knowledge")
        
        # Add user query
        # prompt_parts.append("## User Request:")
        # prompt_parts.append(query)
        # prompt_parts.append("")
        
        # # Add instructions
        # prompt_parts.append("## Instructions:")
        # prompt_parts.append(f"Based on the above {self.config.library_name} documentation and examples, generate:")
        # prompt_parts.append("1. Complete, working Python code that addresses the user's request")
        # prompt_parts.append("2. Include necessary imports")
        # prompt_parts.append("3. Add helpful comments")
        # prompt_parts.append("4. Follow best practices and patterns shown in the examples")
        # prompt_parts.append("")
        # prompt_parts.append("IMPORTANT - Success/Failure Indicators:")  # ← ADD THIS
        # prompt_parts.append("- Your code MUST print success indicators when it completes successfully")
        # prompt_parts.append("- Use keywords: EXECUTION_SUCCESS, SUCCESS, COMPLETED, or DONE")
        # prompt_parts.append("- Example: print('SUCCESS: Window opened and visible')")
        # prompt_parts.append("- For errors, print 'FAILED:' or 'ERROR:' with details")
        # prompt_parts.append("- Always use try-except blocks for risky operations")
        # prompt_parts.append("")
        # prompt_parts.append("Format your response as:")
        # prompt_parts.append("```python")
        # prompt_parts.append("# Your code here")
        # prompt_parts.append("```")
        prompt_parts.append("""Generate automation code to perform the following task:

Task Description:""")
        prompt_parts.append(query)
        prompt_parts.append("""


Requirements

Execute the task exactly as described, without adding extra steps.
Prefer the simplest and most reliable execution method.
If the primary method fails, automatically adapt and try alternative approaches.
Do not assume success—ensure the task is actually performed.
The code must be suitable for use within a multi-agent automation system.
Return only the generated code and necessary explanations, with no assumptions beyond the task description.
Implementation guidance:
- Prefer approaches similar to pyautogui-style interaction
  (keyboard and mouse simulation) when applicable.
- Do not assume the availability of libraries beyond standard Python
  or those implied by the retrieved context.
""")
        
        return "\n".join(prompt_parts)
    
    def _get_system_prompt(self) -> str:
        """Get system prompt for the LLM"""
        return f"""You are an expert Python automation engineer operating inside a multi-agent
RAG + Execution + Validation system.

Your output will be executed automatically in a sandboxed Windows environment.
It may be retried, validated, cached, compared, or re-executed.

Your primary responsibility is to generate automation code that is:
- Correct
- Minimal
- Deterministic
- Robust in real-world Windows environments

Reliability is more important than cleverness.

================================================================================
EXECUTION CONTEXT AWARENESS (MANDATORY)
================================================================================

- This code is part of a multi-step automation pipeline.
- If this task reaches code generation, all prior tasks in the same execution
  chain have already completed successfully and were validated.

YOU MAY ASSUME:
- The system state reflects the successful completion of previous steps.
- Any application opened or focused by prior tasks remains open and focused.
- Files, windows, selections, or UI state created earlier still exist unless
  explicitly modified by the current task.

YOU MUST NOT:
- Re-open applications unless explicitly requested.
- Re-focus, reset, or recreate windows unless required by the task.
- Undo, override, or repeat actions already completed in previous steps.

For first-step or standalone tasks, assume no prior state.

================================================================================
CORE GENERATION PRINCIPLES
================================================================================

1. STRICT INTENT ADHERENCE
- Perform ONLY the action explicitly requested by the user.
- Do NOT infer setup, cleanup, follow-up, or helper actions.
- Do NOT expand the scope of the task.
- Do NOT chain multiple actions unless explicitly instructed.

2. STATE-AWARE EXECUTION
- Respect the current system state implied by prior validated steps.
- Do NOT introduce defensive behavior that alters existing state.
- Never assume failure when the task has already reached execution.

3. SIMPLE-FIRST EXECUTION STRATEGY
- Always prefer the most:
  - General
  - Stable
  - Widely supported
  - Non-fragile
  approach.
- Avoid:
  - Hardcoded file paths
  - Application binaries
  - App-specific internals
  - Deep or brittle UI trees unless strictly necessary

4. ADAPTIVE EXECUTION MINDSET
- Assume no single method is universally reliable.
- Prefer human-like interaction patterns:
  1. Keyboard-driven interaction
  2. Mouse or UI interaction only when required
- Adapt only when the primary approach fails.

================================================================================
CRITICAL - DATA EXTRACTION AND OUTPUT RULES (NON-NEGOTIABLE)
================================================================================

When your code performs data extraction, copying, reading, or retrieval:

**YOU MUST OUTPUT THE ACTUAL DATA BEFORE ANY STATUS MESSAGE**

This is CRITICAL for multi-step workflows where subsequent tasks depend on your output.

✅ CORRECT PATTERN:
```python
# Extract/copy/read the data
content = pyperclip.paste()  # or file.read(), or extracted_data, etc.

# OUTPUT THE ACTUAL DATA FIRST
print(content)

# THEN print success indicator
print("EXECUTION_SUCCESS")
```

❌ WRONG PATTERN (DATA IS LOST):
```python
content = pyperclip.paste()
if content:
    print("EXECUTION_SUCCESS")  # ← Only status message, actual data is lost!
```

**Why this matters:**
- The next task in the pipeline receives your printed output as its input
- If you only print "EXECUTION_SUCCESS", the next task receives an empty string
- The entire workflow fails silently
- Always output data BEFORE status messages

**Specific Examples:**

COPYING FILE CONTENT:
```python
import pyautogui
import pyperclip
import time

try:
    # Select all and copy
    pyautogui.hotkey('ctrl', 'a')
    time.sleep(0.2)
    pyautogui.hotkey('ctrl', 'c')
    time.sleep(0.2)
    
    # Get the copied content
    content = pyperclip.paste()
    
    # OUTPUT THE ACTUAL CONTENT FIRST
    print(content)
    
    # THEN indicate success
    print("EXECUTION_SUCCESS")
except Exception as e:
    print(f"FAILED: {{e}}")
```

READING A FILE:
```python
import os

try:
    filepath = "D:/Downloads/file.txt"
    
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"File not found: {{filepath}}")
    
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # OUTPUT THE FILE CONTENT FIRST
    print(content)
    
    # THEN indicate success
    print("EXECUTION_SUCCESS")
except Exception as e:
    print(f"FAILED: {{e}}")
```

EXTRACTING TEXT FROM UI:
```python
import pyautogui
import pyperclip
import time

try:
    # Select text
    pyautogui.hotkey('ctrl', 'a')
    time.sleep(0.1)
    
    # Copy to clipboard
    pyautogui.hotkey('ctrl', 'c')
    time.sleep(0.2)
    
    # Extract from clipboard
    extracted_text = pyperclip.paste()
    
    # OUTPUT THE EXTRACTED TEXT FIRST
    print(extracted_text)
    
    # THEN indicate success
    print("EXECUTION_SUCCESS")
except Exception as e:
    print(f"FAILED: "{{e}}")
```

WEB SCRAPING / DATA EXTRACTION:
```python
# Whatever extraction method you use...
extracted_data = element.get_text()

# OUTPUT THE EXTRACTED DATA FIRST
print(extracted_data)

# THEN indicate success
print("EXECUTION_SUCCESS")
```

**Remember:**
- Data output FIRST
- Status message SECOND
- This applies to ANY task that extracts, copies, reads, or retrieves information
- Failure to follow this pattern breaks the entire workflow

================================================================================
LIBRARY USAGE RULES
================================================================================

- Use only Python libraries that are part of the standard Python ecosystem
  or clearly implied by the execution context.
- Do NOT invent, guess, or assume the availability of libraries.
- If a capability is unavailable, fail explicitly instead of guessing.
- Do NOT rely on system-level execution or process spawning.

================================================================================
FORBIDDEN EXECUTION METHODS (HARD RULES)
================================================================================

- Do NOT use:
  - subprocess
  - os.system
  - os.startfile
  - shell commands
  - PowerShell or cmd
  - Direct process spawning or execution APIs

This agent simulates user interaction.
If something must be opened or interacted with:
- Do so through input or UI-level automation
- Never through system execution calls

Any violation will fail validation.

================================================================================
AVAILABLE LIBRARIES (STRICT - DO NOT HALLUCINATE)
================================================================================

ALLOWED LIBRARIES ONLY:
- pyautogui (mouse/keyboard automation)
- pyperclip (clipboard operations)
- time (delays)
- os (file operations)
- json, csv (data parsing)

FORBIDDEN - DO NOT USE:
uiautomation (does not exist)
pywinauto (blocked for security)
keyboard (not installed)
subprocess (security violation)

If you need UI automation: USE ONLY pyautogui
If task requires unavailable library: print("FAILED: Library not available")

================================================================================
AUTOMATION INTERACTION GUIDANCE
================================================================================

- Keyboard interaction is preferred whenever reliable.
- UI automation is acceptable when keyboard-only interaction is insufficient.
- Avoid brittle assumptions:
  - Do NOT rely on exact window titles
  - Prefer partial or resilient matching
  - Avoid hardcoded control identifiers

================================================================================
SHORTCUT USAGE RULES
================================================================================

- Do NOT invent or guess shortcuts.
- Only use:
  - Universally standard shortcuts (e.g., Ctrl+C, Ctrl+V, Ctrl+S, Ctrl+N)
  - Shortcuts explicitly provided by the user
- If uncertain, choose a more general interaction method.

================================================================================
EXECUTION & VALIDATION RULES
================================================================================

- Always generate COMPLETE, runnable Python code.
- Include all required imports.
- Wrap risky operations in try-except blocks.
- Print execution state exactly once:
  - EXECUTION_SUCCESS → only when the PRIMARY task completes
  - FAILED: <error>   → only when the PRIMARY task fails
- Do NOT report failure due to cleanup or secondary steps.
- Print success BEFORE any optional teardown logic.
- Do NOT close or terminate applications unless explicitly requested.

**CRITICAL REMINDER:**
For data extraction tasks (copy, read, extract):
1. Print the actual data FIRST
2. Print "EXECUTION_SUCCESS" SECOND

================================================================================
OUTPUT FORMAT (STRICT)
================================================================================

- Output ONLY a single Python code block.
- No explanations.
- No markdown.
- No extra text.

"""

    
    def _parse_response(self, response: str, contexts: List[Dict]) -> Dict:
        """Parse LLM response into structured format"""
        
        # Extract code block
        code = ""
        explanation = ""
        
        if "```python" in response:
            parts = response.split("```python")
            if len(parts) > 1:
                code_part = parts[1].split("```")[0].strip()
                code = code_part
                
                # Get explanation (text after code block)
                remaining = parts[1].split("```", 1)
                if len(remaining) > 1:
                    explanation = remaining[1].strip()
        else:
            # No code block found, treat entire response as explanation
            explanation = response
        
        return {
            'code': code,
            'explanation': explanation,
            'full_response': response,
            'contexts_used': len(contexts),
            'top_similarity': contexts[0]['similarity'] if contexts else 0,
            'references': [
                {
                    'source': ctx['metadata'].get('source', 'unknown'),
                    'similarity': ctx['similarity']
                }
                for ctx in contexts[:3]
            ]
        }
    
    def chat(self, message: str) -> Dict:
        """
        Interactive chat interface
        """
        return self.generate_code(
            user_query=message,
            conversation_context=self.conversation_history
        )
    
    def save_conversation(self, filename: str = None):
        """Save conversation history"""
        if filename is None:
            filename = f"conversation_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        
        filepath = Path("conversations") / filename
        filepath.parent.mkdir(exist_ok=True)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(self.conversation_history, f, indent=2, ensure_ascii=False)
        
        print(f"Conversation saved to {filepath}")



# %%

# ============================================================================
# CELL 5: Code Execution and Testing (Optional)
# ============================================================================

class CodeExecutor:
    """Safely execute generated code for testing"""
    
    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self.module_selector = ModuleSelector()
    
    def execute(self, code: str, test_mode: bool = True) -> Dict:
        """
        Execute code and capture output
        
        Args:
            code: Python code to execute
            test_mode: If True, runs in restricted environment
            
        Returns:
            Dictionary with execution results
        """
        import sys
        from io import StringIO
        import traceback
        
        # Capture output
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        redirected_output = StringIO()
        redirected_error = StringIO()
        
        sys.stdout = redirected_output
        sys.stderr = redirected_error
        
        result = {
            'success': False,
            'output': '',
            'error': '',
            'execution_time': 0
        }
        
        try:
            import time
            start_time = time.time()
            
            # Create restricted globals
            restricted_globals = {
                '__builtins__': __builtins__,
                'print': print,
            }
            
            # Execute code
            exec(code, restricted_globals)
            
            result['success'] = True
            result['execution_time'] = time.time() - start_time
            result['output'] = redirected_output.getvalue()
            
        except Exception as e:
            result['error'] = traceback.format_exc()
            result['output'] = redirected_output.getvalue()
        
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr
        
        return result



# %%

# ============================================================================
# CELL 6: Example Usage and Testing
# ============================================================================

def demo_rag_system():
    """Demonstrate the RAG system"""
    
    print("=" * 80)
    print("RAG SYSTEM DEMO")
    print("=" * 80)
    
    # Initialize system
    config = RAGConfig(
        library_name="pywinauto",
        llm_provider="groq",  # Change to "openai" or "ollama" as needed
        top_k=5,
        temperature=0.2
    )
    
    rag = RAGSystem(config)
    rag.initialize()
    
    # Example queries
    test_queries = [
        "How do I click a button in a window using pywinauto?",
        "Show me how to connect to a running application",
        "How do I type text into a text field?",
        "Create code to automate a login dialog with username and password fields"
    ]
    
    for query in test_queries:
        print("\n" + "="*80)
        result = rag.generate_code(query)
        
        print("\n📝 Generated Code:")
        print("-" * 80)
        print(result['code'])
        
        print("\n💡 Explanation:")
        print("-" * 80)
        print(result['explanation'])
        
        print("\n📊 Metadata:")
        print(f"  - Contexts used: {result['contexts_used']}")
        print(f"  - Top similarity: {result['top_similarity']:.2%}")
        print(f"  - References: {result['references']}")
        
        # Optional: Test execution
        # executor = CodeExecutor()
        # exec_result = executor.execute(result['code'])
        # print(f"\n✅ Execution: {'Success' if exec_result['success'] else 'Failed'}")
        
        input("\nPress Enter to continue to next query...")
    
    # Save conversation
    rag.save_conversation()
    
    return rag



# %%

# ============================================================================
# CELL 7: Interactive Chat Interface
# ============================================================================

def interactive_chat():
    """Run interactive chat session"""
    
    print("=" * 80)
    print("🤖 RAG-POWERED CODE ASSISTANT")
    print("=" * 80)
    print("\nInitializing system...")
    
    config = RAGConfig(
        library_name="pywinauto",
        llm_provider="groq",  # Change as needed
        temperature=0.2
    )
    
    rag = RAGSystem(config)
    rag.initialize()
    
    print(f"\n✅ System ready! Ask me anything about {config.library_name}")
    print("Commands: 'quit' to exit, 'save' to save conversation, 'clear' to clear history\n")
    
    while True:
        try:
            user_input = input("\n👤 You: ").strip()
            
            if not user_input:
                continue
            
            if user_input.lower() == 'quit':
                print("\n👋 Goodbye!")
                break
            
            if user_input.lower() == 'save':
                rag.save_conversation()
                continue
            
            if user_input.lower() == 'clear':
                rag.conversation_history = []
                print("✅ Conversation history cleared")
                continue
            
            # Generate response
            result = rag.chat(user_input)
            
            print("\n🤖 Assistant:\n")
            
            if result['code']:
                print("```python")
                print(result['code'])
                print("```\n")
            
            if result['explanation']:
                print(result['explanation'])
            
            print(f"\n📊 (Used {result['contexts_used']} references, "
                  f"top similarity: {result['top_similarity']:.1%})")
            
        except KeyboardInterrupt:
            print("\n\n👋 Goodbye!")
            break
        except Exception as e:
            print(f"\n❌ Error: {e}")
            import traceback
            traceback.print_exc()



# %%


# %%

# ============================================================================
# CELL 8: Run Demo or Interactive Mode
# ============================================================================

if __name__ == "__main__":
    import sys
    
    # Set your API key here or in environment
    # os.environ["OPENAI_API_KEY"] = "your-api-key-here"
    
    
    print("\nChoose mode:")
    print("1. Demo mode (run example queries)")
    print("2. Interactive chat")
    
    choice = input("\nEnter choice (1 or 2): ").strip()
    
    if choice == "1":
        rag_system = demo_rag_system()
    elif choice == "2":
        interactive_chat()
    else:
        print("Invalid choice. Running demo mode...")
        rag_system = demo_rag_system()



# %%
