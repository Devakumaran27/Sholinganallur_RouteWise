"""
Script: scripts/verify_all_apis_step_5_5.py
Purpose: Complete live verification of all RouteWise backend endpoints for Step 5.5.
"""

import sys
import os
import asyncio
import json
from typing import Dict, Any

sys.path.insert(0, os.path.abspath("."))
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from fastapi.testclient import TestClient
from app.main import app

results: Dict[str, Any] = {}

def log_test(name: str, passed: bool, details: Dict[str, Any]):
    status_str = "PASS" if passed else "FAIL"
    print(f"[{status_str}] {name}")
    results[name] = {"passed": passed, "details": details}

with TestClient(app) as client:
    print("\n" + "="*70)
    print("ROUTEWISE AI - PHASE 5 STEP 5.5 FULL API INTEGRATION VERIFICATION")
    print("="*70 + "\n")

    # 1. GET /
    resp = client.get("/")
    p = resp.status_code == 200 and resp.json().get("status") == "online"
    log_test("1. GET / (Root)", p, {"status_code": resp.status_code, "body": resp.json()})

    # 2. GET /health
    resp = client.get("/health")
    p = resp.status_code == 200 and resp.json().get("status") == "healthy"
    log_test("2. GET /health (Health Check)", p, {"status_code": resp.status_code, "body": resp.json()})

    # 3. GET /api/database/health
    resp = client.get("/api/database/health")
    data = resp.json()
    p = resp.status_code == 200 and data.get("status") == "connected" and data.get("pgvector", {}).get("installed") is True
    # Verify no credentials in response
    no_creds = "password" not in resp.text.lower() and "devakumaran" not in resp.text.lower()
    log_test("3. GET /api/database/health (DB & pgvector)", p and no_creds, {
        "status_code": resp.status_code,
        "database": data.get("database"),
        "server_version": data.get("server_version"),
        "pgvector": data.get("pgvector"),
        "credentials_safe": no_creds,
    })

    # 4. GET /api/v1/stops
    # 4a. Valid search
    resp = client.get("/api/v1/stops?query=sholinganallur&limit=5")
    p = resp.status_code == 200 and resp.json().get("total") > 0
    log_test("4a. GET /api/v1/stops (Valid query)", p, {"status_code": resp.status_code, "total": resp.json().get("total")})

    # 4b. Map eligible only filter
    resp = client.get("/api/v1/stops?map_eligible_only=true&limit=50")
    stops = resp.json().get("stops", [])
    all_eligible = all(s.get("map_eligible") is True and s.get("latitude") is not None for s in stops)
    p = resp.status_code == 200 and all_eligible and len(stops) == 24  # 26 total - 2 quarantined = 24
    log_test("4b. GET /api/v1/stops (map_eligible_only=True)", p, {
        "status_code": resp.status_code,
        "count": len(stops),
        "all_eligible": all_eligible
    })

    # 4c. Empty search result
    resp = client.get("/api/v1/stops?query=NonExistentStopXYZ999")
    p = resp.status_code == 200 and resp.json().get("total") == 0 and resp.json().get("stops") == []
    log_test("4c. GET /api/v1/stops (Empty search)", p, {"status_code": resp.status_code, "total": resp.json().get("total")})

    # 4d. Validation error (limit > 50)
    resp = client.get("/api/v1/stops?limit=100")
    p = resp.status_code == 422
    log_test("4d. GET /api/v1/stops (Validation 422)", p, {"status_code": resp.status_code})

    # 5. GET /api/v1/stops/{stop_id}/routes
    # 5a. Valid stop ID
    resp = client.get("/api/v1/stops/380/routes")
    data = resp.json()
    p = resp.status_code == 200 and data.get("total_routes") > 0 and len(data.get("routes", [])) > 0
    log_test("5a. GET /api/v1/stops/{stop_id}/routes (Valid stop_id 380)", p, {
        "status_code": resp.status_code,
        "stop_name": data.get("stop_name"),
        "total_routes": data.get("total_routes")
    })

    # 5b. Invalid stop ID (404)
    resp = client.get("/api/v1/stops/INVALID_STOP_ID_99999/routes")
    p = resp.status_code == 404
    log_test("5b. GET /api/v1/stops/{stop_id}/routes (404 Not Found)", p, {"status_code": resp.status_code, "detail": resp.json().get("detail")})

    # 6. GET /api/v1/stops/{stop_id}/departures
    # 6a. Valid departures
    resp = client.get("/api/v1/stops/380/departures?limit=5")
    data = resp.json()
    p = resp.status_code == 200 and data.get("count") > 0 and len(data.get("departures", [])) > 0
    log_test("6a. GET /api/v1/stops/{stop_id}/departures (Valid stop_id 380)", p, {
        "status_code": resp.status_code,
        "stop_name": data.get("stop_name"),
        "count": data.get("count"),
        "sample_time": data.get("departures", [{}])[0].get("scheduled_departure")
    })

    # 6b. Departures filtered by route_number
    resp = client.get("/api/v1/stops/380/departures?route_number=99A&limit=5")
    data = resp.json()
    p = resp.status_code == 200 and all(d.get("route_number") == "99A" for d in data.get("departures", []))
    log_test("6b. GET /api/v1/stops/{stop_id}/departures (Filtered by route 99A)", p, {
        "status_code": resp.status_code,
        "count": data.get("count")
    })

    # 6c. Invalid stop ID (404)
    resp = client.get("/api/v1/stops/INVALID_STOP_ID_99999/departures")
    p = resp.status_code == 404
    log_test("6c. GET /api/v1/stops/{stop_id}/departures (404 Not Found)", p, {"status_code": resp.status_code})

    # 6d. Validation error (limit > 100)
    resp = client.get("/api/v1/stops/380/departures?limit=200")
    p = resp.status_code == 422
    log_test("6d. GET /api/v1/stops/{stop_id}/departures (Validation 422)", p, {"status_code": resp.status_code})

    # 7. GET /api/v1/journey/direct
    # 7a. Valid direct journeys
    resp = client.get("/api/v1/journey/direct?limit=5")
    data = resp.json()
    p = resp.status_code == 200 and data.get("total_options") > 0 and len(data.get("journeys", [])) > 0
    log_test("7a. GET /api/v1/journey/direct (Valid query)", p, {
        "status_code": resp.status_code,
        "corridor": data.get("corridor"),
        "total_options": data.get("total_options"),
        "sample_route": data.get("journeys", [{}])[0].get("route_number")
    })

    # 7b. Invalid origin stop ID (404)
    resp = client.get("/api/v1/journey/direct?origin_stop_id=NON_EXISTENT_STOP_999")
    p = resp.status_code == 404
    log_test("7b. GET /api/v1/journey/direct (404 Invalid origin_stop_id)", p, {"status_code": resp.status_code})

    # 7c. Filter with no results (total_options = 0)
    resp = client.get("/api/v1/journey/direct?after_time=25:59:00")
    p = resp.status_code == 200 and resp.json().get("total_options") == 0 and resp.json().get("journeys") == []
    log_test("7c. GET /api/v1/journey/direct (Empty after_time filter)", p, {"status_code": resp.status_code, "total_options": 0})

    # 8. POST /api/v1/rag/search
    # 8a. Valid RAG semantic search
    resp = client.post("/api/v1/rag/search", json={"query": "How do I reach T. Nagar?", "top_k": 3})
    data = resp.json()
    p = resp.status_code == 200 and data.get("retrieved_count") == 3 and len(data.get("chunks", [])) == 3
    # Check similarity bounds and chunk structure
    valid_chunks = all(-1.0 <= c.get("similarity", 0) <= 1.0 and len(c.get("chunk_id", "")) > 0 for c in data.get("chunks", []))
    log_test("8a. POST /api/v1/rag/search (Valid search)", p and valid_chunks, {
        "status_code": resp.status_code,
        "retrieved_count": data.get("retrieved_count"),
        "top_chunk": data.get("chunks", [{}])[0].get("chunk_id"),
        "top_similarity": data.get("chunks", [{}])[0].get("similarity")
    })

    # 8b. Validation error (empty query)
    resp = client.post("/api/v1/rag/search", json={"query": "", "top_k": 3})
    p = resp.status_code == 422
    log_test("8b. POST /api/v1/rag/search (Validation 422 empty query)", p, {"status_code": resp.status_code})

    # 9. POST /api/v1/chat/ask
    # 9a. Valid grounded question answering
    resp = client.post("/api/v1/chat/ask", json={"question": "Which bus connects Sholinganallur to T. Nagar directly?", "top_k": 3})
    data = resp.json()
    answer_text = data.get("answer", "")
    has_direct_routes = "19" in answer_text and "CIT NAGAR" in answer_text.upper()
    does_not_claim_102_as_direct = "102ct is a direct" not in answer_text.lower() and "direct bus routes to t. nagar" in answer_text.lower()
    p = resp.status_code == 200 and has_direct_routes and len(data.get("grounding_sources", [])) > 0
    log_test("9a. POST /api/v1/chat/ask (Valid grounded Q&A)", p, {
        "status_code": resp.status_code,
        "has_direct_routes": has_direct_routes,
        "sources_count": len(data.get("grounding_sources", [])),
        "structured_keys": list(data.get("structured_context_used", {}).keys()),
        "answer_preview": answer_text[:120] + "..."
    })

    # 9b. Validation error (question too short < 2 chars)
    resp = client.post("/api/v1/chat/ask", json={"question": "a", "top_k": 3})
    p = resp.status_code == 422
    log_test("9b. POST /api/v1/chat/ask (Validation 422 short question)", p, {"status_code": resp.status_code})

    # 10. Swagger / OpenAPI docs
    # 10a. GET /openapi.json
    resp = client.get("/openapi.json")
    data = resp.json()
    paths = list(data.get("paths", {}).keys())
    required_paths = [
        "/",
        "/health",
        "/api/database/health",
        "/api/v1/",
        "/api/v1/stops",
        "/api/v1/stops/{stop_id}/routes",
        "/api/v1/stops/{stop_id}/departures",
        "/api/v1/journey/direct",
        "/api/v1/rag/search",
        "/api/v1/chat/ask",
    ]
    all_paths_present = all(rp in paths for rp in required_paths)
    p = resp.status_code == 200 and all_paths_present
    log_test("10a. GET /openapi.json (Valid OpenAPI spec)", p, {
        "status_code": resp.status_code,
        "total_paths": len(paths),
        "all_required_present": all_paths_present,
    })

    # 10b. GET /docs
    resp = client.get("/docs")
    p = resp.status_code == 200 and "swagger" in resp.text.lower()
    log_test("10b. GET /docs (Swagger UI accessible)", p, {"status_code": resp.status_code})

    print("\n" + "="*70)
    passed_count = sum(1 for v in results.values() if v["passed"])
    total_count = len(results)
    print(f"VERIFICATION SUMMARY: {passed_count}/{total_count} PASSED")
    print("="*70 + "\n")

    if passed_count != total_count:
        sys.exit(1)
