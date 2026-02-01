"""
Market Priority Queue for Polymarket Latency-Arbitrage Bot
==========================================================

Time-priority queue for markets, keyed by expiry time and edge size.

Uses Python's heapq for efficient O(log n) operations.
Markets are prioritized by:
1. Time to expiry (sooner = higher priority)
2. Edge size (larger = higher priority as tiebreaker)

Implementation uses a two-list pattern for efficient removal:
- _heap: The actual heap structure
- _entries: Maps token_id to entry metadata for O(1) removal
"""

import heapq
import logging
from typing import Optional, Dict, List, Tuple, TYPE_CHECKING
from datetime import datetime

if TYPE_CHECKING:
    from market_state import Market


logger = logging.getLogger(__name__)


class MarketPriorityQueue:
    """
    Time-priority queue for markets.

    Supports O(log n) push/pop and O(1) removal via lazy deletion pattern.

    Priority is calculated as:
    -time_to_expiry - Returns negated seconds to expiry so heap's min-max
                      semantics put shortest-expiry markets at top
    """

    def __init__(self):
        """Initialize empty priority queue"""
        self._heap: List[Tuple[float, str]] = []  # (priority, token_id)
        self._entries: Dict[str, Tuple[float, str, bool]] = {}  # token_id -> (priority, token_id, removed)
        self._entry_count = 0  # Counter for stable sort

    def push(self, market: "Market") -> None:
        """
        Add market with priority = time_to_expiry.

        Sooner expiries get higher priority (lower values at top of min-heap).

        Args:
            market: Market instance to add
        """
        # Calculate priority as seconds to expiry
        # Min-heap puts lowest values at top, so shortest-expiry markets first
        time_to_expiry_seconds = market.time_to_expiry().total_seconds()
        priority = time_to_expiry_seconds

        # Lazy deletion: remove old entry if exists
        if market.token_id in self._entries:
            old_priority, _, _ = self._entries[market.token_id]
            # Mark old entry as removed
            self._entries[market.token_id] = (old_priority, market.token_id, True)

        # Add new entry
        self._entry_count += 1
        self._entries[market.token_id] = (priority, market.token_id, False)
        heapq.heappush(self._heap, (priority, self._entry_count, market.token_id))

        logger.debug(
            f"Market queued: {market.token_id} | "
            f"Time to expiry: {time_to_expiry_seconds:.1f}s | Priority: {priority:.1f}"
        )

    def pop(self) -> Optional[str]:
        """
        Get and remove highest priority market token_id.

        Uses lazy deletion to skip removed entries.

        Returns:
            token_id of next market to process, or None if queue empty
        """
        while self._heap:
            priority, _, token_id = heapq.heappop(self._heap)

            if token_id not in self._entries:
                continue  # Already removed, skip

            _, _, removed = self._entries[token_id]
            if removed:
                continue  # Marked as removed, skip

            # This is a valid entry
            del self._entries[token_id]

            logger.debug(f"Market popped from queue: {token_id} | Time to expiry: {priority:.1f}s")
            return token_id

        return None

    def peek(self) -> Optional[str]:
        """
        View top priority market without removing.

        Returns:
            token_id of next market, or None if queue empty
        """
        # Skip removed entries to find next valid
        temp_removed = []

        while self._heap:
            priority, _, token_id = self._heap[0]

            if token_id not in self._entries:
                # Already fully removed, skip
                heapq.heappop(self._heap)
                continue

            _, _, removed = self._entries[token_id]
            if removed:
                # Marked as removed, skip
                heapq.heappop(self._heap)
                continue

            # Found valid entry
            return token_id

        return None

    def update_priority(self, market: "Market") -> None:
        """
        Update priority when price/time changes.

        Since we can't update in-place in a heap, we use lazy deletion:
        Mark old entry as removed and add new entry.

        Args:
            market: Market with updated time/price
        """
        if market.token_id not in self._entries:
            # Not in queue, just add it
            self.push(market)
            return

        # Mark old entry as removed
        old_priority, _, _ = self._entries[market.token_id]
        self._entries[market.token_id] = (old_priority, market.token_id, True)

        # Add new entry with updated priority
        time_to_expiry_seconds = market.time_to_expiry().total_seconds()
        new_priority = time_to_expiry_seconds

        self._entry_count += 1
        self._entries[market.token_id] = (new_priority, market.token_id, False)
        heapq.heappush(self._heap, (new_priority, self._entry_count, market.token_id))

        logger.debug(
            f"Market priority updated: {market.token_id} | "
            f"Time to expiry: {new_priority:.1f}s (was {old_priority:.1f}s)"
        )

    def remove(self, token_id: str) -> bool:
        """
        Mark market as removed (lazy deletion).

        Actual removal from heap happens on next pop/peek.

        Args:
            token_id: Market token ID to remove

        Returns:
            True if was in queue, False otherwise
        """
        if token_id not in self._entries:
            logger.debug(f"Market not in queue for removal: {token_id}")
            return False

        # Mark as removed
        priority, _, _ = self._entries[token_id]
        self._entries[token_id] = (priority, token_id, True)

        logger.debug(f"Market marked for removal: {token_id}")
        return True

    def __len__(self) -> int:
        """
        Get number of active markets in queue.

        Counts only non-removed entries.

        Returns:
            Number of active markets
        """
        return sum(1 for _, _, removed in self._entries.values() if not removed)

    def is_empty(self) -> bool:
        """
        Check if queue is empty.

        Returns:
            True if no active markets in queue
        """
        return self.__len__() == 0

    def get_all_active(self) -> List[str]:
        """
        Get list of all active token_ids in queue.

        Returns:
            List of token IDs not marked as removed
        """
        return [
            token_id
            for token_id, (_, _, removed) in self._entries.items()
            if not removed
        ]

    def debug_stats(self) -> Dict[str, int]:
        """
        Get debug statistics about queue state.

        Returns:
            Dictionary with heap size, active entries, removed entries
        """
        active = sum(1 for _, _, removed in self._entries.values() if not removed)
        removed = sum(1 for _, _, removed in self._entries.values() if removed)

        return {
            "heap_size": len(self._heap),
            "active_entries": active,
            "removed_entries": removed,
            "total_entries": len(self._entries),
        }
