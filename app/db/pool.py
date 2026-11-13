"""
Module: app.db.pool
Purpose: Asynchronous PostgreSQL connection pooling for Sholinganallur_RouteWise
         using Psycopg 3's AsyncConnectionPool with pgvector registration.
"""

from __future__ import annotations

import logging
from typing import AsyncGenerator, Optional

import psycopg
from psycopg_pool import AsyncConnectionPool

from app.core.config import settings
from app.db.connections import mask_database_url

logger = logging.getLogger("uvicorn.error")

# Singleton connection pool instance for application lifecycle
_db_pool: Optional[AsyncConnectionPool] = None


async def _configure_pool_connection(conn: psycopg.AsyncConnection) -> None:
    """
    Hook executed when a new connection is created in the pool.
    Enables autocommit mode to prevent idle-in-transaction timeouts with Supabase pooler,
    and registers pgvector types asynchronously.
    """
    await conn.set_autocommit(True)
    try:
        from pgvector.psycopg import register_vector_async
        await register_vector_async(conn)
        logger.debug("[DB Pool] pgvector handlers registered for connection.")
    except Exception as exc:
        logger.debug("[DB Pool] pgvector registration note: %s", exc)


def get_db_pool() -> Optional[AsyncConnectionPool]:
    """Return the active singleton connection pool, or None if not initialized."""
    return _db_pool


def set_db_pool(pool: Optional[AsyncConnectionPool]) -> None:
    """Set or override the active connection pool (primarily for testing)."""
    global _db_pool
    _db_pool = pool


async def init_db_pool(database_url: Optional[str] = None) -> AsyncConnectionPool:
    """
    Initialize and open the asynchronous connection pool during FastAPI lifespan startup.
    Credentials are never logged or exposed on failure.

    Args:
        database_url: Optional override database connection string.

    Returns:
        AsyncConnectionPool: The opened connection pool.

    Raises:
        RuntimeError: If database URL is missing, invalid, or pool open fails.
    """
    global _db_pool

    url = database_url or settings.DATABASE_URL
    if not url or "your_password" in url or "your_project_ref" in url:
        masked = mask_database_url(url)
        logger.warning(
            "[DB Pool] DATABASE_URL is not configured or contains placeholder: %s",
            masked,
        )
        raise RuntimeError("DATABASE_URL is not configured with valid credentials.")

    masked_url = mask_database_url(url)
    logger.info(
        "[DB Pool] Initializing AsyncConnectionPool (min=%d, max=%d) -> %s",
        settings.DB_POOL_MIN_SIZE,
        settings.DB_POOL_MAX_SIZE,
        masked_url,
    )

    try:
        pool = AsyncConnectionPool(
            conninfo=url,
            min_size=settings.DB_POOL_MIN_SIZE,
            max_size=settings.DB_POOL_MAX_SIZE,
            timeout=settings.DB_POOL_TIMEOUT,
            max_idle=settings.DB_POOL_MAX_IDLE,
            open=False,
            configure=_configure_pool_connection,
            name="routewise_async_pool",
        )
        await pool.open()
        _db_pool = pool
        logger.info("[DB Pool] PostgreSQL connection pool opened successfully.")
        return pool
    except Exception as exc:
        logger.error(
            "[DB Pool] Failed to open PostgreSQL connection pool (Target: %s): %s",
            masked_url,
            exc,
        )
        _db_pool = None
        raise RuntimeError("Failed to initialize PostgreSQL database connection pool.") from None


async def close_db_pool() -> None:
    """
    Cleanly close all connections in the pool during application shutdown.
    """
    global _db_pool
    if _db_pool is not None:
        logger.info("[DB Pool] Closing asynchronous connection pool...")
        try:
            await _db_pool.close()
            logger.info("[DB Pool] Connection pool closed cleanly.")
        except Exception as exc:
            logger.warning("[DB Pool] Error closing connection pool: %s", exc)
        finally:
            _db_pool = None


async def get_db() -> AsyncGenerator[psycopg.AsyncConnection, None]:
    """
    FastAPI dependency that yields an active connection from the pool.
    Releases the connection back to the pool when the request context finishes.

    Yields:
        psycopg.AsyncConnection: An active pooled database connection.

    Raises:
        RuntimeError: If the pool has not been initialized.
    """
    pool = get_db_pool()
    if pool is None:
        raise RuntimeError("Database connection pool is not initialized or unavailable.")

    async with pool.connection() as conn:
        yield conn

