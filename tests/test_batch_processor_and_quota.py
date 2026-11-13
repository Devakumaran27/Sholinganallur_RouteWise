"""
Unit tests for Phase 3 — Step 6D.4: Batch Embedding Processor and Quota Manager.
Tests quota enforcement, TPM safety, RPM pacing, batch bisection, retry backoff,
resume idempotency, failed-call accounting, UTC daily rollover, and CLI options
using mocked Gemini responses and temporary directories.
Zero real Gemini API calls or database operations are executed.
"""

import json
import math
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from google.genai import errors

from app.ai.rag.embeddings.batch_processor import BatchProcessor
from app.ai.rag.embeddings.checkpoint_manager import CheckpointManager
from app.ai.rag.embeddings.embedding_service import (
    AuthenticationError,
    EmbeddingService,
    InvalidRequestError,
    RateLimitError,
    TransientServerError,
)
from app.ai.rag.embeddings.models import (
    APIRequestTracker,
    BatchCheckpointLedger,
    ChunkProcessingStatus,
    EmbeddingMetadata,
    TokenAccounting,
    VectorRecord,
)
from app.ai.rag.embeddings.quota_manager import (
    QuotaExceededError,
    QuotaManager,
    TPMLimitExceededError,
)


class TestBatchProcessorAndQuota(unittest.TestCase):
    """Test suite for QuotaManager and BatchProcessor."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.temp_dir.name)
        self.chunks_dir = self.root_path / "chunks"
        self.vectors_dir = self.root_path / "vectors"
        self.checkpoints_dir = self.root_path / "checkpoints"
        self.quarantine_dir = self.root_path / "quarantine"
        self.manifest_path = self.root_path / "chunks_manifest.json"

        self.chunks_dir.mkdir(parents=True, exist_ok=True)
        self.vectors_dir.mkdir(parents=True, exist_ok=True)
        self.checkpoints_dir.mkdir(parents=True, exist_ok=True)
        self.quarantine_dir.mkdir(parents=True, exist_ok=True)

        self.sample_hash_1 = "2b6b0266fe2fbeaa8451a44e5d8a0c20ab2560d2e85a6ba10faad695b23d9061"
        self.sample_hash_2 = "916521fb063944af6f7abd8fe4b45c00e9d7f950fce08f816930bb691aacc5ee"
        self.sample_hash_3 = "cc63fbd50d8ec5e7aaf643320e88a5e9059328b6a81c140be61dd789b05010dd"

        self.synthetic_manifest = [
            {
                "chunk_id": "chunk_001",
                "document_id": "doc_001",
                "chunk_hash": self.sample_hash_1,
                "dataset_version": "v1.0.0-sholinganallur",
                "token_count": 350,
                "title": "Stop Profile 001",
            },
            {
                "chunk_id": "chunk_002",
                "document_id": "doc_001",
                "chunk_hash": self.sample_hash_2,
                "dataset_version": "v1.0.0-sholinganallur",
                "token_count": 420,
                "title": "Stop Profile 002",
            },
            {
                "chunk_id": "chunk_003",
                "document_id": "doc_002",
                "chunk_hash": self.sample_hash_3,
                "dataset_version": "v1.0.0-sholinganallur",
                "token_count": 280,
                "title": "Route Profile 003",
            },
        ]

        # Write manifest
        with open(self.manifest_path, "w", encoding="utf-8") as f:
            json.dump(self.synthetic_manifest, f)

        # Write individual chunk files
        for item in self.synthetic_manifest:
            chunk_file = self.chunks_dir / f"{item['chunk_id']}.json"
            chunk_content = {
                "chunk_id": item["chunk_id"],
                "document_id": item["document_id"],
                "chunk_text": f"Text payload content for {item['chunk_id']}.",
                "chunk_hash": item["chunk_hash"],
                "title": item["title"],
                "token_count": item["token_count"],
            }
            with open(chunk_file, "w", encoding="utf-8") as cf:
                json.dump(chunk_content, cf)

        # Mock embedding service client
        self.mock_client = MagicMock()
        self.embedding_service = EmbeddingService(
            client=self.mock_client,
            model_name="gemini-embedding-001",
            embedding_dimensions=768,
        )

        self.checkpoint_manager = CheckpointManager(
            checkpoint_dir=self.checkpoints_dir,
            vectors_dir=self.vectors_dir,
            manifest_path=self.manifest_path,
        )

        self.quota_manager = QuotaManager(
            daily_budget=200,
            tpm_safety_limit=24000,
            pacing_delay_seconds=0.0,  # Fast for tests
        )

        self.processor = BatchProcessor(
            checkpoint_manager=self.checkpoint_manager,
            embedding_service=self.embedding_service,
            quota_manager=self.quota_manager,
            manifest_path=self.manifest_path,
            chunks_dir=self.chunks_dir,
            vectors_dir=self.vectors_dir,
            quarantine_dir=self.quarantine_dir,
            batch_size=2,
            max_retries=2,
            backoff_base_seconds=0.01,
            backoff_max_seconds=0.05,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def _mock_batch_embeddings_response(self, num_chunks: int, token_count: int = 350):
        """Constructs a mock response containing num_chunks 768-dim embeddings."""
        mock_resp = MagicMock()
        embeddings = []
        for _ in range(num_chunks):
            emb_obj = MagicMock()
            emb_obj.values = [0.01] * 768
            embeddings.append(emb_obj)
        mock_resp.embeddings = embeddings
        mock_resp.metadata = MagicMock(token_count=token_count)
        return mock_resp

    # 1. Quota reservation and release
    def test_quota_reservation_and_release(self):
        """Verifies token budget calculation and request completion recording."""
        qm = QuotaManager(daily_budget=10, tpm_safety_limit=5000, pacing_delay_seconds=0.0)
        reserved = qm.reserve_budget(estimated_tokens=400, call_type="initial")
        self.assertEqual(reserved, 500)  # 400 * 1.25 = 500

        qm.record_request_completion(actual_tokens=420, estimated_tokens=400, call_type="initial")
        self.assertEqual(qm.tracker.global_api_requests, 1)
        self.assertEqual(qm.tracker.initial_requests, 1)
        self.assertEqual(qm.get_current_rolling_tpm(), 420)

    # 2. Daily request budget enforcement
    def test_daily_request_budget_enforcement(self):
        """Verifies that exceeding the daily request budget raises QuotaExceededError."""
        qm = QuotaManager(daily_budget=2, pacing_delay_seconds=0.0)
        qm.reserve_budget(100)
        qm.record_request_completion(100, call_type="initial")
        qm.reserve_budget(100)
        qm.record_request_completion(100, call_type="initial")

        # 3rd request should fail
        with self.assertRaises(QuotaExceededError) as ctx:
            qm.reserve_budget(100)
        self.assertIn("Daily API request safety budget reached", str(ctx.exception))

    # 3. TPM safety enforcement
    def test_tpm_safety_enforcement(self):
        """Verifies that exceeding rolling TPM ceiling raises TPMLimitExceededError when wait=False."""
        qm = QuotaManager(tpm_safety_limit=1000, pacing_delay_seconds=0.0)
        # 900 estimated * 1.25 = 1125 > 1000 limit
        with self.assertRaises(TPMLimitExceededError) as ctx:
            qm.reserve_budget(estimated_tokens=900, wait_if_needed=False)
        self.assertIn("TPM limit exceeded", str(ctx.exception))

    # 4. RPM pacing logic
    def test_rpm_pacing_logic(self):
        """Verifies that pacing delay computes wait time between consecutive requests."""
        qm = QuotaManager(pacing_delay_seconds=0.05)
        start = time.time()
        qm.reserve_budget(50)
        qm.record_request_completion(50)

        # Second call enforces ~0.05s wait
        qm.reserve_budget(50)
        duration = time.time() - start
        self.assertGreaterEqual(duration, 0.04)

    # 5. Global request counter
    def test_global_request_counter(self):
        """Verifies exact tracking across initial, retry, and bisection calls."""
        qm = QuotaManager(daily_budget=10, pacing_delay_seconds=0.0)
        qm.reserve_budget(10)
        qm.record_request_completion(10, call_type="initial")
        qm.reserve_budget(10)
        qm.record_request_completion(10, call_type="retry")
        qm.reserve_budget(10)
        qm.record_request_completion(10, call_type="bisection")

        self.assertEqual(qm.tracker.global_api_requests, 3)
        self.assertEqual(qm.tracker.initial_requests, 1)
        self.assertEqual(qm.tracker.retry_requests, 1)
        self.assertEqual(qm.tracker.bisection_requests, 1)

    # 6. Batch processing with mocked responses
    def test_batch_processing_with_mocked_responses(self):
        """Verifies end-to-end processing of chunks in batches with atomic persistence."""
        # 3 chunks with batch_size=2 -> 2 batches (batch of 2, batch of 1)
        self.mock_client.models.embed_content.side_effect = [
            self._mock_batch_embeddings_response(num_chunks=2),
            self._mock_batch_embeddings_response(num_chunks=1),
        ]

        result = self.processor.process_all(dry_run=False)

        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(result["chunks_succeeded_in_run"], 3)
        self.assertEqual(result["chunks_quarantined_in_run"], 0)
        self.assertEqual(self.mock_client.models.embed_content.call_count, 2)

        # Confirm 3 vector files written on disk
        vector_files = list(self.vectors_dir.glob("*.json"))
        self.assertEqual(len(vector_files), 3)

        # Confirm manifest was generated
        manifest_file = self.root_path / "embeddings_manifest.json"
        self.assertTrue(manifest_file.exists())

    # 7. Partial success and recursive bisection
    def test_partial_success_and_bisection(self):
        """Verifies that an InvalidRequestError on a batch triggers bisection down to quarantine."""
        self.mock_client.models.embed_content.side_effect = [
            # Batch 1 (chunks 1 & 2) fails
            errors.APIError(400, {"error": {"message": "Invalid token in payload."}}),
            # Sub-batch A (chunk 1) succeeds
            self._mock_batch_embeddings_response(num_chunks=1),
            # Sub-batch B (chunk 2) fails again
            errors.APIError(400, {"error": {"message": "Invalid token in chunk 2."}}),
            # Batch 2 (chunk 3) succeeds
            self._mock_batch_embeddings_response(num_chunks=1),
        ]

        result = self.processor.process_all(dry_run=False)

        self.assertEqual(result["chunks_succeeded_in_run"], 2)  # chunks 1 and 3
        self.assertEqual(result["chunks_quarantined_in_run"], 1)  # chunk 2

        # Verify chunk 2 was quarantined
        quarantine_files = list(self.quarantine_dir.glob("*.json"))
        self.assertEqual(len(quarantine_files), 1)
        self.assertEqual(quarantine_files[0].stem, "chunk_002_error")

    # 8. Retry and backoff on transient errors
    def test_retry_and_backoff(self):
        """Verifies that 429 rate limit errors trigger backoff retry and succeed."""
        self.mock_client.models.embed_content.side_effect = [
            # Batch 1, attempt 0: Rate limit 429
            errors.APIError(429, {"error": {"message": "ResourceExhausted."}}),
            # Batch 1, attempt 1: Succeeds
            self._mock_batch_embeddings_response(num_chunks=2),
            # Batch 2: Succeeds
            self._mock_batch_embeddings_response(num_chunks=1),
        ]

        result = self.processor.process_all(dry_run=False)

        self.assertEqual(result["chunks_succeeded_in_run"], 3)
        self.assertEqual(self.quota_manager.tracker.retry_requests, 1)

    # 9. Authentication failure fails fast
    def test_authentication_failure_fails_fast(self):
        """Verifies that HTTP 401 raises AuthenticationError immediately without retrying."""
        self.mock_client.models.embed_content.side_effect = errors.APIError(
            401, {"error": {"message": "Invalid API key."}}
        )

        with self.assertRaises(AuthenticationError):
            self.processor.process_all(dry_run=False)

        # Dispatched only 1 call before failing fast
        self.assertEqual(self.mock_client.models.embed_content.call_count, 1)

    # 10. Bisection budget enforcement
    def test_bisection_budget_enforcement(self):
        """Verifies that bisection halts safely when daily request budget is exhausted."""
        # Set quota manager budget to 2 requests
        self.processor.quota_manager.daily_budget = 2
        self.processor.quota_manager.tracker.daily_budget_limit = 2

        self.mock_client.models.embed_content.side_effect = [
            # Request 1: Batch of 2 fails
            errors.APIError(400, {"error": {"message": "Invalid input."}}),
            # Request 2: Sub-batch 1 succeeds (now 2 requests used!)
            self._mock_batch_embeddings_response(num_chunks=1),
            # Request 3: Budget exceeded before Sub-batch 2 dispatches
            self._mock_batch_embeddings_response(num_chunks=1),
        ]

        result = self.processor.process_all(dry_run=False)
        self.assertEqual(result["status"], "BUDGET_REACHED")
        self.assertEqual(self.quota_manager.tracker.global_api_requests, 2)

    # 11. Resume and duplicate prevention
    def test_resume_and_duplicate_prevention(self):
        """Verifies that restarting a pipeline skips previously completed chunks."""
        # Run 1: process only 2 chunks (limit=2)
        self.mock_client.models.embed_content.return_value = self._mock_batch_embeddings_response(num_chunks=2)
        res1 = self.processor.process_all(limit=2, dry_run=False)
        self.assertEqual(res1["chunks_succeeded_in_run"], 2)

        # Run 2: resume full run
        # Only chunk 3 needs embedding!
        self.mock_client.models.embed_content.reset_mock()
        self.mock_client.models.embed_content.return_value = self._mock_batch_embeddings_response(num_chunks=1)

        res2 = self.processor.process_all(dry_run=False)
        self.assertEqual(res2["chunks_succeeded_in_run"], 1)
        self.assertEqual(self.mock_client.models.embed_content.call_count, 1)

    # 12. Dry-run makes zero API calls
    def test_dry_run_makes_zero_api_calls(self):
        """Verifies that --dry-run mode executes planning with zero remote calls."""
        plan = self.processor.process_all(dry_run=True)
        self.assertEqual(plan["status"], "PLAN_COMPLETED")
        self.assertEqual(plan["total_catalog_chunks"], 3)
        self.mock_client.models.embed_content.assert_not_called()

    # 13. Limit option restricts processing
    def test_limit_option_restricts_processing(self):
        """Verifies that --limit restricts the number of chunks processed."""
        self.mock_client.models.embed_content.return_value = self._mock_batch_embeddings_response(num_chunks=1)
        res = self.processor.process_all(limit=1, dry_run=False)
        self.assertEqual(res["chunks_succeeded_in_run"], 1)
        self.assertEqual(self.mock_client.models.embed_content.call_count, 1)

    # ==========================================================================
    # Additional Verification Tests for Step 6D.4 Mandatory Corrections
    # ==========================================================================

    # 14. Daily counter resets when UTC date advances
    def test_daily_counter_resets_when_utc_date_advances(self):
        """Verifies that daily counters reset to 0 when UTC date advances to a new day."""
        ledger = self.checkpoint_manager.initialize_ledger(self.synthetic_manifest)
        # Simulate requests made yesterday
        ledger.accounting.global_api_requests = 150
        ledger.accounting.initial_requests = 120
        ledger.accounting.retry_requests = 20
        ledger.accounting.bisection_requests = 10
        ledger.accounting.last_request_date_utc = "2026-11-07"
        self.checkpoint_manager.save_ledger(ledger)

        qm = QuotaManager(daily_budget=200, pacing_delay_seconds=0.0)
        reset_occurred = qm.sync_with_ledger(ledger, self.checkpoint_manager)

        self.assertTrue(reset_occurred)
        self.assertEqual(qm.tracker.global_api_requests, 0)
        self.assertEqual(qm.tracker.initial_requests, 0)
        self.assertEqual(qm.tracker.retry_requests, 0)
        self.assertEqual(qm.tracker.bisection_requests, 0)
        self.assertEqual(qm.get_remaining_daily_requests(), 200)

    # 15. Daily counter does not reset on same-day restart
    def test_daily_counter_does_not_reset_on_same_day_restart(self):
        """Verifies that counters are preserved when the application restarts on the same UTC date."""
        ledger = self.checkpoint_manager.initialize_ledger(self.synthetic_manifest)
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        ledger.accounting.global_api_requests = 75
        ledger.accounting.initial_requests = 60
        ledger.accounting.retry_requests = 10
        ledger.accounting.bisection_requests = 5
        ledger.accounting.last_request_date_utc = today_str
        self.checkpoint_manager.save_ledger(ledger)

        qm = QuotaManager(daily_budget=200, pacing_delay_seconds=0.0)
        reset_occurred = qm.sync_with_ledger(ledger, self.checkpoint_manager)

        self.assertFalse(reset_occurred)
        self.assertEqual(qm.tracker.global_api_requests, 75)
        self.assertEqual(qm.tracker.initial_requests, 60)
        self.assertEqual(qm.tracker.retry_requests, 10)
        self.assertEqual(qm.tracker.bisection_requests, 5)
        self.assertEqual(qm.get_remaining_daily_requests(), 125)

    # 16. All request-category counters reset together
    def test_all_request_category_counters_reset_together(self):
        """Verifies that all 4 request counters reset synchronously maintaining sum invariant."""
        tracker = APIRequestTracker(
            global_api_requests=80,
            initial_requests=50,
            retry_requests=20,
            bisection_requests=10,
            daily_budget_limit=200,
            last_request_date_utc="2026-11-01",
        )
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        tracker.reset_daily_counters(today_str)

        self.assertEqual(tracker.global_api_requests, 0)
        self.assertEqual(tracker.initial_requests, 0)
        self.assertEqual(tracker.retry_requests, 0)
        self.assertEqual(tracker.bisection_requests, 0)
        self.assertEqual(tracker.last_request_date_utc, today_str)

    # 17. TPM rolling-window state remains valid across daily rollover
    def test_tpm_rolling_window_valid_across_daily_rollover(self):
        """Verifies that sliding TPM tokens are preserved when daily request counters rollover."""
        qm = QuotaManager(daily_budget=200, pacing_delay_seconds=0.0)
        # Add 500 tokens consumed 10 seconds ago
        qm._token_history.append((time.time() - 10.0, 500))

        ledger = self.checkpoint_manager.initialize_ledger(self.synthetic_manifest)
        ledger.accounting.global_api_requests = 180
        ledger.accounting.initial_requests = 180
        ledger.accounting.last_request_date_utc = "2026-11-01"

        qm.sync_with_ledger(ledger, self.checkpoint_manager)

        # Requests reset to 0, but recent in-window tokens are preserved!
        self.assertEqual(qm.tracker.global_api_requests, 0)
        self.assertEqual(qm.get_current_rolling_tpm(), 500)

    # 18. Failed HTTP 429 invocation increments the request count
    def test_failed_http_429_increments_request_count(self):
        """Verifies that an attempt failing with HTTP 429 increments the global request counter."""
        self.mock_client.models.embed_content.side_effect = [
            # Attempt 0: 429 rate limit error (failed attempt)
            errors.APIError(429, {"error": {"message": "ResourceExhausted."}}),
            # Attempt 1: succeeds
            self._mock_batch_embeddings_response(num_chunks=2),
            # Batch 2: succeeds
            self._mock_batch_embeddings_response(num_chunks=1),
        ]

        self.processor.process_all(dry_run=False)

        # Total calls: 1 initial (failed) + 1 retry (success) + 1 initial (success) = 3 total!
        self.assertEqual(self.quota_manager.tracker.global_api_requests, 3)
        self.assertEqual(self.quota_manager.tracker.initial_requests, 2)
        self.assertEqual(self.quota_manager.tracker.retry_requests, 1)

    # 19. Failed HTTP 503 invocation increments the request count
    def test_failed_http_503_increments_request_count(self):
        """Verifies that an attempt failing with HTTP 503 increments the global request counter."""
        self.mock_client.models.embed_content.side_effect = [
            # Attempt 0: 503 server error (failed attempt)
            errors.APIError(503, {"error": {"message": "Service unavailable."}}),
            # Attempt 1: succeeds
            self._mock_batch_embeddings_response(num_chunks=2),
            # Batch 2: succeeds
            self._mock_batch_embeddings_response(num_chunks=1),
        ]

        self.processor.process_all(dry_run=False)

        self.assertEqual(self.quota_manager.tracker.global_api_requests, 3)
        self.assertEqual(self.quota_manager.tracker.retry_requests, 1)

    # 20. Network failure after dispatch increments the request count
    def test_network_failure_after_dispatch_increments_request_count(self):
        """Verifies that network socket timeout after dispatch begins increments the counter."""
        self.mock_client.models.embed_content.side_effect = [
            # Attempt 0: Socket timeout
            TimeoutError("Socket read timeout"),
            # Attempt 1: succeeds
            self._mock_batch_embeddings_response(num_chunks=2),
            # Batch 2: succeeds
            self._mock_batch_embeddings_response(num_chunks=1),
        ]

        self.processor.process_all(dry_run=False)

        self.assertEqual(self.quota_manager.tracker.global_api_requests, 3)
        self.assertEqual(self.quota_manager.tracker.retry_requests, 1)

    # 21. Successful invocation increments exactly once
    def test_successful_invocation_increments_exactly_once(self):
        """Verifies that a successful request increments the counter by exactly 1."""
        self.mock_client.models.embed_content.return_value = self._mock_batch_embeddings_response(num_chunks=1)
        res = self.processor.process_all(limit=1, dry_run=False)

        self.assertEqual(res["chunks_succeeded_in_run"], 1)
        self.assertEqual(self.quota_manager.tracker.global_api_requests, 1)
        self.assertEqual(self.quota_manager.tracker.initial_requests, 1)

    # 22. Pre-dispatch quota rejection does not increment the request count
    def test_predispatch_quota_rejection_does_not_increment_request_count(self):
        """Verifies that blocked calls (before network dispatch) do not increment the counter."""
        qm = QuotaManager(daily_budget=1, pacing_delay_seconds=0.0)
        qm.reserve_budget(100)
        qm.record_request_completion(100)
        self.assertEqual(qm.tracker.global_api_requests, 1)

        # Second call is blocked in reserve_budget before dispatch
        with self.assertRaises(QuotaExceededError):
            qm.reserve_budget(100)

        # Counter remains 1
        self.assertEqual(qm.tracker.global_api_requests, 1)

    # 23. Daily budget still blocks requests after reaching 200
    def test_daily_budget_still_blocks_requests_after_reaching_200(self):
        """Verifies that reaching 200 requests firmly blocks further API calls."""
        qm = QuotaManager(daily_budget=200, pacing_delay_seconds=0.0)
        qm.tracker.global_api_requests = 200
        qm.tracker.initial_requests = 200

        self.assertFalse(qm.can_make_request())
        with self.assertRaises(QuotaExceededError):
            qm.reserve_budget(100)

    # 24. Restart reloads the persisted request count correctly
    def test_restart_reloads_persisted_request_count_correctly(self):
        """Verifies that restarting reloads the persisted count from checkpoint ledger."""
        ledger = self.checkpoint_manager.initialize_ledger(self.synthetic_manifest)
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        ledger.accounting.global_api_requests = 88
        ledger.accounting.initial_requests = 80
        ledger.accounting.retry_requests = 5
        ledger.accounting.bisection_requests = 3
        ledger.accounting.last_request_date_utc = today_str
        self.checkpoint_manager.save_ledger(ledger)

        # Create new manager and processor simulating process restart
        new_cm = CheckpointManager(
            checkpoint_dir=self.checkpoints_dir,
            vectors_dir=self.vectors_dir,
            manifest_path=self.manifest_path,
        )
        new_qm = QuotaManager(daily_budget=200, pacing_delay_seconds=0.0)
        new_ledger = new_cm.load_ledger()
        new_qm.sync_with_ledger(new_ledger, new_cm)

        self.assertEqual(new_qm.tracker.global_api_requests, 88)
        self.assertEqual(new_qm.tracker.initial_requests, 80)
        self.assertEqual(new_qm.tracker.retry_requests, 5)
        self.assertEqual(new_qm.tracker.bisection_requests, 3)
        self.assertEqual(new_qm.get_remaining_daily_requests(), 112)

    # 25. Persistence occurs strictly BEFORE remote API dispatch begins
    def test_persistence_strictly_before_api_dispatch(self):
        """Verifies ledger on disk contains incremented counter before API method is entered."""
        on_disk_counter_during_call = None

        def side_effect_inspect_disk(*args, **kwargs):
            nonlocal on_disk_counter_during_call
            # Read disk file directly while inside the API invocation
            with open(self.checkpoint_manager.ledger_file, "r", encoding="utf-8") as f:
                disk_data = json.load(f)
            on_disk_counter_during_call = disk_data["accounting"]["global_api_requests"]
            return self._mock_batch_embeddings_response(num_chunks=1)

        self.mock_client.models.embed_content.side_effect = side_effect_inspect_disk
        res = self.processor.process_all(limit=1, dry_run=False)

        self.assertEqual(res["chunks_succeeded_in_run"], 1)
        # Disk had the counter incremented to 1 BEFORE the API returned!
        self.assertEqual(on_disk_counter_during_call, 1)
        self.assertEqual(self.quota_manager.tracker.global_api_requests, 1)

    # 26. Simulated process interruption during dispatch preserves persisted counter
    def test_simulated_process_interruption_after_dispatch_preserves_counter(self):
        """Verifies simulated hard crash after dispatch does not lose persisted request counter."""
        def side_effect_crash(*args, **kwargs):
            # Simulate sudden hard unrecoverable crash or kill
            raise SystemExit("Simulated SIGKILL / process crash during network dispatch")

        self.mock_client.models.embed_content.side_effect = side_effect_crash

        with self.assertRaises(SystemExit):
            self.processor.process_all(limit=1, dry_run=False)

        # Inspect disk file with a completely new CheckpointManager instance
        fresh_cm = CheckpointManager(
            checkpoint_dir=self.checkpoints_dir,
            vectors_dir=self.vectors_dir,
            manifest_path=self.manifest_path,
        )
        fresh_ledger = fresh_cm.load_ledger()
        # Even though process exited with SystemExit, the request attempt was persisted before dispatch!
        self.assertEqual(fresh_ledger.accounting.global_api_requests, 1)
        self.assertEqual(fresh_ledger.accounting.initial_requests, 1)

    # 27. Batch of 20 chunks counts as exactly one API request
    def test_batch_of_20_chunks_counts_as_one_request(self):
        """Verifies that a batch of 20 chunks counts as exactly one API request."""
        run_dir = Path(self.temp_dir.name) / "run_20"
        chunks_dir_20 = run_dir / "chunks"
        checkpoints_dir_20 = run_dir / "checkpoints"
        vectors_dir_20 = run_dir / "vectors"
        quarantine_dir_20 = run_dir / "quarantine"
        manifest_path_20 = run_dir / "manifest_20.json"

        chunks_dir_20.mkdir(parents=True, exist_ok=True)
        checkpoints_dir_20.mkdir(parents=True, exist_ok=True)
        vectors_dir_20.mkdir(parents=True, exist_ok=True)
        quarantine_dir_20.mkdir(parents=True, exist_ok=True)

        # Create manifest with 20 chunks and write chunk content files
        chunks_20 = []
        for i in range(20):
            cid = f"chunk_bulk_{i:03d}"
            chash = f"{i:04x}" + "a" * 60
            item = {
                "chunk_id": cid,
                "document_id": "doc_bulk_001",
                "chunk_hash": chash,
                "token_count": 300,
                "title": f"Bulk Chunk {i}",
            }
            chunks_20.append(item)
            chunk_file = chunks_dir_20 / f"{cid}.json"
            with open(chunk_file, "w", encoding="utf-8") as cf:
                json.dump(
                    {
                        "chunk_id": cid,
                        "document_id": "doc_bulk_001",
                        "chunk_text": f"Bulk chunk content payload for {cid}.",
                        "chunk_hash": chash,
                        "title": f"Bulk Chunk {i}",
                        "token_count": 300,
                    },
                    cf,
                )

        with open(manifest_path_20, "w", encoding="utf-8") as f:
            json.dump(chunks_20, f)

        cm_20 = CheckpointManager(
            checkpoint_dir=checkpoints_dir_20,
            vectors_dir=vectors_dir_20,
            manifest_path=manifest_path_20,
        )
        qm_20 = QuotaManager(daily_budget=200, pacing_delay_seconds=0.0)
        proc_20 = BatchProcessor(
            checkpoint_manager=cm_20,
            embedding_service=self.embedding_service,
            quota_manager=qm_20,
            manifest_path=manifest_path_20,
            chunks_dir=chunks_dir_20,
            vectors_dir=vectors_dir_20,
            quarantine_dir=quarantine_dir_20,
            batch_size=20,
        )

        self.mock_client.models.embed_content.return_value = self._mock_batch_embeddings_response(num_chunks=20)
        res = proc_20.process_all(dry_run=False)

        self.assertEqual(res["chunks_succeeded_in_run"], 20)
        self.assertEqual(res["total_api_requests_issued"], 1)
        self.assertEqual(qm_20.tracker.global_api_requests, 1)
        self.assertEqual(qm_20.tracker.initial_requests, 1)
        self.assertEqual(qm_20.get_remaining_daily_requests(), 199)

    # 28. Retry and bisection calls each count separately
    def test_retry_and_bisection_calls_each_count_separately(self):
        """Verifies that initial, bisection, and retry requests are each counted and tracked separately."""
        # 2-chunk batch: initial attempt fails with 400 (triggers bisection)
        # left chunk (1 chunk) succeeds
        # right chunk (1 chunk) fails first with 503, then retries and succeeds
        self.mock_client.models.embed_content.side_effect = [
            # 1. Initial batch (2 chunks) fails with 400
            errors.APIError(400, {"error": {"message": "Invalid token in chunk"}}),
            # 2. Bisection left (1 chunk) succeeds
            self._mock_batch_embeddings_response(num_chunks=1),
            # 3. Bisection right attempt 0 (1 chunk) fails with 503
            errors.APIError(503, {"error": {"message": "Service unavailable"}}),
            # 4. Bisection right retry 1 (1 chunk) succeeds
            self._mock_batch_embeddings_response(num_chunks=1),
        ]

        res = self.processor.process_all(limit=2, dry_run=False)

        self.assertEqual(res["chunks_succeeded_in_run"], 2)
        # Expected calls:
        # - initial batch: 1 (initial)
        # - left sub-batch: 1 (bisection)
        # - right sub-batch attempt 0: 1 (bisection)
        # - right sub-batch retry 1: 1 (retry)
        # Total = 4 requests
        self.assertEqual(self.quota_manager.tracker.global_api_requests, 4)
        self.assertEqual(self.quota_manager.tracker.initial_requests, 1)
        self.assertEqual(self.quota_manager.tracker.bisection_requests, 2)
        self.assertEqual(self.quota_manager.tracker.retry_requests, 1)

    # 29. Concurrency and atomicity protection
    def test_concurrent_request_counter_and_persistence(self):
        """Verifies thread-safety and lock protection across concurrent workers."""
        ledger = self.checkpoint_manager.initialize_ledger(self.synthetic_manifest)
        qm = QuotaManager(daily_budget=100, pacing_delay_seconds=0.0)
        qm.sync_with_ledger(ledger, self.checkpoint_manager)

        import concurrent.futures

        def worker_task(worker_id: int):
            try:
                qm.reserve_budget(estimated_tokens=50, wait_if_needed=False)
                qm.record_request_attempt(call_type="initial")
                self.checkpoint_manager.save_ledger(ledger)
                return True
            except QuotaExceededError:
                return False

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(worker_task, i) for i in range(25)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        successful_dispatches = sum(1 for r in results if r)
        self.assertEqual(successful_dispatches, 25)
        self.assertEqual(qm.tracker.global_api_requests, 25)

        # Verify disk ledger validity
        fresh_cm = CheckpointManager(
            checkpoint_dir=self.checkpoints_dir,
            vectors_dir=self.vectors_dir,
            manifest_path=self.manifest_path,
        )
        fresh_ledger = fresh_cm.load_ledger()
        self.assertEqual(fresh_ledger.accounting.global_api_requests, 25)


if __name__ == "__main__":
    unittest.main()
