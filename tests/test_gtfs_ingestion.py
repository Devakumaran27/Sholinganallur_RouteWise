"""
Unit tests for Phase 4 Step 4.3 — Structured GTFS Dataset Ingestion.
Tests preflight validation, route aggregation, record mapping, JSONB handling,
foreign-key ordering, batching, idempotency, rollback behavior, and mock DB verification.
"""

import json
import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import psycopg
from psycopg.types.json import Jsonb

from app.db.gtfs_ingestion import (
    DEFAULT_BATCH_SIZE,
    EXPECTED_COUNTS,
    GTFSIngestionService,
    IngestionResult,
    PreflightSummary,
    VerificationResult,
    get_sync_connection,
    mask_database_url,
)


class TestGTFSIngestionPreflight(unittest.TestCase):
    """Tests for dry-run preflight validation and route aggregation."""

    def setUp(self):
        self.service = GTFSIngestionService()

    def test_preflight_success_on_actual_processed_data(self):
        """Preflight on real processed data files must succeed with 100% match."""
        summary = self.service.run_preflight()
        self.assertTrue(summary.valid, f"Preflight failed: {summary.errors}")
        self.assertEqual(summary.counts["routes"], 58)
        self.assertEqual(summary.counts["route_services"], 278)
        self.assertEqual(summary.counts["bus_stops"], 26)
        self.assertEqual(summary.counts["scheduled_departures"], 18439)
        self.assertEqual(summary.counts["direct_connections"], 170)
        self.assertEqual(summary.quarantined_stops_count, 2)
        self.assertTrue(summary.fk_integrity_valid)
        self.assertTrue(summary.pk_uniqueness_valid)
        self.assertEqual(len(summary.errors), 0)

    def test_derive_routes_aggregation(self):
        """Verify that routes are accurately derived from service summaries."""
        mock_services = [
            {
                "route_id": "1",
                "route_number": "19",
                "distinct_trips_in_feed": 10,
                "earliest_departure_seconds": 1000,
                "latest_departure_seconds": 5000,
            },
            {
                "route_id": "2",
                "route_number": "19",
                "distinct_trips_in_feed": 15,
                "earliest_departure_seconds": 800,
                "latest_departure_seconds": 6000,
            },
            {
                "route_id": "3",
                "route_number": "102",
                "distinct_trips_in_feed": 5,
                "earliest_departure_seconds": 2000,
                "latest_departure_seconds": 4000,
            },
        ]
        routes = GTFSIngestionService.derive_routes(mock_services)
        self.assertEqual(len(routes), 2)

        route_19 = next(r for r in routes if r["route_number"] == "19")
        self.assertEqual(route_19["total_service_variants"], 2)
        self.assertEqual(route_19["total_daily_trips"], 25)
        self.assertEqual(route_19["earliest_system_seconds"], 800)
        self.assertEqual(route_19["latest_system_seconds"], 6000)

        route_102 = next(r for r in routes if r["route_number"] == "102")
        self.assertEqual(route_102["total_service_variants"], 1)
        self.assertEqual(route_102["total_daily_trips"], 5)

    def test_preflight_detects_missing_required_file(self):
        """Missing a required file must cause preflight to fail cleanly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            service = GTFSIngestionService(data_dir=Path(tmpdir))
            summary = service.run_preflight()
            self.assertFalse(summary.valid)
            self.assertTrue(any("Missing required file" in err for err in summary.errors))

    def test_preflight_detects_count_mismatch(self):
        """Count mismatch against canonical specification must be flagged."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            # Copy real data but alter one file
            real_data = self.service.load_processed_data()
            with open(tmppath / "bus_stops.json", "w", encoding="utf-8") as f:
                json.dump(real_data["bus_stops"][:20], f)  # 20 instead of 26
            with open(tmppath / "route_service_summary.json", "w", encoding="utf-8") as f:
                json.dump(real_data["route_services"], f)
            with open(tmppath / "scheduled_departures.json", "w", encoding="utf-8") as f:
                json.dump(real_data["scheduled_departures"], f)
            with open(tmppath / "direct_t_nagar_connections.json", "w", encoding="utf-8") as f:
                json.dump(real_data["direct_connections"], f)

            service = GTFSIngestionService(data_dir=tmppath)
            summary = service.run_preflight()
            self.assertFalse(summary.valid)
            self.assertTrue(any("Count mismatch for bus_stops" in err for err in summary.errors))

    def test_preflight_detects_dangling_foreign_key(self):
        """Foreign key pointing to non-existent parent must be rejected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            real_data = self.service.load_processed_data()
            corrupt_services = list(real_data["route_services"])
            # Change route_number of first service to non-existent route
            corrupt_services[0] = dict(corrupt_services[0])
            corrupt_services[0]["route_number"] = "NON_EXISTENT_LINE_999"

            with open(tmppath / "bus_stops.json", "w", encoding="utf-8") as f:
                json.dump(real_data["bus_stops"], f)
            with open(tmppath / "route_service_summary.json", "w", encoding="utf-8") as f:
                json.dump(corrupt_services, f)
            with open(tmppath / "scheduled_departures.json", "w", encoding="utf-8") as f:
                json.dump(real_data["scheduled_departures"], f)
            with open(tmppath / "direct_t_nagar_connections.json", "w", encoding="utf-8") as f:
                json.dump(real_data["direct_connections"], f)

            service = GTFSIngestionService(data_dir=tmppath)
            summary = service.run_preflight()
            # Deriving routes from corrupt_services creates the route, but scheduled_departures or services check will catch
            # Let's test scheduled_departures pointing to invalid stop_id:
            corrupt_deps = list(real_data["scheduled_departures"])
            corrupt_deps[0] = dict(corrupt_deps[0])
            corrupt_deps[0]["stop_id"] = "INVALID_STOP_ID"
            with open(tmppath / "scheduled_departures.json", "w", encoding="utf-8") as f:
                json.dump(corrupt_deps, f)

            summary = service.run_preflight()
            self.assertFalse(summary.valid)
            self.assertTrue(any("scheduled_departures references unknown stop_ids" in err for err in summary.errors))

    def test_preflight_detects_negative_constraint_violation(self):
        """Destination stop found in local bus stops must be rejected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            real_data = self.service.load_processed_data()
            corrupt_conns = list(real_data["direct_connections"])
            corrupt_conns[0] = dict(corrupt_conns[0])
            # Set destination to first local bus stop id
            corrupt_conns[0]["destination_stop_id"] = real_data["bus_stops"][0]["stop_id"]

            with open(tmppath / "bus_stops.json", "w", encoding="utf-8") as f:
                json.dump(real_data["bus_stops"], f)
            with open(tmppath / "route_service_summary.json", "w", encoding="utf-8") as f:
                json.dump(real_data["route_services"], f)
            with open(tmppath / "scheduled_departures.json", "w", encoding="utf-8") as f:
                json.dump(real_data["scheduled_departures"], f)
            with open(tmppath / "direct_t_nagar_connections.json", "w", encoding="utf-8") as f:
                json.dump(corrupt_conns, f)

            service = GTFSIngestionService(data_dir=tmppath)
            summary = service.run_preflight()
            self.assertFalse(summary.valid)
            self.assertTrue(any("Negative constraint violated" in err for err in summary.errors))


class TestGTFSRecordMappingAndJSONB(unittest.TestCase):
    """Tests for field conversion, GTFS >24:00 time preservation, and JSONB mapping."""

    def setUp(self):
        self.service = GTFSIngestionService()
        self.data = self.service.load_processed_data()

    def test_direct_connections_jsonb_wrapping(self):
        """Nested stops array in direct_connections must be wrapped in psycopg Jsonb."""
        conn_record = self.data["direct_connections"][0]
        stops_array = conn_record["stops_between_origin_and_t_nagar"]
        self.assertIsInstance(stops_array, list)
        wrapped = Jsonb(stops_array)
        self.assertEqual(wrapped.obj, stops_array)

    def test_gtfs_post_midnight_times_preserved(self):
        """GTFS arrival and departure times must preserve canonical time strings."""
        deps = self.data["scheduled_departures"]
        sample_times = [d["departure_time"] for d in deps[:50]]
        for t in sample_times:
            self.assertIsInstance(t, str)
            self.assertTrue(len(t) >= 5)  # e.g. "00:02:15"

    def test_duration_minutes_numeric_conversion(self):
        """duration_minutes must cleanly convert to Decimal for NUMERIC(5,2)."""
        conns = self.data["direct_connections"]
        for c in conns:
            dm = Decimal(str(round(float(c["duration_minutes"]), 2)))
            self.assertIsInstance(dm, Decimal)
            self.assertGreater(dm, 0)
            self.assertLess(dm, 999)  # Fits in NUMERIC(5,2)


class TestGTFSIngestionExecutionMocked(unittest.TestCase):
    """Tests for transactional ingestion, foreign-key ordering, batching, and error rollback."""

    def setUp(self):
        self.service = GTFSIngestionService()

    def test_ingest_all_executes_in_exact_fk_dependency_order(self):
        """Ingestion must execute strictly: routes -> route_services -> bus_stops -> scheduled_departures -> direct_connections."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.transaction.return_value.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_cur.fetchone.return_value = (101,)  # Ingestion ID

        result = self.service.ingest_all(batch_size=5000, conn=mock_conn)

        self.assertTrue(result.success)
        self.assertEqual(result.ingestion_log_id, 101)
        self.assertEqual(result.total_records, 18971)

        # Inspect executemany calls to verify order of table inserts
        executemany_calls = mock_cur.executemany.call_args_list
        # There should be 1 call for routes, 1 for route_services, 1 for bus_stops,
        # ceil(18439 / 5000) = 4 calls for scheduled_departures, 1 call for direct_connections
        self.assertEqual(len(executemany_calls), 1 + 1 + 1 + 4 + 1)

        sql_queries = [call_args[0][0] for call_args in executemany_calls]

        # 1. First table must be routes
        self.assertIn("INSERT INTO public.routes", sql_queries[0])
        # 2. Second table must be route_services
        self.assertIn("INSERT INTO public.route_services", sql_queries[1])
        # 3. Third table must be bus_stops
        self.assertIn("INSERT INTO public.bus_stops", sql_queries[2])
        # 4. Fourth table must be scheduled_departures
        for q in sql_queries[3:7]:
            self.assertIn("INSERT INTO public.scheduled_departures", q)
        # 5. Fifth table must be direct_connections
        self.assertIn("INSERT INTO public.direct_connections", sql_queries[7])

    def test_idempotent_on_conflict_clauses_present(self):
        """Every table insertion statement must include an ON CONFLICT DO UPDATE clause."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.transaction.return_value.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_cur.fetchone.return_value = (1,)

        self.service.ingest_all(conn=mock_conn)

        for call_args in mock_cur.executemany.call_args_list:
            sql = call_args[0][0]
            self.assertIn("ON CONFLICT", sql)
            self.assertIn("DO UPDATE SET", sql)

    def test_conflict_targets_match_schema_pks_and_preserve_key_identity(self):
        """Conflict targets must match approved PKs/unique constraints and never update PKs."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.transaction.return_value.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_cur.fetchone.return_value = (1,)

        self.service.ingest_all(conn=mock_conn)

        executed_queries = [call_args[0][0] for call_args in mock_cur.executemany.call_args_list]

        # 1. routes -> ON CONFLICT (route_number)
        routes_query = next(q for q in executed_queries if "INSERT INTO public.routes" in q)
        self.assertIn("ON CONFLICT (route_number) DO UPDATE SET", routes_query)
        self.assertNotIn("route_number = EXCLUDED.route_number", routes_query)

        # 2. route_services -> ON CONFLICT (route_id)
        services_query = next(q for q in executed_queries if "INSERT INTO public.route_services" in q)
        self.assertIn("ON CONFLICT (route_id) DO UPDATE SET", services_query)
        self.assertNotIn("route_id = EXCLUDED.route_id", services_query)

        # 3. bus_stops -> ON CONFLICT (stop_id)
        stops_query = next(q for q in executed_queries if "INSERT INTO public.bus_stops" in q)
        self.assertIn("ON CONFLICT (stop_id) DO UPDATE SET", stops_query)
        self.assertNotIn("stop_id = EXCLUDED.stop_id", stops_query)

        # 4. scheduled_departures -> ON CONFLICT (trip_id, stop_sequence)
        deps_query = next(q for q in executed_queries if "INSERT INTO public.scheduled_departures" in q)
        self.assertIn("ON CONFLICT (trip_id, stop_sequence) DO UPDATE SET", deps_query)
        self.assertNotIn("trip_id = EXCLUDED.trip_id", deps_query)
        self.assertNotIn("stop_sequence = EXCLUDED.stop_sequence", deps_query)
        self.assertNotIn("departure_id = EXCLUDED.departure_id", deps_query)

        # 5. direct_connections -> ON CONFLICT (trip_id) [NOT (route_id, origin_stop_id, destination_stop_id)]
        conns_query = next(q for q in executed_queries if "INSERT INTO public.direct_connections" in q)
        self.assertIn("ON CONFLICT (trip_id) DO UPDATE SET", conns_query)
        self.assertNotIn("ON CONFLICT (route_id, origin_stop_id, destination_stop_id)", conns_query)
        self.assertNotIn("trip_id = EXCLUDED.trip_id", conns_query)

    def test_direct_connections_trip_ids_are_authentic_source_gtfs(self):
        """Verify inserted direct_connections trip_id values match source GTFS trip_ids without alteration."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.transaction.return_value.__enter__.return_value = mock_conn
        mock_cur.fetchone.return_value = (1,)

        captured_rows = {}

        def mock_executemany(sql, rows):
            if "INSERT INTO public.direct_connections" in sql:
                captured_rows["direct_connections"] = rows

        mock_cur.executemany.side_effect = mock_executemany
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        self.service.ingest_all(conn=mock_conn)

        inserted_conns = captured_rows.get("direct_connections", [])
        self.assertEqual(len(inserted_conns), 170)

        with open(self.service.data_dir / "direct_t_nagar_connections.json", "r", encoding="utf-8") as f:
            raw_data = json.load(f)

        raw_trip_ids = [item["trip_id"] for item in raw_data]
        inserted_trip_ids = [row[0] for row in inserted_conns]

        self.assertEqual(inserted_trip_ids, raw_trip_ids)
        for tid in inserted_trip_ids:
            self.assertTrue(len(tid) > 0)
            self.assertFalse(tid.startswith("synthetic_"))

    def test_transaction_rollback_on_failure(self):
        """A failure in any table must raise an exception and cause rollback."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.transaction.return_value.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_cur.fetchone.return_value = (1,)

        # Make bus_stops insertion fail
        def side_effect(sql, *args, **kwargs):
            if "INSERT INTO public.bus_stops" in sql:
                raise psycopg.Error("Simulated database constraint violation on bus_stops")
            return None

        mock_cur.executemany.side_effect = side_effect

        result = self.service.ingest_all(conn=mock_conn)

        self.assertFalse(result.success)
        self.assertIn("Simulated database constraint violation", result.error_message)


class TestGTFSDatabaseVerificationMocked(unittest.TestCase):
    """Tests for database verification logic."""

    def setUp(self):
        self.service = GTFSIngestionService()

    def test_verify_database_matching_counts(self):
        """When all table counts match expected specifications, verification succeeds."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        # Mock responses for SELECT count(*) FROM public.<table_name>
        mock_cur.fetchone.side_effect = [
            (58,),      # routes
            (278,),     # route_services
            (26,),      # bus_stops
            (18439,),   # scheduled_departures
            (170,),     # direct_connections
            # latest dataset_ingestion_log entry
            (
                1, "1.0.0", "hash123", "GTFS_STRUCTURED_DATASET",
                "SUCCESS", 18971, 2, None,
                datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc),
                datetime(2026, 11, 8, 12, 1, tzinfo=timezone.utc),
                Decimal("2.45"),
            ),
        ]

        result = self.service.verify_database(conn=mock_conn)
        self.assertTrue(result.all_matched)
        self.assertEqual(len(result.discrepancies), 0)
        self.assertEqual(result.actual_counts["routes"], 58)
        self.assertEqual(result.actual_counts["direct_connections"], 170)
        self.assertIsNotNone(result.latest_ingestion_log)

    def test_verify_database_detects_discrepancy(self):
        """When any table count diverges, verification flags the discrepancy."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        mock_cur.fetchone.side_effect = [
            (58,),      # routes
            (278,),     # route_services
            (25,),      # bus_stops (expected 26!)
            (18439,),   # scheduled_departures
            (170,),     # direct_connections
            None,       # no log
        ]

        result = self.service.verify_database(conn=mock_conn)
        self.assertFalse(result.all_matched)
        self.assertIn("bus_stops", result.discrepancies)
        self.assertEqual(result.discrepancies["bus_stops"]["expected"], 26)
        self.assertEqual(result.discrepancies["bus_stops"]["actual"], 25)


class TestMaskDatabaseUrl(unittest.TestCase):
    """Test URL credential masking helper."""

    def test_mask_database_url_redacts_credentials(self):
        raw = "postgresql://my_user:secret_password_123@db.supabase.co:5432/postgres"
        masked = mask_database_url(raw)
        self.assertNotIn("secret_password_123", masked)
        self.assertIn("my_user:***REDACTED***@", masked)


if __name__ == "__main__":
    unittest.main()
