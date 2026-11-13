import logging
from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from app.db.connections import get_connection

logger = logging.getLogger("uvicorn.error")

router = APIRouter(prefix="/api/database", tags=["Database"])


@router.get(
    "/health",
    summary="Check Supabase PostgreSQL and pgvector health status",
    status_code=status.HTTP_200_OK,
)
async def database_health():
    """
    Checks and reports active database connectivity, PostgreSQL server version,
    and pgvector extension details without exposing any sensitive credentials.
    """
    try:
        conn = await get_connection()
        async with conn:
            # Fetch current database name and PostgreSQL version
            res = await (
                await conn.execute("SELECT current_database(), version();")
            ).fetchone()
            db_name = res[0]
            raw_version = res[1]
            server_version = " ".join(raw_version.split()[:2])

            # Query pgvector extension registration and schema
            ext_res = await (
                await conn.execute(
                    """
                    SELECT e.extname, e.extversion, n.nspname AS schema_name
                    FROM pg_extension e
                    JOIN pg_namespace n ON n.oid = e.extnamespace
                    WHERE e.extname = 'vector';
                    """
                )
            ).fetchone()

            pgvector_info = {
                "installed": ext_res is not None,
                "version": ext_res[1] if ext_res else None,
                "schema": ext_res[2] if ext_res else None,
            }

            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={
                    "status": "connected",
                    "database": db_name,
                    "server_version": server_version,
                    "pgvector": pgvector_info,
                },
            )

    except ValueError as ve:
        logger.warning("[Database Health] Configuration warning: %s", ve)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "status": "unconfigured",
                "message": "Database connection is not configured.",
                "pgvector": {"installed": False},
            },
        )
    except Exception as exc:
        logger.error("[Database Health] Connection check failed: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "status": "disconnected",
                "message": "Database connection check failed.",
                "pgvector": {"installed": False},
            },
        )

