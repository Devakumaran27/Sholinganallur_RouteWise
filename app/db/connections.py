import logging
from typing import Optional
import psycopg
from pgvector.psycopg import register_vector_async
from app.core.config import settings

logger = logging.getLogger("uvicorn.error")


def mask_database_url(url: Optional[str]) -> str:
    """Mask credentials in database URL for safe logging and reporting."""
    if not url:
        return "None"
    import re
    return re.sub(r"://([^:]+):([^@]+)@", r"://\1:***REDACTED***@", url)


async def get_connection() -> psycopg.AsyncConnection:
    """
    Establishes an asynchronous PostgreSQL connection and registers
    the pgvector extension handlers for vector serialization.

    Returns:
        psycopg.AsyncConnection: The active database connection.
    Raises:
        ValueError: If DATABASE_URL is not configured or is a placeholder.
    """
    database_url = settings.DATABASE_URL

    if not database_url or "your_password" in database_url or "your_project_ref" in database_url:
        raise ValueError(
            "DATABASE_URL is not properly configured in .env. "
            "Please provide a valid Supabase PostgreSQL connection string."
        )

    try:
        conn = await psycopg.AsyncConnection.connect(database_url)
        await register_vector_async(conn)
        return conn
    except Exception as exc:
        logger.error(f"[Database Connection Error] Failed to connect to PostgreSQL: {exc}")
        raise


def get_sync_connection(database_url: Optional[str] = None) -> psycopg.Connection:
    """
    Establishes a synchronous PostgreSQL connection using psycopg 3.

    Args:
        database_url: Optional override connection string.

    Returns:
        psycopg.Connection: The active database connection.
    Raises:
        ValueError: If DATABASE_URL is not configured or is a placeholder.
    """
    url = database_url or settings.DATABASE_URL

    if not url or "your_password" in url or "your_project_ref" in url:
        raise ValueError(
            "DATABASE_URL is not properly configured. "
            "Please provide a valid Supabase PostgreSQL connection string."
        )

    try:
        return psycopg.connect(url)
    except Exception as exc:
        logger.error(f"[Database Connection Error] Failed to connect to PostgreSQL: {exc}")
        raise


