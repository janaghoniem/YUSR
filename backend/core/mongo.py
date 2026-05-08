"""Shared MongoDB client helpers.

The backend previously instantiated a new MongoClient inside many request
handlers. This module centralizes the client so the driver can reuse pooled
connections across routes and agents.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Optional

from pymongo import MongoClient

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_mongo_client() -> Optional[MongoClient]:
    """Return a process-wide Mongo client configured for pooling."""
    mongo_uri = os.getenv("MONGODB_URI")
    if not mongo_uri:
        logger.warning("⚠️ MONGODB_URI is not configured")
        return None

    try:
        return MongoClient(
            mongo_uri,
            serverSelectionTimeoutMS=5000,
            maxPoolSize=int(os.getenv("MONGODB_MAX_POOL_SIZE", "50")),
            minPoolSize=int(os.getenv("MONGODB_MIN_POOL_SIZE", "1")),
        )
    except Exception as exc:  # pragma: no cover
        logger.error(f"❌ Failed to initialize Mongo client: {exc}")
        return None


def get_database(name: str):
    """Return a shared database handle for the given database name."""
    client = get_mongo_client()
    if client is None:
        return None
    return client[name]


def close_mongo_client() -> None:
    """Close and clear the cached Mongo client."""
    client = get_mongo_client()
    if client is None:
        return

    try:
        client.close()
    finally:
        get_mongo_client.cache_clear()