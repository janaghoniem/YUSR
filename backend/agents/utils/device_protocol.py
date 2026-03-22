"""
device_protocol.py
Device Communication Protocol
Defines request/response models for mobile-backend communication
Handles Android ↔ Backend interaction for UI automation

Architecture:
- Android sends semantic UI tree to backend
- Backend sends JSON actions back to Android
- Supports streaming for low-latency perception
"""

from pydantic import BaseModel, Field, ConfigDict
from typing import List, Dict, Any, Optional, Literal
from datetime import datetime
import json


# ============================================================================
# UI TREE MODELS (Android → Backend)
# ============================================================================

class UIElement(BaseModel):
    """Represents a single UI element in the accessibility tree"""
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "element_id": 1,
            "type": "button",
            "text": "Send",
            "content_description": "Send email button",
            "clickable": True,
            "bounds": {"left": 10, "top": 50, "right": 100, "bottom": 80},
            "parent_id": 0,
            "child_ids": []
        }
    })
    
    element_id: int
    type: str  # "button", "textfield", "text", "image", "checkbox", etc.
    text: Optional[str] = None
    content_description: Optional[str] = None
    hint_text: Optional[str] = None
    clickable: bool = False
    focusable: bool = False
    scrollable: bool = False
    
    # Bounding box coordinates (for visual reference)
    bounds: Optional[Dict[str, int]] = None  # {"left": 10, "top": 50, "right": 100, "bottom": 80}
    
    # Parent/child relationships
    parent_id: Optional[int] = None
    child_ids: List[int] = Field(default_factory=list)
    
    # Additional metadata
    resource_id: Optional[str] = None
    package_name: Optional[str] = None
    class_name: Optional[str] = None
    enabled: bool = True
    visibility: Literal["visible", "invisible", "gone"] = "visible"


class SemanticUITree(BaseModel):
    """Complete semantic representation of current screen"""
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "screen_id": "screen_1234567890",
            "device_id": "device_abc123",
            "app_name": "Gmail",
            "app_package": "com.google.android.gm",
            "screen_name": "Compose",
            "elements": [
                {
                    "element_id": 1,
                    "type": "button",
                    "text": "Send"
                }
            ],
            "timestamp": 1234567890.123,
            "screen_width": 1080,
            "screen_height": 2340
        }
    })
    
    screen_id: str = Field(default_factory=lambda: f"screen_{datetime.now().timestamp()}")
    device_id: str  # Unique Android device identifier
    app_name: str  # Current foreground app
    app_package: str
    screen_name: Optional[str] = None  # Activity name or screen context
    elements: List[UIElement] = Field(default_factory=list)
    
    # Optional screenshot (base64 encoded)
    screenshot_base64: Optional[str] = None
    
    # Metadata
    timestamp: float = Field(default_factory=lambda: datetime.now().timestamp())
    screen_width: int
    screen_height: int
    
    def get_element_by_id(self, element_id: int) -> Optional[UIElement]:
        """Find element by ID"""
        return next((e for e in self.elements if e.element_id == element_id), None)
    
    def get_elements_by_text(self, text: str, partial: bool = True) -> List[UIElement]:
        """Find elements by text"""
        if partial:
            return [e for e in self.elements if e.text and text.lower() in e.text.lower()]
        return [e for e in self.elements if e.text and e.text.lower() == text.lower()]
    
    def get_clickable_elements(self) -> List[UIElement]:
        """Get all clickable elements"""
        return [e for e in self.elements if e.clickable and e.visibility == "visible"]
    
    def get_text_input_fields(self) -> List[UIElement]:
        """Get all input fields (EditText, etc)"""
        return [e for e in self.elements if "edit" in e.type.lower() or "text" in e.type.lower()]
    
    def to_semantic_string(self) -> str:
        """Convert to compact semantic representation for LLM"""
        lines = [f"Screen: {self.screen_name or self.app_name}"]
        
        for elem in self.elements:
            if elem.visibility != "visible":
                continue
            # Keep disabled elements that have a content_description
            # (e.g. Send button may become disabled but is still targetable)
            if not elem.enabled and not elem.content_description:
                continue
                
            # Format: [id] type "text" | description
            text_part = f'"{elem.text}"' if elem.text else "(no text)"
            desc_part = f" | {elem.content_description}" if elem.content_description else ""
            
            line = f"[{elem.element_id}] {elem.type.upper()} {text_part}{desc_part}"
            if not elem.enabled:
                line += " [DISABLED]"
            if elem.clickable:
                line += " [CLICKABLE]"
            if elem.focusable:
                line += " [FOCUSABLE]"
                
            lines.append(line)
        
        return "\n".join(lines)


# ============================================================================
# ACTION MODELS (Backend → Android)
# ============================================================================

"""
PATCH for device_protocol.py — replace only the UIAction class.

Two changes from the original:
  1. action_type Literal gains "coordinate_tap"
  2. Two new optional fields: x, y (for coordinate_tap only)

Everything else in device_protocol.py stays identical.
"""

class UIAction(BaseModel):
    """Atomic UI action to execute on device"""

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "action_id":   "action_1234567890",
            "action_type": "click",
            "element_id":  1,
            "max_retries": 3,
        }
    })

    action_id: str = Field(
        default_factory=lambda: f"action_{datetime.now().timestamp()}"
    )

    # ── ADD "coordinate_tap" to the Literal ──────────────────────────────
    action_type: Literal[
        "click", "type", "scroll", "wait",
        "global_action", "long_click", "double_click",
        "coordinate_tap", "swipe"          # ← new: absolute pixel tap (interstitials)
    ]

    # Action parameters
    element_id:    Optional[int]   = None   # click / type / long_click / double_click
    text:          Optional[str]   = None   # type
    direction:     Optional[Literal["up", "down", "left", "right"]] = None  # scroll
    duration:      Optional[int]   = None   # wait (ms) or long_click (ms)
    global_action: Optional[Literal[
        "HOME", "BACK", "RECENTS", "POWER", "VOLUME_UP", "VOLUME_DOWN"
    ]] = None

    # ── ADD x, y for coordinate_tap ──────────────────────────────────────
    x: Optional[int] = None   # absolute pixel X (coordinate_tap only)
    y: Optional[int] = None   # absolute pixel Y (coordinate_tap only)

    # Metadata
    retry_count: int = 0
    max_retries: int = 3
    timeout_ms:  int = 5000

class ActionResult(BaseModel):
    """Result of executing an action"""
    success: bool = False
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "action_id": "action_1234567890",
            "success": True,
            "execution_time_ms": 234,
            "retry_count": 0
        }
    })
    
    action_id: str
    success: bool
    error: Optional[str] = None
    
    # Post-action UI state
    new_tree: Optional[SemanticUITree] = None
    
    # Metadata
    execution_time_ms: int
    retry_count: int = 0


# ============================================================================
# TASK EXECUTION MODELS (Coordinator → Mobile Strategy)
# ============================================================================

class MobileTaskRequest(BaseModel):
    """Request sent to mobile execution handler"""
    task_id: str
    ai_prompt: str  # Natural language instruction
    device_id: str
    session_id: str
    
    # Additional context
    context: Dict[str, Any] = Field(default_factory=dict)
    extra_params: Dict[str, Any] = Field(default_factory=dict)
    
    # Constraints
    max_steps: int = 15  # Max ReAct iterations (increased for task completion)
    timeout_seconds: int = 30
    
    class Config:
        json_schema_extra = {
            "example": {
                "task_id": "task_1",
                "ai_prompt": "Click the Send button to send the email",
                "device_id": "device_abc123",
                "session_id": "session_xyz"
            }
        }


class MobileTaskResult(BaseModel):
    """Result from mobile task execution"""
    task_id: str
    status: Literal["success", "failed", "timeout", "error"]
    
    # Execution details
    steps_taken: int
    actions_executed: List[UIAction] = Field(default_factory=list)
    final_tree: Optional[SemanticUITree] = None
    
    # Error info
    error: Optional[str] = None
    error_step: Optional[int] = None
    
    # Metadata
    execution_time_ms: int
    completion_reason: Optional[str] = None
    
    # LLM usage metrics (populated by MobileReActStrategy)
    token_usage: Optional[Dict[str, int]] = None  # {"prompt": N, "completion": N, "total": N}
    llm_calls: int = 0
    
    class Config:
        json_schema_extra = {
            "example": {
                "task_id": "task_1",
                "status": "success",
                "steps_taken": 2,
                "actions_executed": [
                    {
                        "action_id": "action_1",
                        "action_type": "click",
                        "element_id": 1
                    }
                ],
                "execution_time_ms": 2500
            }
        }


# ============================================================================
# HTTP ENDPOINT CONTRACTS
# ============================================================================

class GetUITreeRequest(BaseModel):
    """Request UI tree from device"""
    device_id: str
    include_bounds: bool = True
    include_screenshot: bool = False  # Can be expensive


class ExecuteActionRequest(BaseModel):
    """Request to execute action on device"""
    device_id: str
    action: UIAction
    wait_for_result: bool = True


class ExecuteActionResponse(BaseModel):
    """Response from action execution"""
    result: ActionResult


# ============================================================================
# MESSAGE BROKER CONTRACTS (Pub/Sub)
# ============================================================================

class MobilePerceptionMessage(BaseModel):
    """Published by Android → Backend (perception channel)"""
    device_id: str
    session_id: str
    ui_tree: SemanticUITree
    timestamp: float = Field(default_factory=lambda: datetime.now().timestamp())


class MobileActionMessage(BaseModel):
    """Published by Backend → Android (action channel)"""
    device_id: str
    session_id: str
    action: UIAction
    timestamp: float = Field(default_factory=lambda: datetime.now().timestamp())


class MobileActionResultMessage(BaseModel):
    """Published by Android → Backend (result channel)"""
    device_id: str
    session_id: str
    result: ActionResult
    timestamp: float = Field(default_factory=lambda: datetime.now().timestamp())
