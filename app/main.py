import sys
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# Windows-specific event loop policy for high-performance async operations
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.core.config import settings
from app.core.logging import setup_logging, get_logger
from app.db.pool import init_db_pool, close_db_pool
from app.routes import database
from app.api.v1.router import api_v1_router

# Initialize application logging
setup_logging()
logger = get_logger("app.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifecycle management for startup and graceful shutdown.
    Initializes PostgreSQL connection pool on startup and closes it on shutdown.
    Database schema management is decoupled from server startup.
    """
    logger.info("Initializing %s (Environment: %s)...", settings.PROJECT_NAME, settings.ENVIRONMENT)
    logger.info("Database schema management is decoupled from server startup (no auto-migrations).")

    # Initialize connection pool
    try:
        await init_db_pool()
    except Exception as exc:
        logger.warning("[Startup] Connection pool initialization note: %s", exc)

    yield

    # Clean shutdown
    logger.info("Shutting down %s gracefully...", settings.PROJECT_NAME)
    await close_db_pool()


# Initialize FastAPI instance
app = FastAPI(
    title=settings.PROJECT_NAME,
    description="RouteWise Backend API with Gemini AI and Supabase pgvector",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# Configure CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


# Standardized error handling
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    Preserve FastAPI's detailed validation errors while preventing internal exposure.
    """
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error": "Validation Error",
            "detail": exc.errors(),
        },
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    """
    Safe catch-all error handler preventing SQL, credentials, or stack trace exposure.
    """
    logger.error(
        "Unhandled exception on %s %s: %s",
        request.method,
        request.url.path,
        exc,
        exc_info=True,
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "Internal Server Error",
            "message": "An unexpected error occurred. Please try again later.",
        },
    )


# Register modular routes
app.include_router(database.router)
app.include_router(api_v1_router)


@app.get(
    "/",
    tags=["General"],
    summary="Root landing endpoint",
    status_code=status.HTTP_200_OK,
)
async def root():
    """
    Landing endpoint providing project information.
    """
    return {
        "project": settings.PROJECT_NAME,
        "status": "online",
        "message": f"Welcome to {settings.PROJECT_NAME} API",
        "docs": "/docs",
    }


@app.get(
    "/health",
    tags=["System"],
    summary="Health check endpoint",
    status_code=status.HTTP_200_OK,
)
async def health_check():
    """
    Health check endpoint for container orchestrators and monitoring tools.
    """
    return {
        "status": "healthy",
        "environment": settings.ENVIRONMENT,
        "project": settings.PROJECT_NAME,
    }
