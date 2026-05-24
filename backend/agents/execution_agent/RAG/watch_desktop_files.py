"""
Real-time File Watcher for Desktop - Instant Detection

Watches Desktop folder for new files and updates index in real-time
using watchdog library (much faster than 5-minute periodic refresh).

Usage:
    python watch_desktop_files.py

Installation:
    pip install watchdog

Benefits over periodic refresh:
    - Detects new files within 1 second (not 5 minutes)
    - Only rescans changed directories (faster)
    - Perfect for same-task download + open workflows
    - Can be used alongside periodic refresh
"""

import os
import sys
import time
import logging
from pathlib import Path
from typing import Optional, Set
from datetime import datetime

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent))

from file_agent import find_file, open_file, refresh_index

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s] %(asctime)s: %(message)s"
)
logger = logging.getLogger(__name__)

# Try to import watchdog (for real-time file detection)
try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler, FileModifiedEvent, FileCreatedEvent
    HAS_WATCHDOG = True
except ImportError:
    HAS_WATCHDOG = False
    # Define dummy classes so imports don't fail
    class FileSystemEventHandler:
        pass
    class Observer:
        pass
    logger.warning("watchdog not installed. Install with: pip install watchdog")
    logger.warning("Falling back to manual refresh (slower)")


# ============================================================================
# Real-Time File Watcher Using Watchdog
# ============================================================================

class DesktopFileWatcher(FileSystemEventHandler):
    """
    Watches Desktop folder for new files and updates index automatically.
    Triggers on: file created, file modified (for in-progress downloads)
    """

    def __init__(self, callback=None):
        self.callback = callback
        self.recent_files: Set[str] = set()
        self.last_refresh_time = time.time()
        self.refresh_cooldown = 2  # Seconds between refresh batches

    def on_created(self, event):
        """Called when a file is created."""
        if event.is_directory:
            return

        file_path = event.src_path
        logger.info(f"[NEW FILE] {os.path.basename(file_path)}")
        self.recent_files.add(file_path)
        self._schedule_refresh()

    def on_modified(self, event):
        """Called when a file is modified (includes download progress)."""
        if event.is_directory:
            return

        file_path = event.src_path

        # Only log large files being downloaded (reduce noise)
        try:
            size_mb = os.path.getsize(file_path) / (1024 * 1024)
            if size_mb > 1:  # Only log files > 1MB
                logger.debug(f"[DOWNLOAD IN PROGRESS] {os.path.basename(file_path)} (~{size_mb:.1f}MB)")
        except:
            pass

        self.recent_files.add(file_path)
        self._schedule_refresh()

    def _schedule_refresh(self):
        """Batch refreshes to avoid excessive index rebuilds."""
        now = time.time()
        if now - self.last_refresh_time > self.refresh_cooldown:
            logger.info(f"[REFRESHING INDEX] Detected {len(self.recent_files)} new/modified file(s)")

            try:
                refresh_index()
                logger.info(f"[REFRESHED] Index updated with new files")

                # Call optional callback
                if self.callback:
                    self.callback(list(self.recent_files))

            except Exception as e:
                logger.error(f"[ERROR] Failed to refresh index: {e}")

            self.recent_files.clear()
            self.last_refresh_time = now


# ============================================================================
# Fallback: Manual Desktop Scanner (without watchdog)
# ============================================================================

class ManualDesktopScanner:
    """
    Fallback for systems without watchdog.
    Polls Desktop folder for new files every N seconds.
    """

    def __init__(self, desktop_path: str, scan_interval: int = 2):
        self.desktop_path = desktop_path
        self.scan_interval = scan_interval
        self.known_files: Set[str] = set()
        self._load_initial_files()

    def _load_initial_files(self):
        """Load current files on Desktop."""
        try:
            for item in os.listdir(self.desktop_path):
                if os.path.isfile(os.path.join(self.desktop_path, item)):
                    self.known_files.add(item)
            logger.info(f"[SCANNER] Loaded {len(self.known_files)} existing files from Desktop")
        except Exception as e:
            logger.error(f"[SCANNER] Failed to load desktop files: {e}")

    def check_for_new_files(self) -> bool:
        """
        Check for newly added files.
        Returns True if new files found (and index was refreshed).
        """
        try:
            current_files = set()
            for item in os.listdir(self.desktop_path):
                path = os.path.join(self.desktop_path, item)
                if os.path.isfile(path):
                    current_files.add(item)

            # Find new files
            new_files = current_files - self.known_files

            if new_files:
                logger.info(f"[NEW FILES DETECTED] {len(new_files)} file(s)")
                for fname in sorted(new_files):
                    fpath = os.path.join(self.desktop_path, fname)
                    file_size = os.path.getsize(fpath) / 1024
                    logger.info(f"  - {fname} ({file_size:.1f}KB)")

                # Refresh index
                logger.info("[REFRESHING INDEX]…")
                refresh_index()
                logger.info("[REFRESHED] Index updated")

                self.known_files = current_files
                return True

        except Exception as e:
            logger.error(f"[SCANNER] Error: {e}")

        return False

    def run_loop(self, duration_seconds: Optional[int] = None):
        """
        Run continuous scanning loop.

        Args:
            duration_seconds: How long to scan (None = forever)
        """
        start_time = time.time()
        logger.info(f"[SCANNER] Starting continuous scan ({self.scan_interval}s interval)…")

        try:
            while True:
                self.check_for_new_files()
                time.sleep(self.scan_interval)

                if duration_seconds and (time.time() - start_time) > duration_seconds:
                    logger.info(f"[SCANNER] Scan duration complete ({duration_seconds}s)")
                    break

        except KeyboardInterrupt:
            logger.info("[SCANNER] Stopped by user")


# ============================================================================
# Main: Desktop Watcher Runner
# ============================================================================

def start_desktop_watcher(
    watch_interval: int = 2,
    on_new_files=None
) -> Optional[Observer]:
    """
    Start real-time Desktop file watcher.

    Args:
        watch_interval: Check interval in seconds (if using fallback)
        on_new_files: Optional callback(files) when new files detected

    Returns:
        Observer object (if watchdog available) or None
    """
    desktop_path = os.path.expanduser("~/Desktop")

    if not os.path.exists(desktop_path):
        logger.warning(f"[WATCHER] Desktop not found: {desktop_path}")
        return None

    logger.info(f"[WATCHER] Watching: {desktop_path}")
    logger.info(f"[WATCHER] Detect new files within 1-2 seconds")

    if HAS_WATCHDOG:
        logger.info("[WATCHER] Using watchdog (real-time)")
        # Real-time watcher
        event_handler = DesktopFileWatcher(callback=on_new_files)
        observer = Observer()
        observer.schedule(event_handler, desktop_path, recursive=False)
        observer.start()

        logger.info("[WATCHER] Started watching Desktop")
        return observer

    else:
        logger.info("[WATCHER] Using manual scanner (polling every 2 seconds)")
        # Fallback: manual scanning
        scanner = ManualDesktopScanner(desktop_path, scan_interval=watch_interval)

        # Run in background thread
        import threading

        thread = threading.Thread(
            target=scanner.run_loop,
            daemon=True,
            name="DesktopScanner"
        )
        thread.start()

        return thread


# ============================================================================
# Demo / Test Mode
# ============================================================================

def demo_same_task_workflow():
    """
    Demonstrate same-task download + open workflow.

    Scenario:
    1. User requests: "Download and open report.pdf"
    2. System downloads file
    3. System searches for it (with force_refresh)
    4. System opens it
    """
    logger.info("\n" + "=" * 70)
    logger.info("DEMO: Same-Task Download + Open Workflow")
    logger.info("=" * 70)

    # Option 1: Force refresh before searching (immediate)
    print("\n[OPTION 1] Force Refresh Before Search (RECOMMENDED)")
    print("-" * 70)

    filename = "example_report.pdf"
    print(f"1. Simulate download of: {filename}")
    # (In real scenario, download happens here)

    print(f"2. Search with force_refresh=True (rebuilds index)…")
    result = find_file(filename, force_refresh=True)

    if result["status"] == "found":
        print(f"   [OK] Found: {result['path']}")
        print(f"3. Opening file…")
        # open_file(filename)
    else:
        print(f"   [NOT FOUND] Suggestions: {result.get('suggestions')}")

    # Option 2: Desktop watcher (automatic)
    print("\n[OPTION 2] Desktop Watcher (AUTOMATIC)")
    print("-" * 70)
    print("1. Start desktop watcher")
    print("2. Download file to Desktop")
    print("3. Watcher detects file automatically (<2 seconds)")
    print("4. Index refreshed, file findable")


def demo_desktop_watcher():
    """
    Interactive demo of desktop watcher.

    Try:
    1. Run this script
    2. Create a file on Desktop (or download something)
    3. Watch it get detected and indexed
    """
    logger.info("\n" + "=" * 70)
    logger.info("DESKTOP FILE WATCHER DEMO")
    logger.info("=" * 70)

    print("\nInstructions:")
    print("1. Leave this window open")
    print("2. Download a file or create a new file on Desktop")
    print("3. Watch it appear in the logs below")
    print("\nPress Ctrl+C to stop\n")

    observer = start_desktop_watcher()

    if observer:
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("[DEMO] Stopping watcher…")
            observer.stop()
            observer.join()
            logger.info("[DEMO] Stopped")
    else:
        logger.error("[DEMO] Could not start watcher")


# ============================================================================
# Script Modes
# ============================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Real-time Desktop file watcher for AURA"
    )
    parser.add_argument(
        "mode",
        nargs="?",
        default="watch",
        choices=["watch", "demo", "workflow"],
        help="Mode to run"
    )

    args = parser.parse_args()

    if args.mode == "watch":
        # Start watching desktop
        observer = start_desktop_watcher()
        if observer:
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                logger.info("[WATCHER] Stopping…")
                observer.stop()
                observer.join()
                logger.info("[WATCHER] Stopped")

    elif args.mode == "demo":
        # Run interactive demo
        demo_desktop_watcher()

    elif args.mode == "workflow":
        # Show workflow example
        demo_same_task_workflow()
