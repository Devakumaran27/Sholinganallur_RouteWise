"""
Module: app.services.chat_service
Purpose: Grounded question-answering service integrating semantic RAG retrieval,
         structured GTFS database queries, and Google Gemini text generation.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Dict, List, Optional

from google import genai
from google.genai import errors, types
import psycopg

from app.ai.prompts.transit_qa import TRANSIT_SYSTEM_INSTRUCTION, build_qa_prompt
from app.ai.rag.embeddings.embedding_service import (
    AuthenticationError,
    InvalidRequestError,
    RateLimitError,
    TransientServerError,
    sanitize_sensitive_data,
)
from app.core.config import settings
from app.db.pool import get_db_pool
from app.schemas.rag import ChatGroundingSource, ChatResponse
from app.services.rag_service import RAGService
from app.services.transit_service import TransitService

logger = logging.getLogger(__name__)


class ChatService:
    """
    Orchestrates grounded question-answering for public transit queries.
    Combines:
    1. Semantic pgvector retrieval over document knowledge chunks via RAGService.
    2. Exact structured GTFS database lookups via TransitService.
    3. Grounded generation via Google Gemini with strict anti-hallucination prompts.
    """

    def __init__(
        self,
        client: Optional[genai.Client] = None,
        rag_service: Optional[RAGService] = None,
        model_name: Optional[str] = None,
        api_key: Optional[str] = None,
    ) -> None:
        """
        Initialize ChatService with Google GenAI client and RAGService.
        """
        self.model_name = model_name or settings.GEMINI_CHAT_MODEL
        self.rag_service = rag_service or RAGService()

        if client is not None:
            self.client = client
        else:
            resolved_key = api_key if api_key is not None else settings.GEMINI_API_KEY
            if not resolved_key or not resolved_key.strip():
                raise AuthenticationError(
                    "GEMINI_API_KEY is not configured in settings or environment. "
                    "Cannot initialize Gemini chat service."
                )
            try:
                self.client = genai.Client(api_key=resolved_key)
            except Exception as exc:
                raise AuthenticationError(
                    f"Failed to initialize Google GenAI client: {exc}",
                    original_error=exc,
                )

    async def ask(
        self,
        conn: Optional[psycopg.AsyncConnection] = None,
        question: str = "",
        top_k: int = 4,
    ) -> ChatResponse:
        """
        Processes a passenger's transport question and produces a grounded answer.

        Args:
            conn: Optional active PostgreSQL connection. If None, acquires a pooled connection
                  strictly for the database retrieval phase and releases it before Gemini generation.
            question: Passenger's question in natural language.
            top_k: Maximum number of semantic RAG chunks to retrieve (1-10).

        Returns:
            ChatResponse: Grounded answer with grounding sources and structured data.
        """
        clean_q = question.strip()
        if len(clean_q) < 2:
            raise InvalidRequestError("Question must be at least 2 characters long.")

        # 1 & 2. Database retrieval phase: query semantic RAG chunks & structured GTFS data
        if conn is not None:
            # Caller provided an active connection (e.g. unit test with mock connection)
            rag_response = await self.rag_service.search(
                conn=conn,
                query=clean_q,
                top_k=top_k,
            )
            transit_service = TransitService(conn)
            structured_context = await self._fetch_structured_context(transit_service, clean_q)
            try:
                if not conn.autocommit and hasattr(conn, "info") and conn.info.transaction_status != 0:
                    await conn.commit()
            except Exception as exc:
                logger.debug("Non-critical commit check skipped: %s", exc)
        else:
            # Production: Acquire connection from pool strictly for DB retrieval,
            # and release it back to the pool immediately before calling Gemini!
            pool = get_db_pool()
            if pool is None:
                raise RuntimeError("Database connection pool is not initialized or unavailable.")
            async with pool.connection() as pooled_conn:
                rag_response = await self.rag_service.search(
                    conn=pooled_conn,
                    query=clean_q,
                    top_k=top_k,
                )
                transit_service = TransitService(pooled_conn)
                structured_context = await self._fetch_structured_context(transit_service, clean_q)

        # Database connection is now completely released back to the pool!
        # 3. Assemble prompt context
        user_prompt = build_qa_prompt(
            question=clean_q,
            rag_chunks=rag_response.chunks,
            structured_context=structured_context,
        )

        # 4. Invoke Gemini with strict system instruction (safe async offload, zero DB connections held!)
        answer = await self._generate_gemini_answer(user_prompt)

        # 5. Build grounding sources citation list
        grounding_sources = [
            ChatGroundingSource(
                chunk_id=c.chunk_id,
                document_id=c.document_id,
                title=c.title,
                similarity=c.similarity,
            )
            for c in rag_response.chunks
        ]

        # 6. Assemble standard operational limitations
        limitations = [
            "Static GTFS schedule feed only; does not provide real-time GPS vehicle tracking or live arrival delays.",
            "Trip travel times on the OMR corridor vary based on peak-hour Chennai traffic conditions.",
            "Stops outside verified Greater Chennai bounds have quarantined coordinates and are excluded from map rendering.",
        ]

        logger.info(
            "Chat completed: question='%s', sources=%d, structured_keys=%s",
            clean_q[:60],
            len(grounding_sources),
            list(structured_context.keys()) if structured_context else [],
        )

        return ChatResponse(
            question=clean_q,
            answer=answer,
            grounding_sources=grounding_sources,
            structured_context_used=structured_context if structured_context else None,
            limitations=limitations,
        )

    @staticmethod
    def _extract_time_filter(question: str) -> tuple[Optional[str], Optional[int]]:
        """
        Extracts explicit service time filters from natural language queries.
        Supports HH:MM:SS, HH:MM, and 12-hour AM/PM formats.
        """
        q = question.lower()
        # 1. 12-hour AM/PM format (e.g. '8 am', '8:00 am', '9:15 pm')
        m_ampm = re.search(r'\b([0-1]?\d)(?::([0-5]\d))?(?::([0-5]\d))?\s*(am|pm)\b', q)
        if m_ampm:
            h = int(m_ampm.group(1))
            m_val = int(m_ampm.group(2)) if m_ampm.group(2) else 0
            s_val = int(m_ampm.group(3)) if m_ampm.group(3) else 0
            ampm = m_ampm.group(4)
            if ampm == 'pm' and h < 12:
                h += 12
            elif ampm == 'am' and h == 12:
                h = 0
            return f"{h:02d}:{m_val:02d}:{s_val:02d}", h * 3600 + m_val * 60 + s_val

        # 2. 24-hour HH:MM:SS or HH:MM format (e.g. '08:00:00', '08:00', '8:00')
        m_24 = re.search(r'\b([0-2]?\d):([0-5]\d)(?::([0-5]\d))?\b', q)
        if m_24:
            h = int(m_24.group(1))
            m_val = int(m_24.group(2))
            s_val = int(m_24.group(3)) if m_24.group(3) else 0
            return f"{h:02d}:{m_val:02d}:{s_val:02d}", h * 3600 + m_val * 60 + s_val

        return None, None

    async def _fetch_structured_context(
        self,
        transit_service: TransitService,
        question: str,
    ) -> Dict[str, Any]:
        """
        Inspects query intent and retrieves relevant structured GTFS facts from PostgreSQL.
        """
        q_lower = question.lower()
        context: Dict[str, Any] = {}
        time_str, time_seconds = self._extract_time_filter(question)

        # Intent A: Direct Sholinganallur -> T. Nagar corridor journeys
        if any(k in q_lower for k in ("t nagar", "t. nagar", "t-nagar", "cit nagar", "direct")) or (
            time_str and any(k in q_lower for k in ("journey", "next bus", "bus to", "route 19"))
        ):
            try:
                direct_summary = await transit_service.get_direct_routes_summary()
                if direct_summary and isinstance(direct_summary, list):
                    context["verified_direct_routes_to_t_nagar"] = [
                        {
                            "route_number": r["route_number"],
                            "total_direct_trips": int(r["total_direct_trips"]),
                            "origin_stops": r["origin_stops"],
                            "destination": r["destination_stops"],
                            "operating_window": f"{r['earliest_departure']} to {r['latest_departure']}",
                            "duration_minutes": float(r["min_duration_minutes"]),
                            "intermediate_stops": int(r["intermediate_stops"]),
                        }
                        for r in direct_summary
                        if isinstance(r, dict) and "route_number" in r
                    ]
                if time_str:
                    direct_journeys = await transit_service.get_direct_journeys(after_time_str=time_str, limit=5)
                    if direct_journeys and direct_journeys.journeys:
                        context["verified_direct_journeys_to_t_nagar_after_time"] = [
                            {
                                "route_number": j.route_number,
                                "route_name": j.route_name,
                                "origin_stop_name": j.origin_stop_name,
                                "scheduled_departure": j.scheduled_departure,
                                "destination_stop_name": j.destination_stop_name,
                                "scheduled_arrival": j.scheduled_arrival,
                                "duration_minutes": j.duration_minutes,
                            }
                            for j in direct_journeys.journeys
                        ]
            except Exception as exc:
                logger.debug("Structured context lookup for direct journeys skipped: %s", exc)

        # Intent B: Routes serving Sholinganallur
        if any(k in q_lower for k in ("routes", "route numbers", "which bus", "bus lines", "line numbers", "buses serve")):
            try:
                serving_routes = await transit_service.get_sholinganallur_serving_routes(limit=58)
                if serving_routes and isinstance(serving_routes, list):
                    context["verified_sholinganallur_serving_routes"] = {
                        "total_verified_serving_routes": len(serving_routes),
                        "authoritative_route_list": [r["route_number"] for r in serving_routes if isinstance(r, dict) and "route_number" in r],
                        "top_frequent_lines": [
                            f"Line {r.get('route_number', '')} ({r.get('total_daily_trips', 0)} total trips, {r.get('sholinganallur_departure_count', 0)} departures at Sholinganallur stops)"
                            for r in serving_routes[:10]
                            if isinstance(r, dict) and "route_number" in r
                        ],
                    }
            except Exception as exc:
                logger.debug("Structured context lookup for serving routes skipped: %s", exc)

        # Intent C: Scheduled departures / Timetables
        if any(k in q_lower for k in ("time", "timing", "timetable", "schedule", "when", "departure", "frequency")) or time_str is not None:
            try:
                deps = await transit_service.get_sholinganallur_departures_overview(
                    stop_name="SHOLINGANALLUR",
                    after_time_seconds=time_seconds,
                    limit=10,
                )
                if deps and isinstance(deps, list):
                    context["verified_scheduled_departures_at_sholinganallur"] = [
                        {
                            "departure_id": d["departure_id"],
                            "trip_id": d["trip_id"],
                            "stop_id": d["stop_id"],
                            "stop_name": d["stop_name"],
                            "route_number": d["route_number"],
                            "route_name": d["route_name"],
                            "departure_time": d["departure_time"],
                        }
                        for d in deps
                        if isinstance(d, dict) and "departure_id" in d
                    ]
                if time_str and "verified_direct_journeys_to_t_nagar_after_time" not in context:
                    try:
                        direct_journeys = await transit_service.get_direct_journeys(after_time_str=time_str, limit=5)
                        if direct_journeys and direct_journeys.journeys:
                            context["verified_direct_journeys_to_t_nagar_after_time"] = [
                                {
                                    "route_number": j.route_number,
                                    "origin_stop_name": j.origin_stop_name,
                                    "destination_stop_name": j.destination_stop_name,
                                    "scheduled_departure": j.scheduled_departure,
                                    "duration_minutes": j.duration_minutes,
                                }
                                for j in direct_journeys.journeys
                            ]
                    except Exception as inner_exc:
                        logger.debug("Additional direct journeys lookup skipped: %s", inner_exc)
            except Exception as exc:
                logger.debug("Structured context lookup for departures skipped: %s", exc)

        # Intent D: Boarding locations / Bus stops
        if any(k in q_lower for k in ("board", "stops", "platform", "where", "location", "coordinate", "junction", "missing")):
            try:
                # Check for missing coordinates specifically
                if any(m in q_lower for m in ("missing", "coordinate", "quarantine", "null")):
                    missing_coords = await transit_service.get_stops_with_missing_coordinates()
                    if missing_coords and isinstance(missing_coords, list):
                        context["verified_stops_with_missing_coordinates"] = [
                            {
                                "stop_id": s["stop_id"],
                                "stop_name": s["stop_name"],
                                "latitude": s["latitude"],
                                "longitude": s["longitude"],
                                "coordinate_status": s["coordinate_status"],
                                "map_eligible": s["map_eligible"],
                            }
                            for s in missing_coords
                            if isinstance(s, dict) and "stop_id" in s
                        ]

                # General bus stops
                stops_resp = await transit_service.search_bus_stops(query="sholinganallur", limit=5)
                if stops_resp and stops_resp.stops:
                    context["sholinganallur_boarding_stops"] = [
                        f"{s.stop_name} (ID: {s.stop_id}, Map Eligible: {s.map_eligible}, Lat: {s.latitude}, Lon: {s.longitude})"
                        for s in stops_resp.stops
                    ]
            except Exception as exc:
                logger.debug("Structured context lookup for stops skipped: %s", exc)

        # Intent E: Live tracking / Real-time GPS status
        if any(k in q_lower for k in ("live", "gps", "real-time", "real time", "tracking", "track")):
            context["dataset_scope_live_tracking"] = {
                "dataset_type": "Static General Transit Feed Specification (GTFS) planned schedule feed",
                "real_time_gps_tracking_supported": False,
                "project_scope_disclaimer": "This project dataset contains static timetable schedules only and does not provide real-time GPS vehicle positions or live delay tracking.",
            }

        return context

    async def _generate_gemini_answer(self, prompt: str) -> str:
        """
        Executes Gemini generate_content asynchronously via thread offloading
        with typed error classification and non-blocking transient retries.
        """
        config_params = {
            "system_instruction": TRANSIT_SYSTEM_INSTRUCTION,
            "temperature": settings.GEMINI_CHAT_TEMPERATURE,
            "max_output_tokens": settings.GEMINI_CHAT_MAX_TOKENS,
        }
        if "lite" not in self.model_name.lower():
            try:
                config_params["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
            except Exception:
                pass

        config = types.GenerateContentConfig(**config_params)

        max_retries = settings.MAX_RETRY_ATTEMPTS
        for attempt in range(max_retries + 1):
            try:
                response = await asyncio.to_thread(
                    self.client.models.generate_content,
                    model=self.model_name,
                    contents=prompt,
                    config=config,
                )
                if not response or not response.text:
                    raise InvalidRequestError("Gemini returned an empty response.")
                return response.text.strip()
            except errors.APIError as exc:
                status_code = getattr(exc, "code", None)
                if status_code in (500, 502, 503, 504) and attempt < max_retries:
                    wait_time = 2.0 * (attempt + 1)
                    logger.warning(
                        "Transient Gemini %s error on attempt %d/%d. Retrying in %.1fs...",
                        status_code,
                        attempt + 1,
                        max_retries,
                        wait_time,
                    )
                    await asyncio.sleep(wait_time)
                    continue
                self._classify_and_raise_api_error(exc)
            except (TimeoutError, ConnectionError) as exc:
                if attempt < max_retries:
                    wait_time = 2.0 * (attempt + 1)
                    logger.warning(
                        "Network timeout on attempt %d/%d. Retrying in %.1fs...",
                        attempt + 1,
                        max_retries,
                        wait_time,
                    )
                    await asyncio.sleep(wait_time)
                    continue
                raise TransientServerError(f"Network transport timeout during chat generation: {exc}", original_error=exc)
            except Exception as exc:
                exc_str = str(exc).lower()
                if ("timeout" in exc_str or "connection" in exc_str) and attempt < max_retries:
                    wait_time = 2.0 * (attempt + 1)
                    logger.warning(
                        "Connection failure on attempt %d/%d. Retrying in %.1fs...",
                        attempt + 1,
                        max_retries,
                        wait_time,
                    )
                    await asyncio.sleep(wait_time)
                    continue
                raise

    def _classify_and_raise_api_error(self, exc: errors.APIError) -> None:
        """Classifies Google GenAI APIError into domain-specific exception types."""
        status_code = getattr(exc, "code", None)
        msg = f"Gemini API error during chat generation: {exc}"

        if status_code in (401, 403):
            raise AuthenticationError(msg, original_error=exc, status_code=status_code)
        elif status_code == 429:
            raise RateLimitError(msg, original_error=exc, status_code=status_code)
        elif status_code in (500, 502, 503, 504):
            raise TransientServerError(msg, original_error=exc, status_code=status_code)
        elif status_code in (400, 404):
            raise InvalidRequestError(msg, original_error=exc, status_code=status_code)
        else:
            raise TransientServerError(msg, original_error=exc, status_code=status_code)
