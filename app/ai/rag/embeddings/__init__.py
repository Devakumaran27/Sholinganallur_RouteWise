"""
Package: app.ai.rag.embeddings
Purpose: Modules and data models powering the RouteWise batch embedding pipeline.
"""

from app.ai.rag.embeddings.batch_processor import BatchProcessor
from app.ai.rag.embeddings.checkpoint_manager import CheckpointManager
from app.ai.rag.embeddings.embedding_service import (
    AuthenticationError,
    EmbeddingService,
    EmbeddingServiceError,
    InvalidRequestError,
    RateLimitError,
    TransientServerError,
)
from app.ai.rag.embeddings.models import (
    APIRequestTracker,
    BatchCheckpointLedger,
    ChunkProcessingStatus,
    EmbeddingCheckpointRecord,
    EmbeddingMetadata,
    TokenAccounting,
    VectorRecord,
    sanitize_sensitive_data,
)
from app.ai.rag.embeddings.quota_manager import (
    QuotaExceededError,
    QuotaManager,
    TPMLimitExceededError,
)

__all__ = [
    "APIRequestTracker",
    "AuthenticationError",
    "BatchCheckpointLedger",
    "BatchProcessor",
    "CheckpointManager",
    "ChunkProcessingStatus",
    "EmbeddingCheckpointRecord",
    "EmbeddingMetadata",
    "EmbeddingService",
    "EmbeddingServiceError",
    "InvalidRequestError",
    "QuotaExceededError",
    "QuotaManager",
    "RateLimitError",
    "TPMLimitExceededError",
    "TokenAccounting",
    "TransientServerError",
    "VectorRecord",
    "sanitize_sensitive_data",
]
