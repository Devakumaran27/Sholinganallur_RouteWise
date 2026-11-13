# Phase 3 — Step 6D.4 Mandatory Corrections Report

**Project:** Sholinganallur_RouteWise  
**Date:** 2026-11-08  
**Author:** Senior Python GenAI Backend Engineer  
**Status:** COMPLETED & VERIFIED  

---

## 1. Executive Summary

Following the static safety audit of the Phase 3 Step 6D.4 batch embedding pipeline (`data/reports/step_6d_4_final_safety_audit.md`), two critical safety corrections were identified to guarantee compliance with Google AI Studio Free Tier limits (1,000 RPD, 30,000 TPM, 100 RPM) and maintain strict local ledger integrity:

1. **Correction 1: UTC Daily Request Budget Reset & Atomic Persistence**  
   Implemented automatic calendar-day rollover tracking using UTC calendar dates (`YYYY-MM-DD`). When the UTC date advances, the daily request counters (`global_api_requests`, `initial_requests`, `retry_requests`, `bisection_requests`) reset to zero, and the reset ledger is immediately persisted atomically to disk. Restarts on the same UTC day strictly preserve existing counters. The rolling 60-second TPM sliding window remains decoupled from the daily counter reset.

2. **Correction 2: Precise Accounting of Dispatched API Invocations**  
   Ensured that **every** remote API invocation reaching the network boundary is counted against the daily budget (`MAX_DAILY_REQUEST_BUDGET = 200`), including HTTP 400 (Bad Request), HTTP 429 (Rate Limit), HTTP 500/503/504 (Server Errors), and network socket timeouts. Crucially, requests blocked *prior* to dispatch (e.g., TPM limit exhaustion, daily budget limit hit) do not consume budget units, successful requests increment exactly once, and request attempts are persisted to disk immediately—even if the subsequent call fails with an unhandled exception.

All unit tests were expanded to cover 11 dedicated safety test cases. All 24 tests in `tests/test_batch_processor_and_quota.py` and all 67 tests across the entire test suite passed with zero errors. Zero real API calls were made, and no database operations were performed.

---

## 2. Implemented Code Changes

### 2.1. `app/ai/rag/embeddings/models.py`

#### Changes:
1. **Added `last_request_date_utc: Optional[str] = None`** to `ApiRequestTracker` to record the ISO calendar date (`YYYY-MM-DD`) when the last request occurred.
2. **Updated `record_request()`** to update `self.last_request_date_utc = now.strftime("%Y-%m-%d")` whenever a request is registered.
3. **Added `reset_daily_counters(new_date_str: Optional[str] = None)`** method to `ApiRequestTracker` to atomically reset all daily counters while updating `last_request_date_utc` and logging the reset event cleanly.

```python
# app/ai/rag/embeddings/models.py (Snippet)

class ApiRequestTracker(BaseModel):
    ...
    last_request_date_utc: Optional[str] = Field(
        default=None,
        description="UTC calendar date (YYYY-MM-DD) of the last recorded API request for daily quota rollover tracking."
    )

    def record_request(self, category: RequestCategory = RequestCategory.INITIAL, tokens: int = 0) -> None:
        now = datetime.now(timezone.utc)
        self.last_request_timestamp = now
        self.last_request_date_utc = now.strftime("%Y-%m-%d")
        self.global_api_requests += 1
        ...

    def reset_daily_counters(self, new_date_str: Optional[str] = None) -> None:
        """Atomically reset daily request counters upon UTC calendar date change."""
        self.global_api_requests = 0
        self.initial_requests = 0
        self.retry_requests = 0
        self.bisection_requests = 0
        now = datetime.now(timezone.utc)
        self.last_request_date_utc = new_date_str or now.strftime("%Y-%m-%d")
        self.last_request_timestamp = now
```

---

### 2.2. `app/ai/rag/embeddings/quota_manager.py`

#### Changes:
1. **Extended `sync_with_ledger(ledger, checkpoint_manager=None)`**:
   - Compares `current_date_str` (`datetime.now(timezone.utc).strftime("%Y-%m-%d")`) against `ledger.request_tracker.last_request_date_utc` (or falls back to `last_request_timestamp.strftime("%Y-%m-%d")`).
   - If `current_date_str > stored_date`, invokes `tracker.reset_daily_counters(current_date_str)` and, if a `checkpoint_manager` is supplied, immediately calls `checkpoint_manager.save_ledger(ledger)` to persist the rollover to disk.
   - If `current_date_str == stored_date`, restores the persisted `global_api_requests` directly into `self._global_request_count`.
   - Leaves sliding 60-second TPM tokens (`self._token_history`) untouched, ensuring in-flight burst safety is preserved across midnight boundaries.
2. **Introduced `record_request_attempt(category)`**:
   - Immediately increments `self._global_request_count` and updates the ledger's request tracker *before* network transmission begins.
   - Sets `self._last_request_time` for RPM pacing enforcement.
3. **Introduced `record_token_usage(actual_tokens, reserved_tokens)`**:
   - Reconciles rolling TPM sliding window tokens when actual usage differs from conservative estimates.
4. **Refactored `record_request_completion()`**:
   - Replaced duplicate counter increments with a call to `record_token_usage()` for backward-compatible completion handling without double-counting.

```python
# app/ai/rag/embeddings/quota_manager.py (Snippet)

def sync_with_ledger(self, ledger: EmbeddingLedger, checkpoint_manager: Any = None) -> None:
    current_utc = datetime.now(timezone.utc)
    current_date_str = current_utc.strftime("%Y-%m-%d")
    tracker = ledger.request_tracker
    
    stored_date = tracker.last_request_date_utc
    if not stored_date and tracker.last_request_timestamp:
        stored_date = tracker.last_request_timestamp.strftime("%Y-%m-%d")
        
    if stored_date and current_date_str > stored_date:
        logger.info(
            f"UTC date rollover detected: stored date was {stored_date}, current date is {current_date_str}. "
            f"Resetting daily request counters from {tracker.global_api_requests} to 0."
        )
        tracker.reset_daily_counters(new_date_str=current_date_str)
        self._global_request_count = 0
        if checkpoint_manager is not None:
            checkpoint_manager.save_ledger(ledger)
    else:
        if not stored_date and tracker.global_api_requests == 0:
            tracker.last_request_date_utc = current_date_str
        self._global_request_count = tracker.global_api_requests
```

---

### 2.3. `app/ai/rag/embeddings/batch_processor.py`

#### Changes:
1. **Pass `self.checkpoint_manager` to `sync_with_ledger`**:
   Ensures automatic disk persistence upon startup if a calendar rollover occurred while the process was offline.
2. **Count Every Dispatched Invocations Before Network Boundary**:
   In `_process_batch_with_recovery()`:
   - Call `self.quota_manager.record_request_attempt(category=request_cat)` directly before `self.embedding_service.embed_batch(...)`.
   - Set flag `request_dispatched = True`.
3. **Persist Ledger on Failure**:
   Inside the `except Exception as exc:` block:
   - If `request_dispatched` is true, immediately invoke `self.checkpoint_manager.save_ledger(ledger)` before logging or retrying.
   - This guarantees that HTTP 429, HTTP 500/503, timeouts, or client network crashes leave a durable record on disk that counts against the daily limit of 200 requests.
4. **Prevent Double Counting**:
   - Following successful invocation, only token reconciliation (`record_token_usage`) is performed; the request counter is not incremented again.

---

## 3. Comprehensive Unit Testing

The test suite in `tests/test_batch_processor_and_quota.py` was extended with 11 targeted test methods covering all edge cases specified in the safety audit:

| Test Name | Verification Focus | Status |
| :--- | :--- | :--- |
| `test_daily_counter_resets_when_utc_date_advances` | Daily counters reset from 195 to 0 when UTC date moves to tomorrow; atomic persistence confirmed. | PASS |
| `test_daily_counter_does_not_reset_on_same_day_restart` | Process restarts on same UTC date preserve request count (150 remains 150). | PASS |
| `test_all_request_category_counters_reset_together` | Initial (50), retry (20), bisection (10), global (80) all reset to 0 simultaneously. | PASS |
| `test_tpm_rolling_window_valid_across_daily_rollover` | In-flight 60-second TPM reservations remain active and enforced across date rollover. | PASS |
| `test_failed_http_429_increments_request_count` | Dispatched request failing with 429 increments counter and persists to disk. | PASS |
| `test_failed_http_503_increments_request_count` | Dispatched request failing with 503 increments counter and persists to disk. | PASS |
| `test_network_failure_after_dispatch_increments_request_count` | Socket timeout after dispatch begins increments counter and persists to disk. | PASS |
| `test_successful_invocation_increments_exactly_once` | Normal 200 OK API call increments counter by exactly 1 (no double counting). | PASS |
| `test_predispatch_quota_rejection_does_not_increment_request_count` | Calls rejected *before* network transmission (due to budget limit) do not increment counter. | PASS |
| `test_daily_budget_still_blocks_requests_after_reaching_200` | Reaching 200 requests strictly halts pipeline and rejects further dispatch attempts. | PASS |
| `test_restart_reloads_persisted_request_count_correctly` | New processor instance correctly reloads count from disk ledger and enforces budget. | PASS |

### Test Suite Execution Summary
- **Module:** `tests/test_batch_processor_and_quota.py`  
  - Total tests: 24  
  - Results: **24 Passed, 0 Failed, 0 Skipped** (Execution time: ~4.66s)
- **Full Discovery Suite:** `python -m unittest discover -s tests -v`  
  - Total tests: 67  
  - Results: **67 Passed, 0 Failed, 0 Skipped** (Execution time: ~4.70s)

---

## 4. Verification of Safety Criteria

1. **Safety Quota Invariants:**
   - Free Tier RPD Ceiling: 1,000 requests/day
   - Project Safety Limit: 200 requests/day (5x safety buffer)
   - Dispatched request accounting: Every outbound HTTP request is counted and saved to disk.
   - Pre-dispatch block accounting: In-memory denials do not consume budget units.
2. **Crash Resilience:**
   - Even if the process terminates during an API error or network exception, the incremented counter is already flushed to `data/checkpoints/ledger.json`.
3. **Zero Remote/Database Activity:**
   - Mocked Gemini SDK clients were utilized for all tests.
   - Zero outbound requests to Google AI Studio.
   - Zero connections to Supabase or PostgreSQL.
   - `.env` and chunk files remain unaltered.

---

## 5. Conclusion & Next Steps

The batch embedding pipeline orchestration layer is now fully fortified with verified UTC rollover logic and resilient request accounting.

The codebase is ready for mentor review and authorization to proceed to live batch embedding execution under strict supervision.

