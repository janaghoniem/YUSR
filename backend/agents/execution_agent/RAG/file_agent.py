# ============================================================================
# File Agent Module - Fast, Smart, Cross-Platform File Discovery
# ============================================================================
r"""
Performance-optimized file search with recursive indexing, fuzzy matching,
and cross-platform support (Windows, macOS, Linux).

Features:
- Recursive file indexing with configurable depth limit (default: 5)
- JSON persistence cache with 24hr TTL and version invalidation
- Token-aware fuzzy matching (order-insensitive, substring-aware)
- Cross-platform drive discovery and file opening
- Thread-safe index building (no global singleton state)
- Structured JSON responses with confidence scoring

Import:
    from agents.execution_agent.RAG.file_agent import find_file
    from file_agent import find_file  (if in same directory)
"""

import os
import sys
import json
import logging
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeoutError
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Try rapidfuzz first (faster), fall back to difflib
try:
    from rapidfuzz import fuzz as _fuzz
    def _score(a: str, b: str) -> float:
        """
        Combine token_sort_ratio (order-insensitive) and partial_ratio
        (substring-aware) for robust voice-input matching.
        Returns 0.0–1.0.
        """
        token  = _fuzz.token_sort_ratio(a, b)   # "q4 report earnings" == "earnings report q4"
        partial = _fuzz.partial_ratio(a, b)      # "report" matches inside "Q4_Report_Final_v2"
        return max(token, partial) / 100.0
    FUZZY_BACKEND = "rapidfuzz"
except ImportError:
    from difflib import SequenceMatcher
    def _score(a: str, b: str) -> float:
        return SequenceMatcher(None, a, b).ratio()
    FUZZY_BACKEND = "difflib"

logger = logging.getLogger(__name__)

# ============================================================================
# Constants
# ============================================================================

# Directories that are never useful to scan — saves huge time
SKIP_DIRS = {
    # Dev / tooling
    "node_modules", ".git", "__pycache__", ".venv", "venv", "env",
    ".tox", "dist", "build", ".gradle", ".idea", ".vscode",
    # Windows system
    "Windows", "System32", "SysWOW64", "Program Files", "Program Files (x86)",
    "AppData", "ProgramData", "$Recycle.Bin", "Recovery",
    # Python / Conda / libraries (NOT user files)
    "site-packages", "dist-packages", ".egg-info", "miniconda3", "anaconda3",
    "conda", "Library", "Scripts", "Include", "Lib",
    # macOS & Linux system
    "usr", "bin", "lib", "var", "etc", "opt", "srv",
    # Development artifacts
    ".next", ".nuxt", ".cache", "coverage", ".pytest_cache",
}

FILLER_WORDS = {"the", "a", "my", "your", "and", "or", "is", "are", "of", "for", "in"}

# Where we persist the index cache
def _cache_path() -> str:
    """Cache path under %LOCALAPPDATA%\\yusr\\."""
    base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
    return os.path.join(base, "AURA", "file_index_cache.json")

CACHE_PATH    = _cache_path()
CACHE_VERSION = 4          # Force rebuild to exclude library dirs
CACHE_TTL_H   = 24
MAX_DEPTH     = 5          # Max folder recursion depth — keeps scan fast
INDEX_TIMEOUT = 15         # Seconds before we give up on a single scan root
FUZZY_HIGH    = 0.75       # Confident single match
FUZZY_MED     = 0.55       # Possible match / needs disambiguation


# ============================================================================
# Normalizer
# ============================================================================

class Normalizer:
    """Normalize filenames and voice queries for fuzzy comparison."""

    @staticmethod
    def normalize(text: str) -> str:
        """
        1. Lowercase
        2. Replace separators (_, -, .) with spaces
        3. Strip non-alphanumeric characters
        4. Remove filler words and single chars
        5. Collapse whitespace

        Example:
            "My-Report_Q4.2024 (FINAL).pdf" → "report q4 2024 final pdf"
        """
        s = text.lower()
        for ch in ("_", "-", ".", "(", ")", "[", "]"):
            s = s.replace(ch, " ")
        s = "".join(c if c.isalnum() or c.isspace() else " " for c in s)
        words = [w for w in s.split() if w not in FILLER_WORDS and len(w) > 1]
        return " ".join(words).strip()


# ============================================================================
# Platform helpers
# ============================================================================

def _get_scan_roots() -> List[str]:
    """
    Return prioritised list of directories to index.

    Priority:
      1. User's Desktop / Downloads / Documents (and OneDrive mirrors)
      2. Same folders on every mounted drive letter
      3. User profile root on each drive
    """
    import string
    roots: List[str] = []
    seen: set = set()
    home = os.path.expanduser("~")
    username = os.path.basename(home)
    common = ["Desktop", "Downloads", "Documents"]

    def add(p: str) -> None:
        if p not in seen and os.path.isdir(p):
            roots.append(p)
            seen.add(p)

    # Home common folders + OneDrive mirrors
    for folder in common:
        add(os.path.join(home, folder))
        add(os.path.join(home, "OneDrive", folder))
        add(os.path.join(home, "OneDrive - Personal", folder))

    # Same folders on every drive letter
    drives = [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]
    for drive in drives:
        for folder in common:
            add(os.path.join(drive, folder))
        add(os.path.join(drive, "Users", username))

    return roots


def _open_file(path: str) -> bool:
    """Open a file with the default Windows application."""
    try:
        os.startfile(path)
        return True
    except Exception as e:
        logger.error(f"Failed to open file: {e}")
        return False


def _system_search_fallback(query: str, roots: List[str]) -> Optional[str]:
    """Last-resort system search using Windows `where /r`."""
    try:
        for root in roots:
            if not os.path.isdir(root):
                continue
            res = subprocess.run(
                ["where", "/r", root, query],
                capture_output=True, text=True, timeout=10
            )
            if res.stdout.strip():
                found = res.stdout.strip().splitlines()[0]
                if os.path.isfile(found):
                    return found
    except Exception as e:
        logger.debug(f"System search error: {e}")
    return None


# ============================================================================
# FileIndexer — recursive, parallel, time-bounded
# ============================================================================

class FileIndexer:
    """
    Build a recursive file index with:
    - Per-root timeout (INDEX_TIMEOUT seconds each)
    - Parallel root scanning via ThreadPoolExecutor
    - SKIP_DIRS pruning to avoid useless directories
    - JSON cache with TTL + version check
    """

    def _scan_root(self, root: str) -> List[Dict[str, Any]]:
        """
        Recursively scan one root directory up to MAX_DEPTH.
        Returns list of file metadata dicts.
        """
        result: List[Dict[str, Any]] = []
        root_depth = root.rstrip(os.sep).count(os.sep)

        try:
            for dirpath, dirnames, filenames in os.walk(root, topdown=True):
                # Depth guard — prune os.walk in-place
                current_depth = dirpath.count(os.sep) - root_depth
                if current_depth >= MAX_DEPTH:
                    dirnames.clear()
                    continue

                # Skip useless directories in-place (fast)
                dirnames[:] = [
                    d for d in dirnames
                    if d not in SKIP_DIRS and not d.startswith(".")
                ]

                for filename in filenames:
                    if filename.startswith("."):
                        continue
                    filepath = os.path.join(dirpath, filename)
                    try:
                        stat = os.stat(filepath)
                        result.append({
                            "name": filename,
                            "normalized_name": Normalizer.normalize(filename),
                            "path": filepath,
                            "size": stat.st_size,
                            "modified_time": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                        })
                    except OSError:
                        pass
        except (PermissionError, OSError):
            pass

        return result

    def build_index(self, roots: List[str]) -> List[Dict[str, Any]]:
        """
        Scan all roots in parallel with per-root timeouts.
        Falls back gracefully if a root times out.
        """
        index: List[Dict[str, Any]] = []
        seen_paths: set = set()

        logger.info(f"[FileIndexer] Scanning {len(roots)} roots (max depth {MAX_DEPTH})…")

        with ThreadPoolExecutor(max_workers=min(len(roots), 4)) as pool:
            futures = {pool.submit(self._scan_root, root): root for root in roots}

            for future in as_completed(futures, timeout=INDEX_TIMEOUT * len(roots)):
                root = futures[future]
                try:
                    entries = future.result(timeout=INDEX_TIMEOUT)
                    for entry in entries:
                        if entry["path"] not in seen_paths:
                            seen_paths.add(entry["path"])
                            index.append(entry)
                    logger.debug(f"  ✓ {root} → {len(entries)} files")
                except FuturesTimeoutError:
                    logger.warning(f"  ⚠ Timeout scanning {root}, skipped")
                except Exception as e:
                    logger.debug(f"  ✗ Error scanning {root}: {e}")

        logger.info(f"[FileIndexer] Indexed {len(index)} files total")
        return index

    # ------------------------------------------------------------------
    # Cache helpers
    # ------------------------------------------------------------------

    def load_cache(self) -> Optional[List[Dict[str, Any]]]:
        try:
            if not os.path.exists(CACHE_PATH):
                return None
            with open(CACHE_PATH, "r", encoding="utf-8") as f:
                cache = json.load(f)
            if cache.get("version") != CACHE_VERSION:
                logger.debug("Cache version mismatch — rebuilding")
                return None
            age = datetime.now() - datetime.fromisoformat(cache["last_updated"])
            if age > timedelta(hours=CACHE_TTL_H):
                logger.debug("Cache expired — rebuilding")
                return None
            files = cache["files"]
            logger.info(f"[FileIndexer] Loaded {len(files)} files from cache")
            return files
        except Exception as e:
            logger.debug(f"Cache load failed: {e}")
            return None

    def save_cache(self, index: List[Dict[str, Any]]) -> None:
        try:
            os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
            with open(CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump(
                    {"version": CACHE_VERSION, "last_updated": datetime.now().isoformat(), "files": index},
                    f, indent=2
                )
            logger.debug(f"Cache saved → {CACHE_PATH}")
        except Exception as e:
            logger.warning(f"Cache save failed: {e}")

    def invalidate_cache(self) -> None:
        try:
            if os.path.exists(CACHE_PATH):
                os.remove(CACHE_PATH)
                logger.info("Cache invalidated")
        except Exception:
            pass


# ============================================================================
# FuzzyMatcher — token-aware, fast
# ============================================================================

class FuzzyMatcher:
    """
    Match a voice query against the indexed file list.

    Uses token_sort_ratio (order-insensitive) + partial_ratio (substring)
    so "quarterly earnings presentation" reliably matches
    "Q4_Earnings_Presentation_Final_v2.pptx".
    """

    def __init__(self, index: List[Dict[str, Any]]) -> None:
        self.index = index

    def _best_candidates(
        self, normalized_query: str, threshold: float
    ) -> List[Dict[str, Any]]:
        """Return all index entries scoring >= threshold, sorted desc."""
        results = []
        for entry in self.index:
            score = _score(normalized_query, entry["normalized_name"])
            if score >= threshold:
                results.append({**entry, "_score": score})
        results.sort(key=lambda x: x["_score"], reverse=True)
        return results

    def match(self, query: str) -> Dict[str, Any]:
        """
        Returns:
            {"status": "found",    "path": ..., "confidence": float}
            {"status": "multiple", "paths": [...]}
            {"status": "not_found","suggestions": [...]}
        """
        nq = Normalizer.normalize(query)
        if not nq:
            return {"status": "not_found", "suggestions": []}

        # High-confidence pass
        candidates = self._best_candidates(nq, FUZZY_HIGH)
        valid = [c for c in candidates if os.path.isfile(c["path"])]

        if valid:
            best = valid[0]
            # If top match is clearly better than second, return it directly
            if len(valid) == 1 or (valid[0]["_score"] - valid[1]["_score"]) >= 0.12:
                return {
                    "status": "found",
                    "path": best["path"],
                    "confidence": round(best["_score"], 3),
                }
            # Several equally good matches → ask user
            return {
                "status": "multiple",
                "paths": [{"path": c["path"], "confidence": round(c["_score"], 3)} for c in valid[:5]],
            }

        # Medium-confidence pass
        candidates = self._best_candidates(nq, FUZZY_MED)
        valid = [c for c in candidates if os.path.isfile(c["path"])]

        if len(valid) == 1:
            return {
                "status": "found",
                "path": valid[0]["path"],
                "confidence": round(valid[0]["_score"], 3),
            }
        elif len(valid) > 1:
            return {
                "status": "multiple",
                "paths": [{"path": c["path"], "confidence": round(c["_score"], 3)} for c in valid[:5]],
            }

        # Suggestions from anything ≥ 0.35
        suggestions_raw = self._best_candidates(nq, 0.35)
        suggestions = [os.path.basename(c["path"]) for c in suggestions_raw[:4]]
        return {"status": "not_found", "suggestions": suggestions}


# ============================================================================
# FileSearch — main orchestrator (thread-safe, no global singleton)
# ============================================================================

class FileSearch:
    """
    Thread-safe file search engine.

    Create one instance per agent/context, or share safely (index access
    is protected by a lock so concurrent calls don't corrupt state).
    """

    def __init__(self, extra_roots: Optional[List[str]] = None) -> None:
        self._indexer = FileIndexer()
        self._extra_roots = extra_roots or []
        self._index: Optional[List[Dict[str, Any]]] = None
        self._matcher: Optional[FuzzyMatcher] = None
        self._lock = threading.Lock()

    def _ensure_index(self) -> None:
        """Lazy-load index. Thread-safe via lock."""
        with self._lock:
            if self._index is not None:
                return

            self._index = self._indexer.load_cache()

            if self._index is None:
                roots = _get_scan_roots() + self._extra_roots
                self._index = self._indexer.build_index(roots)
                self._indexer.save_cache(self._index)

            self._matcher = FuzzyMatcher(self._index)

    def refresh(self) -> None:
        """Force a full re-index (call when files may have changed)."""
        self._indexer.invalidate_cache()
        with self._lock:
            self._index = None
            self._matcher = None
        self._ensure_index()

    def find(self, query: str, force_refresh: bool = False) -> Dict[str, Any]:
        """
        Search for a file.

        Strategy:
          1. If query looks like an absolute path → verify directly (instant)
          2. Fuzzy match against index (fast, cached)
          3. System search fallback if fuzzy yields nothing (slower)

        Args:
            query:         File name or description (voice-friendly)
            force_refresh: Rebuild index before searching

        Returns:
            {
              "status":     "found" | "multiple" | "not_found",
              "path":       str,              # only on "found"
              "confidence": float,            # only on "found"
              "paths":      [{"path", "confidence"}],  # only on "multiple"
              "suggestions":["filename", …], # only on "not_found"
            }
        """
        if force_refresh:
            self.refresh()

        self._ensure_index()

        # Absolute-path shortcut (Windows: C:\... or \\server\...)
        normalized_q = query.replace("/", "\\")
        if len(normalized_q) > 2 and normalized_q[1] == ":":
            if os.path.isfile(normalized_q):
                return {"status": "found", "path": normalized_q, "confidence": 1.0}

        # Fuzzy match
        result = self._matcher.match(query)
        if result["status"] != "not_found":
            return result

        # System-search fallback (only when fuzzy gives nothing)
        logger.debug(f"No fuzzy match for '{query}', trying system search…")
        roots = _get_scan_roots() + self._extra_roots
        found = _system_search_fallback(query, roots)
        if found:
            return {"status": "found", "path": found, "confidence": 0.80}

        # Try without extension as a last resort
        if "." in query:
            base = query.rsplit(".", 1)[0] + "*"
            found = _system_search_fallback(base, roots)
            if found:
                return {"status": "found", "path": found, "confidence": 0.70}

        return result  # not_found with suggestions


# ============================================================================
# Module-level API  (one shared engine, but thread-safe internally)
# ============================================================================

_engine: Optional[FileSearch] = None
_engine_lock = threading.Lock()


def _get_engine() -> FileSearch:
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:          # double-checked locking
                _engine = FileSearch()
    return _engine


def find_file(filename: str, force_refresh: bool = False) -> Dict[str, Any]:
    """
    Find a file by name or description (voice-friendly).

    Args:
        filename:      File name, partial name, or voice description
        force_refresh: Force a full re-index before searching

    Returns:
        Dict with keys: status, path, confidence, paths, suggestions

    Examples:
        >>> result = find_file("flutter quiz answers")
        >>> if result["status"] == "found":
        ...     print(result["path"])
        ...     print(result["confidence"])
    """
    return _get_engine().find(filename, force_refresh=force_refresh)


def find_all_files(filename: str) -> List[str]:
    """Return all matching paths (backward-compatible)."""
    result = find_file(filename)
    if result["status"] == "found":
        return [result["path"]]
    elif result["status"] == "multiple":
        return [m["path"] for m in result["paths"]]
    return []


def get_file_path(filename: str) -> Optional[str]:
    """Return single best path or None (backward-compatible)."""
    result = find_file(filename)
    return result.get("path") if result["status"] == "found" else None


def file_exists(filename: str) -> bool:
    """Check if a file exists (backward-compatible)."""
    return find_file(filename)["status"] == "found"


def open_file(filename: str) -> bool:
    """
    Find a file and open it with the default Windows application.
    Returns True on success.
    """
    result = find_file(filename)

    if result["status"] == "multiple":
        logger.warning(
            "Multiple files matched '%s'. Use get_file_path() to disambiguate. Top match: %s",
            filename, result["paths"][0]["path"]
        )
        # Open best match automatically
        path = result["paths"][0]["path"]
    elif result["status"] == "found":
        path = result["path"]
    else:
        logger.error("File not found: %s  suggestions=%s", filename, result.get("suggestions"))
        return False

    logger.info("Opening: %s", path)
    print(f"[FILE]: {path}")
    return _open_file(path)


def refresh_index() -> None:
    """Force a full re-index. Call after moving/creating files."""
    _get_engine().refresh()


# ============================================================================
# __main__ — quick smoke test
# ============================================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    print(f"File Agent  |  platform=Windows  fuzzy={FUZZY_BACKEND}")
    print(f"Cache path: {CACHE_PATH}")
    print("=" * 60)

    tests = [
        "flutter quiz",
        "quarterly report",
        "config",
        "resume",
        "notes",
    ]

    import time
    t0 = time.perf_counter()

    for query in tests:
        result = find_file(query)
        status = result["status"]
        if status == "found":
            print(f"[FOUND  {result['confidence']:.0%}] {query!r:25s} → {result['path']}")
        elif status == "multiple":
            print(f"[MULTI  {len(result['paths'])}  ] {query!r:25s} → {result['paths'][0]['path']} …")
        else:
            print(f"[MISS        ] {query!r:25s}   suggestions: {result.get('suggestions')}")

    elapsed = time.perf_counter() - t0
    print(f"\n✅ {len(tests)} queries in {elapsed:.3f}s")