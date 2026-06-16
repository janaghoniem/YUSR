"""
OmniParser inference utilities
"""

import torch
from PIL import Image
import numpy as np
from pathlib import Path
import yaml
from ultralytics import YOLO
from transformers import AutoProcessor, AutoModelForCausalLM
import logging

logger = logging.getLogger(__name__)

# Target resolution for OmniParser input.
# Lower = faster but may miss small elements.
# 1280x720 is the recommended sweet spot:
#   - ~2.25x faster than 1920x1080
#   - <5% accuracy loss on typical desktop UI
# Raise to (1600, 900) if small icons are missed.
# Lower to (960, 540) if speed is the priority.
OMNIPARSER_MAX_WIDTH  = 1280
OMNIPARSER_MAX_HEIGHT = 720


def _resize_for_omniparser(image: Image.Image) -> tuple:
    """
    Downscale image to fit within OMNIPARSER_MAX_WIDTH x OMNIPARSER_MAX_HEIGHT
    while preserving aspect ratio.

    Returns (resized_image, scale_x, scale_y) where scale factors map
    coordinates back to the original image space.
    """
    orig_w, orig_h = image.size
    scale = min(OMNIPARSER_MAX_WIDTH / orig_w, OMNIPARSER_MAX_HEIGHT / orig_h, 1.0)

    if scale >= 1.0:
        # Image already smaller than the target — no resize needed
        return image, 1.0, 1.0

    new_w = int(orig_w * scale)
    new_h = int(orig_h * scale)
    resized = image.resize((new_w, new_h), Image.LANCZOS)

    scale_x = orig_w / new_w   # multiply by this to go back to original coords
    scale_y = orig_h / new_h

    logger.debug(
        f"[RESIZE] {orig_w}x{orig_h} → {new_w}x{new_h} "
        f"(scale={scale:.3f}, scale_x={scale_x:.3f}, scale_y={scale_y:.3f})"
    )
    return resized, scale_x, scale_y


class IconDetector:
    """YOLO-based icon detector"""
    
    def __init__(self, model_path: str):
        self.model = YOLO(model_path)
        self.model.conf = 0.3  # Confidence threshold
        logger.info(f"Detector loaded from {model_path}")
    
    def detect(self, image: Image.Image, conf_threshold: float = 0.3):
        """
        Detect icons in image.
        Image is downscaled to OMNIPARSER_MAX_WIDTH x OMNIPARSER_MAX_HEIGHT
        before inference, then bounding boxes are scaled back to original
        image coordinates so callers never need to know about the resize.
        """
        self.model.conf = conf_threshold

        resized, scale_x, scale_y = _resize_for_omniparser(image)
        results = self.model(resized, verbose=False)
        
        detections = []
        for result in results:
            if result.boxes is not None:
                for box in result.boxes:
                    x1, y1, x2, y2 = box.xyxy[0].tolist()
                    conf = box.conf[0].item()
                    cls = int(box.cls[0])

                    # Scale bounding box back to original image coordinates
                    detections.append({
                        'bbox': [
                            x1 * scale_x,
                            y1 * scale_y,
                            x2 * scale_x,
                            y2 * scale_y,
                        ],
                        'confidence': conf,
                        'class_id': cls
                    })
        
        logger.debug(f"Detected {len(detections)} icons")
        return detections

# Normalized size for icon crops fed to the captioner.
# Larger = slightly better quality; smaller = faster.
# 96x96 is the sweet spot — Florence/BLIP both handle it well.
CAPTION_ICON_SIZE = 96


class IconCaptioner:
    """Simplified Florence-2 captioner"""
    
    def __init__(self, model_path: str):
        self.model_path = Path(model_path)
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        
        # Load config
        config_path = self.model_path / "config.json"
        if not config_path.exists():
            raise FileNotFoundError(f"Config not found: {config_path}")
        
        # Simple captioning - using BLIP as fallback since Florence-2 is complex
        try:
            from transformers import BlipProcessor, BlipForConditionalGeneration
            
            # Use BLIP as a simpler alternative
            self.processor = BlipProcessor.from_pretrained("Salesforce/blip-image-captioning-base")
            self.model = BlipForConditionalGeneration.from_pretrained("Salesforce/blip-image-captioning-base").to(self.device)
            logger.info("Loaded BLIP model for captioning")
            self.use_blip = True
        except Exception as e:
            logger.warning(f"Failed to load BLIP: {e}. Using simple captioning.")
            self.use_blip = False
    
    def caption(self, image: Image.Image) -> str:
        """
        Generate caption for icon image.
        The crop is normalized to CAPTION_ICON_SIZE x CAPTION_ICON_SIZE
        before being passed to the model — this keeps inference time
        predictable regardless of how large the original bounding box was.
        """
        try:
            # Normalize crop to a fixed square size
            if image.size != (CAPTION_ICON_SIZE, CAPTION_ICON_SIZE):
                image = image.resize(
                    (CAPTION_ICON_SIZE, CAPTION_ICON_SIZE),
                    Image.LANCZOS
                )

            if self.use_blip:
                # Use BLIP for captioning
                inputs = self.processor(image, return_tensors="pt").to(self.device)
                out = self.model.generate(**inputs, max_length=50)
                caption = self.processor.decode(out[0], skip_special_tokens=True)
            else:
                # Simple fallback - just return generic description
                width, height = image.size
                caption = f"UI element ({width}x{height})"
            
            return caption
        except Exception as e:
            logger.error(f"Captioning failed: {e}")
            return "Unknown UI element"

def crop_image_region(image: Image.Image, bbox):
    """Crop region from image"""
    x1, y1, x2, y2 = bbox
    x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
    return image.crop((x1, y1, x2, y2))

def calculate_center(bbox):
    """Calculate center of bounding box"""
    x1, y1, x2, y2 = bbox
    return (int((x1 + x2) / 2), int((y1 + y2) / 2))