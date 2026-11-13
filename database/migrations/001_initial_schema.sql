-- ==============================================================================
-- Migration: 001_initial_schema.sql
-- Project: Sholinganallur_RouteWise
-- Phase: Phase 4 – Step 4.1: Database Schema Migration Preparation (Hardened Pass)
-- Dialect: PostgreSQL 15+ / Supabase (with pgvector)
--
-- Tables Defined (8):
--   1. public.routes (58 rows expected)
--   2. public.route_services (278 rows expected)
--   3. public.bus_stops (26 rows expected)
--   4. public.scheduled_departures (18,439 rows expected)
--   5. public.direct_connections (170 rows expected)
--   6. public.dataset_ingestion_log (ETL audit log)
--   7. public.documents (parent document manifest)
--   8. public.rag_chunks (145 semantic chunks, vector(768))
--
-- Indexes Defined: 16 indexes (B-tree, GIN, HNSW)
--
-- Safety Guarantees:
--   - Wrapped in atomic transaction block (BEGIN ... COMMIT).
--   - Schema-qualified table and index definitions explicitly target schema 'public'.
--   - Extensions explicitly target schema 'extensions'.
--   - Pre-execution PL/pgSQL validation block performs exhaustive catalog checks.
--   - Rejects existing objects that diverge from approved specifications.
--   - Strict non-NULL default verification before expression comparison.
--   - Exact sequence-ownership verification for BIGSERIAL columns using pg_get_serial_sequence.
--   - Exact foreign-key column validation matching ordered child & parent attributes from catalog.
--   - Exact unique constraint validation matching name, type 'u', and ordered columns.
--   - Exact numeric precision and scale validation (NUMERIC(5,2), NUMERIC(8,2)).
--   - Rigorous index validation: indisvalid, indisready, indnkeyatts, indnatts,
--     no expressions, exact ordered columns, operator-class schema qualification.
--   - Idempotent and non-destructive: uses IF NOT EXISTS, never drops or alters existing data.
-- ==============================================================================

-- ------------------------------------------------------------------------------
-- BEGIN TRANSACTION
-- Ensures that extension enablement, catalog validation, table definitions,
-- and index creations succeed or fail atomically as a single unit of work.
-- ------------------------------------------------------------------------------
BEGIN;

-- ------------------------------------------------------------------------------
-- EXTENSION PREREQUISITE: pgvector
-- Supabase installs pgvector in the 'extensions' schema by default.
-- ------------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA extensions;

-- ==============================================================================
-- PRE-EXECUTION EXHAUSTIVE SCHEMA & IDEMPOTENCY VALIDATION
-- ==============================================================================
-- NOTE ON IDEMPOTENCY & CATALOG VALIDATION:
-- While `CREATE TABLE IF NOT EXISTS` and `CREATE INDEX IF NOT EXISTS` prevent
-- runtime errors if objects already exist, they silently skip creation even if
-- an existing table or index has a divergent structure (e.g., missing columns,
-- wrong data types, incorrect foreign keys, or invalid index definitions).
--
-- This PL/pgSQL block inspects PostgreSQL system catalogs (`pg_class`,
-- `pg_attribute`, `pg_constraint`, `pg_index`, `pg_type`, `pg_namespace`,
-- `pg_attrdef`, `pg_am`, `pg_opclass`) to verify that if any of the 8 tables
-- or 16 indexes exist, their exact definitions match the approved schema design.
--
-- If any incompatibility is detected, execution stops immediately with RAISE EXCEPTION.
-- If the objects do not exist yet, this block safely passes, allowing the
-- subsequent DDL statements to create them cleanly.
-- ==============================================================================
DO $$
DECLARE
    r RECORD;
    v_tbl_oid OID;
    v_actual_col_count INT;
    v_actual_pk TEXT;
    v_actual_uq TEXT;
    v_uq_type CHAR(1);
    v_actual_fk_count INT;
    v_actual_type TEXT;
    v_actual_notnull BOOLEAN;
    v_actual_def TEXT;
    v_norm_def TEXT;
    v_seq_name TEXT;
    v_seq_bare TEXT;
    v_idx_oid OID;
    v_idx_tbl TEXT;
    v_idx_am TEXT;
    v_idx_uniq BOOLEAN;
    v_idx_isvalid BOOLEAN;
    v_idx_isready BOOLEAN;
    v_idx_nkeyatts INT;
    v_idx_natts INT;
    v_idx_has_expr BOOLEAN;
    v_idx_cols TEXT;
    v_idx_opclass TEXT;
    v_idx_opc_schema TEXT;
    v_idx_pred TEXT;
BEGIN
    -- -------------------------------------------------------------------------
    -- 0. EXTENSION SCHEMA VERIFICATION
    -- -------------------------------------------------------------------------
    IF NOT EXISTS (
        SELECT 1 FROM pg_namespace WHERE nspname = 'extensions'
    ) THEN
        RAISE EXCEPTION 'Pre-execution validation failed: Schema "extensions" does not exist in Supabase.';
    END IF;

    -- -------------------------------------------------------------------------
    -- 1. RELATION NAME COLLISION GUARDS
    -- Ensure no existing view, materialized view, sequence, or foreign table
    -- collides with any of the 8 approved table names in schema 'public'.
    -- -------------------------------------------------------------------------
    FOR r IN (
        SELECT c.relname, c.relkind,
               CASE c.relkind
                   WHEN 'v' THEN 'view'
                   WHEN 'm' THEN 'materialized view'
                   WHEN 'S' THEN 'sequence'
                   WHEN 'f' THEN 'foreign table'
                   ELSE 'non-table relation'
               END AS rel_desc
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relname IN (
              'routes', 'route_services', 'bus_stops',
              'scheduled_departures', 'direct_connections',
              'dataset_ingestion_log', 'documents', 'rag_chunks'
          )
          AND c.relkind != 'r'
    ) LOOP
        RAISE EXCEPTION 'Pre-execution validation failed: Name collision on "%" which is a % (relkind: %), not a regular table.',
            r.relname, r.rel_desc, r.relkind;
    END LOOP;

    -- =========================================================================
    -- 2. COMPLETE TABLE-BY-TABLE EXACT SCHEMA & DEFAULT VALIDATION
    -- =========================================================================

    -- -------------------------------------------------------------------------
    -- Table 1: routes (Exact 6 columns, PK route_number, 0 FKs)
    -- -------------------------------------------------------------------------
    SELECT c.oid INTO v_tbl_oid
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relname = 'routes' AND c.relkind = 'r';

    IF v_tbl_oid IS NOT NULL THEN
        -- Exact column count check (rejects unexpected extra columns)
        SELECT count(*) INTO v_actual_col_count
        FROM pg_attribute
        WHERE attrelid = v_tbl_oid AND attnum > 0 AND NOT attisdropped;

        IF v_actual_col_count != 6 THEN
            RAISE EXCEPTION 'Pre-execution validation failed on table "routes": Column count mismatch. Expected exactly 6 columns, found %.', v_actual_col_count;
        END IF;

        -- Exact Primary Key check
        SELECT string_agg(a.attname, ', ' ORDER BY k.ord) INTO v_actual_pk
        FROM pg_constraint con
        JOIN unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord) ON true
        JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.attnum
        WHERE con.conrelid = v_tbl_oid AND con.contype = 'p'
        GROUP BY con.conrelid;

        IF v_actual_pk != 'route_number' OR v_actual_pk IS NULL THEN
            RAISE EXCEPTION 'Pre-execution validation failed on table "routes": Expected PRIMARY KEY (route_number), found: %', COALESCE(v_actual_pk, 'NONE');
        END IF;

        -- Exact Foreign Key count check (expected 0)
        SELECT count(*) INTO v_actual_fk_count
        FROM pg_constraint
        WHERE conrelid = v_tbl_oid AND contype = 'f';

        IF v_actual_fk_count != 0 THEN
            RAISE EXCEPTION 'Pre-execution validation failed on table "routes": Expected 0 foreign keys, found %.', v_actual_fk_count;
        END IF;

        -- Validate all 6 columns, types, nullability, and defaults
        FOR r IN (
            SELECT unnest(ARRAY['route_number', 'total_service_variants', 'total_daily_trips', 'earliest_system_seconds', 'latest_system_seconds', 'created_at']) AS col,
                   unnest(ARRAY['text', 'integer', 'integer', 'integer', 'integer', 'timestamp with time zone']) AS exp_type,
                   unnest(ARRAY[true, true, true, true, true, true]) AS exp_notnull,
                   unnest(ARRAY[NULL, NULL, NULL, NULL, NULL, 'now()']) AS exp_def
        ) LOOP
            SELECT format_type(a.atttypid, a.atttypmod), a.attnotnull, pg_get_expr(ad.adbin, ad.adrelid)
            INTO v_actual_type, v_actual_notnull, v_actual_def
            FROM pg_attribute a
            LEFT JOIN pg_attrdef ad ON ad.adrelid = a.attrelid AND ad.adnum = a.attnum
            WHERE a.attrelid = v_tbl_oid AND a.attname = r.col AND a.attnum > 0 AND NOT a.attisdropped;

            IF v_actual_type IS NULL THEN
                RAISE EXCEPTION 'Pre-execution validation failed on table "routes": Missing column "%".', r.col;
            END IF;
            IF v_actual_type != r.exp_type THEN
                RAISE EXCEPTION 'Pre-execution validation failed on table "routes": Column "%" has type %, expected %.', r.col, v_actual_type, r.exp_type;
            END IF;
            IF v_actual_notnull != r.exp_notnull THEN
                RAISE EXCEPTION 'Pre-execution validation failed on table "routes": Column "%" NOT NULL constraint mismatch.', r.col;
            END IF;
            IF r.exp_def IS NOT NULL THEN
                IF v_actual_def IS NULL THEN
                    RAISE EXCEPTION 'Pre-execution validation failed on table "routes": Column "%" is missing an expected default expression (expected: %).', r.col, r.exp_def;
                END IF;
                v_norm_def := lower(trim(replace(v_actual_def, ' ', '')));
                IF r.exp_def = 'now()' AND v_norm_def NOT IN ('now()', 'current_timestamp') THEN
                    RAISE EXCEPTION 'Pre-execution validation failed on table "routes": Column "%" default mismatch. Expected now(), found %.', r.col, v_actual_def;
                END IF;
            END IF;
        END LOOP;
    END IF;

    -- -------------------------------------------------------------------------
    -- Table 2: route_services (Exact 10 columns, PK route_id, 1 FK route_number -> routes CASCADE)
    -- -------------------------------------------------------------------------
    SELECT c.oid INTO v_tbl_oid
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relname = 'route_services' AND c.relkind = 'r';

    IF v_tbl_oid IS NOT NULL THEN
        SELECT count(*) INTO v_actual_col_count
        FROM pg_attribute
        WHERE attrelid = v_tbl_oid AND attnum > 0 AND NOT attisdropped;

        IF v_actual_col_count != 10 THEN
            RAISE EXCEPTION 'Pre-execution validation failed on table "route_services": Column count mismatch. Expected exactly 10 columns, found %.', v_actual_col_count;
        END IF;

        SELECT string_agg(a.attname, ', ' ORDER BY k.ord) INTO v_actual_pk
        FROM pg_constraint con
        JOIN unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord) ON true
        JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.attnum
        WHERE con.conrelid = v_tbl_oid AND con.contype = 'p'
        GROUP BY con.conrelid;

        IF v_actual_pk != 'route_id' OR v_actual_pk IS NULL THEN
            RAISE EXCEPTION 'Pre-execution validation failed on table "route_services": Expected PRIMARY KEY (route_id), found: %', COALESCE(v_actual_pk, 'NONE');
        END IF;

        -- Exact Foreign Key count check (expected exactly 1)
        SELECT count(*) INTO v_actual_fk_count
        FROM pg_constraint
        WHERE conrelid = v_tbl_oid AND contype = 'f';

        IF v_actual_fk_count != 1 THEN
            RAISE EXCEPTION 'Pre-execution validation failed on table "route_services": Expected exactly 1 foreign key, found %.', v_actual_fk_count;
        END IF;

        -- Exact FK: route_number -> public.routes(route_number) ON DELETE CASCADE
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint con
            JOIN pg_class ref_c ON ref_c.oid = con.confrelid
            JOIN pg_namespace ref_n ON ref_n.oid = ref_c.relnamespace
            WHERE con.conrelid = v_tbl_oid AND con.contype = 'f'
              AND ref_n.nspname = 'public' AND ref_c.relname = 'routes'
              AND con.confdeltype = 'c'
              AND (
                  SELECT string_agg(a.attname, ', ' ORDER BY k.ord)
                  FROM unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord)
                  JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.attnum
              ) = 'route_number'
              AND (
                  SELECT string_agg(a.attname, ', ' ORDER BY k.ord)
                  FROM unnest(con.confkey) WITH ORDINALITY AS k(attnum, ord)
                  JOIN pg_attribute a ON a.attrelid = con.confrelid AND a.attnum = k.attnum
              ) = 'route_number'
        ) THEN
            RAISE EXCEPTION 'Pre-execution validation failed on table "route_services": Missing or incompatible FK route_number -> public.routes(route_number) ON DELETE CASCADE.';
        END IF;

        FOR r IN (
            SELECT unnest(ARRAY['route_id', 'route_number', 'route_name', 'distinct_trips_in_feed', 'scheduled_stop_departures_in_area', 'earliest_departure_time_string', 'latest_departure_time_string', 'earliest_departure_seconds', 'latest_departure_seconds', 'created_at']) AS col,
                   unnest(ARRAY['text', 'text', 'text', 'integer', 'integer', 'text', 'text', 'integer', 'integer', 'timestamp with time zone']) AS exp_type,
                   unnest(ARRAY[true, true, true, true, true, true, true, true, true, true]) AS exp_notnull,
                   unnest(ARRAY[NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, 'now()']) AS exp_def
        ) LOOP
            SELECT format_type(a.atttypid, a.atttypmod), a.attnotnull, pg_get_expr(ad.adbin, ad.adrelid)
            INTO v_actual_type, v_actual_notnull, v_actual_def
            FROM pg_attribute a
            LEFT JOIN pg_attrdef ad ON ad.adrelid = a.attrelid AND ad.adnum = a.attnum
            WHERE a.attrelid = v_tbl_oid AND a.attname = r.col AND a.attnum > 0 AND NOT a.attisdropped;

            IF v_actual_type IS NULL THEN
                RAISE EXCEPTION 'Pre-execution validation failed on table "route_services": Missing column "%".', r.col;
            END IF;
            IF v_actual_type != r.exp_type THEN
                RAISE EXCEPTION 'Pre-execution validation failed on table "route_services": Column "%" has type %, expected %.', r.col, v_actual_type, r.exp_type;
            END IF;
            IF v_actual_notnull != r.exp_notnull THEN
                RAISE EXCEPTION 'Pre-execution validation failed on table "route_services": Column "%" NOT NULL constraint mismatch.', r.col;
            END IF;
            IF r.exp_def IS NOT NULL THEN
                IF v_actual_def IS NULL THEN
                    RAISE EXCEPTION 'Pre-execution validation failed on table "route_services": Column "%" is missing an expected default expression (expected: %).', r.col, r.exp_def;
                END IF;
                v_norm_def := lower(trim(replace(v_actual_def, ' ', '')));
                IF r.exp_def = 'now()' AND v_norm_def NOT IN ('now()', 'current_timestamp') THEN
                    RAISE EXCEPTION 'Pre-execution validation failed on table "route_services": Column "%" default mismatch. Expected now(), found %.', r.col, v_actual_def;
                END IF;
            END IF;
        END LOOP;
    END IF;

    -- -------------------------------------------------------------------------
    -- Table 3: bus_stops (Exact 16 columns, PK stop_id, 0 FKs)
    -- -------------------------------------------------------------------------
    SELECT c.oid INTO v_tbl_oid
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relname = 'bus_stops' AND c.relkind = 'r';

    IF v_tbl_oid IS NOT NULL THEN
        SELECT count(*) INTO v_actual_col_count
        FROM pg_attribute
        WHERE attrelid = v_tbl_oid AND attnum > 0 AND NOT attisdropped;

        IF v_actual_col_count != 16 THEN
            RAISE EXCEPTION 'Pre-execution validation failed on table "bus_stops": Column count mismatch. Expected exactly 16 columns, found %.', v_actual_col_count;
        END IF;

        SELECT string_agg(a.attname, ', ' ORDER BY k.ord) INTO v_actual_pk
        FROM pg_constraint con
        JOIN unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord) ON true
        JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.attnum
        WHERE con.conrelid = v_tbl_oid AND con.contype = 'p'
        GROUP BY con.conrelid;

        IF v_actual_pk != 'stop_id' OR v_actual_pk IS NULL THEN
            RAISE EXCEPTION 'Pre-execution validation failed on table "bus_stops": Expected PRIMARY KEY (stop_id), found: %', COALESCE(v_actual_pk, 'NONE');
        END IF;

        SELECT count(*) INTO v_actual_fk_count
        FROM pg_constraint
        WHERE conrelid = v_tbl_oid AND contype = 'f';

        IF v_actual_fk_count != 0 THEN
            RAISE EXCEPTION 'Pre-execution validation failed on table "bus_stops": Expected 0 foreign keys, found %.', v_actual_fk_count;
        END IF;

        FOR r IN (
            SELECT unnest(ARRAY[
                'stop_id', 'stop_code', 'stop_name', 'normalized_stop_name',
                'latitude', 'longitude', 'coordinates_within_chennai_bounds',
                'map_eligible', 'coordinate_status', 'locality_area_membership_verified',
                'locality_area_membership_status', 'coordinate_source',
                'coordinate_verification_status', 'original_latitude',
                'original_longitude', 'created_at'
            ]) AS col,
            unnest(ARRAY[
                'text', 'text', 'text', 'text',
                'double precision', 'double precision', 'boolean',
                'boolean', 'text', 'boolean',
                'text', 'text',
                'text', 'double precision',
                'double precision', 'timestamp with time zone'
            ]) AS exp_type,
            unnest(ARRAY[
                true, true, true, true,
                false, false, true,
                true, true, true,
                true, true,
                true, false,
                false, true
            ]) AS exp_notnull,
            unnest(ARRAY[
                NULL, NULL, NULL, NULL,
                NULL, NULL, 'true',
                'true', NULL, 'false',
                NULL, NULL,
                NULL, NULL,
                NULL, 'now()'
            ]) AS exp_def
        ) LOOP
            SELECT format_type(a.atttypid, a.atttypmod), a.attnotnull, pg_get_expr(ad.adbin, ad.adrelid)
            INTO v_actual_type, v_actual_notnull, v_actual_def
            FROM pg_attribute a
            LEFT JOIN pg_attrdef ad ON ad.adrelid = a.attrelid AND ad.adnum = a.attnum
            WHERE a.attrelid = v_tbl_oid AND a.attname = r.col AND a.attnum > 0 AND NOT a.attisdropped;

            IF v_actual_type IS NULL THEN
                RAISE EXCEPTION 'Pre-execution validation failed on table "bus_stops": Missing column "%".', r.col;
            END IF;
            IF v_actual_type != r.exp_type THEN
                RAISE EXCEPTION 'Pre-execution validation failed on table "bus_stops": Column "%" has type %, expected %.', r.col, v_actual_type, r.exp_type;
            END IF;
            IF v_actual_notnull != r.exp_notnull THEN
                RAISE EXCEPTION 'Pre-execution validation failed on table "bus_stops": Column "%" NOT NULL constraint mismatch.', r.col;
            END IF;
            IF r.exp_def IS NOT NULL THEN
                IF v_actual_def IS NULL THEN
                    RAISE EXCEPTION 'Pre-execution validation failed on table "bus_stops": Column "%" is missing an expected default expression (expected: %).', r.col, r.exp_def;
                END IF;
                v_norm_def := lower(trim(replace(v_actual_def, ' ', '')));
                IF r.exp_def = 'now()' AND v_norm_def NOT IN ('now()', 'current_timestamp') THEN
                    RAISE EXCEPTION 'Pre-execution validation failed on table "bus_stops": Column "%" default mismatch. Expected now(), found %.', r.col, v_actual_def;
                ELSIF r.exp_def = 'true' AND v_norm_def NOT IN ('true', '''true''::boolean') THEN
                    RAISE EXCEPTION 'Pre-execution validation failed on table "bus_stops": Column "%" default mismatch. Expected TRUE, found %.', r.col, v_actual_def;
                ELSIF r.exp_def = 'false' AND v_norm_def NOT IN ('false', '''false''::boolean') THEN
                    RAISE EXCEPTION 'Pre-execution validation failed on table "bus_stops": Column "%" default mismatch. Expected FALSE, found %.', r.col, v_actual_def;
                END IF;
            END IF;
        END LOOP;
    END IF;

    -- -------------------------------------------------------------------------
    -- Table 4: scheduled_departures (Exact 13 columns, PK departure_id, UQ trip_id+seq, 2 FKs CASCADE)
    -- -------------------------------------------------------------------------
    SELECT c.oid INTO v_tbl_oid
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relname = 'scheduled_departures' AND c.relkind = 'r';

    IF v_tbl_oid IS NOT NULL THEN
        SELECT count(*) INTO v_actual_col_count
        FROM pg_attribute
        WHERE attrelid = v_tbl_oid AND attnum > 0 AND NOT attisdropped;

        IF v_actual_col_count != 13 THEN
            RAISE EXCEPTION 'Pre-execution validation failed on table "scheduled_departures": Column count mismatch. Expected exactly 13 columns, found %.', v_actual_col_count;
        END IF;

        SELECT string_agg(a.attname, ', ' ORDER BY k.ord) INTO v_actual_pk
        FROM pg_constraint con
        JOIN unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord) ON true
        JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.attnum
        WHERE con.conrelid = v_tbl_oid AND con.contype = 'p'
        GROUP BY con.conrelid;

        IF v_actual_pk != 'departure_id' OR v_actual_pk IS NULL THEN
            RAISE EXCEPTION 'Pre-execution validation failed on table "scheduled_departures": Expected PRIMARY KEY (departure_id), found: %', COALESCE(v_actual_pk, 'NONE');
        END IF;

        -- Exact Named Unique Constraint: uq_scheduled_departures_trip_seq (trip_id, stop_sequence)
        SELECT con.contype,
               string_agg(a.attname, ', ' ORDER BY k.ord)
        INTO v_uq_type, v_actual_uq
        FROM pg_constraint con
        LEFT JOIN unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord) ON true
        LEFT JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.attnum
        WHERE con.conrelid = v_tbl_oid AND con.conname = 'uq_scheduled_departures_trip_seq'
        GROUP BY con.conrelid, con.contype;

        IF v_uq_type IS NULL THEN
            RAISE EXCEPTION 'Pre-execution validation failed on table "scheduled_departures": Missing constraint "uq_scheduled_departures_trip_seq".';
        END IF;
        IF v_uq_type != 'u' THEN
            RAISE EXCEPTION 'Pre-execution validation failed on table "scheduled_departures": Constraint "uq_scheduled_departures_trip_seq" has type %, expected unique (u).', v_uq_type;
        END IF;
        IF v_actual_uq != 'trip_id, stop_sequence' THEN
            RAISE EXCEPTION 'Pre-execution validation failed on table "scheduled_departures": Constraint "uq_scheduled_departures_trip_seq" column order mismatch. Expected (trip_id, stop_sequence), found: (%).', COALESCE(v_actual_uq, 'NONE');
        END IF;

        -- Exact Foreign Key count check (expected exactly 2)
        SELECT count(*) INTO v_actual_fk_count
        FROM pg_constraint
        WHERE conrelid = v_tbl_oid AND contype = 'f';

        IF v_actual_fk_count != 2 THEN
            RAISE EXCEPTION 'Pre-execution validation failed on table "scheduled_departures": Expected exactly 2 foreign keys, found %.', v_actual_fk_count;
        END IF;

        -- FK 1: stop_id -> public.bus_stops(stop_id) ON DELETE CASCADE
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint con
            JOIN pg_class ref_c ON ref_c.oid = con.confrelid
            JOIN pg_namespace ref_n ON ref_n.oid = ref_c.relnamespace
            WHERE con.conrelid = v_tbl_oid AND con.contype = 'f'
              AND ref_n.nspname = 'public' AND ref_c.relname = 'bus_stops'
              AND con.confdeltype = 'c'
              AND (
                  SELECT string_agg(a.attname, ', ' ORDER BY k.ord)
                  FROM unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord)
                  JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.attnum
              ) = 'stop_id'
              AND (
                  SELECT string_agg(a.attname, ', ' ORDER BY k.ord)
                  FROM unnest(con.confkey) WITH ORDINALITY AS k(attnum, ord)
                  JOIN pg_attribute a ON a.attrelid = con.confrelid AND a.attnum = k.attnum
              ) = 'stop_id'
        ) THEN
            RAISE EXCEPTION 'Pre-execution validation failed on table "scheduled_departures": Missing or incompatible FK stop_id -> public.bus_stops(stop_id) ON DELETE CASCADE.';
        END IF;

        -- FK 2: route_id -> public.route_services(route_id) ON DELETE CASCADE
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint con
            JOIN pg_class ref_c ON ref_c.oid = con.confrelid
            JOIN pg_namespace ref_n ON ref_n.oid = ref_c.relnamespace
            WHERE con.conrelid = v_tbl_oid AND con.contype = 'f'
              AND ref_n.nspname = 'public' AND ref_c.relname = 'route_services'
              AND con.confdeltype = 'c'
              AND (
                  SELECT string_agg(a.attname, ', ' ORDER BY k.ord)
                  FROM unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord)
                  JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.attnum
              ) = 'route_id'
              AND (
                  SELECT string_agg(a.attname, ', ' ORDER BY k.ord)
                  FROM unnest(con.confkey) WITH ORDINALITY AS k(attnum, ord)
                  JOIN pg_attribute a ON a.attrelid = con.confrelid AND a.attnum = k.attnum
              ) = 'route_id'
        ) THEN
            RAISE EXCEPTION 'Pre-execution validation failed on table "scheduled_departures": Missing or incompatible FK route_id -> public.route_services(route_id) ON DELETE CASCADE.';
        END IF;

        FOR r IN (
            SELECT unnest(ARRAY[
                'departure_id', 'trip_id', 'stop_sequence', 'stop_id',
                'route_id', 'route_number', 'route_name', 'stop_name',
                'direction_id', 'arrival_time', 'departure_time',
                'departure_seconds', 'created_at'
            ]) AS col,
            unnest(ARRAY[
                'bigint', 'text', 'integer', 'text',
                'text', 'text', 'text', 'text',
                'integer', 'text', 'text',
                'integer', 'timestamp with time zone'
            ]) AS exp_type,
            unnest(ARRAY[
                true, true, true, true,
                true, true, true, true,
                true, true, true,
                true, true
            ]) AS exp_notnull,
            unnest(ARRAY[
                'nextval', NULL, NULL, NULL,
                NULL, NULL, NULL, NULL,
                NULL, NULL, NULL,
                NULL, 'now()'
            ]) AS exp_def
        ) LOOP
            SELECT format_type(a.atttypid, a.atttypmod), a.attnotnull, pg_get_expr(ad.adbin, ad.adrelid)
            INTO v_actual_type, v_actual_notnull, v_actual_def
            FROM pg_attribute a
            LEFT JOIN pg_attrdef ad ON ad.adrelid = a.attrelid AND ad.adnum = a.attnum
            WHERE a.attrelid = v_tbl_oid AND a.attname = r.col AND a.attnum > 0 AND NOT a.attisdropped;

            IF v_actual_type IS NULL THEN
                RAISE EXCEPTION 'Pre-execution validation failed on table "scheduled_departures": Missing column "%".', r.col;
            END IF;
            IF v_actual_type != r.exp_type THEN
                RAISE EXCEPTION 'Pre-execution validation failed on table "scheduled_departures": Column "%" has type %, expected %.', r.col, v_actual_type, r.exp_type;
            END IF;
            IF v_actual_notnull != r.exp_notnull THEN
                RAISE EXCEPTION 'Pre-execution validation failed on table "scheduled_departures": Column "%" NOT NULL constraint mismatch.', r.col;
            END IF;
            IF r.exp_def IS NOT NULL THEN
                IF v_actual_def IS NULL THEN
                    RAISE EXCEPTION 'Pre-execution validation failed on table "scheduled_departures": Column "%" is missing an expected default expression (expected: %).', r.col, r.exp_def;
                END IF;
                v_norm_def := lower(trim(replace(v_actual_def, ' ', '')));
                IF r.exp_def = 'now()' AND v_norm_def NOT IN ('now()', 'current_timestamp') THEN
                    RAISE EXCEPTION 'Pre-execution validation failed on table "scheduled_departures": Column "%" default mismatch. Expected now(), found %.', r.col, v_actual_def;
                ELSIF r.exp_def = 'nextval' THEN
                    v_seq_name := pg_get_serial_sequence('public.scheduled_departures', r.col);
                    IF v_seq_name IS NULL THEN
                        RAISE EXCEPTION 'Pre-execution validation failed on table "scheduled_departures": Column "%" has no owned sequence.', r.col;
                    END IF;
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_class c
                        JOIN pg_namespace n ON n.oid = c.relnamespace
                        WHERE n.nspname || '.' || c.relname = v_seq_name AND c.relkind = 'S'
                    ) THEN
                        RAISE EXCEPTION 'Pre-execution validation failed on table "scheduled_departures": Owned sequence "%" for column "%" does not exist in catalog as a sequence.', v_seq_name, r.col;
                    END IF;
                    v_seq_bare := split_part(v_seq_name, '.', 2);
                    IF v_norm_def NOT IN ('nextval(''' || lower(v_seq_bare) || '''::regclass)', 'nextval(''' || lower(v_seq_name) || '''::regclass)') THEN
                        RAISE EXCEPTION 'Pre-execution validation failed on table "scheduled_departures": Column "%" default "%" does not match exact sequence invocation nextval(''%''::regclass).', r.col, v_actual_def, v_seq_bare;
                    END IF;
                END IF;
            END IF;
        END LOOP;
    END IF;

    -- -------------------------------------------------------------------------
    -- Table 5: direct_connections (Exact 18 columns, PK trip_id, 2 FKs CASCADE, NO FK on destination_stop_id)
    -- -------------------------------------------------------------------------
    SELECT c.oid INTO v_tbl_oid
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relname = 'direct_connections' AND c.relkind = 'r';

    IF v_tbl_oid IS NOT NULL THEN
        SELECT count(*) INTO v_actual_col_count
        FROM pg_attribute
        WHERE attrelid = v_tbl_oid AND attnum > 0 AND NOT attisdropped;

        IF v_actual_col_count != 18 THEN
            RAISE EXCEPTION 'Pre-execution validation failed on table "direct_connections": Column count mismatch. Expected exactly 18 columns, found %.', v_actual_col_count;
        END IF;

        SELECT string_agg(a.attname, ', ' ORDER BY k.ord) INTO v_actual_pk
        FROM pg_constraint con
        JOIN unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord) ON true
        JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.attnum
        WHERE con.conrelid = v_tbl_oid AND con.contype = 'p'
        GROUP BY con.conrelid;

        IF v_actual_pk != 'trip_id' OR v_actual_pk IS NULL THEN
            RAISE EXCEPTION 'Pre-execution validation failed on table "direct_connections": Expected PRIMARY KEY (trip_id), found: %', COALESCE(v_actual_pk, 'NONE');
        END IF;

        -- Exact Foreign Key count check (expected exactly 2)
        SELECT count(*) INTO v_actual_fk_count
        FROM pg_constraint
        WHERE conrelid = v_tbl_oid AND contype = 'f';

        IF v_actual_fk_count != 2 THEN
            RAISE EXCEPTION 'Pre-execution validation failed on table "direct_connections": Expected exactly 2 foreign keys, found %.', v_actual_fk_count;
        END IF;

        -- FK 1: route_id -> public.route_services(route_id) ON DELETE CASCADE
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint con
            JOIN pg_class ref_c ON ref_c.oid = con.confrelid
            JOIN pg_namespace ref_n ON ref_n.oid = ref_c.relnamespace
            WHERE con.conrelid = v_tbl_oid AND con.contype = 'f'
              AND ref_n.nspname = 'public' AND ref_c.relname = 'route_services'
              AND con.confdeltype = 'c'
              AND (
                  SELECT string_agg(a.attname, ', ' ORDER BY k.ord)
                  FROM unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord)
                  JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.attnum
              ) = 'route_id'
              AND (
                  SELECT string_agg(a.attname, ', ' ORDER BY k.ord)
                  FROM unnest(con.confkey) WITH ORDINALITY AS k(attnum, ord)
                  JOIN pg_attribute a ON a.attrelid = con.confrelid AND a.attnum = k.attnum
              ) = 'route_id'
        ) THEN
            RAISE EXCEPTION 'Pre-execution validation failed on table "direct_connections": Missing or incompatible FK route_id -> public.route_services(route_id) ON DELETE CASCADE.';
        END IF;

        -- FK 2: origin_stop_id -> public.bus_stops(stop_id) ON DELETE CASCADE
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint con
            JOIN pg_class ref_c ON ref_c.oid = con.confrelid
            JOIN pg_namespace ref_n ON ref_n.oid = ref_c.relnamespace
            WHERE con.conrelid = v_tbl_oid AND con.contype = 'f'
              AND ref_n.nspname = 'public' AND ref_c.relname = 'bus_stops'
              AND con.confdeltype = 'c'
              AND (
                  SELECT string_agg(a.attname, ', ' ORDER BY k.ord)
                  FROM unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord)
                  JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.attnum
              ) = 'origin_stop_id'
              AND (
                  SELECT string_agg(a.attname, ', ' ORDER BY k.ord)
                  FROM unnest(con.confkey) WITH ORDINALITY AS k(attnum, ord)
                  JOIN pg_attribute a ON a.attrelid = con.confrelid AND a.attnum = k.attnum
              ) = 'stop_id'
        ) THEN
            RAISE EXCEPTION 'Pre-execution validation failed on table "direct_connections": Missing or incompatible FK origin_stop_id -> public.bus_stops(stop_id) ON DELETE CASCADE.';
        END IF;

        -- Negative Foreign Key Guard: destination_stop_id must NOT reference bus_stops (or any table)
        IF EXISTS (
            SELECT 1 FROM pg_constraint con
            JOIN unnest(con.conkey) AS fk_key(attnum) ON true
            JOIN pg_attribute a
              ON a.attrelid = con.conrelid
             AND a.attnum = fk_key.attnum
            WHERE con.conrelid = v_tbl_oid AND con.contype = 'f'
              AND a.attname = 'destination_stop_id'
        ) THEN
            RAISE EXCEPTION 'Pre-execution validation failed on table "direct_connections": destination_stop_id must remain unconstrained by FK to support external T. Nagar stops.';
        END IF;

        FOR r IN (
            SELECT unnest(ARRAY[
                'trip_id', 'route_id', 'route_number', 'route_name',
                'origin_area_id', 'origin_area_name', 'origin_stop_id',
                'origin_stop_name', 'scheduled_departure_at_origin',
                'destination_stop_id', 'destination_stop_name',
                'scheduled_arrival_at_t_nagar_stop', 'scheduled_departure_seconds',
                'scheduled_arrival_seconds', 'duration_minutes',
                'intermediate_stop_count', 'stops_between_origin_and_t_nagar',
                'created_at'
            ]) AS col,
            unnest(ARRAY[
                'text', 'text', 'text', 'text',
                'text', 'text', 'text',
                'text', 'text',
                'text', 'text',
                'text', 'integer',
                'integer', 'numeric(5,2)',
                'integer', 'jsonb',
                'timestamp with time zone'
            ]) AS exp_type,
            unnest(ARRAY[
                true, true, true, true,
                true, true, true,
                true, true,
                true, true,
                true, true,
                true, true,
                true, true,
                true
            ]) AS exp_notnull,
            unnest(ARRAY[
                NULL, NULL, NULL, NULL,
                NULL, NULL, NULL,
                NULL, NULL,
                NULL, NULL,
                NULL, NULL,
                NULL, NULL,
                NULL, NULL,
                'now()'
            ]) AS exp_def
        ) LOOP
            SELECT format_type(a.atttypid, a.atttypmod), a.attnotnull, pg_get_expr(ad.adbin, ad.adrelid)
            INTO v_actual_type, v_actual_notnull, v_actual_def
            FROM pg_attribute a
            LEFT JOIN pg_attrdef ad ON ad.adrelid = a.attrelid AND ad.adnum = a.attnum
            WHERE a.attrelid = v_tbl_oid AND a.attname = r.col AND a.attnum > 0 AND NOT a.attisdropped;

            IF v_actual_type IS NULL THEN
                RAISE EXCEPTION 'Pre-execution validation failed on table "direct_connections": Missing column "%".', r.col;
            END IF;
            IF v_actual_type != r.exp_type THEN
                RAISE EXCEPTION 'Pre-execution validation failed on table "direct_connections": Column "%" has type %, expected %.', r.col, v_actual_type, r.exp_type;
            END IF;
            IF v_actual_notnull != r.exp_notnull THEN
                RAISE EXCEPTION 'Pre-execution validation failed on table "direct_connections": Column "%" NOT NULL constraint mismatch.', r.col;
            END IF;
            IF r.exp_def IS NOT NULL THEN
                IF v_actual_def IS NULL THEN
                    RAISE EXCEPTION 'Pre-execution validation failed on table "direct_connections": Column "%" is missing an expected default expression (expected: %).', r.col, r.exp_def;
                END IF;
                v_norm_def := lower(trim(replace(v_actual_def, ' ', '')));
                IF r.exp_def = 'now()' AND v_norm_def NOT IN ('now()', 'current_timestamp') THEN
                    RAISE EXCEPTION 'Pre-execution validation failed on table "direct_connections": Column "%" default mismatch. Expected now(), found %.', r.col, v_actual_def;
                END IF;
            END IF;
        END LOOP;
    END IF;

    -- -------------------------------------------------------------------------
    -- Table 6: dataset_ingestion_log (Exact 11 columns, PK ingestion_id, 0 FKs)
    -- -------------------------------------------------------------------------
    SELECT c.oid INTO v_tbl_oid
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relname = 'dataset_ingestion_log' AND c.relkind = 'r';

    IF v_tbl_oid IS NOT NULL THEN
        SELECT count(*) INTO v_actual_col_count
        FROM pg_attribute
        WHERE attrelid = v_tbl_oid AND attnum > 0 AND NOT attisdropped;

        IF v_actual_col_count != 11 THEN
            RAISE EXCEPTION 'Pre-execution validation failed on table "dataset_ingestion_log": Column count mismatch. Expected exactly 11 columns, found %.', v_actual_col_count;
        END IF;

        SELECT string_agg(a.attname, ', ' ORDER BY k.ord) INTO v_actual_pk
        FROM pg_constraint con
        JOIN unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord) ON true
        JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.attnum
        WHERE con.conrelid = v_tbl_oid AND con.contype = 'p'
        GROUP BY con.conrelid;

        IF v_actual_pk != 'ingestion_id' OR v_actual_pk IS NULL THEN
            RAISE EXCEPTION 'Pre-execution validation failed on table "dataset_ingestion_log": Expected PRIMARY KEY (ingestion_id), found: %', COALESCE(v_actual_pk, 'NONE');
        END IF;

        SELECT count(*) INTO v_actual_fk_count
        FROM pg_constraint
        WHERE conrelid = v_tbl_oid AND contype = 'f';

        IF v_actual_fk_count != 0 THEN
            RAISE EXCEPTION 'Pre-execution validation failed on table "dataset_ingestion_log": Expected 0 foreign keys, found %.', v_actual_fk_count;
        END IF;

        FOR r IN (
            SELECT unnest(ARRAY[
                'ingestion_id', 'dataset_version', 'dataset_file_hash',
                'ingestion_stage', 'status', 'total_records_ingested',
                'quarantined_records_count', 'error_message',
                'started_at', 'completed_at', 'duration_seconds'
            ]) AS col,
            unnest(ARRAY[
                'bigint', 'text', 'text',
                'text', 'text', 'integer',
                'integer', 'text',
                'timestamp with time zone', 'timestamp with time zone', 'numeric(8,2)'
            ]) AS exp_type,
            unnest(ARRAY[
                true, true, true,
                true, true, true,
                true, false,
                true, false, false
            ]) AS exp_notnull,
            unnest(ARRAY[
                'nextval', NULL, NULL,
                NULL, NULL, '0',
                '0', NULL,
                'now()', NULL, NULL
            ]) AS exp_def
        ) LOOP
            SELECT format_type(a.atttypid, a.atttypmod), a.attnotnull, pg_get_expr(ad.adbin, ad.adrelid)
            INTO v_actual_type, v_actual_notnull, v_actual_def
            FROM pg_attribute a
            LEFT JOIN pg_attrdef ad ON ad.adrelid = a.attrelid AND ad.adnum = a.attnum
            WHERE a.attrelid = v_tbl_oid AND a.attname = r.col AND a.attnum > 0 AND NOT a.attisdropped;

            IF v_actual_type IS NULL THEN
                RAISE EXCEPTION 'Pre-execution validation failed on table "dataset_ingestion_log": Missing column "%".', r.col;
            END IF;
            IF v_actual_type != r.exp_type THEN
                RAISE EXCEPTION 'Pre-execution validation failed on table "dataset_ingestion_log": Column "%" has type %, expected %.', r.col, v_actual_type, r.exp_type;
            END IF;
            IF v_actual_notnull != r.exp_notnull THEN
                RAISE EXCEPTION 'Pre-execution validation failed on table "dataset_ingestion_log": Column "%" NOT NULL constraint mismatch.', r.col;
            END IF;
            IF r.exp_def IS NOT NULL THEN
                IF v_actual_def IS NULL THEN
                    RAISE EXCEPTION 'Pre-execution validation failed on table "dataset_ingestion_log": Column "%" is missing an expected default expression (expected: %).', r.col, r.exp_def;
                END IF;
                v_norm_def := lower(trim(replace(v_actual_def, ' ', '')));
                IF r.exp_def = 'now()' AND v_norm_def NOT IN ('now()', 'current_timestamp') THEN
                    RAISE EXCEPTION 'Pre-execution validation failed on table "dataset_ingestion_log": Column "%" default mismatch. Expected now(), found %.', r.col, v_actual_def;
                ELSIF r.exp_def = '0' AND v_norm_def NOT IN ('0', '0::integer') THEN
                    RAISE EXCEPTION 'Pre-execution validation failed on table "dataset_ingestion_log": Column "%" default mismatch. Expected 0, found %.', r.col, v_actual_def;
                ELSIF r.exp_def = 'nextval' THEN
                    v_seq_name := pg_get_serial_sequence('public.dataset_ingestion_log', r.col);
                    IF v_seq_name IS NULL THEN
                        RAISE EXCEPTION 'Pre-execution validation failed on table "dataset_ingestion_log": Column "%" has no owned sequence.', r.col;
                    END IF;
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_class c
                        JOIN pg_namespace n ON n.oid = c.relnamespace
                        WHERE n.nspname || '.' || c.relname = v_seq_name AND c.relkind = 'S'
                    ) THEN
                        RAISE EXCEPTION 'Pre-execution validation failed on table "dataset_ingestion_log": Owned sequence "%" for column "%" does not exist in catalog as a sequence.', v_seq_name, r.col;
                    END IF;
                    v_seq_bare := split_part(v_seq_name, '.', 2);
                    IF v_norm_def NOT IN ('nextval(''' || lower(v_seq_bare) || '''::regclass)', 'nextval(''' || lower(v_seq_name) || '''::regclass)') THEN
                        RAISE EXCEPTION 'Pre-execution validation failed on table "dataset_ingestion_log": Column "%" default "%" does not match exact sequence invocation nextval(''%''::regclass).', r.col, v_actual_def, v_seq_bare;
                    END IF;
                END IF;
            END IF;
        END LOOP;
    END IF;

    -- -------------------------------------------------------------------------
    -- Table 7: documents (Exact 8 columns, PK document_id, 0 FKs)
    -- -------------------------------------------------------------------------
    SELECT c.oid INTO v_tbl_oid
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relname = 'documents' AND c.relkind = 'r';

    IF v_tbl_oid IS NOT NULL THEN
        SELECT count(*) INTO v_actual_col_count
        FROM pg_attribute
        WHERE attrelid = v_tbl_oid AND attnum > 0 AND NOT attisdropped;

        IF v_actual_col_count != 8 THEN
            RAISE EXCEPTION 'Pre-execution validation failed on table "documents": Column count mismatch. Expected exactly 8 columns, found %.', v_actual_col_count;
        END IF;

        SELECT string_agg(a.attname, ', ' ORDER BY k.ord) INTO v_actual_pk
        FROM pg_constraint con
        JOIN unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord) ON true
        JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.attnum
        WHERE con.conrelid = v_tbl_oid AND con.contype = 'p'
        GROUP BY con.conrelid;

        IF v_actual_pk != 'document_id' OR v_actual_pk IS NULL THEN
            RAISE EXCEPTION 'Pre-execution validation failed on table "documents": Expected PRIMARY KEY (document_id), found: %', COALESCE(v_actual_pk, 'NONE');
        END IF;

        SELECT count(*) INTO v_actual_fk_count
        FROM pg_constraint
        WHERE conrelid = v_tbl_oid AND contype = 'f';

        IF v_actual_fk_count != 0 THEN
            RAISE EXCEPTION 'Pre-execution validation failed on table "documents": Expected 0 foreign keys, found %.', v_actual_fk_count;
        END IF;

        FOR r IN (
            SELECT unnest(ARRAY['document_id', 'title', 'document_type', 'source_file', 'file_hash', 'total_chunks', 'metadata', 'created_at']) AS col,
                   unnest(ARRAY['text', 'text', 'text', 'text', 'text', 'integer', 'jsonb', 'timestamp with time zone']) AS exp_type,
                   unnest(ARRAY[true, true, true, true, true, true, true, true]) AS exp_notnull,
                   unnest(ARRAY[NULL, NULL, NULL, NULL, NULL, '1', '''{}''::jsonb', 'now()']) AS exp_def
        ) LOOP
            SELECT format_type(a.atttypid, a.atttypmod), a.attnotnull, pg_get_expr(ad.adbin, ad.adrelid)
            INTO v_actual_type, v_actual_notnull, v_actual_def
            FROM pg_attribute a
            LEFT JOIN pg_attrdef ad ON ad.adrelid = a.attrelid AND ad.adnum = a.attnum
            WHERE a.attrelid = v_tbl_oid AND a.attname = r.col AND a.attnum > 0 AND NOT a.attisdropped;

            IF v_actual_type IS NULL THEN
                RAISE EXCEPTION 'Pre-execution validation failed on table "documents": Missing column "%".', r.col;
            END IF;
            IF v_actual_type != r.exp_type THEN
                RAISE EXCEPTION 'Pre-execution validation failed on table "documents": Column "%" has type %, expected %.', r.col, v_actual_type, r.exp_type;
            END IF;
            IF v_actual_notnull != r.exp_notnull THEN
                RAISE EXCEPTION 'Pre-execution validation failed on table "documents": Column "%" NOT NULL constraint mismatch.', r.col;
            END IF;
            IF r.exp_def IS NOT NULL THEN
                IF v_actual_def IS NULL THEN
                    RAISE EXCEPTION 'Pre-execution validation failed on table "documents": Column "%" is missing an expected default expression (expected: %).', r.col, r.exp_def;
                END IF;
                v_norm_def := lower(trim(replace(v_actual_def, ' ', '')));
                IF r.exp_def = 'now()' AND v_norm_def NOT IN ('now()', 'current_timestamp') THEN
                    RAISE EXCEPTION 'Pre-execution validation failed on table "documents": Column "%" default mismatch. Expected now(), found %.', r.col, v_actual_def;
                ELSIF r.exp_def = '1' AND v_norm_def NOT IN ('1', '1::integer') THEN
                    RAISE EXCEPTION 'Pre-execution validation failed on table "documents": Column "%" default mismatch. Expected 1, found %.', r.col, v_actual_def;
                ELSIF r.exp_def = '''{}''::jsonb' AND v_norm_def NOT IN ('''{}''::jsonb', '''{}''') THEN
                    RAISE EXCEPTION 'Pre-execution validation failed on table "documents": Column "%" default mismatch. Expected ''{}''::jsonb, found %.', r.col, v_actual_def;
                END IF;
            END IF;
        END LOOP;
    END IF;

    -- -------------------------------------------------------------------------
    -- Table 8: rag_chunks (Exact 9 columns, PK chunk_id, 1 FK CASCADE, extensions.vector(768))
    -- -------------------------------------------------------------------------
    SELECT c.oid INTO v_tbl_oid
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relname = 'rag_chunks' AND c.relkind = 'r';

    IF v_tbl_oid IS NOT NULL THEN
        SELECT count(*) INTO v_actual_col_count
        FROM pg_attribute
        WHERE attrelid = v_tbl_oid AND attnum > 0 AND NOT attisdropped;

        IF v_actual_col_count != 9 THEN
            RAISE EXCEPTION 'Pre-execution validation failed on table "rag_chunks": Column count mismatch. Expected exactly 9 columns, found %.', v_actual_col_count;
        END IF;

        SELECT string_agg(a.attname, ', ' ORDER BY k.ord) INTO v_actual_pk
        FROM pg_constraint con
        JOIN unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord) ON true
        JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.attnum
        WHERE con.conrelid = v_tbl_oid AND con.contype = 'p'
        GROUP BY con.conrelid;

        IF v_actual_pk != 'chunk_id' OR v_actual_pk IS NULL THEN
            RAISE EXCEPTION 'Pre-execution validation failed on table "rag_chunks": Expected PRIMARY KEY (chunk_id), found: %', COALESCE(v_actual_pk, 'NONE');
        END IF;

        -- Exact Foreign Key count check (expected exactly 1)
        SELECT count(*) INTO v_actual_fk_count
        FROM pg_constraint
        WHERE conrelid = v_tbl_oid AND contype = 'f';

        IF v_actual_fk_count != 1 THEN
            RAISE EXCEPTION 'Pre-execution validation failed on table "rag_chunks": Expected exactly 1 foreign key, found %.', v_actual_fk_count;
        END IF;

        -- FK 1: document_id -> public.documents(document_id) ON DELETE CASCADE
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint con
            JOIN pg_class ref_c ON ref_c.oid = con.confrelid
            JOIN pg_namespace ref_n ON ref_n.oid = ref_c.relnamespace
            WHERE con.conrelid = v_tbl_oid AND con.contype = 'f'
              AND ref_n.nspname = 'public' AND ref_c.relname = 'documents'
              AND con.confdeltype = 'c'
              AND (
                  SELECT string_agg(a.attname, ', ' ORDER BY k.ord)
                  FROM unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord)
                  JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.attnum
              ) = 'document_id'
              AND (
                  SELECT string_agg(a.attname, ', ' ORDER BY k.ord)
                  FROM unnest(con.confkey) WITH ORDINALITY AS k(attnum, ord)
                  JOIN pg_attribute a ON a.attrelid = con.confrelid AND a.attnum = k.attnum
              ) = 'document_id'
        ) THEN
            RAISE EXCEPTION 'Pre-execution validation failed on table "rag_chunks": Missing or incompatible FK document_id -> public.documents(document_id) ON DELETE CASCADE.';
        END IF;

        FOR r IN (
            SELECT unnest(ARRAY['chunk_id', 'document_id', 'chunk_type', 'title', 'content', 'token_estimate', 'metadata', 'created_at']) AS col,
                   unnest(ARRAY['text', 'text', 'text', 'text', 'text', 'integer', 'jsonb', 'timestamp with time zone']) AS exp_type,
                   unnest(ARRAY[true, true, true, true, true, true, true, true]) AS exp_notnull,
                   unnest(ARRAY[NULL, NULL, NULL, NULL, NULL, NULL, '''{}''::jsonb', 'now()']) AS exp_def
        ) LOOP
            SELECT format_type(a.atttypid, a.atttypmod), a.attnotnull, pg_get_expr(ad.adbin, ad.adrelid)
            INTO v_actual_type, v_actual_notnull, v_actual_def
            FROM pg_attribute a
            LEFT JOIN pg_attrdef ad ON ad.adrelid = a.attrelid AND ad.adnum = a.attnum
            WHERE a.attrelid = v_tbl_oid AND a.attname = r.col AND a.attnum > 0 AND NOT a.attisdropped;

            IF v_actual_type IS NULL THEN
                RAISE EXCEPTION 'Pre-execution validation failed on table "rag_chunks": Missing column "%".', r.col;
            END IF;
            IF v_actual_type != r.exp_type THEN
                RAISE EXCEPTION 'Pre-execution validation failed on table "rag_chunks": Column "%" has type %, expected %.', r.col, v_actual_type, r.exp_type;
            END IF;
            IF v_actual_notnull != r.exp_notnull THEN
                RAISE EXCEPTION 'Pre-execution validation failed on table "rag_chunks": Column "%" NOT NULL constraint mismatch.', r.col;
            END IF;
            IF r.exp_def IS NOT NULL THEN
                IF v_actual_def IS NULL THEN
                    RAISE EXCEPTION 'Pre-execution validation failed on table "rag_chunks": Column "%" is missing an expected default expression (expected: %).', r.col, r.exp_def;
                END IF;
                v_norm_def := lower(trim(replace(v_actual_def, ' ', '')));
                IF r.exp_def = 'now()' AND v_norm_def NOT IN ('now()', 'current_timestamp') THEN
                    RAISE EXCEPTION 'Pre-execution validation failed on table "rag_chunks": Column "%" default mismatch. Expected now(), found %.', r.col, v_actual_def;
                ELSIF r.exp_def = '''{}''::jsonb' AND v_norm_def NOT IN ('''{}''::jsonb', '''{}''') THEN
                    RAISE EXCEPTION 'Pre-execution validation failed on table "rag_chunks": Column "%" default mismatch. Expected ''{}''::jsonb, found %.', r.col, v_actual_def;
                END IF;
            END IF;
        END LOOP;

        -- Explicit pgvector vector(768) NOT NULL validation
        IF NOT EXISTS (
            SELECT 1 FROM pg_attribute a
            JOIN pg_type t ON t.oid = a.atttypid
            JOIN pg_namespace n ON n.oid = t.typnamespace
            WHERE a.attrelid = v_tbl_oid
              AND a.attname = 'embedding'
              AND a.attnum > 0
              AND NOT a.attisdropped
              AND n.nspname = 'extensions'
              AND t.typname = 'vector'
              AND a.atttypmod = 768
              AND a.attnotnull = true
        ) THEN
            RAISE EXCEPTION 'Pre-execution validation failed on table "rag_chunks": Column "embedding" is missing or incompatible. Must be extensions.vector(768) NOT NULL.';
        END IF;
    END IF;

    -- =========================================================================
    -- 3. COMPLETE INDEX DEFINITION & CATALOG-BASED COMPATIBILITY VALIDATION
    -- =========================================================================
    -- Validates all 16 planned indexes against existing catalog definitions.
    -- Checks:
    --   - Table match (schema public)
    --   - Access method (btree, gin, hnsw)
    --   - Index validity (indisvalid = true) and readiness (indisready = true)
    --   - Key attribute count (indnkeyatts) and total attribute count (indnatts)
    --   - No expression attributes (attnum > 0, indexprs IS NULL)
    --   - Uniqueness status (indisunique = false)
    --   - Exact ordered key columns
    --   - Operator class AND operator-class schema (extensions.vector_cosine_ops)
    --   - Partial index predicate match (idx_bus_stops_coords) and absence on non-partial indexes
    FOR r IN (
        SELECT unnest(ARRAY[
            'idx_route_services_number', 'idx_route_services_dep_bounds',
            'idx_bus_stops_normalized_name', 'idx_bus_stops_coords',
            'idx_sched_deps_stop_time', 'idx_sched_deps_route_time',
            'idx_sched_deps_route_number', 'idx_sched_deps_origin_seq',
            'idx_direct_conns_origin_dest', 'idx_direct_conns_dep_sec',
            'idx_direct_conns_gin_stops', 'idx_ingestion_log_hash_status',
            'idx_documents_type', 'idx_rag_chunks_hnsw',
            'idx_rag_chunks_type', 'idx_rag_chunks_metadata'
        ]) AS exp_name,
        unnest(ARRAY[
            'route_services', 'route_services',
            'bus_stops', 'bus_stops',
            'scheduled_departures', 'scheduled_departures',
            'scheduled_departures', 'scheduled_departures',
            'direct_connections', 'direct_connections',
            'direct_connections', 'dataset_ingestion_log',
            'documents', 'rag_chunks',
            'rag_chunks', 'rag_chunks'
        ]) AS exp_tbl,
        unnest(ARRAY[
            'btree', 'btree',
            'btree', 'btree',
            'btree', 'btree',
            'btree', 'btree',
            'btree', 'btree',
            'gin', 'btree',
            'btree', 'hnsw',
            'btree', 'gin'
        ]) AS exp_am,
        unnest(ARRAY[
            1, 2,
            1, 2,
            2, 2,
            1, 2,
            2, 1,
            1, 2,
            1, 1,
            1, 1
        ]) AS exp_nkeyatts,
        unnest(ARRAY[
            1, 2,
            1, 2,
            2, 2,
            1, 2,
            2, 1,
            1, 2,
            1, 1,
            1, 1
        ]) AS exp_natts,
        unnest(ARRAY[
            'route_number', 'earliest_departure_seconds, latest_departure_seconds',
            'normalized_stop_name', 'latitude, longitude',
            'stop_id, departure_seconds', 'route_id, departure_seconds',
            'route_number', 'stop_sequence, departure_seconds',
            'origin_stop_id, destination_stop_id', 'scheduled_departure_seconds',
            'stops_between_origin_and_t_nagar', 'dataset_file_hash, status',
            'document_type', 'embedding',
            'chunk_type', 'metadata'
        ]) AS exp_cols,
        unnest(ARRAY[
            NULL, NULL,
            NULL, NULL,
            NULL, NULL,
            NULL, NULL,
            NULL, NULL,
            NULL, NULL,
            NULL, 'vector_cosine_ops',
            NULL, NULL
        ]) AS exp_opclass,
        unnest(ARRAY[
            NULL, NULL,
            NULL, NULL,
            NULL, NULL,
            NULL, NULL,
            NULL, NULL,
            NULL, NULL,
            NULL, 'extensions',
            NULL, NULL
        ]) AS exp_opc_schema,
        unnest(ARRAY[
            NULL, NULL,
            NULL, '(map_eligible=true)',
            NULL, NULL,
            NULL, NULL,
            NULL, NULL,
            NULL, NULL,
            NULL, NULL,
            NULL, NULL
        ]) AS exp_pred
    ) LOOP
        SELECT idx_c.oid, tbl_c.relname, am.amname, idx.indisunique,
               idx.indisvalid, idx.indisready, idx.indnkeyatts, idx.indnatts,
               (idx.indexprs IS NOT NULL OR bool_or(k.attnum = 0)) AS has_expr,
               array_to_string(array_agg(a.attname ORDER BY k.ord) FILTER (WHERE k.ord <= idx.indnkeyatts), ', ') AS cols,
               pg_get_expr(idx.indpred, idx.indrelid) AS pred_expr
        INTO v_idx_oid, v_idx_tbl, v_idx_am, v_idx_uniq,
             v_idx_isvalid, v_idx_isready, v_idx_nkeyatts, v_idx_natts,
             v_idx_has_expr, v_idx_cols, v_idx_pred
        FROM pg_index idx
        JOIN pg_class idx_c ON idx_c.oid = idx.indexrelid
        JOIN pg_class tbl_c ON tbl_c.oid = idx.indrelid
        JOIN pg_namespace n ON n.oid = idx_c.relnamespace
        JOIN pg_am am ON am.oid = idx_c.relam
        LEFT JOIN unnest(idx.indkey) WITH ORDINALITY AS k(attnum, ord) ON true
        LEFT JOIN pg_attribute a ON a.attrelid = idx.indrelid AND a.attnum = k.attnum
        WHERE n.nspname = 'public' AND idx_c.relname = r.exp_name
        GROUP BY idx_c.oid, tbl_c.relname, am.amname, idx.indisunique,
                 idx.indisvalid, idx.indisready, idx.indnkeyatts, idx.indnatts,
                 idx.indexprs, idx.indpred, idx.indrelid;

        IF v_idx_oid IS NOT NULL THEN
            IF v_idx_tbl != r.exp_tbl THEN
                RAISE EXCEPTION 'Pre-execution validation failed on index "%": Indexed table mismatch. Expected %, found %.', r.exp_name, r.exp_tbl, v_idx_tbl;
            END IF;
            IF v_idx_am != r.exp_am THEN
                RAISE EXCEPTION 'Pre-execution validation failed on index "%": Access method mismatch. Expected %, found %.', r.exp_name, r.exp_am, v_idx_am;
            END IF;
            IF NOT v_idx_isvalid THEN
                RAISE EXCEPTION 'Pre-execution validation failed on index "%": Index is marked invalid (indisvalid = false).', r.exp_name;
            END IF;
            IF NOT v_idx_isready THEN
                RAISE EXCEPTION 'Pre-execution validation failed on index "%": Index is marked not ready (indisready = false).', r.exp_name;
            END IF;
            IF v_idx_uniq != false THEN
                RAISE EXCEPTION 'Pre-execution validation failed on index "%": Expected non-unique index, found unique.', r.exp_name;
            END IF;
            IF v_idx_nkeyatts != r.exp_nkeyatts THEN
                RAISE EXCEPTION 'Pre-execution validation failed on index "%": Key attribute count mismatch. Expected %, found %.', r.exp_name, r.exp_nkeyatts, v_idx_nkeyatts;
            END IF;
            IF v_idx_natts != r.exp_natts THEN
                RAISE EXCEPTION 'Pre-execution validation failed on index "%": Total attribute count mismatch (unexpected INCLUDE attributes). Expected %, found %.', r.exp_name, r.exp_natts, v_idx_natts;
            END IF;
            IF v_idx_has_expr THEN
                RAISE EXCEPTION 'Pre-execution validation failed on index "%": Expression index attributes detected, expected simple column index.', r.exp_name;
            END IF;
            IF v_idx_cols != r.exp_cols THEN
                RAISE EXCEPTION 'Pre-execution validation failed on index "%": Column list mismatch. Expected (%), found (%).', r.exp_name, r.exp_cols, v_idx_cols;
            END IF;
            IF r.exp_opclass IS NOT NULL THEN
                SELECT opc.opcname, opc_ns.nspname
                INTO v_idx_opclass, v_idx_opc_schema
                FROM pg_index idx
                JOIN unnest(idx.indclass) AS opclass_key(opclass_oid) ON true
                JOIN pg_opclass opc ON opc.oid = opclass_key.opclass_oid
                JOIN pg_namespace opc_ns ON opc_ns.oid = opc.opcnamespace
                WHERE idx.indexrelid = v_idx_oid;

                IF v_idx_opclass != r.exp_opclass OR v_idx_opc_schema != r.exp_opc_schema THEN
                    RAISE EXCEPTION 'Pre-execution validation failed on index "%": Operator class mismatch. Expected %.%, found %.%.',
                        r.exp_name, r.exp_opc_schema, r.exp_opclass, v_idx_opc_schema, v_idx_opclass;
                END IF;
            END IF;
            IF r.exp_pred IS NOT NULL THEN
                IF v_idx_pred IS NULL OR replace(replace(lower(v_idx_pred), ' ', ''), '"', '') != r.exp_pred THEN
                    RAISE EXCEPTION 'Pre-execution validation failed on index "%": Predicate mismatch. Expected %, found %.', r.exp_name, r.exp_pred, v_idx_pred;
                END IF;
            ELSE
                IF v_idx_pred IS NOT NULL THEN
                    RAISE EXCEPTION 'Pre-execution validation failed on index "%": Unexpected predicate found on non-partial index: %.', r.exp_name, v_idx_pred;
                END IF;
            END IF;
        END IF;
    END LOOP;

    RAISE NOTICE 'Pre-execution comprehensive schema validation passed: database catalog is clean and compatible.';
END $$;

-- ==============================================================================
-- 1. TRANSIT ROUTE MODEL: ROUTES & ROUTE SERVICES
-- ==============================================================================

-- ------------------------------------------------------------------------------
-- Table 1: public.routes (Canonical Public Route Line Identities)
-- Represents the 58 distinct public bus lines serving Sholinganallur (e.g., Line '102', '19', '570').
-- ------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.routes (
    route_number TEXT PRIMARY KEY,
    total_service_variants INTEGER NOT NULL,
    total_daily_trips INTEGER NOT NULL,
    earliest_system_seconds INTEGER NOT NULL,
    latest_system_seconds INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.routes IS 'Canonical public bus route line identities serving the Sholinganallur corridor.';
COMMENT ON COLUMN public.routes.route_number IS 'Primary key: Normalized public line number (e.g., 102, 19, 570, 102CT).';
COMMENT ON COLUMN public.routes.total_service_variants IS 'Total distinct operational route_id variants for this line.';
COMMENT ON COLUMN public.routes.total_daily_trips IS 'Total daily trips across all operational variants in the Sholinganallur area.';
COMMENT ON COLUMN public.routes.earliest_system_seconds IS 'Earliest departure time in seconds past midnight across all variants.';
COMMENT ON COLUMN public.routes.latest_system_seconds IS 'Latest departure time in seconds past midnight across all variants.';

-- ------------------------------------------------------------------------------
-- Table 2: public.route_services (Operational Origin-to-Destination Variants)
-- Represents the 278 operational service profiles published in the MTC GTFS feed.
-- ------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.route_services (
    route_id TEXT PRIMARY KEY,
    route_number TEXT NOT NULL REFERENCES public.routes(route_number) ON DELETE CASCADE,
    route_name TEXT NOT NULL,
    distinct_trips_in_feed INTEGER NOT NULL,
    scheduled_stop_departures_in_area INTEGER NOT NULL,
    earliest_departure_time_string TEXT NOT NULL,
    latest_departure_time_string TEXT NOT NULL,
    earliest_departure_seconds INTEGER NOT NULL,
    latest_departure_seconds INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.route_services IS 'Operational route variants published in MTC GTFS with specific terminal endpoints.';
COMMENT ON COLUMN public.route_services.route_id IS 'Primary key: Canonical GTFS route identifier.';
COMMENT ON COLUMN public.route_services.route_number IS 'Foreign key referencing public.routes(route_number).';
COMMENT ON COLUMN public.route_services.route_name IS 'Full route trajectory descriptor (e.g., BROADWAY To KELAMBAKKAM B.T).';
COMMENT ON COLUMN public.route_services.earliest_departure_time_string IS 'Canonical GTFS timestamp string (preserves >24:00 syntax).';
COMMENT ON COLUMN public.route_services.latest_departure_time_string IS 'Canonical GTFS timestamp string (preserves >24:00 syntax).';

CREATE INDEX IF NOT EXISTS idx_route_services_number ON public.route_services (route_number);
CREATE INDEX IF NOT EXISTS idx_route_services_dep_bounds ON public.route_services (earliest_departure_seconds, latest_departure_seconds);

-- ==============================================================================
-- 2. TRANSIT NETWORK MODEL: BUS STOPS & SCHEDULED DEPARTURES
-- ==============================================================================

-- ------------------------------------------------------------------------------
-- Table 3: public.bus_stops (Verified Sholinganallur Corridor Bus Stops)
-- Represents the 26 curated bus stops within the Sholinganallur area boundaries.
-- ------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.bus_stops (
    stop_id TEXT PRIMARY KEY,
    stop_code TEXT NOT NULL,
    stop_name TEXT NOT NULL,
    normalized_stop_name TEXT NOT NULL,
    latitude DOUBLE PRECISION NULL,
    longitude DOUBLE PRECISION NULL,
    coordinates_within_chennai_bounds BOOLEAN NOT NULL DEFAULT TRUE,
    map_eligible BOOLEAN NOT NULL DEFAULT TRUE,
    coordinate_status TEXT NOT NULL,
    locality_area_membership_verified BOOLEAN NOT NULL DEFAULT FALSE,
    locality_area_membership_status TEXT NOT NULL,
    coordinate_source TEXT NOT NULL,
    coordinate_verification_status TEXT NOT NULL,
    original_latitude DOUBLE PRECISION NULL,
    original_longitude DOUBLE PRECISION NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.bus_stops IS 'Curated bus stops located within the Sholinganallur transit area.';
COMMENT ON COLUMN public.bus_stops.stop_id IS 'Primary key: Unique MTC bus stop identifier.';
COMMENT ON COLUMN public.bus_stops.map_eligible IS 'Indicates whether verified non-placeholder coordinates exist for map rendering.';
COMMENT ON COLUMN public.bus_stops.coordinate_status IS 'VERIFIED, APPROXIMATED, or PLACEHOLDER coordinate quality status.';

CREATE INDEX IF NOT EXISTS idx_bus_stops_normalized_name ON public.bus_stops (normalized_stop_name);
CREATE INDEX IF NOT EXISTS idx_bus_stops_coords ON public.bus_stops (latitude, longitude) WHERE map_eligible = TRUE;

-- ------------------------------------------------------------------------------
-- Table 4: public.scheduled_departures (Time-Indexed Departure Timetable)
-- Represents the 18,439 scheduled stop departures across the Sholinganallur area.
-- ------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.scheduled_departures (
    departure_id BIGSERIAL PRIMARY KEY,
    trip_id TEXT NOT NULL,
    stop_sequence INTEGER NOT NULL,
    stop_id TEXT NOT NULL REFERENCES public.bus_stops(stop_id) ON DELETE CASCADE,
    route_id TEXT NOT NULL REFERENCES public.route_services(route_id) ON DELETE CASCADE,
    route_number TEXT NOT NULL,
    route_name TEXT NOT NULL,
    stop_name TEXT NOT NULL,
    direction_id INTEGER NOT NULL,
    arrival_time TEXT NOT NULL,
    departure_time TEXT NOT NULL,
    departure_seconds INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_scheduled_departures_trip_seq UNIQUE (trip_id, stop_sequence)
);

COMMENT ON TABLE public.scheduled_departures IS 'Individual scheduled bus stop departure events indexed for fast timetable lookup.';
COMMENT ON COLUMN public.scheduled_departures.departure_time IS 'Canonical GTFS departure time string (supports >24:00 post-midnight syntax).';
COMMENT ON COLUMN public.scheduled_departures.departure_seconds IS 'Seconds past midnight used for numeric range scans and sorting.';

-- Primary access pattern: Timetable scans at a specific stop after a specific time
CREATE INDEX IF NOT EXISTS idx_sched_deps_stop_time ON public.scheduled_departures (stop_id, departure_seconds);
-- Route-level departures ordered by time
CREATE INDEX IF NOT EXISTS idx_sched_deps_route_time ON public.scheduled_departures (route_id, departure_seconds);
-- Route line number lookups
CREATE INDEX IF NOT EXISTS idx_sched_deps_route_number ON public.scheduled_departures (route_number);
-- Origin stop queries (stop_sequence = 1)
CREATE INDEX IF NOT EXISTS idx_sched_deps_origin_seq ON public.scheduled_departures (stop_sequence, departure_seconds);

-- ==============================================================================
-- 3. TRANSIT CORRIDOR CONNECTIVITY: DIRECT CONNECTIONS
-- ==============================================================================

-- ------------------------------------------------------------------------------
-- Table 5: public.direct_connections (Direct Sholinganallur to T. Nagar Runs)
-- Represents the 170 direct trip runs connecting Sholinganallur to T. Nagar.
-- NOTE: destination_stop_id has NO foreign key to bus_stops because T. Nagar stops
-- (e.g. bZroqIhX 'CIT NAGAR') reside outside the local Sholinganallur extract.
-- ------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.direct_connections (
    trip_id TEXT PRIMARY KEY,
    route_id TEXT NOT NULL REFERENCES public.route_services(route_id) ON DELETE CASCADE,
    route_number TEXT NOT NULL,
    route_name TEXT NOT NULL,
    origin_area_id TEXT NOT NULL,
    origin_area_name TEXT NOT NULL,
    origin_stop_id TEXT NOT NULL REFERENCES public.bus_stops(stop_id) ON DELETE CASCADE,
    origin_stop_name TEXT NOT NULL,
    scheduled_departure_at_origin TEXT NOT NULL,
    destination_stop_id TEXT NOT NULL,
    destination_stop_name TEXT NOT NULL,
    scheduled_arrival_at_t_nagar_stop TEXT NOT NULL,
    scheduled_departure_seconds INTEGER NOT NULL,
    scheduled_arrival_seconds INTEGER NOT NULL,
    duration_minutes NUMERIC(5, 2) NOT NULL,
    intermediate_stop_count INTEGER NOT NULL,
    stops_between_origin_and_t_nagar JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.direct_connections IS 'Direct transit trip runs from Sholinganallur to T. Nagar corridor.';
COMMENT ON COLUMN direct_connections.destination_stop_id IS 'Destination stop ID in T. Nagar (intentionally unconstrained by bus_stops FK).';
COMMENT ON COLUMN public.direct_connections.stops_between_origin_and_t_nagar IS 'JSONB array of ordered intermediate stop objects between origin and destination.';

CREATE INDEX IF NOT EXISTS idx_direct_conns_origin_dest ON public.direct_connections (origin_stop_id, destination_stop_id);
CREATE INDEX IF NOT EXISTS idx_direct_conns_dep_sec ON public.direct_connections (scheduled_departure_seconds);
CREATE INDEX IF NOT EXISTS idx_direct_conns_gin_stops ON public.direct_connections USING gin (stops_between_origin_and_t_nagar);

-- ==============================================================================
-- 4. ETL AUDIT LOG: DATASET INGESTION LOG
-- ==============================================================================

-- ------------------------------------------------------------------------------
-- Table 6: public.dataset_ingestion_log (ETL Pipeline Execution & Audit Trail)
-- Tracks the provenance, file hashes, and record counts of data ingestions.
-- ------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.dataset_ingestion_log (
    ingestion_id BIGSERIAL PRIMARY KEY,
    dataset_version TEXT NOT NULL,
    dataset_file_hash TEXT NOT NULL,
    ingestion_stage TEXT NOT NULL,
    status TEXT NOT NULL,
    total_records_ingested INTEGER NOT NULL DEFAULT 0,
    quarantined_records_count INTEGER NOT NULL DEFAULT 0,
    error_message TEXT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ NULL,
    duration_seconds NUMERIC(8, 2) NULL
);

COMMENT ON TABLE public.dataset_ingestion_log IS 'Audit ledger recording dataset ETL pipeline executions and row counts.';

CREATE INDEX IF NOT EXISTS idx_ingestion_log_hash_status ON public.dataset_ingestion_log (dataset_file_hash, status);

-- ==============================================================================
-- 5. SEMANTIC RAG KNOWLEDGE STORE: DOCUMENTS & RAG CHUNKS (PGVECTOR)
-- ==============================================================================

-- ------------------------------------------------------------------------------
-- Table 7: public.documents (RAG Document Manifest)
-- Canonical parent documents containing journey narratives, route profiles, and advisories.
-- ------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.documents (
    document_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    document_type TEXT NOT NULL,
    source_file TEXT NOT NULL,
    file_hash TEXT NOT NULL,
    total_chunks INTEGER NOT NULL DEFAULT 1,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.documents IS 'Parent knowledge documents for semantic retrieval and context grounding.';
COMMENT ON COLUMN public.documents.document_id IS 'Primary key: Unique identifier for the synthesized transit document.';

CREATE INDEX IF NOT EXISTS idx_documents_type ON public.documents (document_type);

-- ------------------------------------------------------------------------------
-- Table 8: public.rag_chunks (768-dimensional Vector Embeddings)
-- Stores the 145 semantic chunks embedded using Google gemini-embedding-001.
-- Schema-qualified extensions.vector(768) ensures full Supabase pgvector compatibility.
-- ------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.rag_chunks (
    chunk_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES public.documents(document_id) ON DELETE CASCADE,
    chunk_type TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    token_estimate INTEGER NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    embedding extensions.vector(768) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.rag_chunks IS 'Semantic document chunks with 768-dim Gemini embeddings for similarity search.';
COMMENT ON COLUMN public.rag_chunks.embedding IS '768-dimensional vector embedding generated by gemini-embedding-001.';

-- HNSW Vector Index with cosine distance operator class (extensions.vector_cosine_ops)
-- Provides sublinear logarithmic approximate nearest-neighbor search.
CREATE INDEX IF NOT EXISTS idx_rag_chunks_hnsw 
ON public.rag_chunks USING hnsw (embedding extensions.vector_cosine_ops);

-- Supporting categorical and metadata indexes
CREATE INDEX IF NOT EXISTS idx_rag_chunks_type ON public.rag_chunks (chunk_type);
CREATE INDEX IF NOT EXISTS idx_rag_chunks_metadata ON public.rag_chunks USING gin (metadata);

-- ------------------------------------------------------------------------------
-- COMMIT TRANSACTION
-- ------------------------------------------------------------------------------
COMMIT;
