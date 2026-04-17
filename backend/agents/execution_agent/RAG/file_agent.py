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

    def _numeric_penalty(a: str, b: str) -> float:
        """
        Return a penalty multiplier (0.0–1.0) based on mismatched numeric tokens.

        If the query contains numbers (e.g. "3", "7") that are absent from the
        filename, the match is almost certainly wrong.  Each missing query number
        that appears in the filename as a *different* number applies a 0.30
        reduction so that "session 7" can never win over "session 1" just because
        partial_ratio sees "session" overlapping.

        Rules
        -----
        * Extract digit-only tokens from both normalised strings.
        * For every number in the query that is NOT in the filename numbers,
          check whether *any* number IS present in the filename — if so, this is
          a numeric mismatch (e.g. query has "7", file has "1"): penalise 0.70×.
        * If query has numbers and file has NONE at all, penalise lightly (0.85×)
          because the file might just omit the number in its name.
        * No numbers in query → no penalty (1.0).
        """
        import re
        q_nums = set(re.findall(r'\b\d+\b', a))
        f_nums = set(re.findall(r'\b\d+\b', b))

        if not q_nums:
            return 1.0  # Query has no numbers — nothing to check

        missing = q_nums - f_nums          # Numbers in query but not in filename
        if not missing:
            return 1.0  # All query numbers present in filename — perfect

        # Filename has numbers but they differ from the query numbers
        if f_nums:
            # Each mismatched number applies a 0.70× factor
            return 0.70 ** len(missing)

        # Filename has no numbers at all — mild penalty
        return 0.85

    def _score(a: str, b: str) -> float:
        """
        Robust filename match combining:
          - token_sort_ratio  : order-insensitive full-token comparison
          - token_set_ratio   : handles extra tokens in filename gracefully
          - partial_ratio     : substring awareness (weighted down to avoid
                                false positives like "Lab 3 SWE" beating "Lab 3 KR")
          - numeric penalty   : punishes mismatched numbers (session 1 vs session 7)

        Returns 0.0–1.0.
        """
        token_sort = _fuzz.token_sort_ratio(a, b)   # order-insensitive
        token_set  = _fuzz.token_set_ratio(a, b)    # handles extra tokens in filename
        partial    = _fuzz.partial_ratio(a, b)       # substring (weighted down)

        # Weighted combination — partial gets less weight to stop it crowning
        # wrong matches just because they share a short substring
        raw = (token_sort * 0.45 + token_set * 0.35 + partial * 0.20) / 100.0

        # Apply numeric penalty AFTER combining so a number mismatch always
        # drags down the final score even when substring similarity is high
        penalty = _numeric_penalty(a, b)
        return raw * penalty

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
    # Python / package managers (LOW PRIORITY - system-generated)
    "site-packages", "dist-info", "miniconda", "anaconda", ".egg-info",
    "pip-cache", ".pyenv", ".gem", "node_modules",
}

# User-priority directories - files here get boosted in ranking
USER_PRIORITY_DIRS = {"Desktop", "Downloads", "Documents", "OneDrive", "Google Drive"}

FILLER_WORDS = {"the", "a", "my", "your", "and", "or", "is", "are", "of", "for", "in"}

# Where we persist the index cache
def _cache_path() -> str:
    """Cache path under %LOCALAPPDATA%\\yusr\\."""
    base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
    return os.path.join(base, "aura", "file_index_cache.json")

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
        4. Remove filler words; keep tokens of len > 1 OR that are purely numeric
           (so single-digit numbers like "1", "3", "7" survive the length filter)
        5. Collapse whitespace

        Example:
            "My-Report_Q4.2024 (FINAL).pdf" → "report q4 2024 final pdf"
            "Session 7 DEPI.pdf"            → "session 7 depi pdf"
        """
        s = text.lower()
        for ch in ("_", "-", ".", "(", ")", "[", "]"):
            s = s.replace(ch, " ")
        s = "".join(c if c.isalnum() or c.isspace() else " " for c in s)
        # Keep a word if it's purely numeric (any length) OR non-numeric and > 1 char
        words = [w for w in s.split() if w not in FILLER_WORDS and (w.isdigit() or len(w) > 1)]
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

    def _is_user_file(self, path: str) -> tuple[bool, float]:
        """
        Check if file is in user-priority directory.
        Returns: (is_user_priority, boost_factor)

        User-priority (boost 1.2x): Desktop, Downloads, Documents, OneDrive
        System-file (penalize 0.4x): site-packages, miniconda, Windows system
        """
        path_lower = path.lower()

        # Check user-priority directories
        for user_dir in USER_PRIORITY_DIRS:
            if user_dir.lower() in path_lower:
                return (True, 1.2)  # Boost user files

        # Check system directories
        system_markers = ["site-packages", "miniconda", "anaconda", "appdata", "program files",
                         "windows\\system", "\\lib\\", "\\.egg-info"]
        for marker in system_markers:
            if marker in path_lower:
                return (False, 0.4)  # Penalize system files

        return (False, 1.0)  # Neutral

    def _count_matching_tokens(self, query_tokens: set, filename_tokens: set) -> tuple[int, int]:
        """
        Count how many tokens from query are in filename.
        Returns: (matching_count, total_query_tokens)
        """
        matching = len(query_tokens & filename_tokens)
        return (matching, len(query_tokens))

    def _best_candidates(
        self, normalized_query: str, threshold: float
    ) -> List[Dict[str, Any]]:
        """
        Return all index entries scoring >= threshold, sorted by:
        1. Combined score (fuzzy × location × token_coverage × path_context)
        2. Exact-number-match bonus
        3. Location priority (user dirs > neutral > system dirs)
        4. Token coverage (full matches > partial matches)

        Changes vs original
        -------------------
        * path_context_score: also score the query against the *parent directory
          path*, normalised the same way.  This means "Lab 3 KR" correctly
          prefers the file inside a "Knowledge Representation" folder over one
          inside an "SWE110" folder, even when filename fuzzy scores are similar.
        * number_match_bonus: if ALL numeric tokens in the query appear in the
          filename, add a 15 % bonus so "Lecture 3" conclusively beats "Lecture 1"
          when the file name literally contains the right number.
        * combined_score formula updated to include path_context and number bonus.
        """
        import re
        query_tokens = set(normalized_query.split())
        q_nums = set(re.findall(r'\b\d+\b', normalized_query))
        results = []

        for entry in self.index:
            # ── Base fuzzy score (filename only) ──────────────────────────────
            fuzzy_score = _score(normalized_query, entry["normalized_name"])
            if fuzzy_score < threshold:
                continue

            # ── Path-context score ────────────────────────────────────────────
            # Normalise the parent directory path and fuzzy-score it against
            # the query.  This lifts "KR" folder matches when the filename
            # alone is ambiguous (e.g. "Lab 3 - Group 5 - SWE110" vs "Lab 3 - KR").
            parent_dir = os.path.dirname(entry["path"])
            norm_parent = Normalizer.normalize(os.path.basename(parent_dir))
            # Score query against the immediate parent folder name
            path_score = _score(normalized_query, norm_parent)
            # Blend: 85 % filename, 15 % folder context
            blended_score = fuzzy_score * 0.85 + path_score * 0.15

            # ── Location boost / penalty ──────────────────────────────────────
            is_user, location_boost = self._is_user_file(entry["path"])

            # ── Token coverage ────────────────────────────────────────────────
            filename_tokens = set(entry["normalized_name"].split())
            matching_tokens, total_tokens = self._count_matching_tokens(query_tokens, filename_tokens)
            token_coverage = matching_tokens / max(total_tokens, 1) if total_tokens > 0 else 0

            # ── Exact-number-match bonus ──────────────────────────────────────
            # If the query has numeric tokens and ALL of them appear verbatim in
            # the filename tokens, reward the file with a 15 % boost.
            f_nums = set(re.findall(r'\b\d+\b', entry["normalized_name"]))
            if q_nums and q_nums.issubset(f_nums):
                number_bonus = 0.15
            elif q_nums and not (q_nums & f_nums):
                number_bonus = -0.10   # slight nudge down for complete number miss
            else:
                number_bonus = 0.0

            # ── Combined score ────────────────────────────────────────────────
            combined_score = (
                blended_score
                * location_boost
                * (1 + token_coverage * 0.8)
                * (1 + number_bonus)
            )

            results.append({
                **entry,
                "_score": fuzzy_score,
                "_combined_score": combined_score,
                "_location_boost": location_boost,
                "_token_coverage": token_coverage,
            })

        results.sort(key=lambda x: x["_combined_score"], reverse=True)
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
            # Use combined_score gap (which includes number bonus + path context)
            # so that "Lecture 3" clearly beats "Lecture 1" even at similar raw
            # fuzzy scores.  Threshold lowered to 0.08 so a number-match bonus
            # of 0.15 is sufficient to resolve ambiguity.
            gap = valid[0]["_combined_score"] - (valid[1]["_combined_score"] if len(valid) > 1 else 0)
            if len(valid) == 1 or gap >= 0.08:
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


def find_all_matches(filename: str) -> Dict[str, Any]:
    """
    Find ALL matching files with confidence scores.
    Perfect for showing users all options when multiple matches exist.

    Returns:
        {
          "status": "found" | "multiple" | "not_found",
          "matches": [{"path": str, "confidence": float, "name": str}, ...],
          "total": int,
          "suggestions": [str, ...]  # if not_found
        }

    Example:
        >>> result = find_all_matches("lecture")
        >>> for match in result["matches"]:
        ...     print(f"{match['name']} ({match['confidence']:.0%}) → {match['path']}")
    """
    result = _get_engine().find(filename)

    if result["status"] == "found":
        return {
            "status": "found",
            "matches": [{"path": result["path"], "confidence": result["confidence"], "name": os.path.basename(result["path"])}],
            "total": 1
        }
    elif result["status"] == "multiple":
        matches = []
        for m in result.get("paths", []):
            matches.append({
                "path": m["path"],
                "confidence": m["confidence"],
                "name": os.path.basename(m["path"])
            })
        return {
            "status": "multiple",
            "matches": matches,
            "total": len(matches)
        }
    else:
        return {
            "status": "not_found",
            "matches": [],
            "total": 0,
            "suggestions": result.get("suggestions", [])
        }


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


def preload_index() -> None:
    """
    Pre-load and warm up the file index BEFORE any file searches.
    Call this during agent startup/initialization to avoid timeout on first find_file() call.

    If cache exists and is valid, loads instantly (~100ms).
    If cache missing/expired, builds from scratch (~5-15s depending on drive).
    """
    logger.info("📂 Preloading file index...")
    _get_engine()._ensure_index()
    logger.info("✅ File index preloaded and ready")


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