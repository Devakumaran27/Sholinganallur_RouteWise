"""
Module: app.ai.rag.embeddings.checkpoint_manager
Purpose: High-reliability local checkpoint manager for the RouteWise batch embedding pipeline.
         Provides atomic state persistence, crash recovery, orphaned vector adoption,
         strict state transition enforcement, and duplicate processing prevention.
"""

from __future__ import annotations

import json
import logging
import math
import os
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from pydantic import ValidationError

from app.core.config import settings
from app.ai.rag.embeddings.models import (
    APIRequestTracker,
    BatchCheckpointLedger,
    ChunkProcessingStatus,
    EmbeddingCheckpointRecord,
    EmbeddingMetadata,
    TokenAccounting,
    VectorRecord,
    sanitize_sensitive_data,
)

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[4]


class CheckpointManager:
    """
    Manages the lifecycle, atomic persistence, and recovery of embedding checkpoints.
    Guarantees idempotency and crash resilience across process restarts.
    """

    ALLOWED_TRANSITIONS: Dict[ChunkProcessingStatus, Set[ChunkProcessingStatus]] = {
        ChunkProcessingStatus.PENDING: {
            ChunkProcessingStatus.COMPLETED,
            ChunkProcessingStatus.RETRYABLE_FAILED,
            ChunkProcessingStatus.PERMANENTLY_FAILED,
        },
        ChunkProcessingStatus.RETRYABLE_FAILED: {
            ChunkProcessingStatus.COMPLETED,
            ChunkProcessingStatus.RETRYABLE_FAILED,  # Retry attempt increment
            ChunkProcessingStatus.PERMANENTLY_FAILED,
        },
        ChunkProcessingStatus.PERMANENTLY_FAILED: {
            ChunkProcessingStatus.PENDING,  # Explicit manual/re-drive reset
        },
        ChunkProcessingStatus.COMPLETED: {
            ChunkProcessingStatus.PENDING,  # Explicit invalidation due to content change
        },
    }

    def __init__(
        self,
        checkpoint_dir: Optional[Path] = None,
        vectors_dir: Optional[Path] = None,
        manifest_path: Optional[Path] = None,
        ledger_filename: str = "batch_checkpoint.json",
        dataset_version: str = "v1.0.0-sholinganallur",
        pipeline_version: str = "v1.0.0-embedding",
        model_name: Optional[str] = None,
        embedding_dimensions: Optional[int] = None,
    ):
        self.checkpoint_dir = Path(checkpoint_dir) if checkpoint_dir else PROJECT_ROOT / "data" / "embeddings" / "checkpoints"
        self.vectors_dir = Path(vectors_dir) if vectors_dir else PROJECT_ROOT / "data" / "embeddings" / "vectors"
        self.manifest_path = Path(manifest_path) if manifest_path else PROJECT_ROOT / "data" / "chunks" / "chunks_manifest.json"
        self.ledger_filename = ledger_filename
        self.ledger_file = self.checkpoint_dir / self.ledger_filename

        self.dataset_version = dataset_version
        self.pipeline_version = pipeline_version
        self.model_name = model_name or settings.EMBEDDING_MODEL
        self.embedding_dimensions = embedding_dimensions or settings.EMBEDDING_DIMENSIONS
        self._lock = threading.RLock()

    def ensure_directories(self) -> None:
        """Ensures that checkpoint and vector storage directories exist."""
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        self.vectors_dir.mkdir(parents=True, exist_ok=True)

    # --------------------------------------------------------------------------
    # 1. Initialize Checkpoint
    # --------------------------------------------------------------------------
    def initialize_ledger(
        self, manifest_chunks: Optional[List[Dict[str, Any]]] = None
    ) -> BatchCheckpointLedger:
        """
        Initializes or reloads the state ledger using the expected chunk catalog.
        Preserves existing valid records and does not overwrite blindly.
        """
        self.ensure_directories()

        # Load chunks from manifest path if not explicitly provided
        chunks_list = manifest_chunks
        if chunks_list is None:
            if not self.manifest_path.exists():
                raise FileNotFoundError(f"Chunks manifest not found at: {self.manifest_path}")
            with open(self.manifest_path, "r", encoding="utf-8") as f:
                chunks_list = json.load(f)

        chunks_by_id = {c["chunk_id"]: c for c in chunks_list}

        if self.ledger_file.exists():
            logger.info("Existing checkpoint ledger found at %s. Loading and reconciling...", self.ledger_file)
            ledger = self.load_ledger()
            self._reconcile_existing_ledger(ledger, chunks_by_id)
        else:
            logger.info("No checkpoint ledger found. Initializing new ledger for %d chunks...", len(chunks_list))
            ledger = BatchCheckpointLedger(
                checkpoint_version="v1.1.0",
                pipeline_name="RouteWise_Batch_Embedding_Pipeline",
                pipeline_version=self.pipeline_version,
                dataset_version=self.dataset_version,
                model_name=self.model_name,
                embedding_dimensions=self.embedding_dimensions,
                task_type="RETRIEVAL_DOCUMENT",
                total_catalog_chunks=len(chunks_list),
                accounting=APIRequestTracker(daily_budget_limit=settings.MAX_DAILY_REQUEST_BUDGET),
                chunks={},
                last_run_started_utc=datetime.now(timezone.utc),
            )
            for c in chunks_list:
                ledger.chunks[c["chunk_id"]] = EmbeddingCheckpointRecord(
                    chunk_id=c["chunk_id"],
                    parent_document_id=c["document_id"],
                    chunk_hash=c["chunk_hash"],
                    dataset_version=c.get("dataset_version", self.dataset_version),
                    pipeline_version=self.pipeline_version,
                    model_name=self.model_name,
                    embedding_dimensions=self.embedding_dimensions,
                    task_type="RETRIEVAL_DOCUMENT",
                    processing_status=ChunkProcessingStatus.PENDING,
                    attempt_count=0,
                    estimated_tokens=c.get("token_count", 0),
                    created_at_utc=datetime.now(timezone.utc),
                )

        # Pre-flight orphan recovery for valid vectors present on disk
        recovered = self.recover_orphaned_vectors(ledger, chunks_by_id)
        if recovered > 0:
            logger.info("Pre-flight reconciliation recovered %d orphaned vector files.", recovered)

        self.save_ledger(ledger)
        return ledger

    def _reconcile_existing_ledger(
        self, ledger: BatchCheckpointLedger, manifest_chunks: Dict[str, Dict[str, Any]]
    ) -> None:
        """
        Reconciles loaded ledger records with the active manifest:
        - Invalids chunks whose content hash changed.
        - Adds missing catalog chunks as PENDING.
        - Updates total catalog count.
        """
        ledger.total_catalog_chunks = len(manifest_chunks)

        for chunk_id, manifest_chunk in manifest_chunks.items():
            current_hash = manifest_chunk["chunk_hash"]
            if chunk_id in ledger.chunks:
                record = ledger.chunks[chunk_id]
                # Check for content hash alteration
                if record.chunk_hash != current_hash:
                    logger.warning(
                        "Chunk %s content hash changed (%s -> %s). Invalidating checkpoint to PENDING.",
                        chunk_id,
                        record.chunk_hash,
                        current_hash,
                    )
                    record.chunk_hash = current_hash
                    record.processing_status = ChunkProcessingStatus.PENDING
                    record.attempt_count = 0
                    record.vector_file_path = None
                    record.vector_l2_norm = None
                    record.completed_at_utc = None
                    record.error_details = None
            else:
                # Newly added chunk in catalog
                ledger.chunks[chunk_id] = EmbeddingCheckpointRecord(
                    chunk_id=chunk_id,
                    parent_document_id=manifest_chunk["document_id"],
                    chunk_hash=current_hash,
                    dataset_version=manifest_chunk.get("dataset_version", self.dataset_version),
                    pipeline_version=self.pipeline_version,
                    model_name=self.model_name,
                    embedding_dimensions=self.embedding_dimensions,
                    task_type="RETRIEVAL_DOCUMENT",
                    processing_status=ChunkProcessingStatus.PENDING,
                    attempt_count=0,
                    estimated_tokens=manifest_chunk.get("token_count", 0),
                    created_at_utc=datetime.now(timezone.utc),
                )

    # --------------------------------------------------------------------------
    # 2. Atomic Persistence & Reload
    # --------------------------------------------------------------------------
    def save_ledger(self, ledger: BatchCheckpointLedger) -> Path:
        """
        Atomically saves the checkpoint ledger to disk.
        Writes to a staging .tmp file, flushes file descriptors with os.fsync,
        and atomically renames over the target path. Thread-safe.
        """
        with self._lock:
            self.ensure_directories()
            ledger.sync_status_counts()
            ledger.last_checkpoint_commit_utc = datetime.now(timezone.utc)

            tmp_file = self.ledger_file.with_suffix(".json.tmp")
            serialized = ledger.model_dump_json(indent=2)

            try:
                with open(tmp_file, "w", encoding="utf-8") as f:
                    f.write(serialized)
                    f.flush()
                    os.fsync(f.fileno())

                os.replace(tmp_file, self.ledger_file)
                return self.ledger_file
            except Exception as exc:
                logger.error("Failed to atomically save checkpoint ledger: %s", exc)
                if tmp_file.exists():
                    tmp_file.unlink(missing_ok=True)
                raise

    def load_ledger(self) -> BatchCheckpointLedger:
        """Loads and validates the checkpoint ledger from disk."""
        if not self.ledger_file.exists():
            raise FileNotFoundError(f"Checkpoint ledger does not exist at: {self.ledger_file}")

        try:
            with open(self.ledger_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return BatchCheckpointLedger.model_validate(data)
        except Exception as exc:
            logger.error("Corrupted checkpoint ledger at %s: %s", self.ledger_file, exc)
            raise ValueError(f"Failed to parse checkpoint ledger: {exc}") from exc

    # --------------------------------------------------------------------------
    # 3. Resume Validation (Duplicate Processing Prevention)
    # --------------------------------------------------------------------------
    def should_skip_chunk(
        self, chunk_id: str, current_hash: str, ledger: BatchCheckpointLedger
    ) -> Tuple[bool, str]:
        """
        Evaluates whether a chunk can be safely skipped on resume.
        A completed chunk is skipped only if all versions, dimensions,
        hashes, and on-disk vector integrity checks pass.
        """
        record = ledger.chunks.get(chunk_id)
        if not record:
            return False, "Not present in checkpoint ledger"

        if record.processing_status != ChunkProcessingStatus.COMPLETED:
            return False, f"Status is {record.processing_status.value}"

        if record.chunk_hash != current_hash:
            return False, f"Chunk hash mismatch (checkpoint: {record.chunk_hash[:8]}, current: {current_hash[:8]})"

        if record.dataset_version != self.dataset_version:
            return False, f"Dataset version mismatch ({record.dataset_version} != {self.dataset_version})"

        if record.pipeline_version != self.pipeline_version:
            return False, f"Pipeline version mismatch ({record.pipeline_version} != {self.pipeline_version})"

        if record.model_name != self.model_name:
            return False, f"Model mismatch ({record.model_name} != {self.model_name})"

        if record.embedding_dimensions != self.embedding_dimensions:
            return False, f"Dimensions mismatch ({record.embedding_dimensions} != {self.embedding_dimensions})"

        vector_path = self.vectors_dir / f"{chunk_id}.json"
        if not vector_path.exists():
            return False, f"Vector file missing on disk: {vector_path.name}"

        # Thorough validation of the on-disk vector file
        is_valid, _, error_msg = self.validate_vector_file(vector_path, chunk_id, current_hash)
        if not is_valid:
            return False, f"On-disk vector file invalid: {error_msg}"

        return True, "Valid completed vector already exists on disk"

    # --------------------------------------------------------------------------
    # 4. Vector File Validation & Orphan Recovery
    # --------------------------------------------------------------------------
    def validate_vector_file(
        self, vector_path: Path, expected_chunk_id: str, expected_hash: str
    ) -> Tuple[bool, Optional[VectorRecord], str]:
        """
        Validates an on-disk vector JSON file:
        - Parseable JSON matching VectorRecord schema.
        - Chunk ID and SHA-256 hash match expected values.
        - Exactly 768 dimensions with all finite float values.
        - Positive L2 norm.
        """
        if not vector_path.exists():
            return False, None, "File does not exist"

        try:
            with open(vector_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            vector_rec = VectorRecord.model_validate(data)

            if vector_rec.chunk_id != expected_chunk_id:
                return False, None, f"Chunk ID mismatch ({vector_rec.chunk_id} != {expected_chunk_id})"

            if vector_rec.chunk_hash != expected_hash:
                return False, None, f"Hash mismatch ({vector_rec.chunk_hash} != {expected_hash})"

            if vector_rec.embedding_metadata.output_dimensionality != self.embedding_dimensions:
                return False, None, f"Dimension mismatch ({vector_rec.embedding_metadata.output_dimensionality} != {self.embedding_dimensions})"

            if len(vector_rec.embedding) != self.embedding_dimensions:
                return False, None, f"Embedding length mismatch ({len(vector_rec.embedding)} != {self.embedding_dimensions})"

            if vector_rec.embedding_metadata.vector_l2_norm <= 0:
                return False, None, "Non-positive L2 norm"

            return True, vector_rec, "Valid"

        except Exception as exc:
            return False, None, f"Validation error: {exc}"

    def recover_orphaned_vectors(
        self, ledger: BatchCheckpointLedger, manifest_chunks: Dict[str, Dict[str, Any]]
    ) -> int:
        """
        Scans vectors_dir for valid vector files whose checkpoint status is missing or incomplete.
        Adopts valid vector files directly into the checkpoint without remote API calls.
        Invalid or corrupted files are marked for reprocessing.
        """
        if not self.vectors_dir.exists():
            return 0

        recovered_count = 0

        for vector_file in sorted(self.vectors_dir.glob("*.json")):
            chunk_id = vector_file.stem
            if chunk_id not in manifest_chunks:
                logger.warning("Extraneous vector file found on disk: %s. Ignoring.", vector_file.name)
                continue

            manifest_chunk = manifest_chunks[chunk_id]
            expected_hash = manifest_chunk["chunk_hash"]
            record = ledger.chunks.get(chunk_id)

            # Skip if already marked completed and on-disk file is valid
            if record and record.processing_status == ChunkProcessingStatus.COMPLETED:
                continue

            # Validate the on-disk candidate
            is_valid, vector_rec, err_msg = self.validate_vector_file(
                vector_file, chunk_id, expected_hash
            )

            if is_valid and vector_rec is not None:
                logger.info("Adopting orphaned vector file for %s without API request.", chunk_id)
                rel_path = str(vector_file.relative_to(PROJECT_ROOT)) if vector_file.is_relative_to(PROJECT_ROOT) else str(vector_file)
                
                attempt = record.attempt_count if record else 1
                ledger.chunks[chunk_id] = EmbeddingCheckpointRecord(
                    chunk_id=chunk_id,
                    parent_document_id=manifest_chunk["document_id"],
                    chunk_hash=expected_hash,
                    dataset_version=manifest_chunk.get("dataset_version", self.dataset_version),
                    pipeline_version=self.pipeline_version,
                    model_name=self.model_name,
                    embedding_dimensions=self.embedding_dimensions,
                    task_type="RETRIEVAL_DOCUMENT",
                    processing_status=ChunkProcessingStatus.COMPLETED,
                    attempt_count=attempt,
                    vector_file_path=rel_path,
                    vector_l2_norm=vector_rec.embedding_metadata.vector_l2_norm,
                    estimated_tokens=manifest_chunk.get("token_count", 0),
                    api_reported_tokens=vector_rec.token_accounting.api_reported_tokens,
                    error_details=None,
                    created_at_utc=record.created_at_utc if record else datetime.now(timezone.utc),
                    completed_at_utc=vector_rec.embedding_metadata.generated_at_utc,
                )
                recovered_count += 1
            else:
                logger.warning(
                    "Orphaned vector file %s failed validation (%s). Marking for reprocessing.",
                    vector_file.name,
                    err_msg,
                )
                # Discard corrupted file to avoid confusion
                vector_file.unlink(missing_ok=True)
                if record:
                    record.processing_status = ChunkProcessingStatus.PENDING

        return recovered_count

    # --------------------------------------------------------------------------
    # 5. Status Management & State Transitions
    # --------------------------------------------------------------------------
    def update_chunk_status(
        self,
        ledger: BatchCheckpointLedger,
        chunk_id: str,
        new_status: ChunkProcessingStatus,
        vector_file_path: Optional[str] = None,
        vector_l2_norm: Optional[float] = None,
        api_reported_tokens: Optional[int] = None,
        error_details: Optional[Dict[str, Any]] = None,
        completed_at_utc: Optional[datetime] = None,
    ) -> EmbeddingCheckpointRecord:
        """
        Updates the operational status of a chunk record, strictly validating state transitions
        and ensuring crash safety before committing COMPLETED status.
        """
        if chunk_id not in ledger.chunks:
            raise KeyError(f"Chunk '{chunk_id}' not found in checkpoint ledger.")

        record = ledger.chunks[chunk_id]
        current_status = record.processing_status

        # Validate allowable state transition
        allowed = self.ALLOWED_TRANSITIONS.get(current_status, set())
        if new_status not in allowed:
            raise ValueError(
                f"Invalid status transition from {current_status.value} to {new_status.value} for chunk '{chunk_id}'."
            )

        if new_status == ChunkProcessingStatus.COMPLETED:
            # Crash Safety Rule: Must verify on-disk vector file exists and passes validation
            target_path = Path(vector_file_path) if vector_file_path else self.vectors_dir / f"{chunk_id}.json"
            if not target_path.is_absolute():
                target_path = PROJECT_ROOT / target_path

            is_valid, vec_rec, err = self.validate_vector_file(target_path, chunk_id, record.chunk_hash)
            if not is_valid or vec_rec is None:
                raise ValueError(
                    f"Cannot mark chunk '{chunk_id}' as COMPLETED: on-disk vector validation failed ({err})."
                )

            record.processing_status = ChunkProcessingStatus.COMPLETED
            record.vector_file_path = str(target_path.relative_to(PROJECT_ROOT)) if target_path.is_relative_to(PROJECT_ROOT) else str(target_path)
            record.vector_l2_norm = vector_l2_norm or vec_rec.embedding_metadata.vector_l2_norm
            record.api_reported_tokens = api_reported_tokens or vec_rec.token_accounting.api_reported_tokens
            record.completed_at_utc = completed_at_utc or vec_rec.embedding_metadata.generated_at_utc
            record.error_details = None

        elif new_status == ChunkProcessingStatus.RETRYABLE_FAILED:
            record.processing_status = ChunkProcessingStatus.RETRYABLE_FAILED
            record.attempt_count += 1
            record.error_details = sanitize_sensitive_data(error_details or {"message": "Transient failure"})

        elif new_status == ChunkProcessingStatus.PERMANENTLY_FAILED:
            if not error_details:
                raise ValueError(f"error_details required when marking chunk '{chunk_id}' as PERMANENTLY_FAILED.")
            record.processing_status = ChunkProcessingStatus.PERMANENTLY_FAILED
            record.attempt_count += 1
            record.error_details = sanitize_sensitive_data(error_details)

        elif new_status == ChunkProcessingStatus.PENDING:
            record.processing_status = ChunkProcessingStatus.PENDING
            record.vector_file_path = None
            record.vector_l2_norm = None
            record.completed_at_utc = None
            record.error_details = None

        return record

    # --------------------------------------------------------------------------
    # 6. Query Helpers
    # --------------------------------------------------------------------------
    def get_pending_or_retryable_chunks(
        self, ledger: BatchCheckpointLedger
    ) -> List[EmbeddingCheckpointRecord]:
        """Returns chunks requiring embedding (PENDING or RETRYABLE_FAILED)."""
        return [
            r
            for r in ledger.chunks.values()
            if r.processing_status in (ChunkProcessingStatus.PENDING, ChunkProcessingStatus.RETRYABLE_FAILED)
        ]

    def get_completed_chunks(
        self, ledger: BatchCheckpointLedger
    ) -> List[EmbeddingCheckpointRecord]:
        """Returns all completed chunks."""
        return [r for r in ledger.chunks.values() if r.processing_status == ChunkProcessingStatus.COMPLETED]

    def get_quarantined_chunks(
        self, ledger: BatchCheckpointLedger
    ) -> List[EmbeddingCheckpointRecord]:
        """Returns all permanently failed chunks."""
        return [r for r in ledger.chunks.values() if r.processing_status == ChunkProcessingStatus.PERMANENTLY_FAILED]

