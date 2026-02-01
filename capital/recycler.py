"""
Capital Recycler
================

Manages capital recycling as markets resolve and complete.
Freed capital goes back to the pool for new opportunities.

Features:
- Queue markets for delayed recycling (respects settlement delays)
- Background task processes recycling automatically
- Configurable recycle delay before capital is freed
- Recycling history and daily statistics
- Optional callback notification when capital is freed
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional, Callable, Awaitable, List, Dict

from capital.allocator import CapitalAllocator, CapitalConfig

logger = logging.getLogger(__name__)


@dataclass
class RecycleEvent:
    """Represents a capital recycling event"""

    market_id: str
    """Market identifier that was recycled"""

    amount: float
    """Amount recycled in dollars"""

    pnl: float
    """Profit/loss on the market"""

    resolved_at: datetime
    """Timestamp when market was resolved"""

    recycled_at: Optional[datetime] = None
    """Timestamp when capital was recycled back (None if pending)"""

    def is_pending(self) -> bool:
        """Check if this recycle event is still pending"""
        return self.recycled_at is None

    def time_to_recycle(self) -> Optional[timedelta]:
        """
        Get time remaining until this recycle can occur.
        Returns None if already recycled.
        """
        if self.recycled_at is not None:
            return None
        # Will be set by recycler based on config delay


class CapitalRecycler:
    """
    Manages capital recycling as markets complete and resolve.

    Thread-safe async implementation that:
    - Queues markets for recycling after resolution
    - Respects configurable settlement delays
    - Automatically releases capital back to allocator
    - Tracks recycling history and statistics
    - Provides notifications via callbacks
    """

    def __init__(
        self,
        config: CapitalConfig,
        allocator: CapitalAllocator,
        on_capital_freed: Optional[Callable[[float], Awaitable[None]]] = None,
    ):
        """
        Initialize the capital recycler.

        Args:
            config: CapitalConfig with recycle settings
            allocator: CapitalAllocator to release capital from
            on_capital_freed: Optional async callback when capital is freed.
                              Called with (freed_amount).
        """
        self.config = config
        self.allocator = allocator
        self.on_capital_freed = on_capital_freed

        self._pending_recycles: List[RecycleEvent] = []
        self._recycle_history: List[RecycleEvent] = []
        self._lock = asyncio.Lock()
        self._running = False
        self._recycler_task: Optional[asyncio.Task] = None

        logger.info(
            f"CapitalRecycler initialized: "
            f"recycle_delay={config.recycle_delay_seconds}s"
        )

    async def start(self) -> None:
        """
        Start the recycler background task.

        Safe to call multiple times (idempotent).
        """
        async with self._lock:
            if self._running:
                logger.warning("Recycler already running")
                return

            self._running = True
            self._recycler_task = asyncio.create_task(self._recycle_loop())
            logger.info("CapitalRecycler started")

    async def stop(self) -> None:
        """
        Stop the recycler background task.

        Safe to call multiple times (idempotent).
        Waits for current iteration to complete.
        """
        async with self._lock:
            if not self._running:
                logger.warning("Recycler not running")
                return

            self._running = False

        if self._recycler_task:
            try:
                await asyncio.wait_for(self._recycler_task, timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("Recycler task did not stop within timeout")
                self._recycler_task.cancel()
            except asyncio.CancelledError:
                pass

        logger.info("CapitalRecycler stopped")

    async def queue_recycle(
        self,
        market_id: str,
        pnl: float = 0.0,
    ) -> None:
        """
        Queue a market's capital for recycling after resolution.

        Capital will be recycled after recycle_delay_seconds.
        Until then, the allocation is still held.

        Args:
            market_id: Market that resolved
            pnl: Profit/loss on the market (positive = profit)

        Example:
            >>> await recycler.queue_recycle("market1", pnl=0.50)
            >>> # Capital will be recycled in ~5 seconds
        """
        async with self._lock:
            event = RecycleEvent(
                market_id=market_id,
                amount=0.0,  # Will be filled in by recycler
                pnl=pnl,
                resolved_at=datetime.utcnow(),
            )

            self._pending_recycles.append(event)
            logger.info(
                f"Recycle queued: market={market_id}, pnl=${pnl:+.2f}, "
                f"will recycle in {self.config.recycle_delay_seconds}s"
            )

    async def force_recycle(self, market_id: str) -> float:
        """
        Immediately recycle a market without waiting for delay.

        Useful for manual cleanup or emergency scenarios.

        Args:
            market_id: Market to recycle

        Returns:
            Released amount (0 if market not found or not allocated)

        Example:
            >>> released = await recycler.force_recycle("market1")
            >>> print(f"Forced recycle of ${released:.2f}")
        """
        async with self._lock:
            # Find and remove from pending
            event = None
            for i, pending in enumerate(self._pending_recycles):
                if pending.market_id == market_id:
                    event = self._pending_recycles.pop(i)
                    break

            # Release allocation
            allocation = await self.allocator.get_allocation(market_id)
            if not allocation:
                logger.warning(f"No allocation found for forced recycle: {market_id}")
                return 0.0

            pnl = event.pnl if event else 0.0
            released = await self.allocator.release_allocation(market_id, pnl=pnl)

            # Record in history
            completed_event = RecycleEvent(
                market_id=market_id,
                amount=released,
                pnl=pnl,
                resolved_at=event.resolved_at if event else datetime.utcnow(),
                recycled_at=datetime.utcnow(),
            )
            self._recycle_history.append(completed_event)

            logger.info(
                f"Forced recycle completed: market={market_id}, "
                f"released=${released:.2f}"
            )

            # Notify callback
            if self.on_capital_freed and released > 0:
                await self.on_capital_freed(released)

            return released

    async def get_pending_recycles(self) -> List[RecycleEvent]:
        """
        Get all pending recycle events.

        Returns:
            List of RecycleEvent objects that haven't been recycled yet
        """
        async with self._lock:
            return [event for event in self._pending_recycles if event.is_pending()]

    async def get_pending_amount(self) -> float:
        """
        Get total amount pending recycling.

        Returns:
            Sum of allocations queued for recycling
        """
        async with self._lock:
            total = 0.0
            for event in self._pending_recycles:
                if not event.is_pending():
                    continue
                allocation = self.allocator._allocations.get(event.market_id)
                if allocation:
                    total += allocation.amount
            return total

    async def get_recycle_history(
        self,
        since: Optional[datetime] = None,
        limit: int = 100,
    ) -> List[RecycleEvent]:
        """
        Get recycle history.

        Args:
            since: Only return events after this timestamp (optional)
            limit: Maximum number of events to return

        Returns:
            List of completed RecycleEvent objects
        """
        async with self._lock:
            history = [e for e in self._recycle_history if e.recycled_at is not None]

            if since:
                history = [e for e in history if e.recycled_at >= since]

            # Most recent first
            history.sort(key=lambda e: e.recycled_at, reverse=True)
            return history[:limit]

    def get_daily_stats(self) -> Dict:
        """
        Get daily recycling statistics.

        Returns:
            Dictionary with daily recycle stats

        Example:
            >>> stats = recycler.get_daily_stats()
            >>> print(f"Recycled {stats['recycles_today']} times today")
        """
        now = datetime.utcnow()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        # Count recycled events from today
        today_recycled = [
            e for e in self._recycle_history
            if e.recycled_at and e.recycled_at >= today_start
        ]

        total_recycled = sum(e.amount for e in today_recycled)
        total_pnl = sum(e.pnl for e in today_recycled)

        # Calculate average recycle time
        recycle_times = []
        for event in today_recycled:
            if event.recycled_at and event.resolved_at:
                recycle_time = (event.recycled_at - event.resolved_at).total_seconds()
                recycle_times.append(recycle_time)

        avg_recycle_time = (
            sum(recycle_times) / len(recycle_times)
            if recycle_times
            else 0.0
        )

        return {
            "recycles_today": len(today_recycled),
            "capital_recycled_today": total_recycled,
            "total_pnl_today": total_pnl,
            "avg_recycle_time_seconds": avg_recycle_time,
            "pending_recycles": len(self._pending_recycles),
        }

    # Private helper methods

    async def _recycle_loop(self) -> None:
        """
        Background loop that processes pending recycles.

        Runs continuously while _running is True.
        Checks for markets ready to be recycled and releases them.
        """
        logger.info("Recycle loop started")

        try:
            while self._running:
                try:
                    await self._process_pending_recycles()
                    # Check every 100ms for pending recycles
                    await asyncio.sleep(0.1)
                except Exception as e:
                    logger.error(f"Error in recycle loop: {e}", exc_info=True)
                    await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            logger.info("Recycle loop cancelled")
        finally:
            logger.info("Recycle loop stopped")

    async def _process_pending_recycles(self) -> None:
        """
        Process markets ready for recycling.

        Check each pending recycle and release if delay has passed.
        """
        async with self._lock:
            now = datetime.utcnow()
            recycles_to_process = []

            # Find markets ready to recycle
            remaining_pending = []
            for event in self._pending_recycles:
                if event.is_pending():
                    time_since_resolve = (now - event.resolved_at).total_seconds()
                    if time_since_resolve >= self.config.recycle_delay_seconds:
                        recycles_to_process.append(event)
                    else:
                        remaining_pending.append(event)
                else:
                    remaining_pending.append(event)

            self._pending_recycles = remaining_pending

            # Release allocations for ready recycles
            for event in recycles_to_process:
                try:
                    allocation = self.allocator._allocations.get(event.market_id)
                    if allocation:
                        released = await self.allocator.release_allocation(
                            event.market_id,
                            pnl=event.pnl,
                        )
                        event.amount = released
                    else:
                        logger.warning(
                            f"Allocation not found during recycle: {event.market_id}"
                        )
                        event.amount = 0.0

                    event.recycled_at = datetime.utcnow()
                    self._recycle_history.append(event)

                    logger.info(
                        f"Recycle processed: market={event.market_id}, "
                        f"released=${event.amount:.2f}"
                    )

                    # Notify callback
                    if self.on_capital_freed and event.amount > 0:
                        try:
                            await self.on_capital_freed(event.amount)
                        except Exception as e:
                            logger.error(
                                f"Error in on_capital_freed callback: {e}",
                                exc_info=True,
                            )

                except Exception as e:
                    logger.error(
                        f"Error processing recycle for {event.market_id}: {e}",
                        exc_info=True,
                    )
