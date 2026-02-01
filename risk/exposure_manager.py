"""
Exposure Manager Module
=======================

Capital exposure tracking and limits across all markets.
Enforces per-market and portfolio-level exposure constraints.

Features:
- Per-market exposure limits (5% of bankroll by default)
- Total portfolio exposure limit (30% of bankroll by default)
- Absolute per-market caps ($50 by default)
- Dynamic allocation based on available capital
- Bankroll tracking and P&L recording

All operations are thread-safe with asyncio.Lock.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Dict, Tuple, Optional

logger = logging.getLogger(__name__)


@dataclass
class ExposureConfig:
    """Configuration for exposure limits."""
    max_exposure_per_market_percent: float = 5.0   # Max 5% bankroll per market
    max_total_exposure_percent: float = 30.0       # Max 30% total exposure
    max_exposure_per_market_absolute: float = 50.0 # Absolute cap $50/market


class ExposureManager:
    """
    Tracks and limits capital exposure across all markets.

    Ensures compliance with exposure constraints before allowing allocations.
    Supports dynamic capital tracking and P&L updates.

    Constraints enforced:
        1. Per-market: min(5% bankroll, $50 absolute)
        2. Total: 30% of bankroll
        3. Total available: bankroll - total_exposure

    Example:
        config = ExposureConfig(
            max_exposure_per_market_percent=5.0,
            max_total_exposure_percent=30.0,
            max_exposure_per_market_absolute=50.0
        )
        manager = ExposureManager(config, initial_bankroll=10000.0)

        # Check allocation
        can_allocate, reason = await manager.can_allocate("market_123", 100.0)
        if can_allocate:
            await manager.allocate("market_123", 100.0)

        # Record P&L
        await manager.record_pnl("market_123", pnl=15.5)
        await manager.update_bankroll(new_bankroll)
    """

    def __init__(self, config: ExposureConfig, initial_bankroll: float):
        """
        Initialize exposure manager.

        Args:
            config: ExposureConfig with limit settings
            initial_bankroll: Starting capital in USDC
        """
        if initial_bankroll <= 0:
            raise ValueError(f"Initial bankroll must be positive, got {initial_bankroll}")

        self.config = config
        self.bankroll = initial_bankroll
        self._exposures: Dict[str, float] = {}  # market_id -> current exposure
        self._lock = asyncio.Lock()

    async def get_market_exposure(self, market_id: str) -> float:
        """
        Get current exposure for a market.

        Args:
            market_id: Market identifier

        Returns:
            Current exposure amount in USDC
        """
        async with self._lock:
            return self._exposures.get(market_id, 0.0)

    async def get_total_exposure(self) -> float:
        """
        Get sum of all market exposures.

        Returns:
            Total exposure across all markets
        """
        async with self._lock:
            return sum(self._exposures.values())

    async def get_available_capital(self) -> float:
        """
        Get unallocated capital.

        Returns:
            bankroll minus total_exposure
        """
        async with self._lock:
            total_exposure = sum(self._exposures.values())
            return max(0.0, self.bankroll - total_exposure)

    async def can_allocate(self, market_id: str, amount: float) -> Tuple[bool, str]:
        """
        Check if allocation is allowed without exceeding limits.

        Returns detailed reason if allocation is blocked.

        Args:
            market_id: Market identifier
            amount: Proposed allocation amount

        Returns:
            Tuple of (allowed: bool, reason: str)
        """
        if amount <= 0:
            return False, f"Amount must be positive, got {amount}"

        async with self._lock:
            # Get current state
            current_exposure = self._exposures.get(market_id, 0.0)
            new_market_exposure = current_exposure + amount
            total_exposure = sum(self._exposures.values())
            new_total_exposure = total_exposure + amount

            # Check per-market absolute limit
            max_per_market_absolute = self.config.max_exposure_per_market_absolute
            if new_market_exposure > max_per_market_absolute:
                return (
                    False,
                    f"Market exposure {new_market_exposure:.2f} > absolute cap {max_per_market_absolute:.2f}",
                )

            # Check per-market percentage limit
            max_per_market_pct = self.config.max_exposure_per_market_percent / 100.0
            max_per_market = self.bankroll * max_per_market_pct
            if new_market_exposure > max_per_market:
                return (
                    False,
                    f"Market exposure {new_market_exposure:.2f} > {max_per_market_pct*100:.1f}% of bankroll ({max_per_market:.2f})",
                )

            # Check total exposure limit
            max_total_pct = self.config.max_total_exposure_percent / 100.0
            max_total = self.bankroll * max_total_pct
            if new_total_exposure > max_total:
                return (
                    False,
                    f"Total exposure {new_total_exposure:.2f} > {max_total_pct*100:.1f}% of bankroll ({max_total:.2f})",
                )

            # Check available capital
            available = max(0.0, self.bankroll - total_exposure)
            if amount > available:
                return (
                    False,
                    f"Amount {amount:.2f} > available capital {available:.2f}",
                )

        return True, "OK"

    async def allocate(self, market_id: str, amount: float) -> bool:
        """
        Allocate capital to a market.

        First validates with can_allocate(), then records exposure.

        Args:
            market_id: Market identifier
            amount: Allocation amount

        Returns:
            True if allocation succeeded, False if would exceed limits
        """
        can_allocate, reason = await self.can_allocate(market_id, amount)
        if not can_allocate:
            logger.warning(f"Allocation blocked for {market_id}: {reason}")
            return False

        async with self._lock:
            current = self._exposures.get(market_id, 0.0)
            self._exposures[market_id] = current + amount
            logger.debug(f"Allocated {amount:.2f} to {market_id} (new exposure: {self._exposures[market_id]:.2f})")

        return True

    async def release(self, market_id: str, amount: Optional[float] = None) -> float:
        """
        Release exposure from a market.

        Args:
            market_id: Market identifier
            amount: Amount to release (None = release all for this market)

        Returns:
            Amount actually released
        """
        async with self._lock:
            current = self._exposures.get(market_id, 0.0)

            if amount is None:
                # Release all
                released = current
                if market_id in self._exposures:
                    del self._exposures[market_id]
            else:
                # Release partial
                released = min(amount, current)
                if released > 0:
                    self._exposures[market_id] = current - released
                    if self._exposures[market_id] <= 0:
                        del self._exposures[market_id]

            logger.debug(f"Released {released:.2f} from {market_id}")
            return released

    def calculate_max_allocation(self, market_id: str) -> float:
        """
        Calculate maximum allowed allocation for a market.

        Considers all active limits and current state.
        NOTE: This is a snapshot calculation - actual allocation may differ
        if other markets change exposure concurrently.

        Args:
            market_id: Market identifier

        Returns:
            Maximum allowable allocation in USDC
        """
        current_exposure = self._exposures.get(market_id, 0.0)
        total_exposure = sum(self._exposures.values())

        # Per-market percentage limit
        max_per_market_pct = self.config.max_exposure_per_market_percent / 100.0
        max_per_market = self.bankroll * max_per_market_pct

        # Per-market absolute limit
        max_per_market_absolute = self.config.max_exposure_per_market_absolute

        # Total portfolio limit
        max_total_pct = self.config.max_total_exposure_percent / 100.0
        max_total = self.bankroll * max_total_pct

        # Available capital
        available_capital = max(0.0, self.bankroll - total_exposure)

        # Take minimum of all constraints
        per_market_limit = max(0.0, min(max_per_market, max_per_market_absolute) - current_exposure)
        total_limit = max(0.0, max_total - total_exposure)
        capital_limit = available_capital

        max_allocation = min(per_market_limit, total_limit, capital_limit)
        return max(0.0, max_allocation)

    async def update_bankroll(self, new_bankroll: float) -> None:
        """
        Update bankroll (e.g., after deposit or withdrawal).

        Args:
            new_bankroll: New bankroll amount

        Raises:
            ValueError if new_bankroll is non-positive
        """
        if new_bankroll <= 0:
            raise ValueError(f"Bankroll must be positive, got {new_bankroll}")

        async with self._lock:
            delta = new_bankroll - self.bankroll
            self.bankroll = new_bankroll
            logger.info(f"Bankroll updated: {self.bankroll:.2f} (change: {delta:+.2f})")

    async def record_pnl(self, market_id: str, pnl: float) -> None:
        """
        Record P&L change for a market and update bankroll.

        Args:
            market_id: Market identifier
            pnl: Profit/loss amount (positive or negative)
        """
        async with self._lock:
            self.bankroll += pnl
            logger.debug(f"Market {market_id} P&L: {pnl:+.2f} -> bankroll: {self.bankroll:.2f}")

    def get_exposure_report(self) -> Dict[str, any]:
        """
        Get comprehensive exposure state for logging/monitoring.

        Returns:
            Dictionary with all exposure metrics
        """
        total_exposure = sum(self._exposures.values())
        available = max(0.0, self.bankroll - total_exposure)

        return {
            "bankroll": self.bankroll,
            "total_exposure": total_exposure,
            "available_capital": available,
            "exposure_percent": (total_exposure / self.bankroll * 100) if self.bankroll > 0 else 0,
            "market_exposures": self._exposures.copy(),
            "limits": {
                "per_market_percent": self.config.max_exposure_per_market_percent,
                "per_market_absolute": self.config.max_exposure_per_market_absolute,
                "total_percent": self.config.max_total_exposure_percent,
            },
        }
