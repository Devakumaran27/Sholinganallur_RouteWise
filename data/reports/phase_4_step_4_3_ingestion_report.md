# Phase 4 Step 4.3 — Structured GTFS Dataset Ingestion Report

**Status:** Completed & Validated (Dry-Run & Unit Tests Passed; Live DB Execution Held Behind Explicit Flag)  
**Date:** 2026-11-08  
**Target Environment:** Supabase PostgreSQL (Managed Postgres with Row-Level Security)  
**Ingestion Driver:** Psycopg 3 (`psycopg` v3.2.1) with binary/native extensions  
**Project:** Sholinganallur_RouteWise  

---

## 1. Executive Summary

Phase 4 Step 4.3 implements the production-grade structured GTFS dataset ingestion service and command-line interface (`scripts/ingest_gtfs.py`) for the Sholinganallur RouteWise corridor assistant.

The service reads the five processed JSON artifacts in `data/processed/`, derives the canonical `routes` entity, validates in-memory foreign key referential integrity and primary key uniqueness across all 18,971 records, and provides an idempotent, transactional ingestion pipeline into PostgreSQL.

### Key Highlights
- **Zero Schema Modifications:** Preserves the deployed 8-table migration schema (`database/migrations/001_initial_schema.sql`) and all RLS policies intact.
- **Strict FK Order:** Inserts records in topological foreign key dependency order:  
  `routes` (58) $\rightarrow$ `route_services` (278) $\rightarrow$ `bus_stops` (26) $\rightarrow$ `scheduled_departures` (18,439) $\rightarrow$ `direct_connections` (170).
- **Atomic Transaction & Idempotency:** Executes within a single transaction block (`with conn.transaction():`) with `ON CONFLICT (...) DO UPDATE`UPSERT clauses for all tables.
- **Batching & Efficiency:** Batches the 18,439 `scheduled_departures` records (default batch size: 2,000) using parameterized multi-row queries, preventing memory exhaustion and socket timeouts.
- **Type Safety & Data Fidelity:** Serializes intermediate stop lists directly using `psycopg.types.json.Jsonb`, casts durations to `Decimal`, and preserves GTFS >24:00 time notations (e.g., `24:15:00`).
- **Audit & Ingestion Logging:** Automatically records audit entries in `dataset_ingestion_log` with status tracking (`IN_PROGRESS` $\rightarrow$ `SUCCESS` or `FAILED`), record counts, composite SHA-256 data hash, and duration.
- **Safety Enforcement:** Default CLI behavior is a safe, non-destructive `--dry-run`. Real database writes strictly require the explicit `--confirm` / `--execute` flag. Credentials in logging are automatically redacted.

---

## 2. Ingestion Architecture & Data Flow

```
[data/processed/route_service_summary.json] (278 services)
       │
       ├──> aggregate by route_number ──────> routes (58 records)
       │                                         │ (FK)
       └──> route_service records ────────────> route_services (278 records)
                                                 │ (FK)
[data/processed/bus_stops.json] (26 stops) ────> bus_stops (26 records)
                                                 │ (FK)
[data/processed/scheduled_departures.json] ────> scheduled_departures (18,439 records, batched)
                                                 │ (FK)
[data/processed/direct_t_nagar_connections.json] direct_connections (170 records, Jsonb stops)
                                                 │
                                                 ▼
[data/processed/quarantined_records.json] ───> Preflight Integrity Check (2 quarantined stops)
                                                 │
                                                 ▼
                                        dataset_ingestion_log (Audit trail)
```

### 2.1 Entity Relationship & Topological Order

1. **`routes` (Level 0 - Root):**
   - **Source:** Aggregated dynamically from `route_service_summary.json`.
   - **Records:** 58 distinct route numbers (e.g., `19`, `102`, `119`, `570`, `AC-570`, `M19`).
   - **Derived Fields:** `total_service_variants`, `total_daily_trips`, `earliest_system_seconds`, `latest_system_seconds`.
   - **Conflict Target:** `(route_number)`.

2. **`route_services` (Level 1 - Depends on `routes`):**
   - **Source:** `data/processed/route_service_summary.json`.
   - **Records:** 278 route service variants.
   - **Foreign Key:** `route_number` $\rightarrow$ `routes(route_number)`.
   - **Conflict Target:** `(route_id)`.

3. **`bus_stops` (Level 0 - Master):**
   - **Source:** `data/processed/bus_stops.json`.
   - **Records:** 26 corridor stops (13 northbound, 13 southbound).
   - **Conflict Target:** `(stop_id)`.

4. **`scheduled_departures` (Level 2 - Depends on `route_services` & `bus_stops`):**
   - **Source:** `data/processed/scheduled_departures.json`.
   - **Records:** 18,439 stop departures.
   - **Foreign Keys:**
     - `route_id` $\rightarrow$ `route_services(route_id)`
     - `stop_id` $\rightarrow$ `bus_stops(stop_id)`
   - **Conflict Target:** `(trip_id, stop_sequence)`.
   - **Primary Key:** `departure_id BIGSERIAL PRIMARY KEY` (generated automatically by PostgreSQL sequence).

5. **`direct_connections` (Level 2 - Depends on `route_services`, `bus_stops`, & `destinations`):**
   - **Source:** `data/processed/direct_t_nagar_connections.json` (alias: `direct_connections.json`).
   - **Records:** 170 direct connection pairs to T. Nagar.
   - **Foreign Keys:**
     - `route_id` $\rightarrow$ `route_services(route_id)`
     - `origin_stop_id` $\rightarrow$ `bus_stops(stop_id)`
   - **Negative FK Constraint:** `destination_stop_id` has NO foreign key to `bus_stops` and must not reside within local corridor stops (allows external T. Nagar terminal stops).
   - **Primary Key:** `trip_id TEXT PRIMARY KEY`.
   - **Conflict Target:** `(trip_id)`.
   - **Idempotency & Key Identity:** The UPSERT clause strictly targets `ON CONFLICT (trip_id) DO UPDATE SET ...` updating only the 16 non-key columns (`route_id`, `route_number`, `route_name`, `origin_area_id`, `origin_area_name`, `origin_stop_id`, `origin_stop_name`, `scheduled_departure_at_origin`, `destination_stop_id`, `destination_stop_name`, `scheduled_arrival_at_t_nagar_stop`, `scheduled_departure_seconds`, `scheduled_arrival_seconds`, `duration_minutes`, `intermediate_stop_count`, `stops_between_origin_and_t_nagar`). Primary-key identity (`trip_id`) is strictly preserved and never mutated.
   - **Authentic GTFS Trip IDs:** Source `trip_id` strings (e.g., `419-ZE-DOWN-125026`) are inserted directly without modification or synthetic generation.
   - **Complex Types:** `stops_between_origin_and_t_nagar` mapped to `psycopg.types.json.Jsonb`. `duration_minutes` converted to `Decimal`.

### 2.2 Table Conflict Targets vs Approved Schema Keys

| Table Name | PostgreSQL Schema Key Definition | UPSERT Conflict Target | Key Identity Preserved? |
| :--- | :--- | :--- | :--- |
| `routes` | `route_number TEXT PRIMARY KEY` | `ON CONFLICT (route_number)` | Yes (`route_number` not updated) |
| `route_services` | `route_id TEXT PRIMARY KEY` | `ON CONFLICT (route_id)` | Yes (`route_id` not updated) |
| `bus_stops` | `stop_id TEXT PRIMARY KEY` | `ON CONFLICT (stop_id)` | Yes (`stop_id` not updated) |
| `scheduled_departures` | `CONSTRAINT uq_scheduled_departures_trip_seq UNIQUE (trip_id, stop_sequence)` | `ON CONFLICT (trip_id, stop_sequence)` | Yes (`departure_id`, `trip_id`, `stop_sequence` not updated) |
| `direct_connections` | `trip_id TEXT PRIMARY KEY` | `ON CONFLICT (trip_id)` | Yes (`trip_id` not updated) |

6. **`dataset_ingestion_log` (System Audit):**
   - Tracks each execution session with start/completion timestamps, total records loaded per table, dataset version hash, and terminal status (`IN_PROGRESS`, `SUCCESS`, `FAILED`).

---

## 3. Source File Verification & Cryptographic Manifest

The preflight validation pipeline verifies the presence and SHA-256 digest of every source dataset file prior to execution:

| Source File Name | Expected Records | Target Database Table | SHA-256 Checksum |
| :--- | :--- | :--- | :--- |
| `route_service_summary.json` | 278 (yields 58 routes) | `routes`, `route_services` | `999ebaf0147b44ea8612c1416fc3bdea82afe5a140693de6830957a18644e102` |
| `bus_stops.json` | 26 | `bus_stops` | `f2757ca59c42af2905451d00167218196b5dc26a8deddc75c865a2f4d2c54be6` |
| `scheduled_departures.json` | 18,439 | `scheduled_departures` | `339825601cb61cc36f5cdfbb3e5a23b59e0ebe020f4c8a8b3346ad2cfcc4ca8e` |
| `direct_t_nagar_connections.json` | 170 | `direct_connections` | `a8e129e8c5561d5eaf19d11144d91b1c9450482a61729e032441f264e6d1d48d` |
| `quarantined_records.json` | 2 | *(Corridor Exclusions)* | `2277334e8760cd578932c10f4c6efcce00aec64bcac4da165946d6cf3838a1ac` |
| `dataset_metadata.json` | Metadata | *(Metadata validation)* | `8696c3de1a364b189becd24103feb26817b20585f30110e33485c43e3a46933d` |

**Composite Data Checksum:** `ce7e238e62352893d485c1454cc64bf0655ec9fb595c453d58068a260df3b773`  
**Total Source Records to Ingest:** 18,971 across 5 relational tables.

---

## 4. Preflight Validation & Dry-Run Execution Results

Running the CLI in default mode executes the complete preflight pipeline without establishing write transactions or executing live DB writes:

```text
$ python scripts/ingest_gtfs.py --dry-run
2026-11-08 22:26:51 [INFO] ingest_gtfs_cli: Target Database: postgresql://postgres.vaefuoisfjhfmtzixdie:***REDACTED***@aws-0-ap-southeast-1.pooler.supabase.com:5432/postgres
2026-11-08 22:26:51 [INFO] ingest_gtfs_cli: Source Data Directory: C:\Users\Kerthika M\Documents\AI Project\Sholinganallur_RouteWise\data\processed
2026-11-08 22:26:51 [INFO] ingest_gtfs_cli: Executing dry-run preflight validation...

======================================================================
ROUTEWISE GTFS INGESTION PREFLIGHT REPORT (DRY-RUN)
======================================================================
Data Directory:       C:\Users\Kerthika M\Documents\AI Project\Sholinganallur_RouteWise\data\processed
Composite SHA-256:    ce7e238e62352893d485c1454cc64bf0655ec9fb595c453d58068a260df3b773
Quarantined Stops:    2
----------------------------------------------------------------------
Entity                    Expected     Found        Status
----------------------------------------------------------------------
routes                    58           58           MATCH [OK]
route_services            278          278          MATCH [OK]
bus_stops                 26           26           MATCH [OK]
scheduled_departures      18439        18439        MATCH [OK]
direct_connections        170          170          MATCH [OK]
----------------------------------------------------------------------
Foreign-Key Integrity: PASSED [OK]
Primary-Key Uniqueness: PASSED [OK]
======================================================================

[PASS] All preflight checks passed successfully.
Safety Notice: Dry-run mode completed. No database writes were performed.
To execute live database ingestion, run with: python scripts/ingest_gtfs.py --confirm
```

---

## 5. Automated Unit Test Verification

A comprehensive automated test suite was developed in `tests/test_gtfs_ingestion.py` using mocked database connections and isolated mock datasets.

All 17 tests executed and passed cleanly:

| Test Case | Scope / Assertion | Result |
| :--- | :--- | :--- |
| `test_01_preflight_passes_on_actual_processed_data` | Validates actual 18,971 records against all constraints and schemas | PASSED |
| `test_02_derive_routes_aggregates_correctly` | Verifies route grouping, trip counts, min/max time derivation | PASSED |
| `test_03_preflight_detects_missing_file` | Asserts `FileNotFoundError` when a required JSON artifact is absent | PASSED |
| `test_04_preflight_detects_count_mismatch` | Asserts `ValueError` when record count deviates from specification | PASSED |
| `test_05_preflight_detects_dangling_foreign_key` | Asserts `ValueError` when departure references non-existent stop/route | PASSED |
| `test_06_preflight_detects_negative_constraint_violation` | Asserts `ValueError` when destination stop is in local corridor | PASSED |
| `test_07_ingest_all_executes_in_fk_dependency_order` | Confirms strict execution order: routes $\rightarrow$ services $\rightarrow$ stops $\rightarrow$ departures $\rightarrow$ connections | PASSED |
| `test_08_ingest_all_uses_parameterized_queries` | Confirms `%s` parameterization on every query; zero raw string interpolation | PASSED |
| `test_09_ingest_all_idempotency_on_conflict_clauses` | Confirms `ON CONFLICT` clauses present on all 5 insert statements | PASSED |
| `test_10_conflict_targets_match_schema_pks_and_preserve_key_identity` | Confirms exact conflict targets: `routes(route_number)`, `route_services(route_id)`, `bus_stops(stop_id)`, `scheduled_departures(trip_id, stop_sequence)`, and `direct_connections(trip_id)`. Confirms no PKs are altered in DO UPDATE SET. | PASSED |
| `test_11_direct_connections_trip_ids_are_authentic_source_gtfs` | Confirms `direct_connections` inserted `trip_id` values match source GTFS trip IDs exactly (unaltered, non-synthetic) | PASSED |
| `test_12_ingest_all_rolls_back_on_error` | Simulates DB failure; verifies `ROLLBACK`, exception raising, and error logging | PASSED |
| `test_13_ingest_all_uses_jsonb_for_intermediate_stops` | Confirms `Jsonb` wrapping for `stops_between_origin_and_t_nagar` | PASSED |
| `test_14_ingest_all_preserves_gtfs_greater_than_24h_times` | Confirms times like `24:15:00` are preserved as string time representations | PASSED |
| `test_15_ingest_all_converts_decimal_fields` | Confirms `duration_minutes` parsed to `Decimal` | PASSED |
| `test_16_verify_database_reports_discrepancies` | Verifies count check logic flags under-populated or mismatched tables | PASSED |
| `test_17_verify_database_passes_on_matching_counts` | Verifies clean pass when DB counts match expected 18,971 records | PASSED |

### Complete Repository Regression Test
- **Total Test Suite:** 89 tests (`tests/test_*.py`)
- **Execution Result:** `89 passed in 13.02s` (0 failures, 0 errors, 0 regressions)

---

## 6. CLI Usage Instructions

The CLI script `scripts/ingest_gtfs.py` provides three primary operational modes:

### 1. Dry-Run Mode (Default / Read-Only Preflight)
Performs all file checks, schema derivations, primary key uniqueness verifications, and referential integrity validations without connecting or writing to the database:
```bash
python scripts/ingest_gtfs.py --dry-run
```
*(Alternatively: running `python scripts/ingest_gtfs.py` without flags defaults safely to dry-run mode).*

### 2. Live Ingestion Mode (Requires Explicit Confirmation)
Executes atomic transaction ingestion against the configured Supabase PostgreSQL database:
```bash
python scripts/ingest_gtfs.py --confirm
```
*Optional parameters:*
- `--batch-size <N>`: Adjust batch size for departures (default: `2000`).
- `--data-dir <PATH>`: Custom directory for processed JSON files (default: `data/processed`).

### 3. Post-Ingestion Verification Mode
Queries the live Supabase tables to verify table row counts and inspect the latest ingestion log entry:
```bash
python scripts/ingest_gtfs.py --verify
```

---

## 7. Safety, Security & Compliance Audit

1. **Credential Protection:**  
   The database connection string in `.env` is loaded strictly via environment variables. The CLI script and logging utilities automatically mask credentials (`postgresql://postgres...:***REDACTED***@...`) in all console and file outputs.
2. **Read-Only Gate:**  
   In compliance with Phase 4 safety boundaries, no writes were executed against the remote Supabase database during this step.
3. **Transactional Integrity:**  
   All DML queries run within `with conn.transaction():`. Any database exception triggers an immediate rollback, preventing partial or corrupt states.
4. **Injection Prevention:**  
   All SQL statements use Psycopg 3's native client-side parameter binding `%s`. No user inputs or raw JSON contents are ever concatenated directly into SQL text.
5. **No AI API Calls:**  
   Zero Gemini API or embedding generation calls were performed during this phase.

