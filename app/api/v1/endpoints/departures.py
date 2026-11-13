"""
Module: app.api.v1.endpoints.departures
Purpose: REST API endpoint for scheduled stop departure timetable queries.
"""

from typing import Optional
from fastapi import APIRouter, Depends, Path, Query, status
import psycopg

from app.db.pool import get_db
from app.schemas.transit import StopDeparturesResponse
from app.services.transit_service import TransitService

router = APIRouter(prefix="/stops", tags=["Departures"])


@router.get(
    "/{stop_id}/departures",
    summary="Retrieve scheduled bus departures for a stop",
    response_model=StopDeparturesResponse,
    status_code=status.HTTP_200_OK,
)
async def get_stop_departures(
    stop_id: str = Path(
        ...,
        description="Target MTC bus stop identifier (e.g. 'elxmPCDf')",
        min_length=1,
        max_length=64,
    ),
    after_time: Optional[str] = Query(
        None,
        description="GTFS service-day departure time filter (e.g. '08:30:00' or '24:15:00')",
        min_length=4,
        max_length=10,
    ),
    route_number: Optional[str] = Query(
        None,
        description="Optional filter by public line number (e.g. '102', '19', '570')",
        min_length=1,
        max_length=16,
    ),
    limit: int = Query(
        20,
        ge=1,
        le=100,
        description="Maximum scheduled departures to return (default 20, max 100)",
    ),
    conn: psycopg.AsyncConnection = Depends(get_db),
) -> StopDeparturesResponse:
    """
    Retrieve static scheduled bus departures for a verified bus stop.

    **Important Notice:**
    - This endpoint returns **static scheduled timetable data** from the MTC GTFS dataset.
    - It does **not** provide live real-time GPS tracking or live bus arrival estimates (ETAs).
    - Queries and sorts strictly using integer seconds past midnight (`departure_seconds`).
    - Supports canonical GTFS service-day timestamps past midnight (e.g., `24:15:00`).
    - If `after_time` is omitted, returns the earliest scheduled departures for the stop.
    - Returns HTTP 404 if the specified stop ID is not found.
    """
    service = TransitService(conn)
    return await service.get_scheduled_departures(
        stop_id=stop_id,
        after_time_str=after_time,
        route_number=route_number,
        limit=limit,
    )

