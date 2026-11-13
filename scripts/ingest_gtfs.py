"""
Script: scripts/ingest_gtfs.py
Purpose: CLI entry point for Phase 4 Step 4.3 — Structured GTFS Dataset Ingestion.
         Supports dry-run preflight, database ingestion, and database verification.

Usage Examples:
    # 1. Safe dry-run preflight (default mode):
    python scripts/ingest_gtfs.py
    python scripts/ingest_gtfs.py --dry-run

    # 2. Database verification (compares live DB table counts against expected counts):
    python scripts/ingest_gtfs.py --verify

    # 3. Live database ingestion (requires explicit --confirm / --execute):
    python scripts/ingest_gtfs.py --confirm
    python scripts/ingest_gtfs.py --execute --batch-size 2000
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
from app.db.gtfs_ingestion import (
    DEFAULT_BATCH_SIZE,
    EXPECTED_COUNTS,
    GTFSIngestionService,
    mask_database_url,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("ingest_gtfs_cli")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="RouteWise GTFS Structured Dataset Ingestion CLI (Phase 4 Step 4.3)"
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

    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Batch size for inserting scheduled_departures (default: {DEFAULT_BATCH_SIZE}).",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=PROJECT_ROOT / "data" / "processed",
        help="Directory containing the processed JSON files (default: data/processed).",
    )
    parser.add_argument(
        "--dataset-version",
        type=str,
        default="1.0.0",
        help="Dataset version string recorded in dataset_ingestion_log (default: 1.0.0).",
    )

    return parser


def run_preflight_cli(service: GTFSIngestionService) -> int:
    """Execute dry-run preflight validation and display formatted report."""
    logger.info("Executing dry-run preflight validation...")
    summary = service.run_preflight()

    print("\n" + "=" * 70)
    print("ROUTEWISE GTFS INGESTION PREFLIGHT REPORT (DRY-RUN)")
    print("=" * 70)
    print(f"Data Directory:       {summary.data_dir}")
    print(f"Composite SHA-256:    {summary.composite_hash}")
    print(f"Quarantined Stops:    {summary.quarantined_stops_count}")
    print("-" * 70)
    print(f"{'Entity':<25} {'Expected':<12} {'Found':<12} {'Status'}")
    print("-" * 70)

    for entity, expected in EXPECTED_COUNTS.items():
        found = summary.counts.get(entity, 0)
        match_status = "MATCH [OK]" if found == expected else "MISMATCH [FAIL]"
        print(f"{entity:<25} {expected:<12} {found:<12} {match_status}")

    print("-" * 70)
    print(f"Foreign-Key Integrity: {'PASSED [OK]' if summary.fk_integrity_valid else 'FAILED [ERROR]'}")
    print(f"Primary-Key Uniqueness: {'PASSED [OK]' if summary.pk_uniqueness_valid else 'FAILED [ERROR]'}")
    print("=" * 70)

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
    print("To execute live database ingestion, run with: python scripts/ingest_gtfs.py --confirm")
    return 0


def run_verify_cli(service: GTFSIngestionService) -> int:
    """Execute live database verification and compare counts."""
    logger.info("Connecting to PostgreSQL to verify table row counts...")
    try:
        verification = service.verify_database()
    except Exception as exc:
        logger.error(f"Database verification failed: {exc}")
        return 1

    print("\n" + "=" * 70)
    print("ROUTEWISE POSTGRESQL DATABASE VERIFICATION REPORT")
    print("=" * 70)
    print(f"{'Entity':<25} {'Expected':<12} {'In Database':<14} {'Status'}")
    print("-" * 70)

    for entity, expected in EXPECTED_COUNTS.items():
        actual = verification.actual_counts.get(entity, 0)
        status = "MATCH [OK]" if actual == expected else "MISMATCH [FAIL]"
        print(f"{entity:<25} {expected:<12} {actual:<14} {status}")

    print("=" * 70)

    if verification.latest_ingestion_log:
        log = verification.latest_ingestion_log
        print("\nLatest Ingestion Audit Log Entry:")
        print(f"  Ingestion ID:       {log.get('ingestion_id')}")
        print(f"  Stage:              {log.get('ingestion_stage')}")
        print(f"  Status:             {log.get('status')}")
        print(f"  Total Ingested:     {log.get('total_records_ingested')}")
        print(f"  Quarantined:        {log.get('quarantined_records_count')}")
        print(f"  Started At (UTC):   {log.get('started_at')}")
        print(f"  Completed At (UTC): {log.get('completed_at')}")
        print(f"  Duration:           {log.get('duration_seconds')}s")
        if log.get("error_message"):
            print(f"  Error Message:      {log.get('error_message')}")
    else:
        print("\nNo entries found in dataset_ingestion_log.")

    if not verification.all_matched:
        print("\n[FAIL] Database counts do not match expected canonical specifications.")
        return 1

    print("\n[PASS] All database table counts match canonical specifications exactly.")
    return 0


def run_ingest_cli(service: GTFSIngestionService, batch_size: int, version: str) -> int:
    """Execute live database ingestion with explicit confirmation."""
    logger.info("Starting live GTFS structured dataset ingestion...")
    result = service.ingest_all(batch_size=batch_size, dataset_version=version)

    print("\n" + "=" * 70)
    print("ROUTEWISE GTFS INGESTION EXECUTION REPORT")
    print("=" * 70)
    print(f"Result Status:        {'SUCCESS [OK]' if result.success else 'FAILED [ERROR]'}")
    print(f"Ingestion Log ID:     {result.ingestion_log_id}")
    print(f"Dataset Version:      {result.dataset_version}")
    print(f"Composite Hash:       {result.composite_hash}")
    print(f"Duration:             {result.duration_seconds:.2f} seconds")
    print(f"Total Rows Committed: {result.total_records}")
    print("-" * 70)
    print(f"{'Entity':<25} {'Rows Ingested'}")
    print("-" * 70)

    for entity, count in result.counts_ingested.items():
        print(f"{entity:<25} {count}")

    print("=" * 70)

    if not result.success:
        print(f"\n[ERROR] Ingestion failed: {result.error_message}")
        return 1

    print("\n[SUCCESS] Structured GTFS dataset ingestion completed and verified.")
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    # Mask and log configuration without leaking credentials
    logger.info(f"Target Database: {mask_database_url(settings.DATABASE_URL)}")
    logger.info(f"Source Data Directory: {args.data_dir}")

    service = GTFSIngestionService(data_dir=args.data_dir)

    if args.verify:
        return run_verify_cli(service)
    elif args.execute:
        return run_ingest_cli(service, batch_size=args.batch_size, version=args.dataset_version)
    else:
        # Default safety behavior: run dry-run preflight
        return run_preflight_cli(service)


if __name__ == "__main__":
    sys.exit(main())

