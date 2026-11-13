"""
Module: app.api.v1.endpoints.journey
Purpose: REST API endpoint for direct Sholinganallur-to-T. Nagar transit journeys.
"""

from typing import Optional
from fastapi import APIRouter, Depends, Query, status
import psycopg

from app.db.pool import get_db
from app.schemas.transit import DirectJourneyResponse
from app.services.transit_service import TransitService

router = APIRouter(prefix="/journey", tags=["Journeys"])


@router.get(
    "/direct",
    summary="Direct Sholinganallur to T. Nagar journeys",
    response_model=DirectJourneyResponse,
    status_code=status.HTTP_200_OK,
)
async def get_direct_journeys(
    origin_stop_id: Optional[str] = Query(
        None,
        description="Filter journeys starting from a specific Sholinganallur bus stop (e.g. 'qQJgvOFz')",
        min_length=1,
        max_length=64,
    ),
    after_time: Optional[str] = Query(
        None,
        description="Optional departure time filter (e.g. '08:00:00' or '17:30:00')",
        min_length=4,
        max_length=16,
    ),
    limit: int = Query(
        10,
        ge=1,
        le=50,
        description="Maximum journey options to return (default 10, max 50)",
    ),
    conn: psycopg.AsyncConnection = Depends(get_db),
) -> DirectJourneyResponse:
    """
    Retrieve direct bus runs connecting the Sholinganallur transit corridor to T. Nagar (CIT Nagar).

    **Features & Specifications:**
    - Queries verified direct runs from `public.direct_connections` (such as Route 19).
    - Preserves external destination stop details in T. Nagar without invalid foreign-key assumptions.
    - If `origin_stop_id` is supplied, validates that the stop exists (returns HTTP 404 if invalid).
    - If `after_time` is supplied, filters trips departing at or after that time on the service day.
    - Returns scheduled departure, arrival, journey duration, and ordered intermediate stop sequences.
    - Returns an empty list (`total_options: 0`) if no direct connections match the criteria.
    """
    service = TransitService(conn)
    return await service.get_direct_journeys(
        origin_stop_id=origin_stop_id,
        after_time_str=after_time,
        limit=limit,
    )

