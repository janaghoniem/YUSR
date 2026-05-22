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
import atexit
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeoutError
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Callable

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
}

# Prefix-based skip — any directory whose name STARTS WITH one of these is skipped.
# Catches versioned names like miniconda3, anaconda3, Python311, etc.
SKIP_DIR_PREFIXES = (
    "miniconda", "anaconda", "python3", "python2",
    "Library", "site-packages", "dist-info",
)

FILLER_WORDS = {"the", "a", "my", "your", "and", "or", "is", "are", "of", "for", "in"}

# Directories considered user-owned — files here get a score boost
USER_PRIORITY_DIRS = {"Desktop", "Downloads", "Documents", "OneDrive", "Google Drive"}

# Where we persist the index cache
def _cache_path() -> str:
    """Cache path under %LOCALAPPDATA%\\yusr\\."""
    base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
    return os.path.join(base, "yusr", "file_index_cache.json")

CACHE_PATH    = _cache_path()
CACHE_VERSION = 3          # Bump when index schema changes
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

def _get_onedrive_roots() -> List[str]:
    """
    Detect ALL OneDrive folders for this user — including custom-named ones
    like "OneDrive - Habiba - Personal" — using three strategies:

    1. Windows registry (most reliable): reads UserFolder key from OneDrive
       account entries, which always points to the real local sync path.
    2. Filesystem scan of home dir: any folder starting with "OneDrive"
       covers variants like "OneDrive - Personal", "OneDrive - Contoso".
    3. Environment variable ONEDRIVE / ONEDRIVECOMMERCIAL as a fallback.

    Only returns paths that actually exist on disk (i.e. are synced locally).
    Cloud-only files (☁️ icon in Explorer) are NOT on disk and cannot be found.
    """
    found: List[str] = []
    seen: set = set()
    home = os.path.expanduser("~")

    def add(p: str) -> None:
        if p not in seen and os.path.isdir(p):
            found.append(p)
            seen.add(p)

    # Strategy 1: Registry — reads actual sync paths registered by OneDrive
    try:
        import winreg
        key_path = r"Software\Microsoft\OneDrive\Accounts"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as accounts_key:
            i = 0
            while True:
                try:
                    account_name = winreg.EnumKey(accounts_key, i)
                    with winreg.OpenKey(accounts_key, account_name) as acc_key:
                        try:
                            user_folder, _ = winreg.QueryValueEx(acc_key, "UserFolder")
                            add(user_folder)
                            logger.debug(f"[OneDrive] Registry found: {user_folder}")
                        except FileNotFoundError:
                            pass
                    i += 1
                except OSError:
                    break
    except Exception as e:
        logger.debug(f"[OneDrive] Registry read failed: {e}")

    # Strategy 2: Filesystem — scan home for any OneDrive-prefixed folder
    try:
        for entry in os.scandir(home):
            if entry.is_dir() and entry.name.startswith("OneDrive"):
                add(entry.path)
                logger.debug(f"[OneDrive] Filesystem found: {entry.path}")
    except Exception as e:
        logger.debug(f"[OneDrive] Filesystem scan failed: {e}")

    # Strategy 3: Environment variables
    for env_var in ("ONEDRIVE", "ONEDRIVECOMMERCIAL", "ONEDRIVECONSUMER"):
        path = os.environ.get(env_var, "")
        if path:
            add(path)
            logger.debug(f"[OneDrive] Env var {env_var}: {path}")

    return found


def _get_scan_roots() -> List[str]:
    """
    Return prioritised list of directories to index.

    Priority:
      1. User common folders: Desktop, Downloads, Documents (direct in home)
      2. All OneDrive roots (registry + filesystem + env var detection)
         and their Desktop/Downloads/Documents subfolders
      3. Same common folders on every mounted drive letter
      4. User profile root on each drive (shallow — catches edge cases)
    """
    import string
    roots: List[str] = []
    seen: set = set()
    home = os.path.expanduser("~")
    username = os.path.basename(home)
    common = ["Desktop", "Downloads", "Documents", "Pictures"]

    def add(p: str) -> None:
        if p not in seen and os.path.isdir(p):
            roots.append(p)
            seen.add(p)

    # 1. Direct home common folders
    for folder in common:
        add(os.path.join(home, folder))

    # 2. All OneDrive roots + their subfolders
    for onedrive_root in _get_onedrive_roots():
        add(onedrive_root)                              # root itself
        for folder in common:
            add(os.path.join(onedrive_root, folder))   # subfolders inside

    # 3. Common folders on every drive letter
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


def _full_drive_search(query: str) -> Optional[str]:
    """
    Nuclear fallback — runs `where /r` from every drive root (C:\\, D:\\, …).
    Slow (5-30s per drive) but finds anything on the machine.
    Only called when the index AND common-folder search both fail.
    Each drive gets its own 60s timeout so one huge drive can't block others.
    """
    import string
    drives = [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]
    logger.info(f"[FullDriveSearch] Scanning {len(drives)} drive(s) for '{query}'…")

    for drive in drives:
        try:
            logger.debug(f"  where /r {drive} {query}")
            res = subprocess.run(
                ["where", "/r", drive, query],
                capture_output=True, text=True, timeout=60
            )
            if res.stdout.strip():
                found = res.stdout.strip().splitlines()[0]
                if os.path.isfile(found):
                    logger.info(f"  ✅ Found on full-drive search: {found}")
                    return found
        except subprocess.TimeoutExpired:
            logger.warning(f"  ⚠ Timeout scanning drive {drive}, skipping")
        except Exception as e:
            logger.debug(f"  ✗ Error on {drive}: {e}")

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
                    if d not in SKIP_DIRS
                    and not d.startswith(".")
                    and not any(d.startswith(p) for p in SKIP_DIR_PREFIXES)
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


    def _is_user_file(self, path: str) -> tuple:
        """
        Check if file is in user-priority directory.
        Returns: (is_user_priority, boost_factor)
        User-priority (boost 1.2x): Desktop, Downloads, Documents, OneDrive
        System-file  (penalize 0.4x): site-packages, miniconda, Windows system
        """
        path_lower = path.lower()
        for user_dir in USER_PRIORITY_DIRS:
            if user_dir.lower() in path_lower:
                return (True, 1.2)
        system_markers = ["site-packages", "miniconda", "anaconda", "appdata",
                          "program files", "windows\\system", "\\lib\\", "\\.egg-info"]
        for marker in system_markers:
            if marker in path_lower:
                return (False, 0.4)
        return (False, 1.0)

    def exact_match(self, query: str) -> Dict[str, Any]:
        """
        Stage 0 — exact filename match (case-insensitive).
        Checks every indexed entry whose filename equals the query exactly.
        Returns immediately on the first hit from a user-priority directory,
        or the best-location hit if multiple identical filenames exist.

        Returns:
            {"status": "found", "path": ..., "confidence": 1.0}
            {"status": "not_found", "suggestions": []}
        """
        query_lower = query.strip().lower()
        # Also try matching without extension: "report" should find "report.txt"
        query_no_ext = query_lower.rsplit(".", 1)[0] if "." in query_lower else None
        hits = []

        for entry in self.index:
            name_lower = entry["name"].lower()
            name_no_ext = name_lower.rsplit(".", 1)[0] if "." in name_lower else name_lower

            # Exact name match (with or without extension on query side)
            is_exact = (name_lower == query_lower) or (query_no_ext and name_no_ext == query_no_ext and "." in query_lower)

            if is_exact and os.path.isfile(entry["path"]):
                _, location_boost = self._is_user_file(entry["path"])
                # Full name match scores higher than extension-stripped match
                score_boost = location_boost * (1.0 if name_lower == query_lower else 0.95)
                hits.append((entry["path"], score_boost))

        if not hits:
            return {"status": "not_found", "suggestions": []}

        # Sort by location priority — user dirs first (boost=1.2 > 1.0 > 0.4)
        hits.sort(key=lambda x: x[1], reverse=True)
        best_path = hits[0][0]

        if len(hits) == 1:
            logger.info(f"[ExactMatch] Found: {best_path}")
            return {"status": "found", "path": best_path, "confidence": 1.0}
        else:
            # Multiple files with the same name — return all for disambiguation
            logger.info(f"[ExactMatch] {len(hits)} files named '{query}' found")
            return {
                "status": "multiple",
                "paths": [{"path": p, "confidence": 1.0} for p, _ in hits[:5]],
            }

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

    def _add_to_index(self, path: str) -> None:
        """
        Add a newly discovered file (from full-drive search) to the live index
        and persist the updated cache so the next search finds it instantly.
        """
        filename = os.path.basename(path)
        entry = {
            "name": filename,
            "normalized_name": Normalizer.normalize(filename),
            "path": path,
            "size": os.path.getsize(path),
            "modified_time": datetime.fromtimestamp(os.path.getmtime(path)).isoformat(),
        }
        with self._lock:
            if any(e["path"] == path for e in self._index):
                return  # already present
            self._index.append(entry)
            self._matcher = FuzzyMatcher(self._index)   # rebuild matcher
        self._indexer.save_cache(self._index)            # persist so next run is fast
        logger.debug(f"[Index] Added new entry: {path}")

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

        # Stage 0 — exact filename match (fastest, highest confidence)
        # Must run BEFORE fuzzy so "report.txt" never loses to "REPORT TITLE.docx"
        exact = self._matcher.exact_match(query)
        if exact["status"] != "not_found":
            logger.info(f"[Find] Exact match hit for '{query}'")
            return exact

        # Stage 1 — fuzzy match
        result = self._matcher.match(query)
        if result["status"] != "not_found":
            return result

        # System-search fallback (only when fuzzy gives nothing)
        logger.debug(f"No fuzzy match for '{query}', trying system search…")
        roots = _get_scan_roots() + self._extra_roots
        found = _system_search_fallback(query, roots)
        if found:
            return {"status": "found", "path": found, "confidence": 0.80}

        # Try without extension as a last resort (still common-folder scope)
        if "." in query:
            base = query.rsplit(".", 1)[0] + "*"
            found = _system_search_fallback(base, roots)
            if found:
                return {"status": "found", "path": found, "confidence": 0.70}

        # Stage 3 — full drive search (slow, whole machine, true last resort)
        logger.info(f"[Find] Common folders exhausted, escalating to full-drive search for '{query}'")
        found = _full_drive_search(query)
        if found:
            self._add_to_index(found)
            return {"status": "found", "path": found, "confidence": 0.75}

        if "." in query:
            base = query.rsplit(".", 1)[0] + "*"
            found = _full_drive_search(base)
            if found:
                self._add_to_index(found)
                return {"status": "found", "path": found, "confidence": 0.65}

        return result  # not_found with suggestions


# ============================================================================
# PeriodicRefresher — background thread for index updates
# ============================================================================

class PeriodicRefresher:
    """
    Background thread that refreshes the file index at regular intervals.
    Useful for detecting newly downloaded files or created files.
    """

    def __init__(self, interval_seconds: int = 300, on_refresh: Optional[Callable] = None):
        """
        Args:
            interval_seconds: How often to refresh index (default: 5 min)
            on_refresh: Optional callback after refresh completes
        """
        self.interval = interval_seconds
        self.on_refresh = on_refresh
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._running = False

    def start(self, file_search: "FileSearch") -> None:
        """Start background refresh thread."""
        if self._running:
            logger.warning("[PeriodicRefresher] Already running")
            return

        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._refresh_loop,
            args=(file_search,),
            daemon=False,
            name="FileIndexRefresher"
        )
        self._thread.start()
        logger.info(f"[PeriodicRefresher] Started (interval={self.interval}s)")

    def _refresh_loop(self, file_search: "FileSearch") -> None:
        """Background loop that refreshes the index periodically."""
        while not self._stop_event.is_set():
            try:
                # Wait for the interval or until stop is signaled
                if self._stop_event.wait(timeout=self.interval):
                    break  # Stop was signaled

                logger.debug("[PeriodicRefresher] Triggering refresh…")
                file_search.refresh()
                logger.debug("[PeriodicRefresher] Refresh complete")

                if self.on_refresh:
                    self.on_refresh()

            except Exception as e:
                logger.error(f"[PeriodicRefresher] Error during refresh: {e}")

    def stop(self) -> None:
        """Stop the background thread gracefully."""
        if not self._running:
            return

        logger.info("[PeriodicRefresher] Stopping…")
        self._stop_event.set()

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

        self._running = False
        logger.info("[PeriodicRefresher] Stopped")


# ============================================================================
# Module-level API  (one shared engine, but thread-safe internally)
# ============================================================================

_engine: Optional[FileSearch] = None
_engine_lock = threading.Lock()
_refresher: Optional[PeriodicRefresher] = None
_refresher_lock = threading.Lock()


def _get_engine() -> FileSearch:
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:          # double-checked locking
                _engine = FileSearch()
    return _engine


def _get_refresher() -> PeriodicRefresher:
    global _refresher
    if _refresher is None:
        with _refresher_lock:
            if _refresher is None:
                _refresher = PeriodicRefresher(interval_seconds=300)  # 5 minutes default
    return _refresher


def _cleanup_refresher():
    """Cleanup function to stop refresher on exit."""
    global _refresher
    if _refresher is not None:
        _refresher.stop()


atexit.register(_cleanup_refresher)


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


def preload_index(extra_roots: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Preload and build the file index at startup.

    This function explicitly loads the cache if available, or builds a fresh
    index from all configured scan roots. Use this at server startup to ensure
    the index is ready before any find_file() calls.

    Args:
        extra_roots: Additional root directories to scan (optional)

    Returns:
        {
            "status": "loaded" | "built",
            "file_count": int,
            "timestamp": str (ISO format),
            "cache_path": str
        }

    Examples:
        >>> result = preload_index()
        >>> print(f"Index ready: {result['file_count']} files")

        >>> preload_index(["/path/to/downloads", "/path/to/custom"])
    """
    engine = _get_engine()
    indexer = engine._indexer

    start_time = datetime.now()

    # Try to load from cache first (fast)
    index = indexer.load_cache()

    if index is not None:
        logger.info(f"[PreloadIndex] ✅ Loaded from cache: {len(index)} files")
        with engine._lock:
            engine._index = index
            engine._matcher = FuzzyMatcher(index)

        return {
            "status": "loaded",
            "file_count": len(index),
            "timestamp": start_time.isoformat(),
            "cache_path": CACHE_PATH,
        }

    # Cache miss or expired — build fresh index
    logger.info("[PreloadIndex] Cache miss or expired, building fresh index…")
    roots = _get_scan_roots() + (extra_roots or [])
    index = indexer.build_index(roots)
    indexer.save_cache(index)

    with engine._lock:
        engine._index = index
        engine._matcher = FuzzyMatcher(index)

    elapsed = (datetime.now() - start_time).total_seconds()
    logger.info(f"[PreloadIndex] ✅ Built fresh index: {len(index)} files in {elapsed:.2f}s")

    return {
        "status": "built",
        "file_count": len(index),
        "timestamp": start_time.isoformat(),
        "cache_path": CACHE_PATH,
        "build_time_seconds": elapsed,
    }


def start_periodic_refresh(
    interval_seconds: int = 300,
    on_refresh: Optional[Callable] = None
) -> PeriodicRefresher:
    """
    Start a background thread that refreshes the file index periodically.
    Useful for detecting newly downloaded or created files.

    Args:
        interval_seconds: Refresh interval in seconds (default: 300 = 5 min)
        on_refresh: Optional callback function to execute after each refresh

    Returns:
        PeriodicRefresher instance (call .stop() to halt background thread)

    Examples:
        >>> refresher = start_periodic_refresh(interval_seconds=180)  # 3 minutes
        >>> # ... later ...
        >>> refresher.stop()

        >>> def on_refresh_callback():
        ...     print("Index refreshed!")
        >>> refresher = start_periodic_refresh(
        ...     interval_seconds=300,
        ...     on_refresh=on_refresh_callback
        ... )
    """
    refresher = _get_refresher()

    # Update interval and callback
    refresher.interval = interval_seconds
    refresher.on_refresh = on_refresh

    if not refresher._running:
        refresher.start(_get_engine())
    else:
        logger.info(f"[PeriodicRefresh] Already running (interval updated to {interval_seconds}s)")

    return refresher


def stop_periodic_refresh() -> None:
    """Stop the background refresh thread."""
    refresher = _get_refresher()
    refresher.stop()


# ============================================================================
# __main__ — quick smoke test
# ============================================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    print(f"File Agent  |  platform=Windows  fuzzy={FUZZY_BACKEND}")
    print(f"Cache path: {CACHE_PATH}")
    print("=" * 60)

    tests = [
        "Test_File_Agent.txt"
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