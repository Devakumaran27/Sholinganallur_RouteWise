"""
Module: app.api.v1.endpoints.rag
Purpose: REST API endpoint for semantic RAG vector retrieval over transit documents.
"""

import logging
from fastapi import APIRouter, Depends, HTTPException, status
import psycopg

from app.ai.rag.embeddings.embedding_service import (
    AuthenticationError,
    InvalidRequestError,
    RateLimitError,
    TransientServerError,
)
from app.db.pool import get_db
from app.schemas.rag import RAGSearchRequest, RAGSearchResponse
from app.services.rag_service import RAGService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/rag", tags=["RAG Semantic Retrieval"])

# Default service instance for dependency injection
_rag_service = RAGService()


def get_rag_service() -> RAGService:
    """Dependency provider for RAGService (enables mock injection in tests)."""
    return _rag_service


@router.post(
    "/search",
    summary="Semantic similarity search over transit documents",
    description=(
        "Retrieves the most semantically relevant transit document chunks for a natural-language "
        "transport question using 768-dimensional Gemini embeddings and PostgreSQL pgvector "
        "cosine similarity search over public.rag_chunks."
    ),
    response_model=RAGSearchResponse,
    status_code=status.HTTP_200_OK,
)
async def semantic_search(
    request: RAGSearchRequest,
    conn: psycopg.AsyncConnection = Depends(get_db),
    service: RAGService = Depends(get_rag_service),
) -> RAGSearchResponse:
    """
    Execute semantic similarity search for a transport query.

    Args:
        request: Validated RAGSearchRequest containing query, top_k, and optional chunk_type.
        conn: Active pooled database connection.
        service: Injected RAGService instance.

    Returns:
        RAGSearchResponse: Top-K ranked document chunks with similarity scores and metadata.
    """
    try:
        return await service.search(
            conn=conn,
            query=request.query,
            top_k=request.top_k,
            chunk_type=request.chunk_type,
        )
    except AuthenticationError as exc:
        logger.error("RAG search authentication error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Gemini embedding service authentication failed. Please verify API configuration.",
        )
    except RateLimitError as exc:
        logger.warning("RAG search rate limit exceeded: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Gemini embedding service quota or rate limit exceeded. Please try again shortly.",
        )
    except TransientServerError as exc:
        logger.error("RAG search transient server error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Gemini embedding service is temporarily unavailable. Please retry shortly.",
        )
    except InvalidRequestError as exc:
        logger.warning("RAG search invalid request error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid request to embedding service: {exc}",
        )
    except psycopg.Error as exc:
        logger.error("RAG search database error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database query error during semantic vector search.",
        )
    except Exception as exc:
        logger.error("Unexpected error during RAG semantic search: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred during semantic search.",
        )

