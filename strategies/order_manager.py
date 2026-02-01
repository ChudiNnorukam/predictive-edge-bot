"""
Order Manager for Spread Capture Strategy
==========================================

Manages limit order lifecycle:
- Place buy/sell limit orders
- Track pending orders and fills
- Handle partial fills
- Cancel stale orders
- Sync state with exchange
"""

import asyncio
import time
import logging
from dataclasses import dataclass, field
from typing import Dict, Optional, List, Any
from enum import Enum

logger = logging.getLogger(__name__)


class OrderStatus(Enum):
    """Order status states."""
    PENDING = "PENDING"       # Order submitted but not confirmed
    OPEN = "OPEN"             # Order confirmed and on order book
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


@dataclass
class Order:
    """Represents a limit order."""

    order_id: str
    token_id: str
    side: str  # YES or NO
    action: str  # BUY or SELL
    price: float
    size: float
    created_at: float = field(default_factory=time.time)

    # Fill tracking
    filled_size: float = 0.0
    average_fill_price: float = 0.0

    # Status
    status: OrderStatus = OrderStatus.PENDING

    # Metadata
    strategy: str = "spread_capture"
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def remaining_size(self) -> float:
        """Size remaining to be filled."""
        return max(0, self.size - self.filled_size)

    @property
    def fill_pct(self) -> float:
        """Percentage of order filled."""
        return (self.filled_size / self.size * 100) if self.size > 0 else 0

    @property
    def age_seconds(self) -> float:
        """Seconds since order was created."""
        return time.time() - self.created_at

    @property
    def is_active(self) -> bool:
        """Check if order is still active (can be filled/cancelled)."""
        return self.status in (OrderStatus.PENDING, OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED)

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "order_id": self.order_id,
            "token_id": self.token_id,
            "side": self.side,
            "action": self.action,
            "price": self.price,
            "size": self.size,
            "filled_size": self.filled_size,
            "remaining_size": self.remaining_size,
            "fill_pct": self.fill_pct,
            "status": self.status.value,
            "age_seconds": self.age_seconds,
            "created_at": self.created_at,
        }


class OrderManager:
    """
    Manages limit order lifecycle for spread capture strategy.

    Coordinates with executor for order placement and tracks
    order state locally for efficient position management.
    """

    def __init__(
        self,
        executor,
        max_orders_per_market: int = 2,
        stale_order_seconds: float = 300,  # 5 minutes
    ):
        """
        Initialize order manager.

        Args:
            executor: OrderExecutor instance for placing orders
            max_orders_per_market: Maximum concurrent orders per market
            stale_order_seconds: Cancel orders older than this
        """
        self.executor = executor
        self.max_orders_per_market = max_orders_per_market
        self.stale_order_seconds = stale_order_seconds

        # Order tracking
        self.orders: Dict[str, Order] = {}  # order_id -> Order
        self.orders_by_market: Dict[str, List[str]] = {}  # token_id -> [order_ids]
        self._lock = asyncio.Lock()

        # Statistics
        self.total_orders_placed = 0
        self.total_orders_filled = 0
        self.total_orders_cancelled = 0
        self.total_volume_filled = 0.0

        logger.info(
            f"OrderManager initialized | Max per market: {max_orders_per_market} | "
            f"Stale threshold: {stale_order_seconds}s"
        )

    async def place_buy(
        self,
        token_id: str,
        side: str,
        price: float,
        size: float,
        metadata: Optional[Dict] = None,
    ) -> Optional[str]:
        """
        Place a limit buy order.

        Args:
            token_id: Market token ID
            side: YES or NO
            price: Limit price (0-1)
            size: Order size in USDC
            metadata: Optional metadata

        Returns:
            Order ID if successful, None otherwise
        """
        return await self._place_order(token_id, side, "BUY", price, size, metadata)

    async def place_sell(
        self,
        token_id: str,
        side: str,
        price: float,
        size: float,
        metadata: Optional[Dict] = None,
    ) -> Optional[str]:
        """
        Place a limit sell order.

        Args:
            token_id: Market token ID
            side: YES or NO
            price: Limit price (0-1)
            size: Order size in USDC
            metadata: Optional metadata

        Returns:
            Order ID if successful, None otherwise
        """
        return await self._place_order(token_id, side, "SELL", price, size, metadata)

    async def _place_order(
        self,
        token_id: str,
        side: str,
        action: str,
        price: float,
        size: float,
        metadata: Optional[Dict] = None,
    ) -> Optional[str]:
        """Internal order placement."""
        async with self._lock:
            # Check order limit per market
            market_orders = self.orders_by_market.get(token_id, [])
            active_count = sum(
                1 for oid in market_orders
                if oid in self.orders and self.orders[oid].is_active
            )

            if active_count >= self.max_orders_per_market:
                logger.warning(
                    f"Order limit reached for {token_id[:16]}... "
                    f"({active_count}/{self.max_orders_per_market})"
                )
                return None

        # Place order via executor
        order_id = await self.executor.place_limit_order(
            token_id=token_id,
            side=side,
            action=action,
            price=price,
            size=size,
            strategy="spread_capture",
        )

        if not order_id:
            return None

        # Track order
        async with self._lock:
            order = Order(
                order_id=order_id,
                token_id=token_id,
                side=side,
                action=action,
                price=price,
                size=size,
                metadata=metadata or {},
            )

            self.orders[order_id] = order

            if token_id not in self.orders_by_market:
                self.orders_by_market[token_id] = []
            self.orders_by_market[token_id].append(order_id)

            self.total_orders_placed += 1

        logger.info(
            f"Order placed: {order_id} | {action} {side} {token_id[:16]}... "
            f"@ ${price:.3f} x ${size:.2f}"
        )

        return order_id

    async def cancel_order(self, order_id: str) -> bool:
        """
        Cancel a specific order.

        Args:
            order_id: Order ID to cancel

        Returns:
            True if cancelled successfully
        """
        async with self._lock:
            if order_id not in self.orders:
                return False

            order = self.orders[order_id]
            if not order.is_active:
                return False

        # Cancel via executor
        success = await self.executor.cancel_order(order_id)

        if success:
            async with self._lock:
                if order_id in self.orders:
                    self.orders[order_id].status = OrderStatus.CANCELLED
                    self.total_orders_cancelled += 1

            logger.info(f"Order cancelled: {order_id}")

        return success

    async def cancel_all_for_market(self, token_id: str) -> int:
        """
        Cancel all orders for a specific market.

        Args:
            token_id: Market token ID

        Returns:
            Number of orders cancelled
        """
        async with self._lock:
            order_ids = self.orders_by_market.get(token_id, [])
            active_orders = [
                oid for oid in order_ids
                if oid in self.orders and self.orders[oid].is_active
            ]

        cancelled = 0
        for order_id in active_orders:
            if await self.cancel_order(order_id):
                cancelled += 1

        return cancelled

    async def cancel_all(self) -> int:
        """
        Cancel all active orders.

        Returns:
            Number of orders cancelled
        """
        # Use executor's bulk cancel
        count = await self.executor.cancel_all_orders()

        # Update local state
        async with self._lock:
            for order in self.orders.values():
                if order.is_active:
                    order.status = OrderStatus.CANCELLED
                    self.total_orders_cancelled += 1

        return count

    async def cancel_stale_orders(self) -> int:
        """
        Cancel orders older than stale threshold.

        Returns:
            Number of stale orders cancelled
        """
        async with self._lock:
            stale_orders = [
                oid for oid, order in self.orders.items()
                if order.is_active and order.age_seconds > self.stale_order_seconds
            ]

        cancelled = 0
        for order_id in stale_orders:
            if await self.cancel_order(order_id):
                cancelled += 1
                logger.info(f"Cancelled stale order: {order_id}")

        return cancelled

    async def sync_with_exchange(self) -> int:
        """
        Sync local order state with exchange.

        Fetches current orders from exchange and updates local state.
        Removes orders no longer on exchange.

        Returns:
            Number of state changes detected
        """
        # Fetch orders from exchange
        exchange_orders = await self.executor.get_open_orders()

        if not exchange_orders:
            exchange_orders = []

        exchange_order_ids = {o.get("orderID") or o.get("id") for o in exchange_orders}
        changes = 0

        async with self._lock:
            # Check for orders that are no longer open
            for order_id, order in self.orders.items():
                if order.is_active and order_id not in exchange_order_ids:
                    # Order is no longer on exchange - mark as filled or cancelled
                    # We assume filled if it was on exchange before
                    order.status = OrderStatus.FILLED
                    order.filled_size = order.size
                    self.total_orders_filled += 1
                    self.total_volume_filled += order.size
                    changes += 1
                    logger.info(f"Order completed (sync): {order_id}")

            # Update fill status from exchange data
            for ex_order in exchange_orders:
                order_id = ex_order.get("orderID") or ex_order.get("id")
                if order_id in self.orders:
                    filled = float(ex_order.get("filledSize", 0) or ex_order.get("sizeMatched", 0))
                    if filled > self.orders[order_id].filled_size:
                        self.orders[order_id].filled_size = filled
                        if filled >= self.orders[order_id].size:
                            self.orders[order_id].status = OrderStatus.FILLED
                        else:
                            self.orders[order_id].status = OrderStatus.PARTIALLY_FILLED
                        changes += 1

        return changes

    async def get_order(self, order_id: str) -> Optional[Order]:
        """Get order by ID."""
        async with self._lock:
            return self.orders.get(order_id)

    async def get_active_orders(self, token_id: Optional[str] = None) -> List[Order]:
        """
        Get all active orders.

        Args:
            token_id: Optional filter by market

        Returns:
            List of active Order objects
        """
        async with self._lock:
            orders = [o for o in self.orders.values() if o.is_active]

            if token_id:
                orders = [o for o in orders if o.token_id == token_id]

            return orders

    async def get_pending_buys(self, token_id: str) -> List[Order]:
        """Get pending buy orders for a market."""
        active = await self.get_active_orders(token_id)
        return [o for o in active if o.action == "BUY"]

    async def get_pending_sells(self, token_id: str) -> List[Order]:
        """Get pending sell orders for a market."""
        active = await self.get_active_orders(token_id)
        return [o for o in active if o.action == "SELL"]

    async def check_for_fills(self, token_id: str) -> List[Order]:
        """
        Check for filled orders on a market.

        Returns newly filled orders for position tracking.

        Args:
            token_id: Market token ID

        Returns:
            List of filled Order objects
        """
        # Sync with exchange first
        await self.sync_with_exchange()

        async with self._lock:
            order_ids = self.orders_by_market.get(token_id, [])
            filled = [
                self.orders[oid] for oid in order_ids
                if oid in self.orders and self.orders[oid].status == OrderStatus.FILLED
            ]

        return filled

    def get_metrics(self) -> dict:
        """Get order manager metrics."""
        active_count = sum(1 for o in self.orders.values() if o.is_active)

        return {
            "total_orders": len(self.orders),
            "active_orders": active_count,
            "total_placed": self.total_orders_placed,
            "total_filled": self.total_orders_filled,
            "total_cancelled": self.total_orders_cancelled,
            "total_volume_filled": self.total_volume_filled,
            "fill_rate": (
                self.total_orders_filled / self.total_orders_placed * 100
                if self.total_orders_placed > 0 else 0
            ),
        }

    async def cleanup_completed(self, max_age_seconds: float = 3600) -> int:
        """
        Remove completed orders older than max_age from memory.

        Args:
            max_age_seconds: Remove orders older than this (1 hour default)

        Returns:
            Number of orders cleaned up
        """
        async with self._lock:
            to_remove = [
                oid for oid, order in self.orders.items()
                if not order.is_active and order.age_seconds > max_age_seconds
            ]

            for oid in to_remove:
                order = self.orders.pop(oid)
                # Remove from market index
                if order.token_id in self.orders_by_market:
                    self.orders_by_market[order.token_id] = [
                        o for o in self.orders_by_market[order.token_id] if o != oid
                    ]

        return len(to_remove)
