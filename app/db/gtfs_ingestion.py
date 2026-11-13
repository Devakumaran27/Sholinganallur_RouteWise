"""
Module: app.db.gtfs_ingestion
Purpose: Phase 4 Step 4.3 — Structured GTFS Dataset Ingestion Service for RouteWise.
         Provides dry-run validation, referential integrity verification,
         parameterized batch ingestion, safe idempotency, and database status verification.
"""

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import psycopg
from psycopg.types.json import Jsonb

from app.core.config import settings

logger = logging.getLogger("gtfs_ingestion")

# Canonical expected counts per specifications
EXPECTED_COUNTS = {
    "routes": 58,
    "route_services": 278,
    "bus_stops": 26,
    "scheduled_departures": 18439,
    "direct_connections": 170,
}

EXPECTED_QUARANTINED_STOPS = 2
DEFAULT_BATCH_SIZE = 2000
DATASET_STAGE = "GTFS_STRUCTURED_DATASET"
DEFAULT_DATASET_VERSION = "1.0.0"


def mask_database_url(url: Optional[str]) -> str:
    """Mask credentials in database URL for safe logging and reporting."""
    if not url:
        return "None"
    import re
    return re.sub(r"://([^:]+):([^@]+)@", r"://\1:***REDACTED***@", url)


def get_sync_connection(database_url: Optional[str] = None) -> psycopg.Connection:
    """
    Establish a synchronous PostgreSQL connection using psycopg 3.

    Args:
        database_url: Optional override connection string.

    Returns:
        psycopg.Connection: Active database connection.
    """
    url = database_url or settings.DATABASE_URL
    if not url or "your_password" in url or "your_project_ref" in url:
        raise ValueError(
            "DATABASE_URL is not properly configured. "
            "Please provide a valid Supabase PostgreSQL connection string."
        )
    try:
        return psycopg.connect(url)
    except Exception as exc:
        logger.error(f"Failed to connect to PostgreSQL database: {exc}")
        raise


@dataclass
class PreflightSummary:
    """Results of preflight dry-run validation."""
    valid: bool
    data_dir: str
    file_hashes: Dict[str, str]
    composite_hash: str
    counts: Dict[str, int]
    expected_counts: Dict[str, int]
    fk_integrity_valid: bool
    pk_uniqueness_valid: bool
    quarantined_stops_count: int
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


@dataclass
class IngestionResult:
    """Results of dataset ingestion."""
    success: bool
    stage: str
    dataset_version: str
    composite_hash: str
    counts_ingested: Dict[str, int]
    total_records: int
    quarantined_count: int
    started_at: datetime
    completed_at: Optional[datetime]
    duration_seconds: float
    ingestion_log_id: Optional[int]
    error_message: Optional[str] = None


@dataclass
class VerificationResult:
    """Results of database count verification against expected values."""
    all_matched: bool
    actual_counts: Dict[str, int]
    expected_counts: Dict[str, int]
    discrepancies: Dict[str, Dict[str, int]]
    latest_ingestion_log: Optional[Dict[str, Any]] = None


class GTFSIngestionService:
    """
    Structured GTFS dataset ingestion service.
    Orchestrates preflight checks, dependency-ordered batch insertion,
    idempotent upserts, audit logging, and database state verification.
    """

    def __init__(self, data_dir: Optional[Path] = None, db_url: Optional[str] = None):
        self.project_root = Path(__file__).resolve().parents[2]
        self.data_dir = data_dir or (self.project_root / "data" / "processed")
        self.db_url = db_url

    # --------------------------------------------------------------------------
    # File Resolution and Loading
    # --------------------------------------------------------------------------
    def _resolve_data_files(self) -> Dict[str, Path]:
        """Resolve expected processed JSON files, supporting direct_connections aliases."""
        bus_stops_path = self.data_dir / "bus_stops.json"
        route_summary_path = self.data_dir / "route_service_summary.json"
        scheduled_deps_path = self.data_dir / "scheduled_departures.json"
        
        # Support either direct_t_nagar_connections.json or direct_connections.json
        direct_conns_path = self.data_dir / "direct_t_nagar_connections.json"
        if not direct_conns_path.exists():
            direct_conns_path = self.data_dir / "direct_connections.json"

        quarantined_path = self.data_dir / "quarantined_records.json"
        metadata_path = self.data_dir / "dataset_metadata.json"

        return {
            "bus_stops": bus_stops_path,
            "route_service_summary": route_summary_path,
            "scheduled_departures": scheduled_deps_path,
            "direct_connections": direct_conns_path,
            "quarantined_records": quarantined_path,
            "dataset_metadata": metadata_path,
        }

    def compute_file_hash(self, path: Path) -> str:
        """Compute SHA-256 hash of a file."""
        sha = hashlib.sha256()
        with open(path, "rb") as f:
            while chunk := f.read(65536):
                sha.update(chunk)
        return sha.hexdigest()

    def load_processed_data(self) -> Dict[str, Any]:
        """
        Load all source processed JSON records from disk.
        
        Returns:
            Dict containing raw datasets:
                - bus_stops: list[dict]
                - route_services: list[dict]
                - routes: list[dict] (derived)
                - scheduled_departures: list[dict]
                - direct_connections: list[dict]
                - quarantined: list[dict]
                - metadata: dict
        """
        files = self._resolve_data_files()
        missing = [name for name, p in files.items() if not p.exists() and name != "quarantined_records" and name != "dataset_metadata"]
        if missing:
            raise FileNotFoundError(f"Missing required processed data files: {missing} in {self.data_dir}")

        with open(files["bus_stops"], "r", encoding="utf-8") as f:
            bus_stops = json.load(f)

        with open(files["route_service_summary"], "r", encoding="utf-8") as f:
            route_services = json.load(f)

        with open(files["scheduled_departures"], "r", encoding="utf-8") as f:
            scheduled_departures = json.load(f)

        with open(files["direct_connections"], "r", encoding="utf-8") as f:
            direct_connections = json.load(f)

        quarantined = []
        if files["quarantined_records"].exists():
            with open(files["quarantined_records"], "r", encoding="utf-8") as f:
                quarantined = json.load(f)

        metadata = {}
        if files["dataset_metadata"].exists():
            with open(files["dataset_metadata"], "r", encoding="utf-8") as f:
                metadata = json.load(f)

        # Derive 58 canonical routes from route_services
        routes = self.derive_routes(route_services)

        return {
            "bus_stops": bus_stops,
            "route_services": route_services,
            "routes": routes,
            "scheduled_departures": scheduled_departures,
            "direct_connections": direct_connections,
            "quarantined": quarantined,
            "metadata": metadata,
            "files": files,
        }

    # --------------------------------------------------------------------------
    # Route Derivation
    # --------------------------------------------------------------------------
    @staticmethod
    def derive_routes(route_services: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Aggregate 278 operational service variants into 58 canonical public route lines.

        Computes:
            - route_number: Public bus line identity
            - total_service_variants: Count of distinct route_id variants
            - total_daily_trips: Sum of distinct_trips_in_feed
            - earliest_system_seconds: Earliest departure seconds across variants
            - latest_system_seconds: Latest departure seconds across variants
        """
        aggregated: Dict[str, Dict[str, Any]] = {}
        for s in route_services:
            rn = str(s["route_number"]).strip()
            if rn not in aggregated:
                aggregated[rn] = {
                    "route_number": rn,
                    "total_service_variants": 0,
                    "total_daily_trips": 0,
                    "earliest_system_seconds": s["earliest_departure_seconds"],
                    "latest_system_seconds": s["latest_departure_seconds"],
                }
            agg = aggregated[rn]
            agg["total_service_variants"] += 1
            agg["total_daily_trips"] += int(s.get("distinct_trips_in_feed", 0))
            agg["earliest_system_seconds"] = min(agg["earliest_system_seconds"], s["earliest_departure_seconds"])
            agg["latest_system_seconds"] = max(agg["latest_system_seconds"], s["latest_departure_seconds"])

        # Return sorted by route_number for deterministic insertion order
        return sorted(aggregated.values(), key=lambda r: r["route_number"])

    # --------------------------------------------------------------------------
    # Preflight / Dry-run Validation
    # --------------------------------------------------------------------------
    def run_preflight(self) -> PreflightSummary:
        """
        Perform complete dry-run validation without writing to PostgreSQL:
            1. File existence and SHA-256 hash calculation.
            2. Record counts verification against expected counts.
            3. Primary key uniqueness validation.
            4. In-memory foreign-key referential integrity verification.
            5. Time string and negative constraint validation.
        """
        errors: List[str] = []
        warnings: List[str] = []
        file_hashes: Dict[str, str] = {}
        composite_sha = hashlib.sha256()

        files = self._resolve_data_files()
        for name, p in files.items():
            if p.exists():
                h = self.compute_file_hash(p)
                file_hashes[name] = h
                composite_sha.update(h.encode("utf-8"))
            else:
                if name not in ("quarantined_records", "dataset_metadata"):
                    errors.append(f"Missing required file: {p.name}")

        if errors:
            return PreflightSummary(
                valid=False,
                data_dir=str(self.data_dir),
                file_hashes=file_hashes,
                composite_hash=composite_sha.hexdigest(),
                counts={},
                expected_counts=EXPECTED_COUNTS,
                fk_integrity_valid=False,
                pk_uniqueness_valid=False,
                quarantined_stops_count=0,
                errors=errors,
            )

        data = self.load_processed_data()
        routes = data["routes"]
        route_services = data["route_services"]
        bus_stops = data["bus_stops"]
        scheduled_departures = data["scheduled_departures"]
        direct_connections = data["direct_connections"]
        quarantined = data["quarantined"]

        counts = {
            "routes": len(routes),
            "route_services": len(route_services),
            "bus_stops": len(bus_stops),
            "scheduled_departures": len(scheduled_departures),
            "direct_connections": len(direct_connections),
        }

        # 1. Count checks
        for entity, expected in EXPECTED_COUNTS.items():
            actual = counts.get(entity, 0)
            if actual != expected:
                errors.append(f"Count mismatch for {entity}: expected {expected}, found {actual}")

        quarantined_count = len(quarantined)
        if quarantined_count != EXPECTED_QUARANTINED_STOPS:
            warnings.append(
                f"Quarantined stops count ({quarantined_count}) differs from expected ({EXPECTED_QUARANTINED_STOPS})"
            )

        # 2. Primary key uniqueness checks
        pk_valid = True
        route_numbers = [r["route_number"] for r in routes]
        if len(route_numbers) != len(set(route_numbers)):
            errors.append("Duplicate primary key in routes")
            pk_valid = False

        service_ids = [s["route_id"] for s in route_services]
        if len(service_ids) != len(set(service_ids)):
            errors.append("Duplicate primary key route_id in route_services")
            pk_valid = False

        stop_ids = [s["stop_id"] for s in bus_stops]
        if len(stop_ids) != len(set(stop_ids)):
            errors.append("Duplicate primary key stop_id in bus_stops")
            pk_valid = False

        dep_keys = [(d["trip_id"], d["stop_sequence"]) for d in scheduled_departures]
        if len(dep_keys) != len(set(dep_keys)):
            errors.append("Duplicate unique constraint (trip_id, stop_sequence) in scheduled_departures")
            pk_valid = False

        conn_trip_ids = [c["trip_id"] for c in direct_connections]
        if len(conn_trip_ids) != len(set(conn_trip_ids)):
            errors.append("Duplicate primary key trip_id in direct_connections")
            pk_valid = False

        # 3. Foreign key referential integrity checks
        fk_valid = True
        known_routes: Set[str] = set(route_numbers)
        known_services: Set[str] = set(service_ids)
        known_stops: Set[str] = set(stop_ids)

        # FK 1: route_services.route_number -> routes.route_number
        missing_rn = set(s["route_number"] for s in route_services if s["route_number"] not in known_routes)
        if missing_rn:
            errors.append(f"route_services references unknown route_numbers: {missing_rn}")
            fk_valid = False

        # FK 2: scheduled_departures.stop_id -> bus_stops.stop_id
        missing_stops_in_deps = set(d["stop_id"] for d in scheduled_departures if d["stop_id"] not in known_stops)
        if missing_stops_in_deps:
            errors.append(f"scheduled_departures references unknown stop_ids: {missing_stops_in_deps}")
            fk_valid = False

        # FK 3: scheduled_departures.route_id -> route_services.route_id
        missing_routes_in_deps = set(d["route_id"] for d in scheduled_departures if d["route_id"] not in known_services)
        if missing_routes_in_deps:
            errors.append(f"scheduled_departures references unknown route_ids: {missing_routes_in_deps}")
            fk_valid = False

        # FK 4: direct_connections.route_id -> route_services.route_id
        missing_routes_in_conns = set(c["route_id"] for c in direct_connections if c["route_id"] not in known_services)
        if missing_routes_in_conns:
            errors.append(f"direct_connections references unknown route_ids: {missing_routes_in_conns}")
            fk_valid = False

        # FK 5: direct_connections.origin_stop_id -> bus_stops.stop_id
        missing_origins_in_conns = set(c["origin_stop_id"] for c in direct_connections if c["origin_stop_id"] not in known_stops)
        if missing_origins_in_conns:
            errors.append(f"direct_connections references unknown origin_stop_ids: {missing_origins_in_conns}")
            fk_valid = False

        # Negative constraint check: destination_stop_id must NOT be in local bus_stops
        dest_in_local = set(c["destination_stop_id"] for c in direct_connections if c["destination_stop_id"] in known_stops)
        if dest_in_local:
            errors.append(f"Negative constraint violated: destination_stop_id found in local bus_stops: {dest_in_local}")
            fk_valid = False

        # 4. JSON field check
        for c in direct_connections:
            if not isinstance(c.get("stops_between_origin_and_t_nagar"), list):
                errors.append(f"stops_between_origin_and_t_nagar in trip {c.get('trip_id')} is not a list")
                break

        composite_hash = composite_sha.hexdigest()
        is_valid = len(errors) == 0

        return PreflightSummary(
            valid=is_valid,
            data_dir=str(self.data_dir),
            file_hashes=file_hashes,
            composite_hash=composite_hash,
            counts=counts,
            expected_counts=EXPECTED_COUNTS,
            fk_integrity_valid=fk_valid,
            pk_uniqueness_valid=pk_valid,
            quarantined_stops_count=quarantined_count,
            errors=errors,
            warnings=warnings,
        )

    # --------------------------------------------------------------------------
    # Database Ingestion Execution
    # --------------------------------------------------------------------------
    def ingest_all(
        self,
        batch_size: int = DEFAULT_BATCH_SIZE,
        conn: Optional[psycopg.Connection] = None,
        dataset_version: str = DEFAULT_DATASET_VERSION,
    ) -> IngestionResult:
        """
        Execute atomic database ingestion in strict foreign-key dependency order:
            1. routes (58 rows)
            2. route_services (278 rows)
            3. bus_stops (26 rows)
            4. scheduled_departures (18,439 rows, batched)
            5. direct_connections (170 rows)

        Records execution in dataset_ingestion_log.
        Guarantees that a failure in any table rolls back the entire ingestion transaction.
        """
        # Run preflight verification first
        preflight = self.run_preflight()
        if not preflight.valid:
            error_msg = f"Preflight validation failed: {'; '.join(preflight.errors)}"
            logger.error(error_msg)
            return IngestionResult(
                success=False,
                stage=DATASET_STAGE,
                dataset_version=dataset_version,
                composite_hash=preflight.composite_hash,
                counts_ingested={},
                total_records=0,
                quarantined_count=preflight.quarantined_stops_count,
                started_at=datetime.now(timezone.utc),
                completed_at=None,
                duration_seconds=0.0,
                ingestion_log_id=None,
                error_message=error_msg,
            )

        data = self.load_processed_data()
        routes = data["routes"]
        route_services = data["route_services"]
        bus_stops = data["bus_stops"]
        scheduled_departures = data["scheduled_departures"]
        direct_connections = data["direct_connections"]

        start_time = time.time()
        started_at = datetime.now(timezone.utc)
        counts_ingested: Dict[str, int] = {}
        ingestion_log_id: Optional[int] = None
        should_close_conn = False

        if conn is None:
            conn = get_sync_connection(self.db_url)
            should_close_conn = True

        try:
            # Enclose entire ingestion in an atomic transaction
            with conn.transaction():
                with conn.cursor() as cur:
                    # 0. Insert initial IN_PROGRESS audit log
                    cur.execute(
                        """
                        INSERT INTO public.dataset_ingestion_log (
                            dataset_version, dataset_file_hash, ingestion_stage,
                            status, total_records_ingested, quarantined_records_count,
                            started_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                        RETURNING ingestion_id;
                        """,
                        (
                            dataset_version,
                            preflight.composite_hash,
                            DATASET_STAGE,
                            "IN_PROGRESS",
                            0,
                            preflight.quarantined_stops_count,
                            started_at,
                        ),
                    )
                    row = cur.fetchone()
                    if row:
                        ingestion_log_id = row[0]

                    # 1. Ingest routes (58 rows)
                    route_rows = [
                        (
                            r["route_number"],
                            r["total_service_variants"],
                            r["total_daily_trips"],
                            r["earliest_system_seconds"],
                            r["latest_system_seconds"],
                        )
                        for r in routes
                    ]
                    cur.executemany(
                        """
                        INSERT INTO public.routes (
                            route_number, total_service_variants, total_daily_trips,
                            earliest_system_seconds, latest_system_seconds
                        ) VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (route_number) DO UPDATE SET
                            total_service_variants = EXCLUDED.total_service_variants,
                            total_daily_trips = EXCLUDED.total_daily_trips,
                            earliest_system_seconds = EXCLUDED.earliest_system_seconds,
                            latest_system_seconds = EXCLUDED.latest_system_seconds;
                        """,
                        route_rows,
                    )
                    counts_ingested["routes"] = len(route_rows)
                    logger.info(f"Ingested {len(route_rows)} routes.")

                    # 2. Ingest route_services (278 rows)
                    service_rows = [
                        (
                            s["route_id"],
                            s["route_number"],
                            s["route_name"],
                            s["distinct_trips_in_feed"],
                            s["scheduled_stop_departures_in_area"],
                            s["earliest_departure_time_string"],
                            s["latest_departure_time_string"],
                            s["earliest_departure_seconds"],
                            s["latest_departure_seconds"],
                        )
                        for s in route_services
                    ]
                    cur.executemany(
                        """
                        INSERT INTO public.route_services (
                            route_id, route_number, route_name, distinct_trips_in_feed,
                            scheduled_stop_departures_in_area, earliest_departure_time_string,
                            latest_departure_time_string, earliest_departure_seconds,
                            latest_departure_seconds
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (route_id) DO UPDATE SET
                            route_number = EXCLUDED.route_number,
                            route_name = EXCLUDED.route_name,
                            distinct_trips_in_feed = EXCLUDED.distinct_trips_in_feed,
                            scheduled_stop_departures_in_area = EXCLUDED.scheduled_stop_departures_in_area,
                            earliest_departure_time_string = EXCLUDED.earliest_departure_time_string,
                            latest_departure_time_string = EXCLUDED.latest_departure_time_string,
                            earliest_departure_seconds = EXCLUDED.earliest_departure_seconds,
                            latest_departure_seconds = EXCLUDED.latest_departure_seconds;
                        """,
                        service_rows,
                    )
                    counts_ingested["route_services"] = len(service_rows)
                    logger.info(f"Ingested {len(service_rows)} route_services.")

                    # 3. Ingest bus_stops (26 rows)
                    stop_rows = [
                        (
                            s["stop_id"],
                            s["stop_code"],
                            s["stop_name"],
                            s["normalized_stop_name"],
                            s["latitude"],
                            s["longitude"],
                            s["coordinates_within_chennai_bounds"],
                            s["map_eligible"],
                            s["coordinate_status"],
                            s["locality_area_membership_verified"],
                            s["locality_area_membership_status"],
                            s["coordinate_source"],
                            s["coordinate_verification_status"],
                            s["original_latitude"],
                            s["original_longitude"],
                        )
                        for s in bus_stops
                    ]
                    cur.executemany(
                        """
                        INSERT INTO public.bus_stops (
                            stop_id, stop_code, stop_name, normalized_stop_name,
                            latitude, longitude, coordinates_within_chennai_bounds,
                            map_eligible, coordinate_status, locality_area_membership_verified,
                            locality_area_membership_status, coordinate_source,
                            coordinate_verification_status, original_latitude, original_longitude
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (stop_id) DO UPDATE SET
                            stop_code = EXCLUDED.stop_code,
                            stop_name = EXCLUDED.stop_name,
                            normalized_stop_name = EXCLUDED.normalized_stop_name,
                            latitude = EXCLUDED.latitude,
                            longitude = EXCLUDED.longitude,
                            coordinates_within_chennai_bounds = EXCLUDED.coordinates_within_chennai_bounds,
                            map_eligible = EXCLUDED.map_eligible,
                            coordinate_status = EXCLUDED.coordinate_status,
                            locality_area_membership_verified = EXCLUDED.locality_area_membership_verified,
                            locality_area_membership_status = EXCLUDED.locality_area_membership_status,
                            coordinate_source = EXCLUDED.coordinate_source,
                            coordinate_verification_status = EXCLUDED.coordinate_verification_status,
                            original_latitude = EXCLUDED.original_latitude,
                            original_longitude = EXCLUDED.original_longitude;
                        """,
                        stop_rows,
                    )
                    counts_ingested["bus_stops"] = len(stop_rows)
                    logger.info(f"Ingested {len(stop_rows)} bus_stops.")

                    # 4. Ingest scheduled_departures (18,439 rows, batched)
                    dep_rows = [
                        (
                            d["trip_id"],
                            d["stop_sequence"],
                            d["stop_id"],
                            d["route_id"],
                            d["route_number"],
                            d["route_name"],
                            d["stop_name"],
                            d["direction_id"],
                            d["arrival_time"],
                            d["departure_time"],
                            d["departure_seconds"],
                        )
                        for d in scheduled_departures
                    ]
                    total_deps = len(dep_rows)
                    for offset in range(0, total_deps, batch_size):
                        batch = dep_rows[offset : offset + batch_size]
                        cur.executemany(
                            """
                            INSERT INTO public.scheduled_departures (
                                trip_id, stop_sequence, stop_id, route_id, route_number,
                                route_name, stop_name, direction_id, arrival_time,
                                departure_time, departure_seconds
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (trip_id, stop_sequence) DO UPDATE SET
                                stop_id = EXCLUDED.stop_id,
                                route_id = EXCLUDED.route_id,
                                route_number = EXCLUDED.route_number,
                                route_name = EXCLUDED.route_name,
                                stop_name = EXCLUDED.stop_name,
                                direction_id = EXCLUDED.direction_id,
                                arrival_time = EXCLUDED.arrival_time,
                                departure_time = EXCLUDED.departure_time,
                                departure_seconds = EXCLUDED.departure_seconds;
                            """,
                            batch,
                        )
                    counts_ingested["scheduled_departures"] = total_deps
                    logger.info(f"Ingested {total_deps} scheduled_departures in batches of {batch_size}.")

                    # 5. Ingest direct_connections (170 rows)
                    conn_rows = [
                        (
                            c["trip_id"],
                            c["route_id"],
                            c["route_number"],
                            c["route_name"],
                            c["origin_area_id"],
                            c["origin_area_name"],
                            c["origin_stop_id"],
                            c["origin_stop_name"],
                            c["scheduled_departure_at_origin"],
                            c["destination_stop_id"],
                            c["destination_stop_name"],
                            c["scheduled_arrival_at_t_nagar_stop"],
                            c["scheduled_departure_seconds"],
                            c["scheduled_arrival_seconds"],
                            Decimal(str(round(float(c["duration_minutes"]), 2))),
                            c["intermediate_stop_count"],
                            Jsonb(c["stops_between_origin_and_t_nagar"]),
                        )
                        for c in direct_connections
                    ]
                    cur.executemany(
                        """
                        INSERT INTO public.direct_connections (
                            trip_id, route_id, route_number, route_name, origin_area_id,
                            origin_area_name, origin_stop_id, origin_stop_name,
                            scheduled_departure_at_origin, destination_stop_id,
                            destination_stop_name, scheduled_arrival_at_t_nagar_stop,
                            scheduled_departure_seconds, scheduled_arrival_seconds,
                            duration_minutes, intermediate_stop_count,
                            stops_between_origin_and_t_nagar
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (trip_id) DO UPDATE SET
                            route_id = EXCLUDED.route_id,
                            route_number = EXCLUDED.route_number,
                            route_name = EXCLUDED.route_name,
                            origin_area_id = EXCLUDED.origin_area_id,
                            origin_area_name = EXCLUDED.origin_area_name,
                            origin_stop_id = EXCLUDED.origin_stop_id,
                            origin_stop_name = EXCLUDED.origin_stop_name,
                            scheduled_departure_at_origin = EXCLUDED.scheduled_departure_at_origin,
                            destination_stop_id = EXCLUDED.destination_stop_id,
                            destination_stop_name = EXCLUDED.destination_stop_name,
                            scheduled_arrival_at_t_nagar_stop = EXCLUDED.scheduled_arrival_at_t_nagar_stop,
                            scheduled_departure_seconds = EXCLUDED.scheduled_departure_seconds,
                            scheduled_arrival_seconds = EXCLUDED.scheduled_arrival_seconds,
                            duration_minutes = EXCLUDED.duration_minutes,
                            intermediate_stop_count = EXCLUDED.intermediate_stop_count,
                            stops_between_origin_and_t_nagar = EXCLUDED.stops_between_origin_and_t_nagar;
                        """,
                        conn_rows,
                    )
                    counts_ingested["direct_connections"] = len(conn_rows)
                    logger.info(f"Ingested {len(conn_rows)} direct_connections.")

                    # Calculate total records and duration
                    total_records = sum(counts_ingested.values())
                    duration = time.time() - start_time
                    completed_at = datetime.now(timezone.utc)

                    # Update dataset_ingestion_log to SUCCESS
                    if ingestion_log_id:
                        cur.execute(
                            """
                            UPDATE public.dataset_ingestion_log
                            SET status = 'SUCCESS',
                                total_records_ingested = %s,
                                completed_at = %s,
                                duration_seconds = %s
                            WHERE ingestion_id = %s;
                            """,
                            (
                                total_records,
                                completed_at,
                                Decimal(str(round(duration, 2))),
                                ingestion_log_id,
                            ),
                        )

            logger.info(f"Successfully committed ingestion of {total_records} records in {duration:.2f}s.")
            return IngestionResult(
                success=True,
                stage=DATASET_STAGE,
                dataset_version=dataset_version,
                composite_hash=preflight.composite_hash,
                counts_ingested=counts_ingested,
                total_records=total_records,
                quarantined_count=preflight.quarantined_stops_count,
                started_at=started_at,
                completed_at=completed_at,
                duration_seconds=duration,
                ingestion_log_id=ingestion_log_id,
            )

        except Exception as exc:
            duration = time.time() - start_time
            error_msg = f"Ingestion transaction failed: {exc}"
            logger.error(error_msg)

            # Try to log failure in a separate transaction
            try:
                if ingestion_log_id and conn and not conn.closed:
                    with conn.transaction():
                        with conn.cursor() as cur:
                            cur.execute(
                                """
                                UPDATE public.dataset_ingestion_log
                                SET status = 'FAILED',
                                    error_message = %s,
                                    completed_at = %s,
                                    duration_seconds = %s
                                WHERE ingestion_id = %s;
                                """,
                                (
                                    str(exc)[:500],
                                    datetime.now(timezone.utc),
                                    Decimal(str(round(duration, 2))),
                                    ingestion_log_id,
                                ),
                            )
            except Exception as log_exc:
                logger.error(f"Failed to record failure in dataset_ingestion_log: {log_exc}")

            return IngestionResult(
                success=False,
                stage=DATASET_STAGE,
                dataset_version=dataset_version,
                composite_hash=preflight.composite_hash,
                counts_ingested=counts_ingested,
                total_records=sum(counts_ingested.values()),
                quarantined_count=preflight.quarantined_stops_count,
                started_at=started_at,
                completed_at=datetime.now(timezone.utc),
                duration_seconds=duration,
                ingestion_log_id=ingestion_log_id,
                error_message=error_msg,
            )
        finally:
            if should_close_conn and conn and not conn.closed:
                conn.close()

    # --------------------------------------------------------------------------
    # Database Count Verification
    # --------------------------------------------------------------------------
    def verify_database(self, conn: Optional[psycopg.Connection] = None) -> VerificationResult:
        """
        Query actual database table row counts and compare against canonical specifications.
        Also retrieves the most recent dataset_ingestion_log audit entry.
        """
        actual_counts: Dict[str, int] = {}
        discrepancies: Dict[str, Dict[str, int]] = {}
        latest_log: Optional[Dict[str, Any]] = None
        should_close_conn = False

        if conn is None:
            conn = get_sync_connection(self.db_url)
            should_close_conn = True

        try:
            with conn.cursor() as cur:
                # Query row counts for all 5 structured tables
                tables = [
                    "routes",
                    "route_services",
                    "bus_stops",
                    "scheduled_departures",
                    "direct_connections",
                ]
                for tbl in tables:
                    cur.execute(f"SELECT count(*) FROM public.{tbl};")
                    row = cur.fetchone()
                    actual_counts[tbl] = row[0] if row else 0

                # Check for discrepancies against expected counts
                for entity, expected in EXPECTED_COUNTS.items():
                    actual = actual_counts.get(entity, 0)
                    if actual != expected:
                        discrepancies[entity] = {"expected": expected, "actual": actual}

                # Retrieve latest audit log
                cur.execute(
                    """
                    SELECT ingestion_id, dataset_version, dataset_file_hash, ingestion_stage,
                           status, total_records_ingested, quarantined_records_count,
                           error_message, started_at, completed_at, duration_seconds
                    FROM public.dataset_ingestion_log
                    ORDER BY ingestion_id DESC
                    LIMIT 1;
                    """
                )
                log_row = cur.fetchone()
                if log_row:
                    latest_log = {
                        "ingestion_id": log_row[0],
                        "dataset_version": log_row[1],
                        "dataset_file_hash": log_row[2],
                        "ingestion_stage": log_row[3],
                        "status": log_row[4],
                        "total_records_ingested": log_row[5],
                        "quarantined_records_count": log_row[6],
                        "error_message": log_row[7],
                        "started_at": log_row[8].isoformat() if log_row[8] else None,
                        "completed_at": log_row[9].isoformat() if log_row[9] else None,
                        "duration_seconds": float(log_row[10]) if log_row[10] is not None else None,
                    }

            all_matched = len(discrepancies) == 0
            return VerificationResult(
                all_matched=all_matched,
                actual_counts=actual_counts,
                expected_counts=EXPECTED_COUNTS,
                discrepancies=discrepancies,
                latest_ingestion_log=latest_log,
            )
        finally:
            if should_close_conn and conn and not conn.closed:
                conn.close()

