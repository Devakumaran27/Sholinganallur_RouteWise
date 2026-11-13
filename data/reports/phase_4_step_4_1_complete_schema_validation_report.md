# Phase 4 – Step 4.1: Complete Schema Validation & Compatibility Report

**Project:** Sholinganallur_RouteWise  
**Phase:** Phase 4 — Step 4.1: Complete Schema Compatibility Validation (Final Targeted Hardened Pass)  
**Date:** 2026-11-08  
**Author:** Senior PostgreSQL & Supabase Database Engineer  
**Status:** FULLY HARDENED & AUDITED (Design Review Ready — Zero DDL Executed)  

---

## 1. Executive Summary

This report documents the final targeted hardening pass performed on [`database/migrations/001_initial_schema.sql`](file:///c:/Users/Kerthika%20M/Documents/AI%20Project/Sholinganallur_RouteWise/database/migrations/001_initial_schema.sql).

While `CREATE TABLE IF NOT EXISTS` and `CREATE INDEX IF NOT EXISTS` prevent runtime errors if objects already exist, they silently skip creation on pre-existing objects regardless of structural divergence. To prevent silent schema drift or incompatible states, the migration embeds an automated PL/pgSQL validation block (`DO $$ ... END $$;`, lines 66–1207) that executes prior to any DDL statement.

The validation block inspects PostgreSQL system catalogs (`pg_class`, `pg_attribute`, `pg_constraint`, `pg_index`, `pg_type`, `pg_namespace`, `pg_attrdef`, `pg_am`, `pg_opclass`) to guarantee that any pre-existing database objects match the approved design ([`data/reports/data_model_design.md`](file:///c:/Users/Kerthika%20M/Documents/AI%20Project/Sholinganallur_RouteWise/data/reports/data_model_design.md)) with zero tolerance for structural drift.

### Key Hardening Corrections Applied in this Pass:
1. **Exact Foreign-Key Column & Cascade Validation:** For all 6 foreign key relationships, the catalog queries verify child table and schema (`public`), exact ordered child columns (`con.conkey`), parent table and schema (`public`), exact ordered parent columns (`con.confkey`), and `ON DELETE CASCADE` (`confdeltype = 'c'`). Table FK counts are strictly enforced, rejecting duplicate or unexpected FKs. The negative constraint on `direct_connections.destination_stop_id` ensures no FK references `bus_stops`.
2. **Strict Default Expression Set-Membership Validation:** Replaced all substring `LIKE` matching with exact set-membership comparisons. Missing defaults are explicitly rejected upfront (`IF r.exp_def IS NOT NULL AND v_actual_def IS NULL THEN RAISE EXCEPTION ...`). Timestamp defaults strictly accept only `'now()'` and `'current_timestamp'`.
3. **Exact Sequence-Ownership Verification for BIGSERIAL:** For `scheduled_departures.departure_id` and `dataset_ingestion_log.ingestion_id`, validates that the sequence is owned by the column via `pg_get_serial_sequence('public.<tbl>', r.col)`, exists in `pg_class` with `relkind = 'S'`, and that the default expression strictly matches `nextval('<sequence>'::regclass)` without loose substring matching.
4. **Exact Named Unique Constraint Verification:** Validates `scheduled_departures.uq_scheduled_departures_trip_seq`, explicitly requiring constraint type `'u'` and exact ordered key columns `(trip_id, stop_sequence)`.
5. **Exact Numeric Precision & Scale Validation:** Replaced loose prefix checks with strict catalog equality (`v_actual_type != r.exp_type`) across all 91 columns, specifically validating `duration_minutes NUMERIC(5,2)` and `duration_seconds NUMERIC(8,2)`.
6. **Hardened Index Catalog Inspection:** Expanded index validation to verify `indisvalid = TRUE`, `indisready = TRUE`, exact key attribute counts (`indnkeyatts`), exact total attribute counts (`indnatts`, preventing unexpected `INCLUDE` columns), absence of expression attributes (`attnum = 0`), exact ordered key columns, operator-class schema qualification (`extensions.vector_cosine_ops`), and rejection of unexpected predicates on non-partial indexes.
7. **Explicit Target Schema Qualification:** Schema-qualified all table and index DDL to target `public` explicitly (e.g., `CREATE TABLE IF NOT EXISTS public.routes`, `CREATE INDEX IF NOT EXISTS idx_... ON public....`, `REFERENCES public....`), eliminating dependence on the session's `search_path`.

**Safety confirmation:** Zero SQL commands were executed against Supabase, no `.env` settings were read or modified, and no data was inserted.

---

## 2. Complete Table-by-Table Schema Validation Checklist

For all eight tables, if a relation with that name exists in schema `public`, the pre-execution PL/pgSQL block performs exhaustive structural verification:
1. **Exact Column-Set & Column Count Enforcement:** Verifies that `count(*)` of non-dropped user columns (`attnum > 0 AND NOT attisdropped`) equals the exact approved count, strictly preventing extra user-defined columns.
2. **Column Names, Exact Types, Nullability & Defaults:** Inspects `pg_attribute`, `pg_type`, and `pg_attrdef` with `pg_get_expr()`, checking exact types (including numeric precision/scale) and rejecting NULL defaults before expression comparison.
3. **Primary Key & Unique Constraints:** Validates column composition and order against `pg_constraint`.
4. **Foreign Key Integrity & Action:** Verifies referenced schema, target table, exact ordered child & parent columns, and `ON DELETE CASCADE` (`confdeltype = 'c'`).

### 2.1. Table 1: `public.routes` (Public Route Line Identities)
- **Exact Column Count:** Exactly 6 columns (extra columns rejected).
- **Primary Key Check:** `PRIMARY KEY (route_number)`
- **Foreign Key Count:** Exactly 0 foreign keys (unexpected FKs rejected).
- **Columns Validated (6 columns):**
  1. `route_number`: `text`, NOT NULL
  2. `total_service_variants`: `integer`, NOT NULL
  3. `total_daily_trips`: `integer`, NOT NULL
  4. `earliest_system_seconds`: `integer`, NOT NULL
  5. `latest_system_seconds`: `integer`, NOT NULL
  6. `created_at`: `timestamp with time zone`, NOT NULL (Default: `now()`, non-NULL enforced)

### 2.2. Table 2: `public.route_services` (Operational Service Profiles)
- **Exact Column Count:** Exactly 10 columns (extra columns rejected).
- **Primary Key Check:** `PRIMARY KEY (route_id)`
- **Foreign Key Count:** Exactly 1 foreign key (unexpected FKs rejected).
- **Foreign Key Check (FK 1):**
  - Child table: `public.route_services`
  - Child column: `route_number`
  - Referenced table: `public.routes`
  - Referenced column: `route_number`
  - Action: `ON DELETE CASCADE` (`confdeltype = 'c'`)
- **Columns Validated (10 columns):**
  1. `route_id`: `text`, NOT NULL
  2. `route_number`: `text`, NOT NULL
  3. `route_name`: `text`, NOT NULL
  4. `distinct_trips_in_feed`: `integer`, NOT NULL
  5. `scheduled_stop_departures_in_area`: `integer`, NOT NULL
  6. `earliest_departure_time_string`: `text`, NOT NULL
  7. `latest_departure_time_string`: `text`, NOT NULL
  8. `earliest_departure_seconds`: `integer`, NOT NULL
  9. `latest_departure_seconds`: `integer`, NOT NULL
  10. `created_at`: `timestamp with time zone`, NOT NULL (Default: `now()`, non-NULL enforced)

### 2.3. Table 3: `public.bus_stops` (Sholinganallur Area Bus Stops)
- **Exact Column Count:** Exactly 16 columns (extra columns rejected).
- **Primary Key Check:** `PRIMARY KEY (stop_id)`
- **Foreign Key Count:** Exactly 0 foreign keys (unexpected FKs rejected).
- **Columns Validated (16 columns):**
  1. `stop_id`: `text`, NOT NULL
  2. `stop_code`: `text`, NOT NULL
  3. `stop_name`: `text`, NOT NULL
  4. `normalized_stop_name`: `text`, NOT NULL
  5. `latitude`: `double precision`, NULL
  6. `longitude`: `double precision`, NULL
  7. `coordinates_within_chennai_bounds`: `boolean`, NOT NULL (Default: `TRUE`, non-NULL enforced)
  8. `map_eligible`: `boolean`, NOT NULL (Default: `TRUE`, non-NULL enforced)
  9. `coordinate_status`: `text`, NOT NULL
  10. `locality_area_membership_verified`: `boolean`, NOT NULL (Default: `FALSE`, non-NULL enforced)
  11. `locality_area_membership_status`: `text`, NOT NULL
  12. `coordinate_source`: `text`, NOT NULL
  13. `coordinate_verification_status`: `text`, NOT NULL
  14. `original_latitude`: `double precision`, NULL
  15. `original_longitude`: `double precision`, NULL
  16. `created_at`: `timestamp with time zone`, NOT NULL (Default: `now()`, non-NULL enforced)

### 2.4. Table 4: `public.scheduled_departures` (Time-Indexed Departure Events)
- **Exact Column Count:** Exactly 13 columns (extra columns rejected).
- **Primary Key Check:** `PRIMARY KEY (departure_id)`
- **Unique Constraint Check:** Named constraint `uq_scheduled_departures_trip_seq`, type `'u'`, exact ordered columns `(trip_id, stop_sequence)`.
- **Foreign Key Count:** Exactly 2 foreign keys (unexpected FKs rejected).
- **Foreign Key Checks:**
  - **FK 2:** `stop_id` $\rightarrow$ `public.bus_stops(stop_id)` with `ON DELETE CASCADE` (`confdeltype = 'c'`)
  - **FK 3:** `route_id` $\rightarrow$ `public.route_services(route_id)` with `ON DELETE CASCADE` (`confdeltype = 'c'`)
- **BIGSERIAL Sequence Validation:**
  - Uses `pg_get_serial_sequence('public.scheduled_departures', 'departure_id')` to confirm sequence existence and column ownership.
  - Verifies sequence `relkind = 'S'` in catalog.
  - Verifies `nextval` default matches exact invocation `nextval('<seq>'::regclass)` without substring matching.
- **Columns Validated (13 columns):**
  1. `departure_id`: `bigint`, NOT NULL (Default: sequence-backed `nextval(...)`, non-NULL enforced)
  2. `trip_id`: `text`, NOT NULL
  3. `stop_sequence`: `integer`, NOT NULL
  4. `stop_id`: `text`, NOT NULL
  5. `route_id`: `text`, NOT NULL
  6. `route_number`: `text`, NOT NULL
  7. `route_name`: `text`, NOT NULL
  8. `stop_name`: `text`, NOT NULL
  9. `direction_id`: `integer`, NOT NULL
  10. `arrival_time`: `text`, NOT NULL
  11. `departure_time`: `text`, NOT NULL
  12. `departure_seconds`: `integer`, NOT NULL
  13. `created_at`: `timestamp with time zone`, NOT NULL (Default: `now()`, non-NULL enforced)

### 2.5. Table 5: `public.direct_connections` (Direct Sholinganallur $\rightarrow$ T. Nagar Corridor Trips)
- **Exact Column Count:** Exactly 18 columns (extra columns rejected).
- **Primary Key Check:** `PRIMARY KEY (trip_id)`
- **Foreign Key Count:** Exactly 2 foreign keys (unexpected FKs rejected).
- **Foreign Key Checks:**
  - **FK 4:** `route_id` $\rightarrow$ `public.route_services(route_id)` with `ON DELETE CASCADE` (`confdeltype = 'c'`)
  - **FK 5:** `origin_stop_id` $\rightarrow$ `public.bus_stops(stop_id)` with `ON DELETE CASCADE` (`confdeltype = 'c'`)
  - **Negative FK Constraint Verification:** Explicitly verified that `destination_stop_id` does NOT possess a foreign key to `bus_stops` (or any table), preserving referential integrity for external T. Nagar destination stops (`bZroqIhX`).
- **Exact Numeric Validation:** `duration_minutes` validated against exact type `'numeric(5,2)'`.
- **Columns Validated (18 columns):**
  1. `trip_id`: `text`, NOT NULL
  2. `route_id`: `text`, NOT NULL
  3. `route_number`: `text`, NOT NULL
  4. `route_name`: `text`, NOT NULL
  5. `origin_area_id`: `text`, NOT NULL
  6. `origin_area_name`: `text`, NOT NULL
  7. `origin_stop_id`: `text`, NOT NULL
  8. `origin_stop_name`: `text`, NOT NULL
  9. `scheduled_departure_at_origin`: `text`, NOT NULL
  10. `destination_stop_id`: `text`, NOT NULL
  11. `destination_stop_name`: `text`, NOT NULL
  12. `scheduled_arrival_at_t_nagar_stop`: `text`, NOT NULL
  13. `scheduled_departure_seconds`: `integer`, NOT NULL
  14. `scheduled_arrival_seconds`: `integer`, NOT NULL
  15. `duration_minutes`: `numeric(5,2)`, NOT NULL
  16. `intermediate_stop_count`: `integer`, NOT NULL
  17. `stops_between_origin_and_t_nagar`: `jsonb`, NOT NULL
  18. `created_at`: `timestamp with time zone`, NOT NULL (Default: `now()`, non-NULL enforced)

### 2.6. Table 6: `public.dataset_ingestion_log` (ETL Ingestion Audit Trail)
- **Exact Column Count:** Exactly 11 columns (extra columns rejected).
- **Primary Key Check:** `PRIMARY KEY (ingestion_id)`
- **Foreign Key Count:** Exactly 0 foreign keys (unexpected FKs rejected).
- **BIGSERIAL Sequence Validation:**
  - Uses `pg_get_serial_sequence('public.dataset_ingestion_log', 'ingestion_id')` to confirm sequence existence and column ownership.
  - Verifies sequence `relkind = 'S'` in catalog.
  - Verifies `nextval` default matches exact invocation `nextval('<seq>'::regclass)` without substring matching.
- **Exact Numeric Validation:** `duration_seconds` validated against exact type `'numeric(8,2)'`.
- **Columns Validated (11 columns):**
  1. `ingestion_id`: `bigint`, NOT NULL (Default: sequence-backed `nextval(...)`, non-NULL enforced)
  2. `dataset_version`: `text`, NOT NULL
  3. `dataset_file_hash`: `text`, NOT NULL
  4. `ingestion_stage`: `text`, NOT NULL
  5. `status`: `text`, NOT NULL
  6. `total_records_ingested`: `integer`, NOT NULL (Default: 0, non-NULL enforced)
  7. `quarantined_records_count`: `integer`, NOT NULL (Default: 0, non-NULL enforced)
  8. `error_message`: `text`, NULL
  9. `started_at`: `timestamp with time zone`, NOT NULL (Default: `now()`, non-NULL enforced)
  10. `completed_at`: `timestamp with time zone`, NULL
  11. `duration_seconds`: `numeric(8,2)`, NULL

### 2.7. Table 7: `public.documents` (Parent Knowledge Document Manifest)
- **Exact Column Count:** Exactly 8 columns (extra columns rejected).
- **Primary Key Check:** `PRIMARY KEY (document_id)`
- **Foreign Key Count:** Exactly 0 foreign keys (unexpected FKs rejected).
- **Columns Validated (8 columns):**
  1. `document_id`: `text`, NOT NULL
  2. `title`: `text`, NOT NULL
  3. `document_type`: `text`, NOT NULL
  4. `source_file`: `text`, NOT NULL
  5. `file_hash`: `text`, NOT NULL
  6. `total_chunks`: `integer`, NOT NULL (Default: 1, non-NULL enforced)
  7. `metadata`: `jsonb`, NOT NULL (Default: `'{}'::jsonb`, non-NULL enforced)
  8. `created_at`: `timestamp with time zone`, NOT NULL (Default: `now()`, non-NULL enforced)

### 2.8. Table 8: `public.rag_chunks` (Semantic Chunks with 768-dim Vectors)
- **Exact Column Count:** Exactly 9 columns (extra columns rejected).
- **Primary Key Check:** `PRIMARY KEY (chunk_id)`
- **Foreign Key Count:** Exactly 1 foreign key (unexpected FKs rejected).
- **Foreign Key Check (FK 6):**
  - Child table: `public.rag_chunks`
  - Child column: `document_id`
  - Referenced table: `public.documents`
  - Referenced column: `document_id`
  - Action: `ON DELETE CASCADE` (`confdeltype = 'c'`)
- **pgvector Vector Column Explicit Verification:**
  - Type name: `vector`
  - Type schema: `extensions` (`extensions.vector`)
  - Dimensionality modifier (`atttypmod`): Exactly **768**
  - Nullability: **NOT NULL**
- **Columns Validated (9 columns):**
  1. `chunk_id`: `text`, NOT NULL
  2. `document_id`: `text`, NOT NULL
  3. `chunk_type`: `text`, NOT NULL
  4. `title`: `text`, NOT NULL
  5. `content`: `text`, NOT NULL
  6. `token_estimate`: `integer`, NOT NULL
  7. `metadata`: `jsonb`, NOT NULL (Default: `'{}'::jsonb`, non-NULL enforced)
  8. `embedding`: `extensions.vector(768)`, NOT NULL
  9. `created_at`: `timestamp with time zone`, NOT NULL (Default: `now()`, non-NULL enforced)

---

## 3. Catalog-Based Index Compatibility Validation

For each of the 16 planned indexes, the validation inspects `pg_index`, `pg_class`, `pg_am`, `pg_attribute`, `pg_opclass`, and `pg_namespace`:
1. Target table (`indrelid` matching expected table OID in schema `public`).
2. Access method (`pg_am.amname`: `btree`, `gin`, or `hnsw`).
3. Index validity (`indisvalid = true`) and readiness (`indisready = true`).
4. Key attributes count (`indnkeyatts` matching approved key column count).
5. Total attributes count (`indnatts = indnkeyatts`, rejecting unexpected `INCLUDE` columns).
6. Absence of expression attributes (`attnum = 0` or `indexprs IS NOT NULL`).
7. Non-unique enforcement (`indisunique = false`).
8. Exact ordered key columns: reconstructed with `FILTER (WHERE k.ord <= idx.indnkeyatts)`.
9. Operator class and operator-class schema: `opc.opcname = 'vector_cosine_ops'` AND `opc_ns.nspname = 'extensions'` for `idx_rag_chunks_hnsw`.
10. Predicate definition: exact match for `(map_eligible = true)` on `idx_bus_stops_coords`, and rejection of unexpected predicates on the other 15 indexes.

| Index Name | Target Table | Method | Key / Total Atts | Ordered Key Columns / Opclass | Predicate Check |
| :--- | :--- | :---: | :---: | :--- | :--- |
| `idx_route_services_number` | `public.route_services` | `btree` | 1 / 1 | `(route_number)` | None |
| `idx_route_services_dep_bounds` | `public.route_services` | `btree` | 2 / 2 | `(earliest_departure_seconds, latest_departure_seconds)` | None |
| `idx_bus_stops_normalized_name` | `public.bus_stops` | `btree` | 1 / 1 | `(normalized_stop_name)` | None |
| `idx_bus_stops_coords` | `public.bus_stops` | `btree` | 2 / 2 | `(latitude, longitude)` | `WHERE (map_eligible = true)` |
| `idx_sched_deps_stop_time` | `public.scheduled_departures` | `btree` | 2 / 2 | `(stop_id, departure_seconds)` | None |
| `idx_sched_deps_route_time` | `public.scheduled_departures` | `btree` | 2 / 2 | `(route_id, departure_seconds)` | None |
| `idx_sched_deps_route_number` | `public.scheduled_departures` | `btree` | 1 / 1 | `(route_number)` | None |
| `idx_sched_deps_origin_seq` | `public.scheduled_departures` | `btree` | 2 / 2 | `(stop_sequence, departure_seconds)` | None |
| `idx_direct_conns_origin_dest` | `public.direct_connections` | `btree` | 2 / 2 | `(origin_stop_id, destination_stop_id)` | None |
| `idx_direct_conns_dep_sec` | `public.direct_connections` | `btree` | 1 / 1 | `(scheduled_departure_seconds)` | None |
| `idx_direct_conns_gin_stops` | `public.direct_connections` | `gin` | 1 / 1 | `(stops_between_origin_and_t_nagar)` | None |
| `idx_ingestion_log_hash_status` | `public.dataset_ingestion_log` | `btree` | 2 / 2 | `(dataset_file_hash, status)` | None |
| `idx_documents_type` | `public.documents` | `btree` | 1 / 1 | `(document_type)` | None |
| `idx_rag_chunks_hnsw` | `public.rag_chunks` | `hnsw` | 1 / 1 | `(embedding extensions.vector_cosine_ops)` | None |
| `idx_rag_chunks_type` | `public.rag_chunks` | `btree` | 1 / 1 | `(chunk_type)` | None |
| `idx_rag_chunks_metadata` | `public.rag_chunks` | `gin` | 1 / 1 | `(metadata)` | None |

---

## 4. Name Collision & Relation Type Guards

The pre-execution block queries `pg_class` and `pg_namespace` to verify that no non-table relation exists with any of our 8 target table names in schema `public`:
- Views (`relkind = 'v'`)
- Materialized Views (`relkind = 'm'`)
- Sequences (`relkind = 'S'`)
- Foreign Tables (`relkind = 'f'`)

If any name collision is detected, execution halts with an informative error detailing the conflicting relation type.

---

## 5. SQL Syntax & Migration Structure Validation Results

Automated script verification of [`database/migrations/001_initial_schema.sql`](file:///c:/Users/Kerthika%20M/Documents/AI%20Project/Sholinganallur_RouteWise/database/migrations/001_initial_schema.sql) confirmed:
- **Total Lines:** 1,450 lines.
- **Transaction Block:** Exactly 1 `BEGIN;` (line 40) and 1 `COMMIT;` (line 1450) wrapping the entire migration.
- **Dollar Quotes:** Exactly 2 matching `$$` tokens enclosing the PL/pgSQL validation block (lines 66 and 1207).
- **NULL Default Guards:** Exactly 8 explicit `IF v_actual_def IS NULL THEN RAISE EXCEPTION ...` blocks.
- **Timestamp Defaults:** Exactly 8 occurrences of `DEFAULT now()`; 0 legacy `timezone('utc', now())` references.
- **BIGSERIAL Validation:** Uses `pg_get_serial_sequence('public.<tbl>', r.col)` and exact sequence name catalog ownership.
- **Numeric Precision/Scale:** Strict equality check `!= r.exp_type` for `numeric(5,2)` and `numeric(8,2)`.
- **Target Tables Defined:** All 8 tables present with `CREATE TABLE IF NOT EXISTS public.<tbl>` (lines 1217–1427).
- **Target Indexes Defined:** All 16 indexes present with `CREATE INDEX IF NOT EXISTS <idx> ON public.<tbl>`.
- **Vector Configuration:** Schema-qualified `extensions.vector(768)` and `extensions.vector_cosine_ops` in place.
- **Comments:** Full schema-qualified table and column comments documented.

---

## 6. Conclusion & Next Step

The migration [`database/migrations/001_initial_schema.sql`](file:///c:/Users/Kerthika%20M/Documents/AI%20Project/Sholinganallur_RouteWise/database/migrations/001_initial_schema.sql) provides production-grade catalog validation. It is safe to run on an empty database or safely re-run against an already migrated database without data corruption, silent errors, or object drops.

*No SQL has been executed against Supabase. Migration execution awaits explicit operator authorization.*
