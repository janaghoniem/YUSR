# ============================================================================
# File Utility Module
# ============================================================================
r"""
Lightweight file search utilities for the File Agent.
Uses Windows 'where' command for fast file discovery.

Import styles:
  from agents.execution_agent.RAG.file_utils import open_file
  from file_utils import open_file  (if in same directory)
"""

import subprocess
import string
import os
import logging
from typing import List, Optional

logger = logging.getLogger(__name__)


def get_drives() -> List[str]:
    """Get all accessible drives on the system."""
    return [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]


def find_file(filename: str, search_scope: str = "fast") -> Optional[str]:
    r"""
    Find a file by name and return the first match.

    Args:
        filename: Name of file to find (e.g., "test.txt", "report.pdf")
        search_scope: "fast" (all drives) or "full" (deeper search with retries)

    Returns:
        Full path to the file, or None if not found

    Example:
        >>> path = find_file("API ENDPOINTS.txt")
        >>> if path:
        ...     os.startfile(path)
        ... else:
        ...     print("File not found")
    """

    if search_scope == "fast":
        # Fast search: Try all available drives (not just C:\Users)
        # This is important when files are on different drives (D:, E:, etc.)
        search_paths = get_drives()  # Get ALL accessible drives
    else:
        # Full search: Start with all drives, then try deeper
        search_paths = get_drives()

    for path in search_paths:
        if not os.path.exists(path):
            continue

        try:
            result = subprocess.run(
                ["where", "/r", path, filename],
                capture_output=True,
                text=True,
                timeout=30  # Increased timeout for large directory trees
            )

            if result.stdout:
                found_path = result.stdout.strip().split('\n')[0]  # First match
                if os.path.isfile(found_path):
                    logger.info(f"✅ Found file: {found_path}")
                    return found_path
        except subprocess.TimeoutExpired:
            logger.debug(f"Search timeout in {path} (using fallback)...")  # Changed to debug level
            # Fallback to faster glob search in common locations
            try:
                import glob
                fast_search = [
                    os.path.expanduser(f'~/{filename}'),
                    os.path.expanduser(f'~/Desktop/{filename}'),
                    os.path.expanduser(f'~/Documents/{filename}'),
                    os.path.expanduser(f'~/Downloads/{filename}'),
                ]
                for candidate in fast_search:
                    if os.path.isfile(candidate):
                        logger.info(f"✅ Found file (fallback): {candidate}")
                        return candidate
            except:
                pass
        except Exception as e:
            logger.warning(f"⚠️ Search error in {path}: {e}")
            continue

    logger.warning(f"❌ File not found: {filename}")
    return None


def find_all_files(filename: str, search_scope: str = "fast") -> List[str]:
    r"""
    Find all matching files by name.

    Args:
        filename: Name of file to find (supports wildcards)
        search_scope: "fast" (all drives) or "full" (deeper search)

    Returns:
        List of paths to all matching files

    Example:
        >>> files = find_all_files("*.txt")
        >>> for f in files:
        ...     print(f)
    """

    if search_scope == "fast":
        search_paths = get_drives()  # Get ALL accessible drives
    else:
        search_paths = get_drives()  # Same for full search - search all drives

    results = []

    for path in search_paths:
        if not os.path.exists(path):
            continue

        try:
            result = subprocess.run(
                ["where", "/r", path, filename],
                capture_output=True,
                text=True,
                timeout=30  # Increased for large directory searches
            )

            if result.stdout:
                for line in result.stdout.strip().split('\n'):
                    if os.path.isfile(line):
                        results.append(line)
        except Exception as e:
            logger.warning(f"⚠️ Search error in {path}: {e}")
            continue

    # Remove duplicates
    results = list(set(results))

    if results:
        logger.info(f"✅ Found {len(results)} file(s)")
    else:
        logger.warning(f"❌ No files found matching: {filename}")

    return results


def open_file(filename: str) -> bool:
    """
    Find and open a file with the default application.

    IMPORTANT: Outputs [FILE]: <path> to stdout for downstream tasks

    Args:
        filename: Name of file to open

    Returns:
        True if successful, False otherwise

    Example:
        >>> if open_file("report.pdf"):
        ...     print("EXECUTION_SUCCESS")
        ... else:
        ...     print("EXECUTION_FAILED")
    """

    try:
        path = find_file(filename, search_scope="fast")

        if not path:
            # File not found in fast search, try full search
            logger.debug(f"Not found in fast locations, trying full system search...")
            path = find_file(filename, search_scope="full")

        if not path:
            logger.error(f"File not found: {filename}")
            return False

        if not os.path.isfile(path):
            logger.error(f"Path is not a file: {path}")
            return False

        try:
            os.startfile(path)
            # Output the path for downstream tasks
            print(f"[FILE]: {path}")
            logger.info(f"✅ Opened: {path}")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to open file: {e}")
            return False

    except Exception as e:
        logger.error(f"❌ Error opening file: {e}")
        return False


def get_file_path(filename: str) -> Optional[str]:
    """
    Alias for find_file() - returns path without opening.

    Args:
        filename: Name of file to locate

    Returns:
        Full path to the file, or None if not found
    """
    return find_file(filename)


def file_exists(filename: str) -> bool:
    """
    Check if a file exists without opening it.

    Args:
        filename: Name of file to check

    Returns:
        True if file found, False otherwise

    Example:
        >>> if file_exists("config.txt"):
        ...     print("File exists")
    """
    path = find_file(filename, search_scope="fast")
    return path is not None


# ============================================================================
# Testing
# ============================================================================

if __name__ == "__main__":
    print("File Utility Module - Test")
    print("=" * 50)

    # Test find_file (fast)
    print("\n[TEST 1] Quick search for config.txt:")
    path = find_file("config.txt", search_scope="fast")
    print(f"Result: {path}")

    # Test find_all_files
    print("\n[TEST 2] Search for all .txt files:")
    files = find_all_files("*.txt", search_scope="fast")
    print(f"Found {len(files)} files:")
    for f in files[:5]:  # Show first 5
        print(f"  - {f}")
    if len(files) > 5:
        print(f"  ... and {len(files) - 5} more")

    # Test get_file_path
    print("\n[TEST 3] Get path for test.txt:")
    path = get_file_path("test.txt")
    print(f"Result: {path}")
