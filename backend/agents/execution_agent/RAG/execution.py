# %%
# %pip install docker

# %%
# ============================================================================
# RAG PIPELINE - PART 3: SANDBOXED EXECUTION & VALIDATION
# ============================================================================
# This notebook implements safe code execution with Docker sandbox and validation

import subprocess
import docker
import tempfile
import json
import os
import time
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime
import hashlib
import ast
import sys
from enum import Enum
import logging
import re

logger = logging.getLogger(__name__)


# %%

# ============================================================================
# CELL 1: Configuration and Data Classes
# ============================================================================

class ExecutionStatus(Enum):
    """Execution status enumeration"""
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    SECURITY_VIOLATION = "security_violation"
    SYNTAX_ERROR = "syntax_error"

@dataclass
class SandboxConfig:
    """Configuration for sandbox execution"""
    
    # Docker settings
    # docker_image: str = "python:3.10-slim"
    container_name_prefix: str = "rag_sandbox"
    
    # Resource limits
    cpu_limit: float = 1.0  # CPU cores
    memory_limit: str = "512m"  # Memory limit
    timeout_seconds: int = 30  # Execution timeout
    
    # Network settings
    network_mode: str = "none"  # Isolated network
    
    # Mounted volumes
    enable_display: bool = False  # Virtual display for GUI
    
    # Security
    read_only_root: bool = True
    enable_security_check: bool = True
    
    # Validation
    require_success_indicator: bool = True
    max_retry_attempts: int = 1
    
    # Paths
    logs_dir: Path = Path("sandbox_logs")
    
    def __post_init__(self):
        # self.sandbox_dir.mkdir(exist_ok=True)
        self.logs_dir.mkdir(exist_ok=True)

@dataclass
class ExecutionResult:
    """Result of code execution"""
    status: ExecutionStatus
    exit_code: int
    stdout: str
    stderr: str
    execution_time: float
    timestamp: str
    
    # Validation
    validation_passed: bool
    validation_errors: List[str]
    
    # Security
    security_passed: bool
    security_violations: List[str]
    
    # Metadata
    code_hash: str
    retry_count: int = 0
    
    def to_dict(self) -> Dict:
        result = asdict(self)
        result['status'] = self.status.value
        return result

config = SandboxConfig()
# print(f"Sandbox Configuration Loaded")
# print(f"  Docker Image: {config.docker_image}")
# print(f"  Timeout: {config.timeout_seconds}s")
# print(f"  Memory Limit: {config.memory_limit}")
# print(f"  CPU Limit: {config.cpu_limit}")



# %%

# ============================================================================
# CELL 2: Security Validator
# ============================================================================

class SecurityValidator:
    """Validate code for security issues before execution"""
    
    def __init__(self):
        # Dangerous operations to block
        self.blocked_imports = {
            # 'os.system',
            'subprocess.Popen', 'subprocess.call',
            'eval', 'exec', '__import__',
            'socket', 'urllib', 'requests',  # Block network (except in allowed context)
            'pickle', 'shelve',  # Serialization risks
            'ctypes', 'cffi',  # Low-level system access
            # F1: Added after pentest — A4 used these to execute on host
            'shutil',
            'pathlib',
            # 'os',          # block os module import entirely
            # 'os.path',
            'winreg',      # Windows registry access
            'subprocess',  # catch bare subprocess import too
        }
        
        self.blocked_builtins = {
            'eval', 'exec', 'compile', '__import__'
        }
        
        #here this is updated
        self.dangerous_patterns = [
            'subprocess.call',
            'subprocess.Popen',
            '__import__',
            'exec(',
            'eval(',
            # F1: Added after pentest
            'shutil.rmtree',
            'shutil.move',
            'shutil.copy',
            'os.remove',
            'os.unlink',
            'os.rmdir',
            'os.system',
            'os.popen',
            # NOTE: 'pathlib.Path' removed — os.path.* calls are safe and used
            # by module_selector.py. pathlib import is blocked at the AST level.
            'ctypes.windll',
            'ctypes.wintypes',
        ]
    
    def validate_code(self, code: str) -> Tuple[bool, List[str]]:
        """
        Validate code for security issues
        
        Returns:
            (is_safe, list_of_violations)
        """
        violations = []
        
        # 1. Check for dangerous patterns
        for pattern in self.dangerous_patterns:
            if pattern in code:
                violations.append(f"Dangerous pattern detected: {pattern}")
        
        # 2. Parse AST and check for dangerous operations
        try:
            tree = ast.parse(code)
            violations.extend(self._check_ast(tree))
        except SyntaxError as e:
            violations.append(f"Syntax error: {e}")
            return False, violations
        
        # 3. Check for file system operations
        # if 'open(' in code and 'w' in code:
        #     violations.append("File write operations detected")
        
        # 4. Check for network operations
        network_keywords = ['socket', 'urllib', 'requests']
        for keyword in network_keywords:
            if keyword in code.lower():
                violations.append(f"Network operation detected: {keyword}")
        
        is_safe = len(violations) == 0
        return is_safe, violations
    
    # def _check_ast(self, tree: ast.AST) -> List[str]:
    #     """Check AST for dangerous operations"""
    #     violations = []
        
    #     for node in ast.walk(tree):
    #         # Check imports
    #         if isinstance(node, ast.Import):
    #             for alias in node.names:
    #                 if any(blocked in alias.name for blocked in [ 'subprocess', 'socket']):
    #                     violations.append(f"Blocked import: {alias.name}")
            
    #         # Check function calls
    #         if isinstance(node, ast.Call):
    #             if isinstance(node.func, ast.Name):
    #                 if node.func.id in self.blocked_builtins:
    #                     violations.append(f"Blocked builtin: {node.func.id}")
        
    #     return violations


    def _check_ast(self, tree: ast.AST) -> List[str]:
        """Check AST for dangerous operations — F1: added from-import and os method checks"""
        violations = []

        BLOCKED_BASES = {
            'shutil', 'pathlib', 'ctypes',
            'subprocess', 'winreg', 'socket',
            'urllib', 'requests', 'pickle', 'shelve', 'cffi'
            # NOTE: 'os' is intentionally NOT in this set.
            # os.startfile, os.makedirs, os.path.* are required by module_selector.py.
            # Dangerous os methods (os.system, os.remove, etc.) are blocked
            # individually in DANGEROUS_METHODS below.
        }

        for node in ast.walk(tree):

            # --- Original: Check 'import X' style ---
            if isinstance(node, ast.Import):
                for alias in node.names:
                    # Original check
                    if any(blocked in alias.name for blocked in ['subprocess', 'socket']):
                        violations.append(f"Blocked import: {alias.name}")
                    # F1: Extended check — catch all blocked modules
                    base = alias.name.split('.')[0]
                    if base in BLOCKED_BASES:
                        violations.append(f"Blocked import: {alias.name}")

            # --- F1: NEW — Check 'from X import Y' style (was completely missing) ---
            # A4 exploited this gap: "from ctypes import wintypes" slipped through
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                base = module.split('.')[0]
                if base in BLOCKED_BASES or module in BLOCKED_BASES:
                    imported_names = [alias.name for alias in node.names]
                    violations.append(
                        f"Blocked from-import: 'from {module} import "
                        f"{', '.join(imported_names)}'"
                    )

            # --- F1: NEW — Catch os/shutil method calls at call-site level ---
            if isinstance(node, ast.Call):
                # Check for os.remove(), os.unlink(), shutil.rmtree() etc.
                if isinstance(node.func, ast.Attribute):
                    obj_name = ""
                    if isinstance(node.func.value, ast.Name):
                        obj_name = node.func.value.id
                    method_name = node.func.attr

                    DANGEROUS_METHODS = {
                        'os':     {
                            # Blocked: destructive or shell-execution methods
                            'remove', 'unlink', 'rmdir', 'system', 'popen',
                            'rename', 'replace',
                            # NOTE: 'makedirs' and 'listdir' are intentionally ALLOWED.
                            # module_selector.py uses makedirs to create output folders.
                            # 'startfile' is also allowed — it opens files/apps safely.
                        },
                        'shutil': {'rmtree', 'move', 'copy', 'copy2', 'copytree',
                                   'disk_usage'},
                        'ctypes': {'windll', 'cdll', 'WinDLL'},
                    }

                    if obj_name in DANGEROUS_METHODS:
                        if method_name in DANGEROUS_METHODS[obj_name]:
                            violations.append(
                                f"Blocked method call: {obj_name}.{method_name}()"
                            )

            # --- Original: Check dangerous builtins (eval, exec, etc.) ---
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    if node.func.id in self.blocked_builtins:
                        violations.append(f"Blocked builtin: {node.func.id}")

        return violations
    
    def create_safe_wrapper(self, code: str, allow_pywinauto: bool = True) -> str:
        """
        Wrap code in safe execution environment
        
        Args:
            code: User code to wrap
            allow_pywinauto: Whether to allow pywinauto imports
        """
        wrapper = f'''
import sys
import traceback
from io import StringIO

# Restricted builtins
_safe_builtins = {{
    'print': print,
    'len': len,
    'range': range,
    'str': str,
    'int': int,
    'float': float,
    'bool': bool,
    'list': list,
    'dict': dict,
    'tuple': tuple,
    'set': set,
}}

# Capture output
_output = StringIO()
_old_stdout = sys.stdout
sys.stdout = _output

try:
    # Execute user code with restricted globals
    exec("""
{code}
""", {{'__builtins__': _safe_builtins}})
    
    sys.stdout = _old_stdout
    print("EXECUTION_SUCCESS")
    print(_output.getvalue())
    
except Exception as e:
    sys.stdout = _old_stdout
    print("EXECUTION_FAILED")
    print(f"Error: {{type(e).__name__}}: {{str(e)}}")
    traceback.print_exc()
'''
        return wrapper



# %%
# class DockerSandbox:
#     """Manage Docker-based code execution sandbox"""
    
#     def __init__(self, config: SandboxConfig):
#         self.config = config
#         try:
#             self.client = docker.from_env()
#             print("Docker client initialized")
#         except Exception as e:
#             print(f"  Docker not available: {e}")
#             print(" Install Docker: https://docs.docker.com/get-docker/")
#             self.client = None
    
#     def setup_container(self) -> Optional[docker.models.containers.Container]:
#         """Setup and start Docker container"""
#         if not self.client:
#             return None
        
#         try:
#             # Pull image if needed
#             try:
#                 self.client.images.get(self.config.docker_image)
#             except docker.errors.ImageNotFound:
#                 print(f"Pulling Docker image: {self.config.docker_image}...")
#                 self.client.images.pull(self.config.docker_image)
            
#             # Container configuration
#             container_name = f"{self.config.container_name_prefix}_{int(time.time())}"
            
#             # Create tmpfs mount for writable /tmp (security: in-memory only)
#             tmpfs = {'/tmp': 'size=100M,mode=1777'}
            
#             # Create container
#             container = self.client.containers.create(
#                 image=self.config.docker_image,
#                 name=container_name,
#                 detach=True,
#                 network_mode=self.config.network_mode,
#                 mem_limit=self.config.memory_limit,
#                 cpu_quota=int(self.config.cpu_limit * 100000),
#                 read_only=False,  
#                 tmpfs={'/tmp': 'size=100M,mode=1777'},
#                 command="tail -f /dev/null"
#             )

            
#             print(f" Container created: {container_name}")
#             return container
            
#         except Exception as e:
#             print(f"Error creating container: {e}")
#             return None
    
#     def execute_in_container(self, container, code: str, timeout: int = None) -> ExecutionResult:
#         """Execute code inside container"""
#         if timeout is None:
#             timeout = self.config.timeout_seconds
        
#         start_time = time.time()
        
#         try:
#             # Start container
#             container.start()
            
#             # Create temporary Python file
#             code_file = "/tmp/exec_code.py"
            
#             # Copy code to container
#             container.exec_run(f"bash -c 'cat > {code_file}'", stdin=True, socket=True)
            
#             # Alternative: Use tar to copy file
#             import tarfile
#             import io
            
#             # Create tar archive in memory
#             tar_stream = io.BytesIO()
#             with tarfile.open(fileobj=tar_stream, mode='w') as tar:
#                 code_data = code.encode('utf-8')
#                 tarinfo = tarfile.TarInfo(name='exec_code.py')
#                 tarinfo.size = len(code_data)
#                 tar.addfile(tarinfo, io.BytesIO(code_data))
            
#             tar_stream.seek(0)
#             container.put_archive('/tmp/', tar_stream)
            
#             # Execute Python code
#             exec_result = container.exec_run(
#                 f"python {code_file}",
#                 stdout=True,
#                 stderr=True,
#                 demux=True,
#                 stream=False,
#             )
            
#             execution_time = time.time() - start_time
            
#             # Parse output
#             stdout = exec_result.output[0].decode('utf-8') if exec_result.output[0] else ""
#             stderr = exec_result.output[1].decode('utf-8') if exec_result.output[1] else ""
            
#             # Check timeout
#             if execution_time >= timeout:
#                 status = ExecutionStatus.TIMEOUT
#             elif exec_result.exit_code == 0:
#                 status = ExecutionStatus.SUCCESS
#             else:
#                 status = ExecutionStatus.FAILED
            
#             # Create result
#             result = ExecutionResult(
#                 status=status,
#                 exit_code=exec_result.exit_code,
#                 stdout=stdout,
#                 stderr=stderr,
#                 execution_time=execution_time,
#                 timestamp=datetime.now().isoformat(),
#                 validation_passed=False,  # Will be set by validator
#                 validation_errors=[],
#                 security_passed=True,
#                 security_violations=[],
#                 code_hash=hashlib.md5(code.encode()).hexdigest()
#             )
            
#             return result
            
#         except Exception as e:
#             execution_time = time.time() - start_time
            
#             return ExecutionResult(
#                 status=ExecutionStatus.FAILED,
#                 exit_code=-1,
#                 stdout="",
#                 stderr=str(e),
#                 execution_time=execution_time,
#                 timestamp=datetime.now().isoformat(),
#                 validation_passed=False,
#                 validation_errors=[f"Container execution error: {e}"],
#                 security_passed=True,
#                 security_violations=[],
#                 code_hash=hashlib.md5(code.encode()).hexdigest()
#             )
        
#         finally:
#             # Cleanup
#             try:
#                 container.stop(timeout=5)
#                 container.remove()
#                 print(f"  Container cleaned up")
#             except:
#                 pass
    
#     def cleanup_all_containers(self):
#         """Remove all sandbox containers"""
#         if not self.client:
#             return
        
#         try:
#             containers = self.client.containers.list(
#                 all=True,
#                 filters={"name": self.config.container_name_prefix}
#             )
            
#             for container in containers:
#                 try:
#                     container.stop(timeout=2)
#                     container.remove()
#                     print(f"Removed container: {container.name}")
#                 except:
#                     pass
                    
#         except Exception as e:
#             print(f"Error during cleanup: {e}")

# %%

# ============================================================================
# CELL 4: Local Process Sandbox (Fallback)
# ============================================================================

class LocalSandbox:
    """Local process-based sandbox (fallback when Docker unavailable)"""
    
    def __init__(self, config: SandboxConfig):
        self.config = config
    
    def execute_local(self, code: str, timeout: int = None) -> ExecutionResult:
        """Execute code in local subprocess"""
        if timeout is None:
            timeout = self.config.timeout_seconds
        
        start_time = time.time()
        
        # Create temporary file
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', suffix='.py', delete=False) as f:
            f.write(code)
            temp_file = f.name
        
        try:
            # Execute in subprocess
            result = subprocess.run(
                [sys.executable, temp_file],
                capture_output=True,
                text=True,
                timeout=timeout
            )
            
            execution_time = time.time() - start_time
            
            # Determine status
            if result.returncode == 0:
                status = ExecutionStatus.SUCCESS
            else:
                status = ExecutionStatus.FAILED
            
            return ExecutionResult(
                status=status,
                exit_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                execution_time=execution_time,
                timestamp=datetime.now().isoformat(),
                validation_passed=False,
                validation_errors=[],
                security_passed=True,
                security_violations=[],
                code_hash=hashlib.md5(code.encode()).hexdigest()
            )
            
        except subprocess.TimeoutExpired:
            execution_time = time.time() - start_time
            
            return ExecutionResult(
                status=ExecutionStatus.TIMEOUT,
                exit_code=-1,
                stdout="",
                stderr=f"Execution timeout after {timeout}s",
                execution_time=execution_time,
                timestamp=datetime.now().isoformat(),
                validation_passed=False,
                validation_errors=["Execution timeout"],
                security_passed=True,
                security_violations=[],
                code_hash=hashlib.md5(code.encode()).hexdigest()
            )
        
        except Exception as e:
            execution_time = time.time() - start_time
            
            return ExecutionResult(
                status=ExecutionStatus.FAILED,
                exit_code=-1,
                stdout="",
                stderr=str(e),
                execution_time=execution_time,
                timestamp=datetime.now().isoformat(),
                validation_passed=False,
                validation_errors=[str(e)],
                security_passed=True,
                security_violations=[],
                code_hash=hashlib.md5(code.encode()).hexdigest()
            )
        
        finally:
            # Cleanup temp file
            try:
                os.unlink(temp_file)
            except:
                pass



# %%

# ============================================================================
# CELL 5: Execution Validator
# ============================================================================

class ExecutionValidator:
    """Validate execution results"""
    
    def __init__(self):
        self.success_indicators = [
            "EXECUTION_SUCCESS",
            "SUCCESS",
            "COMPLETED",
            "DONE"
        ]
        
        self.failure_indicators = [
            "Error:",
            "Exception:",
            "Traceback",
            "FAILED",
            "EXECUTION_FAILED"
        ]
    
    def validate_result(self, result: ExecutionResult, 
                    expected_output: Optional[str] = None) -> ExecutionResult:
        """
        Validate execution result
        
        Args:
            result: Execution result to validate
            expected_output: Expected output pattern (optional)
        
        Returns:
            Updated ExecutionResult with validation status
        """
        validation_errors = []
        
        # 1. Check exit code
        if result.exit_code != 0:
            validation_errors.append(f"Non-zero exit code: {result.exit_code}")
        
        # 2. Check for explicit failure messages in stdout (FAILED:, ERROR:)
        has_explicit_failure = False
        for failure_word in ['FAILED:', 'ERROR:', 'Exception occurred']:
            if failure_word in result.stdout:
                has_explicit_failure = True
                validation_errors.append(f"Code reported failure: {failure_word}")
                break
        
        # 3. Check for error indicators in stderr (but ignore debugger warnings)
        if result.stderr:
            # Filter out known harmless warnings
            stderr_clean = result.stderr
            harmless_patterns = [
                'Debugger warning',
                'frozen modules',
                'PYDEVD_DISABLE_FILE_VALIDATION',
                'Debugging will proceed'
            ]
            
            # Check if stderr has real errors (not just warnings)
            has_real_error = False
            for indicator in self.failure_indicators:
                if indicator in stderr_clean:
                    # Make sure it's not part of a harmless warning
                    if not any(harmless in stderr_clean for harmless in harmless_patterns):
                        has_real_error = True
                        validation_errors.append(f"Error indicator found in stderr: {indicator}")
                        break
        
        # 4. Check for success indicators in stdout
        has_success_indicator = False
        
        # Check for exact keyword matches
        for indicator in self.success_indicators:
            if indicator in result.stdout:
                has_success_indicator = True
                break
        
        # Also check for common success patterns like "SUCCESS:", "COMPLETED:"
        if not has_success_indicator:
            success_patterns = ['SUCCESS:', 'COMPLETED:', 'DONE:', 'successfully']
            for pattern in success_patterns:
                if pattern in result.stdout:
                    has_success_indicator = True
                    break
        
        # 5. Determine final validation based on exit code and indicators
        if result.exit_code == 0:
            # Exit code is 0 (good)
            if has_explicit_failure:
                # But code explicitly reported failure
                # Don't add another error - already added in step 2
                pass
            elif not has_success_indicator:
                # No failure message, but no success indicator either
                # Only mark as error if we don't have explicit failure already
                if not has_explicit_failure:
                    validation_errors.append("No success indicator found in output")
        
        # 6. Check expected output (if provided)
        if expected_output and expected_output not in result.stdout:
            validation_errors.append(f"Expected output not found: {expected_output}")
        
        # 7. Check for timeout
        if result.status == ExecutionStatus.TIMEOUT:
            validation_errors.append("Execution timeout")
        
        # Update result - validation passes if no errors
        result.validation_passed = len(validation_errors) == 0
        result.validation_errors = validation_errors
        
        return result
    
    def extract_action_result(self, result: ExecutionResult) -> Dict[str, Any]:
        """Extract structured action result from execution output"""
        
        action_result = {
            'success': result.validation_passed,
            'execution_time': result.execution_time,
            'timestamp': result.timestamp,
            'output': result.stdout,
            'errors': result.validation_errors + result.security_violations
        }
        
        # Try to extract structured data from output
        try:
            # Look for JSON output
            import re
            json_match = re.search(r'\{.*\}', result.stdout, re.DOTALL)
            if json_match:
                action_result['structured_output'] = json.loads(json_match.group())
        except:
            pass
        
        return action_result



# %%
# ============================================================================
# CELL 5.5: Action Cache Manager (NEW CELL - INSERT AFTER CELL 5)
# ============================================================================

class ActionCache:
    """Cache for validated action codes with semantic search"""
    
    def __init__(self, config: SandboxConfig):
        self.config = config
        self.cache_dir = Path("action_cache")
        self.cache_dir.mkdir(exist_ok=True)
        
        # ChromaDB for cache
        import chromadb
        self.client = chromadb.PersistentClient(path=str(self.cache_dir))
        
        try:
            self.collection = self.client.get_or_create_collection(
                name="action_code_cache",
                metadata={"hnsw:space": "cosine"}
            )
        except:
            self.collection = self.client.create_collection(
                name="action_code_cache",
                metadata={"hnsw:space": "cosine"}
            )
        
        # Load embedding model
        from sentence_transformers import SentenceTransformer
        model_path = Path("models/pywinauto/embedding_model")
        if model_path.exists():
            self.embedding_model = SentenceTransformer(str(model_path))
        else:
            self.embedding_model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
        
        print(f"Action cache initialized: {self.collection.count()} cached actions")
    
    def search_cache(self, query: str, threshold: float = 0.85) -> Optional[Dict]:
        """
        Search cache for similar action
        
        Args:
            query: User's action request
            threshold: Similarity threshold (0.85 = 85% match)
        
        Returns:
            Cached action dict or None
        """
        # Generate query embedding
        query_embedding = self.embedding_model.encode([query])[0]
        
        # Search cache
        results = self.collection.query(
            query_embeddings=[query_embedding.tolist()],
            n_results=1
        )
        
        if not results['documents'][0]:
            return None
        
        # Check similarity
        distance = results['distances'][0][0]
        similarity = 1 - distance
        
        if similarity >= threshold:
            print(f"Cache HIT! Similarity: {similarity:.2%}")
            
            # Parse cached data
            cached_code = results['documents'][0][0]
            metadata = results['metadatas'][0][0]
            
            return {
                'code': cached_code,
                'metadata': metadata,
                'similarity': similarity,
                'cache_hit': True
            }
        else:
            print(f"Cache MISS. Best match: {similarity:.2%} (threshold: {threshold:.0%})")
            return None
    
    def store_action(self, query: str, code: str, execution_result: ExecutionResult):
        """
        Store validated action in cache
        
        Args:
            query: User's action request
            code: Validated Python code
            execution_result: Successful execution result
        """
        # Generate embedding
        embedding = self.embedding_model.encode([query])[0]
        
        # Create unique ID
        import hashlib
        action_id = hashlib.md5(f"{query}_{datetime.now().isoformat()}".encode()).hexdigest()
        
        # Metadata
        metadata = {
            'query': query,
            'execution_time': str(execution_result.execution_time),
            'timestamp': execution_result.timestamp,
            'code_hash': execution_result.code_hash,
            'status': 'validated'
        }
        
        # Store in cache
        self.collection.add(
            ids=[action_id],
            embeddings=[embedding.tolist()],
            documents=[code],
            metadatas=[metadata]
        )
        
        print(f"Cached action: '{query}' (ID: {action_id[:8]}...)")
    
    def get_stats(self) -> Dict:
        """Get cache statistics"""
        return {
            'total_cached_actions': self.collection.count(),
            'cache_path': str(self.cache_dir)
        }

# %%

# ============================================================================
# CELL 6: Complete Sandbox Execution Pipeline
# ============================================================================

class SandboxExecutionPipeline:
    """Complete pipeline for safe code execution with optional caching"""

    # Whitelist of allowed packages for dynamic installation
    ALLOWED_PACKAGES = {
        'docx': 'python-docx',           # Word files
        'openpyxl': 'openpyxl',          # Excel files
        'pptx': 'python-pptx',           # PowerPoint files
        'selenium': 'selenium',          # Browser automation
        'requests': 'requests',          # HTTP requests
        'bs4': 'beautifulsoup4',         # Web scraping
    }

    def __init__(self, config: SandboxConfig = None, enable_cache: bool = True):
        """
        Initialize sandbox pipeline
        
        Args:
            config: Sandbox configuration
            enable_cache: Whether to enable action cache (requires ChromaDB)
        """
        self.config = config or SandboxConfig()
        self.security_validator = SecurityValidator()
        self.execution_validator = ExecutionValidator()
        self.local_sandbox = LocalSandbox(self.config)
        
        # Optional: Action cache (gracefully handle if unavailable)
        self.action_cache = None
        if enable_cache:
            try:
                logger.info("🔧 Initializing action cache...")
                self.action_cache = ActionCache(self.config)
                logger.info(f"✅ Action cache initialized: {self.action_cache.collection.count()} cached actions")
            except Exception as e:
                logger.warning(f"⚠️ Action cache not available: {e}")
                logger.info("📝 Continuing without cache support")
        
        # Execution history
        self.execution_history = []

    def _extract_imports(self, code: str) -> List[str]:
        """
        Extract module names from Python import statements.

        Returns list of top-level module names (deduplicated).
        """
        imports = set()

        try:
            tree = ast.parse(code)
        except SyntaxError:
            # If code has syntax errors, try regex fallback
            logger.warning("Failed to parse code with ast, using regex fallback")
            return self._extract_imports_regex(code)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    # Get the top-level module name
                    module_name = alias.name.split('.')[0]
                    imports.add(module_name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    # Get the top-level module name
                    module_name = node.module.split('.')[0]
                    imports.add(module_name)

        return list(imports)

    def _extract_imports_regex(self, code: str) -> List[str]:
        """Fallback regex-based import extraction"""
        imports = set()

        # Match: import x, import x.y, import x as y
        import_pattern = r'^\s*import\s+(\w+)'
        # Match: from x import y, from x.y import z
        from_pattern = r'^\s*from\s+(\w+)'

        for line in code.split('\n'):
            import_match = re.match(import_pattern, line)
            if import_match:
                imports.add(import_match.group(1))

            from_match = re.match(from_pattern, line)
            if from_match:
                imports.add(from_match.group(1))

        return list(imports)

    def _get_package_name(self, module_name: str) -> Optional[str]:
        """
        Map a module name to its pip package name.

        Returns package name if whitelisted, None otherwise.
        """
        return self.ALLOWED_PACKAGES.get(module_name)

    def _install_dependencies(self, code: str) -> bool:
        """
        Dynamically install required packages based on imports in code.

        Only installs packages that are in the whitelist.
        Silent on failure - let execution reveal import errors.

        Returns True if all installs succeeded or no installs needed.
        """
        imports = self._extract_imports(code)

        if not imports:
            logger.debug("No imports detected, skipping dependency installation")
            return True

        # Map module names to package names (only whitelisted)
        packages_to_install = []
        for module_name in imports:
            package_name = self._get_package_name(module_name)
            if package_name:
                packages_to_install.append(package_name)

        if not packages_to_install:
            logger.debug(f"No whitelisted packages needed. Detected modules: {imports}")
            return True

        # Remove duplicates while preserving install-time consistency
        packages_to_install = list(set(packages_to_install))

        logger.info(f"🔧 Installing {len(packages_to_install)} package(s): {packages_to_install}")

        all_success = True
        for package in packages_to_install:
            try:
                logger.debug(f"   Installing: {package}...")
                result = subprocess.run(
                    [sys.executable, '-m', 'pip', 'install', '--quiet', package],
                    timeout=30,
                    capture_output=True,
                    text=True
                )

                if result.returncode == 0:
                    logger.debug(f"   ✅ {package} installed successfully")
                else:
                    logger.warning(f"   ⚠️ {package} installation failed: {result.stderr[:100]}")
                    all_success = False

            except subprocess.TimeoutExpired:
                logger.warning(f"   ⚠️ {package} installation timed out")
                all_success = False
            except Exception as e:
                logger.warning(f"   ⚠️ {package} installation error: {e}")
                all_success = False

        if all_success:
            logger.info(f"✅ All dependencies installed successfully")
        else:
            logger.info(f"⚠️ Some dependencies failed to install (continuing anyway)")

        return all_success

    def execute_code(self, code: str, 
                    use_docker: bool = False,
                    expected_output: Optional[str] = None,
                    retry_on_failure: bool = True) -> ExecutionResult:
        """
        Execute code with full security and validation pipeline
        ALWAYS uses LocalSandbox (Docker disabled for server compatibility)
        """
        print("\n" + "="*80)
        print("SANDBOX EXECUTION PIPELINE")
        print("="*80)
        
        # Step 1: Security validation
        print("\n[1/5] Security Validation...")
        
        # ── NEW: Output validator code scan (DiD Layer 3) ────────────────────
        from agents.security.output_validator import validate_code as validate_code_security
        code_scan = validate_code_security(code, context="pre-execution")
        if code_scan.violations:
            logger.warning(f"🔒 Layer 3 (code scan): {len(code_scan.violations)} violation(s)")
            for v in code_scan.violations:
                logger.warning(f"   - {v}")
            # Block execution if code contains credential extraction patterns
            if any("password" in v.lower() or "credential" in v.lower() for v in code_scan.violations):
                return ExecutionResult(
                    status=ExecutionStatus.SECURITY_VIOLATION,
                    exit_code=-1,
                    stdout="",
                    stderr="Code blocked: credential extraction patterns detected",
                    execution_time=0.0,
                    timestamp=datetime.now().isoformat(),
                    validation_passed=False,
                    validation_errors=[],
                    security_passed=False,
                    security_violations=code_scan.violations,
                    code_hash=hashlib.md5(code.encode()).hexdigest()
                )
        # ─────────────────────────────────────────────────────────────────────
        
        is_safe, violations = self.security_validator.validate_code(code)
        if not is_safe:
            print(f"❌ Security validation failed:")
            for violation in violations:
                print(f"  - {violation}")
            
            return ExecutionResult(
                status=ExecutionStatus.SECURITY_VIOLATION,
                exit_code=-1,
                stdout="",
                stderr="Security validation failed",
                execution_time=0.0,
                timestamp=datetime.now().isoformat(),
                validation_passed=False,
                validation_errors=[],
                security_passed=False,
                security_violations=violations,
                code_hash=hashlib.md5(code.encode()).hexdigest()
            )
        
        print("✅ Security validation passed")

        # Step 2: Prepare code
        print("\n[2/5] Preparing code for execution...")
        wrapped_code = self._prepare_code(code)

        # Step 3: Install dependencies
        print("\n[3/5] Installing dependencies...")
        self._install_dependencies(code)

        # Step 4: Execute in LOCAL sandbox (Docker disabled)
        print("\n[4/5] Executing in sandbox...")
        print("  📍 Using LOCAL subprocess sandbox (Docker disabled)")

        result = self.local_sandbox.execute_local(wrapped_code)
        
        print(f"  Status: {result.status.value}")
        print(f"  Execution time: {result.execution_time:.3f}s")
        print(f"  Exit code: {result.exit_code}")
        
        # Step 5: Validate result
        print("\n[5/5] Validating result...")
        result = self.execution_validator.validate_result(result, expected_output)
        
        if result.validation_passed:
            print("✅ Validation passed")
        else:
            print("⚠️ Validation failed:")
            for error in result.validation_errors:
                print(f"  - {error}")
                
        # if expected_app and result.validation_passed:
        #     print("\n[5/5] Window validation...")
        #     try:
        #         from validation.fast_window_detector import get_current_window
                
        #         actual_window = get_current_window()
                
        #         if expected_app.lower() not in actual_window.process_name.lower():
        #             print(f"❌ Wrong window active!")
        #             print(f"   Expected: {expected_app}")
        #             print(f"   Got: {actual_window.process_name}")
                    
        #             result.status = ExecutionStatus.FAILED
        #             result.validation_passed = False
        #             result.validation_errors.append(
        #                 f"Expected {expected_app}, but {actual_window.process_name} is active"
        #             )
        #         else:
        #             print(f"✅ Correct window active: {actual_window.process_name}")
            
        #     except Exception as e:
        #         print(f"⚠️ Window validation skipped: {e}")
        
        # Store in history
        self.execution_history.append(result)
        
        # Save log
        self._save_execution_log(code, result)
        
        print("\n" + "="*80)
        
        return result
    
    # def _prepare_code(self, code: str) -> str:
    #     """Prepare code for execution"""
    #     has_indicator = any(
    #         keyword in code 
    #         for keyword in ["EXECUTION_SUCCESS", "SUCCESS", "COMPLETED", "DONE"]
    #     )
        
    #     if not has_indicator:
    #         prepared = code + "\n\nprint('EXECUTION_SUCCESS')\n"
    #     else:
    #         prepared = code
        
    #     return prepared

    def _prepare_code(self, code: str) -> str:
        """Prepare code for execution — F4: strip blocked imports as defence in depth"""

        # ── F4: Strip blocked imports before execution ────────────────────────
        # Even if SecurityValidator passed the code, this strips any blocked
        # imports as a final layer before subprocess execution.
        # Addresses A4 where shutil/os/ctypes ran on host.
        STRIP_IMPORTS = [
            'shutil', 'pathlib', 'os', 'ctypes',
            'subprocess', 'winreg', 'socket',
        ]

        lines = code.split('\n')
        clean_lines = []
        for line in lines:
            stripped = line.strip()
            blocked = False
            for mod in STRIP_IMPORTS:
                if (stripped.startswith(f'import {mod}') or
                        stripped.startswith(f'from {mod}') or
                        f'import {mod},' in stripped or
                        f', {mod}' in stripped):
                    logger.warning(f"🔒 F4: Stripped blocked import: {stripped}")
                    blocked = True
                    break
            if not blocked:
                clean_lines.append(line)

        code = '\n'.join(clean_lines)
        # ─────────────────────────────────────────────────────────────────────

        # Original logic: add EXECUTION_SUCCESS indicator if missing
        has_indicator = any(
            keyword in code
            for keyword in ["EXECUTION_SUCCESS", "SUCCESS", "COMPLETED", "DONE"]
        )

        if not has_indicator:
            prepared = code + "\n\nprint('EXECUTION_SUCCESS')\n"
        else:
            prepared = code

        return prepared
    
    def _save_execution_log(self, code: str, result: ExecutionResult):
        """Save execution log to file"""
        import hashlib
        import time
        
        log_file = self.config.logs_dir / f"exec_{result.code_hash}_{int(time.time())}.json"
        
        log_data = {
            'code': code,
            'result': result.to_dict(),
            'timestamp': datetime.now().isoformat()
        }
        
        with open(log_file, 'w') as f:
            json.dump(log_data, f, indent=2)
    
    def get_execution_stats(self) -> Dict[str, Any]:
        """Get execution statistics"""
        if not self.execution_history:
            return {"total_executions": 0}
        
        total = len(self.execution_history)
        successful = sum(1 for r in self.execution_history if r.validation_passed)
        failed = sum(1 for r in self.execution_history if not r.validation_passed)
        avg_time = sum(r.execution_time for r in self.execution_history) / total
        
        return {
            'total_executions': total,
            'successful': successful,
            'failed': failed,
            'success_rate': successful / total if total > 0 else 0,
            'avg_execution_time': avg_time,
            'security_violations': sum(1 for r in self.execution_history if not r.security_passed)
        }

# %%

# ============================================================================
# CELL 7: Integration with RAG System
# ============================================================================

class RAGWithSandbox:
    """Integration layer between RAG system and sandbox execution"""
    
    def __init__(self, rag_system, sandbox_pipeline: SandboxExecutionPipeline):
        self.rag = rag_system
        self.sandbox = sandbox_pipeline
        self.last_file_path = None  # Tracks active file across sequential tasks
    
    def generate_and_execute(self, user_query: str, 
                            max_retries: int = 1,
                            enable_cache: bool = True,  # ← ADD THIS
                            cache_threshold: float = 0.85) -> Dict[str, Any]:
        """
        Complete flow: Cache Check → RAG → Code Generation → Sandbox Execution
        
        Args:
            user_query: User's action request
            max_retries: Maximum retry attempts
            enable_cache: Whether to check/use cache
            cache_threshold: Similarity threshold for cache hit (0.85 = 85%)
        
        Returns:
            Execution result dictionary
        """
        print("\n" + "="*80)
        print(f"RAG + SANDBOX EXECUTION")
        print("="*80)
        print(f"Query: {user_query}")
        
        # ========================================================================
        # STEP 0: CHECK CACHE FIRST
        # ========================================================================
        # if enable_cache:
        #     print("\n[STEP 0] Checking action cache...")
        #     cached_action = self.sandbox.action_cache.search_cache(
        #         user_query, 
        #         threshold=cache_threshold
        #     )
            
        #     if cached_action:
        #         print(f"CACHE HIT! (Similarity: {cached_action['similarity']:.2%})")
        #         print(f"Skipping RAG + Sandbox - using validated code!")
                
        #         # Execute cached code directly (already validated, no sandbox needed)
        #         print("\n[EXECUTION] Running cached code...")
                
        #         import subprocess
        #         import tempfile
        #         import sys
                
        #         with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        #             f.write(cached_action['code'])
        #             temp_file = f.name
                
        #         try:
        #             start_time = time.time()
        #             result = subprocess.run(
        #                 [sys.executable, temp_file],
        #                 capture_output=True,
        #                 text=True,
        #                 timeout=30
        #             )
        #             execution_time = time.time() - start_time
                    
        #             # Create execution result
        #             exec_result = ExecutionResult(
        #                 status=ExecutionStatus.SUCCESS if result.returncode == 0 else ExecutionStatus.FAILED,
        #                 exit_code=result.returncode,
        #                 stdout=result.stdout,
        #                 stderr=result.stderr,
        #                 execution_time=execution_time,
        #                 timestamp=datetime.now().isoformat(),
        #                 validation_passed=True,  # Already validated when cached
        #                 validation_errors=[],
        #                 security_passed=True,    # Already validated when cached
        #                 security_violations=[],
        #                 code_hash=cached_action['metadata']['code_hash']
        #             )
                    
        #             print(f"✅ Cached code executed in {execution_time:.3f}s")
                    
        #             return {
        #                 'success': True,
        #                 'query': user_query,
        #                 'generated_code': cached_action['code'],
        #                 'execution_result': exec_result.to_dict(),
        #                 'rag_context': None,
        #                 'attempts': 0,  # No RAG/LLM calls needed!
        #                 'cache_hit': True,
        #                 'cache_similarity': cached_action['similarity']
        #             }
                    
        #         except Exception as e:
        #             print(f"  Cached code execution failed: {e}")
        #             print("Falling back to RAG generation...")
        #             # Continue to RAG flow below
        #         finally:
        #             import os
        #             try:
        #                 os.unlink(temp_file)
        #             except:
        #                 pass
        #     else:
        #         print(" Cache miss - proceeding with RAG generation")
        
        # ========================================================================
        # CACHE MISS - FULL RAG + SANDBOX FLOW
        # ========================================================================
        attempt = 0
        error_context = ""
        start_context_index = 0

        # Inject active file context so consecutive tasks work on the same file
        base_query = user_query
        if self.last_file_path:
            base_query = f"[ACTIVE FILE: {self.last_file_path}]\n\n{user_query}"
            print(f"[FILE CONTEXT] Active file: {self.last_file_path}")

        while attempt < max_retries:
            attempt += 1
            print(f"\n--- Attempt {attempt}/{max_retries} ---")

            # Step 1: Generate code using RAG
            print("\n[RAG] Generating code...")

            enhanced_query = base_query
            if error_context:
                enhanced_query += f"\n\nPrevious attempt failed with: {error_context}"
                enhanced_query += "\nPlease provide an alternative approach."
            
            rag_result = self.rag.generate_code(
                enhanced_query,
                cache_key=user_query,  # Use original query for cache key
                start_context_index=start_context_index,
                num_contexts=self.rag.config.top_k
            )
            
            generated_code = rag_result.get('code', '')
            
            if not generated_code:
                print(" No code generated by RAG system")
                
                if rag_result.get('contexts_used', 0) == 0:
                    print(" No more contexts available - stopping retries")
                    break
                
                start_context_index += self.rag.config.top_k
                continue
            
            print(f" Code generated ({len(generated_code)} chars)")
            print("\nGenerated Code Preview:")
            print("-" * 40)
            print(generated_code[:300] + "..." if len(generated_code) > 300 else generated_code)
            print("-" * 40)
            print("THE FULL GENERATED CODE:")
            print(generated_code)
            print("-" * 40)

            
            # Step 2: Execute in sandbox
            print("\n[SANDBOX] Executing code...")
            exec_result = self.sandbox.execute_code(
                code=generated_code,
                use_docker=False,  # Use local for speed (Docker if needed)
                retry_on_failure=False
            )
            
            # Step 3: Check result
            if exec_result.validation_passed and exec_result.security_passed:
                print("\nExecution successful!")
                
                # ================================================================
                # CACHE THE SUCCESSFUL RESULT
                # ================================================================
                # if enable_cache:
                #     print("\n[CACHING] Storing validated action...")
                #     self.sandbox.action_cache.store_action(
                #         query=user_query,
                #         code=generated_code,
                #         execution_result=exec_result
                #     )
                
                # Parse [FILE]: output to track active file for next task
                if exec_result.stdout:
                    for line in exec_result.stdout.splitlines():
                        if line.startswith('[FILE]:'):
                            self.last_file_path = line[7:].strip()
                            print(f"[FILE CONTEXT] Captured: {self.last_file_path}")
                            break

                return {
                    'success': True,
                    'query': user_query,
                    'generated_code': generated_code,
                    'execution_result': exec_result.to_dict(),
                    'rag_context': rag_result,
                    'attempts': attempt,
                    'cache_hit': False,
                    'last_file_path': self.last_file_path
                }
            
            # Failed - prepare for retry
            print(f"\n Execution failed (attempt {attempt})")
            
            error_context = f"Errors: {', '.join(exec_result.validation_errors)}"
            if exec_result.stderr:
                error_context += f" | Stderr: {exec_result.stderr[:200]}"
            
            start_context_index += self.rag.config.top_k
            
            if attempt >= max_retries:
                print("\n❌ Max retries reached")
                
                return {
                    'success': False,
                    'query': user_query,
                    'generated_code': generated_code,
                    'execution_result': exec_result.to_dict(),
                    'rag_context': rag_result,
                    'attempts': attempt,
                    'error': 'Max retries exceeded',
                    'cache_hit': False
                }
        
        # If we exit the loop without returning (all contexts exhausted)
        return {
            'success': False,
            'query': user_query,
            'generated_code': '',
            'execution_result': None,
            'rag_context': None,
            'attempts': attempt,
            'error': 'All contexts exhausted',
            'cache_hit': False
        }
        
    



# %%

# ============================================================================
# CELL 8: Usage Examples and Testing
# ============================================================================

def test_sandbox_basic():
    """Test basic sandbox functionality"""
    print("="*80)
    print("TEST 1: Basic Sandbox Execution")
    print("="*80)
    
    config = SandboxConfig(timeout_seconds=10)
    pipeline = SandboxExecutionPipeline(config)
    
    # Test code
    test_code = """
import time
import pyautogui

try:
    # Open Start Menu
    pyautogui.press('win')
    time.sleep(1)

    # Search for Word
    pyautogui.write('Word', interval=0.05)
    time.sleep(1)

    # Press Enter to open Word
    pyautogui.press('enter')

    print('SUCCESS: Word app opened')
except Exception as e:
    print(f'FAILED: {e}')
"""

    
    result = pipeline.execute_code(test_code, use_docker=False)
    
    print("\nResult:")
    print(f"  Success: {result.validation_passed}")
    print(f"  Output: {result.stdout}")
    
    return result

def test_sandbox_with_error():
    """Test sandbox with code that has errors"""
    print("\n" + "="*80)
    print("TEST 2: Error Handling")
    print("="*80)
    
    config = SandboxConfig(timeout_seconds=10)
    pipeline = SandboxExecutionPipeline(config)
    
    # Code with error
    test_code = """
print("Starting...")
x = 10 / 0  # Division by zero
print("This won't print")
"""
    
    result = pipeline.execute_code(test_code, use_docker=False)
    
    print("\nResult:")
    print(f"  Success: {result.validation_passed}")
    print(f"  Errors: {result.validation_errors}")
    print(f"  Stderr: {result.stderr}")
    
    return result

def test_sandbox_security():
    """Test security validation"""
    print("\n" + "="*80)
    print("TEST 3: Security Validation")
    print("="*80)
    
    config = SandboxConfig()
    pipeline = SandboxExecutionPipeline(config)
    
    # Malicious code
    malicious_code = """
import os
os.system("rm -rf /")  # This should be blocked!
"""
    
    result = pipeline.execute_code(malicious_code, use_docker=False)
    
    print("\nResult:")
    print(f"  Security Passed: {result.security_passed}")
    print(f"  Violations: {result.security_violations}")
    
    return result

def test_complete_rag_flow(rag_system=None, query: str=None):
    """Test complete RAG + Sandbox flow"""
    print("\n" + "="*80)
    print("TEST 4: Complete RAG + Sandbox Flow")
    print("="*80)
    
    if not rag_system:
        print("⚠️  No RAG system provided. Skipping this test.")
        print("💡 To run this test, pass your initialized RAG system:")
        print("   test_complete_rag_flow(your_rag_system)")
        return None
    
    # Initialize sandbox
    config = SandboxConfig(timeout_seconds=30)
    sandbox_pipeline = SandboxExecutionPipeline(config)
    
    # Create integrated system
    rag_sandbox = RAGWithSandbox(rag_system, sandbox_pipeline)
    
    # Test query
    # query = """press win key  and type word to open app"""

    
    # Execute complete flow
    result = rag_sandbox.generate_and_execute(query, max_retries=3)
    
    print("\n" + "="*80)
    print("FINAL RESULT")
    print("="*80)
    print(f"Success: {result['success']}")
    print(f"Attempts: {result['attempts']}")
    
    if result['success']:
        print("\n✅ Code executed successfully!")
        print("\nGenerated Code:")
        print("-" * 40)
        print(result['generated_code'])
        print("-" * 40)
    else:
        print("\nExecution failed")
        print(f"Error: {result.get('error', 'Unknown')}")
    
    # Cleanup
    # sandbox_pipeline.cleanup()
    
    return result



# %%


# %%


GROQ_API_KEY = os.environ.get("GROQ_API_KEY")


# %%
# import importlib
# import code_generation

# # Reload the module to pick up any changes
# importlib.reload(code_generation)

# # Now you can access the updated classes/functions
# from code_generation import RAGSystem, RAGConfig


# # %%
# import importlib
# import code_generation

# # Reload the module to pick up any changes
# importlib.reload(code_generation)

from agents.execution_agent.RAG.code_generation import RAGSystem, RAGConfig
# from code_generation import RAGSystem, RAGConfig

# %%

# ============================================================================
# CELL 9: Run Tests
# ============================================================================

if __name__ == "__main__":
    print("\n" + " "*20)
    print("SANDBOX EXECUTION SYSTEM - TEST SUITE")
    print(""*20)
    
    # # Run basic tests
    # print("\n\n")
    # result1 = test_sandbox_basic()
    
    # # print("\n\n")
    # result2 = test_sandbox_with_error()
    
    # # print("\n\n")
    # result3 = test_sandbox_security()
    
    # For complete RAG test, you need to provide your RAG system:
    def ensure_execution_result(r):
        if isinstance(r, ExecutionResult):
            return r
        elif isinstance(r, dict):
            return ExecutionResult(**r)
        else:
            raise TypeError("Invalid execution history item")

    

    config = RAGConfig(library_name="pywinauto")
    rag = RAGSystem(config)
    rag.initialize()
    # prompt0="open calculator and calculate 25 times 25 and copy the result and paste it in notepad"
    # result4 = test_complete_rag_flow(rag,prompt0)
    # prompt="open word doc and chooose new blank doc and type hello-habiba"
    # result4 = test_complete_rag_flow(rag,prompt)
    # prompt2="open powerpoint and navigate to new ctrl n then press enter twice and save it in the default folder"
    # result4 = test_complete_rag_flow(rag,prompt2)
    # prompt3="open powerpoint and navigate to new and open blank doc and save it in the default folder"
    # result4 = test_complete_rag_flow(rag,prompt3)
    prompt4="start whatsapp application "
    result4 = test_complete_rag_flow(rag,prompt4)
    # prompt5="search current window ctrl f for Grad-Project and navigate to it"
    # result4 = test_complete_rag_flow(rag,prompt5)
    # prompt="type message hello from agent and send it "
    # result4 = test_complete_rag_flow(rag,prompt)
    # prompt5="open word application  "
    # result4 = test_complete_rag_flow(rag,prompt5)
    


    print("\n\n" + "="*80)
    print("TEST SUITE COMPLETED")
    print("="*80)
    
    # Get statistics
    config = SandboxConfig()
    pipeline = SandboxExecutionPipeline(config)
    
    pipeline.execution_history = [result4]

#     pipeline.execution_history = [
#     ensure_execution_result(r)
#     for r in pipeline.execution_history
# ]

    
#     stats = pipeline.get_execution_stats()
#     print("\nExecution Statistics:")
#     for key, value in stats.items():
#         print(f"  {key}: {value}")
    
    # pipeline.cleanup() 


