#!/usr/bin/env python3
"""
verification.py - Screenshot Verification via SSIM Comparison

Features:
- Take before/after screenshots of page interactions
- Compare screenshots using SSIM (Structural Similarity Index)
- Detect page state changes
- Verify success of automation tasks
"""

import os
import time
import logging
from typing import Optional, Tuple
from datetime import datetime
from pathlib import Path

try:
    import cv2
    from skimage.metrics import structural_similarity as ssim
except ImportError:
    cv2 = None
    ssim = None
    logging.warning("⚠️ OpenCV (cv2) or scikit-image not installed, screenshot verification disabled")

logger = logging.getLogger(__name__)

# ============================================================================
# SCREENSHOT VERIFICATION
# ============================================================================

class ScreenshotVerifier:
    """
    Verifies page changes and interaction success via screenshot comparison
    Uses SSIM (Structural Similarity Index) for comparison
    """
    
    # SSIM threshold for detecting meaningful changes (0-1 scale)
    # Lower = more different
    # SSIM < 0.95 indicates a significant visual change
    CHANGE_DETECTION_THRESHOLD = 0.95
    
    def __init__(self, output_dir: str = "./screenshots"):
        """
        Initialize screenshot verifier
        
        Args:
            output_dir: Directory to store screenshots
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        self.before_screenshot = None
        self.after_screenshot = None
        self.comparison_ssim = None
        
        logger.info(f"✅ ScreenshotVerifier initialized (output: {self.output_dir})")
    
    async def take_screenshot_before(self, page, session_id: str, action_name: str) -> Optional[str]:
        """
        Take screenshot before action
        
        Args:
            page: Playwright page object
            session_id: Session identifier
            action_name: Name of action about to be performed
        
        Returns:
            Path to saved screenshot
        """
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{session_id}_{action_name}_BEFORE_{timestamp}.png"
            filepath = self.output_dir / filename
            
            # Take screenshot
            screenshot_data = await page.screenshot(path=str(filepath))
            
            # Store in memory
            if cv2:
                self.before_screenshot = cv2.imread(str(filepath))
            
            logger.info(f"📸 Before screenshot: {filename}")
            return str(filepath)
        
        except Exception as e:
            logger.error(f"❌ Failed to take before screenshot: {e}")
            return None
    
    async def take_screenshot_after(self, page, session_id: str, action_name: str) -> Optional[str]:
        """
        Take screenshot after action
        
        Args:
            page: Playwright page object
            session_id: Session identifier
            action_name: Name of action that was performed
        
        Returns:
            Path to saved screenshot
        """
        try:
            # Add small delay to ensure action completes
            await asyncio.sleep(0.5)
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{session_id}_{action_name}_AFTER_{timestamp}.png"
            filepath = self.output_dir / filename
            
            # Take screenshot
            screenshot_data = await page.screenshot(path=str(filepath))
            
            # Store in memory
            if cv2:
                self.after_screenshot = cv2.imread(str(filepath))
            
            logger.info(f"📸 After screenshot: {filename}")
            return str(filepath)
        
        except Exception as e:
            logger.error(f"❌ Failed to take after screenshot: {e}")
            return None
    
    def compare_screenshots(self, before_path: Optional[str] = None, 
                           after_path: Optional[str] = None) -> Tuple[bool, float]:
        """
        Compare before/after screenshots using SSIM
        
        Args:
            before_path: Path to before screenshot (uses stored if not provided)
            after_path: Path to after screenshot (uses stored if not provided)
        
        Returns:
            Tuple of (changed: bool, ssim_score: float)
            - changed=True if SSIM < threshold (page changed)
            - ssim_score ranges from 0 (completely different) to 1 (identical)
        """
        if not cv2 or not ssim:
            logger.warning("⚠️ OpenCV/scikit-image not available, skipping screenshot comparison")
            return None, None
        
        try:
            # Load screenshots if paths provided
            if before_path:
                self.before_screenshot = cv2.imread(before_path)
            if after_path:
                self.after_screenshot = cv2.imread(after_path)
            
            # Validate both screenshots exist
            if self.before_screenshot is None or self.after_screenshot is None:
                logger.error("❌ Before/after screenshots not available for comparison")
                return None, None
            
            # Convert to grayscale if color
            if len(self.before_screenshot.shape) == 3:
                before_gray = cv2.cvtColor(self.before_screenshot, cv2.COLOR_BGR2GRAY)
            else:
                before_gray = self.before_screenshot
            
            if len(self.after_screenshot.shape) == 3:
                after_gray = cv2.cvtColor(self.after_screenshot, cv2.COLOR_BGR2GRAY)
            else:
                after_gray = self.after_screenshot
            
            # Verify same dimensions
            if before_gray.shape != after_gray.shape:
                logger.warning("⚠️ Screenshots have different dimensions, resizing after to match before")
                after_gray = cv2.resize(after_gray, (before_gray.shape[1], before_gray.shape[0]))
            
            # Calculate SSIM
            self.comparison_ssim = ssim(before_gray, after_gray, data_range=255)
            
            # Determine if change detected
            changed = self.comparison_ssim < self.CHANGE_DETECTION_THRESHOLD
            
            status = "🔴 CHANGED" if changed else "🟢 UNCHANGED"
            logger.info(f"{status} - SSIM Score: {self.comparison_ssim:.4f} (threshold: {self.CHANGE_DETECTION_THRESHOLD})")
            
            return changed, self.comparison_ssim
        
        except Exception as e:
            logger.error(f"❌ Screenshot comparison failed: {e}")
            return None, None
    
    def get_difference_mask(self, before_path: Optional[str] = None,
                           after_path: Optional[str] = None) -> Optional[str]:
        """
        Generate visual difference mask showing what changed
        Useful for debugging and verification
        
        Args:
            before_path: Path to before screenshot
            after_path: Path to after screenshot
        
        Returns:
            Path to difference mask image
        """
        if not cv2:
            logger.warning("⚠️ OpenCV not available, cannot generate difference mask")
            return None
        
        try:
            # Load images if paths provided
            if before_path:
                before = cv2.imread(before_path)
            else:
                before = self.before_screenshot
            
            if after_path:
                after = cv2.imread(after_path)
            else:
                after = self.after_screenshot
            
            if before is None or after is None:
                return None
            
            # Ensure same shape
            if before.shape != after.shape:
                after = cv2.resize(after, (before.shape[1], before.shape[0]))
            
            # Compute difference
            diff = cv2.absdiff(before, after)
            
            # Enhance differences with morphological operations
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
            diff = cv2.morphologyEx(diff, cv2.MORPH_CLOSE, kernel)
            
            # Threshold to binary
            _, diff_binary = cv2.threshold(cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY), 50, 255, cv2.THRESH_BINARY)
            
            # Create colored difference mask (red = changed, green = unchanged)
            mask = cv2.cvtColor(diff_binary, cv2.COLOR_GRAY2BGR)
            mask[:, :, 0] = diff_binary  # Red channel = differences
            mask[:, :, 1] = 255 - diff_binary  # Green channel = unchanged
            mask[:, :, 2] = 0
            
            # Save mask
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            mask_filename = f"difference_mask_{timestamp}.png"
            mask_path = self.output_dir / mask_filename
            
            cv2.imwrite(str(mask_path), mask)
            logger.info(f"📊 Difference mask saved: {mask_filename}")
            
            return str(mask_path)
        
        except Exception as e:
            logger.error(f"❌ Failed to generate difference mask: {e}")
            return None
    
    def get_statistics(self) -> dict:
        """Get verification statistics"""
        return {
            "before_screenshot": self.before_screenshot is not None,
            "after_screenshot": self.after_screenshot is not None,
            "ssim_score": self.comparison_ssim,
            "threshold": self.CHANGE_DETECTION_THRESHOLD,
            "changed_detected": (self.comparison_ssim < self.CHANGE_DETECTION_THRESHOLD) if self.comparison_ssim else None,
        }


# ============================================================================
# ASYNC SUPPORT
# ============================================================================

import asyncio
