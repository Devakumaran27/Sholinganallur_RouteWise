"""
Script: scripts/ingest_rag.py
Purpose: CLI entry point for Phase 4 Step 4.4 — RAG Documents and Embeddings Ingestion.
         Supports dry-run preflight, database ingestion, and database verification.

Usage Examples:
    # 1. Safe dry-run preflight (default mode):
    python scripts/ingest_rag.py
    python scripts/ingest_rag.py --dry-run

    # 2. Database verification (compares live DB table counts against expected counts):
    python scripts/ingest_rag.py --verify

    # 3. Live database ingestion (requires explicit --confirm / --execute):
    python scripts/ingest_rag.py --confirm
    python scripts/ingest_rag.py --execute --batch-size 50
"""

import argparse
import logging
import sys
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import settings
from app.db.connections import get_sync_connection, mask_database_url
from app.db.rag_ingestion import (
    DEFAULT_BATCH_SIZE,
    EXPECTED_CHUNKS_COUNT,
    EXPECTED_DOCUMENTS_COUNT,
    EXPECTED_EMBEDDING_DIMENSIONS,
    EXPECTED_EMBEDDING_MODEL,
    EXPECTED_VECTORS_COUNT,
    RAGIngestionService,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("ingest_rag_cli")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="RouteWise RAG Documents & Vector Embeddings Ingestion CLI (Phase 4 Step 4.4)"
    )

    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Run complete preflight validation without writing to PostgreSQL (default behavior).",
    )
    mode_group.add_argument(
        "--execute",
        "--confirm",
        dest="execute",
        action="store_true",
        default=False,
        help="Explicit confirmation required to execute database writes.",
    )
    mode_group.add_argument(
        "--verify",
        "--status",
        dest="verify",
        action="store_true",
        default=False,
        help="Query PostgreSQL and compare actual table row counts against canonical specifications.",
    )
    mode_group.add_argument(
        "--smoke-test-vector",
        "--test-vector",
        dest="smoke_test_vector",
        action="store_true",
        default=False,
        help="Execute a safe, read-only vector adaptation smoke test against PostgreSQL without writing data.",
    )

    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Batch size for inserting rag_chunks (default: {DEFAULT_BATCH_SIZE}).",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=PROJECT_ROOT / "data",
        help="Base data directory containing documents, chunks, and embeddings (default: data).",
    )

    return parser


def run_preflight_cli(service: RAGIngestionService) -> int:
    """Execute dry-run preflight validation and display formatted report."""
    logger.info("Executing dry-run preflight validation...")
    summary = service.run_preflight()

    print("\n" + "=" * 75)
    print("ROUTEWISE RAG DOCUMENTS & EMBEDDINGS PREFLIGHT REPORT (DRY-RUN)")
    print("=" * 75)
    print(f"Data Directory:       {service.data_dir}")
    print(f"Composite SHA-256:    {summary.composite_hash}")
    print(f"Detected Model:       {summary.model_name} (Dimensions: {EXPECTED_EMBEDDING_DIMENSIONS})")
    print("-" * 75)
    print(f"{'Entity':<25} {'Expected':<12} {'Found':<12} {'Status'}")
    print("-" * 75)

    entities = [
        ("documents", EXPECTED_DOCUMENTS_COUNT, summary.doc_count),
        ("rag_chunks", EXPECTED_CHUNKS_COUNT, summary.chunk_count),
        ("vector_files", EXPECTED_VECTORS_COUNT, summary.vector_count),
    ]

    for name, expected, found in entities:
        match_status = "MATCH [OK]" if found == expected else "MISMATCH [FAIL]"
        print(f"{name:<25} {expected:<12} {found:<12} {match_status}")

    print("-" * 75)
    print(f"Foreign-Key Integrity (chunks -> docs): {'PASSED [OK]' if summary.fk_integrity_valid else 'FAILED [ERROR]'}")
    print(f"Primary-Key Uniqueness (doc_id & chunk_id): {'PASSED [OK]' if summary.pk_uniqueness_valid else 'FAILED [ERROR]'}")
    print(f"Vector File Hashes & Alignment: {'PASSED [OK]' if summary.vectors_valid else 'FAILED [ERROR]'}")
    print(f"Vector Dimensions & Finite Values: {'PASSED [OK]' if summary.dimensions_valid else 'FAILED [ERROR]'}")
    print("=" * 75)

    if summary.warnings:
        print("\nWarnings:")
        for w in summary.warnings:
            print(f"  [WARN] {w}")

    if not summary.valid:
        print("\nPreflight Validation Errors:")
        for err in summary.errors:
            print(f"  [ERROR] {err}")
        print("\n[FAIL] Preflight validation failed. Correct the errors before ingesting.")
        return 1

    print("\n[PASS] All preflight checks passed successfully.")
    print("Safety Notice: Dry-run mode completed. No database writes were performed.")
    print("To execute live database ingestion, run with: python scripts/ingest_rag.py --confirm")
    return 0


def run_verify_cli(service: RAGIngestionService) -> int:
    """Execute live database verification and compare counts."""
    logger.info("Connecting to database for verification...")
    try:
        with get_sync_connection() as conn:
            result = service.verify_database(conn)

            print("\n" + "=" * 70)
            print("ROUTEWISE RAG DATABASE VERIFICATION REPORT")
            print("=" * 70)
            print(f"{'Table':<25} {'Expected':<12} {'Actual':<12} {'Status'}")
            print("-" * 70)

            for table, expected in result.expected_counts.items():
                actual = result.actual_counts.get(table, 0)
                status = "MATCH [OK]" if actual == expected else "MISMATCH [FAIL]"
                print(f"{table:<25} {expected:<12} {actual:<12} {status}")

            print("=" * 70)

            if result.latest_ingestion_log:
                log = result.latest_ingestion_log
                print("\nLatest RAG Ingestion Log Entry:")
                print(f"  Ingestion ID:     {log.get('ingestion_id')}")
                print(f"  Dataset Version:  {log.get('dataset_version')}")
                print(f"  Stage:            {log.get('ingestion_stage')}")
                print(f"  Status:           {log.get('status')}")
                print(f"  Records Ingested: {log.get('total_records_ingested')}")
                print(f"  Started At:       {log.get('started_at')}")
                print(f"  Completed At:     {log.get('completed_at')}")
                print(f"  Duration:         {log.get('duration_seconds')}s")
                if log.get("error_message"):
                    print(f"  Error Message:    {log.get('error_message')}")
            else:
                print("\n[INFO] No previous RAG ingestion log entry found in public.dataset_ingestion_log.")

            if not result.all_matched:
                print("\n[FAIL] Database verification failed: actual table row counts diverge from specifications.")
                return 1

            print("\n[PASS] Database verification succeeded: all RAG tables match expected specifications.")
            return 0

    except Exception as exc:
        logger.error(f"Database verification failed: {exc}")
        return 1


def run_smoke_test_cli(service: RAGIngestionService) -> int:
    """Execute read-only pgvector adaptation smoke test and display report."""
    logger.info("Executing safe read-only pgvector adaptation smoke test against PostgreSQL...")
    try:
        result = service.smoke_test_pgvector()

        print("\n" + "=" * 70)
        print("ROUTEWISE PGVECTOR READ-ONLY SMOKE TEST REPORT")
        print("=" * 70)
        print(f"Schema Registered:        {result.get('schema')}")
        print(f"Target Vector Type:       {result.get('schema')}.vector({result.get('expected_dimensions')})")
        print(f"Expected Dimensions:      {result.get('expected_dimensions')}")
        print(f"Returned Dimensions:      {result.get('actual_dimensions')}")
        print(f"Parameter Adaptation:     PASSED [OK]")
        print(f"Result Serialization:     PASSED [OK]")
        print(f"Database Writes:          0 rows modified [SAFE - READ ONLY]")
        print(f"Status:                   {result.get('status')} [OK]")
        print("=" * 70)
        print(f"\n[PASS] {result.get('message')}")
        return 0
    except Exception as exc:
        logger.error(f"Vector smoke test failed: {exc}")
        print(f"\n[FAIL] Vector smoke test failed: {exc}")
        return 1


def run_ingest_cli(service: RAGIngestionService, batch_size: int) -> int:
    """Execute live database ingestion with explicit confirmation."""
    logger.info("Starting live transactional RAG ingestion...")
    try:
        with get_sync_connection() as conn:
            result = service.ingest_all(conn=conn, batch_size=batch_size)

            if not result.success:
                print(f"\n[FAIL] Ingestion failed: {result.error_message}")
                return 1

            print("\n" + "=" * 70)
            print("ROUTEWISE RAG DATABASE INGESTION SUCCESS")
            print("=" * 70)
            print(f"Ingestion Log ID:    {result.ingestion_log_id}")
            print(f"Stage:               {result.stage}")
            print(f"Dataset Version:     {result.dataset_version}")
            print(f"Total Records:       {result.total_records}")
            print(f"Elapsed Time:        {result.duration_seconds:.2f}s")
            print("-" * 70)
            for tbl, cnt in result.counts_ingested.items():
                print(f"  {tbl:<25}: {cnt}")
            print("=" * 70)
            print("\n[SUCCESS] Transaction committed. All RAG documents and embeddings successfully ingested.")
            return 0

    except Exception as exc:
        logger.error(f"Failed to execute ingestion: {exc}")
        return 1


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    # Log masked DB target
    masked_url = mask_database_url(settings.DATABASE_URL)
    logger.info(f"Target Database: {masked_url}")
    logger.info(f"Source Data Directory: {args.data_dir.resolve()}")

    service = RAGIngestionService(data_dir=args.data_dir)

    if args.verify:
        return run_verify_cli(service)
    elif args.smoke_test_vector:
        return run_smoke_test_cli(service)
    elif args.execute:
        return run_ingest_cli(service, batch_size=args.batch_size)
    else:
        # Default mode is dry-run preflight
        return run_preflight_cli(service)


if __name__ == "__main__":
    sys.exit(main())

