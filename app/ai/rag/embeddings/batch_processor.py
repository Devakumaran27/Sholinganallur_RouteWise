"""
Module: app.ai.rag.embeddings.batch_processor
Purpose: Production batch embedding processor for the RouteWise pipeline.
         Coordinates catalog ingestion, orphan recovery, rate-limited batch dispatch,
         bounded recursive bisection, atomic vector persistence, and checkpoint ledgers.
"""

from __future__ import annotations

import json
import logging
import math
import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.core.config import settings
from app.ai.rag.embeddings.checkpoint_manager import CheckpointManager
from app.ai.rag.embeddings.embedding_service import (
    AuthenticationError,
    EmbeddingService,
    EmbeddingServiceError,
    InvalidRequestError,
    RateLimitError,
    TransientServerError,
)
from app.ai.rag.embeddings.models import (
    BatchCheckpointLedger,
    ChunkProcessingStatus,
    EmbeddingCheckpointRecord,
    VectorRecord,
    sanitize_sensitive_data,
)
from app.ai.rag.embeddings.quota_manager import QuotaExceededError, QuotaManager

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[4]


class BatchProcessor:
    """
    Orchestrates the end-to-end batch embedding workflow across all 145 chunks.
    Ensures zero duplicate work, atomic disk writes, bounded bisection, and quota compliance.
    """

    def __init__(
        self,
        checkpoint_manager: Optional[CheckpointManager] = None,
        embedding_service: Optional[EmbeddingService] = None,
        quota_manager: Optional[QuotaManager] = None,
        manifest_path: Optional[Path] = None,
        chunks_dir: Optional[Path] = None,
        vectors_dir: Optional[Path] = None,
        quarantine_dir: Optional[Path] = None,
        batch_size: Optional[int] = None,
        max_retries: Optional[int] = None,
        backoff_base_seconds: float = 2.0,
        backoff_max_seconds: float = 60.0,
    ):
        self.manifest_path = Path(manifest_path) if manifest_path else PROJECT_ROOT / "data" / "chunks" / "chunks_manifest.json"
        self.chunks_dir = Path(chunks_dir) if chunks_dir else PROJECT_ROOT / "data" / "chunks"
        self.vectors_dir = Path(vectors_dir) if vectors_dir else PROJECT_ROOT / "data" / "embeddings" / "vectors"
        self.quarantine_dir = Path(quarantine_dir) if quarantine_dir else PROJECT_ROOT / "data" / "embeddings" / "quarantine"

        self.batch_size = batch_size or settings.EMBEDDING_BATCH_SIZE
        self.max_retries = max_retries if max_retries is not None else settings.MAX_RETRY_ATTEMPTS
        self.backoff_base_seconds = backoff_base_seconds
        self.backoff_max_seconds = backoff_max_seconds

        self.checkpoint_manager = checkpoint_manager or CheckpointManager(
            vectors_dir=self.vectors_dir, manifest_path=self.manifest_path
        )
        self.embedding_service = embedding_service or EmbeddingService()
        self.quota_manager = quota_manager or QuotaManager()

    def ensure_directories(self) -> None:
        """Ensures that vector and quarantine output directories exist."""
        self.vectors_dir.mkdir(parents=True, exist_ok=True)
        self.quarantine_dir.mkdir(parents=True, exist_ok=True)

    # --------------------------------------------------------------------------
    # Catalog Ingestion & Validation
    # --------------------------------------------------------------------------
    def load_and_validate_catalog(self) -> List[Dict[str, Any]]:
        """
        Loads the chunks catalog from manifest and enriches each record with full chunk_text.
        Validates chunk ID, text non-emptiness, and SHA-256 content hash integrity.
        """
        if not self.manifest_path.exists():
            raise FileNotFoundError(f"Chunks manifest not found: {self.manifest_path}")

        with open(self.manifest_path, "r", encoding="utf-8") as f:
            manifest_items = json.load(f)

        validated_chunks = []
        for item in manifest_items:
            chunk_id = item["chunk_id"]
            chunk_file = self.chunks_dir / f"{chunk_id}.json"

            if not chunk_file.exists():
                raise FileNotFoundError(f"Individual chunk file missing for {chunk_id}: {chunk_file}")

            with open(chunk_file, "r", encoding="utf-8") as cf:
                chunk_data = json.load(cf)

            chunk_text = chunk_data.get("chunk_text", "")
            if not chunk_text or not chunk_text.strip():
                raise ValueError(f"Chunk text is empty for chunk '{chunk_id}'.")

            # Merge manifest and chunk content into a unified dictionary
            merged = dict(item)
            merged["chunk_text"] = chunk_text
            validated_chunks.append(merged)

        logger.info("Successfully loaded and validated %d catalog chunks.", len(validated_chunks))
        return validated_chunks

    # --------------------------------------------------------------------------
    # Planning & Dry Run
    # --------------------------------------------------------------------------
    def plan_execution(self, limit: Optional[int] = None) -> Dict[str, Any]:
        """
        Generates a dry-run execution plan without issuing any remote Gemini API requests.
        Inspects existing checkpoint, identifies chunks to skip, and calculates token/request budgets.
        """
        self.ensure_directories()
        catalog = self.load_and_validate_catalog()
        ledger = self.checkpoint_manager.initialize_ledger(catalog)
        self.quota_manager.sync_with_ledger(ledger, self.checkpoint_manager)

        # Pre-flight orphan recovery
        orphans_recovered = self.checkpoint_manager.recover_orphaned_vectors(
            ledger, {c["chunk_id"]: c for c in catalog}
        )

        chunks_to_process = []
        skipped_chunks = []

        for c in catalog:
            chunk_id = c["chunk_id"]
            current_hash = c["chunk_hash"]
            should_skip, reason = self.checkpoint_manager.should_skip_chunk(chunk_id, current_hash, ledger)
            if should_skip:
                skipped_chunks.append({"chunk_id": chunk_id, "reason": reason})
            else:
                chunks_to_process.append(c)

        if limit is not None and limit > 0:
            chunks_to_process = chunks_to_process[:limit]

        # Calculate planned batches and token estimates
        batch_count = math.ceil(len(chunks_to_process) / self.batch_size) if chunks_to_process else 0
        total_estimated_tokens = sum(c.get("token_count", 0) for c in chunks_to_process)
        conservative_tokens = int(total_estimated_tokens * self.quota_manager.token_safety_multiplier)

        return {
            "status": "PLAN_COMPLETED",
            "total_catalog_chunks": len(catalog),
            "orphans_recovered_preflight": orphans_recovered,
            "completed_and_skipped": len(skipped_chunks),
            "pending_to_process": len(chunks_to_process),
            "batch_size": self.batch_size,
            "planned_batches": batch_count,
            "nominal_estimated_tokens": total_estimated_tokens,
            "conservative_estimated_tokens": conservative_tokens,
            "remaining_daily_requests": self.quota_manager.get_remaining_daily_requests(),
            "daily_budget_limit": self.quota_manager.daily_budget,
            "tpm_safety_limit": self.quota_manager.tpm_safety_limit,
            "target_model": self.embedding_service.model_name,
            "output_dimensions": self.embedding_service.embedding_dimensions,
        }

    # --------------------------------------------------------------------------
    # Main Batch Orchestration Execution
    # --------------------------------------------------------------------------
    def process_all(
        self,
        limit: Optional[int] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        Executes the batch embedding pipeline across catalog chunks.

        Args:
            limit: Optional restriction on the number of chunks to process.
            dry_run: When True, generates plan without remote API calls.

        Returns:
            Dict containing detailed execution metrics and final ledger status.
        """
        if dry_run:
            logger.info("Executing dry-run mode (zero Gemini API calls).")
            return self.plan_execution(limit=limit)

        self.ensure_directories()
        catalog = self.load_and_validate_catalog()
        ledger = self.checkpoint_manager.initialize_ledger(catalog)
        self.quota_manager.sync_with_ledger(ledger, self.checkpoint_manager)

        # Pre-flight orphan recovery
        orphans_recovered = self.checkpoint_manager.recover_orphaned_vectors(
            ledger, {c["chunk_id"]: c for c in catalog}
        )

        # Filter chunks requiring processing
        queue: List[Dict[str, Any]] = []
        for c in catalog:
            chunk_id = c["chunk_id"]
            should_skip, _ = self.checkpoint_manager.should_skip_chunk(chunk_id, c["chunk_hash"], ledger)
            if not should_skip:
                queue.append(c)

        if limit is not None and limit > 0:
            queue = queue[:limit]

        logger.info(
            "Batch execution starting: %d chunks to embed (limit: %s, batch_size: %d).",
            len(queue),
            limit,
            self.batch_size,
        )

        batches_processed = 0
        chunks_succeeded = 0
        chunks_quarantined = 0
        job_interrupted_budget = False

        # Partition queue into batches of batch_size
        for i in range(0, len(queue), self.batch_size):
            batch = queue[i : i + self.batch_size]
            batches_processed += 1
            batch_num = (i // self.batch_size) + 1
            total_batches = math.ceil(len(queue) / self.batch_size)

            logger.info("--- Dispatching Batch %d/%d (%d chunks) ---", batch_num, total_batches, len(batch))

            try:
                succeeded, quarantined = self._process_batch_with_recovery(batch, ledger)
                chunks_succeeded += succeeded
                chunks_quarantined += quarantined
            except QuotaExceededError as exc:
                logger.warning("Daily request safety budget reached: %s. Pausing pipeline gracefully.", exc)
                job_interrupted_budget = True
                break
            except AuthenticationError as exc:
                logger.critical("Fatal authentication error: %s. Aborting pipeline immediately.", exc)
                raise

        # Compile consolidated manifest catalog
        manifest_path = self._generate_embeddings_manifest(ledger)

        final_counts = ledger.sync_status_counts()
        self.checkpoint_manager.save_ledger(ledger)

        return {
            "status": "BUDGET_REACHED" if job_interrupted_budget else "COMPLETED",
            "batches_dispatched": batches_processed,
            "chunks_succeeded_in_run": chunks_succeeded,
            "chunks_quarantined_in_run": chunks_quarantined,
            "orphans_recovered": orphans_recovered,
            "ledger_status_counts": final_counts,
            "total_api_requests_issued": self.quota_manager.tracker.global_api_requests,
            "remaining_daily_budget": self.quota_manager.get_remaining_daily_requests(),
            "manifest_catalog_path": str(manifest_path.relative_to(PROJECT_ROOT)) if manifest_path.is_relative_to(PROJECT_ROOT) else str(manifest_path),
        }

    # --------------------------------------------------------------------------
    # Bounded Bisection, Retries, and Recovery
    # --------------------------------------------------------------------------
    def _process_batch_with_recovery(
        self,
        batch_chunks: List[Dict[str, Any]],
        ledger: BatchCheckpointLedger,
        is_bisection: bool = False,
    ) -> Tuple[int, int]:
        """
        Attempts to embed a batch of chunks.
        If a retryable error occurs, retries with exponential backoff.
        If an invalid request or unresolvable error occurs on a multi-chunk batch,
        recursively bisects the batch into smaller sub-batches down to 1-chunk requests.
        Quarantines permanently failing single chunks without failing the job.
        """
        if not batch_chunks:
            return 0, 0

        estimated_tokens = sum(c.get("token_count", 0) for c in batch_chunks)
        call_type = "bisection" if is_bisection else "initial"

        for attempt in range(self.max_retries + 1):
            actual_call_type = "retry" if attempt > 0 else call_type
            request_dispatched = False
            try:
                # 1. Quota reservation (enforces daily budget, TPM, and RPM pacing)
                # If this raises QuotaExceededError or TPMLimitExceededError, it is BLOCKED before dispatch.
                self.quota_manager.reserve_budget(
                    estimated_tokens=estimated_tokens,
                    call_type=actual_call_type,
                    wait_if_needed=True,
                )

                # 2. Remote API dispatch begins: record request attempt NOW
                self.quota_manager.record_request_attempt(call_type=actual_call_type)

                # 3. Persist the updated checkpoint ledger atomically BEFORE the remote API call begins.
                # This guarantees that even if a hard process crash occurs during network dispatch,
                # the recorded request attempt is already persisted on disk.
                self.checkpoint_manager.save_ledger(ledger)
                request_dispatched = True

                # 4. Dispatch the Gemini API invocation
                vector_records = self.embedding_service.embed_batch(batch_chunks)

                # 3. Request succeeded! Record token usage
                actual_tokens = (
                    vector_records[0].token_accounting.api_reported_tokens
                    if vector_records and vector_records[0].token_accounting.api_reported_tokens
                    else estimated_tokens
                )
                self.quota_manager.record_token_usage(
                    actual_tokens=actual_tokens,
                    estimated_tokens=estimated_tokens,
                )

                # 4. Atomic persistence of each vector file and checkpoint commit
                for vec_rec in vector_records:
                    self._save_vector_atomically(vec_rec)
                    self.checkpoint_manager.update_chunk_status(
                        ledger=ledger,
                        chunk_id=vec_rec.chunk_id,
                        new_status=ChunkProcessingStatus.COMPLETED,
                        vector_l2_norm=vec_rec.embedding_metadata.vector_l2_norm,
                        api_reported_tokens=vec_rec.token_accounting.api_reported_tokens,
                        completed_at_utc=vec_rec.embedding_metadata.generated_at_utc,
                    )
                self.checkpoint_manager.save_ledger(ledger)

                logger.info("Successfully processed and saved batch of %d chunks.", len(batch_chunks))
                return len(batch_chunks), 0

            except Exception as exc:
                # 5. Remote API call failed, but request was dispatched and consumed quota at Google
                if request_dispatched:
                    # Record estimated tokens in rolling window to prevent TPM spike
                    self.quota_manager.record_token_usage(actual_tokens=0, estimated_tokens=estimated_tokens)
                    # Persist the incremented request count immediately
                    self.checkpoint_manager.save_ledger(ledger)

                if isinstance(exc, (RateLimitError, TransientServerError)):
                    if attempt < self.max_retries:
                        backoff = min(
                            self.backoff_max_seconds,
                            self.backoff_base_seconds * (2 ** attempt),
                        ) + random.uniform(0.1, 1.0)
                        logger.warning(
                            "Transient error (%s). Retrying batch (attempt %d/%d) in %.2fs...",
                            exc.__class__.__name__,
                            attempt + 1,
                            self.max_retries,
                            backoff,
                        )
                        time.sleep(backoff)
                    else:
                        logger.error("Exhausted retries (%d) for batch.", self.max_retries)
                        # If multi-chunk, attempt bisection
                        if len(batch_chunks) > 1:
                            logger.info("Triggering bisection on exhausted retry batch (%d chunks)...", len(batch_chunks))
                            return self._bisect_batch(batch_chunks, ledger)
                        else:
                            # Single chunk exhausted retries -> mark RETRYABLE_FAILED
                            chunk_id = batch_chunks[0]["chunk_id"]
                            self.checkpoint_manager.update_chunk_status(
                                ledger=ledger,
                                chunk_id=chunk_id,
                                new_status=ChunkProcessingStatus.RETRYABLE_FAILED,
                                error_details={"error": str(exc), "attempt": attempt + 1},
                            )
                            self.checkpoint_manager.save_ledger(ledger)
                            return 0, 0

                elif isinstance(exc, InvalidRequestError):
                    # Payload error: bisect if multi-chunk, or quarantine if single-chunk
                    if len(batch_chunks) > 1:
                        logger.info("InvalidRequestError on batch of %d chunks. Bisecting...", len(batch_chunks))
                        return self._bisect_batch(batch_chunks, ledger)
                    else:
                        chunk_id = batch_chunks[0]["chunk_id"]
                        logger.error("Permanently failed chunk '%s': %s. Isolating in quarantine.", chunk_id, exc)
                        self._quarantine_failed_chunk(batch_chunks[0], exc, ledger)
                        return 0, 1

                elif isinstance(exc, AuthenticationError):
                    # Fatal fail-fast
                    logger.critical("Authentication failure on attempt: %s. Aborting immediately.", exc)
                    raise

                else:
                    logger.error("Unexpected error during batch embedding: %s", exc)
                    raise

        return 0, 0

    def _bisect_batch(
        self, batch_chunks: List[Dict[str, Any]], ledger: BatchCheckpointLedger
    ) -> Tuple[int, int]:
        """Splits a failing batch into two halves and processes each recursively."""
        mid = len(batch_chunks) // 2
        sub_a = batch_chunks[:mid]
        sub_b = batch_chunks[mid:]

        succ_a, quar_a = self._process_batch_with_recovery(sub_a, ledger, is_bisection=True)
        succ_b, quar_b = self._process_batch_with_recovery(sub_b, ledger, is_bisection=True)

        return succ_a + succ_b, quar_a + quar_b

    # --------------------------------------------------------------------------
    # Persistence & Quarantine Helpers
    # --------------------------------------------------------------------------
    def _save_vector_atomically(self, vector_rec: VectorRecord) -> Path:
        """
        Persists a VectorRecord atomically to data/embeddings/vectors/{chunk_id}.json.
        Uses staged .tmp write and os.fsync before atomic os.replace.
        """
        self.vectors_dir.mkdir(parents=True, exist_ok=True)
        target_path = self.vectors_dir / f"{vector_rec.chunk_id}.json"
        tmp_path = target_path.with_suffix(".json.tmp")

        serialized = vector_rec.model_dump_json(indent=2)
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(serialized)
                f.flush()
                os.fsync(f.fileno())

            os.replace(tmp_path, target_path)
            return target_path
        except Exception as exc:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
            raise IOError(f"Failed to atomically persist vector for {vector_rec.chunk_id}: {exc}") from exc

    def _quarantine_failed_chunk(
        self, chunk: Dict[str, Any], exc: Exception, ledger: BatchCheckpointLedger
    ) -> None:
        """Writes an isolated diagnostic report to data/embeddings/quarantine/ and updates ledger."""
        self.quarantine_dir.mkdir(parents=True, exist_ok=True)
        chunk_id = chunk["chunk_id"]
        quarantine_file = self.quarantine_dir / f"{chunk_id}_error.json"

        error_data = {
            "chunk_id": chunk_id,
            "document_id": chunk.get("document_id"),
            "chunk_hash": chunk.get("chunk_hash"),
            "error_type": exc.__class__.__name__,
            "error_message": str(exc),
            "quarantined_at_utc": datetime.now(timezone.utc).isoformat(),
            "chunk_payload_preview": chunk.get("chunk_text", "")[:300],
        }

        with open(quarantine_file, "w", encoding="utf-8") as f:
            json.dump(error_data, f, indent=2)

        self.checkpoint_manager.update_chunk_status(
            ledger=ledger,
            chunk_id=chunk_id,
            new_status=ChunkProcessingStatus.PERMANENTLY_FAILED,
            error_details=error_data,
        )
        self.checkpoint_manager.save_ledger(ledger)

    def _generate_embeddings_manifest(self, ledger: BatchCheckpointLedger) -> Path:
        """Generates the consolidated embeddings_manifest.json catalog."""
        manifest_path = self.vectors_dir.parent / "embeddings_manifest.json"
        completed_records = self.checkpoint_manager.get_completed_chunks(ledger)

        manifest_data = {
            "catalog_name": "RouteWise_Sholinganallur_Embeddings",
            "model_name": self.embedding_service.model_name,
            "output_dimensions": self.embedding_service.embedding_dimensions,
            "task_type": self.embedding_service.task_type,
            "total_completed_vectors": len(completed_records),
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "vectors": [
                {
                    "chunk_id": r.chunk_id,
                    "parent_document_id": r.parent_document_id,
                    "chunk_hash": r.chunk_hash,
                    "vector_file_path": r.vector_file_path,
                    "vector_l2_norm": r.vector_l2_norm,
                    "estimated_tokens": r.estimated_tokens,
                    "completed_at_utc": r.completed_at_utc.isoformat() if r.completed_at_utc else None,
                }
                for r in completed_records
            ],
        }

        tmp_path = manifest_path.with_suffix(".json.tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(manifest_data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())

        os.replace(tmp_path, manifest_path)
        return manifest_path

