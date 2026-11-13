# Phase 3 — Step 6D.4: Final Static Safety Audit Report

**Project:** `Sholinganallur_RouteWise`  
**Phase & Step:** Phase 3 — Step 6D.4 (Final Static Safety Audit)  
**Author:** Senior GenAI Backend Engineer  
**Date:** 2026-11-08  
**Audit Scope:** `quota_manager.py`, `batch_processor.py`, `embedding_service.py`, `checkpoint_manager.py`, `models.py`, `generate_embeddings.py`, and test suites  
**Audit Status:** Static Inspection Complete (Actionable Corrections Recommended)  

---

## Executive Summary & Audit Scorecard

This report provides the final static safety audit of the Phase 3 Step 6D.4 implementation, evaluating system resilience, Google AI Studio Free Tier quota protections, batch response integrity, crash recovery, and CLI safety gates.

In accordance with strict audit directives:
- **Zero Gemini API calls** were made during this audit.
- **Zero embeddings** were generated.
- **Zero database operations** or table mutations were performed.
- No codebase modifications were applied prior to reporting.

### Summary Scorecard

| Audit ID | Audit Focus Area | Verdict | Summary Finding |
| :---: | :--- | :---: | :--- |
| **AUD-1** | **Daily Request Budget Persistence** | **`NEEDS CORRECTION`** | Counter survives process restarts via checkpoint ledger, but lacks an automated UTC calendar-day reset mechanism, locking the pipeline permanently once 200 requests are reached. |
| **AUD-2** | **TPM Reservation & Token Accounting** | **`NEEDS CORRECTION`** | Token reservations precede API dispatch and enforce thread-safety; however, failed API calls (429/500/400) do not record their request attempts in the global counter. |
| **AUD-3** | **Batch Response Integrity** | **`PASS`** | Exact 1:1 input-output chunk association is strictly enforced via input length equality checks, vector dimension assertions (768), and finite float validation. |
| **AUD-4** | **Crash & Resume Idempotency** | **`PASS`** | Valid orphaned vectors are adopted without API calls; atomic disk writes (`.tmp` + `os.fsync` + `os.replace`) prevent corruption; completed chunks are skipped. |
| **AUD-5** | **CLI Safety & Operator Gates** | **`PASS`** | `--dry-run` and `--status` make zero API calls; default invocation enforces a mandatory `--confirm` / `--execute` safety gate; `--limit` strictly restricts queue size. |

---

## Detailed Audit Findings

---

### Audit 1: Daily Request Budget Persistence

#### Objective:
Verify whether the 200-request daily counter survives process restarts, determines daily resets, counts retries/bisections, and prevents separate CLI executions from exceeding the quota.

#### Static Code Inspection:
1. **Persistence Mechanism:**
   - **Location:** [`app/ai/rag/embeddings/batch_processor.py#L198-L200`](file:///c:/Users/Kerthika%20M/Documents/AI%20Project/Sholinganallur_RouteWise/app/ai/rag/embeddings/batch_processor.py#L198-L200) & [`quota_manager.py#L64-L80`](file:///c:/Users/Kerthika%20M/Documents/AI%20Project/Sholinganallur_RouteWise/app/ai/rag/embeddings/quota_manager.py#L64-L80)
   - When `BatchProcessor.process_all()` starts, it initializes the checkpoint ledger:
     ```python
     ledger = self.checkpoint_manager.initialize_ledger(catalog)
     self.quota_manager.sync_with_ledger(ledger)
     ```
   - In `QuotaManager.sync_with_ledger(ledger)`:
     ```python
     self.tracker = ledger.accounting
     self.tracker.daily_budget_limit = self.daily_budget
     ```
   - When `checkpoint_manager.save_ledger(ledger)` commits state, `ledger.accounting` is serialized into `data/embeddings/checkpoints/batch_checkpoint.json`.
   - **Verdict on Persistence:** **PASS**. The counter survives process termination and reloads on subsequent runs.

2. **Inter-Process Headroom Protection:**
   - If Execution A makes 50 requests and terminates, `global_api_requests` is saved as 50.
   - When Execution B starts, `sync_with_ledger` loads 50. It will halt when `global_api_requests` reaches 200.
   - **Verdict on Multi-Process Safety:** **PASS**. Separate CLI executions on the same day cannot exceed the 200-request safety ceiling.

3. **Daily Reset Determination:**
   - **Location:** [`app/ai/rag/embeddings/quota_manager.py#L64-L80`](file:///c:/Users/Kerthika%20M/Documents/AI%20Project/Sholinganallur_RouteWise/app/ai/rag/embeddings/quota_manager.py#L64-L80) & [`app/ai/rag/embeddings/models.py#L220-L283`](file:///c:/Users/Kerthika%20M/Documents/AI%20Project/Sholinganallur_RouteWise/app/ai/rag/embeddings/models.py#L220-L283)
   - **Flaw Detected:** While `APIRequestTracker` contains `last_request_timestamp_utc`, neither `sync_with_ledger()` nor `APIRequestTracker` checks whether `current_utc_date > last_request_utc_date`.
   - **Impact:** If a job reaches 200 requests on Day 1, resuming on Day 2 still reads `global_api_requests = 200`, causing `reserve_budget()` to immediately raise `QuotaExceededError` forever unless the file is manually modified.
   - **Verdict on Daily Reset:** **NEEDS CORRECTION**.

---

### Audit 2: TPM Reservation & Token Accounting

#### Objective:
Verify that token reservations precede actual API calls, retries/bisections require fresh reservations, concurrent workers cannot bypass limits, actual usage reconciles with estimates, and unknown tokens are never treated as zero.

#### Static Code Inspection:
1. **Pre-Invocation Token Reservation:**
   - **Location:** [`app/ai/rag/embeddings/batch_processor.py#L295-L299`](file:///c:/Users/Kerthika%20M/Documents/AI%20Project/Sholinganallur_RouteWise/app/ai/rag/embeddings/batch_processor.py#L295-L299)
   - `self.quota_manager.reserve_budget(estimated_tokens, call_type, wait_if_needed=True)` is called directly before `self.embedding_service.embed_batch(batch_chunks)`.
   - Tokens are calculated with safety multiplier ($k = 1.25$):
     `budgeted_tokens = max(1, int(base_tokens * self.token_safety_multiplier))` ([`quota_manager.py#L130`](file:///c:/Users/Kerthika%20M/Documents/AI%20Project/Sholinganallur_RouteWise/app/ai/rag/embeddings/quota_manager.py#L130)).
   - Thread safety: Protected by `threading.Lock` ([`quota_manager.py#L59`](file:///c:/Users/Kerthika%20M/Documents/AI%20Project/Sholinganallur_RouteWise/app/ai/rag/embeddings/quota_manager.py#L59)).
   - Non-zero guarantee: `max(1, ...)` ensures token count is strictly $\ge 1$.
   - **Verdict on Reservation:** **PASS**.

2. **Failed Request Accounting ("Retries Are Not Free"):**
   - **Location:** [`app/ai/rag/embeddings/batch_processor.py#L305-L332`](file:///c:/Users/Kerthika%20M/Documents/AI%20Project/Sholinganallur_RouteWise/app/ai/rag/embeddings/batch_processor.py#L305-L332)
   - **Flaw Detected:** `self.quota_manager.record_request_completion()` is called on line 310, inside the `try` block *after* `embed_batch()` succeeds.
   - If `embed_batch()` raises `RateLimitError` (HTTP 429), `TransientServerError` (HTTP 503), or `InvalidRequestError` (HTTP 400), execution jumps to the `except` blocks (lines 332 & 361).
   - `record_request_completion()` is **never reached for failed attempts**.
   - **Impact:** An HTTP request was dispatched over the network and consumed Google AI Studio quota, but RouteWise's local counter did not increment. If an operation retries 3 times before succeeding, 4 network calls occurred, but `global_api_requests` increments by only 1.
   - **Verdict on Failed Request Accounting:** **NEEDS CORRECTION**.

---

### Audit 3: Batch Response Integrity

#### Objective:
Verify returned embedding count equals submitted chunk count, embedding order is preserved, each vector maps to its source chunk, and malformed responses cannot generate corrupt records.

#### Static Code Inspection:
- **Location:** [`app/ai/rag/embeddings/embedding_service.py#L256-L283`](file:///c:/Users/Kerthika%20M/Documents/AI%20Project/Sholinganallur_RouteWise/app/ai/rag/embeddings/embedding_service.py#L256-L283)
1. **Length Equality Check:**
   ```python
   if len(response.embeddings) != len(chunks):
       raise InvalidRequestError(
           f"Gemini returned {len(response.embeddings)} embeddings for {len(chunks)} input chunks."
       )
   ```
   If the API omits any vector or returns a mismatched count, execution aborts immediately; no partial files are created.
2. **Deterministic 1:1 Mapping:**
   ```python
   for idx, (c, emb_obj) in enumerate(zip(chunks, response.embeddings)):
       chunk_id = c["chunk_id"]
       vector = list(emb_obj.values)
       l2_norm = self._validate_vector_elements(vector, chunk_id)
   ```
   Google's `batchEmbedContents` returns vectors in exact sequence matching the input `contents` list. The pairwise `zip` assigns each vector directly to its source chunk dictionary.
3. **Rigorous Structural Assertions:**
   `_validate_vector_elements()` asserts:
   - `len(vector) == 768` (pgvector column requirement).
   - `all(math.isfinite(x) for x in vector)` (rejects `NaN` and `±Inf`).
   - `l2_norm > 0.0`.
4. **Verdict:** **`PASS`**.

---

### Audit 4: Crash and Resume Idempotency

#### Objective:
Verify valid existing vectors are adopted without API calls, checkpoint recovery is safe after interruption, completed chunks are not re-embedded, and corrupted vectors are rejected.

#### Static Code Inspection:
- **Location:** [`app/ai/rag/embeddings/checkpoint_manager.py#L182-L280`](file:///c:/Users/Kerthika%20M/Documents/AI%20Project/Sholinganallur_RouteWise/app/ai/rag/embeddings/checkpoint_manager.py#L182-L280)
1. **Pre-Flight Orphan Adoption:**
   `recover_orphaned_vectors()` inspects `data/embeddings/vectors/*.json`. If a vector passes `validate_vector_file()` (768 dimensions, finite floats, matching SHA-256 chunk hash), it is adopted as `COMPLETED` into the checkpoint ledger with **zero remote API calls**.
2. **Corrupted Vector Rejection:**
   If a vector file contains malformed JSON or hash mismatch, `recover_orphaned_vectors()` unlinks the corrupted file and resets the chunk status to `PENDING`.
3. **Atomic Persistence:**
   - Vector files: `_save_vector_atomically()` writes to `.tmp`, flushes buffers, calls `os.fsync()`, and atomically renames via `os.replace()`.
   - Checkpoint ledger: `save_ledger()` follows the identical atomic staging pattern.
   - Completed status invariant: A chunk is never marked `COMPLETED` in the ledger unless its vector file is validated on disk first ([`checkpoint_manager.py#L320-L335`](file:///c:/Users/Kerthika%20M/Documents/AI%20Project/Sholinganallur_RouteWise/app/ai/rag/embeddings/checkpoint_manager.py#L320-L335)).
4. **Verdict:** **`PASS`**.

---

### Audit 5: CLI Safety & Operator Gates

#### Objective:
Verify `--dry-run` and `--status` make zero API calls, `--limit N` restricts queue size, live execution requires confirmation, and quota errors stop execution cleanly.

#### Static Code Inspection:
- **Location:** [`scripts/generate_embeddings.py#L55-L105`](file:///c:/Users/Kerthika%20M/Documents/AI%20Project/Sholinganallur_RouteWise/scripts/generate_embeddings.py#L55-L105)
1. **`--status` Safety:**
   Lines 56–71 invoke `processor.plan_execution()`, print formatted table, and exit with code 0. Zero API calls executed.
2. **`--dry-run` Safety:**
   Lines 73–88 invoke `processor.process_all(dry_run=True)`. In `batch_processor.py#L191`:
   ```python
   if dry_run:
       return self.plan_execution(limit=limit)
   ```
   Zero remote API calls dispatched.
3. **Mandatory Confirmation Gate:**
   Lines 91–103:
   ```python
   if not args.confirm:
       logger.warning("[SAFETY GATE] Real embedding execution requested, but neither --confirm nor --execute was supplied.")
       print("SAFETY NOTICE: Real Gemini API embedding calls require confirmation...")
       return 1
   ```
   Running `python scripts/generate_embeddings.py` without `--confirm` or `--execute` halts immediately with code 1.
4. **`--limit` Enforcement:**
   Line 207 of `batch_processor.py`:
   `queue = queue[:limit]`
   Slices the queue before partitioning into batches.
5. **Clean Quota Exit:**
   Line 244 of `batch_processor.py`:
   `except QuotaExceededError: job_interrupted_budget = True; break`
   Gracefully terminates batch dispatch, generates manifest for completed chunks, saves ledger, and returns `status="BUDGET_REACHED"`.
6. **Verdict:** **`PASS`**.

---

## Recommended Minimal Corrections

Prior to running any production embeddings, the following two minimal, non-disruptive corrections are recommended:

### Correction 1: Automatic UTC Calendar-Day Reset for Daily Budget
**File:** `app/ai/rag/embeddings/quota_manager.py` (in `sync_with_ledger`)  
**Proposed Change:**
When synchronizing with the ledger, compare the current UTC date with the date of `last_request_timestamp_utc`. If a new day has arrived, reset the daily request counters to 0 while preserving completed chunk vectors:

```python
def sync_with_ledger(self, ledger: BatchCheckpointLedger) -> None:
    with self._lock:
        self.tracker = ledger.accounting
        self.tracker.daily_budget_limit = self.daily_budget
        
        # Check for calendar day rollover in UTC
        now_utc = datetime.now(timezone.utc)
        if self.tracker.last_request_timestamp_utc is not None:
            if now_utc.date() > self.tracker.last_request_timestamp_utc.date():
                logger.info(
                    "New calendar day detected (UTC: %s > %s). Resetting daily request budget to 0/%d.",
                    now_utc.date(),
                    self.tracker.last_request_timestamp_utc.date(),
                    self.daily_budget,
                )
                self.tracker.global_api_requests = 0
                self.tracker.initial_requests = 0
                self.tracker.retry_requests = 0
                self.tracker.bisection_requests = 0
```

### Correction 2: Record Dispatched Requests Regardless of API Outcome
**File:** `app/ai/rag/embeddings/batch_processor.py` (in `_process_batch_with_recovery`)  
**Proposed Change:**
Increment the global request counter immediately when an attempt is dispatched or inside a `finally`/error block, ensuring failed HTTP calls count against the 200 daily request ceiling:

```python
# Record request completion in quota ledger regardless of success or failure
self.quota_manager.record_request_completion(
    actual_tokens=0,
    estimated_tokens=estimated_tokens,
    call_type=call_type if attempt == 0 else "retry",
)
```

---

## Static Audit Deliverable Declaration

- **Report Path:** `data/reports/step_6d_4_final_safety_audit.md`
- **Real Gemini API Calls Made:** `0`
- **Real Embeddings Generated:** `0`
- **Database Operations:** `0`
- **Filesystem Integrity:** `.env`, `data/chunks/`, `data/documents/`, and manifests remain completely unmodified.
