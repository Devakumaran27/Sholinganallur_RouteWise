"""
Module: app.ai.rag.embeddings.embedding_service
Purpose: Reusable Gemini embedding service for generating and validating 768-dimensional
         dense vectors using Google's gemini-embedding-001 model via the modern google-genai SDK.
"""

from __future__ import annotations

import logging
import math
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from google import genai
from google.genai import errors, types

from app.core.config import settings
from app.ai.rag.embeddings.models import (
    EmbeddingMetadata,
    TokenAccounting,
    VectorRecord,
    sanitize_sensitive_data,
)

logger = logging.getLogger(__name__)


# ==============================================================================
# Typed Exceptions for Clear Error Classification
# ==============================================================================

class EmbeddingServiceError(Exception):
    """Base exception for all embedding service failures."""

    def __init__(
        self,
        message: str,
        original_error: Optional[Exception] = None,
        status_code: Optional[int] = None,
    ):
        sanitized_msg = sanitize_sensitive_data(message)
        super().__init__(sanitized_msg)
        self.message = sanitized_msg
        self.original_error = original_error
        self.status_code = status_code


class AuthenticationError(EmbeddingServiceError):
    """Raised on HTTP 401 or 403 (Invalid, missing, or revoked API key). Non-retryable."""
    pass


class RateLimitError(EmbeddingServiceError):
    """Raised on HTTP 429 (ResourceExhausted / Quota / Rate limit exceeded). Retryable."""
    pass


class TransientServerError(EmbeddingServiceError):
    """Raised on HTTP 500, 502, 503, 504 or network socket timeout. Retryable."""
    pass


class InvalidRequestError(EmbeddingServiceError):
    """Raised on HTTP 400, malformed payloads, or invalid vector responses. Non-retryable."""
    pass


# ==============================================================================
# Embedding Service Class
# ==============================================================================

class EmbeddingService:
    """
    Encapsulates interaction with Google's gemini-embedding-001 endpoint.
    Generates and strictly validates 768-dimensional dense vectors for individual chunks.
    Independent of checkpoint persistence and retry loops.
    """

    def __init__(
        self,
        client: Optional[genai.Client] = None,
        api_key: Optional[str] = None,
        model_name: Optional[str] = None,
        embedding_dimensions: Optional[int] = None,
        task_type: Optional[str] = None,
        dataset_version: str = "v1.0.0-sholinganallur",
        pipeline_version: str = "v1.0.0-embedding",
    ):
        self.model_name = model_name or settings.EMBEDDING_MODEL
        self.embedding_dimensions = embedding_dimensions or settings.EMBEDDING_DIMENSIONS
        self.task_type = task_type or settings.EMBEDDING_TASK_TYPE
        self.dataset_version = dataset_version
        self.pipeline_version = pipeline_version

        # Initialize or inject google-genai client
        if client is not None:
            self.client = client
        else:
            resolved_key = api_key if api_key is not None else settings.GEMINI_API_KEY
            if not resolved_key or not resolved_key.strip():
                raise AuthenticationError(
                    "GEMINI_API_KEY is not configured in settings or environment. "
                    "Cannot initialize Gemini embedding client."
                )
            try:
                self.client = genai.Client(api_key=resolved_key)
            except Exception as exc:
                raise AuthenticationError(f"Failed to initialize Google GenAI client: {exc}", original_error=exc)

    def embed_chunk(
        self,
        chunk_id: str,
        document_id: str,
        chunk_text: str,
        chunk_hash: str,
        document_type: str = "transit_document",
        title: str = "",
        dataset_version: Optional[str] = None,
        pipeline_version: Optional[str] = None,
        estimated_tokens: Optional[int] = None,
    ) -> VectorRecord:
        """
        Submits a single chunk text to Gemini and returns a validated VectorRecord.

        Args:
            chunk_id: Unique chunk identifier (e.g. doc_stop_380_c000).
            document_id: Parent document identifier.
            chunk_text: Raw or enriched Markdown text payload.
            chunk_hash: SHA-256 hash of the source chunk.
            document_type: Transit document category.
            title: Human-readable title of document or chunk.
            dataset_version: Source dataset version.
            pipeline_version: Active embedding pipeline version.
            estimated_tokens: Offline estimated token count.

        Returns:
            VectorRecord: Validated 768-dimensional dense vector record.

        Raises:
            AuthenticationError: On 401/403 credentials failures.
            RateLimitError: On 429 quota/rate limit spikes.
            TransientServerError: On 500/503 backend or network timeouts.
            InvalidRequestError: On empty text, malformed schema, or non-finite vectors.
        """
        # 1. Input pre-validation
        if not chunk_text or not chunk_text.strip():
            raise InvalidRequestError(f"Cannot generate embedding for empty chunk_text (chunk_id='{chunk_id}').")

        # 2. Build SDK Request Configuration
        config = types.EmbedContentConfig(
            task_type=self.task_type,
            output_dimensionality=self.embedding_dimensions,
            title=title if title else None,
        )

        # 3. Remote API Execution with Typed Error Classification
        start_time = time.perf_counter()
        try:
            response = self.client.models.embed_content(
                model=self.model_name,
                contents=chunk_text,
                config=config,
            )
        except errors.APIError as exc:
            self._classify_and_raise_api_error(exc, chunk_id)
        except (TimeoutError, ConnectionError) as exc:
            raise TransientServerError(f"Network transport timeout for chunk '{chunk_id}': {exc}", original_error=exc)
        except Exception as exc:
            # Check for httpx timeout/connect errors that might not be APIError
            exc_str = str(exc).lower()
            if "timeout" in exc_str or "connection" in exc_str:
                raise TransientServerError(f"Network connection failure for chunk '{chunk_id}': {exc}", original_error=exc)
            raise EmbeddingServiceError(f"Unexpected error while embedding chunk '{chunk_id}': {exc}", original_error=exc)

        elapsed_seconds = time.perf_counter() - start_time

        # 4. Extract and Validate Response Payload
        vector = self._extract_vector_from_response(response, chunk_id)
        l2_norm = self._validate_vector_elements(vector, chunk_id)

        # 5. Extract Token Usage Metadata (if provided by API)
        api_reported_tokens = self._extract_token_usage(response)

        # 6. Construct and return verified VectorRecord
        return VectorRecord(
            chunk_id=chunk_id,
            document_id=document_id,
            document_type=document_type,
            title=title or f"Document {document_id}",
            chunk_hash=chunk_hash,
            dataset_version=dataset_version or self.dataset_version,
            pipeline_version=pipeline_version or self.pipeline_version,
            embedding_metadata=EmbeddingMetadata(
                model=self.model_name,
                output_dimensionality=self.embedding_dimensions,
                task_type=self.task_type,
                vector_l2_norm=l2_norm,
                is_finite=True,
                generation_latency_seconds=round(elapsed_seconds, 4),
                generated_at_utc=datetime.now(timezone.utc),
            ),
            token_accounting=TokenAccounting(
                estimated_tokens=estimated_tokens if estimated_tokens is not None else 0,
                api_reported_tokens=api_reported_tokens,
                source_character_length=len(chunk_text),
            ),
            embedding=vector,
        )

    def embed_query(
        self,
        query: str,
    ) -> List[float]:
        """
        Generates and strictly validates a 768-dimensional dense vector for a user query.
        Uses task_type='RETRIEVAL_QUERY' per Google Gemini embedding specification.

        Args:
            query: Natural language transport search query or question.

        Returns:
            List[float]: Validated 768-dimensional dense float vector.

        Raises:
            AuthenticationError: On 401/403 credentials failures.
            RateLimitError: On 429 quota/rate limit spikes.
            TransientServerError: On 500/503 backend or network timeouts.
            InvalidRequestError: On empty text, malformed schema, or non-finite vectors.
        """
        # 1. Input pre-validation
        if not query or not query.strip():
            raise InvalidRequestError("Cannot generate embedding for empty or whitespace-only query.")

        # 2. Build SDK Request Configuration with task_type='RETRIEVAL_QUERY'
        config = types.EmbedContentConfig(
            task_type="RETRIEVAL_QUERY",
            output_dimensionality=self.embedding_dimensions,
        )

        # 3. Remote API Execution with Typed Error Classification
        try:
            response = self.client.models.embed_content(
                model=self.model_name,
                contents=query.strip(),
                config=config,
            )
        except errors.APIError as exc:
            self._classify_and_raise_api_error(exc, "query")
        except (TimeoutError, ConnectionError) as exc:
            raise TransientServerError(f"Network transport timeout for query: {exc}", original_error=exc)
        except Exception as exc:
            exc_str = str(exc).lower()
            if "timeout" in exc_str or "connection" in exc_str:
                raise TransientServerError(f"Network connection failure for query: {exc}", original_error=exc)
            raise EmbeddingServiceError(f"Unexpected error while embedding query: {exc}", original_error=exc)

        # 4. Extract and Validate Response Payload
        vector = self._extract_vector_from_response(response, "query")
        self._validate_vector_elements(vector, "query")

        return vector

    def embed_batch(
        self,
        chunks: List[Dict[str, Any]],
        title: Optional[str] = None,
    ) -> List[VectorRecord]:
        """
        Submits a batch of chunks to Gemini and returns a list of validated VectorRecords.
        Preserves 1:1 order with input chunks.
        """
        if not chunks:
            return []

        # 1. Validate inputs
        for c in chunks:
            text = c.get("chunk_text", "")
            if not text or not text.strip():
                raise InvalidRequestError(f"Cannot generate embedding for empty chunk_text (chunk_id='{c.get('chunk_id')}').")

        contents = [c["chunk_text"] for c in chunks]
        config = types.EmbedContentConfig(
            task_type=self.task_type,
            output_dimensionality=self.embedding_dimensions,
            title=title if title else None,
        )

        batch_label = f"batch_{chunks[0].get('chunk_id')}_to_{chunks[-1].get('chunk_id')}"
        start_time = time.perf_counter()
        try:
            response = self.client.models.embed_content(
                model=self.model_name,
                contents=contents,
                config=config,
            )
        except errors.APIError as exc:
            self._classify_and_raise_api_error(exc, batch_label)
        except (TimeoutError, ConnectionError) as exc:
            raise TransientServerError(f"Network transport timeout for {batch_label}: {exc}", original_error=exc)
        except Exception as exc:
            exc_str = str(exc).lower()
            if "timeout" in exc_str or "connection" in exc_str:
                raise TransientServerError(f"Network connection failure for {batch_label}: {exc}", original_error=exc)
            raise EmbeddingServiceError(f"Unexpected error while embedding {batch_label}: {exc}", original_error=exc)

        elapsed_seconds = time.perf_counter() - start_time

        if not response or not hasattr(response, "embeddings") or not response.embeddings:
            raise InvalidRequestError(f"Gemini API returned an empty response with no embeddings for {batch_label}.")

        if len(response.embeddings) != len(chunks):
            raise InvalidRequestError(
                f"Gemini returned {len(response.embeddings)} embeddings for {len(chunks)} input chunks in {batch_label}."
            )

        api_reported_tokens = self._extract_token_usage(response)
        per_chunk_latency = round(elapsed_seconds / len(chunks), 4)

        results = []
        for idx, (c, emb_obj) in enumerate(zip(chunks, response.embeddings)):
            chunk_id = c["chunk_id"]
            if not hasattr(emb_obj, "values") or emb_obj.values is None:
                raise InvalidRequestError(f"Embedding object at index {idx} missing 'values' for chunk '{chunk_id}'.")

            vector = list(emb_obj.values)
            l2_norm = self._validate_vector_elements(vector, chunk_id)

            rec = VectorRecord(
                chunk_id=chunk_id,
                document_id=c["document_id"],
                document_type=c.get("document_type", "transit_document"),
                title=c.get("title", f"Document {c['document_id']}"),
                chunk_hash=c["chunk_hash"],
                dataset_version=c.get("dataset_version", self.dataset_version),
                pipeline_version=c.get("pipeline_version", self.pipeline_version),
                embedding_metadata=EmbeddingMetadata(
                    model=self.model_name,
                    output_dimensionality=self.embedding_dimensions,
                    task_type=self.task_type,
                    vector_l2_norm=l2_norm,
                    is_finite=True,
                    generation_latency_seconds=per_chunk_latency,
                    generated_at_utc=datetime.now(timezone.utc),
                ),
                token_accounting=TokenAccounting(
                    estimated_tokens=c.get("token_count", c.get("estimated_tokens", 0)),
                    api_reported_tokens=api_reported_tokens,
                    source_character_length=len(c["chunk_text"]),
                ),
                embedding=vector,
            )
            results.append(rec)

        return results

    # --------------------------------------------------------------------------
    # Internal Validation & Extraction Helpers
    # --------------------------------------------------------------------------

    def _classify_and_raise_api_error(self, exc: errors.APIError, chunk_id: str) -> None:
        """Classifies Google GenAI APIError into domain-specific exception types."""
        status_code = getattr(exc, "code", None)
        msg = f"Gemini API error for chunk '{chunk_id}': {exc}"

        if status_code in (401, 403):
            raise AuthenticationError(msg, original_error=exc, status_code=status_code)
        elif status_code == 429:
            raise RateLimitError(msg, original_error=exc, status_code=status_code)
        elif status_code in (500, 502, 503, 504):
            raise TransientServerError(msg, original_error=exc, status_code=status_code)
        elif status_code in (400, 404):
            raise InvalidRequestError(msg, original_error=exc, status_code=status_code)
        else:
            raise EmbeddingServiceError(msg, original_error=exc, status_code=status_code)

    def _extract_vector_from_response(self, response: Any, chunk_id: str) -> List[float]:
        """Extracts the raw vector values from the Gemini response structure."""
        if not response or not hasattr(response, "embeddings") or not response.embeddings:
            raise InvalidRequestError(f"Gemini API returned an empty response with no embeddings for chunk '{chunk_id}'.")

        embedding_obj = response.embeddings[0]
        if not hasattr(embedding_obj, "values") or embedding_obj.values is None:
            raise InvalidRequestError(f"Gemini embedding object is missing 'values' array for chunk '{chunk_id}'.")

        vector = list(embedding_obj.values)
        if len(vector) == 0:
            raise InvalidRequestError(f"Gemini returned an empty vector values list for chunk '{chunk_id}'.")

        return vector

    def _validate_vector_elements(self, vector: List[float], chunk_id: str) -> float:
        """
        Validates 768 dimensionality, numeric types, finite values, and computes positive L2 norm.
        """
        if len(vector) != self.embedding_dimensions:
            raise InvalidRequestError(
                f"Vector dimension mismatch for chunk '{chunk_id}': "
                f"expected {self.embedding_dimensions}, received {len(vector)}."
            )

        sum_squares = 0.0
        for idx, val in enumerate(vector):
            if not isinstance(val, (int, float)):
                raise InvalidRequestError(
                    f"Vector element at index {idx} for chunk '{chunk_id}' is non-numeric: {val}."
                )
            if not math.isfinite(val):
                raise InvalidRequestError(
                    f"Vector element at index {idx} for chunk '{chunk_id}' is non-finite (NaN or Inf): {val}."
                )
            sum_squares += float(val) * float(val)

        l2_norm = math.sqrt(sum_squares)
        if l2_norm <= 0.0:
            raise InvalidRequestError(f"Vector L2 norm for chunk '{chunk_id}' must be positive, got {l2_norm}.")

        return l2_norm

    def _extract_token_usage(self, response: Any) -> Optional[int]:
        """Extracts token usage metadata if populated by the Gemini API response."""
        metadata = getattr(response, "metadata", None)
        if metadata is not None:
            return getattr(metadata, "token_count", None) or getattr(metadata, "billable_character_count", None)
        return None

    def __repr__(self) -> str:
        return (
            f"EmbeddingService(model='{self.model_name}', "
            f"dimensions={self.embedding_dimensions}, task_type='{self.task_type}')"
        )
