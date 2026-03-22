"""
Coordinator - Entry Point for Execution Agent
Loads tasks from JSON file and executes them via Execution Agent

Supports:
- Single task: task.json (single object)
- Workflow: workflow.json or search_save_workflow.json (array of tasks)
"""

import json
import os
from datetime import datetime
from agents.utils.protocol import (
    Channels, AgentMessage, MessageType, AgentType, ExecutionResult
)
# from backend.agents.execution_agent.core.exec_agent_main import ExecutionAgent

# from core import ExecutionAgent, ExecutionTask, ExecutionResult, ExecutionContext, ActionStatus
# from strategies import LocalStrategy, WebStrategy, SystemStrategy

## ADDED BY JANA BEGIN
import asyncio
import logging
from agents.utils.protocol import Channels
from agents.utils.broker import broker

logger = logging.getLogger(__name__)
## ADDED BY JANA END

from .core.exec_agent_main import ExecutionAgent
# from core.exec_agent_models import ExecutionResult, ExecutionTask, ExecutionResult, ExecutionContext, ActionStatus
from .strategies.local_strategy import LocalStrategy

def replace_placeholders(obj, context, **kwargs):
    """Recursively replace {{key}} placeholders with context values"""
    # Add default context values
    default_context = {
        "desktop_path": os.path.join(os.path.expanduser("~"), "Desktop"),
        "timestamp": datetime.now().strftime("%Y%m%d_%H%M%S"),
        "research_topic": kwargs.get("research_topic", "AI Automation"),
        "report_name": kwargs.get("report_name", "ResearchReport")
    }
    full_context = {**default_context, **context}
    
    if isinstance(obj, dict):
        return {k: replace_placeholders(v, full_context) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [replace_placeholders(item, full_context) for item in obj]
    elif isinstance(obj, str):
        result = obj
        for k, v in full_context.items():
            placeholder = f"{{{{{k}}}}}"
            if placeholder in result:
                result = result.replace(placeholder, str(v))
        return result
    return obj


def execute_workflow(agent, workflow_file: str, **kwargs):
    """Execute a workflow from JSON file"""
    print(f"\n{'='*70}")
    print(f"Loading workflow from: {workflow_file}")
    print(f"{'='*70}\n")
    
    with open(workflow_file, "r", encoding="utf-8") as f:
        workflow = json.load(f)
    
    if not isinstance(workflow, list):
        print("Error: Workflow file must contain an array of tasks")
        return
    
    print(f"Found {len(workflow)} tasks in workflow\n")
    
    context = {}
    results = []
    
    for i, task_dict in enumerate(workflow, 1):
        task_id = task_dict.get("task_id", f"task_{i}")
        
        # Check dependencies
        depends_on = task_dict.get("depends_on")
        if depends_on:
            # depends_on could be a list or a string
            if isinstance(depends_on, str):
                depends_list = [depends_on]
            else:
                depends_list = depends_on
                
            dependencies_met = True
            for dep_id in depends_list:
                dep_result = next((r for r in results if r.get("task_id") == dep_id), None)
                if not dep_result or dep_result.get("status") != "success":
                    print(f"Skipping task {i} ({task_id}): Dependency '{dep_id}' failed or not found")
                    results.append({
                        "task_id": task_id,
                        "status": "skipped",
                        "reason": f"Dependency '{dep_id}' not met"
                    })
                    dependencies_met = False
                    break
                    
            if not dependencies_met:
                continue
        
        print(f"{'='*70}")
        print(f"Task {i}/{len(workflow)}: {task_dict.get('action', 'unknown')}")
        print(f"   Task ID: {task_id}")
        print(f"   Context: {task_dict.get('context')}")
        print("-" * 70)
        
        # Replace placeholders with context values (including kwargs for workflow-specific params)
        task_to_execute = replace_placeholders(task_dict, context, **kwargs)
        
        try:
            result = agent.execute_from_dict(task_to_execute)
            results.append({
                "task_id": task_id,
                "status": result.get("status", "unknown"),
                "details": result.get("details", ""),
                "metadata": result.get("metadata", {}),
                "error": result.get("error")
            })
            
            # Update context with metadata from result
            if result.get("metadata"):
                for k, v in result.get("metadata", {}).items():
                    if v:  # Only store non-empty values
                        context[k] = v
            
            # Special handling for file paths and metadata
            if result.get("status") == "success":
                if task_dict.get("action") == "save_file":
                    file_path = task_to_execute.get("params", {}).get("file_path")
                    if file_path:
                        context["output_file_path"] = file_path
                        print(f"\nFile saved: {file_path}")
                elif task_dict.get("action") == "extract_web_content":
                    metadata = result.get("metadata", {})
                    if metadata.get("web_content"):
                        context["web_content"] = metadata["web_content"]
                        print(f"\nWeb content extracted: {len(context['web_content'])} characters")
                    if metadata.get("source_url"):
                        context["source_url"] = metadata["source_url"]
                    # Calculate content length for JSON
                    if context.get("web_content"):
                        context["content_length"] = len(context["web_content"])
            
            print(f"\nStatus: {result.get('status', 'unknown')}")
            if result.get("error"):
                print(f"Error: {result.get('error')}")
            print("-" * 70)
            
        except Exception as e:
            print(f"\nTask execution failed: {e}")
            results.append({
                "task_id": task_id,
                "status": "failed",
                "error": str(e)
            })
    
    # Print summary
    print(f"\n{'='*70}")
    print("WORKFLOW SUMMARY")
    print(f"{'='*70}")
    successful = sum(1 for r in results if r.get("status") == "success")
    failed = sum(1 for r in results if r.get("status") == "failed")
    skipped = sum(1 for r in results if r.get("status") == "skipped")
    
    print(f"\nTotal Tasks: {len(workflow)}")
    print(f"Successful: {successful}")
    print(f"Failed: {failed}")
    print(f"Skipped: {skipped}")
    
    if context.get("output_file_path"):
        print(f"\nOutput File: {context['output_file_path']}")
    
    print(f"{'='*70}\n")
    
    return results


def execute_single_task(agent, task_file: str):
    """Execute a single task from JSON file"""
    print(f"\n{'='*70}")
    print(f"Loading task from: {task_file}")
    print(f"{'='*70}\n")
    
    with open(task_file, "r", encoding="utf-8") as f:
        task_dict = json.load(f)
    
    if isinstance(task_dict, list):
        print("Warning: File contains array. Use execute_workflow() instead.")
        return execute_workflow(agent, task_file)
    
    print(f"Task: {task_dict.get('action', 'unknown')}")
    print(f"   Context: {task_dict.get('context')}")
    print("-" * 70)
    
    result = agent.execute_from_dict(task_dict)
    print(f"\nResult Status: {result.get('status', 'unknown')}")
    print(f"Details: {result.get('details', 'N/A')}")
    
    if result.get("error"):
        print(f"Error: {result.get('error')}")
    
    return result


# In Coordinator.py - start_execution_agent function

async def start_execution_agent(broker_instance):
    """Start Execution Agent with proper message handling"""
    
    agent = ExecutionAgent()
    
    # CHANGE 1: Track if already started to prevent double subscription
    if hasattr(broker_instance, '_execution_subscribed'):
        logger.warning("⚠️ Execution agent already subscribed, skipping")
        return
    broker_instance._execution_subscribed = True

    async def handle_execution_request(message: AgentMessage):
        """Handle execution request from Coordinator"""
        
        task_id = message.task_id or "unknown"
        task_payload = message.payload
        
        # CHANGE 2: Add request logging with unique identifier
        logger.info(f"🎯 [REQ-{message.message_id[:8]}] Executing: {task_id}")
        logger.debug(f"📋 [REQ-{message.message_id[:8]}] Payload: {task_payload}")
        
        # Execute the task
        result_dict = agent.execute_from_dict(task_payload)
        
        # CHANGE 3: Log only once per execution with same identifier
        logger.info(f"✅ [REQ-{message.message_id[:8]}] Complete: {task_id} -> {result_dict.get('status')}")
        
        # Create proper AgentMessage response
        response_msg = AgentMessage(
            message_type=MessageType.EXECUTION_RESPONSE,
            sender=AgentType.EXECUTION,
            receiver=AgentType.COORDINATOR,
            session_id=message.session_id,
            task_id=task_id,
            response_to=message.message_id,
            payload=ExecutionResult(
                status=result_dict.get("status", "failed"),
                details=result_dict.get("details", ""),
                # metadata=result_dict.get("metadata", {}),  # CHANGE 4: Comment out if causing issues
                error=result_dict.get("error")
            ).model_dump()
        )
        
        # Publish proper message
        await broker_instance.publish(Channels.EXECUTION_TO_COORDINATOR, response_msg)
    
    # Subscribe to execution requests
    broker_instance.subscribe(Channels.COORDINATOR_TO_EXECUTION, handle_execution_request)
    
    logger.info("✅ Execution Agent started")
    
    while True:
        await asyncio.sleep(1)


# if __name__ == "__main__":
#     import sys
    
#     agent = ExecutionAgent()
    
#     # Parse command-line arguments
#     workflow_kwargs = {}
#     file_path = None
    
#     i = 1
#     while i < len(sys.argv):
#         arg = sys.argv[i]
#         if arg in ["--topic", "-t"] and i + 1 < len(sys.argv):
#             workflow_kwargs["research_topic"] = sys.argv[i + 1]
#             i += 2
#         elif arg in ["--report-name", "-r"] and i + 1 < len(sys.argv):
#             workflow_kwargs["report_name"] = sys.argv[i + 1]
#             i += 2
#         elif not arg.startswith("--") and not arg.startswith("-"):
#             file_path = arg
#             i += 1
#         else:
#             i += 1
    
#     # Determine which file to use
#     if not file_path:
#         # Check for workflow files first, then task.json
#         workflow_files = ["research_report_workflow.json", "search_save_workflow.json", "workflow.json"]
#         task_file = "task.json"
        
#         if any(os.path.exists(f) for f in workflow_files):
#             file_path = next(f for f in workflow_files if os.path.exists(f))
#         elif os.path.exists(task_file):
#             file_path = task_file
#         else:
#             print("Error: No task or workflow file found!")
#             print("   Available options:")
#             print("   - task.json (single task)")
#             print("   - workflow.json (workflow)")
#             print("   - search_save_workflow.json (search & save workflow)")
#             print("   - research_report_workflow.json (research report workflow)")
#             print("\n   Usage:")
#             print("     python Coordinator.py [file.json] [--topic \"your topic\"] [--report-name \"ReportName\"]")
#             sys.exit(1)
    
#     if not os.path.exists(file_path):
#         print(f"Error: File not found: {file_path}")
#         sys.exit(1)
    
#     # Determine if it's a workflow or single task
#     with open(file_path, "r", encoding="utf-8") as f:
#         data = json.load(f)
    
#     if isinstance(data, list):
#         execute_workflow(agent, file_path, **workflow_kwargs)
#     else:
#         execute_single_task(agent, file_path)