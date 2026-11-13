"""
Unit tests for Phase 3 — Step 6D.2: Embedding Checkpoint Manager.
Tests atomic checkpoint persistence, crash recovery, orphaned vector adoption,
state transition validation, and duplicate prevention using temporary directories.
"""

import json
import math
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from app.ai.rag.embeddings.checkpoint_manager import CheckpointManager
from app.ai.rag.embeddings.models import (
    BatchCheckpointLedger,
    ChunkProcessingStatus,
    EmbeddingCheckpointRecord,
    EmbeddingMetadata,
    TokenAccounting,
    VectorRecord,
)


class TestCheckpointManager(unittest.TestCase):
    """Tests for the local Embedding Checkpoint Manager."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.temp_dir.name)
        self.checkpoint_dir = self.root_path / "checkpoints"
        self.vectors_dir = self.root_path / "vectors"
        self.manifest_path = self.root_path / "chunks_manifest.json"

        self.sample_hash = "2b6b0266fe2fbeaa8451a44e5d8a0c20ab2560d2e85a6ba10faad695b23d9061"
        self.sample_hash_2 = "916521fb063944af6f7abd8fe4b45c00e9d7f950fce08f816930bb691aacc5ee"

        self.synthetic_manifest = [
            {
                "chunk_id": "chunk_001",
                "document_id": "doc_001",
                "chunk_hash": self.sample_hash,
                "dataset_version": "v1.0.0-sholinganallur",
                "token_count": 350,
            },
            {
                "chunk_id": "chunk_002",
                "document_id": "doc_001",
                "chunk_hash": self.sample_hash_2,
                "dataset_version": "v1.0.0-sholinganallur",
                "token_count": 420,
            },
        ]

        # Write synthetic manifest to disk
        with open(self.manifest_path, "w", encoding="utf-8") as f:
            json.dump(self.synthetic_manifest, f)

        self.manager = CheckpointManager(
            checkpoint_dir=self.checkpoint_dir,
            vectors_dir=self.vectors_dir,
            manifest_path=self.manifest_path,
            dataset_version="v1.0.0-sholinganallur",
            pipeline_version="v1.0.0-embedding",
            model_name="gemini-embedding-001",
            embedding_dimensions=768,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def _create_valid_vector_file(self, chunk_id: str, chunk_hash: str) -> Path:
        """Helper to create a validated 768-dim vector JSON on disk."""
        self.vectors_dir.mkdir(parents=True, exist_ok=True)
        vector_path = self.vectors_dir / f"{chunk_id}.json"
        record = VectorRecord(
            chunk_id=chunk_id,
            document_id="doc_001",
            document_type="stop_profile",
            title=f"Profile for {chunk_id}",
            chunk_hash=chunk_hash,
            dataset_version="v1.0.0-sholinganallur",
            pipeline_version="v1.0.0-embedding",
            embedding_metadata=EmbeddingMetadata(
                model="gemini-embedding-001",
                output_dimensionality=768,
                task_type="RETRIEVAL_DOCUMENT",
                vector_l2_norm=0.5716,
                is_finite=True,
                generation_latency_seconds=1.1,
                generated_at_utc=datetime.now(timezone.utc),
            ),
            token_accounting=TokenAccounting(
                estimated_tokens=350,
                api_reported_tokens=355,
                source_character_length=1100,
            ),
            embedding=[0.02] * 768,
        )
        with open(vector_path, "w", encoding="utf-8") as f:
            f.write(record.model_dump_json(indent=2))
        return vector_path

    # 1. Initial checkpoint creation
    def test_initial_checkpoint_creation(self):
        """Verifies initial checkpoint creation from manifest."""
        ledger = self.manager.initialize_ledger()
        self.assertTrue(self.manager.ledger_file.exists())
        self.assertEqual(ledger.total_catalog_chunks, 2)
        self.assertEqual(len(ledger.chunks), 2)
        self.assertIn("chunk_001", ledger.chunks)
        self.assertIn("chunk_002", ledger.chunks)
        self.assertEqual(ledger.chunks["chunk_001"].processing_status, ChunkProcessingStatus.PENDING)
        self.assertEqual(ledger.chunks["chunk_002"].processing_status, ChunkProcessingStatus.PENDING)

    # 2. Atomic save and reload
    def test_atomic_save_and_reload(self):
        """Verifies atomic save and reload of ledger without data loss."""
        ledger = self.manager.initialize_ledger()
        ledger.chunks["chunk_001"].attempt_count = 2
        self.manager.save_ledger(ledger)

        reloaded = self.manager.load_ledger()
        self.assertEqual(reloaded.chunks["chunk_001"].attempt_count, 2)
        self.assertIsNotNone(reloaded.last_checkpoint_commit_utc)

    # 3. Interrupted-write simulation
    def test_interrupted_write_simulation(self):
        """Verifies that an error during save does not corrupt the existing ledger."""
        ledger = self.manager.initialize_ledger()
        original_mtime = self.manager.ledger_file.stat().st_mtime_ns

        # Simulate power cut / permission error during replace
        with patch("os.replace", side_effect=OSError("Simulated disk error")):
            with self.assertRaises(OSError):
                self.manager.save_ledger(ledger)

        # Confirm original ledger remains uncorrupted
        reloaded = self.manager.load_ledger()
        self.assertEqual(reloaded.total_catalog_chunks, 2)
        # Confirm staging .tmp file was cleaned up
        tmp_file = self.manager.ledger_file.with_suffix(".json.tmp")
        self.assertFalse(tmp_file.exists())

    # 4. Valid completed chunk skipping
    def test_valid_completed_chunk_skipping(self):
        """Verifies that a valid completed chunk is identified and skipped."""
        ledger = self.manager.initialize_ledger()
        self._create_valid_vector_file("chunk_001", self.sample_hash)

        # Update status to completed
        self.manager.update_chunk_status(ledger, "chunk_001", ChunkProcessingStatus.COMPLETED)
        self.manager.save_ledger(ledger)

        should_skip, reason = self.manager.should_skip_chunk("chunk_001", self.sample_hash, ledger)
        self.assertTrue(should_skip)
        self.assertIn("Valid completed vector", reason)

    # 5. Changed chunk hash detection
    def test_changed_chunk_hash_detection(self):
        """Verifies that modified chunk content causes skip check to fail and resets to PENDING."""
        ledger = self.manager.initialize_ledger()
        self._create_valid_vector_file("chunk_001", self.sample_hash)
        self.manager.update_chunk_status(ledger, "chunk_001", ChunkProcessingStatus.COMPLETED)

        # Hash altered on disk
        new_hash = "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
        should_skip, reason = self.manager.should_skip_chunk("chunk_001", new_hash, ledger)
        self.assertFalse(should_skip)
        self.assertIn("hash mismatch", reason.lower())

        # Re-running initialization with new manifest hash resets to PENDING
        updated_manifest = list(self.synthetic_manifest)
        updated_manifest[0]["chunk_hash"] = new_hash
        reconciled = self.manager.initialize_ledger(updated_manifest)
        self.assertEqual(reconciled.chunks["chunk_001"].processing_status, ChunkProcessingStatus.PENDING)

    # 6. Orphan vector recovery
    def test_orphan_vector_recovery(self):
        """Verifies adoption of valid orphaned vector files without remote calls."""
        # Create vector file on disk before ledger exists
        self._create_valid_vector_file("chunk_001", self.sample_hash)

        # Initialize ledger
        ledger = self.manager.initialize_ledger()

        # Chunk 1 was adopted as COMPLETED!
        self.assertEqual(ledger.chunks["chunk_001"].processing_status, ChunkProcessingStatus.COMPLETED)
        self.assertEqual(ledger.chunks["chunk_001"].vector_l2_norm, 0.5716)
        self.assertIsNotNone(ledger.chunks["chunk_001"].completed_at_utc)

        # Chunk 2 remains PENDING
        self.assertEqual(ledger.chunks["chunk_002"].processing_status, ChunkProcessingStatus.PENDING)

    # 7. Corrupted vector detection
    def test_corrupted_vector_detection(self):
        """Verifies that corrupted on-disk vector files are detected, removed, and marked PENDING."""
        self.vectors_dir.mkdir(parents=True, exist_ok=True)
        bad_vector_path = self.vectors_dir / "chunk_001.json"
        with open(bad_vector_path, "w", encoding="utf-8") as f:
            f.write("{ invalid json corrupted ...")

        ledger = self.manager.initialize_ledger()

        # Corrupted file is rejected and unlinked, status remains PENDING
        self.assertEqual(ledger.chunks["chunk_001"].processing_status, ChunkProcessingStatus.PENDING)
        self.assertFalse(bad_vector_path.exists())

    # 8. Duplicate chunk prevention
    def test_duplicate_chunk_prevention(self):
        """Verifies that completed chunks are excluded from pending/retryable queue."""
        ledger = self.manager.initialize_ledger()
        self._create_valid_vector_file("chunk_001", self.sample_hash)
        self.manager.update_chunk_status(ledger, "chunk_001", ChunkProcessingStatus.COMPLETED)

        pending = self.manager.get_pending_or_retryable_chunks(ledger)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].chunk_id, "chunk_002")

        completed = self.manager.get_completed_chunks(ledger)
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0].chunk_id, "chunk_001")

    # 9. Status transition validation
    def test_status_transition_validation(self):
        """Verifies strict state machine enforcement for chunk records."""
        ledger = self.manager.initialize_ledger()

        # PENDING -> RETRYABLE_FAILED is allowed
        self.manager.update_chunk_status(
            ledger, "chunk_001", ChunkProcessingStatus.RETRYABLE_FAILED, error_details={"err": "429"}
        )
        self.assertEqual(ledger.chunks["chunk_001"].processing_status, ChunkProcessingStatus.RETRYABLE_FAILED)
        self.assertEqual(ledger.chunks["chunk_001"].attempt_count, 1)

        # RETRYABLE_FAILED -> COMPLETED is allowed once vector file exists
        self._create_valid_vector_file("chunk_001", self.sample_hash)
        self.manager.update_chunk_status(ledger, "chunk_001", ChunkProcessingStatus.COMPLETED)
        self.assertEqual(ledger.chunks["chunk_001"].processing_status, ChunkProcessingStatus.COMPLETED)

        # COMPLETED -> RETRYABLE_FAILED is forbidden
        with self.assertRaises(ValueError):
            self.manager.update_chunk_status(ledger, "chunk_001", ChunkProcessingStatus.RETRYABLE_FAILED)

        # COMPLETED without vector file raises error
        with self.assertRaises(ValueError):
            self.manager.update_chunk_status(ledger, "chunk_002", ChunkProcessingStatus.COMPLETED)

        # PERMANENTLY_FAILED requires error_details
        with self.assertRaises(ValueError):
            self.manager.update_chunk_status(
                ledger, "chunk_002", ChunkProcessingStatus.PERMANENTLY_FAILED, error_details=None
            )

    # 10. Preservation of completed records after simulated failures
    def test_preservation_of_completed_records_after_simulated_failures(self):
        """Verifies that failures on later chunks do not corrupt or discard earlier completed records."""
        ledger = self.manager.initialize_ledger()

        # Step 1: Chunk 1 completes
        self._create_valid_vector_file("chunk_001", self.sample_hash)
        self.manager.update_chunk_status(ledger, "chunk_001", ChunkProcessingStatus.COMPLETED)
        self.manager.save_ledger(ledger)

        # Step 2: Chunk 2 fails permanently
        self.manager.update_chunk_status(
            ledger,
            "chunk_002",
            ChunkProcessingStatus.PERMANENTLY_FAILED,
            error_details={"code": 400, "reason": "Malformed input"},
        )
        self.manager.save_ledger(ledger)

        # Reload from disk
        reloaded = self.manager.load_ledger()
        self.assertEqual(reloaded.chunks["chunk_001"].processing_status, ChunkProcessingStatus.COMPLETED)
        self.assertEqual(reloaded.chunks["chunk_002"].processing_status, ChunkProcessingStatus.PERMANENTLY_FAILED)
        counts = reloaded.sync_status_counts()
        self.assertEqual(counts["completed"], 1)
        self.assertEqual(counts["permanently_failed"], 1)
        self.assertEqual(counts["pending"], 0)


if __name__ == "__main__":
    unittest.main()

