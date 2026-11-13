# Phase 4 Step 4.4 — RAG Documents and Embeddings Ingestion Report

**Status:** Completed & Validated (Preflight Dry-Run & Unit Tests Passed; Live Database Writes Held Behind Explicit Safety Flag)  
**Date:** 2026-11-09  
**Target Environment:** Supabase PostgreSQL (Managed PostgreSQL with Row-Level Security enabled)  
**Ingestion Driver:** Psycopg 3 (`psycopg` v3.2.1) with `pgvector` extension  
**Embedding Model:** `gemini-embedding-001` (Dimensions: 768, Type: `extensions.vector(768)`)  
**Project:** Sholinganallur_RouteWise  

---

## 1. Executive Summary

Phase 4 Step 4.4 implements the production-grade ingestion service and command-line interface (`scripts/ingest_rag.py`) for the semantic RAG knowledge store.

The pipeline ingests:
1. **86 synthesized transit knowledge documents** into `public.documents`.
2. **145 semantic chunks with 768-dimensional vector embeddings** into `public.rag_chunks`.
3. **Execution audit trail** into `public.dataset_ingestion_log` under stage `RAG_DOCUMENTS_AND_EMBEDDINGS`.

### Key Highlights
- **Zero Schema Modifications:** Operates strictly within the approved 8-table migration schema (`database/migrations/001_initial_schema.sql`) and maintains all RLS policies intact.
- **Strict Topological FK Order:** Inserts canonical parent records into `public.documents` before child chunks into `public.rag_chunks` (`rag_chunks.document_id REFERENCES documents(document_id) ON DELETE CASCADE`).
- **Cryptographic & Semantic Alignment:** 100% of the 145 vector files were verified against chunk IDs, SHA-256 chunk hashes, finite float checks, and dimensionality (768).
- **Native pgvector Adaptation:** Adapts vector float arrays into `pgvector.Vector` objects, dumped with PostgreSQL vector OID via `pgvector.psycopg.register_vector`.
- **Atomic Transaction & Idempotency:** Executes within a single transaction block (`with conn.transaction():`) using `ON CONFLICT (...) DO UPDATE` clauses that preserve primary-key identities (`document_id`, `chunk_id`).
- **Batching & Performance:** Batches `rag_chunks` insertion in chunks of 50 (3 batches: 50, 50, 45) using parameterized queries.
- **Safety Compliance:** Dry-run preflight is the default mode. Real database writes are strictly gated behind an explicit `--confirm` / `--execute` flag. No live database writes were performed during implementation. Credentials in logs and terminal outputs are automatically redacted.

---

## 2. Ingestion Architecture & Data Flow

```
[data/documents/documents_manifest.json] (86 documents)
       │
       ├──> join with individual doc JSONs (content & hashes)
       └──> calculate total_chunks from chunks_manifest
                   │
                   ▼
       Level 0: public.documents (86 records)
                   │
                   │ (Foreign Key CASCADE)
                   ▼
[data/chunks/chunks_manifest.json] (145 chunks)
       │
       ├──> join with individual chunk JSONs (chunk_text)
       └──> join with [data/embeddings/vectors/*.json] (768-dim embeddings)
                   │
                   ▼
       Level 1: public.rag_chunks (145 records, batched)
                   │
                   ▼
       public.dataset_ingestion_log (Audit trail: IN_PROGRESS -> SUCCESS/FAILED)
```

### 2.1 Entity Relationship & Topological Dependency

1. **`public.documents` (Level 0 - Parent Entity):**
   - **Primary Key:** `document_id TEXT PRIMARY KEY`.
   - **Records:** Exactly 86 synthesized transit documents:
     - 26 Bus Stop Profiles (`doc_stop_{stop_id}`)
     - 58 Route Profiles (`doc_route_{slug}_{hash}`)
     - 1 Corridor Travel Guide (`doc_corridor_sholinganallur_t_nagar`)
     - 1 System Governance & Limitations (`doc_system_governance_limitations`)
   - **Columns:** `document_id`, `title`, `document_type`, `source_file`, `file_hash`, `total_chunks`, `metadata`, `created_at`.
   - **Conflict Target:** `ON CONFLICT (document_id) DO UPDATE SET title = EXCLUDED.title, document_type = EXCLUDED.document_type, source_file = EXCLUDED.source_file, file_hash = EXCLUDED.file_hash, total_chunks = EXCLUDED.total_chunks, metadata = EXCLUDED.metadata;`
   - **Key Identity:** `document_id` and `created_at` are omitted from `DO UPDATE SET`, strictly preserving primary-key identity and creation timestamps.

2. **`public.rag_chunks` (Level 1 - Child Entity):**
   - **Primary Key:** `chunk_id TEXT PRIMARY KEY`.
   - **Foreign Key:** `document_id TEXT NOT NULL REFERENCES public.documents(document_id) ON DELETE CASCADE`.
   - **Records:** Exactly 145 semantic chunks.
   - **Columns:** `chunk_id`, `document_id`, `chunk_type`, `title`, `content`, `token_estimate`, `metadata`, `embedding`, `created_at`.
   - **Embedding Specification:** `extensions.vector(768) NOT NULL`.
   - **Index:** `idx_rag_chunks_hnsw` using HNSW with cosine distance operator class (`extensions.vector_cosine_ops`).
   - **Conflict Target:** `ON CONFLICT (chunk_id) DO UPDATE SET document_id = EXCLUDED.document_id, chunk_type = EXCLUDED.chunk_type, title = EXCLUDED.title, content = EXCLUDED.content, token_estimate = EXCLUDED.token_estimate, metadata = EXCLUDED.metadata, embedding = EXCLUDED.embedding;`
   - **Key Identity:** `chunk_id` and `created_at` are omitted from `DO UPDATE SET`.

3. **`public.dataset_ingestion_log` (ETL Audit):**
   - **Stage:** `RAG_DOCUMENTS_AND_EMBEDDINGS`.
   - **Dataset Version:** `v1.0.0-sholinganallur`.
   - **Composite Hash:** `fa3913a19b241923329ef2854b5f377d9649450e1fb0e392059591a27fbcf33e`.
   - **Lifecycle:** Records `IN_PROGRESS` at transaction start; commits `SUCCESS` with `total_records_ingested = 231` (86 docs + 145 chunks), timestamps, and elapsed duration.

---

## 3. Source Artifacts & Cryptographic Manifest

The preflight validation pipeline verifies the existence, manifest integrity, and cryptographic checksums of all source artifacts prior to ingestion:

| Source Artifact Location | Expected Count | Ingestion Role | SHA-256 Digest |
| :--- | :--- | :--- | :--- |
| `data/documents/documents_manifest.json` | 86 documents | Manifest & metadata | `050a498b8137357c91d4e680072b22b1c8f1a7dbe899988da618bfd118fc2666` |
| `data/documents/doc_*.json` | 86 files | Document text & provenance | Verified 1:1 against manifest |
| `data/chunks/chunks_manifest.json` | 145 chunks | Chunk manifest & tokens | `2233b246a482b434eb066dcbf8b846e9df528ca7e84992451f2be58f62f026a2` |
| `data/chunks/*.json` | 145 files | Chunk content (`chunk_text`) | Verified 1:1 against manifest |
| `data/embeddings/vectors/*.json` | 145 files | 768-dim float arrays | Verified 1:1 against chunk hashes |
| `data/embeddings/checkpoints/batch_checkpoint.json` | 1 checkpoint | Embedding pipeline ledger | `c876228dc2b2b1a9e320f77ea9d1cfdbecefb7412ca6717eb8a8ce6eb10f0f42` |

**Composite SHA-256 Checksum:** `fa3913a19b241923329ef2854b5f377d9649450e1fb0e392059591a27fbcf33e`  
**Total Records to Ingest:** 231 (86 documents + 145 rag_chunks).

---

## 4. Preflight Validation & Dry-Run Execution Results

Running `python scripts/ingest_rag.py --dry-run` executes the complete preflight pipeline without establishing write transactions or modifying the database:

```text
$ python scripts/ingest_rag.py --dry-run
2026-11-09 08:12:49 [INFO] ingest_rag_cli: Target Database: postgresql://postgres.vaefuoisfjhfmtzixdie:***REDACTED***@aws-0-ap-southeast-1.pooler.supabase.com:5432/postgres
2026-11-09 08:12:49 [INFO] ingest_rag_cli: Source Data Directory: C:\Users\Kerthika M\Documents\AI Project\Sholinganallur_RouteWise\data
2026-11-09 08:12:49 [INFO] ingest_rag_cli: Executing dry-run preflight validation...

===========================================================================
ROUTEWISE RAG DOCUMENTS & EMBEDDINGS PREFLIGHT REPORT (DRY-RUN)
===========================================================================
Data Directory:       C:\Users\Kerthika M\Documents\AI Project\Sholinganallur_RouteWise\data
Composite SHA-256:    fa3913a19b241923329ef2854b5f377d9649450e1fb0e392059591a27fbcf33e
Detected Model:       gemini-embedding-001 (Dimensions: 768)
---------------------------------------------------------------------------
Entity                    Expected     Found        Status
---------------------------------------------------------------------------
documents                 86           86           MATCH [OK]
rag_chunks                145          145          MATCH [OK]
vector_files              145          145          MATCH [OK]
---------------------------------------------------------------------------
Foreign-Key Integrity (chunks -> docs): PASSED [OK]
Primary-Key Uniqueness (doc_id & chunk_id): PASSED [OK]
Vector File Hashes & Alignment: PASSED [OK]
Vector Dimensions & Finite Values: PASSED [OK]
===========================================================================

[PASS] All preflight checks passed successfully.
Safety Notice: Dry-run mode completed. No database writes were performed.
To execute live database ingestion, run with: python scripts/ingest_rag.py --confirm
```

---

## 5. pgvector Registration & Safe Read-Only Smoke Test Verification

### 5.1 Schema `extensions` Type Registration Fix

Supabase PostgreSQL installs `pgvector` into the dedicated `extensions` schema. To ensure seamless serialization and deserialization with Psycopg 3:
1. `register_vector()` was updated to resolve type information explicitly from `extensions.vector` via `TypeInfo.fetch(conn, "extensions.vector")`.
2. `register_pgvector()` was updated to explicitly invoke `register_vector(conn, schema="extensions")`.
3. Error handling was hardened: failures on real database connections are **never** silently swallowed; an explicit, informative `RuntimeError("pgvector registration in 'extensions' schema failed: ...")` is raised.
4. Unit-test mock connections (`MagicMock`, `Mock`, or objects lacking `adapters`) remain fully compatible and bypass server catalog registration without raising false positives.

### 5.2 Safe Read-Only Vector Adaptation Smoke Test (Live Supabase Verification)

A read-only smoke-test command was added to verify end-to-end vector adaptation without writing, updating, or deleting any rows in Supabase:
- **Command:** `python scripts/ingest_rag.py --smoke-test-vector`
- **Execution Flow:**
  1. Opens a synchronous connection via `get_sync_connection()`.
  2. Registers pgvector with `schema="extensions"`.
  3. Creates a test `Vector` of 768 finite float values.
  4. Executes a parameterized query: `SELECT %s::extensions.vector(768) AS test_vec;`.
  5. Validates that the returned vector has exactly 768 dimensions and finite float values.
  6. Confirms that 0 rows were modified in Supabase.
  7. Credentials are masked across all outputs.

#### Live Execution Output:
```text
$ python scripts/ingest_rag.py --smoke-test-vector
2026-11-09 09:14:22 [INFO] ingest_rag_cli: Target Database: postgresql://postgres.vaefuoisfjhfmtzixdie:***REDACTED***@aws-0-ap-southeast-1.pooler.supabase.com:5432/postgres
2026-11-09 09:14:22 [INFO] ingest_rag_cli: Source Data Directory: C:\Users\Kerthika M\Documents\AI Project\Sholinganallur_RouteWise\data
2026-11-09 09:14:22 [INFO] ingest_rag_cli: Executing safe read-only pgvector adaptation smoke test against PostgreSQL...
2026-11-09 09:14:23 [INFO] rag_ingestion: pgvector smoke test PASSED: successfully adapted and round-tripped 768-dimension vector in schema 'extensions' (0 rows modified).

======================================================================
ROUTEWISE PGVECTOR READ-ONLY SMOKE TEST REPORT
======================================================================
Schema Registered:        extensions
Target Vector Type:       extensions.vector(768)
Expected Dimensions:      768
Returned Dimensions:      768
Parameter Adaptation:     PASSED [OK]
Result Serialization:     PASSED [OK]
Database Writes:          0 rows modified [SAFE - READ ONLY]
Status:                   SUCCESS [OK]
======================================================================

[PASS] Read-only 768-dim pgvector round-trip verified successfully.
```

---

## 6. Automated Unit Test Verification

A dedicated automated test suite was implemented in `tests/test_rag_ingestion.py` covering preflight validation, foreign key ordering, parameterization, pgvector format conversion, pgvector registration error handling, read-only smoke testing, idempotency, rollback on errors, and mock database verification.

All 36 tests executed and passed cleanly:

| Test Case | Scope / Assertion | Result |
| :--- | :--- | :--- |
| `test_01_preflight_passes_on_actual_source_artifacts` | Validates actual 86 docs, 145 chunks, and 145 vectors against all constraints | PASSED |
| `test_02_preflight_detects_missing_document_manifest` | Asserts failure when `documents_manifest.json` is missing | PASSED |
| `test_03_preflight_detects_document_count_mismatch` | Asserts failure when document count diverges from 86 | PASSED |
| `test_04_preflight_detects_chunk_count_mismatch` | Asserts failure when chunk count diverges from 145 | PASSED |
| `test_05_preflight_detects_dangling_chunk_foreign_key` | Asserts failure when a chunk references an unknown `document_id` | PASSED |
| `test_06_preflight_detects_vector_hash_mismatch` | Asserts failure when vector `chunk_hash` disagrees with chunk manifest | PASSED |
| `test_07_preflight_detects_invalid_vector_dimensions` | Asserts failure when vector dimension is not 768 (e.g., 512) | PASSED |
| `test_08_preflight_detects_non_finite_vector_values` | Asserts failure when embedding contains `NaN` or `Inf` | PASSED |
| `test_09_preflight_detects_unexpected_model_name` | Asserts failure when vector model is not `gemini-embedding-001` | PASSED |
| `test_10_ingest_all_executes_documents_before_chunks` | Confirms strict FK dependency order: `documents` inserted before `rag_chunks` | PASSED |
| `test_11_ingest_all_uses_parameterized_queries` | Confirms `%s` parameterization on all queries; zero string interpolation | PASSED |
| `test_12_ingest_all_conflict_targets_match_schema_pks` | Confirms `ON CONFLICT (document_id)` and `ON CONFLICT (chunk_id)` preserve PKs | PASSED |
| `test_13_ingest_all_uses_jsonb_adaptation` | Confirms `Jsonb` wrapping for document and chunk metadata | PASSED |
| `test_14_ingest_all_preserves_vector_dimensions_and_values` | Confirms vector objects passed to driver preserve 768 dimensions and exact float values | PASSED |
| `test_15_ingest_all_rolls_back_on_error` | Simulates database error; verifies `ROLLBACK`, exception raising, and error logging | PASSED |
| `test_16_verify_database_detects_discrepancy` | Verifies count check logic flags under-populated or mismatched tables | PASSED |
| `test_17_verify_database_passes_on_matching_counts` | Verifies clean pass when DB counts match 86 documents and 145 rag_chunks | PASSED |
| `test_18_preflight_detects_missing_chunk_json_file` | Asserts failure when an individual `data/chunks/{chunk_id}.json` file is missing | PASSED |
| `test_19_preflight_detects_missing_vector_json_file` | Asserts failure when an individual `data/embeddings/vectors/{chunk_id}.json` is missing | PASSED |
| `test_20_preflight_detects_duplicate_vector_chunk_id` | Asserts failure when multiple vector files reuse the same `chunk_id` | PASSED |
| `test_21_preflight_detects_orphaned_vector_file` | Asserts failure when a vector's `chunk_id` is missing from the chunk manifest | PASSED |
| `test_22_preflight_detects_empty_chunk_content` | Asserts failure when a chunk JSON file has empty or whitespace-only content | PASSED |
| `test_23_preflight_detects_incomplete_vector_set` | Asserts failure when complete manifest chunk set != complete vector chunk set | PASSED |
| `test_24_load_chunks_and_vectors_raises_on_missing_chunk_file` | Asserts `FileNotFoundError` raised when a chunk JSON file is missing | PASSED |
| `test_25_load_chunks_and_vectors_raises_on_missing_vector_file` | Asserts `FileNotFoundError` raised when a vector JSON file is missing | PASSED |
| `test_26_load_chunks_and_vectors_raises_on_empty_content` | Asserts `ValueError` raised when a chunk has empty text content | PASSED |
| `test_27_load_chunks_and_vectors_raises_on_hash_mismatch` | Asserts `ValueError` raised when vector `chunk_hash` does not match manifest | PASSED |
| `test_28_load_chunks_and_vectors_raises_on_dimension_mismatch` | Asserts `ValueError` raised when embedding vector dimension is != 768 | PASSED |
| `test_29_load_chunks_and_vectors_raises_on_non_finite_values` | Asserts `ValueError` raised when embedding vector contains `NaN` or `Inf` | PASSED |
| `test_30_load_documents_raises_on_missing_file_or_empty_content` | Asserts `FileNotFoundError` or `ValueError` raised when a document file is missing or empty | PASSED |
| `test_31_register_pgvector_calls_register_vector_with_schema_extensions` | Asserts `register_pgvector` invokes `register_vector(conn, schema="extensions")` | PASSED |
| `test_32_register_pgvector_raises_on_real_connection_failure` | Asserts real connection failure raises `RuntimeError` explaining schema registration failure | PASSED |
| `test_33_register_pgvector_mock_connection_compatibility` | Asserts mock connection gracefully skips registration without error | PASSED |
| `test_34_smoke_test_pgvector_success_mock` | Asserts `smoke_test_pgvector` succeeds and confirms 768 dimensions with zero writes | PASSED |
| `test_35_smoke_test_pgvector_dimension_mismatch_raises` | Asserts `smoke_test_pgvector` raises `ValueError` if returned vector dimension != 768 | PASSED |
| `test_36_smoke_test_pgvector_non_finite_value_raises` | Asserts `smoke_test_pgvector` raises `ValueError` if returned vector contains `NaN`/`Inf` | PASSED |

### Full Repository Regression Test
- **Total Test Suite:** 125 tests across the entire repository (`tests/test_*.py`)
- **Execution Result:** `125 passed in 48.07s` (0 failures, 0 errors, 0 regressions)

---

## 7. CLI Usage Instructions

The CLI script `scripts/ingest_rag.py` provides four primary operational modes:

### 1. Dry-Run Mode (Default / Read-Only Preflight)
Performs all manifest verifications, document-to-chunk FK integrity checks, vector SHA-256 hash checks, dimension checks (768), and model validation without connecting or writing to PostgreSQL:
```bash
python scripts/ingest_rag.py --dry-run
```
*(Running `python scripts/ingest_rag.py` without flags defaults safely to dry-run mode).*

### 2. Read-Only Vector Smoke Test Mode
Executes a safe round-trip query casting a test vector to `extensions.vector(768)` to verify Psycopg 3 adaptation against live Supabase without modifying any data:
```bash
python scripts/ingest_rag.py --smoke-test-vector
```

### 3. Live Ingestion Mode (Requires Explicit Confirmation)
Executes atomic transaction ingestion against Supabase PostgreSQL:
```bash
python scripts/ingest_rag.py --confirm
```
*Optional parameters:*
- `--batch-size <N>`: Adjust batch size for chunk inserts (default: `50`).
- `--data-dir <PATH>`: Custom base data directory (default: `data`).

### 4. Post-Ingestion Verification Mode
Queries live Supabase tables to verify row counts against canonical specifications and displays the latest RAG ingestion log entry:
```bash
python scripts/ingest_rag.py --verify
```

---

## 8. Safety, Security & Compliance Audit

1. **Zero Premature Live Writes:** In compliance with Phase 4 safety boundaries, no writes were executed against the remote Supabase database during implementation. Live execution is strictly gated behind `--confirm`.
2. **Credential Redaction:** The database connection string from `.env` is loaded strictly via environment variables. The CLI and logging utilities automatically mask credentials (`postgresql://postgres...:***REDACTED***@...`) across all terminal and log outputs.
3. **Transactional Integrity:** All DML operations run within `with conn.transaction():`. Any database exception triggers an immediate rollback, preventing partial ingestion.
4. **Data Fidelity & Zero Re-Embedding:** Embeddings are read directly from `data/embeddings/vectors/` without alteration, truncation, or re-computation. Zero calls to Gemini API were made.
5. **Preservation of Local Artifacts:** All 86 documents, 145 chunks, 145 vector files, and manifests remain intact.

