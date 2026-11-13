"""
Unit tests for Phase 3 — Step 6D.3: Gemini Embedding Service.
Tests single-chunk embedding generation, parameter validation, response extraction,
error classification, and credential protection using mocked Gemini responses.
Zero real remote API calls are executed.
"""

import math
import unittest
from unittest.mock import MagicMock

from google.genai import errors

from app.ai.rag.embeddings.embedding_service import (
    AuthenticationError,
    EmbeddingService,
    EmbeddingServiceError,
    InvalidRequestError,
    RateLimitError,
    TransientServerError,
)


class TestEmbeddingService(unittest.TestCase):
    """Unit tests for EmbeddingService using mocked Google GenAI client."""

    def setUp(self):
        self.mock_client = MagicMock()
        self.sample_hash = "2b6b0266fe2fbeaa8451a44e5d8a0c20ab2560d2e85a6ba10faad695b23d9061"
        self.sample_text = "# Stop 380: Sholinganallur\nKey hub in OMR corridor."

        self.service = EmbeddingService(
            client=self.mock_client,
            model_name="gemini-embedding-001",
            embedding_dimensions=768,
            task_type="RETRIEVAL_DOCUMENT",
            dataset_version="v1.0.0-sholinganallur",
            pipeline_version="v1.0.0-embedding",
        )

    def _create_mock_response(
        self,
        values=None,
        token_count=350,
        has_embeddings=True,
    ):
        """Helper to construct a mock response structure resembling google-genai."""
        if values is None:
            values = [0.02] * 768

        mock_resp = MagicMock()
        if has_embeddings:
            mock_emb = MagicMock()
            mock_emb.values = values
            mock_resp.embeddings = [mock_emb]
        else:
            mock_resp.embeddings = []

        mock_resp.metadata = MagicMock(token_count=token_count)
        return mock_resp

    # 1. Successful embedding response
    def test_successful_embedding_response(self):
        """Verifies successful generation and extraction of a 768-dim VectorRecord."""
        mock_response = self._create_mock_response(token_count=386)
        self.mock_client.models.embed_content.return_value = mock_response

        record = self.service.embed_chunk(
            chunk_id="doc_stop_380_c000",
            document_id="doc_stop_380",
            chunk_text=self.sample_text,
            chunk_hash=self.sample_hash,
            document_type="stop_profile",
            title="Bus Stop Profile: SHOLINGANALLUR",
            estimated_tokens=386,
        )

        self.assertEqual(record.chunk_id, "doc_stop_380_c000")
        self.assertEqual(record.document_id, "doc_stop_380")
        self.assertEqual(record.chunk_hash, self.sample_hash)
        self.assertEqual(len(record.embedding), 768)
        self.assertEqual(record.embedding_metadata.output_dimensionality, 768)
        self.assertEqual(record.embedding_metadata.model, "gemini-embedding-001")
        self.assertEqual(record.embedding_metadata.task_type, "RETRIEVAL_DOCUMENT")
        self.assertTrue(record.embedding_metadata.is_finite)
        self.assertGreater(record.embedding_metadata.vector_l2_norm, 0.0)
        self.assertEqual(record.token_accounting.estimated_tokens, 386)
        self.assertEqual(record.token_accounting.api_reported_tokens, 386)

    # 2. Correct model, task type, and dimensions passed to client
    def test_correct_parameters_passed_to_sdk(self):
        """Verifies that EmbedContentConfig contains the exact expected parameters."""
        self.mock_client.models.embed_content.return_value = self._create_mock_response()

        self.service.embed_chunk(
            chunk_id="doc_stop_380_c000",
            document_id="doc_stop_380",
            chunk_text=self.sample_text,
            chunk_hash=self.sample_hash,
            title="Bus Stop Profile: SHOLINGANALLUR",
        )

        self.mock_client.models.embed_content.assert_called_once()
        call_kwargs = self.mock_client.models.embed_content.call_args.kwargs

        self.assertEqual(call_kwargs["model"], "gemini-embedding-001")
        self.assertEqual(call_kwargs["contents"], self.sample_text)

        config = call_kwargs["config"]
        self.assertEqual(config.task_type, "RETRIEVAL_DOCUMENT")
        self.assertEqual(config.output_dimensionality, 768)
        self.assertEqual(config.title, "Bus Stop Profile: SHOLINGANALLUR")

    # 3. Missing embedding response
    def test_missing_embedding_response(self):
        """Verifies that an empty or missing embeddings list raises InvalidRequestError."""
        # Empty embeddings array
        self.mock_client.models.embed_content.return_value = self._create_mock_response(has_embeddings=False)
        with self.assertRaises(InvalidRequestError) as ctx:
            self.service.embed_chunk("c1", "d1", self.sample_text, self.sample_hash)
        self.assertIn("no embeddings", str(ctx.exception).lower())

        # None values array
        mock_resp = MagicMock()
        mock_emb = MagicMock()
        mock_emb.values = None
        mock_resp.embeddings = [mock_emb]
        self.mock_client.models.embed_content.return_value = mock_resp
        with self.assertRaises(InvalidRequestError) as ctx:
            self.service.embed_chunk("c1", "d1", self.sample_text, self.sample_hash)
        self.assertIn("missing 'values'", str(ctx.exception).lower())

    # 4. Incorrect vector dimensions
    def test_incorrect_vector_dimensions(self):
        """Verifies that non-768 length responses raise InvalidRequestError."""
        # 512 dimensions
        self.mock_client.models.embed_content.return_value = self._create_mock_response(values=[0.01] * 512)
        with self.assertRaises(InvalidRequestError) as ctx:
            self.service.embed_chunk("c1", "d1", self.sample_text, self.sample_hash)
        self.assertIn("dimension mismatch", str(ctx.exception).lower())

        # 769 dimensions
        self.mock_client.models.embed_content.return_value = self._create_mock_response(values=[0.01] * 769)
        with self.assertRaises(InvalidRequestError) as ctx:
            self.service.embed_chunk("c1", "d1", self.sample_text, self.sample_hash)
        self.assertIn("dimension mismatch", str(ctx.exception).lower())

    # 5. NaN and Infinity values
    def test_nan_and_infinity_values(self):
        """Verifies that NaN or Infinite values are strictly rejected."""
        nan_values = [0.01] * 768
        nan_values[5] = float("nan")
        self.mock_client.models.embed_content.return_value = self._create_mock_response(values=nan_values)
        with self.assertRaises(InvalidRequestError) as ctx:
            self.service.embed_chunk("c1", "d1", self.sample_text, self.sample_hash)
        self.assertIn("non-finite", str(ctx.exception).lower())

        inf_values = [0.01] * 768
        inf_values[42] = float("inf")
        self.mock_client.models.embed_content.return_value = self._create_mock_response(values=inf_values)
        with self.assertRaises(InvalidRequestError) as ctx:
            self.service.embed_chunk("c1", "d1", self.sample_text, self.sample_hash)
        self.assertIn("non-finite", str(ctx.exception).lower())

    # 6. Empty input
    def test_empty_input(self):
        """Verifies that empty or whitespace text raises InvalidRequestError without API calls."""
        with self.assertRaises(InvalidRequestError) as ctx:
            self.service.embed_chunk("c1", "d1", "", self.sample_hash)
        self.assertIn("empty chunk_text", str(ctx.exception).lower())

        with self.assertRaises(InvalidRequestError):
            self.service.embed_chunk("c1", "d1", "   \n\t  ", self.sample_hash)

        # Zero API requests were dispatched
        self.mock_client.models.embed_content.assert_not_called()

    # 7. API authentication failure
    def test_api_authentication_failure(self):
        """Verifies that HTTP 401 and 403 are classified as AuthenticationError."""
        # HTTP 401
        self.mock_client.models.embed_content.side_effect = errors.APIError(
            401, {"error": {"message": "API key not valid."}}
        )
        with self.assertRaises(AuthenticationError) as ctx:
            self.service.embed_chunk("c1", "d1", self.sample_text, self.sample_hash)
        self.assertEqual(ctx.exception.status_code, 401)

        # HTTP 403
        self.mock_client.models.embed_content.side_effect = errors.APIError(
            403, {"error": {"message": "Permission denied."}}
        )
        with self.assertRaises(AuthenticationError) as ctx:
            self.service.embed_chunk("c1", "d1", self.sample_text, self.sample_hash)
        self.assertEqual(ctx.exception.status_code, 403)

        # Missing API key on initialization
        with self.assertRaises(AuthenticationError):
            EmbeddingService(client=None, api_key="")

    # 8. Rate-limit error classification
    def test_rate_limit_error_classification(self):
        """Verifies that HTTP 429 is classified as RateLimitError."""
        self.mock_client.models.embed_content.side_effect = errors.APIError(
            429, {"error": {"message": "ResourceExhausted: Quota exceeded for free tier."}}
        )
        with self.assertRaises(RateLimitError) as ctx:
            self.service.embed_chunk("c1", "d1", self.sample_text, self.sample_hash)
        self.assertEqual(ctx.exception.status_code, 429)

    # 9. Transient server error classification
    def test_transient_server_error_classification(self):
        """Verifies that HTTP 500/503 and network timeouts are classified as TransientServerError."""
        # HTTP 500
        self.mock_client.models.embed_content.side_effect = errors.APIError(
            500, {"error": {"message": "Internal error."}}
        )
        with self.assertRaises(TransientServerError) as ctx:
            self.service.embed_chunk("c1", "d1", self.sample_text, self.sample_hash)
        self.assertEqual(ctx.exception.status_code, 500)

        # HTTP 503
        self.mock_client.models.embed_content.side_effect = errors.APIError(
            503, {"error": {"message": "Service unavailable."}}
        )
        with self.assertRaises(TransientServerError) as ctx:
            self.service.embed_chunk("c1", "d1", self.sample_text, self.sample_hash)
        self.assertEqual(ctx.exception.status_code, 503)

        # Network TimeoutError
        self.mock_client.models.embed_content.side_effect = TimeoutError("Socket connection timed out")
        with self.assertRaises(TransientServerError) as ctx:
            self.service.embed_chunk("c1", "d1", self.sample_text, self.sample_hash)
        self.assertIn("timeout", str(ctx.exception).lower())

    # 10. No credential leakage in error messages
    def test_no_credential_leakage_in_error_messages(self):
        """Verifies that raw API keys are redacted from error message representations."""
        secret_key = "AIzaSyDummySecretKey1234567890123456"
        error_with_secret = f"Call failed with key {secret_key} on endpoint"

        self.mock_client.models.embed_content.side_effect = errors.APIError(
            401, {"error": {"message": error_with_secret}}
        )

        with self.assertRaises(AuthenticationError) as ctx:
            self.service.embed_chunk("c1", "d1", self.sample_text, self.sample_hash)

        err_msg = str(ctx.exception)
        self.assertNotIn(secret_key, err_msg)
        self.assertIn("***REDACTED_API_KEY***", err_msg)

        # Verify repr of service does not leak keys
        repr_str = repr(self.service)
        self.assertNotIn("key", repr_str.lower())
        self.assertIn("gemini-embedding-001", repr_str)


if __name__ == "__main__":
    unittest.main()

