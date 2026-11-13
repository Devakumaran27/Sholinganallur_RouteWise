"""
Script: scripts/generate_embeddings.py
Purpose: Production CLI entry point for the RouteWise batch embedding pipeline.
         Supports dry-run planning, status inspection, chunk limiting, and resume capabilities.
         Guarantees safety: default invocation requires explicit --confirm / --execute to run.
"""

import argparse
import logging
import sys
from pathlib import Path

# Add project root to path for module resolution
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import settings
from app.ai.rag.embeddings import BatchProcessor, CheckpointManager, EmbeddingService, QuotaManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("generate_embeddings_cli")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="RouteWise Production Batch Embedding Pipeline CLI",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Inspect catalog, plan batches, and estimate token demand without making Gemini API calls.",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Display current checkpoint ledger status, orphan vector counts, and quota statistics without API calls.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Restrict execution to N chunks for controlled smoke testing.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=settings.EMBEDDING_BATCH_SIZE,
        help="Number of chunks per API request batch (default: 20).",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Explicitly resume from the existing checkpoint ledger (default behavior).",
    )
    parser.add_argument(
        "--confirm",
        "--execute",
        dest="confirm",
        action="store_true",
        help="Safety flag required to execute real embedding generation against Google AI Studio.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    processor = BatchProcessor(
        batch_size=args.batch_size,
    )

    # 1. Status Command (Zero API Calls)
    if args.status:
        logger.info("Inspecting pipeline and checkpoint status...")
        plan = processor.plan_execution()
        print("\n" + "=" * 70)
        print("          ROUTEWISE BATCH EMBEDDING PIPELINE: STATUS REPORT")
        print("=" * 70)
        print(f"Total Catalog Chunks        : {plan['total_catalog_chunks']}")
        print(f"Already Completed (Skipped) : {plan['completed_and_skipped']}")
        print(f"Pending to Embed            : {plan['pending_to_process']}")
        print(f"Orphans Recovered           : {plan['orphans_recovered_preflight']}")
        print(f"Target Model                : {plan['target_model']} ({plan['output_dimensions']}-dim)")
        print(f"Daily API Budget Limit      : {plan['daily_budget_limit']} requests")
        print(f"Remaining Requests Today    : {plan['remaining_daily_requests']}")
        print(f"TPM Safety Ceiling          : {plan['tpm_safety_limit']} tokens/minute")
        print("=" * 70 + "\n")
        return 0

    # 2. Dry-Run Planning Command (Zero API Calls)
    if args.dry_run:
        logger.info("Executing dry-run planning analysis...")
        plan = processor.process_all(limit=args.limit, dry_run=True)
        print("\n" + "=" * 70)
        print("          ROUTEWISE BATCH EMBEDDING PIPELINE: DRY-RUN EXECUTION PLAN")
        print("=" * 70)
        print(f"Total Catalog Chunks        : {plan['total_catalog_chunks']}")
        print(f"Completed / On-Disk Vectors : {plan['completed_and_skipped']}")
        print(f"Pending Queue (after limit) : {plan['pending_to_process']}")
        print(f"Configured Batch Size       : {plan['batch_size']} chunks/request")
        print(f"Planned API Batches         : {plan['planned_batches']} requests")
        print(f"Nominal Estimated Tokens    : {plan['nominal_estimated_tokens']:,} tokens")
        print(f"Conservative Budget Tokens  : {plan['conservative_estimated_tokens']:,} tokens (with 1.25x safety)")
        print(f"Daily Budget Headroom       : {plan['remaining_daily_requests']}/{plan['daily_budget_limit']} requests")
        print(f"TPM Safety Threshold        : {plan['tpm_safety_limit']} tokens/minute")
        print("=" * 70)
        print("[DRY-RUN COMPLETE] Zero Gemini API requests were dispatched.\n")
        return 0

    # 3. Safety Confirmation Gate for Real Execution
    if not args.confirm:
        logger.warning(
            "[SAFETY GATE] Real embedding execution requested, but neither --confirm nor --execute was supplied."
        )
        print("\n" + "!" * 70)
        print(" SAFETY NOTICE: Real Gemini API embedding calls require confirmation.")
        print(" To prevent accidental quota consumption, run with --confirm or --execute:")
        print(f"   python scripts/generate_embeddings.py --confirm")
        print(" To inspect without making calls, use --dry-run or --status:")
        print(f"   python scripts/generate_embeddings.py --dry-run")
        print("!" * 70 + "\n")
        return 1

    # 4. Confirmed Execution
    logger.info("Execution confirmed by operator. Starting batch embedding runner...")
    result = processor.process_all(limit=args.limit, dry_run=False)
    logger.info("Batch execution finished with status: %s", result["status"])
    return 0


if __name__ == "__main__":
    sys.exit(main())

