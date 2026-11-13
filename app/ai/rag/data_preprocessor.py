import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("uvicorn.error")


def parse_time_to_seconds(time_str: str) -> int:
    """
    Parses GTFS HH:MM:SS time string into total seconds from midnight.
    Correctly supports GTFS post-midnight trips where hour >= 24 (e.g., '24:15:00').
    """
    parts = time_str.strip().split(":")
    if len(parts) != 3:
        raise ValueError(f"Invalid time format '{time_str}', expected 'HH:MM:SS'")
    hours = int(parts[0])
    minutes = int(parts[1])
    seconds = int(parts[2])
    return hours * 3600 + minutes * 60 + seconds


def parse_boolean(val: Any) -> bool:
    """
    Parses boolean string or value ('True', 'False', 1, 0) to Python bool.
    """
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().lower() in ("true", "1", "yes", "t")
    return bool(val)


def parse_float_optional(val: Any) -> Optional[float]:
    """
    Converts string to float if non-empty, preserving missing/empty values as None.
    Never invents coordinates.
    """
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    val_str = str(val).strip()
    if not val_str:
        return None
    try:
        return float(val_str)
    except ValueError:
        return None


class DataPreprocessor:
    """
    Production ETL Preprocessor for Sholinganallur GTFS dataset.
    Normalizes entities into clean, typed, JSON-serializable structures.
    """

    @staticmethod
    def normalize_bus_stops(raw_stops: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Normalizes bus stop records.
        - Preserves stop IDs and names.
        - Converts valid latitude and longitude to float.
        - Preserves missing coordinates as null.
        - Retains map_eligible status as bool.
        - Identifies and tracks quarantined stops without coordinates.
        """
        normalized_stops: List[Dict[str, Any]] = []
        quarantined_stops: List[Dict[str, Any]] = []

        for record in raw_stops:
            stop_id = str(record["stop_id"]).strip()
            lat = parse_float_optional(record.get("latitude"))
            lon = parse_float_optional(record.get("longitude"))
            orig_lat = parse_float_optional(record.get("original_latitude"))
            orig_lon = parse_float_optional(record.get("original_longitude"))
            map_eligible = parse_boolean(record.get("map_eligible", False))
            in_bounds = parse_boolean(record.get("coordinates_within_chennai_bounds", False))
            area_verified = parse_boolean(record.get("locality_area_membership_verified", False))

            clean_stop = {
                "stop_id": stop_id,
                "stop_code": str(record.get("stop_code", "")).strip(),
                "stop_name": str(record.get("stop_name", "")).strip(),
                "normalized_stop_name": str(record.get("normalized_stop_name", "")).strip(),
                "latitude": lat,
                "longitude": lon,
                "coordinates_within_chennai_bounds": in_bounds,
                "map_eligible": map_eligible,
                "coordinate_status": str(record.get("coordinate_status", "")).strip(),
                "locality_area_membership_verified": area_verified,
                "locality_area_membership_status": str(record.get("locality_area_membership_status", "")).strip(),
                "coordinate_source": str(record.get("coordinate_source", "")).strip(),
                "coordinate_verification_status": str(record.get("coordinate_verification_status", "")).strip(),
                "original_latitude": orig_lat,
                "original_longitude": orig_lon,
            }

            normalized_stops.append(clean_stop)

            # If coordinates are missing or stop is not map eligible, track in quarantine audit
            if lat is None or lon is None or not map_eligible:
                quarantined_stops.append({
                    "entity_type": "bus_stop",
                    "id": stop_id,
                    "name": clean_stop["stop_name"],
                    "reason": "Missing or quarantined geographic coordinates (outside Chennai bounds in source GTFS)",
                    "map_eligible": map_eligible,
                    "coordinate_status": clean_stop["coordinate_status"],
                })

        return normalized_stops, quarantined_stops

    @staticmethod
    def normalize_route_service_summary(raw_routes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Normalizes route service summaries.
        - Preserves route IDs and route numbers.
        - Converts trip and departure counts to int.
        - Preserves earliest/latest departure strings and adds seconds offset.
        - Does not merge distinct routes.
        """
        normalized_routes: List[Dict[str, Any]] = []

        for record in raw_routes:
            route_id = str(record["route_id"]).strip()
            route_number = str(record["route_number"]).strip()
            route_name = str(record["route_name"]).strip()
            distinct_trips = int(record["distinct_trips_in_feed"])
            scheduled_deps = int(record["scheduled_stop_departures_in_area"])
            earliest_str = str(record["earliest_departure_time_string"]).strip()
            latest_str = str(record["latest_departure_time_string"]).strip()

            clean_route = {
                "route_id": route_id,
                "route_number": route_number,
                "route_name": route_name,
                "distinct_trips_in_feed": distinct_trips,
                "scheduled_stop_departures_in_area": scheduled_deps,
                "earliest_departure_time_string": earliest_str,
                "latest_departure_time_string": latest_str,
                "earliest_departure_seconds": parse_time_to_seconds(earliest_str),
                "latest_departure_seconds": parse_time_to_seconds(latest_str),
            }
            normalized_routes.append(clean_route)

        return normalized_routes

    @staticmethod
    def normalize_scheduled_departures(raw_deps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Normalizes scheduled departures.
        - Preserves original departure time strings.
        - Preserves stop, route, trip IDs and stop sequences.
        - Adds departure_seconds for fast chronological filtering.
        - Maintains static GTFS schedule semantics.
        """
        normalized_deps: List[Dict[str, Any]] = []

        for record in raw_deps:
            arr_time_str = str(record["arrival_time"]).strip()
            dep_time_str = str(record["departure_time"]).strip()

            clean_dep = {
                "stop_id": str(record["stop_id"]).strip(),
                "stop_name": str(record["stop_name"]).strip(),
                "route_id": str(record["route_id"]).strip(),
                "route_number": str(record["route_number"]).strip(),
                "route_name": str(record["route_name"]).strip(),
                "trip_id": str(record["trip_id"]).strip(),
                "direction_id": int(record["direction_id"]),
                "stop_sequence": int(record["stop_sequence"]),
                "arrival_time": arr_time_str,
                "departure_time": dep_time_str,
                "departure_seconds": parse_time_to_seconds(dep_time_str),
            }
            normalized_deps.append(clean_dep)

        return normalized_deps

    @staticmethod
    def normalize_direct_t_nagar_connections(raw_conns: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Normalizes direct scheduled connections to T. Nagar.
        - Parses stops_between_origin_and_t_nagar using json.loads.
        - Preserves full intermediate stop journey order without fabricating stops.
        - Calculates journey duration in minutes between origin departure and T. Nagar arrival.
        """
        normalized_conns: List[Dict[str, Any]] = []

        for record in raw_conns:
            raw_stops_json = record.get("stops_between_origin_and_t_nagar", "[]")
            intermediate_stops = json.loads(raw_stops_json)

            # Ensure integer sequence types inside intermediate stops
            clean_intermediate_stops = []
            for st in intermediate_stops:
                clean_intermediate_stops.append({
                    "stop_sequence": int(st["stop_sequence"]),
                    "stop_id": str(st["stop_id"]).strip(),
                    "stop_name": str(st["stop_name"]).strip(),
                    "arrival_time": str(st["arrival_time"]).strip(),
                    "departure_time": str(st["departure_time"]).strip(),
                })

            dep_str = str(record["scheduled_departure_at_origin"]).strip()
            arr_str = str(record["scheduled_arrival_at_t_nagar_stop"]).strip()
            dep_sec = parse_time_to_seconds(dep_str)
            arr_sec = parse_time_to_seconds(arr_str)

            # Calculate journey duration (in minutes)
            duration_minutes = round((arr_sec - dep_sec) / 60.0, 2)

            clean_conn = {
                "origin_area_id": str(record["origin_area_id"]).strip(),
                "origin_area_name": str(record["origin_area_name"]).strip(),
                "trip_id": str(record["trip_id"]).strip(),
                "route_id": str(record["route_id"]).strip(),
                "route_number": str(record["route_number"]).strip(),
                "route_name": str(record["route_name"]).strip(),
                "origin_stop_id": str(record["origin_stop_id"]).strip(),
                "origin_stop_name": str(record["origin_stop_name"]).strip(),
                "scheduled_departure_at_origin": dep_str,
                "destination_stop_id": str(record["destination_stop_id"]).strip(),
                "destination_stop_name": str(record["destination_stop_name"]).strip(),
                "scheduled_arrival_at_t_nagar_stop": arr_str,
                "scheduled_departure_seconds": dep_sec,
                "scheduled_arrival_seconds": arr_sec,
                "duration_minutes": duration_minutes,
                "intermediate_stop_count": len(clean_intermediate_stops),
                "stops_between_origin_and_t_nagar": clean_intermediate_stops,
            }
            normalized_conns.append(clean_conn)

        return normalized_conns

    @staticmethod
    def build_dataset_metadata(
        area_summary: Dict[str, Any],
        dataset_json: Dict[str, Any],
        readme: str,
        counts: Dict[str, int],
    ) -> Dict[str, Any]:
        """
        Synthesizes standardized dataset metadata preserving documented provenance,
        scope, limitations, coordinate patch policy, and static schedule disclaimers.
        """
        return {
            "dataset_title": dataset_json.get(
                "dataset_title",
                "RouteWise AI — Chennai MTC Bus Data — Sholinganallur Only"
            ),
            "area": area_summary.get("area", "Sholinganallur"),
            "preprocessed_at_utc": datetime.now(timezone.utc).isoformat(),
            "source_uploaded_file": dataset_json.get("source_uploaded_file", "static_mtc_gtfs_data(3).zip"),
            "source_gtfs_archive": dataset_json.get("source_gtfs_archive", "static_mtc_gtfs_data/chennai.bus.gtfs.zip"),
            "source_tables_used": dataset_json.get("source_tables_used", ["agency", "stops", "routes", "trips", "stop_times"]),
            "canonical_counts": counts,
            "scope": dataset_json.get("scope", {}),
            "data_quality_and_limitations": dataset_json.get("data_quality_and_limitations", [
                "Static GTFS schedules only; not live bus arrivals or vehicle positions.",
                "Scheduled time strings do not prove the current bus is on time or currently approaching.",
                "The feed contains no verified live or historical passenger occupancy.",
                "Stop matching is based on names; review before production use.",
                "Some stop coordinates are flagged if outside broad Chennai bounds.",
                "Direct T. Nagar connections are same-trip candidates (no transfers generated).",
                "Validate feed freshness, service dates, direction, and route operation before passenger-facing use."
            ]),
            "coordinate_quality_patch": dataset_json.get("coordinate_quality_patch", {}),
            "static_schedule_disclaimer": (
                "GTFS schedule values are static timetable values, not live bus tracking. "
                "RouteWise AI will answer queries using scheduled timetable departure times."
            ),
            "readme_summary": readme.strip(),
        }

