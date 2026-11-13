"""
Module: app.ai.rag.embeddings.models
Purpose: Pydantic v2 data models for the RouteWise batch embedding pipeline.
         Covers chunk processing statuses, API request tracking, embedding metadata,
         vector persistence schemas, and atomic checkpoint ledgers.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator, model_validator


# Common Regex Patterns
HEX_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
API_KEY_MASK_PATTERN = re.compile(r"AIza[0-9A-Za-z-_]{20,40}", re.IGNORECASE)


def sanitize_sensitive_data(val: Any) -> Any:
    """
    Recursively scans and scrubs API keys, connection strings, and credentials
    from error dictionaries or text metadata.
    """
    if isinstance(val, str):
        masked = API_KEY_MASK_PATTERN.sub("***REDACTED_API_KEY***", val)
        if "postgres://" in masked or "postgresql://" in masked:
            masked = re.sub(r"://([^:]+):([^@]+)@", "://***:***@", masked)
        return masked
    elif isinstance(val, dict):
        return {k: sanitize_sensitive_data(v) for k, v in val.items()}
    elif isinstance(val, list):
        return [sanitize_sensitive_data(item) for item in val]
    return val


class ChunkProcessingStatus(str, Enum):
    """
    Mutually exclusive operational processing states for chunks
    within the batch embedding lifecycle.
    """
    PENDING = "pending"
    COMPLETED = "completed"
    RETRYABLE_FAILED = "retryable_failed"
    PERMANENTLY_FAILED = "permanently_failed"


class TokenAccounting(BaseModel):
    """
    Token metrics tracking both offline heuristic estimates and official
    authoritative tokens reported by the Gemini API endpoint.
    """
    estimated_tokens: int = Field(ge=0, description="Tokens estimated by offline regex heuristic")
    api_reported_tokens: Optional[int] = Field(
        default=None, ge=0, description="Authoritative token count returned by Google GenAI API"
    )
    source_character_length: int = Field(ge=0, description="Raw character count of source text")

    model_config = {
        "extra": "forbid",
        "frozen": True,
    }


class EmbeddingMetadata(BaseModel):
    """
    Provenance and mathematical metrics for a generated 768-dimensional dense vector.
    """
    model: str = Field(default="gemini-embedding-001", description="Gemini embedding model identifier")
    output_dimensionality: int = Field(default=768, description="Target vector dimension (768 for RouteWise)")
    task_type: str = Field(default="RETRIEVAL_DOCUMENT", description="Gemini task type used during embedding")
    vector_l2_norm: float = Field(gt=0.0, description="Euclidean L2 norm of the vector")
    is_finite: bool = Field(default=True, description="Asserts zero NaN and zero infinite values")
    generation_latency_seconds: Optional[float] = Field(
        default=None, ge=0.0, description="API round-trip duration in seconds"
    )
    generated_at_utc: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp of generation",
    )

    model_config = {
        "extra": "forbid",
    }

    @field_validator("model")
    @classmethod
    def validate_model(cls, v: str) -> str:
        if v != "gemini-embedding-001":
            raise ValueError(f"Unsupported model '{v}'. Expected 'gemini-embedding-001'.")
        return v

    @field_validator("output_dimensionality")
    @classmethod
    def validate_dimensions(cls, v: int) -> int:
        if v != 768:
            raise ValueError(f"output_dimensionality must be 768, got {v}.")
        return v

    @field_validator("task_type")
    @classmethod
    def validate_task_type(cls, v: str) -> str:
        if v != "RETRIEVAL_DOCUMENT":
            raise ValueError(f"Expected task_type 'RETRIEVAL_DOCUMENT', got '{v}'.")
        return v


class VectorRecord(BaseModel):
    """
    Complete schema for persisted local vector records (data/embeddings/vectors/{chunk_id}.json).
    Guarantees full provenance, cryptographic hash authenticity, and 768-dim numeric integrity.
    """
    chunk_id: str = Field(description="Unique semantic chunk identifier")
    document_id: str = Field(description="Parent document identifier")
    document_type: str = Field(description="Type category: stop_profile, route_profile, corridor_guide, etc.")
    title: str = Field(description="Human-readable title of the chunk or parent document")
    chunk_hash: str = Field(description="SHA-256 hash of original chunk payload")
    dataset_version: str = Field(default="v1.0.0-sholinganallur", description="Source dataset version")
    pipeline_version: str = Field(default="v1.0.0-embedding", description="Active embedding pipeline version")
    embedding_metadata: EmbeddingMetadata
    token_accounting: TokenAccounting
    embedding: List[float] = Field(description="768-element floating-point dense vector array")

    model_config = {
        "extra": "forbid",
    }

    @field_validator("chunk_hash")
    @classmethod
    def validate_hash(cls, v: str) -> str:
        if not HEX_SHA256_PATTERN.match(v.lower()):
            raise ValueError(f"chunk_hash must be a valid 64-character lowercase hex SHA-256 string, got '{v}'.")
        return v.lower()

    @field_validator("embedding")
    @classmethod
    def validate_embedding_vector(cls, v: List[float]) -> List[float]:
        if len(v) != 768:
            raise ValueError(f"Vector embedding must contain exactly 768 dimensions, got {len(v)}.")
        for idx, val in enumerate(v):
            if not isinstance(val, (int, float)):
                raise ValueError(f"Vector element at index {idx} is not a numeric float/int: {val}")
            if not math.isfinite(val):
                raise ValueError(f"Vector element at index {idx} is non-finite (NaN or Inf): {val}")
        return v

    def __repr__(self) -> str:
        """
        Concise representation that summarizes the 768-dimensional float vector
        to prevent terminal and log overflow.
        """
        first_3 = [round(x, 4) for x in self.embedding[:3]]
        last_3 = [round(x, 4) for x in self.embedding[-3:]]
        preview = f"[{first_3[0]}, {first_3[1]}, {first_3[2]}, ..., {last_3[0]}, {last_3[1]}, {last_3[2]}] (768 dims)"
        return (
            f"VectorRecord(chunk_id='{self.chunk_id}', doc_id='{self.document_id}', "
            f"model='{self.embedding_metadata.model}', norm={self.embedding_metadata.vector_l2_norm:.4f}, "
            f"embedding={preview})"
        )


class EmbeddingCheckpointRecord(BaseModel):
    """
    Atomic transaction record for a single chunk within the stateful checkpoint ledger.
    """
    chunk_id: str
    parent_document_id: str
    chunk_hash: str
    dataset_version: str = "v1.0.0-sholinganallur"
    pipeline_version: str = "v1.0.0-embedding"
    model_name: str = "gemini-embedding-001"
    embedding_dimensions: int = 768
    task_type: str = "RETRIEVAL_DOCUMENT"
    processing_status: ChunkProcessingStatus = ChunkProcessingStatus.PENDING
    attempt_count: int = Field(default=0, ge=0)
    vector_file_path: Optional[str] = None
    vector_l2_norm: Optional[float] = None
    estimated_tokens: int = Field(ge=0)
    api_reported_tokens: Optional[int] = None
    error_details: Optional[Dict[str, Any]] = None
    created_at_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at_utc: Optional[datetime] = None

    model_config = {
        "extra": "forbid",
    }

    @field_validator("chunk_hash")
    @classmethod
    def validate_hash(cls, v: str) -> str:
        if not HEX_SHA256_PATTERN.match(v.lower()):
            raise ValueError(f"chunk_hash must be a valid 64-character lowercase hex SHA-256 string, got '{v}'.")
        return v.lower()

    @field_validator("error_details", mode="before")
    @classmethod
    def sanitize_errors(cls, v: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if v is None:
            return None
        return sanitize_sensitive_data(v)

    @model_validator(mode="after")
    def validate_status_consistency(self) -> EmbeddingCheckpointRecord:
        if self.processing_status == ChunkProcessingStatus.COMPLETED:
            if not self.vector_file_path:
                raise ValueError("Completed chunk checkpoint must specify 'vector_file_path'.")
            if not self.completed_at_utc:
                raise ValueError("Completed chunk checkpoint must specify 'completed_at_utc'.")
            if self.vector_l2_norm is None or self.vector_l2_norm <= 0:
                raise ValueError("Completed chunk checkpoint must specify a positive 'vector_l2_norm'.")
        elif self.processing_status == ChunkProcessingStatus.PERMANENTLY_FAILED:
            if not self.error_details:
                raise ValueError("Permanently failed chunk checkpoint must include 'error_details'.")
        return self


class APIRequestTracker(BaseModel):
    """
    Strict monotonic request counter and quota tracking ledger.
    Enforces the principle that 'retries are never free' against the Free Tier daily budget.
    """
    global_api_requests: int = Field(default=0, ge=0, description="Total API requests issued across all types")
    initial_requests: int = Field(default=0, ge=0, description="Initial batch requests dispatched")
    retry_requests: int = Field(default=0, ge=0, description="Retry requests dispatched (e.g. 429/503 backoff)")
    bisection_requests: int = Field(default=0, ge=0, description="Sub-batch or 1-chunk fallback requests dispatched")
    daily_budget_limit: int = Field(
        default=200, ge=1, le=1000, description="Configurable safety ceiling below 1,000 RPD quota"
    )
    status_counts: Dict[str, int] = Field(
        default_factory=dict, description="Running count of chunk statuses"
    )
    last_request_timestamp_utc: Optional[datetime] = None
    last_request_date_utc: Optional[str] = Field(
        default=None, description="UTC calendar date (YYYY-MM-DD) of the last recorded request"
    )

    model_config = {
        "extra": "forbid",
    }

    @model_validator(mode="after")
    def validate_request_sum(self) -> APIRequestTracker:
        expected_total = self.initial_requests + self.retry_requests + self.bisection_requests
        if self.global_api_requests != expected_total:
            raise ValueError(
                f"global_api_requests ({self.global_api_requests}) does not match the sum of "
                f"initial ({self.initial_requests}) + retry ({self.retry_requests}) + "
                f"bisection ({self.bisection_requests}) = {expected_total}."
            )
        if self.global_api_requests > self.daily_budget_limit:
            raise ValueError(
                f"global_api_requests ({self.global_api_requests}) exceeds daily_budget_limit "
                f"({self.daily_budget_limit})."
            )
        return self

    def can_make_request(self) -> bool:
        """Checks if another remote request can be dispatched without exceeding the safety budget."""
        return self.global_api_requests < self.daily_budget_limit

    def record_request(self, call_type: str = "initial") -> None:
        """
        Increments the global request counter and the categorized sub-counter.
        Raises ValueError if the daily safety budget would be exceeded.
        """
        if not self.can_make_request():
            raise ValueError(
                f"Daily API request safety budget reached ({self.global_api_requests}/{self.daily_budget_limit}). "
                "Halting pipeline to protect Google AI Studio Free Tier RPD quota."
            )
        self.global_api_requests += 1
        if call_type == "initial":
            self.initial_requests += 1
        elif call_type == "retry":
            self.retry_requests += 1
        elif call_type == "bisection":
            self.bisection_requests += 1
        else:
            raise ValueError(f"Unknown call_type '{call_type}'. Must be 'initial', 'retry', or 'bisection'.")
        now = datetime.now(timezone.utc)
        self.last_request_timestamp_utc = now
        self.last_request_date_utc = now.strftime("%Y-%m-%d")

    def reset_daily_counters(self, new_date_str: str) -> None:
        """
        Resets daily request counters upon UTC calendar day rollover.
        Maintains consistency across all request categories.
        """
        self.global_api_requests = 0
        self.initial_requests = 0
        self.retry_requests = 0
        self.bisection_requests = 0
        self.last_request_date_utc = new_date_str


class BatchCheckpointLedger(BaseModel):
    """
    Master state ledger representing data/embeddings/checkpoints/batch_checkpoint.json.
    Provides complete provenance, atomic update validation, and resumption accounting.
    """
    checkpoint_version: str = Field(default="v1.1.0", description="Schema version of checkpoint ledger")
    pipeline_name: str = Field(default="RouteWise_Batch_Embedding_Pipeline")
    pipeline_version: str = Field(default="v1.0.0-embedding")
    dataset_version: str = Field(default="v1.0.0-sholinganallur")
    model_name: str = Field(default="gemini-embedding-001")
    embedding_dimensions: int = Field(default=768)
    task_type: str = Field(default="RETRIEVAL_DOCUMENT")
    total_catalog_chunks: int = Field(default=145, ge=1)
    accounting: APIRequestTracker = Field(default_factory=APIRequestTracker)
    chunks: Dict[str, EmbeddingCheckpointRecord] = Field(default_factory=dict)
    last_run_started_utc: Optional[datetime] = None
    last_checkpoint_commit_utc: Optional[datetime] = None

    model_config = {
        "extra": "forbid",
    }

    def sync_status_counts(self) -> Dict[str, int]:
        """
        Recalculates status counts from chunks map and updates the accounting block.
        """
        counts = {
            ChunkProcessingStatus.PENDING.value: 0,
            ChunkProcessingStatus.COMPLETED.value: 0,
            ChunkProcessingStatus.RETRYABLE_FAILED.value: 0,
            ChunkProcessingStatus.PERMANENTLY_FAILED.value: 0,
        }
        for record in self.chunks.values():
            counts[record.processing_status.value] = counts.get(record.processing_status.value, 0) + 1

        self.accounting.status_counts = counts
        return counts

    def get_chunks_by_status(self, status: ChunkProcessingStatus) -> List[EmbeddingCheckpointRecord]:
        """Returns all chunk records matching the specified status."""
        return [r for r in self.chunks.values() if r.processing_status == status]

