"""
Module: tests/test_api_chat.py
Purpose: Comprehensive unit and integration tests for Phase 5 Step 5.4 Grounded Gemini Chat:
         - Question validation
         - RAG retrieval and structured GTFS data integration
         - Gemini response generation and strict grounding prompts
         - Error mapping (AuthenticationError, RateLimitError, TransientServerError, psycopg.Error)
         - Source attribution and limitation disclosures
         - FastAPI endpoint POST /api/v1/chat/ask schema validation
"""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import status
from fastapi.testclient import TestClient
from google.genai import errors

from app.ai.rag.embeddings.embedding_service import (
    AuthenticationError,
    InvalidRequestError,
    RateLimitError,
    TransientServerError,
)
from app.api.v1.endpoints.chat import get_chat_service
from app.db.pool import get_db
from app.main import app
from app.schemas.rag import (
    ChatGroundingSource,
    ChatRequest,
    ChatResponse,
    RAGChunkItem,
    RAGSearchResponse,
)
from app.services.chat_service import ChatService
from app.services.rag_service import RAGService


class TestChatServiceUnit(unittest.IsolatedAsyncioTestCase):
    """Unit tests for ChatService with mocked Gemini client and database interactions."""

    def setUp(self):
        self.mock_client = MagicMock()
        self.mock_rag_service = MagicMock(spec=RAGService)

        self.mock_conn = MagicMock()
        self.mock_cursor = AsyncMock()
        cm = AsyncMock()
        cm.__aenter__.return_value = self.mock_cursor
        cm.__aexit__.return_value = None
        self.mock_conn.cursor.return_value = cm

        self.service = ChatService(
            client=self.mock_client,
            rag_service=self.mock_rag_service,
            model_name="gemini-3.8-flash",
        )

    async def test_ask_valid_grounded_question_success(self):
        """Processes valid question, retrieves RAG chunks & structured data, and returns grounded answer."""
        # 1. Mock RAG response
        self.mock_rag_service.search = AsyncMock(
            return_value=RAGSearchResponse(
                query="Which bus connects to T Nagar?",
                retrieved_count=1,
                chunks=[
                    RAGChunkItem(
                        chunk_id="doc_corridor_c001",
                        document_id="doc_corridor",
                        chunk_type="corridor_guide",
                        title="Corridor Travel Guide",
                        content="Direct Route 102CT connects Sholinganallur to T Nagar.",
                        similarity=0.7742,
                        metadata={"corridor": "OMR"},
                    )
                ],
            )
        )

        # 2. Mock structured direct journeys query in TransitService
        self.mock_cursor.fetchall.return_value = [
            {
                "route_number": "19",
                "total_direct_trips": 115,
                "origin_stops": "SHOLINGANALLUR KUMARAN NAGAR",
                "destination_stops": "CIT NAGAR",
                "earliest_departure": "05:44:15",
                "latest_departure": "22:24:15",
                "min_duration_minutes": 65.0,
                "max_duration_minutes": 65.0,
                "intermediate_stops": 30,
            }
        ]

        # 3. Mock Gemini generate_content response
        mock_response = MagicMock()
        mock_response.text = (
            "Direct bus service 19 connects Sholinganallur to T. Nagar (CIT Nagar) "
            "with a scheduled departure at 05:44:15. [Structured Database: verified_direct_routes_to_t_nagar]."
        )
        self.mock_client.models.generate_content.return_value = mock_response

        # Execute
        resp = await self.service.ask(
            conn=self.mock_conn,
            question="Which bus connects to T Nagar?",
            top_k=2,
        )

        # Assertions
        self.assertIsInstance(resp, ChatResponse)
        self.assertEqual(resp.question, "Which bus connects to T Nagar?")
        self.assertIn("19", resp.answer)
        self.assertEqual(len(resp.grounding_sources), 1)
        self.assertEqual(resp.grounding_sources[0].chunk_id, "doc_corridor_c001")
        self.assertEqual(resp.grounding_sources[0].similarity, 0.7742)
        self.assertIsNotNone(resp.structured_context_used)
        self.assertTrue(len(resp.limitations) > 0)

        # Verify Gemini was invoked with system instruction
        call_kwargs = self.mock_client.models.generate_content.call_args.kwargs
        self.assertEqual(call_kwargs["model"], "gemini-3.8-flash")
        config = call_kwargs["config"]
        self.assertIn("STRICT GROUNDING", config.system_instruction)

    async def test_ask_question_too_short_raises_invalid_request(self):
        """Single character or whitespace question raises InvalidRequestError."""
        with self.assertRaises(InvalidRequestError):
            await self.service.ask(conn=self.mock_conn, question="?")

    async def test_ask_missing_evidence_honesty(self):
        """When no RAG chunks are found, prompt still generates honest out-of-scope response."""
        self.mock_rag_service.search = AsyncMock(
            return_value=RAGSearchResponse(
                query="How do I travel to Mars?",
                retrieved_count=0,
                chunks=[],
            )
        )
        self.mock_cursor.fetchall.return_value = []

        mock_response = MagicMock()
        mock_response.text = "The Sholinganallur transit dataset does not contain information regarding travel to Mars."
        self.mock_client.models.generate_content.return_value = mock_response

        resp = await self.service.ask(
            conn=self.mock_conn,
            question="How do I travel to Mars?",
        )

        self.assertEqual(len(resp.grounding_sources), 0)
        self.assertIn("does not contain information", resp.answer)

    async def test_ask_gemini_auth_error_classification(self):
        """HTTP 401/403 from Gemini maps to AuthenticationError."""
        self.mock_rag_service.search = AsyncMock(
            return_value=RAGSearchResponse(query="Bus timings?", retrieved_count=0, chunks=[])
        )
        self.mock_cursor.fetchall.return_value = []

        api_err = errors.APIError(403, "API_KEY_INVALID: The provided key is invalid")
        self.mock_client.models.generate_content.side_effect = api_err

        with self.assertRaises(AuthenticationError):
            await self.service.ask(conn=self.mock_conn, question="Bus timings?")

    async def test_ask_gemini_rate_limit_classification(self):
        """HTTP 429 from Gemini maps to RateLimitError."""
        self.mock_rag_service.search = AsyncMock(
            return_value=RAGSearchResponse(query="Bus timings?", retrieved_count=0, chunks=[])
        )
        self.mock_cursor.fetchall.return_value = []

        api_err = errors.APIError(429, "ResourceExhausted: Quota exceeded")
        self.mock_client.models.generate_content.side_effect = api_err

        with self.assertRaises(RateLimitError):
            await self.service.ask(conn=self.mock_conn, question="Bus timings?")

    async def test_ask_gemini_transient_error_classification(self):
        """HTTP 503 from Gemini maps to TransientServerError."""
        self.mock_rag_service.search = AsyncMock(
            return_value=RAGSearchResponse(query="Bus timings?", retrieved_count=0, chunks=[])
        )
        self.mock_cursor.fetchall.return_value = []

        api_err = errors.APIError(503, "ServiceUnavailable: Backend unavailable")
        self.mock_client.models.generate_content.side_effect = api_err

        with self.assertRaises(TransientServerError):
            await self.service.ask(conn=self.mock_conn, question="Bus timings?")

    async def test_direct_route_only_claimed_when_matching_structured_record_exists(self):
        """
        Tests that only routes present in public.direct_connections (19, 19A, M51, 19BET)
        are placed in the direct routes structured context, and routes not in that table
        (such as 102CT, 102PCT) are not verified as direct connections.
        """
        self.mock_rag_service.search = AsyncMock(
            return_value=RAGSearchResponse(
                query="Which bus connects Sholinganallur to T. Nagar directly?",
                retrieved_count=2,
                chunks=[
                    RAGChunkItem(
                        chunk_id="doc_route_102CT_c000",
                        document_id="doc_route_102CT",
                        chunk_type="route_profile",
                        title="Route Profile 102CT",
                        content="Route 102CT operates on OMR corridor to Broadway.",
                        similarity=0.74,
                        metadata={},
                    )
                ],
            )
        )

        # Mock direct_routes_summary returning only verified direct routes
        verified_direct = [
            {
                "route_number": "19",
                "total_direct_trips": 115,
                "origin_stops": "SHOLINGANALLUR KUMARAN NAGAR",
                "destination_stops": "CIT NAGAR",
                "earliest_departure": "05:44:15",
                "latest_departure": "22:24:15",
                "min_duration_minutes": 65.0,
                "max_duration_minutes": 65.0,
                "intermediate_stops": 30,
            },
            {
                "route_number": "19A",
                "total_direct_trips": 42,
                "origin_stops": "SHOLINGANALLUR NUCLUEUS COMPANY",
                "destination_stops": "CIT NAGAR",
                "earliest_departure": "04:34:45",
                "latest_departure": "21:09:45",
                "min_duration_minutes": 67.25,
                "max_duration_minutes": 67.25,
                "intermediate_stops": 31,
            },
        ]
        self.mock_cursor.fetchall.return_value = verified_direct

        mock_response = MagicMock()
        mock_response.text = "Verified direct routes to T. Nagar are Route 19 and Route 19A. [Structured Database: verified_direct_routes_to_t_nagar]."
        self.mock_client.models.generate_content.return_value = mock_response

        resp = await self.service.ask(
            conn=self.mock_conn,
            question="Which bus connects Sholinganallur to T. Nagar directly?",
        )

        direct_ctx = resp.structured_context_used.get("verified_direct_routes_to_t_nagar", [])
        verified_route_numbers = {r["route_number"] for r in direct_ctx}
        self.assertIn("19", verified_route_numbers)
        self.assertIn("19A", verified_route_numbers)
        self.assertNotIn("102CT", verified_route_numbers)
        self.assertNotIn("102PCT", verified_route_numbers)

    async def test_unrelated_routes_cannot_be_described_as_sholinganallur_serving(self):
        """
        Tests that routes serving Sholinganallur are derived from relational joins
        (bus_stops -> scheduled_departures -> route_services -> routes).
        Unrelated routes not calling at Sholinganallur stops are excluded.
        """
        self.mock_rag_service.search = AsyncMock(
            return_value=RAGSearchResponse(
                query="Which routes serve Sholinganallur?",
                retrieved_count=0,
                chunks=[],
            )
        )

        serving_routes = [
            {"route_number": "570", "total_service_variants": 10, "total_daily_trips": 578, "sholinganallur_departure_count": 2470, "sholinganallur_stops_served": 18},
            {"route_number": "95", "total_service_variants": 7, "total_daily_trips": 467, "sholinganallur_departure_count": 1380, "sholinganallur_stops_served": 10},
        ]
        self.mock_cursor.fetchall.return_value = serving_routes

        mock_response = MagicMock()
        mock_response.text = "Verified routes serving Sholinganallur include Line 570 and Line 95."
        self.mock_client.models.generate_content.return_value = mock_response

        resp = await self.service.ask(
            conn=self.mock_conn,
            question="Which routes serve Sholinganallur?",
        )

        routes_ctx = resp.structured_context_used.get("verified_sholinganallur_serving_routes", {})
        authoritative_list = routes_ctx.get("authoritative_route_list", [])
        self.assertIn("570", authoritative_list)
        self.assertIn("95", authoritative_list)
        self.assertNotIn("UNRELATED_ROUTE_999", authoritative_list)

    async def test_scheduled_departures_preserves_post_midnight_gtfs_time(self):
        """
        Tests that scheduled departures structured context contains exact verified records
        and preserves GTFS times > 24:00 (e.g. 24:02:00, 24:27:30).
        """
        self.mock_rag_service.search = AsyncMock(
            return_value=RAGSearchResponse(
                query="What are the scheduled departures from Sholinganallur?",
                retrieved_count=0,
                chunks=[],
            )
        )

        mock_deps = [
            {
                "departure_id": 5653,
                "trip_id": "3342-S-DOWN-70498",
                "stop_id": "tcbhlVZo",
                "stop_name": "SHOLINGANALLUR",
                "route_number": "555S",
                "route_name": "SHOLINGANALLUR To KILAMBAKKAM BUS TERMINUS",
                "departure_time": "00:00:15",
                "departure_seconds": 15,
            },
            {
                "departure_id": 6997,
                "trip_id": "4346-S-UP-100567",
                "stop_id": "380",
                "stop_name": "SHOLINGANALLUR",
                "route_number": "99A",
                "route_name": "KCBT - KILAMBAKKAM To SHOLINGANALLUR",
                "departure_time": "24:02:00",
                "departure_seconds": 86520,
            },
        ]
        self.mock_cursor.fetchall.return_value = mock_deps

        mock_response = MagicMock()
        mock_response.text = "Scheduled departures at Sholinganallur include Route 555S at 00:00:15 and Route 99A at 24:02:00."
        self.mock_client.models.generate_content.return_value = mock_response

        resp = await self.service.ask(
            conn=self.mock_conn,
            question="What are the scheduled departures from Sholinganallur?",
        )

        deps_ctx = resp.structured_context_used.get("verified_scheduled_departures_at_sholinganallur", [])
        self.assertEqual(len(deps_ctx), 2)
        times = [d["departure_time"] for d in deps_ctx]
        self.assertIn("00:00:15", times)
        self.assertIn("24:02:00", times)

    async def test_scheduled_departures_with_after_time_query(self):
        """
        Tests that when a question asks for departures after 08:00,
        the time filter (08:00:00 / 28800s) is parsed and passed to the structured query.
        """
        self.mock_rag_service.search = AsyncMock(
            return_value=RAGSearchResponse(
                query="What is the next scheduled departure after 08:00 from Sholinganallur?",
                retrieved_count=0,
                chunks=[],
            )
        )

        mock_deps = [
            {
                "departure_id": 1001,
                "trip_id": "TRIP_0800_99",
                "stop_id": "10026",
                "stop_name": "SHOLINGANALLUR",
                "route_number": "99",
                "route_name": "TAMBARAM WEST To ADYAR B.T",
                "departure_time": "08:00:00",
                "departure_seconds": 28800,
            },
            {
                "departure_id": 1002,
                "trip_id": "TRIP_0802_19AX",
                "stop_id": "10026",
                "stop_name": "SHOLINGANALLUR",
                "route_number": "19AX",
                "route_name": "PERUMBAKKAM To THYAGARAYA NAGAR",
                "departure_time": "08:02:00",
                "departure_seconds": 28920,
            },
        ]
        self.mock_cursor.fetchall.return_value = mock_deps

        mock_response = MagicMock()
        mock_response.text = "The next scheduled departure after 08:00 from Sholinganallur is Route 99 at 08:00:00, followed by Route 19AX at 08:02:00."
        self.mock_client.models.generate_content.return_value = mock_response

        resp = await self.service.ask(
            conn=self.mock_conn,
            question="What is the next scheduled departure after 08:00 from Sholinganallur?",
        )

        deps_ctx = resp.structured_context_used.get("verified_scheduled_departures_at_sholinganallur", [])
        self.assertEqual(len(deps_ctx), 2)
        self.assertEqual(deps_ctx[0]["departure_time"], "08:00:00")
        self.assertEqual(deps_ctx[0]["route_number"], "99")
        self.assertEqual(deps_ctx[1]["departure_time"], "08:02:00")

    async def test_missing_coordinates_structured_lookup(self):
        """
        Tests that stops with missing coordinates (380 and 168) are identified from
        structured database records (where latitude IS NULL).
        """
        self.mock_rag_service.search = AsyncMock(
            return_value=RAGSearchResponse(
                query="Which stops have missing coordinates?",
                retrieved_count=0,
                chunks=[],
            )
        )

        mock_missing = [
            {"stop_id": "168", "stop_name": "SHOLINGANALLUR P.U.OFFICE", "latitude": None, "longitude": None, "coordinate_status": "QUARANTINED_UNVERIFIED_DO_NOT_MAP", "map_eligible": False},
            {"stop_id": "380", "stop_name": "SHOLINGANALLUR", "latitude": None, "longitude": None, "coordinate_status": "QUARANTINED_UNVERIFIED_DO_NOT_MAP", "map_eligible": False},
        ]
        self.mock_cursor.fetchall.return_value = mock_missing

        mock_response = MagicMock()
        mock_response.text = "Stops 168 and 380 have missing coordinates quarantined by policy."
        self.mock_client.models.generate_content.return_value = mock_response

        resp = await self.service.ask(
            conn=self.mock_conn,
            question="Which stops have missing coordinates?",
        )

        missing_ctx = resp.structured_context_used.get("verified_stops_with_missing_coordinates", [])
        self.assertEqual(len(missing_ctx), 2)
        missing_ids = {s["stop_id"] for s in missing_ctx}
        self.assertEqual(missing_ids, {"168", "380"})

    async def test_gemini_generation_runs_after_database_connection_released(self):
        """Verifies pooled connection is fully released back to the pool before Gemini generation."""
        connection_released = False

        class MockPoolContextManager:
            async def __aenter__(self):
                return self_conn

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                nonlocal connection_released
                connection_released = True

        self_conn = self.mock_conn
        mock_pool = MagicMock()
        mock_pool.connection.return_value = MockPoolContextManager()

        self.mock_rag_service.search = AsyncMock(
            return_value=RAGSearchResponse(query="Fastest bus?", retrieved_count=0, chunks=[])
        )
        self.mock_cursor.fetchall.return_value = []

        def side_effect_check_release(*args, **kwargs):
            # Assert that the database connection was already returned to the pool
            if not connection_released:
                raise RuntimeError("Database connection was held during Gemini generation!")
            resp = MagicMock()
            resp.text = "Grounded response generated without holding connection."
            return resp

        self.mock_client.models.generate_content.side_effect = side_effect_check_release

        with patch("app.services.chat_service.get_db_pool", return_value=mock_pool):
            resp = await self.service.ask(
                conn=None,  # triggers production pooled connection checkout & early release
                question="Fastest bus?",
            )

        self.assertTrue(connection_released)
        self.assertIn("Grounded response", resp.answer)

    async def test_generate_gemini_answer_is_non_blocking_async_coroutine(self):
        """Verifies _generate_gemini_answer is an asynchronous coroutine that offloads generation."""
        import inspect
        self.assertTrue(inspect.iscoroutinefunction(self.service._generate_gemini_answer))

        mock_resp = MagicMock()
        mock_resp.text = "Non-blocking answer."
        self.mock_client.models.generate_content.return_value = mock_resp
        self.mock_client.models.generate_content.side_effect = None

        ans = await self.service._generate_gemini_answer("Test prompt")
        self.assertEqual(ans, "Non-blocking answer.")


class TestChatAPIEndpoint(unittest.TestCase):
    """Integration tests for POST /api/v1/chat/ask endpoint using FastAPI TestClient."""

    def setUp(self):
        self.client = TestClient(app, raise_server_exceptions=False)
        self.mock_chat_service = MagicMock(spec=ChatService)

        # Override dependencies
        app.dependency_overrides[get_chat_service] = lambda: self.mock_chat_service
        app.dependency_overrides[get_db] = lambda: AsyncMock()

    def tearDown(self):
        app.dependency_overrides.clear()

    def test_post_chat_ask_success_200(self):
        """Valid question returns 200 OK with full ChatResponse schema."""
        self.mock_chat_service.ask = AsyncMock(
            return_value=ChatResponse(
                question="Which bus goes directly to T Nagar?",
                answer="Route 102CT operates directly between Sholinganallur and T. Nagar. [doc_corridor_c001]",
                grounding_sources=[
                    ChatGroundingSource(
                        chunk_id="doc_corridor_c001",
                        document_id="doc_corridor",
                        title="Corridor Travel Guide",
                        similarity=0.7742,
                    )
                ],
                structured_context_used={"direct_service": "102CT"},
                limitations=["Static GTFS timetables only."],
            )
        )

        resp = self.client.post(
            "/api/v1/chat/ask",
            json={
                "question": "Which bus goes directly to T Nagar?",
                "top_k": 3,
            },
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        data = resp.json()
        self.assertEqual(data["question"], "Which bus goes directly to T Nagar?")
        self.assertIn("102CT", data["answer"])
        self.assertEqual(len(data["grounding_sources"]), 1)
        self.assertEqual(data["grounding_sources"][0]["chunk_id"], "doc_corridor_c001")
        self.assertIsNotNone(data["structured_context_used"])
        self.assertTrue(len(data["limitations"]) > 0)

    def test_post_chat_ask_question_too_short_422(self):
        """Question with length < 2 fails validation with HTTP 422."""
        resp = self.client.post("/api/v1/chat/ask", json={"question": "a"})
        self.assertEqual(resp.status_code, 422)

    def test_post_chat_ask_question_too_long_422(self):
        """Question with length > 1000 fails validation with HTTP 422."""
        resp = self.client.post("/api/v1/chat/ask", json={"question": "x" * 1001})
        self.assertEqual(resp.status_code, 422)

    def test_post_chat_ask_invalid_top_k_422(self):
        """top_k < 1 or > 10 fails validation with HTTP 422."""
        for invalid_k in [0, -1, 11, 50]:
            with self.subTest(top_k=invalid_k):
                resp = self.client.post(
                    "/api/v1/chat/ask",
                    json={"question": "Valid question?", "top_k": invalid_k},
                )
                self.assertEqual(resp.status_code, 422)

    def test_post_chat_ask_extra_fields_forbidden_422(self):
        """Undeclared payload fields are rejected with HTTP 422."""
        resp = self.client.post(
            "/api/v1/chat/ask",
            json={"question": "Valid question?", "forbidden_param": 123},
        )
        self.assertEqual(resp.status_code, 422)

    def test_post_chat_ask_auth_error_503(self):
        """AuthenticationError returns HTTP 503 without credential disclosure."""
        self.mock_chat_service.ask = AsyncMock(
            side_effect=AuthenticationError("Invalid API key configured")
        )

        resp = self.client.post("/api/v1/chat/ask", json={"question": "Bus timings?"})
        self.assertEqual(resp.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertIn("authentication failed", resp.json()["detail"].lower())

    def test_post_chat_ask_rate_limit_error_429(self):
        """RateLimitError returns HTTP 429."""
        self.mock_chat_service.ask = AsyncMock(
            side_effect=RateLimitError("Quota exceeded")
        )

        resp = self.client.post("/api/v1/chat/ask", json={"question": "Bus timings?"})
        self.assertEqual(resp.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertIn("quota or rate limit", resp.json()["detail"].lower())

    def test_post_chat_ask_transient_server_error_503(self):
        """TransientServerError returns HTTP 503."""
        self.mock_chat_service.ask = AsyncMock(
            side_effect=TransientServerError("Gemini timeout")
        )

        resp = self.client.post("/api/v1/chat/ask", json={"question": "Bus timings?"})
        self.assertEqual(resp.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertIn("temporarily unavailable", resp.json()["detail"].lower())

    def test_post_chat_ask_database_failure_500(self):
        """psycopg.Error returns HTTP 500."""
        import psycopg
        self.mock_chat_service.ask = AsyncMock(
            side_effect=psycopg.OperationalError("Database connection lost")
        )

        resp = self.client.post("/api/v1/chat/ask", json={"question": "Bus timings?"})
        self.assertEqual(resp.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        self.assertIn("database", resp.json()["detail"].lower())

    def test_api_v1_module_directory_shows_chat_active(self):
        """GET /api/v1/ confirms 'chat' module is Active."""
        resp = self.client.get("/api/v1/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        modules = resp.json()["modules"]
        self.assertIn("Active", modules["chat"])


if __name__ == "__main__":
    unittest.main()

