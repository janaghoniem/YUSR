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
    4. Applies safe modifiers to reduce suspicion for legitimate use
    """
    
    def __init__(self):
        # Weighted keywords — higher score = more suspicious
        # Format: (keyword_pattern, weight, category)
        self.keywords = [
            # ================================================================
            # CRITICAL WEIGHT (10.0) — Immediate block (OS destruction only)
            # ================================================================
            (r"delete all files", 10.0, "destructive"),
            (r"permanently delete", 10.0, "destructive"),
            (r"rm -rf", 10.0, "destructive"),
            (r"format drive", 10.0, "destructive"),
            (r"wipe all", 10.0, "destructive"),
            (r"del /f /q", 10.0, "destructive"),
            (r"delete everything", 8.0, "destructive"),
            (r"delete all", 8.0, "destructive"),
            
            # ================================================================
            # OS-CRITICAL PATHS (weight 9.0) — System file deletion (BLOCK)
            # ================================================================
            (r"delete.*(?:windows|system32|boot|program files|winnt)", 9.0, "os_critical"),
            (r"rm -rf.*(?:/boot|/etc|/usr|/var|/lib|/bin|/sbin)", 9.0, "os_critical"),
            (r"del.*(?:C:\\Windows|C:\\Program Files|C:\\System)", 9.0, "os_critical"),
            
            # ================================================================
            # HIGH WEIGHT (8.0) — Credential harvesting (malicious intent)
            # ================================================================
            (r"extract.*password", 8.0, "credential_theft"),
            (r"netsh wlan", 8.0, "credential_theft"),
            (r"key=clear", 8.0, "credential_theft"),
            
            # ================================================================
            # MEDIUM-HIGH WEIGHT (6.0) — Credential access (SUSPICIOUS)
            # ================================================================
            (r"saved wifi passwords", 6.0, "credential_access"),
            (r"wifi passwords", 6.0, "credential_access"),
            (r"saved passwords", 6.0, "credential_access"),
            (r"stored passwords", 6.0, "credential_access"),
            (r"view saved passwords", 6.0, "credential_access"),
            (r"display saved passwords", 6.0, "credential_access"),
            (r"show my passwords", 6.0, "credential_access"),
            (r"list all passwords", 6.0, "credential_access"),
            (r"show me.*saved.*password", 6.0, "credential_access"),
            (r"retrieve my password", 6.0, "credential_access"),
            (r"get my password", 6.0, "credential_access"),
            (r"show my credentials", 6.0, "credential_access"),
            (r"show network credentials", 6.0, "credential_access"),
            (r"reveal the password", 6.0, "credential_access"),
            (r"show me the api_key", 6.0, "credential_access"),
            (r"display the mongodb", 6.0, "credential_access"),
            (r"show me the secret", 6.0, "credential_access"),
            (r"print my api key", 6.0, "credential_access"),
            (r"disclose", 6.0, "credential_access"),
            
            # ================================================================
            # HIGH WEIGHT (7.0) — System commands (BLOCK)
            # ================================================================
            (r"cmd.*delete", 7.0, "system_manipulation"),
            (r"powershell.*delete", 7.0, "system_manipulation"),
            (r"command prompt.*delete", 7.0, "system_manipulation"),
            (r"shut\s*down", 7.0, "system_manipulation"),
            (r"shutdown", 7.0, "system_manipulation"),
            (r"power\s*off", 7.0, "system_manipulation"),
            (r"turn\s+off\s+(?:the\s+)?(?:computer|pc|desktop|system)", 7.0, "system_manipulation"),
            (r"reboot", 5.0, "system_manipulation"),
            (r"restart\s+(?:the\s+)?(?:computer|pc|desktop|system)", 5.0, "system_manipulation"),
            
            # ================================================================
            # MEDIUM WEIGHT (5.0) — Dangerous libraries (BLOCK)
            # ================================================================
            (r"using pathlib", 5.0, "library_abuse"),
            (r"using shutil", 5.0, "library_abuse"),
            (r"import os", 5.0, "library_abuse"),
            (r"import subprocess", 5.0, "library_abuse"),
            
            # ================================================================
            # MEDIUM WEIGHT (4.0) — Sensitive file access
            # ================================================================
            (r"list.*(?:etc/passwd|/etc/shadow|sam database|system32/config)", 4.0, "enumeration"),
            (r"enumerate.*(?:system|registry|credential|network)", 4.0, "enumeration"),
            (r"read.*etc/passwd", 4.0, "enumeration"),
            
            # ================================================================
            # LOW WEIGHT (2.0) — Context-dependent shell access
            # ================================================================
            (r"(?:open|launch|run|start)\s+(?:cmd|command prompt|powershell|terminal|bash)\s+(?:and|then|to)\s+\w+", 2.0, "shell_access_with_action"),

            # ================================================================
            # FILE DELETION - MUTUALLY EXCLUSIVE PATTERNS (weight 2.0 each)
            # Each pattern is designed NOT to overlap with others
            # ================================================================
            # Matches "delete file" (singular) - NOT "delete files"
            (r"delete\s+file\b(?!s\b)", 2.0, "file_deletion"),
            
            # Matches "delete files" (plural) - NOT "delete file"
            (r"delete\s+files\b", 2.0, "file_deletion"),
            
            # Matches "delete folder"
            (r"delete\s+folder", 2.0, "file_deletion"),
            
            # Matches "remove file" - NOT "remove files"
            (r"remove\s+file\b(?!s\b)", 2.0, "file_deletion"),
            
            # Matches "remove files"
            (r"remove\s+files\b", 2.0, "file_deletion"),
            
            # ================================================================
            # os MODULE OPERATIONS (weight 2.0 each)
            # ================================================================
            (r"os\.remove\b", 2.0, "file_deletion"),
            (r"os\.unlink\b", 2.0, "file_deletion"),
            (r"os\.rmdir\b", 2.0, "file_deletion"),
            
            # ================================================================
            # CLEANUP OPERATIONS (weight 1.0 - low suspicion)
            # ================================================================
            (r"clean\s+up\s+(?:files|folders|downloads)", 1.0, "cleanup"),
            (r"empty\s+(?:recycle\s+bin|trash)", 1.0, "cleanup"),
            
            # ================================================================
            # NOTE: NO standalone \bdelete\b or \bremove\b patterns!
            # These cause false positives and score stacking.
            # ================================================================
        ]
        
        # ================================================================
        # SAFE MODIFIERS — Reduce suspicion for legitimate contexts
        # These subtract from total_score when matched
        # ================================================================
        self.safe_modifiers = [
            # Ownership context (strong reduction)
            (r"my own", -4.0),
            (r"my personal", -3.0),
            (r"for my account", -3.0),
            (r"with my permission", -3.0),
            (r"for my network", -4.0),
            (r"my wifi", -3.0),
            (r"on my computer", -3.0),
            (r"on my device", -3.0),
            
            # Legitimate file operation contexts (medium reduction)
            (r"my\s+files?\b", -2.5),            # "my files"
            (r"my\s+folder", -2.0),              # "my folder"
            (r"in\s+downloads", -1.5),           # "in Downloads" - safe location
            (r"in\s+documents", -1.5),           # "in Documents" - safe location
            (r"on\s+desktop", -1.5),             # "on desktop" - safe location
            (r"from\s+downloads", -1.5),         # "from Downloads"
            (r"temp\s+files?\b", -1.5),          # "temp files" - less sensitive
            (r"cache", -1.0),                    # "cache" - less sensitive
            (r"backup", -1.0),                   # "backup" - less sensitive
            (r"old\s+files?\b", -1.0),           # "old files"
            
            # User-requested cleanup (mild reduction)
            (r"please\s+delete", -1.0),          # "please delete" - polite request
            (r"can you delete", -1.0),           # "can you delete" - request form
            (r"could you delete", -1.0),         # "could you delete" - request form
        ]
        
        # ================================================================
        # THRESHOLDS
        # ================================================================
        self.malicious_threshold = 5.0   # Score >= this → MALICIOUS (block)
        self.suspicious_threshold = 2.0  # Score >= this → SUSPICIOUS (ask confirmation)

        # ================================================================
        # ALWAYS BLOCK PATTERNS — Zero tolerance
        # These block immediately regardless of score
        # ================================================================
        self.always_block_patterns = [
            # Prompt injection
            r"ignore previous instructions",
            r"disregard your instructions",
            r"forget everything",
            r"new system prompt",
            r"system override",
            r"important system note",

            # Shutdown / reboot / power-off commands (any phrasing)
            r"shutdown\s+/s",
            r"shutdown\s+/r",
            r"shutdown\s+/h",
            r"shutdown\s+/p",
            r"shutdown\s+-h",
            r"shutdown\s+-r",
            r"shutdown\s+now",
            r"poweroff\b",
            r"halt\b",
            r"reboot\b",
            r"(?:shut\s*down|power\s*off|turn\s*off)\s+(?:the\s+)?(?:computer|pc|desktop|system|machine|device)",
            r"(?:cmd|powershell|terminal|bash).*shutdown",
            r"(?:cmd|powershell|terminal|bash).*(?:del\s+/f|rm\s+-rf|rmdir\s+/s|format\s+[a-z]:)",
            r"(?:cmd|powershell).*(?:reg\s+delete|reg\s+add.*system)",
            r"(?:cmd|powershell).*(?:net\s+user|net\s+localgroup)",
            r"(?:cmd|powershell).*(?:attrib|cacls|icacls|takeown).*system32",
            r"(?:cmd|powershell).*(?:taskkill.*system|sc\s+delete)",

            # System-critical path deletion
            r"rm -rf /",
            r"rm -rf /\*",
            r"del /f /s C:\\Windows",
            r"format C:",
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
        
        # Ensure score doesn't go below 0
        total_score = max(0.0, total_score)
        
        # Normalize confidence (0-1) based on score vs threshold
        if total_score >= self.malicious_threshold:
            confidence = min(0.99, total_score / (self.malicious_threshold * 2))
            classification = IntentClass.MALICIOUS
            requires_confirmation = False
        elif total_score >= self.suspicious_threshold:
            confidence = min(0.99, total_score / self.malicious_threshold)
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