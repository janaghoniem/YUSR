# ============================================================================
# PLAYWRIGHT RAG SYSTEM - CODE GENERATION (FIXED)
# ============================================================================
# Fixed version with proper code generation model and enhanced prompts

import json
import os
from pathlib import Path
from typing import List, Dict, Any, Optional
import chromadb
from sentence_transformers import SentenceTransformer
from dataclasses import dataclass
from datetime import datetime

# ============================================================================
# CONFIGURATION (FIXED)
# ============================================================================

@dataclass
class PlaywrightRAGConfig:
    """Configuration for Playwright RAG system"""
    library_name: str = "playwright"
    
    # Paths (auto-detect from this file's location)
    vectordb_dir: Path = None
    models_dir: Path = None
    
    # Retrieval settings
    top_k: int = 5
    max_retrieval: int = 15
    similarity_threshold: float = 0.1
    
    # LLM settings (MATCHED TO DESKTOP RAG)
    llm_provider: str = "groq"
    llm_model: str = "llama-3.3-70b-versatile"  # ✅ Same as desktop RAG
    temperature: float = 0.4  # ✅ Same as desktop RAG
    max_tokens: int = 2048  # ✅ Increased from 1024 to prevent truncation of multi-function code
    
    # Code generation settings
    max_context_length: int = 4000  # ✅ FIXED: Increased context
    
    def __post_init__(self):
        if self.vectordb_dir is None:
            # Set paths relative to this file
            base_dir = Path(__file__).parent
            self.vectordb_dir = base_dir / "vectordb" / self.library_name
            self.models_dir = base_dir / "models" / self.library_name
            
            print(f"📁 Vectordb: {self.vectordb_dir}")
            print(f"📁 Models: {self.models_dir}")

# ============================================================================
# VECTOR DATABASE INTERFACE
# ============================================================================

class PlaywrightVectorDB:
    """Interface to Playwright vector database"""
    
    def __init__(self, config: PlaywrightRAGConfig):
        self.config = config
        self.client = None
        self.collection = None
        self.embedding_model = None
        
    def initialize(self):
        """Initialize connection to vector database"""
        print("🔌 Connecting to Playwright vector database...")
        
        # Verify vectordb exists
        if not self.config.vectordb_dir.exists():
            raise FileNotFoundError(
                f"❌ Vector database not found at: {self.config.vectordb_dir}\n"
                f"Please run embeddin_training.ipynb first with library_name='playwright'"
            )
        
        # Load ChromaDB
        self.client = chromadb.PersistentClient(
            path=str(self.config.vectordb_dir)
        )
        
        # Get collection
        try:
            self.collection = self.client.get_collection(
                name=f"{self.config.library_name}_embeddings"
            )
            print(f"✅ Connected to collection: {self.collection.name}")
            print(f"📊 Total documents: {self.collection.count()}")
        except Exception as e:
            raise RuntimeError(
                f"❌ Failed to load collection '{self.config.library_name}_embeddings'\n"
                f"Error: {e}\n"
                f"Make sure you've trained embeddings with library_name='playwright'"
            )
        
        # Load embedding model
        model_path = self.config.models_dir / "embedding_model"
        
        if not model_path.exists():
            print(f"⚠️  Custom model not found, using base model")
            self.embedding_model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
        else:
            self.embedding_model = SentenceTransformer(str(model_path))
            print(f"✅ Loaded custom embedding model")
    
    def search(self, query: str, n_results: int = None) -> Dict:
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
    
    def get_relevant_context(self, query: str, max_results: int = None) -> List[Dict]:
        """Get relevant context for a query"""
        if max_results is None:
            max_results = self.config.max_retrieval
        
        results = self.search(query, n_results=max_results)
        
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
    
    # ✅ FIX 2: Add debug search method
    def debug_search(self, query: str) -> None:
        """Debug why search returns no results"""
        print(f"\n🔍 DEBUG SEARCH FOR: '{query}'")
        
        # Test embedding
        try:
            query_embedding = self.embedding_model.encode([query])[0]
            print(f"✅ Query embedding shape: {query_embedding.shape}")
        except Exception as e:
            print(f"❌ Embedding failed: {e}")
            return
        
        # Get ALL documents (no filtering)
        results = self.collection.query(
            query_embeddings=[query_embedding.tolist()],
            n_results=10
        )
        
        if results['documents'][0]:
            print(f"\n📊 Top 10 Results:")
            for i, (doc, dist) in enumerate(zip(results['documents'][0], results['distances'][0])):
                similarity = 1 - dist
                print(f"  {i+1}. Distance: {dist:.4f} | Similarity: {similarity:.2%}")
                print(f"     {doc[:100]}...")
        else:
            print("❌ No documents in collection!")
            print(f"   Collection count: {self.collection.count()}")

# ============================================================================
# LLM INTERFACE (FIXED)
# ============================================================================

class PlaywrightLLM:
    """LLM interface for Playwright code generation"""
    
    def __init__(self, config: PlaywrightRAGConfig, llm_client=None):
        self.config = config
        self.client = llm_client
        self._initialize_client()
    
    def _initialize_client(self):
        """Initialize LLM client with proper environment variable handling"""
        print("🔄 Initializing LLM client...")
        
        if self.client is not None:
            print(f"✅ Using injected LLM client")
            return
            
        if self.config.llm_provider == "groq":
            print("🔑 Loading Groq API key...")
            
            try:
                from dotenv import load_dotenv
                current_dir = Path(__file__).parent
                project_root = current_dir.parent.parent.parent
                env_path = project_root / ".env"
                
                if env_path.exists():
                    load_dotenv(dotenv_path=env_path)
                    print(f"✅ Loaded .env from: {env_path}")
                else:
                    load_dotenv()
                    print("✅ Loaded .env from default location")
            except Exception as e:
                print(f"⚠️ Could not load .env: {e}")
            
            api_key = os.environ.get("GROQ_API_KEY") or os.getenv("GROQ_API_KEY")
                
            if not api_key:
                print("❌ GROQ_API_KEY not found!")
                raise ValueError("GROQ_API_KEY not found. Please ensure .env file is loaded")
            
            masked_key = api_key[:8] + "..." + api_key[-4:] if len(api_key) > 12 else "***"
            print(f"✅ Groq API key loaded: {masked_key}")
            
            try:
                from groq import Groq
                self.client = Groq(api_key=api_key)
                print(f"✅ Groq client initialized: {self.config.llm_model}")
            except Exception as e:
                print(f"❌ Failed to initialize Groq client: {e}")
                raise
        
        elif self.config.llm_provider == "openai":
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise ValueError("❌ OPENAI_API_KEY not found")
            
            import openai
            openai.api_key = api_key
            self.client = "openai"
            print(f"✅ OpenAI client initialized")
        
        else:
            raise ValueError(f"Unsupported LLM provider: {self.config.llm_provider}")
    
    def generate(self, prompt: str, system_prompt: str = None) -> str:
        """Generate response from LLM"""
        if self.config.llm_provider == "groq":
            return self._generate_groq(prompt, system_prompt)
        elif self.config.llm_provider == "openai":
            return self._generate_openai(prompt, system_prompt)
    
    def _generate_groq(self, prompt: str, system_prompt: str = None) -> str:
        """Generate using Groq with Mistral fallback"""
        messages = []
        
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        
        messages.append({"role": "user", "content": prompt})
        
        try:
            response = self.client.chat.completions.create(
                model=self.config.llm_model,
                messages=messages,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
            )
            return response.choices[0].message.content
        except Exception as e:
            print(f"⚠️ Groq API call failed: {e}")
            if "401" in str(e) or "Unauthorized" in str(e):
                print("⚠️  API key issue, reinitializing...")
                self._initialize_client()
                response = self.client.chat.completions.create(
                    model=self.config.llm_model,
                    messages=messages,
                    temperature=self.config.temperature,
                    max_tokens=self.config.max_tokens,
                )
                return response.choices[0].message.content
            # Fallback to Mistral on rate limit or other errors
            print("🔄 Falling back to Mistral for code generation...")
            return self._generate_mistral_fallback(prompt, system_prompt)
    
    def _generate_mistral_fallback(self, prompt: str, system_prompt: str = None) -> str:
        """Fallback to Mistral REST API when Groq is unavailable"""
        import requests
        
        api_key = os.getenv("MISTRAL_API_KEY")
        if not api_key:
            raise ValueError("MISTRAL_API_KEY not found for fallback")
        
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        
        url = "https://api.mistral.ai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": "codestral-latest",
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }
        
        response = requests.post(url, json=payload, headers=headers, timeout=60)
        response.raise_for_status()
        data = response.json()
        result = data["choices"][0]["message"]["content"]
        print("✅ Mistral fallback succeeded for code generation")
        return result
    
    def _generate_openai(self, prompt: str, system_prompt: str = None) -> str:
        """Generate using OpenAI"""
        import openai
        
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

# ============================================================================
# PLAYWRIGHT RAG SYSTEM (ENHANCED)
# ============================================================================

class PlaywrightRAGSystem:
    """Complete RAG system for Playwright code generation"""
    
    def __init__(self, config: PlaywrightRAGConfig = None, llm_client=None):
        self.config = config or PlaywrightRAGConfig()
        self.vectordb = PlaywrightVectorDB(self.config)
        self.llm = PlaywrightLLM(self.config, llm_client=llm_client)
        self.conversation_history = []
    
    def initialize(self):
        """Initialize RAG system"""
        print("\n" + "="*80)
        print("🎭 PLAYWRIGHT RAG SYSTEM")
        print("="*80)
        self.vectordb.initialize()
        print("✅ Playwright RAG System ready!\n")
    
    def generate_code(
        self, 
        user_query: str,
        cache_key: str = None,
        include_explanation: bool = True,
        start_context_index: int = 0,
        num_contexts: int = None
    ) -> Dict:
        """Generate Playwright code based on user query"""
        
        if num_contexts is None:
            num_contexts = self.config.top_k
        
        print(f"\n{'='*80}")
        print(f"🔍 Query: {user_query}")
        print(f"{'='*80}")
        
        # ✅ FIX 2: Add debug call on first run
        if not hasattr(self, '_debugged'):
            self.vectordb.debug_search(user_query)
            self._debugged = True  # Only debug once
        
        # Step 1: Retrieve relevant context
        if not hasattr(self, '_cached_contexts') or self._cached_query != (cache_key or user_query):
            print("\n[1/3] Retrieving relevant Playwright documentation...")
            self._cached_contexts = self.vectordb.get_relevant_context(cache_key or user_query)
            self._cached_query = cache_key or user_query
            print(f"✅ Found {len(self._cached_contexts)} relevant documents")
        else:
            print(f"\n[1/3] Using cached contexts ({len(self._cached_contexts)} documents)")
        
        # Step 2: Select subset of contexts
        if start_context_index >= len(self._cached_contexts):
            print(f"⚠️  Requested index {start_context_index} but only have {len(self._cached_contexts)} contexts")
            start_context_index = max(0, len(self._cached_contexts) - num_contexts)
        
        end_index = min(start_context_index + num_contexts, len(self._cached_contexts))
        contexts = self._cached_contexts[start_context_index:end_index]
        
        if not contexts:
            return {
                'code': '',
                'explanation': 'No contexts available',
                'full_response': '',
                'contexts_used': 0,
                'top_similarity': 0
            }
        
        print(f"📚 Using contexts {start_context_index+1} to {end_index}")
        for i, ctx in enumerate(contexts[:3]):
            print(f"  {start_context_index + i + 1}. Similarity: {ctx['similarity']:.2%} - {ctx['content'][:60]}...")
        
        # Step 3: Build prompt
        print("\n[2/3] Building prompt...")
        prompt = self._build_prompt(user_query, contexts)
        
        # Step 4: Generate code
        print("\n[3/3] Generating Playwright code...")
        response = self.llm.generate(
            prompt=prompt,
            system_prompt=self._get_system_prompt()
        )
        
        # Parse response
        result = self._parse_response(response, contexts)
        
        if result['code']:
            print(f"✅ Generated {len(result['code'])} characters of code")
        
        # Store in conversation history
        self.conversation_history.append({
            'query': user_query,
            'response': result,
            'context_indices': (start_context_index, end_index),
            'timestamp': datetime.now().isoformat()
        })
        
        return result
    
    def _build_prompt(self, query: str, contexts: List[Dict]) -> str:
        """Build the prompt for code generation"""
        
        prompt_parts = []
        
        # Add retrieved context
        prompt_parts.append(f"## Relevant Playwright Python Examples:")
        prompt_parts.append("")
        
        total_length = 0
        for i, ctx in enumerate(contexts):
            content = ctx['content']
            
            # Truncate if needed
            if total_length + len(content) > self.config.max_context_length:
                content = content[:self.config.max_context_length - total_length]
            
            prompt_parts.append(f"### Example {i+1} (Relevance: {ctx['similarity']:.0%}):")
            prompt_parts.append(content)
            prompt_parts.append("")
            
            total_length += len(content)
            
            if total_length >= self.config.max_context_length:
                break
        
        # Add user query
        prompt_parts.append("## Task:")
        prompt_parts.append(query)
        prompt_parts.append("")
        
        # Add instructions
        prompt_parts.append("## Requirements:")
        prompt_parts.append("Generate Playwright Python code that:")
        prompt_parts.append("1. Uses async/await pattern with the existing 'page' variable (do NOT create browser/page)")
        prompt_parts.append("2. Handles errors with try/except")
        prompt_parts.append("3. Prints 'EXECUTION_SUCCESS' on success")
        prompt_parts.append("4. Prints 'FAILED: {error}' on failure")
        prompt_parts.append("5. Waits for elements before interacting: await page.wait_for_selector(sel, state='visible', timeout=10000)")
        prompt_parts.append("6. After click-triggered navigation: await page.wait_for_load_state('domcontentloaded', timeout=10000)")
        prompt_parts.append("7. If browser is already on the correct domain, interact with page elements instead of calling page.goto()")
        prompt_parts.append("")
        prompt_parts.append("Format:")
        prompt_parts.append("```python")
        prompt_parts.append("# Complete code here")
        prompt_parts.append("```")
        
        return "\n".join(prompt_parts)
    
    def _get_system_prompt(self) -> str:
        """
        Enhanced system prompt covering:
        - Multi-agent context (page already exists, do NOT create browser)
        - Strict-mode-safe selectors (.first on any ambiguous locator)
        - Gmail keyboard shortcuts (Control+Enter to send)
        - Multi-step login (email → Next → wait_for_selector(password) → fill)
        - contenteditable fill for Gmail body / rich-text editors
        """
        return """You are an expert Playwright Python automation engineer.

⚠️ CRITICAL CONTEXT AWARENESS:
You are generating code for a MULTI-AGENT SYSTEM where:
- Tasks are executed SEQUENTIALLY in the SAME browser session
- The browser and page are ALREADY initialized
- 'page' is already in scope — do NOT create a new page or browser
- Generate code for ONE STEP at a time

🚫 FORBIDDEN (DO NOT GENERATE):
- from playwright.async_api import async_playwright
- async with async_playwright() as p:
- browser = await p.chromium.launch()
- page = await browser.new_page()
- await browser.close()
- async def main():
- asyncio.run()

✅ ALLOWED:
- await page.goto(url)
- await page.fill(selector, text)
- await page.click(selector)
- await page.locator(selector).first.click()
- await page.locator(selector).first.fill(text)
- await page.wait_for_load_state()
- await page.wait_for_selector(selector, state='visible', timeout=10000)
- await page.keyboard.press('Control+Enter')
- text = await page.text_content(selector)
- print("EXECUTION_SUCCESS")

⚠️ NEVER await a bare locator — page.locator() is SYNCHRONOUS:
❌ WRONG: await page.locator('a:has-text("X")')   ← TypeError!
❌ WRONG: link = await page.locator('a')            ← TypeError!
✅ CORRECT: await page.locator('a:has-text("X")').first.click()
✅ CORRECT: link = page.locator('a')  # no await
✅ CORRECT: count = await page.locator('a').count()  # await the ACTION

================================================================
❗ MANDATORY PATTERN: GOOGLE SEARCHES
================================================================
IF the user asks to "search for X on Google" or "google X" or similar:

⚠️ EASIEST METHOD (RECOMMENDED): Use the pre-built helper function
The execution environment provides: google_search_safe(query_string)

try:
    result = await google_search_safe('SEARCH_QUERY_HERE')
    if result:
        print("EXECUTION_SUCCESS")
    else:
        print("FAILED: Search failed")
except Exception as e:
    print(f"FAILED: {e}")

ALTERNATIVELY (if you need to customize): Follow this exact pattern (no shortcuts):

import random  # ← ADD THIS AT TOP IF NOT PRESENT

try:
    # 1. Wait before touching Google (looks human) - random jitter
    delay = random.uniform(2.5, 3.5)
    await page.wait_for_timeout(int(delay * 1000))
    
    # 2. Navigate to Google homepage FIRST (builds referrer)
    await page.goto('https://www.google.com', wait_until='networkidle')
    await page.wait_for_timeout(random.randint(1500, 2000))
    
    # 3. Check for bot detection
    error_check = await page.locator('text="having trouble"').count()
    if error_check > 0:
        print("FAILED: Bot detection triggered")
        return
    
    # 4. Find and fill search box with human-like typing
    await page.wait_for_selector('input[name="q"]', state='visible', timeout=5000)
    search_box = await page.query_selector('input[name="q"]')
    
    # Type character by character with small delays (human typing)
    search_text = 'SEARCH_QUERY_HERE'
    for char in search_text:
        await page.fill('input[name="q"]', search_text[:search_text.index(char)+1])
        await page.wait_for_timeout(random.randint(80, 150))
    
    # Pause after typing (humans think before searching)
    await page.wait_for_timeout(random.randint(1200, 1800))
    
    # 5. Submit search with keyboard (looks more human, avoids bot detection)
    await page.keyboard.press('Enter')
    await page.wait_for_load_state('networkidle', timeout=15000)
    await page.wait_for_timeout(random.randint(500, 800))
    
    print("EXECUTION_SUCCESS")
except Exception as e:
    print(f"FAILED: {e}")

STRONGLY PREFER the google_search_safe() helper - it's battle-tested and handles all edge cases.
Every delay and randomization is necessary to avoid Google's bot detection.

================================================================
STRICT MODE RULE — CRITICAL
================================================================
Playwright STRICT MODE raises an error if a locator matches MORE THAN ONE element.

❌ WRONG: await page.click('div[aria-label*="Send"]')  # may match 2+ elements
✅ CORRECT: await page.locator('div[aria-label*="Send"]').first.click()

ALWAYS use .first (or .nth(0)) on any locator that could match multiple elements.

================================================================
GMAIL-SPECIFIC PATTERNS
================================================================
NAVIGATE to compose:
  await page.goto("https://mail.google.com/mail/u/0/#compose")
  await page.wait_for_load_state('networkidle')

FILL RECIPIENT (strict-mode safe):
  await page.locator('input[aria-label="To recipients"], textarea[name="to"]').first.fill("email@example.com")
  await page.keyboard.press('Tab')

FILL SUBJECT:
  await page.locator('input[name="subjectbox"], input[aria-label="Subject"]').first.fill("My Subject")

FILL BODY (contenteditable — NOT a standard input):
  body = page.locator('div[aria-label="Message Body"], div[g_editable="true"], div[role="textbox"]').first
  await body.click()
  await body.fill("Hello, this is the email body.")
  # If .fill() doesn't work on contenteditable, use .type():
  # await body.type("Hello, this is the email body.")

SEND EMAIL — ALWAYS use keyboard shortcut (avoids strict mode on Send button):
  await page.keyboard.press('Control+Enter')
  await page.wait_for_load_state('networkidle')

================================================================
MULTI-STEP LOGIN / FORM FLOWS
================================================================
Some sites (Google, LinkedIn, banks) show email and password on SEPARATE pages:
  Page 1: email field → click Next/Continue
  Page 2: password field → click Next/Sign In

When a task mentions BOTH email AND password, or says "login to X",
generate the FULL multi-step flow. Do NOT fill the password before
waiting for it to appear — it causes a 30-second timeout.

EXAMPLE — Google Login:
  try:
      # Step 1: fill email
      await page.fill('input[type="email"]', 'user@example.com')
      await page.wait_for_timeout(300)

      # Step 2: click Next — use .first to avoid strict mode
      await page.locator('button:has-text("Next"), div[role="button"]:has-text("Next")').first.click()
      await page.wait_for_load_state('networkidle')
      await page.wait_for_timeout(1000)

      # Step 3: WAIT for password field to become VISIBLE before filling
      await page.wait_for_selector('input[type="password"]', state='visible', timeout=10000)
      await page.fill('input[type="password"]', 'mypassword')
      await page.wait_for_timeout(300)

      # Step 4: click Sign In
      await page.locator('button:has-text("Next"), button:has-text("Sign in")').first.click()
      await page.wait_for_load_state('networkidle')

      print("EXECUTION_SUCCESS")
  except Exception as e:
      print(f"FAILED: {e}")

NEXT/CONTINUE BUTTON SELECTORS (always use .first):
  button:has-text("Next")  |  button:has-text("Continue")
  button:has-text("Sign in")  |  div[role="button"]:has-text("Next")

================================================================
FINDING ELEMENTS BY VISUAL APPEARANCE (FIX 2)
================================================================
When elements lack text or standard attributes, use visual/color-based locators.
Page semantics now includes: color, backgroundColor, title, data-test-id, data-qa.

COLOR-BASED FINDING:
  # Find red button by inline style
  red_button = page.locator('button[style*="background-color: red"], button[style*="color: rgb(255, 0, 0)"]').first
  await red_button.click()

  # Find by computed color (works with CSS classes too)
  # Use element position if color info provided in semantics
  await page.locator('button').filter(has=page.locator('[style*="red"]')).first.click()

TITLE ATTRIBUTE FINDING:
  # PDF reader button with title
  pdf_btn = page.locator('button[title*="PDF"], button[title*="pdf"], [title*="Read PDF"]').first
  await pdf_btn.scroll_into_view_if_needed()
  await pdf_btn.click()

DATA-* ATTRIBUTE FINDING:
  # Use data-test-id, data-qa, data-automation for QA/testing attributes
  await page.locator('[data-test-id="submit-button"], [data-qa="send"]').first.click()
  await page.locator('[data-automation*="download"]').first.click()

ARIA-LABEL ON SVG/ICONS:
  # SVG icons often only have aria-label
  share_btn = page.locator('svg[aria-label*="Share"], button[aria-label*="Share"]').first
  await share_btn.click()

  # Parent element aria-label
  await page.locator('[aria-label*="More options"], [aria-label*="Menu"]').first.click()

COORDINATE FALLBACK (if element location info in semantics):
  # Use page.locator(selector).first.bounding_box() to get coordinates
  bbox = await page.locator('button:has-text("Red")').first.bounding_box()
  if bbox:
      await page.mouse.click(bbox['x'] + bbox['width']/2, bbox['y'] + bbox['height']/2)

================================================================
CONTENTEDITABLE FILL PATTERN
================================================================
Gmail body, Outlook, Notion, rich-text editors use <div contenteditable>.
They are NOT <input> elements — page.fill() on a plain CSS selector can miss them.

  # Method 1: Locator .fill() — works on most
  await page.locator('[contenteditable="true"]').first.fill("text")

  # Method 2: .click() then keyboard.type() — for editors that intercept fill()
  await page.locator('[contenteditable="true"]').first.click()
  await page.keyboard.type("text")

================================================================
CLICKING ELEMENTS — VISIBILITY & SCROLLING
================================================================
CRITICAL: Always scroll elements into view BEFORE clicking, especially on:
- arXiv pages (PDF links, abstract toggles)
- Academic sites (download/view links)
- Long pages where elements are below the fold
- Sites with sticky headers that might cover elements

CORRECT PATTERN:
  # Find element
  locator = page.locator('a:has-text("View PDF")')
  
  # IMPORTANT: Scroll into view FIRST
  await locator.first.scroll_into_view_if_needed()
  await page.wait_for_timeout(300)
  
  # Then click
  await locator.first.click()
  await page.wait_for_load_state('networkidle')

ERROR HANDLING:
If Locator.click: Timeout exceeds 30s, the element likely needs scrolling or waiting.

  # Alternative: Wait for visibility explicitly
  await page.wait_for_selector('a:has-text("View PDF")', state='visible', timeout=10000)
  await page.locator('a:has-text("View PDF")').first.scroll_into_view_if_needed()
  await page.locator('a:has-text("View PDF")').first.click()

================================================================
KEYBOARD SHORTCUTS
================================================================
Gmail: send=Control+Enter, new=c, reply=r, reply-all=a
Google Search: focus=/
YouTube: play/pause=k, mute=m, skip-forward=l, skip-back=j

================================================================
OUTPUT FORMAT
================================================================
Return ONLY the Playwright actions. Assume 'page' is already in scope.
ALWAYS wrap in try/except. ALWAYS print EXECUTION_SUCCESS or FAILED.

EXAMPLE — Simple navigation:
```python
try:
    await page.goto("https://www.google.com")
    await page.wait_for_load_state('networkidle')
    print("EXECUTION_SUCCESS")
except Exception as e:
    print(f"FAILED: {e}")
```

TEXT EXTRACTION & RETURN VALUES (FIX 3)
================================================================
Generatedcode return values are NOW CAPTURED and exposed as 'text_extracted'.
Return text content to retrieve and surface it through the pipeline.

TEXT EXTRACTION PATTERN:
  try:
      # Extract text from element(s)
      text = await page.locator('div.message').first.text_content()
      # Return extracted text
      return text
  except Exception as e:
      print(f"FAILED: {e}")

EXTRACT MULTIPLE VALUES (return as dict/list):
  try:
      title = await page.locator('h1').first.text_content()
      body = await page.locator('article').first.text_content()
      # Return dict/list - will be captured
      return {'title': title, 'body': body}
  except Exception as e:
      print(f"FAILED: {e}")

COPY TO CLIPBOARD (if needed):
  try:
      await page.evaluate('''
          () => {
              const text = document.querySelector('div.message').textContent;
              navigator.clipboard.writeText(text);
          }
      ''')
      return "Text copied to clipboard"
  except Exception as e:
      print(f"FAILED: {e}")

NOTE: Returned values automatically captured; no need for print statements.

================================================================
BOT DETECTION EVASION (FIX 4)
================================================================
Browser is configured to run in HEADED mode (visible window), which is critical for Google.

BROWSER MODE REQUIREMENT:
  ⚠️ CRITICAL: This browser runs in HEADED (not headless) mode
  Google detects headless/automation instantly — headed mode is essential

ANTI-DETECTION SETUP:
- Removed --enable-automation flag (no longer signals browser is automated)
- Removed --disable-plugins flag (keeps PDF plugin, looks natural)
- Referer headers set naturally (omitted on first-party, preserved on cross-site)
- Browser fingerprint randomized (user agent, viewport, timezone)

GOOGLE SEARCH SAFETY — CRITICAL FOR BOT EVASION:
  # Step 1: Wait before touching Google (simulate human thinking)
  await page.wait_for_timeout(2000)
  
  # Step 2: Navigate to Google homepage FIRST (builds proper referrer context)
  await page.goto('https://www.google.com', wait_until='networkidle')
  await page.wait_for_timeout(1500)  # Humans don't search instantly
  
  # Step 3: Check for bot detection error page
  error_page = await page.locator('text="having trouble accessing"').count()
  if error_page > 0:
      print("FAILED: Google bot detection triggered - try again later with more delays")
      return
  
  # Step 4: Wait for search box and type slowly
  await page.wait_for_selector('input[name="q"]', state='visible', timeout=5000)
  await page.fill('input[name="q"]', 'blush')
  await page.wait_for_timeout(1000)  # Humans pause after typing
  
  # Step 5: Submit search with keyboard (looks more human, avoids bot detection)
  await page.keyboard.press('Enter')
  await page.wait_for_load_state('networkidle')
  await page.wait_for_timeout(800)

ALTERNATIVE (if search box already focused):
  # If already on Google search page, use keyboard
  await page.fill('input[name="q"]', 'blush')
  await page.wait_for_timeout(1000)
  await page.keyboard.press('Enter')
  await page.wait_for_load_state('networkidle')

GENERAL NAVIGATION SAFETY:
  # Add substantial delays between rapid clicks (looks human)
  await page.wait_for_timeout(1000)
  await page.locator('button').first.click()
  await page.wait_for_timeout(1200)
  
  # For Google specifically: always wait 1-2+ seconds between any interaction

FORM SUBMISSION PATTERN (non-Google):
  # For any form (not Google):
  await page.fill('input[type="text"]', 'value')
  await page.wait_for_timeout(800)
  await page.keyboard.press('Enter')
  await page.wait_for_load_state('networkidle')

================================================================
CRITICAL RULES:
1. Generate ONLY the action for THIS step (unless multi-step login — do full flow)
2. NEVER import playwright
3. NEVER create browser/page
4. NEVER close browser/page
5. ALWAYS use existing 'page' variable
6. ALWAYS wrap in try/except
7. Print "EXECUTION_SUCCESS" when done
8. Print "FAILED: {error}" on errors
9. ALWAYS use .first on any locator that could match multiple elements
10. For Gmail send — ALWAYS use Control+Enter keyboard shortcut
11. For Gmail body — ALWAYS use contenteditable locator, NOT input selectors
12. For login with email+password — generate FULL multi-step flow with wait_for_selector
13. For visual element finding — use color/title/data-* attributes per FIX 2
14. For text extraction — return text directly; it will be captured as 'text_extracted' per FIX 3
15. Avoid rapid navigation; add small waits to evade bot detection (FIX 4)

================================================================
CLICKING SEARCH RESULTS & LINKS — MANDATORY PATTERN
================================================================
When clicking a search result, link, video thumbnail, or any interactive element:

The LINKS section in the prompt shows ALL clickable links with their text.
Use a:has-text("EXACT_LINK_TEXT") to click a specific link.

EXAMPLE — Click a named link from the LINKS section (e.g. 'Cybernaut: Towards Reliable Web Automation'):
  try:
      link = page.locator('a:has-text("Cybernaut")')
      await link.first.scroll_into_view_if_needed()
      await page.wait_for_timeout(300)
      await link.first.click()
      await page.wait_for_load_state('domcontentloaded', timeout=10000)
      print("EXECUTION_SUCCESS")
  except Exception as e:
      # Fallback: JS click by partial text
      try:
          js_code = 'const links=[...document.querySelectorAll("a")];const link=links.find(a=>a.textContent.includes("Cybernaut"));if(link)link.click();else throw new Error("Link not found");'
          await page.evaluate(js_code)
          await page.wait_for_load_state('domcontentloaded', timeout=10000)
          print("EXECUTION_SUCCESS")
      except Exception as e2:
          print(f"FAILED: {e2}")

1. ALWAYS wait for the element to be visible BEFORE clicking:
     await page.wait_for_selector('h3 a', state='visible', timeout=10000)

2. Use page.locator() with human-readable text/role locators as primary strategy:
     await page.locator('h3 a').first.click()
   NOT brittle CSS selectors that break on dynamic pages.

3. After any click that triggers navigation, ALWAYS wait for load:
     await page.wait_for_load_state('domcontentloaded', timeout=10000)

4. If locator click times out, fall back to JavaScript DOM click:
     await page.evaluate('document.querySelector("h3 a").click()')

EXAMPLE — Click first Google search result:
  try:
      await page.wait_for_selector('h3', state='visible', timeout=10000)
      await page.locator('h3').first.scroll_into_view_if_needed()
      await page.wait_for_timeout(300)
      await page.locator('h3').first.click()
      await page.wait_for_load_state('domcontentloaded', timeout=10000)
      print("EXECUTION_SUCCESS")
  except Exception as e:
      # Fallback: JS click
      try:
          await page.evaluate('document.querySelector("h3 a").click()')
          await page.wait_for_load_state('domcontentloaded', timeout=10000)
          print("EXECUTION_SUCCESS")
      except Exception as e2:
          print(f"FAILED: {e2}")

================================================================
IN-PAGE NAVIGATION — DO NOT CONSTRUCT URLs
================================================================
If the browser is ALREADY on the correct domain, do NOT call page.goto().
Instead, interact with the existing page elements:
  - Type in search boxes and press Enter
  - Click links and buttons
  - Use page.go_back() for back-navigation (NOT page.goto(previous_url))

WRONG:
  await page.goto("https://www.google.com/search?q=test")  # constructs URL

CORRECT:
  await page.fill('input[name="q"]', 'test')
  await page.keyboard.press('Enter')
  await page.wait_for_load_state('domcontentloaded')

The same tab MUST be reused across all steps of a single task plan.
Never open a new tab mid-task unless the task explicitly says "open in new tab".
"""

    def _parse_response(self, response: str, contexts: List[Dict]) -> Dict:
        """Parse LLM response into structured format"""
        
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
            # No code block found
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

# ============================================================================
# BACKWARDS COMPATIBILITY
# ============================================================================

RAGSystem = PlaywrightRAGSystem
RAGConfig = PlaywrightRAGConfig

# ============================================================================
# MAIN (for testing)
# ============================================================================

if __name__ == "__main__":
    print("🎭 Playwright RAG System - Test Mode")
    print("="*80)
    
    config = PlaywrightRAGConfig()
    rag = PlaywrightRAGSystem(config)
    
    try:
        rag.initialize()
        
        # Test query
        query = "Navigate to Google and search for 'Playwright tutorial'"
        print(f"\n🧪 Test Query: {query}")
        
        result = rag.generate_code(query)
        
        print("\n" + "="*80)
        print("📝 GENERATED CODE:")
        print("="*80)
        print(result['code'])
        print("="*80)
        
        if result['explanation']:
            print("\n💡 Explanation:")
            print(result['explanation'])
        
        print(f"\n📊 Used {result['contexts_used']} contexts")
        print(f"   Top similarity: {result['top_similarity']:.2%}")
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()