"""
Module: app.schemas.transit
Purpose: Pydantic request and response models for public transit entities:
         bus stops, route services, scheduled departures, and direct corridor connections.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


# ==============================================================================
# Bus Stop Models
# ==============================================================================

class BusStopBase(BaseModel):
    """Core bus stop attributes."""
    stop_id: str = Field(..., description="Canonical MTC bus stop identifier")
    stop_code: str = Field(..., description="Bus stop operational code")
    stop_name: str = Field(..., description="Primary bus stop display name")
    normalized_stop_name: str = Field(..., description="Uppercase normalized stop name for search")
    latitude: Optional[float] = Field(None, description="WGS84 latitude coordinate (null if missing)")
    longitude: Optional[float] = Field(None, description="WGS84 longitude coordinate (null if missing)")
    map_eligible: bool = Field(True, description="True if verified non-placeholder coordinates exist")
    coordinate_status: str = Field("VERIFIED", description="Coordinate quality status: VERIFIED, APPROXIMATED, or PLACEHOLDER")

    model_config = ConfigDict(from_attributes=True)


class BusStopResponse(BusStopBase):
    """Detailed bus stop profile."""
    coordinates_within_chennai_bounds: bool = True
    locality_area_membership_verified: bool = False
    locality_area_membership_status: str = "LOCALITY_MEMBERSHIP_CONFIRMED"
    coordinate_source: str = "GTFS_EXTRACT"
    coordinate_verification_status: str = "VERIFIED_VALID"
    created_at: Optional[datetime] = None


class BusStopSearchResponse(BaseModel):
    """Response envelope for bus stop search and directory listings."""
    total: int = Field(..., description="Total matching bus stops found")
    query: Optional[str] = Field(None, description="Original search query parameter")
    stops: List[BusStopResponse] = Field(default_factory=list, description="List of matched bus stop profiles")


# ==============================================================================
# Route Service Models
# ==============================================================================

class RouteServiceBrief(BaseModel):
    """Operational route variant serving a stop."""
    route_number: str = Field(..., description="Public bus line number (e.g. 102, 19, 570)")
    route_id: str = Field(..., description="Canonical GTFS route identifier variant")
    route_name: str = Field(..., description="Full trajectory name (Origin To Destination)")
    direction_id: Optional[int] = Field(0, description="0 for Outbound, 1 for Inbound")
    earliest_departure: Optional[str] = Field(None, description="Earliest scheduled departure time string")
    latest_departure: Optional[str] = Field(None, description="Latest scheduled departure time string")

    model_config = ConfigDict(from_attributes=True)


class StopRoutesResponse(BaseModel):
    """Response envelope for all routes serving a specific stop."""
    stop_id: str = Field(..., description="Target bus stop identifier")
    stop_name: str = Field(..., description="Target bus stop display name")
    total_routes: int = Field(..., description="Number of distinct operational route services")
    routes: List[RouteServiceBrief] = Field(default_factory=list, description="Route variants serving this stop")


# ==============================================================================
# Scheduled Departure Models
# ==============================================================================

class ScheduledDepartureResponse(BaseModel):
    """Individual scheduled departure item from a bus stop timetable."""
    departure_id: Optional[int] = Field(None, description="Unique timetable departure ID")
    trip_id: str = Field(..., description="Canonical GTFS trip identifier")
    stop_sequence: int = Field(..., description="1-based sequence order of this stop on the trip")
    route_number: str = Field(..., description="Public bus line number")
    route_name: str = Field(..., description="Route trajectory name")
    stop_id: str = Field(..., description="Bus stop identifier")
    stop_name: str = Field(..., description="Bus stop display name")
    direction_id: int = Field(0, description="Direction code (0 or 1)")
    scheduled_departure: str = Field(..., description="Scheduled departure time (e.g. '08:35:00')")
    departure_seconds: int = Field(..., description="Seconds past midnight for sorting and window scans")
    minutes_until_departure: Optional[int] = Field(None, description="Estimated minutes from query time")

    model_config = ConfigDict(from_attributes=True)


class StopDeparturesResponse(BaseModel):
    """Response envelope for upcoming stop departures."""
    stop_id: str = Field(..., description="Target bus stop identifier")
    stop_name: str = Field(..., description="Target bus stop display name")
    query_time: str = Field(..., description="Base query time string (HH:MM:SS)")
    count: int = Field(..., description="Total departures returned")
    departures: List[ScheduledDepartureResponse] = Field(default_factory=list, description="Departures ordered by time")


# ==============================================================================
# Direct Connection Models (Sholinganallur -> T. Nagar)
# ==============================================================================

class DirectConnectionItem(BaseModel):
    """Direct journey run connecting Sholinganallur corridor to T. Nagar."""
    trip_id: str = Field(..., description="Canonical GTFS trip identifier")
    route_number: str = Field(..., description="Bus line number (e.g. '19')")
    route_name: str = Field(..., description="Full trajectory name")
    origin_stop_id: str = Field(..., description="Departure stop ID in Sholinganallur area")
    origin_stop_name: str = Field(..., description="Departure stop display name")
    scheduled_departure: str = Field(..., description="Scheduled departure time at origin")
    destination_stop_id: str = Field(..., description="Arrival stop ID in T. Nagar area")
    destination_stop_name: str = Field(..., description="Arrival stop display name")
    scheduled_arrival: str = Field(..., description="Scheduled arrival time at destination")
    scheduled_departure_seconds: int = Field(..., description="Departure time in seconds past midnight")
    scheduled_arrival_seconds: int = Field(..., description="Arrival time in seconds past midnight")
    duration_minutes: float = Field(..., description="Journey duration in minutes")
    intermediate_stop_count: int = Field(..., description="Number of intermediate stops between origin and destination")
    stops_between: Optional[List[Dict[str, Any]]] = Field(None, description="Ordered intermediate stop sequence")

    model_config = ConfigDict(from_attributes=True)


class DirectJourneyResponse(BaseModel):
    """Response envelope for direct Sholinganallur to T. Nagar connections."""
    corridor: str = Field("Sholinganallur -> T. Nagar", description="Corridor description")
    total_options: int = Field(..., description="Total direct journey options found")
    journeys: List[DirectConnectionItem] = Field(default_factory=list, description="Direct trip options ordered by departure")

