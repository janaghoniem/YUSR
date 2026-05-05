"""
OmniParser UI Element Detector
Integrates with existing VisionLayer as advanced fallback
COMPLETE FIXED VERSION - All bugs resolved
"""

import logging
from pathlib import Path
from typing import Optional, Dict
from PIL import Image
import pyautogui


import importlib
import sys

# Force reload the OmniParser module
if 'agents.execution_agent.fallback.omniparser_detector' in sys.modules:
    importlib.reload(sys.modules['agents.execution_agent.fallback.omniparser_detector'])

sys.path.insert(0, str(Path(__file__).parent.parent))
from ..core.exec_agent_models import VisionResult

# Import OmniParser utilities
from .utils import IconDetector, IconCaptioner, crop_image_region, calculate_center


class OmniParserDetector:
    """
    OmniParser-based UI element detection
    Advanced fallback when UIA/OCR/CV fail
    """
    
    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self.initialized = False
        self.detector = None
        self.captioner = None
        
        # Paths
        self.weights_dir = Path(__file__).parent / "weights"
        self.detector_path = self.weights_dir / "icon_detect" / "model.pt"
        self.captioner_path = self.weights_dir / "icon_caption_florence"
        
        # Lazy initialization (only load models when first used)
        self._check_models_exist()
    
    def _check_models_exist(self):
        """Check if model weights are downloaded"""
        if not self.detector_path.exists():
            self.logger.warning(f"⚠️ OmniParser detector model not found at {self.detector_path}")
            self.logger.warning("Run: python fallback/download_weights.py")
            return False
        
        if not self.captioner_path.exists():
            self.logger.warning(f"⚠️ OmniParser captioner model not found at {self.captioner_path}")
            return False
        
        return True
    
    def _initialize_models(self):
        """Lazy load models (only when first detection is needed)"""
        if self.initialized:
            return True
        
        if not self._check_models_exist():
            self.logger.error("❌ OmniParser models not available")
            return False
        
        try:
            self.logger.info("🔄 Loading OmniParser models...")
            
            # Load detector
            self.detector = IconDetector(str(self.detector_path))
            self.logger.info("✓ Detector loaded")
            
            # Load captioner
            self.captioner = IconCaptioner(str(self.captioner_path))
            self.logger.info("✓ Captioner loaded")
            
            self.initialized = True
            self.logger.info("✅ OmniParser initialized successfully")
            return True
            
        except Exception as e:
            self.logger.error(f"❌ Failed to initialize OmniParser: {e}")
            self.initialized = False
            return False
    
    def _compute_word_similarity(self, word1: str, word2: str) -> float:
        """
        Compute similarity between two words
        
        IMPORTANT: Filters out short words to avoid matching articles like "a", "in", "on"
        
        Args:
            word1: First word to compare
            word2: Second word to compare
            
        Returns:
            Similarity score (0.0 to 1.0)
        """
        from difflib import SequenceMatcher
        
        # CRITICAL FIX: Ignore very short words (articles, prepositions)
        if len(word1) < 3 or len(word2) < 3:
            return 0.0
        
        # Exact match
        if word1 == word2:
            return 1.0
        
        # Check if one is substring of other (e.g., "game" in "gaming")
        # But ONLY if both words are reasonably long
        if len(word1) >= 4 and len(word2) >= 4:
            if word1 in word2 or word2 in word1:
                return 0.85
        
        # Check stem similarity (first 4 chars)
        if len(word1) >= 4 and len(word2) >= 4:
            if word1[:4] == word2[:4]:
                return 0.80
        
        # Use sequence matcher for fuzzy similarity
        similarity = SequenceMatcher(None, word1, word2).ratio()
        return similarity if similarity > 0.7 else 0.0
    
    def detect_element_by_text(self, target_text: str, screenshot_path: Optional[str] = None) -> VisionResult:
        """
        Detect UI element by text description
        FIXED: Proper semantic matching, filters short words, improved element selection
        
        Args:
            target_text: The text to search for (e.g., "Gaming", "Settings")
            screenshot_path: Optional path to screenshot file
            
        Returns:
            VisionResult with success status and coordinates
        """
        if not self._initialize_models():
            return VisionResult(
                success=False,
                coordinates=None,
                confidence=0.0,
                detected_text=None,
                method="omniparser_unavailable"
            )
        
        try:
            # Capture screenshot
            if screenshot_path is None:
                import time
                time.sleep(0.5)
                screenshot = pyautogui.screenshot()
                image = screenshot
                self.logger.info(f"📸 Screenshot captured: {image.size[0]}x{image.size[1]} pixels")
            else:
                image = Image.open(screenshot_path)
            
            # Detect all UI elements
            self.logger.info(f"🔍 Detecting UI elements...")
            detections = self.detector.detect(image, conf_threshold=0.2)
            
            if not detections:
                self.logger.info("No elements detected")
                return VisionResult(
                    success=False,
                    coordinates=None,
                    confidence=0.0,
                    detected_text=None,
                    method="omniparser_no_elements"
                )
            
            self.logger.info(f"Found {len(detections)} elements")

            # Log all detected positions
            self.logger.info("="*80)
            self.logger.info("🔍 ALL DETECTED ELEMENTS (no captions yet):")
            for i, det in enumerate(detections):
                bbox = det['bbox']
                center = calculate_center(bbox)
                conf = det['confidence']
                
                x, y = center
                window_width = image.size[0]
                position = "LEFT" if x < window_width * 0.2 else "CENTER" if x < window_width * 0.8 else "RIGHT"
                
                self.logger.info(f"  [{i+1:2d}] Conf:{conf:.2f} | Pos:{position:6s} | Center:({x:4d},{y:4d})")
            self.logger.info("="*80)
            
            # ====================================================================
            # IMPROVED: Fuzzy matching with semantic similarity
            # ====================================================================
            best_match = None
            best_score = 0.0
            
            # FIXED: Filter out short words from target text
            target_lower = target_text.lower()
            target_words = set(word for word in target_lower.split() if len(word) >= 3)

            # Sort by confidence (highest first)
            sorted_detections = sorted(detections, key=lambda d: d['confidence'], reverse=True)

            self.logger.info(f"🔍 Searching for '{target_text}' in {len(sorted_detections)} elements...")
            self.logger.info(f"   Target words (filtered): {target_words}")

            # Caption and check ALL elements
            for i, detection in enumerate(sorted_detections):
                bbox = detection['bbox']
                element_img = crop_image_region(image, bbox)
                caption = self.captioner.caption(element_img)
                caption_lower = caption.lower()
                
                # FIXED: Filter out short words from caption
                caption_words = set(word for word in caption_lower.split() if len(word) >= 3)
                
                self.logger.info(f"  [{i+1:2d}] Caption: '{caption}' (conf: {detection['confidence']:.2f})")
                
                match_found = False
                match_type = None
                match_score_multiplier = 1.0
                
                # MATCH TYPE 1: EXACT SUBSTRING (highest priority)
                # "gaming" in "the logo for gaming"
                if target_lower in caption_lower:
                    match_found = True
                    match_type = 'exact'
                    match_score_multiplier = 1.0
                    self.logger.info(f"       ✓ EXACT MATCH: '{caption}' contains '{target_text}'")
                
                # MATCH TYPE 2: EXACT WORD MATCH
                # {"gaming"} & {"the", "logo", "for", "gaming"}
                elif target_words & caption_words:
                    match_found = True
                    match_type = 'word_match'
                    match_score_multiplier = 0.95
                    matched_words = target_words & caption_words
                    self.logger.info(f"       ⚡ WORD MATCH: contains words {matched_words}")
                
                # MATCH TYPE 3: SEMANTIC SIMILARITY
                # "gaming" matches "game" in "the game logo"
                else:
                    max_similarity = 0.0
                    best_matched_word = None
                    
                    for target_word in target_words:
                        for caption_word in caption_words:
                            similarity = self._compute_word_similarity(target_word, caption_word)
                            if similarity > max_similarity:
                                max_similarity = similarity
                                best_matched_word = (target_word, caption_word)
                    
                    # Accept if similarity >= 0.75
                    if max_similarity >= 0.75:
                        match_found = True
                        match_type = 'semantic'
                        match_score_multiplier = 0.80 * max_similarity
                        self.logger.info(f"       🔸 SEMANTIC MATCH: '{best_matched_word[1]}' ↔ '{best_matched_word[0]}' (sim: {max_similarity:.2f})")
                    
                    # MATCH TYPE 4: PARTIAL SUBSTRING
                    # "gam" in "game" (only for words > 3 chars)
                    elif any(target_lower in word or word in target_lower for word in caption_words if len(word) > 3):
                        match_found = True
                        match_type = 'partial'
                        match_score_multiplier = 0.70
                        self.logger.info(f"       🔹 PARTIAL MATCH: partial word overlap")
                
                # Update best match if this is better
                if match_found:
                    # IMPROVED: Weight by BOTH confidence AND match quality
                    base_score = detection['confidence']
                    
                    # Add bonus for match type
                    if match_type == 'exact':
                        type_bonus = 0.5  # Huge bonus for exact matches
                    elif match_type == 'word_match':
                        type_bonus = 0.3  # Good bonus for word matches
                    elif match_type == 'semantic':
                        type_bonus = 0.1  # Small bonus for semantic
                    else:
                        type_bonus = 0.0
                    
                    score = (base_score * match_score_multiplier) + type_bonus
                    
                    self.logger.info(f"       → Score: {score:.3f} (base:{base_score:.2f} × {match_score_multiplier:.2f} + bonus:{type_bonus:.2f})")
                    
                    if score > best_score:
                        best_score = score
                        best_match = {
                            'bbox': bbox,
                            'caption': caption,
                            'confidence': score,
                            'match_type': match_type,
                            'base_confidence': base_score
                        }
                        self.logger.info(f"       ⭐ NEW BEST MATCH!")
                        
            # ====================================================================
            # RETURN THE BEST MATCH
            # ====================================================================
            if best_match:
                center = calculate_center(best_match['bbox'])
                match_type = best_match.get('match_type', 'unknown')
                self.logger.info(f"")
                self.logger.info(f"✅ FINAL MATCH ({match_type}):")
                self.logger.info(f"   Caption: '{best_match['caption']}'")
                self.logger.info(f"   Position: {center}")
                self.logger.info(f"   Score: {best_match['confidence']:.3f}")
                
                return VisionResult(
                    success=True,
                    coordinates=center,
                    confidence=best_match['confidence'],
                    detected_text=best_match['caption'],
                    method=f"omniparser_{match_type}"
                )
            else:
                self.logger.warning(f"❌ No match found for '{target_text}' after checking {len(sorted_detections)} elements")
                
                # DEBUG: Show all captions for troubleshooting
                self.logger.warning("📋 All captions generated:")
                for i, detection in enumerate(sorted_detections[:10]):  # Show top 10
                    bbox = detection['bbox']
                    element_img = crop_image_region(image, bbox)
                    caption = self.captioner.caption(element_img)
                    self.logger.warning(f"    [{i+1}] {caption}")
                
                return VisionResult(
                    success=False,
                    coordinates=None,
                    confidence=0.0,
                    detected_text=None,
                    method="omniparser_no_match"
                )
        
        except Exception as e:
            self.logger.error(f"❌ OmniParser detection failed: {e}")
            import traceback
            traceback.print_exc()
            return VisionResult(
                success=False,
                coordinates=None,
                confidence=0.0,
                detected_text=None,
                method=f"omniparser_error"
            )

    def detect_all_elements(self, screenshot_path: Optional[str] = None) -> Dict:
        """
        Detect and caption all UI elements (useful for debugging)
        
        Returns:
            Dict with all detected elements and their captions
        """
        if not self._initialize_models():
            return {"error": "OmniParser unavailable"}
        
        try:
            # Get image
            if screenshot_path is None:
                image = pyautogui.screenshot()
                import tempfile
                import os
                debug_path = os.path.join(tempfile.gettempdir(), "omniparser_debug.png")
                image.save(debug_path)
                self.logger.info(f"💾 Debug screenshot saved to: {debug_path}")
            else:
                image = Image.open(screenshot_path)
            
            # Detect
            detections = self.detector.detect(image)
            
            # Caption each
            results = []
            for i, det in enumerate(detections):
                bbox = det['bbox']
                element_img = crop_image_region(image, bbox)
                caption = self.captioner.caption(element_img)
                center = calculate_center(bbox)
                
                results.append({
                    'id': i,
                    'bbox': bbox,
                    'center': center,
                    'caption': caption,
                    'confidence': det['confidence']
                })
            
            return {
                'total_elements': len(results),
                'elements': results
            }
        
        except Exception as e:
            return {"error": str(e)}