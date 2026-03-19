"""
Mobile Action Handler
Routes mobile tasks to ReAct strategy ONLY

This sits between:
- Coordinator (sends ActionTask with device="mobile")
- MobileStrategy (ReAct loop with Groq LLM for ALL mobile tasks)
- Android Device (HTTP API endpoints)

Strategy:
- Use ReAct loop for ALL mobile tasks (dynamic, flexible)
- No hardcoded task detection or app-specific automation
"""

import logging
import asyncio
from typing import Dict, Any, Optional

from agents.utils.protocol import (
    ExecutionResult, MessageType, AgentType, Channels
)
from agents.utils.broker import broker
from agents.execution_agent.strategies.mobile_strategy import (
    execute_mobile_task, MobileStrategy, MobileTaskRequest
)

logger = logging.getLogger(__name__)


class MobileActionHandler:
    """Handles execution of mobile tasks with ReAct loop ONLY"""
    
    def __init__(self, device_id: str = "default_device"):
        """
        Initialize mobile handler
        
        Args:
            device_id: Android device to target (can be configured per session)
        """
        self.device_id = device_id
        self.mobile_strategy = MobileStrategy(device_id)
        # NOTE: AccessibilityAutomationHandler removed - use ReAct loop for ALL mobile tasks
        logger.info(f"✅ Initialized MobileActionHandler for device {device_id} (ReAct loop only)")
    
    async def handle_action_task(
        self,
        task_id: str,
        session_id: str,
        task: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> ExecutionResult:
        """
        Handle a single action task. 
        Uses **kwargs to safely capture 'task_data' or 'task' from Coordinator.
        """
        # Resolve which variable contains the task info
        actual_task = task or kwargs.get("task_data")
        
        if not actual_task:
            logger.error(f"❌ No task data provided for {task_id}")
            return ExecutionResult(
                task_id=task_id,
                status="failed",
                error="Task data missing in request"
            )

        logger.info(f"📱 Handling mobile task: {task_id}")
        logger.info(f"   Prompt: {actual_task.get('ai_prompt')}")
        
        try:
            # Validate task
            ai_prompt = actual_task.get("ai_prompt")
            if not ai_prompt:
                return ExecutionResult(
                    task_id=task_id,
                    status="failed",
                    error="Missing ai_prompt"
                )
            
            # Extract device-specific settings
            extra = actual_task.get("extra_params", {})
            device_id = extra.get("device_id", self.device_id)
            
            # ====================================================================
            # ALWAYS USE REACT LOOP FOR MOBILE TASKS
            # ====================================================================
            logger.info(f"🤖 Using ReAct loop for mobile task: {ai_prompt}")
            
            max_steps = extra.get("max_steps", 15)
            timeout_seconds = extra.get("timeout_seconds", 30)
            
            # Build mobile task request
            mobile_task = MobileTaskRequest(
                task_id=task_id,
                ai_prompt=ai_prompt,
                device_id=device_id,
                session_id=session_id or "default_session",  # Handle None
                context=extra,
                extra_params=extra,
                max_steps=max_steps,
                timeout_seconds=timeout_seconds
            )
            
            # Execute with timeout
            try:
                result = await asyncio.wait_for(
                    self.mobile_strategy.execute_task(mobile_task),
                    timeout=float(timeout_seconds)
                )
            except asyncio.TimeoutError:
                # Pull whatever metrics the strategy accumulated before timeout
                strat = self.mobile_strategy
                logger.warning(
                    f"⏱️ Task timed out after {timeout_seconds}s — "
                    f"steps: {len(strat.action_history)}, "
                    f"LLM calls: {strat.total_llm_calls}, "
                    f"tiers: {dict(strat.tier_stats)}"
                )
                return ExecutionResult(
                    task_id=task_id,
                    status="failed",
                    error=f"Task timed out after {timeout_seconds}s",
                    metadata={
                        "token_usage": dict(strat.token_usage),
                        "llm_calls": strat.total_llm_calls,
                        "steps_taken": len(strat.action_history),
                        "execution_time_ms": int(timeout_seconds * 1000),
                        "tier_stats": dict(strat.tier_stats),
                    }
                )
            
            success = result.status == "success"
            return ExecutionResult(
                status="success" if success else "failed",
                details=result.completion_reason or "Mobile task executed",
                error=result.error,
                metadata={
                    "token_usage": result.token_usage or {},
                    "llm_calls": result.llm_calls,
                    "steps_taken": result.steps_taken,
                    "execution_time_ms": result.execution_time_ms,
                    "tier_stats": dict(self.mobile_strategy.tier_stats),
                }
            )
        
        except Exception as e:
            logger.error(f"❌ Error handling mobile task: {e}", exc_info=True)
            return ExecutionResult(
                task_id=task_id,
                status="failed",
                error=str(e)
            )
        
# ============================================================================
# GLOBAL HANDLER INSTANCE
# ============================================================================

mobile_handler: Optional[MobileActionHandler] = None


def initialize_mobile_handler(device_id: str = "default_device"):
    """Initialize the global mobile handler"""
    global mobile_handler
    mobile_handler = MobileActionHandler(device_id)
    logger.info("✅ Mobile handler initialized")


async def get_mobile_handler() -> MobileActionHandler:
    """Get or create the mobile handler"""
    global mobile_handler
    if mobile_handler is None:
        initialize_mobile_handler()
    return mobile_handler


# ============================================================================
# MESSAGE BROKER INTEGRATION
# ============================================================================

async def handle_mobile_action_task(message: Dict[str, Any]):
    """
    Callback for handling mobile action tasks from the message broker
    
    Called when coordinator publishes a task with device="mobile"
    """
    try:
        payload = message.get("payload", {})
        task_id = payload.get("task_id")
        session_id = payload.get("session_id")
        task_data = payload.get("task", {})
        
        logger.info(f"📬 Received mobile task from broker: {task_id}")
        
        # Get handler
        handler = await get_mobile_handler()
        
        # Execute task
        result = await handler.handle_action_task(task_data, task_id, session_id)
        
        # Publish result back to coordinator
        logger.info(f"📤 Publishing result for task {task_id}: {result.status}")
        await broker.publish(
            Channels.EXECUTION_TO_COORDINATOR,
            {
                "type": MessageType.TASK_RESULT,
                "agent": AgentType.EXECUTION,
                "payload": {
                    "task_id": task_id,
                    "status": result.status,
                    "content": result.content,
                    "error": result.error
                }
            }
        )
    
    except Exception as e:
        logger.error(f"❌ Error in handle_mobile_action_task: {e}", exc_info=True)


async def subscribe_to_mobile_tasks():
    """Subscribe to mobile action tasks from the coordinator"""
    
    # TODO: This needs to be integrated into the main execution agent
    # For now, this shows how tasks would be routed
    
    logger.info("🔔 Subscribing to mobile action tasks...")
    # In practice, this would be called in the execution agent's startup
    # broker.subscribe(Channels.COORDINATOR_TO_EXECUTION, handle_mobile_action_task)


# ============================================================================
# DIAGNOSTIC/TESTING
# ============================================================================

async def test_mobile_task():
    """Test the mobile handler with a sample task"""
    
    logger.info("🧪 Testing mobile handler...")
    
    initialize_mobile_handler(device_id="test_device")
    handler = await get_mobile_handler()
    
    # Sample task
    test_task = {
        "ai_prompt": "Click the Send button",
        "device": "mobile",
        "context": "local",
        "extra_params": {
            "device_id": "test_device",
            "max_steps": 5,
            "timeout_seconds": 30
        }
    }
    
    result = await handler.handle_action_task(test_task, "test_task_1", "test_session")
    
    logger.info(f"Test result: {result.dict()}")
    return result


if __name__ == "__main__":
    # Run test
    asyncio.run(test_mobile_task())
