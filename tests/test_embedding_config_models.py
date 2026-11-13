"""
Unit tests for Phase 3 — Step 6D.1: Batch Embedding Pipeline Configuration and Data Models.
Tests configuration validation, credential masking, typed model constraints,
and state ledger accounting.
"""

import math
from datetime import datetime, timezone
import unittest

from pydantic import ValidationError

from app.core.config import Settings
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


class TestSettingsValidation(unittest.TestCase):
    """Tests for application settings and configuration constraints."""

    def test_default_embedding_settings(self):
        """Verifies default embedding settings match project specifications."""
        settings = Settings()
        self.assertEqual(settings.EMBEDDING_MODEL, "gemini-embedding-001")
        self.assertEqual(settings.EMBEDDING_DIMENSIONS, 768)
        self.assertEqual(settings.EMBEDDING_TASK_TYPE, "RETRIEVAL_DOCUMENT")
        self.assertEqual(settings.EMBEDDING_BATCH_SIZE, 20)
        self.assertEqual(settings.MAX_DAILY_REQUEST_BUDGET, 200)
        self.assertEqual(settings.MAX_TPM_SAFETY_LIMIT, 24000)
        self.assertEqual(settings.MAX_RETRY_ATTEMPTS, 3)
        self.assertEqual(settings.EMBEDDING_MAX_RETRIES, 3)

    def test_model_name_prefix_normalization(self):
        """Verifies that 'models/gemini-embedding-001' is normalized to 'gemini-embedding-001'."""
        s = Settings(EMBEDDING_MODEL="models/gemini-embedding-001")
        self.assertEqual(s.EMBEDDING_MODEL, "gemini-embedding-001")

    def test_invalid_embedding_model_rejected(self):
        """Verifies deprecated text-embedding-004 or arbitrary models are rejected."""
        with self.assertRaises(ValidationError) as ctx:
            Settings(EMBEDDING_MODEL="text-embedding-004")
        self.assertIn("Invalid EMBEDDING_MODEL", str(ctx.exception))

        with self.assertRaises(ValidationError) as ctx:
            Settings(EMBEDDING_MODEL="random-embedding-model")
        self.assertIn("Invalid EMBEDDING_MODEL", str(ctx.exception))

    def test_invalid_embedding_dimensions_rejected(self):
        """Verifies only 768 dimensions are allowed."""
        with self.assertRaises(ValidationError) as ctx:
            Settings(EMBEDDING_DIMENSIONS=1536)
        self.assertIn("Must be exactly 768", str(ctx.exception))

        with self.assertRaises(ValidationError) as ctx:
            Settings(EMBEDDING_DIMENSIONS=512)
        self.assertIn("Must be exactly 768", str(ctx.exception))

    def test_invalid_task_type_rejected(self):
        """Verifies invalid Gemini task types are rejected."""
        with self.assertRaises(ValidationError) as ctx:
            Settings(EMBEDDING_TASK_TYPE="INVALID_TASK")
        self.assertIn("Invalid EMBEDDING_TASK_TYPE", str(ctx.exception))

    def test_batch_size_bounds(self):
        """Verifies batch size bounds (1 to 50)."""
        # Valid boundaries
        s1 = Settings(EMBEDDING_BATCH_SIZE=1)
        self.assertEqual(s1.EMBEDDING_BATCH_SIZE, 1)
        s50 = Settings(EMBEDDING_BATCH_SIZE=50)
        self.assertEqual(s50.EMBEDDING_BATCH_SIZE, 50)

        # Invalid bounds
        with self.assertRaises(ValidationError):
            Settings(EMBEDDING_BATCH_SIZE=0)
        with self.assertRaises(ValidationError):
            Settings(EMBEDDING_BATCH_SIZE=51)

    def test_daily_budget_bounds(self):
        """Verifies daily budget bounds (1 to 1000)."""
        s_valid = Settings(MAX_DAILY_REQUEST_BUDGET=500)
        self.assertEqual(s_valid.MAX_DAILY_REQUEST_BUDGET, 500)

        with self.assertRaises(ValidationError):
            Settings(MAX_DAILY_REQUEST_BUDGET=0)
        with self.assertRaises(ValidationError):
            Settings(MAX_DAILY_REQUEST_BUDGET=1001)

    def test_tpm_limit_bounds(self):
        """Verifies TPM safety limit bounds (1,000 to 30,000)."""
        s_valid = Settings(MAX_TPM_SAFETY_LIMIT=20000)
        self.assertEqual(s_valid.MAX_TPM_SAFETY_LIMIT, 20000)

        with self.assertRaises(ValidationError):
            Settings(MAX_TPM_SAFETY_LIMIT=500)
        with self.assertRaises(ValidationError):
            Settings(MAX_TPM_SAFETY_LIMIT=35000)

    def test_retry_attempts_bounds(self):
        """Verifies retry attempts bounds (0 to 10)."""
        s_valid = Settings(MAX_RETRY_ATTEMPTS=5)
        self.assertEqual(s_valid.MAX_RETRY_ATTEMPTS, 5)

        with self.assertRaises(ValidationError):
            Settings(MAX_RETRY_ATTEMPTS=-1)
        with self.assertRaises(ValidationError):
            Settings(MAX_RETRY_ATTEMPTS=11)

    def test_secret_masking_in_repr_and_str(self):
        """Verifies that API keys and database credentials are redacted in string/repr output."""
        s = Settings(
            GEMINI_API_KEY="AIzaSyTestSecretKey1234567890123456",
            DATABASE_URL="postgresql://postgres:mysecretpassword@localhost:5432/db",
        )
        repr_out = repr(s)
        str_out = str(s)
        self.assertNotIn("AIzaSyTestSecretKey", repr_out)
        self.assertNotIn("mysecretpassword", repr_out)
        self.assertIn("***REDACTED***", repr_out)
        self.assertNotIn("AIzaSyTestSecretKey", str_out)
        self.assertNotIn("mysecretpassword", str_out)


class TestDataModelsValidation(unittest.TestCase):
    """Tests for typed embedding data models and state ledgers."""

    def setUp(self):
        self.valid_hash = "2b6b0266fe2fbeaa8451a44e5d8a0c20ab2560d2e85a6ba10faad695b23d9061"
        self.sample_embedding = [0.01] * 768

    def test_chunk_processing_status_enum(self):
        """Verifies that all four required chunk processing states are present."""
        expected = {"pending", "completed", "retryable_failed", "permanently_failed"}
        actual = {status.value for status in ChunkProcessingStatus}
        self.assertEqual(actual, expected)

    def test_token_accounting_model(self):
        """Verifies TokenAccounting validation."""
        tokens = TokenAccounting(
            estimated_tokens=386,
            api_reported_tokens=390,
            source_character_length=1138,
        )
        self.assertEqual(tokens.estimated_tokens, 386)
        self.assertEqual(tokens.api_reported_tokens, 390)

        with self.assertRaises(ValidationError):
            TokenAccounting(estimated_tokens=-1, source_character_length=100)

    def test_embedding_metadata_model(self):
        """Verifies EmbeddingMetadata model validation and defaults."""
        meta = EmbeddingMetadata(
            model="gemini-embedding-001",
            output_dimensionality=768,
            task_type="RETRIEVAL_DOCUMENT",
            vector_l2_norm=0.5716,
            generation_latency_seconds=1.12,
        )
        self.assertEqual(meta.model, "gemini-embedding-001")
        self.assertEqual(meta.output_dimensionality, 768)
        self.assertTrue(meta.is_finite)

        # Rejects non-768 dimensions
        with self.assertRaises(ValidationError):
            EmbeddingMetadata(
                output_dimensionality=1536,
                vector_l2_norm=1.0,
            )

        # Rejects non-positive L2 norm
        with self.assertRaises(ValidationError):
            EmbeddingMetadata(
                vector_l2_norm=0.0,
            )

    def test_vector_record_valid_creation(self):
        """Verifies valid VectorRecord creation and vector length check."""
        record = VectorRecord(
            chunk_id="doc_stop_380_c000",
            document_id="doc_stop_380",
            document_type="stop_profile",
            title="Bus Stop Profile: SHOLINGANALLUR (Stop ID: 380)",
            chunk_hash=self.valid_hash,
            embedding_metadata=EmbeddingMetadata(
                vector_l2_norm=0.5716,
            ),
            token_accounting=TokenAccounting(
                estimated_tokens=386,
                source_character_length=1138,
            ),
            embedding=self.sample_embedding,
        )
        self.assertEqual(record.chunk_id, "doc_stop_380_c000")
        self.assertEqual(len(record.embedding), 768)

    def test_vector_record_invalid_hash_rejected(self):
        """Verifies invalid SHA-256 hashes are rejected."""
        with self.assertRaises(ValidationError):
            VectorRecord(
                chunk_id="doc_stop_380_c000",
                document_id="doc_stop_380",
                document_type="stop_profile",
                title="Test",
                chunk_hash="invalid_short_hash",
                embedding_metadata=EmbeddingMetadata(vector_l2_norm=1.0),
                token_accounting=TokenAccounting(estimated_tokens=100, source_character_length=400),
                embedding=self.sample_embedding,
            )

    def test_vector_record_invalid_dimensions_rejected(self):
        """Verifies that vector records with != 768 elements are rejected."""
        # 767 elements
        with self.assertRaises(ValidationError):
            VectorRecord(
                chunk_id="doc_stop_380_c000",
                document_id="doc_stop_380",
                document_type="stop_profile",
                title="Test",
                chunk_hash=self.valid_hash,
                embedding_metadata=EmbeddingMetadata(vector_l2_norm=1.0),
                token_accounting=TokenAccounting(estimated_tokens=100, source_character_length=400),
                embedding=[0.1] * 767,
            )

    def test_vector_record_non_finite_values_rejected(self):
        """Verifies that NaN and Inf vector values are strictly rejected."""
        nan_vector = list(self.sample_embedding)
        nan_vector[10] = float("nan")
        with self.assertRaises(ValidationError):
            VectorRecord(
                chunk_id="doc_stop_380_c000",
                document_id="doc_stop_380",
                document_type="stop_profile",
                title="Test",
                chunk_hash=self.valid_hash,
                embedding_metadata=EmbeddingMetadata(vector_l2_norm=1.0),
                token_accounting=TokenAccounting(estimated_tokens=100, source_character_length=400),
                embedding=nan_vector,
            )

        inf_vector = list(self.sample_embedding)
        inf_vector[20] = float("inf")
        with self.assertRaises(ValidationError):
            VectorRecord(
                chunk_id="doc_stop_380_c000",
                document_id="doc_stop_380",
                document_type="stop_profile",
                title="Test",
                chunk_hash=self.valid_hash,
                embedding_metadata=EmbeddingMetadata(vector_l2_norm=1.0),
                token_accounting=TokenAccounting(estimated_tokens=100, source_character_length=400),
                embedding=inf_vector,
            )

    def test_vector_record_repr_truncation(self):
        """Verifies that VectorRecord repr does not output all 768 floats."""
        record = VectorRecord(
            chunk_id="doc_stop_380_c000",
            document_id="doc_stop_380",
            document_type="stop_profile",
            title="Test",
            chunk_hash=self.valid_hash,
            embedding_metadata=EmbeddingMetadata(vector_l2_norm=0.5716),
            token_accounting=TokenAccounting(estimated_tokens=100, source_character_length=400),
            embedding=self.sample_embedding,
        )
        repr_str = repr(record)
        self.assertIn("(768 dims)", repr_str)
        self.assertLess(len(repr_str), 300)

    def test_embedding_checkpoint_record_completed_consistency(self):
        """Verifies that a completed checkpoint record requires path, timestamp, and norm."""
        # Missing vector_file_path for completed status
        with self.assertRaises(ValidationError):
            EmbeddingCheckpointRecord(
                chunk_id="doc_stop_380_c000",
                parent_document_id="doc_stop_380",
                chunk_hash=self.valid_hash,
                processing_status=ChunkProcessingStatus.COMPLETED,
                estimated_tokens=386,
                completed_at_utc=datetime.now(timezone.utc),
                vector_l2_norm=0.57,
            )

        # Valid completed record
        valid_completed = EmbeddingCheckpointRecord(
            chunk_id="doc_stop_380_c000",
            parent_document_id="doc_stop_380",
            chunk_hash=self.valid_hash,
            processing_status=ChunkProcessingStatus.COMPLETED,
            estimated_tokens=386,
            vector_file_path="data/embeddings/vectors/doc_stop_380_c000.json",
            completed_at_utc=datetime.now(timezone.utc),
            vector_l2_norm=0.57,
        )
        self.assertEqual(valid_completed.processing_status, ChunkProcessingStatus.COMPLETED)

    def test_embedding_checkpoint_record_permanently_failed_consistency(self):
        """Verifies that permanently failed records must have error_details."""
        with self.assertRaises(ValidationError):
            EmbeddingCheckpointRecord(
                chunk_id="doc_stop_380_c000",
                parent_document_id="doc_stop_380",
                chunk_hash=self.valid_hash,
                processing_status=ChunkProcessingStatus.PERMANENTLY_FAILED,
                estimated_tokens=386,
            )

    def test_sensitive_data_sanitization(self):
        """Verifies that API keys and Postgres credentials are automatically sanitized in error details."""
        raw_error = {
            "message": "Call failed with API key AIzaSyA1B2C3D4E5F6G7H8I9J0K1L2M3N4O5P6Q",
            "db_uri": "postgresql://postgres:supersecret@db.supabase.com:5432/postgres",
        }
        record = EmbeddingCheckpointRecord(
            chunk_id="doc_stop_380_c000",
            parent_document_id="doc_stop_380",
            chunk_hash=self.valid_hash,
            processing_status=ChunkProcessingStatus.PERMANENTLY_FAILED,
            estimated_tokens=386,
            error_details=raw_error,
        )
        self.assertNotIn("AIzaSyA1B2C3D4E5F6G7H8I9J0K1L2M3N4O5P6Q", str(record.error_details))
        self.assertIn("***REDACTED_API_KEY***", str(record.error_details))
        self.assertNotIn("supersecret", str(record.error_details))
        self.assertIn("://***:***@", str(record.error_details))

    def test_api_request_tracker(self):
        """Verifies API request counter monotonicity, budget bounds, and sum equality."""
        tracker = APIRequestTracker(daily_budget_limit=5)
        self.assertEqual(tracker.global_api_requests, 0)
        self.assertTrue(tracker.can_make_request())

        # Record initial request
        tracker.record_request("initial")
        self.assertEqual(tracker.global_api_requests, 1)
        self.assertEqual(tracker.initial_requests, 1)

        # Record retry request
        tracker.record_request("retry")
        self.assertEqual(tracker.global_api_requests, 2)
        self.assertEqual(tracker.retry_requests, 1)

        # Record bisection request
        tracker.record_request("bisection")
        self.assertEqual(tracker.global_api_requests, 3)
        self.assertEqual(tracker.bisection_requests, 1)

        # Check sum assertion
        self.assertEqual(
            tracker.global_api_requests,
            tracker.initial_requests + tracker.retry_requests + tracker.bisection_requests,
        )

        # Push to limit
        tracker.record_request("initial")
        tracker.record_request("initial")
        self.assertEqual(tracker.global_api_requests, 5)
        self.assertFalse(tracker.can_make_request())

        # Exceeding budget raises ValueError
        with self.assertRaises(ValueError) as ctx:
            tracker.record_request("initial")
        self.assertIn("Daily API request safety budget reached", str(ctx.exception))

    def test_batch_checkpoint_ledger_sync(self):
        """Verifies BatchCheckpointLedger creation and status count synchronization."""
        ledger = BatchCheckpointLedger(total_catalog_chunks=2)
        ledger.chunks["c1"] = EmbeddingCheckpointRecord(
            chunk_id="c1",
            parent_document_id="doc1",
            chunk_hash=self.valid_hash,
            processing_status=ChunkProcessingStatus.PENDING,
            estimated_tokens=200,
        )
        ledger.chunks["c2"] = EmbeddingCheckpointRecord(
            chunk_id="c2",
            parent_document_id="doc2",
            chunk_hash=self.valid_hash,
            processing_status=ChunkProcessingStatus.COMPLETED,
            vector_file_path="data/embeddings/vectors/c2.json",
            vector_l2_norm=0.55,
            completed_at_utc=datetime.now(timezone.utc),
            estimated_tokens=300,
        )

        counts = ledger.sync_status_counts()
        self.assertEqual(counts["pending"], 1)
        self.assertEqual(counts["completed"], 1)
        self.assertEqual(counts["retryable_failed"], 0)
        self.assertEqual(counts["permanently_failed"], 0)
        self.assertEqual(len(ledger.get_chunks_by_status(ChunkProcessingStatus.COMPLETED)), 1)


if __name__ == "__main__":
    unittest.main()

