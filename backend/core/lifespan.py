import asyncio
import os
import subprocess
import sys
import time
from contextlib import asynccontextmanager

from agents.coordinator_agent.coordinator_agent import start_coordinator_agent
from agents.api_agent import start_api_agent
from agents.execution_agent.RAG.code_execution import initialize_execution_agent_for_server
from agents.language_agent import start_language_agent
from agents.reasoning_agent import start_reasoning_agent
from agents.utils.protocol import Channels
from agents.utils.broker import broker
from core.mongo import close_mongo_client, get_database, get_mongo_client
from services.task_dispatcher import TaskDispatcher

from utils.response_handlers import handle_coordinator_output, handle_language_output, handle_ws_output
from core.dependencies import logger


# Module-level handle so we can terminate Redis on shutdown
_redis_process: subprocess.Popen = None


def _start_redis() -> None:
    """
    Start the bundled Redis server if not already running.
    Looks for redis-server.exe in a 'redis' folder next to the executable
    (or next to this file during development).
    Silently skips if Redis is already running or the binary is not found.
    """
    global _redis_process

    # Resolve the base directory — works both as a .py file and inside a PyInstaller exe
    if getattr(sys, "frozen", False):
        # Running inside PyInstaller bundle
        base_dir = os.path.dirname(sys.executable)
    else:
        # Running as normal Python script — lifespan.py lives in backend/core/
        # so we go one level up to reach backend/
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    redis_exe = os.path.join(base_dir, "redis", "redis-server.exe")

    if not os.path.exists(redis_exe):
        logger.warning(
            f"⚠️ Redis binary not found at {redis_exe} — skipping bundled Redis start. "
            "Memory layer will fall back to MongoDB-only mode."
        )
        return

    # Check if Redis is already accepting connections so we don't spawn a duplicate
    try:
        import socket
        with socket.create_connection(("127.0.0.1", 6379), timeout=1):
            logger.info("✅ Redis already running on port 6379 — skipping bundled start")
            return
    except OSError:
        pass  # Not running — proceed to start it

    try:
        _redis_process = subprocess.Popen(
            [redis_exe, "--port", "6379", "--daemonize", "no"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        # Give Redis a moment to bind the port before the MemoryLayer tries to connect
        time.sleep(1.0)
        logger.info("✅ Bundled Redis server started (pid=%d)", _redis_process.pid)
    except Exception as exc:
        logger.warning(f"⚠️ Failed to start bundled Redis: {exc} — falling back to MongoDB-only mode")


def _stop_redis() -> None:
    """Terminate the Redis process we started, if any."""
    global _redis_process
    if _redis_process and _redis_process.poll() is None:
        logger.info("🛑 Stopping bundled Redis server...")
        _redis_process.terminate()
        try:
            _redis_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _redis_process.kill()
        _redis_process = None
        logger.info("✅ Bundled Redis server stopped")


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


dispatcher = TaskDispatcher(poll_interval_seconds=2)


@asynccontextmanager
async def lifespan(app):
    """Lifespan context manager for startup/shutdown"""
    logger.info("🚀 Starting AURA Backend...")

    # Start bundled Redis before MemoryLayer tries to connect to it
    await asyncio.to_thread(_start_redis)

    mongo_client = get_mongo_client()
    if mongo_client:
        try:
            mongo_client.admin.command("ping")
            logger.info("✅ Shared Mongo client initialized")
        except Exception as exc:
            logger.warning(f"⚠️ Mongo ping failed during startup: {exc}")

    # ── Initialize unified async MemoryLayer (Redis + async MongoDB + Mem0 queue) ──
    try:
        from memory_layer import memory_layer
        await memory_layer.initialize()
        logger.info("✅ MemoryLayer ready (Redis + async MongoDB + Mem0 queue)")
    except Exception as exc:
        logger.error(f"❌ MemoryLayer init failed — system will use fallback: {exc}")

    await broker.start()
    logger.info("✅ Broker started")

    broker.subscribe(Channels.LANGUAGE_OUTPUT, handle_language_output)
    broker.subscribe(Channels.COORDINATOR_TO_LANGUAGE, handle_coordinator_output)
    broker.subscribe(Channels.WEBSOCKET_OUTPUT, handle_ws_output)
    logger.info("✅ Subscribed to output channels")

    try:
        from routes.cross_platform_manager import init_cross_platform_manager
        from core.dependencies import ws_manager

        init_cross_platform_manager(get_database("aura_db"), ws_manager)
        logger.info("✅ Cross-platform task manager initialized")
    except Exception as exc:
        logger.warning(f"⚠️ Cross-platform manager initialization skipped: {exc}")

    try:
        await dispatcher.start()
    except Exception as exc:
        logger.warning(f"⚠️ Task dispatcher startup skipped: {exc}")

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
    await dispatcher.stop()
    await broker.stop()
    close_mongo_client()
    try:
        from memory_layer import memory_layer
        await memory_layer.close()
        logger.info("✅ MemoryLayer closed cleanly")
    except Exception as exc:
        logger.warning(f"⚠️ MemoryLayer shutdown error (non-fatal): {exc}")
    logger.info("✅ Broker stopped")
    _stop_redis()
