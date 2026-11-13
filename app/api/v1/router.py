"""
Module: app.api.v1.router
Purpose: Master API v1 router for Sholinganallur_RouteWise.
         Aggregates modular routers for transit entities, RAG retrieval, and chat.
"""

from fastapi import APIRouter, status

from app.api.v1.endpoints import chat, departures, journey, rag, stops

api_v1_router = APIRouter(prefix="/api/v1")


@api_v1_router.get(
    "/",
    summary="API Version 1 Status & Module Directory",
    status_code=status.HTTP_200_OK,
    tags=["API v1"],
)
async def api_v1_info():
    """
    Returns API v1 status and module directory.
    Structured transit APIs (Step 5.2), Semantic RAG retrieval (Step 5.3),
    and Grounded Gemini Chat Q&A (Step 5.4) are active.
    """
    return {
        "api_version": "v1",
        "status": "active",
        "modules": {
            "stops": "Active (Bus stop search & coordinates)",
            "routes": "Active (Routes serving stops)",
            "departures": "Active (Scheduled departure timetables)",
            "journey": "Active (Direct Sholinganallur -> T. Nagar lookup)",
            "rag": "Active (pgvector semantic chunk search)",
            "chat": "Active (Gemini-powered grounded Q&A)",
        },
    }


# Register Step 5.2 transit routers
api_v1_router.include_router(stops.router)
api_v1_router.include_router(departures.router)
api_v1_router.include_router(journey.router)

# Register Step 5.3 RAG semantic retrieval router
api_v1_router.include_router(rag.router)

# Register Step 5.4 Grounded Chat Q&A router
api_v1_router.include_router(chat.router)
