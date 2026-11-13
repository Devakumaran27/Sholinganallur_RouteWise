# Phase 6 — Step 6.4: Security and Production Readiness Audit

**Project:** Sholinganallur_RouteWise  
**Auditor:** Senior Python Backend & Security Engineer  
**Date:** November 9, 2026  
**Status:** COMPLETE (READ-ONLY AUDIT)  
**Test Suite Status:** 220/220 Tests Passing (100% Pass Rate)

---

## 1. Executive Summary

A comprehensive, read-only security and production readiness audit was conducted on the `Sholinganallur_RouteWise` FastAPI backend application. The system integrates Supabase PostgreSQL 17.11 with `pgvector 0.8.2`, Psycopg 3.3.8 asynchronous connection pooling, Google Gemini 3.5 Flash for grounded question answering, and Gemini Embedding 001 (768 dimensions) for semantic transit retrieval.

### Overall Assessment: Production-Ready with Minor Hardening Recommendations
The codebase demonstrates disciplined security hygiene and architectural maturity:
- **Zero hardcoded credentials or API keys** exist anywhere in source code or documentation.
- **Row-Level Security (RLS) is enabled on 100% (8 of 8) of public database tables**.
- **100% of database queries are strictly parameterized** via Psycopg tuple/dict bindings, eliminating SQL injection vectors.
- **Strict input validation** is enforced across all endpoints via Pydantic v2 schemas configured with `extra="forbid"` and explicit string length and numeric bounds.
- **Secrets masking** is implemented across logging, settings representations, and error handlers.
- **Zero critical or high-severity vulnerabilities** were identified. There are **zero blockers** preventing project submission.

A small number of low- and medium-severity operational hardening recommendations are cataloged below for transition from local/development staging to public production cloud deployment.

---

## 2. Findings Matrix & Classification

| ID | Category | Severity | Item / Component | Finding Summary | Recommended Action |
|:---|:---|:---:|:---|:---|:---|
| **SEC-01** | Secrets & Auth | **Clean** | `app/core/config.py` | No hardcoded secrets. Loaded from `.env`. Masked in logs and `__repr__`. | None. Clean. |
| **SEC-02** | Database / RLS | **Clean** | PostgreSQL Catalog | RLS enabled on all 8 public tables (`c.relrowsecurity = True`). | None. Clean. |
| **SEC-03** | Injection Defense | **Clean** | `TransitService`, `RAGService` | All SQL queries parameterized (`%s` and `%(vector)s`). Zero string interpolation. | None. Clean. |
| **SEC-04** | API Validation | **Clean** | `app/schemas/` | Pydantic v2 `extra="forbid"`, length caps (`1000` chars max), integer bounds (`ge`, `le`). | None. Clean. |
| **SEC-05** | CORS Policy | **Medium** | `app/main.py:63` | `allow_headers=["*"]` combined with `allow_credentials=True`. | Enumerate allowed headers explicitly for production. |
| **SEC-06** | Configuration | **Low** | `app/core/config.py:20` | `DEBUG = True` default setting in BaseSettings class. | Ensure `DEBUG=False` is set in production environment variables. |
| **SEC-07** | Information Exposure | **Low** | `app/routes/database.py:55` | `/api/database/health` exposes database name and PostgreSQL version. | Strip server version banner in production mode or protect endpoint. |
| **SEC-08** | API Docs Exposure | **Low** | `app/main.py:52-53` | Swagger `/docs` and `/redoc` exposed unconditionally. | Conditionally disable OpenAPI docs in public production environments. |
| **SEC-09** | Dependencies | **Low** | `requirements.txt` | Unpinned `>=` version constraints across dependencies. | Generate pinned `requirements.lock` for deterministic production builds. |

---

## 3. Detailed Audit by Category

### Category 1: API Key & Secret Protection
- **Target Files:** `app/core/config.py`, `app/core/logging.py`, `app/db/pool.py`, `app/ai/rag/embeddings/embedding_service.py`, `.env.example`, `.gitignore`.
- **Evidence & Findings:**
  1. **Source Code Cleanliness:** Automated regex scan searching for Google AI keys (`AIzaSy...`), database URLs (`postgresql://...`, `postgres://...`), tokens, and passwords revealed zero credentials in application source files or git history. Only mock strings in unit tests and standard placeholders in `.env.example` were present.
  2. **Secret Loading:** Credentials (`GEMINI_API_KEY`, `DATABASE_URL`) are read at runtime via Pydantic `BaseSettings` from environment variables or a local `.env` file (`SettingsConfigDict(env_file=".env")`).
  3. **Repository Exclusion:** The root `.gitignore` explicitly excludes `.env`, `.env.*`, `*.env`, temporary scratch scripts, and logs.
  4. **Log & Traceback Masking:**
     - `app/core/config.py:195-207`: `Settings.__repr__` and `__str__` intercept keys matching `(KEY|SECRET|PASSWORD|TOKEN|URL)` and replace their values with `"***REDACTED***"`.
     - `app/db/pool.py:32-47`: `mask_database_url` replaces passwords with `***REDACTED***` in all pool startup and connection logs.
     - `app/ai/rag/embeddings/embedding_service.py:73-83`: `sanitize_sensitive_data` strips API keys matching `AIza[0-9A-Za-z-_]{35}` from error logs and exception messages before propagation.

### Category 2: Cross-Origin Resource Sharing (CORS)
- **Target Files:** `app/main.py:58-64`, `app/core/config.py:36-41, 160-179`.
- **Evidence & Findings:**
  1. **Allowed Origins:** `settings.CORS_ORIGINS` defaults to local development origins:
     ```python
     ["http://localhost:3000", "http://127.0.0.1:3000", "http://localhost:8000", "http://127.0.0.1:8000"]
     ```
     Origins can be overridden via `CORS_ORIGINS` in `.env` either as a JSON list or comma-separated string.
  2. **Allowed Methods:** Explicitly restricted to `["GET", "POST", "PUT", "DELETE", "OPTIONS"]`.
  3. **Allowed Headers (SEC-05):** `allow_headers=["*"]` is specified alongside `allow_credentials=True`. According to the Fetch specification and modern web security standards, using a wildcard `*` with credentials enabled can cause browser compatibility inconsistencies and unnecessarily opens the API to unexpected custom headers.
  - **Minimal Recommended Fix (for production deployment):**
    ```python
    allow_headers=["Content-Type", "Authorization", "Accept", "Origin", "X-Requested-With"]
    ```

### Category 3: Database Security & Row-Level Security (RLS)
- **Target Files:** Database Catalog (`pg_class`, `pg_tables`), `app/services/transit_service.py`, `app/services/rag_service.py`.
- **Evidence & Findings:**
  1. **Row-Level Security Verification:** A direct inspection of PostgreSQL's system catalog (`pg_class.relrowsecurity`) confirms that RLS is active on **all 8 public transit and RAG tables**:
     - `public.bus_stops`: `RLS Enabled: True`
     - `public.dataset_ingestion_log`: `RLS Enabled: True`
     - `public.direct_connections`: `RLS Enabled: True`
     - `public.documents`: `RLS Enabled: True`
     - `public.rag_chunks`: `RLS Enabled: True`
     - `public.route_services`: `RLS Enabled: True`
     - `public.routes`: `RLS Enabled: True`
     - `public.scheduled_departures`: `RLS Enabled: True`
  2. **Service Role Architecture:** The backend communicates using the Supabase pooled service connection string, allowing trusted server-side reads/writes while protecting all tables from direct unauthenticated or unauthorized access via client-side Supabase keys (`anon` / `authenticated`).
  3. **SQL Injection Defense:** All queries across `TransitService` and `RAGService` utilize parameterized SQL placeholders (`%s` and `%(vector)s`). Zero instances of string concatenation, f-strings, or raw query formatting with user input were found.
     - Example from `TransitService.get_scheduled_departures`:
       ```python
       conditions.append("sd.departure_seconds >= %s")
       params.append(after_seconds)
       ...
       await cursor.execute(query, tuple(params))
       ```
     - Example from `RAGService.search`:
       ```python
       params = {"vector": query_embedding, "min_similarity": min_similarity, "top_k": top_k}
       await cursor.execute(query, params)
       ```

### Category 4: API Validation & Attack Surface
- **Target Files:** `app/schemas/rag.py`, `app/schemas/transit.py`, `app/api/v1/endpoints/`.
- **Evidence & Findings:**
  1. **Strict Payload Rejection:**
     - `ChatRequest` and `RAGSearchRequest` include `model_config = ConfigDict(extra="forbid")`. Any request containing extra or unexpected fields is immediately rejected with HTTP 422 Unprocessable Content.
  2. **Length & Value Constraints:**
     - `ChatRequest.question`: Enforces `min_length=2`, `max_length=1000`. Prevents prompt-stuffing Denial of Service.
     - `ChatRequest.top_k`: Enforces `ge=1`, `le=10` (default 4). Bounds vector retrieval memory usage.
     - `RAGSearchRequest.query`: Enforces `min_length=1`, `max_length=500`.
     - `RAGSearchRequest.top_k`: Enforces `ge=1`, `le=20` (default 5).
     - `/stops`: `limit` constrained to `ge=1`, `le=50`; query capped at `100` chars.
     - `/stops/{stop_id}/departures`: `stop_id` capped at `64` chars; `limit` constrained to `ge=1`, `le=100`.
     - `/journey/direct`: `limit` constrained to `ge=1`, `le=50`.
  3. **Time Parsing Hardening:**
     - Timetable departure parsing in `TransitService._parse_time_to_seconds` validates strict format `HH:MM:SS` (or `H:MM:SS`), verifies numeric bounds (minutes/seconds < 60), strips surrounding whitespace, and accommodates canonical GTFS service-day timestamps > 24:00 (e.g., `24:15:00`). Invalid formats raise `ValueError`, resulting in a clean HTTP 400 Bad Request.

### Category 5: Error Handling & Information Leakage
- **Target Files:** `app/main.py:68-100`, `app/routes/database.py:11-80`.
- **Evidence & Findings:**
  1. **Global Exception Handlers:**
     - `validation_exception_handler`: Catches `RequestValidationError` and returns HTTP 422 with structured field validation errors. Does not leak internal code logic or file paths.
     - `generic_exception_handler`: Catches all unhandled `Exception` instances, logs the stack trace internally (`logger.error(..., exc_info=True)`), and responds with a sanitized payload:
       ```json
       {
         "error": "Internal Server Error",
         "message": "An unexpected error occurred. Please try again later."
       }
       ```
       Internal stack traces, SQL errors, and database connection strings are never sent to the client.
  2. **Database Health Endpoint (SEC-07):**
     - `/api/database/health` reports connection status, database name (`postgres`), PostgreSQL server version (`PostgreSQL 17.11`), and pgvector extension version (`0.8.2`).
     - While highly valuable for development diagnostics, revealing exact database names and minor server versions in public production provides reconnaissance information to adversaries.
     - **Recommendation:** Restrict access to `/api/database/health` behind internal authentication, or omit server version details in production (`settings.ENVIRONMENT == "production"`).

### Category 6: Production Configuration & Server Hardening
- **Target Files:** `app/core/config.py:19-24`, `app/main.py:48-55`, `run.py`.
- **Evidence & Findings:**
  1. **Debug Mode (SEC-06):** `Settings.DEBUG` is defined with a default of `True`. In production, this should be explicitly set to `DEBUG=False` in environment configuration.
  2. **Interactive Documentation (SEC-08):** `FastAPI` instance defines `docs_url="/docs"` and `redoc_url="/redoc"` unconditionally. In public production systems, public access to interactive Swagger UI should be disabled or protected behind reverse-proxy authentication.
     - **Recommended configuration:**
       ```python
       app = FastAPI(
           title=settings.PROJECT_NAME,
           docs_url="/docs" if settings.DEBUG else None,
           redoc_url="/redoc" if settings.DEBUG else None,
           openapi_url="/openapi.json" if settings.DEBUG else None,
           lifespan=lifespan,
       )
       ```
  3. **Network Host Binding:** `Settings.HOST` defaults to `127.0.0.1` (safe loopback for development). In containerized cloud deployments (Docker, AWS ECS, GCP Cloud Run), host binding is configured via environment variables to `0.0.0.0` with the container port.

### Category 7: Dependencies & Supply Chain
- **Target Files:** `requirements.txt`, Python 3.13 Virtual Environment (`pip list`).
- **Evidence & Findings:**
  1. **Installed Key Packages:**
     - `fastapi`: 0.162.2
     - `uvicorn`: 0.54.0
     - `psycopg` / `psycopg-pool`: 3.3.8 / 3.3.3
     - `google-genai`: 3.0.0 (modern official SDK)
     - `pydantic` / `pydantic-settings`: 2.14.3 / 2.16.2
     - `supabase`: 2.34.1
     - `pgvector`: 0.5.1
  2. **Specification Format (SEC-09):** `requirements.txt` specifies minimum dependencies (`>=`) rather than locked versions:
     ```text
     fastapi>=0.110.0
     uvicorn[standard]>=0.28.0
     pydantic>=2.6.0
     pydantic-settings>=2.2.0
     python-dotenv>=1.0.1
     psycopg[binary]>=3.1.18
     psycopg-pool>=3.1.8
     pgvector>=0.2.5
     supabase>=2.34.0
     google-genai>=0.1.1
     httpx>=0.27.0
     ```
     For reproducible, immutable production deployments, generating a pinned `requirements.lock` file is recommended to prevent upstream supply-chain breakages.

---

## 4. Verification & Regression Testing

The complete project test suite was executed to verify that baseline functionality, transit routing, RAG retrieval, and grounded chat generation remain 100% operational:

```bash
python -m unittest discover -s tests -p "test_*.py"
```

**Results:**
- Total Tests: **220**
- Failures: **0**
- Errors: **0**
- Execution Duration: **54.082s**
- Status: **PASSED (OK)**

All test suites passed cleanly:
- `tests/test_transit_api.py` (Stops, routes, timetables, direct connections, `after_time` filtering)
- `tests/test_api_chat.py` (Grounded Q&A, mock Gemini integration, fallback modes, rate limiting, connection release)
- `tests/test_rag_pipeline.py` (Cosine similarity, pgvector query generation, metadata filtering)
- `tests/test_ingestion.py` (GTFS ingestion, RAG document batching, rollback transactions)
- `tests/test_db_pool.py` (Async pool acquisition, timeout, masking)

---

## 5. Submission Blockers Assessment

- **Blockers for Project Submission:** **0 (None)**
- **Critical Vulnerabilities:** **0**
- **High Vulnerabilities:** **0**
- **Medium Items:** **1** (CORS header wildcard — standard practice during development; simple hardening recommended before public production launch).
- **Low Items:** **4** (Production config defaults, server banner exposure, OpenAPI docs exposure, dependency locking).

**Conclusion:** The backend application meets all security and architectural requirements for Phase 6 Step 6.4. It is fully prepared for submission.

---

## 6. Strict Compliance Confirmation

In accordance with Phase 6 Step 6.4 instructions:
- This audit was performed **strictly in read-only mode**.
- **Zero source code files were edited or modified.**
- **Zero database records, schemas, or indexes were altered.**
- **Zero embeddings or datasets were re-ingested.**
- **Zero dependencies were installed, updated, or removed.**
- **Zero real API keys, passwords, or secrets are exposed in this report.**

