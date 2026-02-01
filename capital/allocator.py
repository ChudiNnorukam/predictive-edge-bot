"""
Capital Allocator
=================

Manages capital allocation across markets with per-market and total limits.
Thread-safe async implementation with comprehensive state tracking.

Features:
- Per-market exposure limits (percent and absolute)
- Total portfolio exposure limits
- Order splitting for large allocations
- Allocation tracking and reporting
- P&L tracking with bankroll updates
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Tuple

logger = logging.getLogger(__name__)


class AllocationResult(Enum):
    """Enumeration of allocation outcomes"""
    SUCCESS = "success"
    INSUFFICIENT_CAPITAL = "insufficient_capital"
    MARKET_LIMIT_EXCEEDED = "market_limit_exceeded"
    TOTAL_LIMIT_EXCEEDED = "total_limit_exceeded"
    ALREADY_ALLOCATED = "already_allocated"
    INVALID_AMOUNT = "invalid_amount"


@dataclass
class CapitalConfig:
    """Configuration for capital allocation system"""

    # Per-market limits
    max_exposure_per_market_percent: float = 5.0
    """Maximum exposure per market as % of bankroll (5% default)"""

    max_exposure_per_market_absolute: float = 50.0
    """Hard cap on exposure per market in dollars ($50 default)"""

    # Total limits
    max_total_exposure_percent: float = 30.0
    """Maximum total exposure across all markets as % of bankroll (30% default)"""

    # Order sizing
    min_order_size: float = 1.0
    """Minimum order size in dollars ($1 default)"""

    order_split_threshold: float = 20.0
    """Split orders larger than this threshold ($20 default)"""

    order_split_count: int = 3
    """Number of orders to split large orders into (3 orders default)"""

    # Recycling
    recycle_delay_seconds: float = 5.0
    """Delay before recycling capital after market resolution (5s default)"""


@dataclass
class Allocation:
    """Represents a capital allocation to a market"""

    market_id: str
    """Unique market identifier"""

    amount: float
    """Total amount allocated in dollars"""

    allocated_at: datetime
    """Timestamp of allocation"""

    strategy: str
    """Strategy name (e.g., 'sniper', 'copy_trader')"""

    orders: list[float] = field(default_factory=list)
    """Split order sizes if order splitting was applied"""

    def get_order_sizes(self) -> list[float]:
        """Get list of order sizes. Returns split orders if available, else single order."""
        if self.orders:
            return self.orders
        return [self.amount]


class CapitalAllocator:
    """
    Manages capital allocation across markets with strict exposure controls.

    Thread-safe async implementation that ensures:
    - No single market exceeds per-market limits
    - Total exposure never exceeds portfolio limit
    - All state changes are atomic and logged
    - Concurrent requests are properly serialized
    """

    def __init__(self, config: CapitalConfig, initial_bankroll: float):
        """
        Initialize the capital allocator.

        Args:
            config: CapitalConfig with allocation limits
            initial_bankroll: Initial bankroll in dollars

        Raises:
            ValueError: If initial_bankroll <= 0
        """
        if initial_bankroll <= 0:
            raise ValueError(f"initial_bankroll must be positive, got {initial_bankroll}")

        self.config = config
        self.bankroll = initial_bankroll
        self._allocations: Dict[str, Allocation] = {}
        self._pending_releases: Dict[str, datetime] = {}  # market_id -> release_time
        self._lock = asyncio.Lock()

        logger.info(
            f"CapitalAllocator initialized: bankroll=${initial_bankroll:.2f}, "
            f"max_total_exposure={config.max_total_exposure_percent}%"
        )

    async def request_allocation(
        self,
        market_id: str,
        requested_amount: float,
        strategy: str = "sniper",
    ) -> Tuple[AllocationResult, float]:
        """
        Request capital allocation for a market.

        Validates against per-market and total exposure limits.
        If requested_amount exceeds limits, allocates the maximum allowed.

        Args:
            market_id: Unique market identifier
            requested_amount: Requested allocation in dollars
            strategy: Strategy name for allocation tracking

        Returns:
            Tuple of (AllocationResult, allocated_amount)
            - Result status (SUCCESS, INSUFFICIENT_CAPITAL, etc.)
            - Actual allocated amount (0 if failed)

        Example:
            >>> result, amount = await allocator.request_allocation("market1", 10.0)
            >>> if result == AllocationResult.SUCCESS:
            ...     print(f"Allocated ${amount:.2f}")
        """
        async with self._lock:
            # Validate input
            if requested_amount <= 0:
                logger.warning(
                    f"Invalid allocation request: market={market_id}, "
                    f"amount={requested_amount}"
                )
                return AllocationResult.INVALID_AMOUNT, 0.0

            # Check if already allocated
            if market_id in self._allocations:
                logger.warning(
                    f"Market {market_id} already has allocation "
                    f"${self._allocations[market_id].amount:.2f}"
                )
                return AllocationResult.ALREADY_ALLOCATED, 0.0

            # Calculate maximum allowed for this market
            max_for_market = self._calculate_max_for_market(market_id)

            # Allocate minimum of requested and max allowed
            actual_amount = min(requested_amount, max_for_market)

            # Determine outcome
            if actual_amount <= 0:
                # Determine which limit was exceeded
                total_allocated = self._get_total_allocated()
                max_total = self._get_max_total_exposure()

                if total_allocated >= max_total:
                    result = AllocationResult.TOTAL_LIMIT_EXCEEDED
                else:
                    result = AllocationResult.MARKET_LIMIT_EXCEEDED

                logger.warning(
                    f"Allocation failed for {market_id}: "
                    f"requested=${requested_amount:.2f}, "
                    f"max_allowed=${max_for_market:.2f}, reason={result.value}"
                )
                return result, 0.0

            # Actual allocation
            order_sizes = self._calculate_order_splits(actual_amount)

            allocation = Allocation(
                market_id=market_id,
                amount=actual_amount,
                allocated_at=datetime.utcnow(),
                strategy=strategy,
                orders=order_sizes,
            )

            self._allocations[market_id] = allocation

            log_msg = f"Allocation SUCCESS: market={market_id}, amount=${actual_amount:.2f}"
            if order_sizes != [actual_amount]:
                log_msg += f", orders={[f'${x:.2f}' for x in order_sizes]}"
            logger.info(log_msg)

            return AllocationResult.SUCCESS, actual_amount

    async def release_allocation(self, market_id: str, pnl: float = 0.0) -> float:
        """
        Release allocation after market completes.

        Updates bankroll with P&L and removes allocation from tracking.

        Args:
            market_id: Market to release allocation for
            pnl: Profit/loss on this market (positive = profit, negative = loss)

        Returns:
            Released amount in dollars (0 if market not allocated)

        Example:
            >>> released = await allocator.release_allocation("market1", pnl=0.50)
            >>> print(f"Released ${released:.2f}, P&L was ${pnl:.2f}")
        """
        async with self._lock:
            if market_id not in self._allocations:
                logger.warning(f"No allocation found for market {market_id}")
                return 0.0

            allocation = self._allocations.pop(market_id)
            released_amount = allocation.amount

            # Update bankroll with P&L
            old_bankroll = self.bankroll
            self.bankroll += pnl

            logger.info(
                f"Allocation released: market={market_id}, "
                f"amount=${released_amount:.2f}, pnl=${pnl:+.2f}, "
                f"bankroll=${old_bankroll:.2f}->${self.bankroll:.2f}"
            )

            # Remove from pending releases if queued
            self._pending_releases.pop(market_id, None)

            return released_amount

    async def get_allocation(self, market_id: str) -> Optional[Allocation]:
        """
        Get current allocation for a market.

        Args:
            market_id: Market to look up

        Returns:
            Allocation object or None if not allocated
        """
        async with self._lock:
            return self._allocations.get(market_id)

    async def get_total_allocated(self) -> float:
        """
        Get sum of all current allocations.

        Returns:
            Total allocated amount in dollars
        """
        async with self._lock:
            return self._get_total_allocated()

    async def get_available_capital(self) -> float:
        """
        Get available capital for new allocations.

        Available = Bankroll - Allocated - Pending Releases

        Returns:
            Available capital in dollars
        """
        async with self._lock:
            total_allocated = self._get_total_allocated()
            pending_amount = sum(
                self._allocations.get(mid, Allocation(mid, 0, datetime.utcnow(), ""))
                .amount
                for mid in self._pending_releases
                if mid in self._allocations
            )
            return max(0, self.bankroll - total_allocated - pending_amount)

    async def get_market_headroom(self, market_id: str) -> float:
        """
        Get how much more can be allocated to a specific market.

        Returns:
            Additional amount that can be allocated to this market
        """
        async with self._lock:
            if market_id in self._allocations:
                return 0.0  # Already allocated
            return self._calculate_max_for_market(market_id)

    async def get_total_headroom(self) -> float:
        """
        Get how much more can be allocated globally.

        Returns:
            Maximum additional amount that can be allocated across all markets
        """
        async with self._lock:
            max_total = self._get_max_total_exposure()
            current_total = self._get_total_allocated()
            return max(0, max_total - current_total)

    async def update_bankroll(self, new_bankroll: float) -> None:
        """
        Update bankroll (e.g., from deposits or withdrawals).

        Args:
            new_bankroll: New bankroll amount in dollars

        Raises:
            ValueError: If new_bankroll <= 0
        """
        if new_bankroll <= 0:
            raise ValueError(f"new_bankroll must be positive, got {new_bankroll}")

        async with self._lock:
            old_bankroll = self.bankroll
            self.bankroll = new_bankroll
            logger.info(
                f"Bankroll updated: ${old_bankroll:.2f} -> ${new_bankroll:.2f}"
            )

    async def sync_with_wallet(self, wallet_balance: float) -> None:
        """
        Sync bankroll with actual wallet balance.

        Useful for periodic reconciliation with on-chain state.

        Args:
            wallet_balance: Current wallet balance in dollars
        """
        await self.update_bankroll(wallet_balance)
        logger.info(f"Bankroll synced with wallet: ${wallet_balance:.2f}")

    def get_allocation_report(self) -> Dict:
        """
        Get comprehensive allocation report.

        Returns:
            Dictionary with allocation status and details

        Example:
            >>> report = allocator.get_allocation_report()
            >>> print(f"Utilization: {report['utilization_percent']:.1f}%")
        """
        total_allocated = self._get_total_allocated()
        available = self.bankroll - total_allocated
        utilization_percent = (total_allocated / self.bankroll * 100) if self.bankroll > 0 else 0
        max_total = self._get_max_total_exposure()
        headroom = max(0, max_total - total_allocated)

        allocations_list = [
            {
                "market_id": alloc.market_id,
                "amount": alloc.amount,
                "strategy": alloc.strategy,
                "allocated_at": alloc.allocated_at.isoformat(),
                "orders": alloc.orders if alloc.orders else [alloc.amount],
            }
            for alloc in self._allocations.values()
        ]

        return {
            "bankroll": self.bankroll,
            "total_allocated": total_allocated,
            "available": available,
            "utilization_percent": utilization_percent,
            "max_total_allowed": max_total,
            "headroom": headroom,
            "num_allocated_markets": len(self._allocations),
            "allocations": allocations_list,
        }

    # Private helper methods

    def _calculate_max_for_market(self, market_id: str) -> float:
        """
        Calculate maximum allocation for a market.

        Considers:
        1. Per-market percent limit
        2. Per-market absolute limit
        3. Remaining total exposure headroom
        4. Available capital

        Args:
            market_id: Market to calculate limit for

        Returns:
            Maximum allocatable amount in dollars
        """
        # Per-market limits
        max_by_percent = self.bankroll * (self.config.max_exposure_per_market_percent / 100)
        max_by_absolute = self.config.max_exposure_per_market_absolute
        market_limit = min(max_by_percent, max_by_absolute)

        # Total exposure headroom
        max_total = self._get_max_total_exposure()
        total_allocated = self._get_total_allocated()
        total_headroom = max(0, max_total - total_allocated)

        # Available capital
        available = self._get_available_capital()

        # Return minimum of all constraints
        max_for_market = min(market_limit, total_headroom, available)
        return max(0, max_for_market)

    def _calculate_order_splits(self, amount: float) -> list[float]:
        """
        Split large orders to reduce slippage.

        If amount > order_split_threshold, splits into order_split_count orders.
        Otherwise returns single order.

        Args:
            amount: Total amount to allocate

        Returns:
            List of order sizes

        Example:
            >>> allocator.config.order_split_threshold = 20.0
            >>> allocator.config.order_split_count = 3
            >>> allocator._calculate_order_splits(30.0)
            [10.0, 10.0, 10.0]
        """
        if amount <= self.config.order_split_threshold:
            return []  # Empty list means single order of full amount

        # Split into equal parts
        order_size = amount / self.config.order_split_count
        orders = [order_size] * self.config.order_split_count

        # Handle rounding: add remainder to last order
        remainder = amount - (order_size * self.config.order_split_count)
        if remainder > 0:
            orders[-1] += remainder

        return orders

    def _get_total_allocated(self) -> float:
        """Get sum of all current allocations (not thread-safe, use with lock)"""
        return sum(alloc.amount for alloc in self._allocations.values())

    def _get_available_capital(self) -> float:
        """Get available capital (not thread-safe, use with lock)"""
        total_allocated = self._get_total_allocated()
        return max(0, self.bankroll - total_allocated)

    def _get_max_total_exposure(self) -> float:
        """Get maximum total exposure allowed (not thread-safe, use with lock)"""
        return self.bankroll * (self.config.max_total_exposure_percent / 100)
