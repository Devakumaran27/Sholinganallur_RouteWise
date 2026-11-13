"""
Module: app.services.transit_service
Purpose: Read-only database service layer for structured public transit data:
         bus stop discovery, route service associations, scheduled timetable lookups,
         and direct Sholinganallur-to-T. Nagar journey connections.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException, status
import psycopg
from psycopg.rows import dict_row

from app.schemas.transit import (
    BusStopResponse,
    BusStopSearchResponse,
    DirectConnectionItem,
    DirectJourneyResponse,
    RouteServiceBrief,
    ScheduledDepartureResponse,
    StopDeparturesResponse,
    StopRoutesResponse,
)

logger = logging.getLogger("uvicorn.error")


def parse_gtfs_time_to_seconds(time_str: str) -> int:
    """
    Parse a GTFS service-day time string (HH:MM:SS or HH:MM) into seconds past midnight.
    Strictly preserves post-midnight >24:00 syntax (e.g. '24:15:00' -> 87300s, '25:30:00' -> 91800s)
    without modulo-wrapping to next day.

    Args:
        time_str: Time string formatted as HH:MM:SS or HH:MM.

    Returns:
        int: Total seconds past midnight for the service day.

    Raises:
        ValueError: If the format or numeric values are invalid.
    """
    parts = time_str.strip().split(":")
    if len(parts) not in (2, 3):
        raise ValueError(f"Invalid time format '{time_str}'. Expected 'HH:MM:SS' or 'HH:MM'.")

    try:
        hours = int(parts[0])
        minutes = int(parts[1])
        seconds = int(parts[2]) if len(parts) == 3 else 0
    except ValueError:
        raise ValueError(f"Non-numeric values in time string '{time_str}'.")

    if hours < 0:
        raise ValueError(f"Hours component cannot be negative in '{time_str}'.")
    if not (0 <= minutes < 60):
        raise ValueError(f"Minutes component must be between 0 and 59 in '{time_str}'.")
    if not (0 <= seconds < 60):
        raise ValueError(f"Seconds component must be between 0 and 59 in '{time_str}'.")

    return hours * 3600 + minutes * 60 + seconds


class TransitService:
    """
    Read-only transit data access service executing parameterized SQL queries
    against Supabase PostgreSQL using an active Psycopg 3 AsyncConnection.
    """

    def __init__(self, conn: psycopg.AsyncConnection):
        self.conn = conn

    async def search_bus_stops(
        self,
        query: Optional[str] = None,
        map_eligible_only: bool = False,
        limit: int = 26,
    ) -> BusStopSearchResponse:
        """
        Search bus stops by name with case-insensitive partial matching.
        Preserves null coordinates by default; filters by map_eligible when requested.

        Args:
            query: Optional search keyword to filter stop_name or normalized_stop_name.
            map_eligible_only: If True, returns only stops with verified coordinates.
            limit: Maximum records to return (1-50).

        Returns:
            BusStopSearchResponse: Matching stop profiles and total count.
        """
        conditions: List[str] = []
        params: List[Any] = []

        if query and query.strip():
            clean_query = query.strip()
            conditions.append(
                "(stop_name ILIKE %s OR normalized_stop_name ILIKE %s)"
            )
            wildcard = f"%{clean_query}%"
            params.extend([wildcard, wildcard])

        if map_eligible_only:
            conditions.append(
                "(map_eligible = TRUE AND latitude IS NOT NULL AND longitude IS NOT NULL)"
            )

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"""
            SELECT
                stop_id,
                stop_code,
                stop_name,
                normalized_stop_name,
                latitude,
                longitude,
                coordinates_within_chennai_bounds,
                map_eligible,
                coordinate_status,
                locality_area_membership_verified,
                locality_area_membership_status,
                coordinate_source,
                coordinate_verification_status,
                created_at,
                count(*) OVER() AS total_count
            FROM public.bus_stops
            {where_clause}
            ORDER BY stop_name ASC, stop_id ASC
            LIMIT %s;
        """
        params.append(limit)

        async with self.conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(sql, params)
            rows = await cur.fetchall()

        if not rows:
            return BusStopSearchResponse(total=0, query=query, stops=[])

        total_count = int(rows[0]["total_count"])
        stops = [
            BusStopResponse(
                stop_id=r["stop_id"],
                stop_code=r["stop_code"],
                stop_name=r["stop_name"],
                normalized_stop_name=r["normalized_stop_name"],
                latitude=r["latitude"],
                longitude=r["longitude"],
                coordinates_within_chennai_bounds=bool(r["coordinates_within_chennai_bounds"]),
                map_eligible=bool(r["map_eligible"]),
                coordinate_status=r["coordinate_status"],
                locality_area_membership_verified=bool(r["locality_area_membership_verified"]),
                locality_area_membership_status=r["locality_area_membership_status"],
                coordinate_source=r["coordinate_source"],
                coordinate_verification_status=r["coordinate_verification_status"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

        return BusStopSearchResponse(total=total_count, query=query, stops=stops)

    async def get_routes_for_stop(self, stop_id: str) -> StopRoutesResponse:
        """
        Retrieve distinct route services that call at a verified bus stop.

        Args:
            stop_id: Target bus stop identifier.

        Returns:
            StopRoutesResponse: List of unique route service variants serving the stop.

        Raises:
            HTTPException: 404 if the stop does not exist.
        """
        # 1. Verify stop existence
        async with self.conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT stop_id, stop_name FROM public.bus_stops WHERE stop_id = %s LIMIT 1;",
                (stop_id,),
            )
            stop_row = await cur.fetchone()

        if not stop_row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Bus stop '{stop_id}' not found in Sholinganallur transit area.",
            )

        # 2. Join scheduled_departures and route_services to find active variants
        sql = """
            SELECT DISTINCT
                rs.route_number,
                rs.route_id,
                rs.route_name,
                sd.direction_id,
                rs.earliest_departure_time_string AS earliest_departure,
                rs.latest_departure_time_string AS latest_departure
            FROM public.scheduled_departures sd
            JOIN public.route_services rs ON sd.route_id = rs.route_id
            WHERE sd.stop_id = %s
            ORDER BY rs.route_number ASC, rs.route_name ASC, sd.direction_id ASC;
        """
        async with self.conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(sql, (stop_id,))
            rows = await cur.fetchall()

        routes = [
            RouteServiceBrief(
                route_number=r["route_number"],
                route_id=r["route_id"],
                route_name=r["route_name"],
                direction_id=r["direction_id"],
                earliest_departure=r["earliest_departure"],
                latest_departure=r["latest_departure"],
            )
            for r in rows
        ]

        return StopRoutesResponse(
            stop_id=stop_row["stop_id"],
            stop_name=stop_row["stop_name"],
            total_routes=len(routes),
            routes=routes,
        )

    async def get_scheduled_departures(
        self,
        stop_id: str,
        after_time_str: Optional[str] = None,
        route_number: Optional[str] = None,
        limit: int = 20,
    ) -> StopDeparturesResponse:
        """
        Retrieve static scheduled timetable departures from a verified bus stop.
        Queries and sorts strictly using integer departure_seconds.

        Args:
            stop_id: Target bus stop identifier.
            after_time_str: Optional service-day time filter (HH:MM:SS or HH:MM).
            route_number: Optional line number filter (e.g. '102').
            limit: Maximum departures to return (1-100).

        Returns:
            StopDeparturesResponse: Chronological scheduled timetable departures.

        Raises:
            HTTPException: 404 if the stop does not exist.
            HTTPException: 422 if after_time format is invalid.
        """
        # 1. Verify stop existence
        async with self.conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT stop_id, stop_name FROM public.bus_stops WHERE stop_id = %s LIMIT 1;",
                (stop_id,),
            )
            stop_row = await cur.fetchone()

        if not stop_row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Bus stop '{stop_id}' not found in Sholinganallur transit area.",
            )

        # 2. Parse after_time if provided
        after_seconds: Optional[int] = None
        base_query_time = after_time_str or "00:00:00"
        if after_time_str:
            try:
                after_seconds = parse_gtfs_time_to_seconds(after_time_str)
            except ValueError as ve:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Invalid 'after_time' parameter: {ve}",
                )

        # 3. Construct parameterized query
        conditions: List[str] = ["stop_id = %s"]
        params: List[Any] = [stop_id]

        if after_seconds is not None:
            conditions.append("departure_seconds >= %s")
            params.append(after_seconds)

        if route_number and route_number.strip():
            conditions.append("UPPER(route_number) = UPPER(%s)")
            params.append(route_number.strip())

        sql = f"""
            SELECT
                departure_id,
                trip_id,
                stop_sequence,
                route_number,
                route_name,
                stop_id,
                stop_name,
                direction_id,
                departure_time AS scheduled_departure,
                departure_seconds
            FROM public.scheduled_departures
            WHERE {' AND '.join(conditions)}
            ORDER BY departure_seconds ASC, stop_sequence ASC
            LIMIT %s;
        """
        params.append(limit)

        async with self.conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(sql, params)
            rows = await cur.fetchall()

        departures = [
            ScheduledDepartureResponse(
                departure_id=r["departure_id"],
                trip_id=r["trip_id"],
                stop_sequence=r["stop_sequence"],
                route_number=r["route_number"],
                route_name=r["route_name"],
                stop_id=r["stop_id"],
                stop_name=r["stop_name"],
                direction_id=r["direction_id"],
                scheduled_departure=r["scheduled_departure"],
                departure_seconds=r["departure_seconds"],
            )
            for r in rows
        ]

        return StopDeparturesResponse(
            stop_id=stop_row["stop_id"],
            stop_name=stop_row["stop_name"],
            query_time=base_query_time,
            count=len(departures),
            departures=departures,
        )

    async def get_direct_journeys(
        self,
        origin_stop_id: Optional[str] = None,
        after_time_str: Optional[str] = None,
        limit: int = 10,
    ) -> DirectJourneyResponse:
        """
        Retrieve direct scheduled transit connections from Sholinganallur to T. Nagar.
        Uses verified public.direct_connections records and preserves external destination IDs.

        Args:
            origin_stop_id: Optional origin stop identifier to filter trips.
            after_time_str: Optional service-day departure time filter.
            limit: Maximum journeys to return (1-50).

        Returns:
            DirectJourneyResponse: Direct journey runs sorted by scheduled departure.

        Raises:
            HTTPException: 404 if origin_stop_id is provided but unknown in bus_stops.
            HTTPException: 422 if after_time format is invalid.
        """
        # 1. If origin_stop_id is specified, validate that the stop exists in bus_stops
        if origin_stop_id:
            async with self.conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    "SELECT stop_id, stop_name FROM public.bus_stops WHERE stop_id = %s LIMIT 1;",
                    (origin_stop_id,),
                )
                stop_row = await cur.fetchone()

            if not stop_row:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Origin bus stop '{origin_stop_id}' not found in Sholinganallur transit area.",
                )

        # 2. Parse after_time if provided
        after_seconds: Optional[int] = None
        if after_time_str is not None:
            clean_time_str = after_time_str.strip()
            if not clean_time_str:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Invalid 'after_time' parameter: time string cannot be blank.",
                )
            try:
                after_seconds = parse_gtfs_time_to_seconds(clean_time_str)
            except ValueError as ve:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Invalid 'after_time' parameter: {ve}",
                )

        # 3. Build query
        conditions: List[str] = []
        params: List[Any] = []

        if origin_stop_id:
            conditions.append("origin_stop_id = %s")
            params.append(origin_stop_id)

        if after_seconds is not None:
            conditions.append("scheduled_departure_seconds >= %s")
            params.append(after_seconds)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"""
            SELECT
                trip_id,
                route_number,
                route_name,
                origin_stop_id,
                origin_stop_name,
                scheduled_departure_at_origin AS scheduled_departure,
                destination_stop_id,
                destination_stop_name,
                scheduled_arrival_at_t_nagar_stop AS scheduled_arrival,
                scheduled_departure_seconds,
                scheduled_arrival_seconds,
                duration_minutes,
                intermediate_stop_count,
                stops_between_origin_and_t_nagar AS stops_between
            FROM public.direct_connections
            {where_clause}
            ORDER BY scheduled_departure_seconds ASC, trip_id ASC
            LIMIT %s;
        """
        params.append(limit)

        async with self.conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(sql, params)
            rows = await cur.fetchall()

        journeys = [
            DirectConnectionItem(
                trip_id=r["trip_id"],
                route_number=r["route_number"],
                route_name=r["route_name"],
                origin_stop_id=r["origin_stop_id"],
                origin_stop_name=r["origin_stop_name"],
                scheduled_departure=r["scheduled_departure"],
                destination_stop_id=r["destination_stop_id"],
                destination_stop_name=r["destination_stop_name"],
                scheduled_arrival=r["scheduled_arrival"],
                scheduled_departure_seconds=r["scheduled_departure_seconds"],
                scheduled_arrival_seconds=r["scheduled_arrival_seconds"],
                duration_minutes=float(r["duration_minutes"]),
                intermediate_stop_count=r["intermediate_stop_count"],
                stops_between=r["stops_between"],
            )
            for r in rows
        ]

        return DirectJourneyResponse(
            corridor="Sholinganallur -> T. Nagar",
            total_options=len(journeys),
            journeys=journeys,
        )

    async def get_all_routes(self, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Retrieve canonical public routes serving the Sholinganallur transit area.
        Queries public.routes ordered by total_daily_trips descending.

        Args:
            limit: Maximum route records to retrieve (default: 100).

        Returns:
            List[Dict[str, Any]]: List of canonical route dictionaries.
        """
        sql = """
            SELECT
                route_number,
                total_service_variants,
                total_daily_trips,
                earliest_system_seconds,
                latest_system_seconds
            FROM public.routes
            ORDER BY total_daily_trips DESC, route_number ASC
            LIMIT %s;
        """
        async with self.conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(sql, (limit,))
            return await cur.fetchall()

    async def get_direct_routes_summary(self) -> List[Dict[str, Any]]:
        """
        Retrieve distinct verified direct routes connecting Sholinganallur to T. Nagar (CIT Nagar).
        Derived strictly from public.direct_connections with explicit origin and destination filters.
        """
        sql = """
            SELECT
                route_number,
                count(*)::int AS total_direct_trips,
                string_agg(DISTINCT origin_stop_name, ', ') AS origin_stops,
                string_agg(DISTINCT destination_stop_name, ', ') AS destination_stops,
                MIN(scheduled_departure_at_origin) AS earliest_departure,
                MAX(scheduled_departure_at_origin) AS latest_departure,
                ROUND(MIN(duration_minutes)::numeric, 2)::float AS min_duration_minutes,
                ROUND(MAX(duration_minutes)::numeric, 2)::float AS max_duration_minutes,
                MIN(intermediate_stop_count)::int AS intermediate_stops
            FROM public.direct_connections
            WHERE origin_stop_name ILIKE '%SHOLINGANALLUR%'
              AND (destination_stop_name ILIKE '%CIT NAGAR%' OR destination_stop_name ILIKE '%T. NAGAR%' OR destination_stop_name ILIKE '%T NAGAR%')
            GROUP BY route_number
            ORDER BY total_direct_trips DESC, route_number ASC;
        """
        async with self.conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(sql)
            return await cur.fetchall()

    async def get_sholinganallur_serving_routes(self, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Retrieve canonical routes with verified service calls at Sholinganallur bus stops.
        Derived through relational joins: bus_stops -> scheduled_departures -> route_services -> routes.
        """
        sql = """
            SELECT
                r.route_number,
                r.total_service_variants,
                r.total_daily_trips,
                COUNT(DISTINCT sd.departure_id) AS sholinganallur_departure_count,
                COUNT(DISTINCT sd.stop_id) AS sholinganallur_stops_served
            FROM public.bus_stops bs
            JOIN public.scheduled_departures sd ON sd.stop_id = bs.stop_id
            JOIN public.route_services rs ON rs.route_id = sd.route_id
            JOIN public.routes r ON r.route_number = rs.route_number
            GROUP BY r.route_number, r.total_service_variants, r.total_daily_trips
            ORDER BY r.total_daily_trips DESC, r.route_number ASC
            LIMIT %s;
        """
        async with self.conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(sql, (limit,))
            return await cur.fetchall()

    async def get_stops_with_missing_coordinates(self) -> List[Dict[str, Any]]:
        """
        Retrieve bus stops with quarantined or null geographic coordinates in public.bus_stops.
        """
        sql = """
            SELECT
                stop_id,
                stop_code,
                stop_name,
                normalized_stop_name,
                latitude,
                longitude,
                coordinate_status,
                map_eligible
            FROM public.bus_stops
            WHERE latitude IS NULL OR longitude IS NULL
            ORDER BY stop_id ASC;
        """
        async with self.conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(sql)
            return await cur.fetchall()

    async def get_sholinganallur_departures_overview(
        self,
        stop_name: str = "SHOLINGANALLUR",
        after_time_seconds: Optional[int] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        Retrieve structured scheduled departures from stops matching the specified stop name.
        Supports filtering by after_time_seconds and preserves GTFS departures exceeding 24:00.
        """
        conditions = ["bs.stop_name = %s"]
        params: List[Any] = [stop_name]

        if after_time_seconds is not None:
            conditions.append("sd.departure_seconds >= %s")
            params.append(after_time_seconds)

        where_clause = " AND ".join(conditions)
        sql = f"""
            SELECT
                sd.departure_id,
                sd.trip_id,
                sd.stop_id,
                sd.stop_name,
                sd.route_number,
                sd.route_name,
                sd.departure_time,
                sd.departure_seconds
            FROM public.scheduled_departures sd
            JOIN public.bus_stops bs ON bs.stop_id = sd.stop_id
            WHERE {where_clause}
            ORDER BY sd.departure_seconds ASC, sd.trip_id ASC
            LIMIT %s;
        """
        params.append(limit)
        async with self.conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(sql, params)
            return await cur.fetchall()

