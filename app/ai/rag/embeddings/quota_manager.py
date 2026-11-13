"""
Module: app.ai.rag.embeddings.quota_manager
Purpose: Thread-safe quota manager, rate limiter, and token pacing controller for the RouteWise
         embedding pipeline. Enforces Google AI Studio Free Tier constraints:
         - Daily Request Budget: 200 requests/day (circuit breaker well below 1,000 RPD)
         - Rolling Token Ceiling: 24,000 TPM (20% safety margin below 30,000 TPM quota)
         - Conservative Inter-Request Pacing: 30s delay + jitter (~2 RPM vs 100 RPM ceiling)
         - Strict monotonic accounting: Counts initial, retry, and bisection calls.
"""

from __future__ import annotations

import collections
import logging
import random
import threading
import time
from datetime import datetime, timezone
from typing import Deque, Optional, Tuple

from app.core.config import settings
from app.ai.rag.embeddings.embedding_service import EmbeddingServiceError
from app.ai.rag.embeddings.models import APIRequestTracker, BatchCheckpointLedger

logger = logging.getLogger(__name__)


class QuotaExceededError(EmbeddingServiceError):
    """Raised when the daily API request safety budget has been reached."""
    pass


class TPMLimitExceededError(EmbeddingServiceError):
    """Raised when the rolling token rate limit would be breached and waiting is disabled."""
    pass


class QuotaManager:
    """
    Thread-safe orchestrator for API request accounting, pacing delays, and token budgeting.
    Enforces the invariant that 'retries and bisections are never free'.
    """

    def __init__(
        self,
        daily_budget: Optional[int] = None,
        tpm_safety_limit: Optional[int] = None,
        pacing_delay_seconds: float = 30.0,
        token_safety_multiplier: float = 1.25,
        tracker: Optional[APIRequestTracker] = None,
    ):
        self.daily_budget = daily_budget if daily_budget is not None else settings.MAX_DAILY_REQUEST_BUDGET
        self.tpm_safety_limit = tpm_safety_limit if tpm_safety_limit is not None else settings.MAX_TPM_SAFETY_LIMIT
        self.pacing_delay_seconds = pacing_delay_seconds
        self.token_safety_multiplier = token_safety_multiplier

        self.tracker = tracker or APIRequestTracker(daily_budget_limit=self.daily_budget)

        self._lock = threading.Lock()
        # Deque of (timestamp_epoch: float, tokens_consumed: int) within rolling 60s
        self._token_history: Deque[Tuple[float, int]] = collections.deque()
        self._last_request_time: float = 0.0

    def sync_with_ledger(
        self,
        ledger: BatchCheckpointLedger,
        checkpoint_manager: Optional[Any] = None,
    ) -> bool:
        """
        Synchronizes internal request tracker with an existing checkpoint ledger.
        Preserves past request counts on same-day job resumption.
        Automatically resets daily request counters when UTC date advances.
        Returns True if a daily rollover reset occurred, False otherwise.
        """
        with self._lock:
            self.tracker = ledger.accounting
            # Ensure daily budget in tracker matches quota manager
            self.tracker.daily_budget_limit = self.daily_budget

            now_utc = datetime.now(timezone.utc)
            current_date_str = now_utc.strftime("%Y-%m-%d")

            # Determine stored date from last_request_date_utc or fallback to last_request_timestamp_utc
            stored_date = self.tracker.last_request_date_utc
            if not stored_date and self.tracker.last_request_timestamp_utc is not None:
                stored_date = self.tracker.last_request_timestamp_utc.strftime("%Y-%m-%d")

            reset_occurred = False
            if stored_date is not None and stored_date != current_date_str:
                if current_date_str > stored_date:
                    logger.info(
                        "New calendar day detected in UTC (%s > %s). Resetting daily API request counter to 0/%d.",
                        current_date_str,
                        stored_date,
                        self.daily_budget,
                    )
                    self.tracker.reset_daily_counters(new_date_str=current_date_str)
                    reset_occurred = True
            elif stored_date is None and self.tracker.global_api_requests == 0:
                self.tracker.last_request_date_utc = current_date_str

            logger.info(
                "QuotaManager synchronized with ledger: %d/%d requests used today (%s) "
                "(initial: %d, retry: %d, bisection: %d).",
                self.tracker.global_api_requests,
                self.daily_budget,
                self.tracker.last_request_date_utc or current_date_str,
                self.tracker.initial_requests,
                self.tracker.retry_requests,
                self.tracker.bisection_requests,
            )

        if reset_occurred and checkpoint_manager is not None:
            checkpoint_manager.save_ledger(ledger)
            logger.info("Persisted atomic checkpoint ledger following daily rollover reset.")

        return reset_occurred

    def can_make_request(self) -> bool:
        """Checks if daily request safety budget allows another request."""
        with self._lock:
            return self.tracker.global_api_requests < self.daily_budget

    def get_remaining_daily_requests(self) -> int:
        """Returns the number of requests remaining in the daily budget."""
        with self._lock:
            return max(0, self.daily_budget - self.tracker.global_api_requests)

    def get_current_rolling_tpm(self, current_time: Optional[float] = None) -> int:
        """Computes total tokens consumed within the rolling 60-second window."""
        now = current_time or time.time()
        with self._lock:
            self._prune_expired_token_history(now)
            return sum(tokens for _, tokens in self._token_history)

    def reserve_budget(
        self,
        estimated_tokens: int,
        call_type: str = "initial",
        wait_if_needed: bool = True,
    ) -> int:
        """
        Reserves quota and enforces pacing before dispatching a remote API call.

        Args:
            estimated_tokens: Estimated tokens for the payload (never zero).
            call_type: Type of invocation ('initial', 'retry', or 'bisection').
            wait_if_needed: Whether to pause thread for TPM/RPM pacing or raise immediately.

        Returns:
            int: Budgeted token reservation amount (with safety multiplier).

        Raises:
            QuotaExceededError: When daily budget limit is reached.
            TPMLimitExceededError: When rolling TPM ceiling would be exceeded and wait_if_needed=False.
        """
        with self._lock:
            # 1. Hard Daily Request Budget Circuit Breaker
            if self.tracker.global_api_requests >= self.daily_budget:
                raise QuotaExceededError(
                    f"Daily API request safety budget reached: {self.tracker.global_api_requests}/{self.daily_budget}. "
                    "Halting to protect Google AI Studio Free Tier RPD quota. Resume safely tomorrow."
                )

            # 2. Token Budgeting with Safety Multiplier (never zero)
            base_tokens = max(1, estimated_tokens)
            budgeted_tokens = max(1, int(base_tokens * self.token_safety_multiplier))

        # 3. Rolling TPM Rate Limiting
        while True:
            now = time.time()
            with self._lock:
                self._prune_expired_token_history(now)
                current_rolling_tpm = sum(t for _, t in self._token_history)

                if current_rolling_tpm + budgeted_tokens <= self.tpm_safety_limit:
                    break  # Headroom available

                if not wait_if_needed:
                    raise TPMLimitExceededError(
                        f"TPM limit exceeded: current {current_rolling_tpm} + payload {budgeted_tokens} > "
                        f"limit {self.tpm_safety_limit}."
                    )

                # Calculate sleep duration until the oldest item expires
                if self._token_history:
                    oldest_time, _ = self._token_history[0]
                    sleep_needed = max(0.5, 60.0 - (now - oldest_time) + 0.1)
                else:
                    sleep_needed = 2.0

            logger.warning(
                "Rolling TPM ceiling reached (%d/%d tokens). Throttling for %.2f seconds...",
                current_rolling_tpm + budgeted_tokens,
                self.tpm_safety_limit,
                sleep_needed,
            )
            time.sleep(sleep_needed)

        # 4. Conservative Inter-Request RPM Pacing
        if self.pacing_delay_seconds > 0.0:
            with self._lock:
                elapsed = time.time() - self._last_request_time
                target_delay = self.pacing_delay_seconds + random.uniform(0.1, 0.5)
                wait_time = target_delay - elapsed

            if wait_time > 0.0:
                if wait_if_needed:
                    logger.info("Enforcing RPM pacing delay: waiting %.2f seconds...", wait_time)
                    time.sleep(wait_time)
                else:
                    logger.debug("Skipping sleep in non-waiting mode.")

        return budgeted_tokens

    def record_request_attempt(self, call_type: str = "initial") -> None:
        """
        Atomically records that an API request has been dispatched over the network.
        Consumes exactly 1 daily request budget unit, regardless of subsequent API success or failure.
        """
        now = time.time()
        with self._lock:
            self.tracker.record_request(call_type=call_type)
            self._last_request_time = now
            logger.debug(
                "API request dispatched (%s). Daily budget consumed: %d/%d.",
                call_type,
                self.tracker.global_api_requests,
                self.daily_budget,
            )

    def record_token_usage(
        self,
        actual_tokens: Optional[int] = None,
        estimated_tokens: int = 0,
    ) -> None:
        """
        Records token consumption in the rolling 60-second TPM sliding window.
        Reconciles with authoritative API token count when available.
        """
        now = time.time()
        tokens_to_record = actual_tokens if actual_tokens is not None and actual_tokens > 0 else max(1, estimated_tokens)
        with self._lock:
            self._token_history.append((now, tokens_to_record))
            self._last_request_time = now
            self._prune_expired_token_history(now)
            logger.debug(
                "Token usage recorded: %d tokens. Current rolling TPM: %d.",
                tokens_to_record,
                sum(t for _, t in self._token_history),
            )

    def record_request_completion(
        self,
        actual_tokens: Optional[int] = None,
        estimated_tokens: int = 0,
        call_type: str = "initial",
        already_counted: bool = False,
    ) -> None:
        """
        Records completion of an API request.
        If already_counted is False, records both request attempt and token usage.
        If already_counted is True, records only token usage.
        """
        if not already_counted:
            self.record_request_attempt(call_type=call_type)
        self.record_token_usage(actual_tokens=actual_tokens, estimated_tokens=estimated_tokens)

    def _prune_expired_token_history(self, current_time: float) -> None:
        """Prunes token events older than 60 seconds from the sliding window."""
        cutoff = current_time - 60.0
        while self._token_history and self._token_history[0][0] < cutoff:
            self._token_history.popleft()

