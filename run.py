"""
Module: run.py
Purpose: Application launcher for RouteWise AI (Sholinganallur_RouteWise).
         Ensures Windows uses a compatible asyncio SelectorEventLoop before Uvicorn
         initializes its event loop, resolving Psycopg 3's ProactorEventLoop limitation.
"""

import argparse
import asyncio
import os
import sys

# Windows-specific event loop policy:
# On Windows, Python 3.8+ defaults to WindowsProactorEventLoopPolicy.
# Psycopg 3's asynchronous connection and pool require a SelectorEventLoop.
# This policy MUST be configured before Uvicorn creates and starts its event loop.
if sys.platform == "win32":
    if hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import uvicorn
from app.core.config import settings


def main() -> None:
    """Launch the RouteWise AI FastAPI application server."""
    parser = argparse.ArgumentParser(description="Start the RouteWise AI FastAPI server.")
    parser.add_argument("--host", type=str, default=settings.HOST, help="Bind socket host")
    parser.add_argument("--port", type=int, default=settings.PORT, help="Bind socket port")
    parser.add_argument("--reload", action="store_true", default=False, help="Enable auto-reload")
    args = parser.parse_args()

    # Ensure project root is present in sys.path
    project_root = os.path.dirname(os.path.abspath(__file__))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    # On Windows, setting loop="none" tells Uvicorn to let asyncio use the active
    # event loop policy (WindowsSelectorEventLoopPolicy) rather than forcing ProactorEventLoop.
    # On non-Windows platforms, use Uvicorn's standard "auto" loop resolution.
    loop_param = "none" if sys.platform == "win32" else "auto"

    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        loop=loop_param,
        log_level=settings.LOG_LEVEL.lower(),
    )


if __name__ == "__main__":
    main()
