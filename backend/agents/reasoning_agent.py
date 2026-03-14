import os
import asyncio
import logging
import json
from typing import Dict, Any
from dotenv import load_dotenv

# Groq & LangChain Imports
from langchain_groq import ChatGroq

# Project Utilities
from agents.utils.protocol import Channels, AgentMessage, MessageType, AgentType
from agents.utils.broker import broker

load_dotenv()
logger = logging.getLogger(__name__)

# --- Configuration ---
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
REASONING_MODEL = "llama-3.3-70b-versatile"

class ReasoningAgent:
    def __init__(self):
        self.llm = ChatGroq(
            model=REASONING_MODEL,
            temperature=0.2,
            groq_api_key=GROQ_API_KEY
        )
        
        self.system_prompt = """You are the REASONING AGENT – the cognitive brain of the AURA multi-agent system.

PURPOSE:
You are responsible for all high-level intellectual processing inside AURA.
You transform raw or intermediate outputs from other agents into meaningful,
actionable, and human-understandable results.

CORE RESPONSIBILITIES:
1. Text Summarization: Condense long or complex content into structured key points.
2. Information Extraction: Identify and structure specific data (e.g., names, dates, entities, links, values).
3. Text Generation: Generate coherent, context-aware text based on instructions.
4. Code Generation: Produce clean, correct code snippets based on requirements.
5. Code Debugging: Analyze code, detect errors, and suggest precise fixes.
6. Explanation & Teaching: Explain documents, concepts, or outputs in clear, understandable language.
7. Decision Support: Recommend next steps based on previous results or failures.
8. Comparison & Evaluation: Compare outputs, approaches, or datasets logically and objectively.
9. Validation & Consistency Checking: Detect contradictions, missing elements, or logical flaws.

STRICT OPERATIONAL RULES:
- You NEVER interact with UI elements, browsers, mouse, keyboard, or operating system.
- You NEVER execute tools or external actions.
- You ONLY reason over the content and parameters provided by the Coordinator or Action Layer.
- If required data is missing or ambiguous, explicitly state what is needed.
- You MUST avoid hallucinating unknown information.
- You MUST return your output as a valid JSON object with this structure:
  - "result": your main output
  - "metadata": an object with "confidence" (0.0-1.0), "notes" (optional), "assumptions" (optional)

You are an internal reasoning component, not a user-facing assistant.
Your goal is correctness, clarity, and usefulness to the system."""

    async def process_reasoning_task(self, task_payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Executes the logic requested by the Coordinator.
        """
        ai_prompt = task_payload.get("ai_prompt", "Process the following content.")
        content = task_payload.get("content", "")
        extra_params = task_payload.get("extra_params", {})
        
        # ✅ FIX 1: Extract input_content from multiple sources
        if "input_content" in extra_params:
            content = extra_params["input_content"]
            logger.info(f"📥 Using input_content from extra_params ({len(content)} chars)")
        
        # ✅ FIX 2: Clean success/failure markers from content
        if content:
            content = content.replace("EXECUTION_SUCCESS", "").replace("FAILED:", "").strip()
            logger.info(f"🧹 Cleaned content: {len(content)} chars")
        
        # ✅ FIX 3: Validate content exists
        if not content or content == "":
            # Check if this looks like a data-processing task that truly needs input
            data_keywords = ["summarize", "analyze", "analyse", "review", "translate",
                             "extract", "check", "fix", "debug", "explain this", "describe this"]
            needs_data = any(kw in ai_prompt.lower() for kw in data_keywords)
            if needs_data:
                logger.warning(f"⚠️ Data-processing task with no content — attempting with prompt only")
            else:
                logger.info(f"📝 Standalone generation task — no input data required")
            # Either way, proceed — DATA TO PROCESS section will be omitted from the prompt
        
        try:
            logger.info(f"🧠 Reasoning Agent processing task: {ai_prompt[:50]}...")
            if content:
                logger.info(f"📊 Content preview: {content[:200]}...")
            
            # Build prompt: include DATA section only when content is available
            if content:
                full_prompt = f"""{self.system_prompt}

    TASK: {ai_prompt}

    DATA TO PROCESS:
    {content}

    EXTRA PARAMETERS: {json.dumps(extra_params)}

    Please respond with valid JSON only."""
            else:
                full_prompt = f"""{self.system_prompt}

    TASK: {ai_prompt}

    EXTRA PARAMETERS: {json.dumps(extra_params)}

    Please respond with valid JSON only."""

            response = await self.llm.ainvoke(full_prompt)
            response_text = response.content if hasattr(response, 'content') else str(response)
            logger.info(f"🤖 REASONING RESPONSE ({len(response_text)} chars): {response_text[:200]}...")
            
            # Parse JSON response
            try:
                parsed_response = json.loads(response_text)
                result_content = parsed_response.get("result", str(parsed_response))
                
                logger.info(f"✅ Reasoning complete: {result_content[:200]}...")
                
                return {
                    "task_id": task_payload.get("task_id"),
                    "status": "success",
                    "content": result_content,
                    "metadata": parsed_response.get("metadata", {})
                }
            except json.JSONDecodeError:
                logger.warning("⚠️ Response was not valid JSON, using raw text")
                return {
                    "task_id": task_payload.get("task_id"),
                    "status": "success",
                    "content": response_text,
                    "metadata": {"notes": "Response was not in JSON format"}
                }

        except Exception as e:
            logger.error(f"❌ Reasoning Agent Error: {e}", exc_info=True)
            return {
                "task_id": task_payload.get("task_id"),
                "status": "failed",
                "error": str(e),
                "content": ""
            }

async def start_reasoning_agent():
    agent = ReasoningAgent()
    logger.info(f"✅ Reasoning Agent (Groq) started using {REASONING_MODEL}")

    async def handle_reasoning_request(message: AgentMessage):
        """
        Callback for when the Coordinator sends a task to the Reasoning channel.
        """
        task_id = message.task_id
        payload = message.payload

        # Process the logic
        result = await agent.process_reasoning_task(payload)
        
        # FIX: Add task_id to result payload
        result["task_id"] = task_id

        # Send response back to Coordinator
        response_msg = AgentMessage(
            message_type=MessageType.EXECUTION_RESPONSE,
            sender=AgentType.REASONING,
            receiver=AgentType.COORDINATOR,
            session_id=message.session_id,
            task_id=task_id,
            response_to=message.message_id,
            payload=result  # Now includes task_id
        )

        await broker.publish(Channels.REASONING_TO_COORDINATOR, response_msg)
        logger.info(f"📤 Sent reasoning result for task {task_id}")
        logger.info(f"Content: {result}")

    # Subscribe to the reasoning channel
    broker.subscribe(Channels.COORDINATOR_TO_REASONING, handle_reasoning_request)

    while True:
        await asyncio.sleep(1)

# if __name__ == "__main__":
#     logging.basicConfig(level=logging.INFO)
#     asyncio.run(start_reasoning_agent())