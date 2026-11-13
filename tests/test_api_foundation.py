"""
Module: tests/test_api_foundation.py
Purpose: Comprehensive test suite for Phase 5 Step 5.1 foundation components:
         - Async PostgreSQL connection pool initialization, checkout, and shutdown
         - FastAPI get_db dependency injection and unavailable pool handling
         - Pydantic schema validation for transit, RAG, and chat models
         - CORS middleware origin enforcement
         - Standardized error handling (422 validation, 500 unhandled)
         - System health and API v1 directory endpoints
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import Settings, settings
from app.db.pool import (
    close_db_pool,
    get_db,
    get_db_pool,
    init_db_pool,
    set_db_pool,
)
from app.main import app
from app.schemas.rag import (
    ChatGroundingSource,
    ChatRequest,
    ChatResponse,
    RAGChunkItem,
    RAGSearchRequest,
    RAGSearchResponse,
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


class TestConnectionPool(unittest.IsolatedAsyncioTestCase):
    """Test connection pool lifecycle, configuration, and exception safety."""

    async def asyncSetUp(self):
        self.original_pool = get_db_pool()
        set_db_pool(None)

    async def asyncTearDown(self):
        set_db_pool(self.original_pool)

    async def test_init_db_pool_missing_url_raises_without_credentials(self):
        """init_db_pool must fail cleanly when URL is empty or placeholder without leaking credentials."""
        with patch.object(settings, "DATABASE_URL", None):
            with self.assertRaises(RuntimeError) as ctx:
                await init_db_pool()
            self.assertIn("DATABASE_URL is not configured", str(ctx.exception))

        with patch.object(settings, "DATABASE_URL", "postgresql://user:your_password@localhost/db"):
            with self.assertRaises(RuntimeError) as ctx:
                await init_db_pool()
            self.assertIn("DATABASE_URL is not configured", str(ctx.exception))

    async def test_init_db_pool_open_failure_masks_credentials(self):
        """When AsyncConnectionPool.open() fails, exception must not leak credentials."""
        secret_url = "postgresql://myuser:supersecretpass@aws.supabase.com:5432/postgres"
        with patch("app.db.pool.AsyncConnectionPool") as mock_pool_cls:
            mock_instance = AsyncMock()
            mock_instance.open.side_effect = Exception("Network connection refused")
            mock_pool_cls.return_value = mock_instance

            with self.assertRaises(RuntimeError) as ctx:
                await init_db_pool(database_url=secret_url)

            # Ensure password was never exposed in exception message
            self.assertNotIn("supersecretpass", str(ctx.exception))
            self.assertIn("Failed to initialize PostgreSQL database connection pool", str(ctx.exception))
            self.assertIsNone(get_db_pool())

    async def test_init_and_close_db_pool_lifecycle(self):
        """Pool can be initialized, retrieved, and closed cleanly."""
        with patch("app.db.pool.AsyncConnectionPool") as mock_pool_cls:
            mock_instance = AsyncMock()
            mock_pool_cls.return_value = mock_instance

            pool = await init_db_pool(database_url="postgresql://user:pass@localhost/testdb")
            self.assertIsNotNone(pool)
            self.assertEqual(get_db_pool(), pool)
            mock_instance.open.assert_awaited_once()

            await close_db_pool()
            mock_instance.close.assert_awaited_once()
            self.assertIsNone(get_db_pool())

    async def test_get_db_dependency_when_pool_missing_raises_runtime_error(self):
        """get_db must raise RuntimeError if accessed before pool initialization."""
        set_db_pool(None)
        with self.assertRaises(RuntimeError) as ctx:
            async for _ in get_db():
                pass
        self.assertIn("Database connection pool is not initialized", str(ctx.exception))

    async def test_get_db_dependency_yields_connection(self):
        """get_db yields active connection from the pool and releases it."""
        mock_conn = AsyncMock()
        mock_pool = MagicMock()

        # Mock pool.connection() async context manager
        conn_cm = AsyncMock()
        conn_cm.__aenter__.return_value = mock_conn
        conn_cm.__aexit__.return_value = None
        mock_pool.connection.return_value = conn_cm

        set_db_pool(mock_pool)

        yielded_connections = []
        async for conn in get_db():
            yielded_connections.append(conn)

        self.assertEqual(len(yielded_connections), 1)
        self.assertEqual(yielded_connections[0], mock_conn)
        mock_pool.connection.assert_called_once()
        conn_cm.__aenter__.assert_awaited_once()
        conn_cm.__aexit__.assert_awaited_once()


class TestPydanticSchemas(unittest.TestCase):
    """Test schema validation, optional fields, and null coordinates."""

    def test_bus_stop_with_valid_coordinates(self):
        """BusStopResponse accepts valid float coordinates."""
        data = {
            "stop_id": "10026",
            "stop_code": "10026",
            "stop_name": "Sholinganallur",
            "normalized_stop_name": "SHOLINGANALLUR",
            "latitude": 12.901037,
            "longitude": 80.227926,
            "map_eligible": True,
            "coordinate_status": "VERIFIED",
        }
        stop = BusStopResponse(**data)
        self.assertEqual(stop.stop_id, "10026")
        self.assertEqual(stop.latitude, 12.901037)
        self.assertTrue(stop.map_eligible)

    def test_bus_stop_preserves_null_coordinates(self):
        """BusStopResponse preserves None for missing or unverified coordinates."""
        data = {
            "stop_id": "99999",
            "stop_code": "99999",
            "stop_name": "Unknown Stop",
            "normalized_stop_name": "UNKNOWN STOP",
            "latitude": None,
            "longitude": None,
            "map_eligible": False,
            "coordinate_status": "PLACEHOLDER",
        }
        stop = BusStopResponse(**data)
        self.assertIsNone(stop.latitude)
        self.assertIsNone(stop.longitude)
        self.assertFalse(stop.map_eligible)

    def test_bus_stop_search_response_envelope(self):
        """BusStopSearchResponse encapsulates multiple stops and total count."""
        resp = BusStopSearchResponse(
            total=1,
            query="Sholinganallur",
            stops=[
                BusStopResponse(
                    stop_id="10026",
                    stop_code="10026",
                    stop_name="Sholinganallur",
                    normalized_stop_name="SHOLINGANALLUR",
                    latitude=12.901037,
                    longitude=80.227926,
                    map_eligible=True,
                    coordinate_status="VERIFIED",
                )
            ],
        )
        self.assertEqual(resp.total, 1)
        self.assertEqual(len(resp.stops), 1)

    def test_route_service_and_stop_routes_response(self):
        """StopRoutesResponse correctly serializes route services."""
        resp = StopRoutesResponse(
            stop_id="10026",
            stop_name="Sholinganallur",
            total_routes=1,
            routes=[
                RouteServiceBrief(
                    route_number="102",
                    route_id="102-D-MF-01",
                    route_name="BROADWAY To KELAMBAKKAM B.T",
                    earliest_departure="05:00:00",
                    latest_departure="22:15:00",
                )
            ],
        )
        self.assertEqual(resp.total_routes, 1)
        self.assertEqual(resp.routes[0].route_number, "102")

    def test_scheduled_departures_response(self):
        """StopDeparturesResponse models time-ordered departures."""
        resp = StopDeparturesResponse(
            stop_id="10026",
            stop_name="Sholinganallur",
            query_time="08:30:00",
            count=1,
            departures=[
                ScheduledDepartureResponse(
                    departure_id=4012,
                    trip_id="TRIP_102_0835",
                    stop_sequence=5,
                    route_number="102",
                    route_name="BROADWAY To KELAMBAKKAM B.T",
                    stop_id="10026",
                    stop_name="Sholinganallur",
                    direction_id=0,
                    scheduled_departure="08:35:00",
                    departure_seconds=30900,
                    minutes_until_departure=5,
                )
            ],
        )
        self.assertEqual(resp.count, 1)
        self.assertEqual(resp.departures[0].departure_seconds, 30900)

    def test_direct_journey_response(self):
        """DirectJourneyResponse validates direct corridor runs."""
        resp = DirectJourneyResponse(
            corridor="Sholinganallur -> T. Nagar",
            total_options=1,
            journeys=[
                DirectConnectionItem(
                    trip_id="TRIP_DIRECT_19",
                    route_number="19",
                    route_name="T.NAGAR To KELAMBAKKAM",
                    origin_stop_id="10026",
                    origin_stop_name="Sholinganallur",
                    scheduled_departure="08:15:00",
                    destination_stop_id="bZroqIhX",
                    destination_stop_name="CIT NAGAR (T. Nagar)",
                    scheduled_arrival="09:12:00",
                    scheduled_departure_seconds=29700,
                    scheduled_arrival_seconds=33120,
                    duration_minutes=57.0,
                    intermediate_stop_count=22,
                )
            ],
        )
        self.assertEqual(resp.total_options, 1)
        self.assertEqual(resp.journeys[0].duration_minutes, 57.0)

    def test_rag_search_request_validation(self):
        """RAGSearchRequest validates query bounds and top_k limits."""
        # Valid
        req = RAGSearchRequest(query="Which bus to T. Nagar?", top_k=5)
        self.assertEqual(req.query, "Which bus to T. Nagar?")
        self.assertEqual(req.top_k, 5)

        # Empty query rejected
        with self.assertRaises(ValidationError):
            RAGSearchRequest(query="", top_k=5)

        # top_k > 20 rejected
        with self.assertRaises(ValidationError):
            RAGSearchRequest(query="valid query", top_k=25)

    def test_chat_request_validation(self):
        """ChatRequest validates question length constraints."""
        # Valid
        req = ChatRequest(question="Where is the bus stop?", top_k=3)
        self.assertEqual(req.question, "Where is the bus stop?")

        # Empty question rejected
        with self.assertRaises(ValidationError):
            ChatRequest(question="a", top_k=3)  # min_length=2


class TestFastAPIApplicationAndCORS(unittest.TestCase):
    """Test API endpoints, CORS headers, and error handling."""

    def setUp(self):
        self.client = TestClient(app, raise_server_exceptions=False)

    def test_root_endpoint(self):
        """GET / returns project info and docs link."""
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "online")
        self.assertEqual(data["docs"], "/docs")

    def test_health_check_endpoint(self):
        """GET /health returns healthy status."""
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "healthy")
        self.assertIn("environment", data)

    def test_api_v1_info_endpoint(self):
        """GET /api/v1/ returns API v1 status and module directory."""
        resp = self.client.get("/api/v1/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["api_version"], "v1")
        self.assertEqual(data["status"], "active")
        self.assertIn("stops", data["modules"])
        self.assertIn("chat", data["modules"])

    def test_cors_allowed_origin(self):
        """Allowed origin receives Access-Control-Allow-Origin header."""
        resp = self.client.get(
            "/",
            headers={"Origin": "http://localhost:3000"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.headers.get("access-control-allow-origin"),
            "http://localhost:3000",
        )

    def test_cors_allowed_frontend_vite_origin(self):
        """Frontend Vite dev server origin http://localhost:5173 receives CORS headers and successful OPTIONS preflight."""
        # Simple GET request
        resp = self.client.get(
            "/",
            headers={"Origin": "http://localhost:5173"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.headers.get("access-control-allow-origin"),
            "http://localhost:5173",
        )

        # Preflight OPTIONS request for /api/v1/chat/ask
        resp_options = self.client.options(
            "/api/v1/chat/ask",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        self.assertEqual(resp_options.status_code, 200)
        self.assertEqual(
            resp_options.headers.get("access-control-allow-origin"),
            "http://localhost:5173",
        )
        self.assertIn("POST", resp_options.headers.get("access-control-allow-methods", ""))

    def test_cors_disallowed_origin(self):
        """Disallowed origin does NOT receive Access-Control-Allow-Origin header."""
        resp = self.client.get(
            "/",
            headers={"Origin": "http://unauthorized-domain.com"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.headers.get("access-control-allow-origin"))

    def test_swagger_docs_and_openapi_json(self):
        """Swagger /docs and OpenAPI JSON spec are accessible."""
        resp = self.client.get("/docs")
        self.assertEqual(resp.status_code, 200)

        resp_openapi = self.client.get("/openapi.json")
        self.assertEqual(resp_openapi.status_code, 200)
        schema = resp_openapi.json()
        self.assertEqual(schema["info"]["title"], settings.PROJECT_NAME)

    def test_standardized_unhandled_exception_handler(self):
        """Unhandled 500 exceptions do not leak stack traces or credentials."""
        # Create a test route that triggers an unhandled exception
        @app.get("/test-internal-error-route")
        async def trigger_error():
            raise ValueError("Simulated unexpected internal error with SECRET_KEY=xyz123")

        resp = self.client.get("/test-internal-error-route")
        self.assertEqual(resp.status_code, 500)
        data = resp.json()
        self.assertEqual(data["error"], "Internal Server Error")
        self.assertEqual(data["message"], "An unexpected error occurred. Please try again later.")
        # Ensure error message did not expose internal exception text
        self.assertNotIn("SECRET_KEY", str(data))
        self.assertNotIn("Simulated unexpected", str(data))


if __name__ == "__main__":
    unittest.main()

