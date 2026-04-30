import asyncio
from contextlib import asynccontextmanager

from agents.coordinator_agent.coordinator_agent import start_coordinator_agent
from agents.api_agent import start_api_agent
from agents.execution_agent.RAG.code_execution import initialize_execution_agent_for_server
from agents.language_agent import start_language_agent
from agents.reasoning_agent import start_reasoning_agent
from agents.utils.protocol import Channels
from agents.utils.broker import broker

from utils.response_handlers import handle_coordinator_output, handle_language_output, handle_ws_output
from core.dependencies import logger


def _preload_task_memory_embedding() -> None:
    """Warm TaskMemory + embedding model before first user task."""
    try:
        from agents.execution_agent.strategies.task_memory import TaskMemory
        logger.info("🔥 Preloading TaskMemory embedding model...")
        mem = TaskMemory()
        mem._get_embedder()
        mem._embed(["startup warmup"])
        logger.info("✅ TaskMemory embedding model preloaded")
    except Exception as exc:
        logger.warning(f"⚠️ TaskMemory preload skipped: {exc}")


@asynccontextmanager
async def lifespan(app):
    """Lifespan context manager for startup/shutdown"""
    logger.info("🚀 Starting AURA Backend...")

    await broker.start()
    logger.info("✅ Broker started")

    broker.subscribe(Channels.LANGUAGE_OUTPUT, handle_language_output)
    broker.subscribe(Channels.COORDINATOR_TO_LANGUAGE, handle_coordinator_output)
    broker.subscribe(Channels.WEBSOCKET_OUTPUT, handle_ws_output)
    logger.info("✅ Subscribed to output channels")

    await asyncio.to_thread(_preload_task_memory_embedding)

    try:
        logger.info("🚀 Starting Language Agent...")
        asyncio.create_task(start_language_agent(broker))
        await asyncio.sleep(0.1)

        logger.info("🚀 Starting Coordinator Agent...")
        asyncio.create_task(start_coordinator_agent(broker))
        await asyncio.sleep(0.1)

        logger.info("🚀 Starting Reasoning Agent...")
        asyncio.create_task(start_reasoning_agent())
        await asyncio.sleep(0.1)

        logger.info("🚀 Starting Execution Agent...")
        asyncio.create_task(initialize_execution_agent_for_server(broker))
        await asyncio.sleep(0.1)

        logger.info("🚀 Starting API Agent...")
        asyncio.create_task(start_api_agent(broker))
        await asyncio.sleep(0.1)

        logger.info("✅ All agents scheduled successfully")
    except Exception as exc:
        logger.error(f"❌ Error starting agents: {exc}", exc_info=True)

    yield

    logger.info("🛑 Shutting down AURA Backend...")
    await broker.stop()
    logger.info("✅ Broker stopped")
