"""
Module: tests/test_api_transit.py
Purpose: Comprehensive unit and integration tests for Phase 5 Step 5.2 transit APIs:
         - GET /api/v1/stops (search, case-insensitive, null coords, map_eligible filter)
         - GET /api/v1/stops/{stop_id}/routes (routes serving stop, deduplication, 404 handling)
         - GET /api/v1/stops/{stop_id}/departures (time sorting, >24:00 syntax, route filter, 404, 422)
         - GET /api/v1/journey/direct (Sholinganallur -> T. Nagar, origin validation, empty results)
         - GTFS time parser validation
"""

import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException, status
from fastapi.testclient import TestClient

from app.db.pool import get_db
from app.main import app
from app.services.transit_service import TransitService, parse_gtfs_time_to_seconds


class TestGTFSTimeParser(unittest.TestCase):
    """Test GTFS service-day time parser with >24:00 support."""

    def test_parse_standard_time(self):
        """08:30:15 converts to 30615 seconds."""
        self.assertEqual(parse_gtfs_time_to_seconds("08:30:15"), 30615)

    def test_parse_midnight(self):
        """00:00:00 converts to 0 seconds."""
        self.assertEqual(parse_gtfs_time_to_seconds("00:00:00"), 0)

    def test_parse_two_part_time(self):
        """09:45 converts to 35100 seconds."""
        self.assertEqual(parse_gtfs_time_to_seconds("09:45"), 35100)

    def test_parse_post_midnight_gtfs_time(self):
        """25:17:30 strictly converts to 91050 seconds without modulo wrapping."""
        self.assertEqual(parse_gtfs_time_to_seconds("25:17:30"), 91050)
        self.assertEqual(parse_gtfs_time_to_seconds("24:00:00"), 86400)

    def test_parse_invalid_formats_raise_value_error(self):
        """Malformed time strings raise ValueError."""
        invalid_inputs = [
            "invalid",
            "12",
            "12:34:56:78",
            "08:65:00",  # minute >= 60
            "08:00:65",  # second >= 60
            "-01:00:00", # negative hour
            "ab:cd:ef",  # non-numeric
        ]
        for val in invalid_inputs:
            with self.subTest(val=val):
                with self.assertRaises(ValueError):
                    parse_gtfs_time_to_seconds(val)


class TestTransitServiceUnit(unittest.IsolatedAsyncioTestCase):
    """Unit tests for TransitService methods using mocked AsyncConnection."""

    def setUp(self):
        self.mock_conn = MagicMock()
        self.mock_cursor = AsyncMock()

        # Wire cursor context manager
        cm = AsyncMock()
        cm.__aenter__.return_value = self.mock_cursor
        cm.__aexit__.return_value = None
        self.mock_conn.cursor.return_value = cm

        self.service = TransitService(self.mock_conn)

    async def test_search_bus_stops_with_matching_name(self):
        """search_bus_stops returns matching stops and window count."""
        self.mock_cursor.fetchall.return_value = [
            {
                "stop_id": "10026",
                "stop_code": "10026",
                "stop_name": "Sholinganallur",
                "normalized_stop_name": "SHOLINGANALLUR",
                "latitude": 12.901037,
                "longitude": 80.227926,
                "coordinates_within_chennai_bounds": True,
                "map_eligible": True,
                "coordinate_status": "VERIFIED",
                "locality_area_membership_verified": False,
                "locality_area_membership_status": "LOCALITY_MEMBERSHIP_CONFIRMED",
                "coordinate_source": "GTFS_EXTRACT",
                "coordinate_verification_status": "VERIFIED_VALID",
                "created_at": None,
                "total_count": 1,
            }
        ]

        result = await self.service.search_bus_stops(query="Sholinganallur", limit=10)
        self.assertEqual(result.total, 1)
        self.assertEqual(len(result.stops), 1)
        self.assertEqual(result.stops[0].stop_name, "Sholinganallur")

        # Verify SQL execution parameterization
        call_args = self.mock_cursor.execute.call_args
        sql, params = call_args[0]
        self.assertIn("stop_name ILIKE %s", sql)
        self.assertEqual(params, ["%Sholinganallur%", "%Sholinganallur%", 10])

    async def test_search_bus_stops_preserves_null_coordinates(self):
        """search_bus_stops preserves null coordinates for unverified stops."""
        self.mock_cursor.fetchall.return_value = [
            {
                "stop_id": "380",
                "stop_code": "380",
                "stop_name": "SHOLINGANALLUR",
                "normalized_stop_name": "SHOLINGANALLUR",
                "latitude": None,
                "longitude": None,
                "coordinates_within_chennai_bounds": False,
                "map_eligible": False,
                "coordinate_status": "PLACEHOLDER",
                "locality_area_membership_verified": False,
                "locality_area_membership_status": "UNVERIFIED",
                "coordinate_source": "PLACEHOLDER",
                "coordinate_verification_status": "UNVERIFIED",
                "created_at": None,
                "total_count": 1,
            }
        ]

        result = await self.service.search_bus_stops(query="380", map_eligible_only=False)
        self.assertEqual(len(result.stops), 1)
        self.assertIsNone(result.stops[0].latitude)
        self.assertIsNone(result.stops[0].longitude)
        self.assertFalse(result.stops[0].map_eligible)

    async def test_search_bus_stops_map_eligible_filter(self):
        """search_bus_stops appends map_eligible condition when requested."""
        self.mock_cursor.fetchall.return_value = []
        await self.service.search_bus_stops(map_eligible_only=True, limit=20)

        sql, params = self.mock_cursor.execute.call_args[0]
        self.assertIn("map_eligible = TRUE", sql)
        self.assertIn("latitude IS NOT NULL", sql)
        self.assertEqual(params, [20])

    async def test_get_routes_for_stop_success(self):
        """get_routes_for_stop joins departures and route_services."""
        # 1. Stop existence check
        self.mock_cursor.fetchone.return_value = {
            "stop_id": "10026",
            "stop_name": "Sholinganallur",
        }
        # 2. Distinct routes
        self.mock_cursor.fetchall.return_value = [
            {
                "route_number": "102",
                "route_id": "102-D-MF-01",
                "route_name": "BROADWAY To KELAMBAKKAM B.T",
                "direction_id": 0,
                "earliest_departure": "05:00:00",
                "latest_departure": "22:15:00",
            },
            {
                "route_number": "19",
                "route_id": "19-D-MF-01",
                "route_name": "T.NAGAR To KELAMBAKKAM",
                "direction_id": 1,
                "earliest_departure": "06:00:00",
                "latest_departure": "21:30:00",
            },
        ]

        result = await self.service.get_routes_for_stop("10026")
        self.assertEqual(result.stop_id, "10026")
        self.assertEqual(result.total_routes, 2)
        self.assertEqual(result.routes[0].route_number, "102")
        self.assertEqual(result.routes[1].route_number, "19")

    async def test_get_routes_for_stop_unknown_stop_raises_404(self):
        """get_routes_for_stop raises HTTP 404 when stop does not exist."""
        self.mock_cursor.fetchone.return_value = None

        with self.assertRaises(HTTPException) as ctx:
            await self.service.get_routes_for_stop("UNKNOWN_999")
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertIn("not found", ctx.exception.detail)

    async def test_get_scheduled_departures_sorted_and_gtfs_post_midnight(self):
        """get_scheduled_departures correctly sorts and preserves >24:00 departure times."""
        self.mock_cursor.fetchone.return_value = {
            "stop_id": "10026",
            "stop_name": "Sholinganallur",
        }
        self.mock_cursor.fetchall.return_value = [
            {
                "departure_id": 101,
                "trip_id": "TRIP_001",
                "stop_sequence": 2,
                "route_number": "102",
                "route_name": "BROADWAY To KELAMBAKKAM B.T",
                "stop_id": "10026",
                "stop_name": "Sholinganallur",
                "direction_id": 0,
                "scheduled_departure": "24:15:00",
                "departure_seconds": 87300,
            }
        ]

        result = await self.service.get_scheduled_departures(
            stop_id="10026",
            after_time_str="24:00:00",
            route_number="102",
            limit=10,
        )
        self.assertEqual(result.count, 1)
        self.assertEqual(result.departures[0].scheduled_departure, "24:15:00")
        self.assertEqual(result.departures[0].departure_seconds, 87300)

        # Check parameter values
        sql, params = self.mock_cursor.execute.call_args[0]
        self.assertIn("departure_seconds >= %s", sql)
        self.assertIn("UPPER(route_number) = UPPER(%s)", sql)
        self.assertIn(86400, params)
        self.assertIn("102", params)

    async def test_get_scheduled_departures_unknown_stop_raises_404(self):
        """get_scheduled_departures raises 404 when stop does not exist."""
        self.mock_cursor.fetchone.return_value = None

        with self.assertRaises(HTTPException) as ctx:
            await self.service.get_scheduled_departures(stop_id="NON_EXISTENT")
        self.assertEqual(ctx.exception.status_code, 404)

    async def test_get_scheduled_departures_invalid_after_time_raises_422(self):
        """get_scheduled_departures raises 422 on invalid time string."""
        self.mock_cursor.fetchone.return_value = {"stop_id": "10026", "stop_name": "Sholinganallur"}

        with self.assertRaises(HTTPException) as ctx:
            await self.service.get_scheduled_departures(stop_id="10026", after_time_str="99:99:99")
        self.assertEqual(ctx.exception.status_code, 422)

    async def test_get_direct_journeys_all_origins(self):
        """get_direct_journeys returns direct runs and deserialized intermediate stops."""
        self.mock_cursor.fetchall.return_value = [
            {
                "trip_id": "TRIP_DIRECT_19",
                "route_number": "19",
                "route_name": "THIRUPORUR To T. NAGAR",
                "origin_stop_id": "qQJgvOFz",
                "origin_stop_name": "SHOLINGANALLUR KUMARAN NAGAR",
                "scheduled_departure": "08:15:00",
                "destination_stop_id": "bZroqIhX",
                "destination_stop_name": "CIT NAGAR",
                "scheduled_arrival": "09:12:00",
                "scheduled_departure_seconds": 29700,
                "scheduled_arrival_seconds": 33120,
                "duration_minutes": Decimal("57.00"),
                "intermediate_stop_count": 22,
                "stops_between": [{"stop_sequence": 1, "stop_name": "Karapakkam"}],
            }
        ]

        result = await self.service.get_direct_journeys(after_time_str="08:00:00", limit=5)
        self.assertEqual(result.total_options, 1)
        self.assertEqual(result.journeys[0].destination_stop_id, "bZroqIhX")
        self.assertEqual(result.journeys[0].duration_minutes, 57.0)
        self.assertEqual(len(result.journeys[0].stops_between), 1)

    async def test_get_direct_journeys_origin_not_found_raises_404(self):
        """get_direct_journeys raises 404 if specified origin_stop_id does not exist."""
        self.mock_cursor.fetchone.return_value = None

        with self.assertRaises(HTTPException) as ctx:
            await self.service.get_direct_journeys(origin_stop_id="INVALID_ORIGIN")
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertIn("not found", ctx.exception.detail)

    async def test_get_direct_journeys_whitespace_and_filter(self):
        """get_direct_journeys strips whitespace and filters scheduled_departure_seconds >= after_seconds."""
        self.mock_cursor.fetchall.return_value = []
        await self.service.get_direct_journeys(after_time_str="  08:00:00  ", limit=10)
        self.mock_cursor.execute.assert_called_once()
        call_sql, call_params = self.mock_cursor.execute.call_args[0]
        self.assertIn("scheduled_departure_seconds >= %s", call_sql)
        self.assertIn("ORDER BY scheduled_departure_seconds ASC", call_sql)
        self.assertIn("LIMIT %s", call_sql)
        self.assertIn(28800, call_params)

    async def test_get_direct_journeys_gtfs_time_exceeding_24(self):
        """get_direct_journeys correctly preserves GTFS times > 24:00 without modulo wrapping."""
        self.mock_cursor.fetchall.return_value = []
        await self.service.get_direct_journeys(after_time_str="25:15:00", limit=5)
        call_sql, call_params = self.mock_cursor.execute.call_args[0]
        self.assertIn(90900, call_params)

    async def test_get_direct_journeys_blank_after_time_raises_422(self):
        """get_direct_journeys raises 422 if after_time is purely whitespace."""
        with self.assertRaises(HTTPException) as ctx:
            await self.service.get_direct_journeys(after_time_str="   ")
        self.assertEqual(ctx.exception.status_code, 422)


class TestTransitAPIEndpoints(unittest.TestCase):
    """Integration tests for transit REST endpoints via FastAPI TestClient with dependency overrides."""

    def setUp(self):
        self.mock_conn = MagicMock()
        self.mock_cursor = AsyncMock()

        cm = AsyncMock()
        cm.__aenter__.return_value = self.mock_cursor
        cm.__aexit__.return_value = None
        self.mock_conn.cursor.return_value = cm

        # Override get_db dependency
        async def override_get_db():
            yield self.mock_conn

        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app, raise_server_exceptions=False)

    def tearDown(self):
        app.dependency_overrides.clear()

    def test_get_stops_search_200(self):
        """GET /api/v1/stops returns search results."""
        self.mock_cursor.fetchall.return_value = [
            {
                "stop_id": "10026",
                "stop_code": "10026",
                "stop_name": "Sholinganallur",
                "normalized_stop_name": "SHOLINGANALLUR",
                "latitude": 12.901037,
                "longitude": 80.227926,
                "coordinates_within_chennai_bounds": True,
                "map_eligible": True,
                "coordinate_status": "VERIFIED",
                "locality_area_membership_verified": False,
                "locality_area_membership_status": "CONFIRMED",
                "coordinate_source": "GTFS",
                "coordinate_verification_status": "VERIFIED",
                "created_at": None,
                "total_count": 1,
            }
        ]

        resp = self.client.get("/api/v1/stops?query=sholinganallur&limit=10")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["total"], 1)
        self.assertEqual(len(data["stops"]), 1)
        self.assertEqual(data["stops"][0]["stop_id"], "10026")

    def test_get_stops_invalid_limit_422(self):
        """GET /api/v1/stops with limit > 50 returns 422."""
        resp = self.client.get("/api/v1/stops?limit=100")
        self.assertEqual(resp.status_code, 422)

    def test_get_stop_routes_404(self):
        """GET /api/v1/stops/{stop_id}/routes returns 404 for unknown stop."""
        self.mock_cursor.fetchone.return_value = None

        resp = self.client.get("/api/v1/stops/UNKNOWN_STOP/routes")
        self.assertEqual(resp.status_code, 404)

    def test_get_stop_departures_200(self):
        """GET /api/v1/stops/{stop_id}/departures returns timetable departures."""
        self.mock_cursor.fetchone.return_value = {"stop_id": "10026", "stop_name": "Sholinganallur"}
        self.mock_cursor.fetchall.return_value = [
            {
                "departure_id": 1,
                "trip_id": "TRIP_001",
                "stop_sequence": 1,
                "route_number": "102",
                "route_name": "BROADWAY To KELAMBAKKAM B.T",
                "stop_id": "10026",
                "stop_name": "Sholinganallur",
                "direction_id": 0,
                "scheduled_departure": "08:30:00",
                "departure_seconds": 30600,
            }
        ]

        resp = self.client.get("/api/v1/stops/10026/departures?after_time=08:00:00&limit=5")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["departures"][0]["route_number"], "102")

    def test_get_stop_departures_invalid_after_time_422(self):
        """GET /api/v1/stops/{stop_id}/departures with bad time returns 422."""
        self.mock_cursor.fetchone.return_value = {"stop_id": "10026", "stop_name": "Sholinganallur"}

        resp = self.client.get("/api/v1/stops/10026/departures?after_time=bad-time")
        self.assertEqual(resp.status_code, 422)

    def test_get_direct_journeys_200(self):
        """GET /api/v1/journey/direct returns direct runs."""
        self.mock_cursor.fetchall.return_value = [
            {
                "trip_id": "TRIP_19",
                "route_number": "19",
                "route_name": "THIRUPORUR To T. NAGAR",
                "origin_stop_id": "qQJgvOFz",
                "origin_stop_name": "SHOLINGANALLUR KUMARAN NAGAR",
                "scheduled_departure": "08:15:00",
                "destination_stop_id": "bZroqIhX",
                "destination_stop_name": "CIT NAGAR",
                "scheduled_arrival": "09:12:00",
                "scheduled_departure_seconds": 29700,
                "scheduled_arrival_seconds": 33120,
                "duration_minutes": Decimal("57.0"),
                "intermediate_stop_count": 22,
                "stops_between": [],
            }
        ]

        resp = self.client.get("/api/v1/journey/direct")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["total_options"], 1)
        self.assertEqual(data["journeys"][0]["route_number"], "19")

    def test_get_direct_journeys_unknown_origin_404(self):
        """GET /api/v1/journey/direct with unknown origin returns 404."""
        self.mock_cursor.fetchone.return_value = None

        resp = self.client.get("/api/v1/journey/direct?origin_stop_id=UNKNOWN_ORIGIN")
        self.assertEqual(resp.status_code, 404)

    def test_get_direct_journeys_after_time_08_filter_200(self):
        """GET /api/v1/journey/direct?after_time=08:00:00&limit=10 verifies scheduled_departure_seconds >= 28800."""
        self.mock_cursor.fetchall.return_value = [
            {
                "trip_id": "TRIP_19_1",
                "route_number": "19",
                "route_name": "THIRUPORUR To T. NAGAR",
                "origin_stop_id": "qQJgvOFz",
                "origin_stop_name": "SHOLINGANALLUR KUMARAN NAGAR",
                "scheduled_departure": "08:04:15",
                "destination_stop_id": "bZroqIhX",
                "destination_stop_name": "CIT NAGAR",
                "scheduled_arrival": "09:01:00",
                "scheduled_departure_seconds": 29055,
                "scheduled_arrival_seconds": 32460,
                "duration_minutes": Decimal("56.75"),
                "intermediate_stop_count": 22,
                "stops_between": [],
            },
            {
                "trip_id": "TRIP_19A_1",
                "route_number": "19A",
                "route_name": "KELAMBAKKAM To T. NAGAR",
                "origin_stop_id": "qQJgvOFz",
                "origin_stop_name": "SHOLINGANALLUR KUMARAN NAGAR",
                "scheduled_departure": "08:14:45",
                "destination_stop_id": "bZroqIhX",
                "destination_stop_name": "CIT NAGAR",
                "scheduled_arrival": "09:12:00",
                "scheduled_departure_seconds": 29685,
                "scheduled_arrival_seconds": 33120,
                "duration_minutes": Decimal("57.25"),
                "intermediate_stop_count": 21,
                "stops_between": [],
            },
        ]

        resp = self.client.get("/api/v1/journey/direct?after_time=08:00:00&limit=10")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["total_options"], 2)

        # Verify SQL filter condition and parameters
        call_sql, call_params = self.mock_cursor.execute.call_args[0]
        self.assertIn("scheduled_departure_seconds >= %s", call_sql)
        self.assertIn(28800, call_params)
        self.assertIn(10, call_params)

        # Verify SQL order: WHERE condition precedes ORDER BY and LIMIT
        where_pos = call_sql.find("WHERE scheduled_departure_seconds >= %s")
        order_pos = call_sql.find("ORDER BY scheduled_departure_seconds ASC")
        limit_pos = call_sql.find("LIMIT %s")
        self.assertTrue(0 <= where_pos < order_pos < limit_pos)

        # Verify every returned departure has scheduled_departure_seconds >= 28800
        for journey in data["journeys"]:
            self.assertGreaterEqual(journey["scheduled_departure_seconds"], 28800)

    def test_get_direct_journeys_whitespace_after_time_200(self):
        """GET /api/v1/journey/direct with padded whitespace in after_time trims and returns 200."""
        self.mock_cursor.fetchall.return_value = []
        resp = self.client.get("/api/v1/journey/direct?after_time=%2008:00:00%20&limit=10")
        self.assertEqual(resp.status_code, 200)
        call_sql, call_params = self.mock_cursor.execute.call_args[0]
        self.assertIn("scheduled_departure_seconds >= %s", call_sql)
        self.assertIn(28800, call_params)

    def test_get_direct_journeys_invalid_after_time_422(self):
        """GET /api/v1/journey/direct with invalid time format returns HTTP 422."""
        resp = self.client.get("/api/v1/journey/direct?after_time=invalid-time")
        self.assertEqual(resp.status_code, 422)

    def test_get_direct_journeys_without_after_time_retains_behaviour(self):
        """GET /api/v1/journey/direct without after_time does not filter by departure time."""
        self.mock_cursor.fetchall.return_value = [
            {
                "trip_id": "TRIP_EARLY",
                "route_number": "19A",
                "route_name": "KELAMBAKKAM To T. NAGAR",
                "origin_stop_id": "qQJgvOFz",
                "origin_stop_name": "SHOLINGANALLUR KUMARAN NAGAR",
                "scheduled_departure": "04:34:45",
                "destination_stop_id": "bZroqIhX",
                "destination_stop_name": "CIT NAGAR",
                "scheduled_arrival": "05:31:00",
                "scheduled_departure_seconds": 16485,
                "scheduled_arrival_seconds": 19860,
                "duration_minutes": Decimal("56.25"),
                "intermediate_stop_count": 21,
                "stops_between": [],
            }
        ]

        resp = self.client.get("/api/v1/journey/direct?limit=10")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["total_options"], 1)
        self.assertEqual(data["journeys"][0]["scheduled_departure"], "04:34:45")

        # Verify SQL query does NOT contain after_time filter
        call_sql, call_params = self.mock_cursor.execute.call_args[0]
        self.assertNotIn("scheduled_departure_seconds >=", call_sql)
        self.assertEqual(call_params, [10])


if __name__ == "__main__":
    unittest.main()
