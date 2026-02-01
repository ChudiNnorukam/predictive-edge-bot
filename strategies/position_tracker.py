"""
Position Tracker for Spread Capture Strategy
=============================================

Tracks open positions with entry price, size, timestamp, and P&L calculations.
Designed for spread capture where positions are exited before market resolution.
"""

import asyncio
import time
import logging
from dataclasses import dataclass, field
from typing import Dict, Optional, List
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class Position:
    """Represents an open position in the spread capture strategy."""

    token_id: str
    side: str  # YES or NO
    entry_price: float
    size: float  # in USDC
    entry_time: float  # Unix timestamp

    # Exit targets
    take_profit_price: Optional[float] = None
    stop_loss_price: Optional[float] = None
    max_hold_seconds: int = 600  # 10 minutes default

    # Current state
    current_price: float = 0.0
    last_update: float = field(default_factory=time.time)

    # Metadata
    order_id: Optional[str] = None
    market_question: str = ""
    market_expiry: Optional[datetime] = None

    @property
    def unrealized_pnl(self) -> float:
        """Calculate unrealized P&L based on current price."""
        if self.current_price <= 0:
            return 0.0

        # For BUY positions: profit when price rises
        # Shares = size / entry_price
        shares = self.size / self.entry_price if self.entry_price > 0 else 0
        current_value = shares * self.current_price
        return current_value - self.size

    @property
    def unrealized_pnl_pct(self) -> float:
        """Calculate unrealized P&L as percentage."""
        if self.size <= 0:
            return 0.0
        return (self.unrealized_pnl / self.size) * 100

    @property
    def hold_time_seconds(self) -> float:
        """Time since position was opened."""
        return time.time() - self.entry_time

    @property
    def is_expired(self) -> bool:
        """Check if position has exceeded max hold time."""
        return self.hold_time_seconds > self.max_hold_seconds

    @property
    def time_to_market_expiry(self) -> Optional[float]:
        """Seconds until market expires, or None if unknown."""
        if self.market_expiry:
            return (self.market_expiry - datetime.utcnow()).total_seconds()
        return None

    def should_take_profit(self) -> bool:
        """Check if take profit target is hit."""
        if self.take_profit_price and self.current_price >= self.take_profit_price:
            return True
        return False

    def should_stop_loss(self) -> bool:
        """Check if stop loss is triggered."""
        if self.stop_loss_price and self.current_price <= self.stop_loss_price:
            return True
        return False

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "token_id": self.token_id,
            "side": self.side,
            "entry_price": self.entry_price,
            "size": self.size,
            "entry_time": self.entry_time,
            "take_profit_price": self.take_profit_price,
            "stop_loss_price": self.stop_loss_price,
            "max_hold_seconds": self.max_hold_seconds,
            "current_price": self.current_price,
            "unrealized_pnl": self.unrealized_pnl,
            "unrealized_pnl_pct": self.unrealized_pnl_pct,
            "hold_time_seconds": self.hold_time_seconds,
            "order_id": self.order_id,
            "market_question": self.market_question,
        }


class PositionTracker:
    """
    Tracks all open positions for the spread capture strategy.

    Thread-safe with async locking. Provides methods to:
    - Add/remove positions
    - Update prices
    - Calculate portfolio-level P&L
    - Find positions needing exit
    """

    def __init__(self, max_positions: int = 10):
        """
        Initialize position tracker.

        Args:
            max_positions: Maximum concurrent positions allowed
        """
        self.positions: Dict[str, Position] = {}
        self.max_positions = max_positions
        self._lock = asyncio.Lock()

        # Statistics
        self.total_positions_opened = 0
        self.total_positions_closed = 0
        self.total_realized_pnl = 0.0

        logger.info(f"PositionTracker initialized | Max positions: {max_positions}")

    async def add_position(
        self,
        token_id: str,
        side: str,
        entry_price: float,
        size: float,
        take_profit_price: Optional[float] = None,
        stop_loss_price: Optional[float] = None,
        max_hold_seconds: int = 600,
        order_id: Optional[str] = None,
        market_question: str = "",
        market_expiry: Optional[datetime] = None,
    ) -> bool:
        """
        Add a new position.

        Args:
            token_id: Market token ID
            side: YES or NO
            entry_price: Entry price
            size: Position size in USDC
            take_profit_price: Target exit price (optional)
            stop_loss_price: Stop loss price (optional)
            max_hold_seconds: Maximum hold time before forced exit
            order_id: Associated order ID
            market_question: Market question text
            market_expiry: Market expiration time

        Returns:
            True if position added, False if at max capacity or already exists
        """
        async with self._lock:
            if len(self.positions) >= self.max_positions:
                logger.warning(
                    f"Cannot add position - at max capacity ({self.max_positions})"
                )
                return False

            if token_id in self.positions:
                logger.warning(f"Position already exists for {token_id[:16]}...")
                return False

            position = Position(
                token_id=token_id,
                side=side,
                entry_price=entry_price,
                size=size,
                entry_time=time.time(),
                take_profit_price=take_profit_price,
                stop_loss_price=stop_loss_price,
                max_hold_seconds=max_hold_seconds,
                current_price=entry_price,
                order_id=order_id,
                market_question=market_question,
                market_expiry=market_expiry,
            )

            self.positions[token_id] = position
            self.total_positions_opened += 1

            logger.info(
                f"Position opened: {token_id[:16]}... | {side} @ ${entry_price:.3f} | "
                f"Size: ${size:.2f} | TP: ${take_profit_price or 0:.3f}"
            )
            return True

    async def update_position(
        self,
        token_id: str,
        current_price: Optional[float] = None,
        take_profit_price: Optional[float] = None,
        stop_loss_price: Optional[float] = None,
    ) -> bool:
        """
        Update an existing position.

        Args:
            token_id: Market token ID
            current_price: New current market price
            take_profit_price: New take profit target
            stop_loss_price: New stop loss level

        Returns:
            True if updated, False if position not found
        """
        async with self._lock:
            if token_id not in self.positions:
                return False

            pos = self.positions[token_id]

            if current_price is not None:
                pos.current_price = current_price
                pos.last_update = time.time()

            if take_profit_price is not None:
                pos.take_profit_price = take_profit_price

            if stop_loss_price is not None:
                pos.stop_loss_price = stop_loss_price

            return True

    async def close_position(
        self,
        token_id: str,
        exit_price: float,
        reason: str = "manual",
    ) -> Optional[Position]:
        """
        Close a position and calculate realized P&L.

        Args:
            token_id: Market token ID
            exit_price: Price at which position was exited
            reason: Reason for closing (target_hit, stop_loss, max_hold, pre_expiry)

        Returns:
            Closed Position object with final P&L, or None if not found
        """
        async with self._lock:
            if token_id not in self.positions:
                return None

            position = self.positions.pop(token_id)
            position.current_price = exit_price

            # Calculate realized P&L
            realized_pnl = position.unrealized_pnl
            self.total_realized_pnl += realized_pnl
            self.total_positions_closed += 1

            logger.info(
                f"Position closed: {token_id[:16]}... | Reason: {reason} | "
                f"Entry: ${position.entry_price:.3f} → Exit: ${exit_price:.3f} | "
                f"P&L: ${realized_pnl:.2f} ({position.unrealized_pnl_pct:.1f}%)"
            )

            return position

    async def get_position(self, token_id: str) -> Optional[Position]:
        """Get position by token ID."""
        async with self._lock:
            return self.positions.get(token_id)

    async def get_all_positions(self) -> List[Position]:
        """Get all open positions."""
        async with self._lock:
            return list(self.positions.values())

    async def get_positions_needing_exit(
        self,
        exit_before_expiry_seconds: int = 60,
    ) -> List[tuple]:
        """
        Find positions that need to be exited.

        Args:
            exit_before_expiry_seconds: Exit this many seconds before market expiry

        Returns:
            List of (position, reason) tuples
        """
        async with self._lock:
            needs_exit = []

            for pos in self.positions.values():
                # Check take profit
                if pos.should_take_profit():
                    needs_exit.append((pos, "target_hit"))
                    continue

                # Check stop loss
                if pos.should_stop_loss():
                    needs_exit.append((pos, "stop_loss"))
                    continue

                # Check max hold time
                if pos.is_expired:
                    needs_exit.append((pos, "max_hold"))
                    continue

                # Check pre-expiry exit
                time_to_expiry = pos.time_to_market_expiry
                if time_to_expiry is not None and time_to_expiry <= exit_before_expiry_seconds:
                    needs_exit.append((pos, "pre_expiry"))
                    continue

            return needs_exit

    async def get_total_exposure(self) -> float:
        """Get total USDC exposure across all positions."""
        async with self._lock:
            return sum(pos.size for pos in self.positions.values())

    async def get_total_unrealized_pnl(self) -> float:
        """Get total unrealized P&L across all positions."""
        async with self._lock:
            return sum(pos.unrealized_pnl for pos in self.positions.values())

    def get_metrics(self) -> dict:
        """Get tracker metrics."""
        return {
            "open_positions": len(self.positions),
            "max_positions": self.max_positions,
            "total_opened": self.total_positions_opened,
            "total_closed": self.total_positions_closed,
            "total_realized_pnl": self.total_realized_pnl,
            "capacity_pct": (len(self.positions) / self.max_positions) * 100,
        }

    async def clear_all(self) -> int:
        """
        Clear all positions (emergency use only).

        Returns:
            Number of positions cleared
        """
        async with self._lock:
            count = len(self.positions)
            self.positions.clear()
            logger.warning(f"Cleared {count} positions (emergency)")
            return count
