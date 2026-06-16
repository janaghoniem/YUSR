"""
Standalone OmniParser test for any application.

Usage:
    # Test whatever is currently on screen:
    python test_omniparser_app.py --search "timeline" "playhead" "export"

    # Test a specific app by name (it must already be open):
    python test_omniparser_app.py --app "DaVinci Resolve" --search "timeline" "color"

    # Test from an existing screenshot file:
    python test_omniparser_app.py --screenshot "C:/path/to/screen.png" --search "trim"

    # Dump ALL detected elements (no search filter):
    python test_omniparser_app.py --dump

    # Dump elements from a specific app window:
    python test_omniparser_app.py --app "Blender" --dump
"""

import argparse
import logging
import os
import sys
import time
import tempfile
from pathlib import Path

# ── path setup so imports work from any working directory ──────────────────
_ROOT = Path(__file__).parent.parent.parent   # backend/
sys.path.insert(0, str(_ROOT))

import pyautogui
from PIL import Image

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("omniparser_test")


# ══════════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════════

def _get_window_screenshot(app_name: str) -> tuple:
    """
    Capture a screenshot of a specific app window by (partial) title.
    Returns (PIL.Image, window_left, window_top) so coordinates can be
    mapped back to screen space later.

    Raises RuntimeError if the window is not found.
    """
    try:
        import pygetwindow as gw
    except ImportError:
        raise RuntimeError(
            "pygetwindow is not installed. "
            "Run: pip install pygetwindow"
        )

    # Find all windows whose title contains app_name (case-insensitive)
    matches = [w for w in gw.getAllWindows()
               if app_name.lower() in (w.title or "").lower()]

    if not matches:
        available = [w.title for w in gw.getAllWindows() if w.title.strip()]
        raise RuntimeError(
            f"No window found containing '{app_name}'.\n"
            f"Open windows:\n" + "\n".join(f"  • {t}" for t in available[:20])
        )

    # Pick the largest window if multiple match (e.g. DaVinci has splash + main)
    win = max(matches, key=lambda w: w.width * w.height)
    logger.info(f"Found window: '{win.title}' at ({win.left},{win.top}) size {win.width}x{win.height}")

    # Bring window to front
    try:
        win.activate()
        time.sleep(0.5)
    except Exception:
        pass

    screenshot = pyautogui.screenshot(region=(
        win.left, win.top, win.width, win.height
    ))
    return screenshot, win.left, win.top


def _get_active_screenshot() -> tuple:
    """
    Capture whatever window is currently active.
    Returns (PIL.Image, window_left, window_top).
    """
    try:
        import pygetwindow as gw
        win = gw.getActiveWindow()
        if win:
            logger.info(
                f"Active window: '{win.title}' "
                f"at ({win.left},{win.top}) size {win.width}x{win.height}"
            )
            screenshot = pyautogui.screenshot(region=(
                win.left, win.top, win.width, win.height
            ))
            return screenshot, win.left, win.top
    except Exception:
        pass

    # Full-screen fallback
    logger.warning("Could not detect active window — capturing full screen")
    screenshot = pyautogui.screenshot()
    return screenshot, 0, 0


def _load_omniparser():
    """Import and return an initialised OmniParserDetector."""
    from agents.execution_agent.fallback.omniparser_detector import OmniParserDetector
    detector = OmniParserDetector(logger)
    # Force model initialisation now so we get a clear error if weights are missing
    ok = detector._initialize_models()
    if not ok:
        raise RuntimeError(
            "OmniParser models could not be loaded.\n"
            "Run: python backend/agents/execution_agent/fallback/download_weights.py"
        )
    return detector


def _apply_resize(image: Image.Image) -> tuple:
    """
    Apply the same downscale used by the main pipeline.
    Returns (resized_image, scale_x, scale_y).
    """
    try:
        from agents.execution_agent.fallback.utils.inference_utils import (
            _resize_for_omniparser,
        )
        return _resize_for_omniparser(image)
    except ImportError:
        return image, 1.0, 1.0


# ══════════════════════════════════════════════════════════════════════════
#  DUMP MODE  —  show every element OmniParser finds
# ══════════════════════════════════════════════════════════════════════════

def run_dump(image: Image.Image, win_left: int, win_top: int, detector):
    """Detect and print every element on screen, sorted by confidence."""
    print("\n" + "═" * 70)
    print("  DUMP MODE — all detected elements")
    print("═" * 70)

    resized, sx, sy = _apply_resize(image)

    # Save temp file for the detector
    tmp = os.path.join(tempfile.gettempdir(), "omni_dump.png")
    resized.save(tmp)

    detections = detector.detector.detect(resized, conf_threshold=0.15)
    if not detections:
        print("  No elements detected.")
        return

    # Sort by confidence descending
    detections.sort(key=lambda d: d["confidence"], reverse=True)

    results = []
    for i, det in enumerate(detections):
        bbox = det["bbox"]
        elem_img = image.crop([
            bbox[0] * sx, bbox[1] * sy,
            bbox[2] * sx, bbox[3] * sy,
        ])
        # Use original-resolution crop for captioning quality
        caption = detector.captioner.caption(elem_img)
        cx = int((bbox[0] + bbox[2]) / 2 * sx) + win_left
        cy = int((bbox[1] + bbox[3]) / 2 * sy) + win_top
        results.append((det["confidence"], caption, cx, cy))

    print(f"\n  Found {len(results)} elements:\n")
    print(f"  {'#':>3}  {'Conf':>5}  {'Screen coords':>18}  Caption")
    print("  " + "-" * 66)
    for i, (conf, caption, cx, cy) in enumerate(results, 1):
        print(f"  {i:>3}  {conf:>5.2f}  ({cx:>5}, {cy:>5})        {caption[:50]}")

    print(f"\n  Tip: run with --search <label> to find a specific element.")


# ══════════════════════════════════════════════════════════════════════════
#  SEARCH MODE  —  find specific elements by label
# ══════════════════════════════════════════════════════════════════════════

def run_search(image: Image.Image, win_left: int, win_top: int,
               detector, queries: list):
    """Search for each query label in the image."""
    resized, sx, sy = _apply_resize(image)
    tmp = os.path.join(tempfile.gettempdir(), "omni_search.png")
    resized.save(tmp)

    print("\n" + "═" * 70)
    print(f"  SEARCH MODE — {len(queries)} queries")
    print("═" * 70)

    found = 0
    for query in queries:
        print(f"\n  🔍  Searching for: '{query}'")
        t0 = time.perf_counter()

        result = detector.detect_element_by_text(query, screenshot_path=tmp)

        elapsed = (time.perf_counter() - t0) * 1000

        if result.success and result.coordinates:
            # result.coordinates are in resized-image space — scale back
            screen_x = int(result.coordinates[0] * sx) + win_left
            screen_y = int(result.coordinates[1] * sy) + win_top
            print(f"  ✅  FOUND  →  screen ({screen_x}, {screen_y})  "
                  f"conf={result.confidence:.2f}  [{elapsed:.0f} ms]")
            print(f"      Caption: '{result.detected_text}'")
            found += 1
        else:
            print(f"  ❌  NOT FOUND  [{elapsed:.0f} ms]")
            print(f"      (method: {result.method})")

    print(f"\n  Result: {found}/{len(queries)} elements found.")


# ══════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Standalone OmniParser test for any application",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--app", metavar="TITLE",
        help="Partial window title to capture (e.g. 'DaVinci', 'Blender', 'Premiere'). "
             "Omit to use the currently active window.",
    )
    parser.add_argument(
        "--screenshot", metavar="PATH",
        help="Path to an existing screenshot file instead of capturing live.",
    )
    parser.add_argument(
        "--search", metavar="LABEL", nargs="+",
        help="One or more element labels to search for.",
    )
    parser.add_argument(
        "--dump", action="store_true",
        help="Print ALL detected elements with their captions and coordinates.",
    )
    parser.add_argument(
        "--delay", type=float, default=1.0, metavar="SECONDS",
        help="Seconds to wait before capturing (gives you time to switch windows). "
             "Default: 1.0",
    )
    args = parser.parse_args()

    if not args.search and not args.dump:
        parser.error("Specify --search <label> and/or --dump")

    # ── Load models ────────────────────────────────────────────────────────
    print("\nLoading OmniParser models…")
    try:
        detector = _load_omniparser()
        print("✓ Models ready\n")
    except RuntimeError as e:
        print(f"\n❌  {e}")
        sys.exit(1)

    # ── Capture image ──────────────────────────────────────────────────────
    if args.screenshot:
        path = args.screenshot
        if not os.path.exists(path):
            print(f"❌  Screenshot not found: {path}")
            sys.exit(1)
        image = Image.open(path).convert("RGB")
        win_left, win_top = 0, 0
        print(f"Using screenshot: {path}  ({image.size[0]}x{image.size[1]})")

    elif args.app:
        print(f"Capturing window containing '{args.app}' in {args.delay:.1f}s…")
        time.sleep(args.delay)
        try:
            image, win_left, win_top = _get_window_screenshot(args.app)
        except RuntimeError as e:
            print(f"\n❌  {e}")
            sys.exit(1)

    else:
        print(f"Capturing active window in {args.delay:.1f}s…")
        time.sleep(args.delay)
        image, win_left, win_top = _get_active_screenshot()

    print(f"Image size: {image.size[0]}x{image.size[1]}")

    # ── Run ────────────────────────────────────────────────────────────────
    if args.dump:
        run_dump(image, win_left, win_top, detector)

    if args.search:
        run_search(image, win_left, win_top, detector, args.search)

    print()


if __name__ == "__main__":
    main()