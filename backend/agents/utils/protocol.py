"""
Protocol definitions for agent communication
Defines message formats and channels
"""
from pydantic import BaseModel, Field
from typing import Dict, Any, Optional, List
from datetime import datetime
from enum import Enum

class MessageType(str, Enum):
    """Message types for agent communication"""
    TASK_REQUEST = "task_request"
    TASK_RESPONSE = "task_response"
    CLARIFICATION_REQUEST = "clarification_request"
    CLARIFICATION_RESPONSE = "clarification_response"
    EXECUTION_REQUEST = "execution_request"
    EXECUTION_RESPONSE = "execution_response"
    STATUS_UPDATE = "status_update"
    ERROR = "error"
    SESSION_CONTROL = "session_control"
    STORE_PREFERENCE = "store_preference"
    MEMORY_INSPECTION = "memory_inspection"  # Optional

class AgentType(str, Enum):
    """Agent types in the system"""
    LANGUAGE = "language"
    COORDINATOR = "coordinator"
    EXECUTION = "execution"
    REASONING = "reasoning"

class AgentMessage(BaseModel):
    """Base message format for all agent communication"""
    message_id: str = Field(default_factory=lambda: f"msg_{datetime.now().timestamp()}")
    message_type: MessageType
    sender: AgentType
    receiver: AgentType
    timestamp: float = Field(default_factory=lambda: datetime.now().timestamp())
    
    # Session tracking
    session_id: Optional[str] = None
    task_id: Optional[str] = None
    
    # Payload
    payload: Dict[str, Any] = Field(default_factory=dict)
    
    # Response tracking
    response_to: Optional[str] = None  # message_id this is responding to
    
    class Config:
        use_enum_values = True

class TaskMessage(BaseModel):
    """Task specification message"""
    action: str
    context: str  # local, web, system, reasoning
    params: Dict[str, Any]
    priority: str = "normal"
    timeout: int = 30
    retry_count: int = 3
    depends_on: Optional[str] = None

class ClarificationMessage(BaseModel):
    """Clarification request/response"""
    question: Optional[str] = None  # For requests
    answer: Optional[str] = None    # For responses
    context: Dict[str, Any] = Field(default_factory=dict)

class ExecutionResult(BaseModel):
    """Execution result message"""
    status: str  # success, failed, pending
    details: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None

# Channel names for pub/sub
class Channels:
    """Redis/Broker channel names"""
    LANGUAGE_INPUT = "language.input" 
    LANGUAGE_OUTPUT = "language.output"
    LANGUAGE_TO_COORDINATOR = "language.to.coordinator"
    
    COORDINATOR_TO_EXECUTION = "coordinator.to.execution"
    COORDINATOR_TO_LANGUAGE = "coordinator.to.language"
    COORDINATOR_TO_REASONING = "coordinator.to.reasoning"
    
    EXECUTION_INPUT = "execution.input"
    EXECUTION_OUTPUT = "execution.output"
    EXECUTION_TO_COORDINATOR = "execution.to.coordinator"
    
    REASONING_INPUT = "reasoning.input"
    REASONING_OUTPUT = "reasoning.output"
    REASONING_TO_COORDINATOR = "reasoning.to.coordinator"
    
    BROADCAST = "broadcast"  # For status updates
    SESSION_CONTROL = "session_control"
    PREFERENCE_STORAGE = "preference_storage"
    
    INTERRUPT_CONTROL="interrupt_control"