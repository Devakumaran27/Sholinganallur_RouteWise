import logging
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger("uvicorn.error")


class DataValidator:
    """
    Validates data integrity, entity counts, schema completeness,
    referential consistency, and chronological ordering for public transit datasets.
    """

    def __init__(self):
        self.validation_errors: List[str] = []
        self.validation_warnings: List[str] = []
        self.metrics: Dict[str, Any] = {}

    def validate_dataset(
        self,
        raw_counts: Dict[str, int],
        bus_stops: List[Dict[str, Any]],
        routes: List[Dict[str, Any]],
        departures: List[Dict[str, Any]],
        direct_connections: List[Dict[str, Any]],
        quarantined_records: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Executes complete validation suite across all preprocessed datasets.
        """
        self.validation_errors.clear()
        self.validation_warnings.clear()

        # 1. Record count validation
        self._validate_record_counts(raw_counts, bus_stops, routes, departures, direct_connections)

        # 2. Required fields and schema completeness
        self._validate_required_fields(bus_stops, routes, departures, direct_connections)

        # 3. Uniqueness of primary entity identifiers
        self._validate_identifier_uniqueness(bus_stops, routes, departures, direct_connections)

        # 4. Referential consistency
        self._validate_referential_consistency(bus_stops, routes, departures, direct_connections)

        # 5. Coordinate integrity & quarantine checks
        self._validate_coordinates(bus_stops, quarantined_records)

        # 6. Chronological ordering
        self._validate_chronological_ordering(departures, direct_connections)

        is_valid = len(self.validation_errors) == 0

        return {
            "is_valid": is_valid,
            "error_count": len(self.validation_errors),
            "warning_count": len(self.validation_warnings),
            "errors": list(self.validation_errors),
            "warnings": list(self.validation_warnings),
            "metrics": dict(self.metrics),
        }

    def _validate_record_counts(
        self,
        raw_counts: Dict[str, int],
        bus_stops: List[Dict[str, Any]],
        routes: List[Dict[str, Any]],
        departures: List[Dict[str, Any]],
        direct_connections: List[Dict[str, Any]],
    ) -> None:
        checks = [
            ("bus_stops", len(bus_stops), raw_counts.get("bus_stops", 0)),
            ("route_service_summary", len(routes), raw_counts.get("route_service_summary", 0)),
            ("scheduled_departures", len(departures), raw_counts.get("scheduled_departures", 0)),
            ("direct_t_nagar_connections", len(direct_connections), raw_counts.get("direct_t_nagar_connections", 0)),
        ]
        count_results = {}
        for entity, actual, expected in checks:
            count_results[entity] = {"actual": actual, "expected": expected, "match": actual == expected}
            if actual != expected:
                self.validation_errors.append(
                    f"Count mismatch for {entity}: expected {expected}, processed {actual}"
                )
        self.metrics["record_counts"] = count_results

    def _validate_required_fields(
        self,
        bus_stops: List[Dict[str, Any]],
        routes: List[Dict[str, Any]],
        departures: List[Dict[str, Any]],
        direct_connections: List[Dict[str, Any]],
    ) -> None:
        # Bus stops
        for stop in bus_stops:
            if not stop.get("stop_id") or not stop.get("stop_name"):
                self.validation_errors.append(f"Bus stop missing required ID or name: {stop}")

        # Routes
        for route in routes:
            if not route.get("route_id") or not route.get("route_number") or not route.get("route_name"):
                self.validation_errors.append(f"Route missing required fields: {route}")

        # Departures
        for dep in departures:
            if not dep.get("stop_id") or not dep.get("route_id") or not dep.get("trip_id"):
                self.validation_errors.append(f"Departure missing required identifiers: {dep}")
            if "departure_seconds" not in dep or dep["departure_seconds"] is None:
                self.validation_errors.append(f"Departure missing parsed seconds: {dep}")

        # Direct connections
        for conn in direct_connections:
            if not conn.get("trip_id") or not conn.get("origin_stop_id") or not conn.get("destination_stop_id"):
                self.validation_errors.append(f"Direct connection missing required identifiers: {conn}")

    def _validate_identifier_uniqueness(
        self,
        bus_stops: List[Dict[str, Any]],
        routes: List[Dict[str, Any]],
        departures: List[Dict[str, Any]],
        direct_connections: List[Dict[str, Any]],
    ) -> None:
        # Stop ID uniqueness
        stop_ids = [s["stop_id"] for s in bus_stops]
        if len(stop_ids) != len(set(stop_ids)):
            self.validation_errors.append("Duplicate stop_id detected in bus_stops")
        self.metrics["unique_bus_stops"] = len(set(stop_ids))

        # Route ID uniqueness
        route_ids = [r["route_id"] for r in routes]
        if len(route_ids) != len(set(route_ids)):
            self.validation_errors.append("Duplicate route_id detected in route_service_summary")
        self.metrics["unique_route_ids"] = len(set(route_ids))

        # Departures composite key (trip_id, stop_sequence)
        dep_keys = [(d["trip_id"], d["stop_sequence"]) for d in departures]
        if len(dep_keys) != len(set(dep_keys)):
            self.validation_errors.append("Duplicate (trip_id, stop_sequence) detected in scheduled_departures")
        self.metrics["unique_departure_keys"] = len(set(dep_keys))

        # Direct connections trip_id
        conn_trip_ids = [c["trip_id"] for c in direct_connections]
        if len(conn_trip_ids) != len(set(conn_trip_ids)):
            self.validation_errors.append("Duplicate trip_id detected in direct_t_nagar_connections")
        self.metrics["unique_direct_connection_trips"] = len(set(conn_trip_ids))

    def _validate_referential_consistency(
        self,
        bus_stops: List[Dict[str, Any]],
        routes: List[Dict[str, Any]],
        departures: List[Dict[str, Any]],
        direct_connections: List[Dict[str, Any]],
    ) -> None:
        valid_stop_ids: Set[str] = {s["stop_id"] for s in bus_stops}
        valid_route_ids: Set[str] = {r["route_id"] for r in routes}

        # 1. Scheduled departures -> bus_stops
        orphan_dep_stops = {d["stop_id"] for d in departures if d["stop_id"] not in valid_stop_ids}
        if orphan_dep_stops:
            self.validation_errors.append(f"Orphan stop_ids in departures not found in bus_stops: {orphan_dep_stops}")

        # 2. Scheduled departures -> routes
        orphan_dep_routes = {d["route_id"] for d in departures if d["route_id"] not in valid_route_ids}
        if orphan_dep_routes:
            self.validation_errors.append(f"Orphan route_ids in departures not found in route_service_summary: {orphan_dep_routes}")

        # 3. Direct connections origin_stop_id -> bus_stops
        orphan_conn_stops = {c["origin_stop_id"] for c in direct_connections if c["origin_stop_id"] not in valid_stop_ids}
        if orphan_conn_stops:
            self.validation_errors.append(f"Orphan origin_stop_ids in direct connections: {orphan_conn_stops}")

        # 4. Direct connections route_id -> routes
        orphan_conn_routes = {c["route_id"] for c in direct_connections if c["route_id"] not in valid_route_ids}
        if orphan_conn_routes:
            self.validation_errors.append(f"Orphan route_ids in direct connections: {orphan_conn_routes}")

        self.metrics["referential_integrity"] = {
            "all_departure_stops_in_bus_stops": len(orphan_dep_stops) == 0,
            "all_departure_routes_in_routes": len(orphan_dep_routes) == 0,
            "all_connection_origins_in_bus_stops": len(orphan_conn_stops) == 0,
            "all_connection_routes_in_routes": len(orphan_conn_routes) == 0,
        }

    def _validate_coordinates(
        self,
        bus_stops: List[Dict[str, Any]],
        quarantined_records: List[Dict[str, Any]],
    ) -> None:
        valid_coords = 0
        null_coords = 0

        for stop in bus_stops:
            lat = stop.get("latitude")
            lon = stop.get("longitude")
            map_eligible = stop.get("map_eligible", False)

            if lat is not None and lon is not None:
                # Coordinate validity in Chennai vicinity
                if not (12.5 <= lat <= 13.5 and 79.8 <= lon <= 80.5):
                    self.validation_warnings.append(
                        f"Stop {stop['stop_id']} coordinates ({lat}, {lon}) are outside standard Chennai bounds"
                    )
                valid_coords += 1
            else:
                null_coords += 1
                if map_eligible:
                    self.validation_errors.append(
                        f"Stop {stop['stop_id']} has null coordinates but map_eligible is True"
                    )

        self.metrics["coordinate_audit"] = {
            "total_stops": len(bus_stops),
            "valid_coordinate_stops": valid_coords,
            "null_or_quarantined_coordinate_stops": null_coords,
            "quarantined_records_count": len(quarantined_records),
        }

    def _validate_chronological_ordering(
        self,
        departures: List[Dict[str, Any]],
        direct_connections: List[Dict[str, Any]],
    ) -> None:
        # Check direct connections: arrival > departure (positive duration)
        negative_durations = [c for c in direct_connections if c["duration_minutes"] <= 0]
        if negative_durations:
            self.validation_errors.append(
                f"Direct connections with negative/zero duration: {len(negative_durations)}"
            )

        # Check intermediate stops sequence ordering
        unordered_intermediate = 0
        for conn in direct_connections:
            seqs = [s["stop_sequence"] for s in conn["stops_between_origin_and_t_nagar"]]
            if seqs != sorted(seqs):
                unordered_intermediate += 1
        if unordered_intermediate > 0:
            self.validation_errors.append(
                f"Direct connections with out-of-order intermediate stops: {unordered_intermediate}"
            )

        self.metrics["chronological_ordering"] = {
            "all_direct_connections_positive_duration": len(negative_durations) == 0,
            "all_intermediate_stops_ordered": unordered_intermediate == 0,
        }

