"""
Module: tests/test_api_rag.py
Purpose: Comprehensive unit and integration tests for Phase 5 Step 5.3 RAG semantic retrieval:
         - Query embedding generation and validation (768 dims, finite floats)
         - Error classification (AuthenticationError, RateLimitError, TransientServerError, InvalidRequestError)
         - RAGService vector similarity querying, chunk type filtering, and empty result handling
         - FastAPI endpoint POST /api/v1/rag/search validation, response schema, and HTTP error mappings
"""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import status
from fastapi.testclient import TestClient
from google.genai import errors

from app.ai.rag.embeddings.embedding_service import (
    AuthenticationError,
    EmbeddingService,
    InvalidRequestError,
    RateLimitError,
    TransientServerError,
)
from app.api.v1.endpoints.rag import get_rag_service
from app.db.pool import get_db
from app.main import app
from app.schemas.rag import RAGSearchRequest, RAGSearchResponse
from app.services.rag_service import RAGService


def _create_mock_embed_response(values):
    """Helper to create a Gemini embed_content response mock."""
    mock_resp = MagicMock()
    mock_emb = MagicMock()
    mock_emb.values = values
    mock_resp.embeddings = [mock_emb]
    return mock_resp


class TestQueryEmbeddingService(unittest.TestCase):
    """Unit tests for query embedding generation in EmbeddingService."""

    def setUp(self):
        self.mock_client = MagicMock()
        self.service = EmbeddingService(
            client=self.mock_client,
            model_name="gemini-embedding-001",
            embedding_dimensions=768,
            task_type="RETRIEVAL_DOCUMENT",
        )

    def test_embed_query_valid(self):
        """Generates a valid 768-dimensional float embedding using RETRIEVAL_QUERY."""
        dummy_vector = [0.01 * (i % 10) for i in range(768)]
        self.mock_client.models.embed_content.return_value = _create_mock_embed_response(dummy_vector)

        vec = self.service.embed_query("Which bus goes to T Nagar?")
        self.assertEqual(len(vec), 768)
        self.assertEqual(vec, dummy_vector)

        # Verify task_type was set to RETRIEVAL_QUERY
        call_kwargs = self.mock_client.models.embed_content.call_args.kwargs
        self.assertEqual(call_kwargs["model"], "gemini-embedding-001")
        self.assertEqual(call_kwargs["contents"], "Which bus goes to T Nagar?")
        self.assertEqual(call_kwargs["config"].task_type, "RETRIEVAL_QUERY")
        self.assertEqual(call_kwargs["config"].output_dimensionality, 768)

    def test_embed_query_empty_text_raises_invalid_request(self):
        """Empty or whitespace-only query raises InvalidRequestError."""
        for empty_val in ["", "   ", "\t\n"]:
            with self.subTest(val=repr(empty_val)):
                with self.assertRaises(InvalidRequestError):
                    self.service.embed_query(empty_val)

    def test_embed_query_invalid_dimension_raises_invalid_request(self):
        """Vector with dimension != 768 raises InvalidRequestError."""
        short_vector = [0.1] * 512
        self.mock_client.models.embed_content.return_value = _create_mock_embed_response(short_vector)

        with self.assertRaises(InvalidRequestError) as ctx:
            self.service.embed_query("Which bus goes to T Nagar?")
        self.assertIn("dimension mismatch", str(ctx.exception).lower())

    def test_embed_query_non_finite_values_raises_invalid_request(self):
        """Vector with NaN or Inf values raises InvalidRequestError."""
        nan_vector = [0.01] * 767 + [float("nan")]
        self.mock_client.models.embed_content.return_value = _create_mock_embed_response(nan_vector)

        with self.assertRaises(InvalidRequestError) as ctx:
            self.service.embed_query("Which bus goes to T Nagar?")
        self.assertIn("non-finite", str(ctx.exception).lower())

    def test_embed_query_authentication_error(self):
        """HTTP 401/403 from Gemini maps to AuthenticationError."""
        api_err = errors.APIError(403, "API_KEY_INVALID: The provided API key is invalid")
        self.mock_client.models.embed_content.side_effect = api_err

        with self.assertRaises(AuthenticationError):
            self.service.embed_query("Which bus goes to T Nagar?")

    def test_embed_query_rate_limit_error(self):
        """HTTP 429 from Gemini maps to RateLimitError."""
        api_err = errors.APIError(429, "ResourceExhausted: Quota exceeded")
        self.mock_client.models.embed_content.side_effect = api_err

        with self.assertRaises(RateLimitError):
            self.service.embed_query("Which bus goes to T Nagar?")

    def test_embed_query_transient_server_error(self):
        """HTTP 503 from Gemini maps to TransientServerError."""
        api_err = errors.APIError(503, "ServiceUnavailable: Backend timeout")
        self.mock_client.models.embed_content.side_effect = api_err

        with self.assertRaises(TransientServerError):
            self.service.embed_query("Which bus goes to T Nagar?")


class TestRAGServiceUnit(unittest.IsolatedAsyncioTestCase):
    """Unit tests for RAGService search logic with mocked DB connection and EmbeddingService."""

    def setUp(self):
        self.mock_conn = MagicMock()
        self.mock_cursor = AsyncMock()

        cm = AsyncMock()
        cm.__aenter__.return_value = self.mock_cursor
        cm.__aexit__.return_value = None
        self.mock_conn.cursor.return_value = cm

        self.mock_embedding_service = MagicMock()
        self.dummy_vector = [0.02] * 768
        self.mock_embedding_service.embed_query.return_value = self.dummy_vector

        self.rag_service = RAGService(embedding_service=self.mock_embedding_service)

    async def test_search_successful_retrieval(self):
        """Retrieves and ranks chunks with similarity scores properly formatted."""
        self.mock_cursor.fetchall.return_value = [
            {
                "chunk_id": "doc_corridor_c001",
                "document_id": "doc_corridor",
                "chunk_type": "corridor_guide",
                "title": "Corridor Travel Guide",
                "content": "Direct MTC bus routes connect Sholinganallur to T Nagar.",
                "metadata": {"section": "overview"},
                "similarity": 0.773512,
            },
            {
                "chunk_id": "doc_route_102CT_c000",
                "document_id": "doc_route_102CT",
                "chunk_type": "route_profile",
                "title": "Route 102CT Profile",
                "content": "Route 102CT operates frequent trips.",
                "metadata": {},
                "similarity": 0.744820,
            },
        ]

        resp = await self.rag_service.search(
            conn=self.mock_conn,
            query="Which bus connects to T Nagar?",
            top_k=2,
            chunk_type=None,
        )

        self.assertIsInstance(resp, RAGSearchResponse)
        self.assertEqual(resp.query, "Which bus connects to T Nagar?")
        self.assertEqual(resp.retrieved_count, 2)
        self.assertEqual(len(resp.chunks), 2)

        # Check first chunk
        c1 = resp.chunks[0]
        self.assertEqual(c1.chunk_id, "doc_corridor_c001")
        self.assertEqual(c1.chunk_type, "corridor_guide")
        self.assertEqual(c1.similarity, 0.7735)

        # Verify SQL executed with vector and limit
        call_args = self.mock_cursor.execute.call_args
        sql = call_args[0][0]
        params = call_args[0][1]
        self.assertIn("ORDER BY c.embedding <=> %(vector)s ASC", sql)
        self.assertIn("LIMIT %(top_k)s", sql)
        self.assertEqual(params["top_k"], 2)
        self.assertIsNone(params["chunk_type"])

    async def test_search_with_chunk_type_filter(self):
        """Applies chunk_type filter parameter correctly."""
        self.mock_cursor.fetchall.return_value = [
            {
                "chunk_id": "doc_corridor_c001",
                "document_id": "doc_corridor",
                "chunk_type": "corridor_guide",
                "title": "Corridor Travel Guide",
                "content": "Content text.",
                "metadata": {},
                "similarity": 0.8200,
            }
        ]

        resp = await self.rag_service.search(
            conn=self.mock_conn,
            query="Direct connection guide",
            top_k=5,
            chunk_type="CORRIDOR_GUIDE",
        )

        self.assertEqual(resp.retrieved_count, 1)
        params = self.mock_cursor.execute.call_args[0][1]
        self.assertEqual(params["chunk_type"], "CORRIDOR_GUIDE")

    async def test_search_empty_results_returns_empty_list(self):
        """When database finds no chunks, returns retrieved_count=0 and chunks=[]."""
        self.mock_cursor.fetchall.return_value = []

        resp = await self.rag_service.search(
            conn=self.mock_conn,
            query="Non-existent query with no matches",
            top_k=3,
        )

        self.assertEqual(resp.retrieved_count, 0)
        self.assertEqual(resp.chunks, [])

    async def test_similarity_range_positive_zero_negative_and_overshoot(self):
        """
        Verifies that similarity is preserved in full [-1.0, 1.0] range:
        - Positive similarity preserved
        - Zero similarity preserved
        - Negative similarity preserved (not clamped to 0)
        - Boundary overshoots (1.00000001, -1.00000001) safely clamped to [-1.0, 1.0]
        - Rounded to 4 decimal places
        """
        self.mock_cursor.fetchall.return_value = [
            {
                "chunk_id": "c_pos",
                "document_id": "d1",
                "chunk_type": "corridor_guide",
                "title": "Positive chunk",
                "content": "Text",
                "metadata": {},
                "similarity": 0.85429,  # rounds to 0.8543
            },
            {
                "chunk_id": "c_zero",
                "document_id": "d2",
                "chunk_type": "route_profile",
                "title": "Zero similarity chunk (orthogonal)",
                "content": "Text",
                "metadata": {},
                "similarity": 0.0,  # exactly 0.0
            },
            {
                "chunk_id": "c_neg",
                "document_id": "d3",
                "chunk_type": "stop_profile",
                "title": "Negative similarity chunk",
                "content": "Text",
                "metadata": {},
                "similarity": -0.42318,  # rounds to -0.4232 (negative preserved!)
            },
            {
                "chunk_id": "c_upper_overshoot",
                "document_id": "d4",
                "chunk_type": "route_profile",
                "title": "Upper boundary overshoot",
                "content": "Text",
                "metadata": {},
                "similarity": 1.000000005,  # float imprecision above 1.0 -> clamped to 1.0
            },
            {
                "chunk_id": "c_lower_overshoot",
                "document_id": "d5",
                "chunk_type": "route_profile",
                "title": "Lower boundary overshoot",
                "content": "Text",
                "metadata": {},
                "similarity": -1.000000008,  # float imprecision below -1.0 -> clamped to -1.0
            },
        ]

        resp = await self.rag_service.search(
            conn=self.mock_conn,
            query="Evaluate full range similarity",
            top_k=5,
        )

        self.assertEqual(resp.retrieved_count, 5)
        sims = [c.similarity for c in resp.chunks]
        self.assertEqual(sims, [0.8543, 0.0, -0.4232, 1.0, -1.0])

    async def test_descending_similarity_corresponds_to_ascending_cosine_distance(self):
        """
        Validates that ascending cosine distance from PostgreSQL (0.1, 0.4, 1.0, 1.6, 2.0)
        produces strictly descending cosine similarity (+0.9, +0.6, 0.0, -0.6, -1.0).
        """
        cosine_distances = [0.1, 0.4, 1.0, 1.6, 2.0]
        # SQL returns 1.0 - distance
        self.mock_cursor.fetchall.return_value = [
            {
                "chunk_id": f"c_{idx}",
                "document_id": f"d_{idx}",
                "chunk_type": "route_profile",
                "title": f"Chunk rank {idx}",
                "content": "Text",
                "metadata": {},
                "similarity": 1.0 - dist,
            }
            for idx, dist in enumerate(cosine_distances)
        ]

        resp = await self.rag_service.search(
            conn=self.mock_conn,
            query="Sort order verification",
            top_k=5,
        )

        sims = [c.similarity for c in resp.chunks]
        expected_sims = [0.9, 0.6, 0.0, -0.6, -1.0]
        self.assertEqual(sims, expected_sims)
        # Verify strictly descending order
        for i in range(len(sims) - 1):
            self.assertGreater(sims[i], sims[i + 1])


class TestRAGSearchAPIEndpoint(unittest.TestCase):
    """Integration tests for POST /api/v1/rag/search endpoint using FastAPI TestClient."""

    def setUp(self):
        self.client = TestClient(app, raise_server_exceptions=False)
        self.mock_rag_service = MagicMock(spec=RAGService)

        # Override dependencies
        app.dependency_overrides[get_rag_service] = lambda: self.mock_rag_service
        app.dependency_overrides[get_db] = lambda: AsyncMock()

    def tearDown(self):
        app.dependency_overrides.clear()

    def test_post_rag_search_success_200(self):
        """Valid query returns 200 OK with expected RAGSearchResponse payload."""
        self.mock_rag_service.search = AsyncMock(
            return_value=RAGSearchResponse(
                query="Direct bus to T Nagar",
                retrieved_count=1,
                chunks=[
                    {
                        "chunk_id": "doc_corridor_c001",
                        "document_id": "doc_corridor",
                        "chunk_type": "corridor_guide",
                        "title": "Corridor Travel Guide",
                        "content": "Direct bus routes connect Sholinganallur to T Nagar.",
                        "similarity": 0.7735,
                        "metadata": {"hub": "Sholinganallur"},
                    }
                ],
            )
        )

        resp = self.client.post(
            "/api/v1/rag/search",
            json={
                "query": "Direct bus to T Nagar",
                "top_k": 3,
                "chunk_type": "CORRIDOR_GUIDE",
            },
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        data = resp.json()
        self.assertEqual(data["query"], "Direct bus to T Nagar")
        self.assertEqual(data["retrieved_count"], 1)
        self.assertEqual(len(data["chunks"]), 1)
        self.assertEqual(data["chunks"][0]["chunk_id"], "doc_corridor_c001")
        self.assertEqual(data["chunks"][0]["similarity"], 0.7735)

    def test_post_rag_search_empty_results_200(self):
        """No matches returns 200 OK with empty chunks array."""
        self.mock_rag_service.search = AsyncMock(
            return_value=RAGSearchResponse(
                query="Query with no hits",
                retrieved_count=0,
                chunks=[],
            )
        )

        resp = self.client.post(
            "/api/v1/rag/search",
            json={"query": "Query with no hits"},
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        data = resp.json()
        self.assertEqual(data["retrieved_count"], 0)
        self.assertEqual(data["chunks"], [])

    def test_post_rag_search_query_too_short_422(self):
        """Empty query fails Pydantic validation with 422."""
        resp = self.client.post("/api/v1/rag/search", json={"query": ""})
        self.assertEqual(resp.status_code, 422)

    def test_post_rag_search_query_too_long_422(self):
        """Query > 500 characters fails validation with 422."""
        resp = self.client.post("/api/v1/rag/search", json={"query": "a" * 501})
        self.assertEqual(resp.status_code, 422)

    def test_post_rag_search_invalid_top_k_422(self):
        """top_k < 1 or > 20 fails validation with 422."""
        for invalid_k in [0, -1, 21, 100]:
            with self.subTest(top_k=invalid_k):
                resp = self.client.post(
                    "/api/v1/rag/search",
                    json={"query": "Test query", "top_k": invalid_k},
                )
                self.assertEqual(resp.status_code, 422)

    def test_post_rag_search_extra_field_forbidden_422(self):
        """Extra undeclared fields are forbidden by extra='forbid'."""
        resp = self.client.post(
            "/api/v1/rag/search",
            json={"query": "Valid query", "forbidden_key": "injected_value"},
        )
        self.assertEqual(resp.status_code, 422)

    def test_post_rag_search_gemini_auth_error_503(self):
        """AuthenticationError returns HTTP 503 without leaking credentials."""
        self.mock_rag_service.search = AsyncMock(
            side_effect=AuthenticationError("Invalid API key")
        )

        resp = self.client.post("/api/v1/rag/search", json={"query": "Bus timings?"})
        self.assertEqual(resp.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertIn("authentication failed", resp.json()["detail"].lower())

    def test_post_rag_search_gemini_rate_limit_error_429(self):
        """RateLimitError returns HTTP 429."""
        self.mock_rag_service.search = AsyncMock(
            side_effect=RateLimitError("Quota limit reached")
        )

        resp = self.client.post("/api/v1/rag/search", json={"query": "Bus timings?"})
        self.assertEqual(resp.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertIn("quota or rate limit", resp.json()["detail"].lower())

    def test_post_rag_search_gemini_transient_error_503(self):
        """TransientServerError returns HTTP 503."""
        self.mock_rag_service.search = AsyncMock(
            side_effect=TransientServerError("Gemini timeout")
        )

        resp = self.client.post("/api/v1/rag/search", json={"query": "Bus timings?"})
        self.assertEqual(resp.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertIn("temporarily unavailable", resp.json()["detail"].lower())

    def test_post_rag_search_invalid_request_error_400(self):
        """InvalidRequestError returns HTTP 400."""
        self.mock_rag_service.search = AsyncMock(
            side_effect=InvalidRequestError("Vector dimension mismatch: expected 768, got 512")
        )

        resp = self.client.post("/api/v1/rag/search", json={"query": "Bus timings?"})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("invalid request", resp.json()["detail"].lower())

    def test_post_rag_search_database_failure_500(self):
        """Database error (import psycopg; psycopg.Error) returns HTTP 500."""
        import psycopg
        self.mock_rag_service.search = AsyncMock(
            side_effect=psycopg.OperationalError("Database connection lost")
        )

        resp = self.client.post("/api/v1/rag/search", json={"query": "Bus timings?"})
        self.assertEqual(resp.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        self.assertIn("database", resp.json()["detail"].lower())

    def test_api_v1_module_directory_shows_rag_active(self):
        """GET /api/v1/ confirms 'rag' module is Active."""
        resp = self.client.get("/api/v1/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        modules = resp.json()["modules"]
        self.assertIn("Active", modules["rag"])


if __name__ == "__main__":
    unittest.main()

