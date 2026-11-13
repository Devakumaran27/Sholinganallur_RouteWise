"""
Module: app.api.v1.endpoints.chat
Purpose: REST API endpoint for grounded transit question answering powered by
         semantic RAG retrieval, structured GTFS queries, and Google Gemini.
"""

import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
import psycopg

from app.ai.rag.embeddings.embedding_service import (
    AuthenticationError,
    InvalidRequestError,
    RateLimitError,
    TransientServerError,
)
from app.db.pool import get_db
from app.schemas.rag import ChatRequest, ChatResponse
from app.services.chat_service import ChatService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["Grounded Transit Assistant"])

# Default service instance for dependency injection
_chat_service: Optional[ChatService] = None


def get_chat_service() -> ChatService:
    """Dependency provider for ChatService (enables mock injection in tests)."""
    global _chat_service
    if _chat_service is None:
        _chat_service = ChatService()
    return _chat_service


@router.post(
    "/ask",
    summary="Ask a grounded transit question",
    description=(
        "Accepts a natural-language transport question, retrieves relevant knowledge chunks "
        "via pgvector semantic search and structured GTFS database records, and synthesizes a "
        "strictly grounded, passenger-friendly answer using Google Gemini with anti-hallucination guarantees."
    ),
    response_model=ChatResponse,
    status_code=status.HTTP_200_OK,
)
async def ask_transit_assistant(
    request: ChatRequest,
    service: ChatService = Depends(get_chat_service),
) -> ChatResponse:
    """
    Execute grounded transit question answering.

    Args:
        request: Validated ChatRequest containing question and optional top_k.
        service: Injected ChatService instance.

    Returns:
        ChatResponse: Grounded answer with citations, structured context, and limitations.
    """
    try:
        return await service.ask(
            question=request.question,
            top_k=request.top_k,
        )
    except AuthenticationError as exc:
        logger.error("Chat authentication error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Gemini AI service authentication failed. Please verify API configuration.",
        )
    except RateLimitError as exc:
        logger.warning("Chat rate limit exceeded: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Gemini AI service quota or rate limit exceeded. Please try again shortly.",
        )
    except TransientServerError as exc:
        logger.error("Chat transient server error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Gemini AI service is temporarily unavailable. Please retry shortly.",
        )
    except InvalidRequestError as exc:
        logger.warning("Chat invalid request error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid request to chat service: {exc}",
        )
    except psycopg.Error as exc:
        logger.error("Chat database error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database query error while retrieving grounded transit data.",
        )
    except Exception as exc:
        logger.error("Unexpected error during chat generation: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while generating the transit response.",
        )
