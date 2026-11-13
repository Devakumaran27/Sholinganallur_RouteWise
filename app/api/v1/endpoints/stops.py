"""
Module: app.api.v1.endpoints.stops
Purpose: REST API endpoints for discovering bus stops and routes serving each stop.
"""

from typing import Optional
from fastapi import APIRouter, Depends, Path, Query, status
import psycopg

from app.db.pool import get_db
from app.schemas.transit import BusStopSearchResponse, StopRoutesResponse
from app.services.transit_service import TransitService

router = APIRouter(prefix="/stops", tags=["Bus Stops"])


@router.get(
    "",
    summary="Search Sholinganallur bus stops",
    response_model=BusStopSearchResponse,
    status_code=status.HTTP_200_OK,
)
async def search_stops(
    query: Optional[str] = Query(
        None,
        description="Case-insensitive search string matching stop names (e.g. 'Sholinganallur')",
        min_length=1,
        max_length=100,
    ),
    map_eligible_only: bool = Query(
        False,
        description="If True, filters out stops with missing/placeholder coordinates",
    ),
    limit: int = Query(
        26,
        ge=1,
        le=50,
        description="Maximum number of stops to return (default 26, max 50)",
    ),
    conn: psycopg.AsyncConnection = Depends(get_db),
) -> BusStopSearchResponse:
    """
    Search bus stops located within the Sholinganallur transit corridor.
    - Case-insensitive partial matching across stop names.
    - Preserves null coordinates for stops with placeholder/unverified coordinates.
    - Set `map_eligible_only=true` to retrieve only stops with coordinates suitable for map rendering.
    """
    service = TransitService(conn)
    return await service.search_bus_stops(
        query=query,
        map_eligible_only=map_eligible_only,
        limit=limit,
    )


@router.get(
    "/{stop_id}/routes",
    summary="List routes serving a specific bus stop",
    response_model=StopRoutesResponse,
    status_code=status.HTTP_200_OK,
)
async def get_stop_routes(
    stop_id: str = Path(
        ...,
        description="Target MTC bus stop identifier (e.g. 'elxmPCDf')",
        min_length=1,
        max_length=64,
    ),
    conn: psycopg.AsyncConnection = Depends(get_db),
) -> StopRoutesResponse:
    """
    Retrieve all distinct operational public bus routes calling at the specified stop.
    Returns route line number, trajectory name, and operational departure span.
    Returns HTTP 404 if the stop does not exist in the Sholinganallur transit area.
    """
    service = TransitService(conn)
    return await service.get_routes_for_stop(stop_id)

