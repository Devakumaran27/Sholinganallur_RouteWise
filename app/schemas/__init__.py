"""
Pydantic models and schemas for request/response validation.
"""

from app.schemas.embeddings import (
    APIRequestTracker,
    BatchCheckpointLedger,
    ChunkProcessingStatus,
    EmbeddingCheckpointRecord,
    EmbeddingMetadata,
    TokenAccounting,
    VectorRecord,
)
from app.schemas.transit import (
    BusStopBase,
    BusStopResponse,
    BusStopSearchResponse,
    DirectConnectionItem,
    DirectJourneyResponse,
    RouteServiceBrief,
    ScheduledDepartureResponse,
    StopDeparturesResponse,
    StopRoutesResponse,
)
from app.schemas.rag import (
    ChatGroundingSource,
    ChatRequest,
    ChatResponse,
    RAGChunkItem,
    RAGSearchRequest,
    RAGSearchResponse,
)

__all__ = [
    # Embeddings
    "APIRequestTracker",
    "BatchCheckpointLedger",
    "ChunkProcessingStatus",
    "EmbeddingCheckpointRecord",
    "EmbeddingMetadata",
    "TokenAccounting",
    "VectorRecord",
    # Transit
    "BusStopBase",
    "BusStopResponse",
    "BusStopSearchResponse",
    "RouteServiceBrief",
    "StopRoutesResponse",
    "ScheduledDepartureResponse",
    "StopDeparturesResponse",
    "DirectConnectionItem",
    "DirectJourneyResponse",
    # RAG & Chat
    "RAGSearchRequest",
    "RAGChunkItem",
    "RAGSearchResponse",
    "ChatRequest",
    "ChatGroundingSource",
    "ChatResponse",
]
