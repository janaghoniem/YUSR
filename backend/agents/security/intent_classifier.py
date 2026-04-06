"""
intent_classifier.py — Lightweight pre-LLM intent classification
Zero token cost. Runs in <10ms.
Classifies user input as SAFE, SUSPICIOUS, or MALICIOUS.
"""

import re
import logging
from dataclasses import dataclass
from typing import List, Tuple, Optional
from enum import Enum

logger = logging.getLogger(__name__)


class IntentClass(Enum):
    SAFE = "safe"
    SUSPICIOUS = "suspicious"
    MALICIOUS = "malicious"


@dataclass
class IntentResult:
    classification: IntentClass
    confidence: float  # 0.0 to 1.0
    reasons: List[str]
    requires_confirmation: bool = False


class IntentClassifier:
    """
    Zero-token intent classifier using weighted keyword scoring.
    More sophisticated than simple pattern matching because it:
    1. Uses weighted scores (not just presence/absence)
    2. Detects combinations (e.g., "cmd" + "delete" is worse than either alone)
    3. Has configurable thresholds
    """
    
    def __init__(self):
        # Weighted keywords — higher score = more suspicious
        # Format: (keyword_pattern, weight, category)
        self.keywords = [
            # CRITICAL WEIGHT (10.0) — Immediate block
            (r"delete all files", 10.0, "destructive"),
            (r"permanently delete", 10.0, "destructive"),
            (r"rm -rf", 10.0, "destructive"),
            (r"format drive", 10.0, "destructive"),
            (r"wipe all", 10.0, "destructive"),
            (r"del /f /q", 10.0, "destructive"),
            
            # HIGH WEIGHT (8.0) — Credential harvesting
            (r"show me.*saved.*password", 8.0, "credential_theft"),
            (r"saved wifi passwords", 8.0, "credential_theft"),
            (r"extract.*password", 8.0, "credential_theft"),
            (r"netsh wlan", 8.0, "credential_theft"),
            (r"key=clear", 8.0, "credential_theft"),
            
            # HIGH WEIGHT (7.0) — System commands
            (r"cmd.*delete", 7.0, "system_manipulation"),
            (r"powershell.*delete", 7.0, "system_manipulation"),
            (r"command prompt.*delete", 7.0, "system_manipulation"),
            
            # MEDIUM WEIGHT (5.0) — Dangerous libraries
            (r"using pathlib", 5.0, "library_abuse"),
            (r"using shutil", 5.0, "library_abuse"),
            (r"import os", 5.0, "library_abuse"),
            (r"import subprocess", 5.0, "library_abuse"),
            
            # MEDIUM WEIGHT (4.0) — File system access
            (r"list all files in", 4.0, "enumeration"),
            (r"enumerate.*files", 4.0, "enumeration"),
            (r"read.*etc/passwd", 4.0, "enumeration"),
            
            # LOW WEIGHT (2.0) — Context-dependent
            (r"open cmd", 2.0, "shell_access"),
            (r"open command prompt", 2.0, "shell_access"),
            (r"open powershell", 2.0, "shell_access"),
        ]
        
        # Keywords that REDUCE suspicion (e.g., legitimate contexts)
        # Format: (keyword_pattern, weight_reduction)
        self.safe_modifiers = [
            (r"my own", -3.0),
            (r"my personal", -2.0),
            (r"for my account", -2.0),
            (r"with my permission", -3.0),
        ]
        
        # Thresholds
        self.malicious_threshold = 5.0  # Score >= this → MALICIOUS
        self.suspicious_threshold = 2.0  # Score >= this → SUSPICIOUS
        
        # Patterns that ALWAYS block regardless of score
        self.always_block_patterns = [
            r"ignore previous instructions",
            r"disregard your instructions",
            r"forget everything",
            r"new system prompt",
            r"system override",
            r"important system note",
        ]
        
    def classify(self, text: str) -> IntentResult:
        """
        Classify user input intent.
        Returns IntentResult with classification, confidence, and reasons.
        """
        if not text:
            return IntentResult(
                classification=IntentClass.SAFE,
                confidence=1.0,
                reasons=["Empty input"]
            )
        
        text_lower = text.lower()
        reasons = []
        total_score = 0.0
        max_possible_score = 0.0
        
        # Check always-block patterns first (zero tolerance)
        for pattern in self.always_block_patterns:
            if re.search(pattern, text_lower):
                logger.warning(f"🚫 Intent classifier: Always-block pattern '{pattern}'")
                return IntentResult(
                    classification=IntentClass.MALICIOUS,
                    confidence=0.99,
                    reasons=[f"Always-block pattern: {pattern}"],
                    requires_confirmation=False
                )
        
        # Calculate weighted score from malicious keywords
        for pattern, weight, category in self.keywords:
            if re.search(pattern, text_lower):
                total_score += weight
                reasons.append(f"Matched '{pattern}' (weight: {weight}, category: {category})")
                max_possible_score += weight
        
        # Apply safe modifiers (reduce score)
        for pattern, reduction in self.safe_modifiers:
            if re.search(pattern, text_lower):
                total_score += reduction  # reduction is negative
                reasons.append(f"Safe modifier '{pattern}' reduced score by {abs(reduction)}")
        
        # Normalize confidence (0-1) based on score vs threshold
        if total_score >= self.malicious_threshold:
            confidence = min(0.99, total_score / (self.malicious_threshold * 2))
            classification = IntentClass.MALICIOUS
            requires_confirmation = False
        elif total_score >= self.suspicious_threshold:
            confidence = total_score / self.malicious_threshold
            classification = IntentClass.SUSPICIOUS
            requires_confirmation = True
        else:
            confidence = 1.0 - min(0.95, total_score / self.suspicious_threshold)
            classification = IntentClass.SAFE
            requires_confirmation = False
        
        # Log result
        if classification == IntentClass.MALICIOUS:
            logger.warning(f"🚫 Intent: MALICIOUS (score={total_score:.1f}, confidence={confidence:.2%})")
        elif classification == IntentClass.SUSPICIOUS:
            logger.info(f"⚠️ Intent: SUSPICIOUS (score={total_score:.1f}, confidence={confidence:.2%})")
        else:
            logger.debug(f"✅ Intent: SAFE (score={total_score:.1f})")
        
        return IntentResult(
            classification=classification,
            confidence=confidence,
            reasons=reasons,
            requires_confirmation=requires_confirmation
        )


# Singleton instance
_intent_classifier = None

def get_intent_classifier() -> IntentClassifier:
    global _intent_classifier
    if _intent_classifier is None:
        _intent_classifier = IntentClassifier()
    return _intent_classifier


def classify_intent(text: str) -> IntentResult:
    """Convenience function to classify intent."""
    return get_intent_classifier().classify(text)