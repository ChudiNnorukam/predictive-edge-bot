"""
Circuit Breakers Module
=======================

Per-market circuit breaker pattern for handling transient failures.
Implements state machine: CLOSED -> OPEN -> HALF_OPEN -> CLOSED

Features:
- Automatic trip on consecutive failures
- Auto-recovery with configurable timeout
- Half-open state for gradual recovery testing
- Per-market isolation (failure in one market doesn't affect others)

All operations are thread-safe with asyncio.Lock.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    """Circuit breaker state enumeration."""
    CLOSED = "closed"       # Normal operation, requests allowed
    OPEN = "open"           # Tripped, blocking requests
    HALF_OPEN = "half_open" # Testing recovery, limited requests allowed


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker behavior."""
    failure_threshold: int = 3              # Trips after N consecutive failures
    recovery_timeout_seconds: int = 60      # Time before auto-reset from OPEN
    half_open_max_requests: int = 1         # Max requests allowed in HALF_OPEN state


class CircuitBreaker:
    """
    Per-market circuit breaker using state machine pattern.

    Transitions:
        CLOSED -> OPEN: N consecutive failures
        OPEN -> HALF_OPEN: recovery_timeout expires
        HALF_OPEN -> CLOSED: success
        HALF_OPEN -> OPEN: failure in half-open state

    Example:
        config = CircuitBreakerConfig(failure_threshold=3, recovery_timeout_seconds=60)
        breaker = CircuitBreaker("market_123", config)

        if breaker.can_execute():
            try:
                result = await execute_trade()
                breaker.record_success()
            except Exception as e:
                breaker.record_failure(str(e))
    """

    def __init__(self, market_id: str, config: CircuitBreakerConfig):
        """
        Initialize circuit breaker for a market.

        Args:
            market_id: Unique identifier for the market
            config: CircuitBreakerConfig with behavior settings
        """
        self.market_id = market_id
        self.config = config
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time: Optional[datetime] = None
        self.half_open_requests = 0
        self._lock = asyncio.Lock()

    def can_execute(self) -> bool:
        """
        Check if execution is allowed based on circuit state.

        Returns:
            True if execution allowed, False if circuit OPEN
        """
        # Check if should transition OPEN -> HALF_OPEN
        if self.state == CircuitState.OPEN:
            self._check_recovery()

        if self.state == CircuitState.CLOSED:
            return True
        elif self.state == CircuitState.HALF_OPEN:
            # Allow limited requests in half-open state
            return self.half_open_requests < self.config.half_open_max_requests
        else:  # OPEN
            return False

    def record_success(self) -> None:
        """
        Record a successful execution.

        Resets failure counter and transitions HALF_OPEN -> CLOSED.
        """
        if self.state == CircuitState.CLOSED:
            # Already healthy
            self.failure_count = 0
            return

        if self.state == CircuitState.HALF_OPEN:
            # Recovered!
            logger.info(f"Circuit breaker {self.market_id}: HALF_OPEN -> CLOSED (recovered)")
            self.state = CircuitState.CLOSED
            self.failure_count = 0
            self.half_open_requests = 0
            return

    def record_failure(self, reason: str) -> None:
        """
        Record a failed execution.

        Increments failure counter and trips to OPEN if threshold exceeded.

        Args:
            reason: Description of the failure
        """
        self.last_failure_time = datetime.now(timezone.utc)
        self.failure_count += 1

        if self.state == CircuitState.HALF_OPEN:
            # Failed while recovering
            logger.warning(f"Circuit breaker {self.market_id}: HALF_OPEN failure, reopening: {reason}")
            self.state = CircuitState.OPEN
            self.failure_count = self.config.failure_threshold  # Reset counter to trip
            return

        # Check if should trip to OPEN
        if self.failure_count >= self.config.failure_threshold:
            if self.state != CircuitState.OPEN:
                logger.error(
                    f"Circuit breaker {self.market_id}: CLOSED -> OPEN "
                    f"({self.failure_count} failures): {reason}"
                )
                self.state = CircuitState.OPEN

    def _check_recovery(self) -> None:
        """
        Check if recovery timeout has elapsed.

        Transitions OPEN -> HALF_OPEN if timeout expired.
        """
        if self.state != CircuitState.OPEN or self.last_failure_time is None:
            return

        elapsed = (datetime.now(timezone.utc) - self.last_failure_time).total_seconds()
        if elapsed >= self.config.recovery_timeout_seconds:
            logger.info(
                f"Circuit breaker {self.market_id}: OPEN -> HALF_OPEN "
                f"(recovery timeout {elapsed:.0f}s)"
            )
            self.state = CircuitState.HALF_OPEN
            self.failure_count = 0
            self.half_open_requests = 0

    def get_status(self) -> Dict[str, any]:
        """
        Get current state and metrics.

        Returns:
            Dictionary with circuit breaker status
        """
        return {
            "market_id": self.market_id,
            "state": self.state.value,
            "failure_count": self.failure_count,
            "last_failure": self.last_failure_time.isoformat() if self.last_failure_time else None,
            "half_open_requests": self.half_open_requests if self.state == CircuitState.HALF_OPEN else 0,
        }


class CircuitBreakerRegistry:
    """
    Registry managing circuit breakers for all markets.

    Creates breakers on-demand and provides centralized access.

    Example:
        config = CircuitBreakerConfig(failure_threshold=3)
        registry = CircuitBreakerRegistry(config)

        can_trade = await registry.can_execute("market_123")
        await registry.record_success("market_123")
        open_markets = await registry.get_open_breakers()
    """

    def __init__(self, config: CircuitBreakerConfig):
        """
        Initialize circuit breaker registry.

        Args:
            config: CircuitBreakerConfig shared by all breakers
        """
        self.config = config
        self._breakers: Dict[str, CircuitBreaker] = {}
        self._lock = asyncio.Lock()

    async def get_or_create(self, market_id: str) -> CircuitBreaker:
        """
        Get existing breaker or create new one.

        Args:
            market_id: Market identifier

        Returns:
            CircuitBreaker instance for the market
        """
        async with self._lock:
            if market_id not in self._breakers:
                self._breakers[market_id] = CircuitBreaker(market_id, self.config)
            return self._breakers[market_id]

    async def can_execute(self, market_id: str) -> bool:
        """
        Check if execution is allowed for a market.

        Args:
            market_id: Market identifier

        Returns:
            True if execution allowed, False if circuit open
        """
        breaker = await self.get_or_create(market_id)
        return breaker.can_execute()

    async def record_success(self, market_id: str) -> None:
        """
        Record successful execution for a market.

        Args:
            market_id: Market identifier
        """
        breaker = await self.get_or_create(market_id)
        breaker.record_success()

    async def record_failure(self, market_id: str, reason: str) -> None:
        """
        Record failed execution for a market.

        Args:
            market_id: Market identifier
            reason: Failure description
        """
        breaker = await self.get_or_create(market_id)
        breaker.record_failure(reason)

    async def get_open_breakers(self) -> list[str]:
        """
        List all markets with open circuit breakers.

        Returns:
            List of market IDs with OPEN breakers
        """
        async with self._lock:
            return [
                market_id
                for market_id, breaker in self._breakers.items()
                if breaker.state == CircuitState.OPEN
            ]

    async def get_status(self) -> Dict[str, any]:
        """
        Get status of all breakers.

        Returns:
            Dictionary mapping market_id to breaker status
        """
        async with self._lock:
            return {
                market_id: breaker.get_status()
                for market_id, breaker in self._breakers.items()
            }

    async def get_breaker_count(self) -> int:
        """
        Get total number of managed breakers.

        Returns:
            Count of breakers in registry
        """
        async with self._lock:
            return len(self._breakers)
