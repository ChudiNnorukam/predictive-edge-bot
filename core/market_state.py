"""
Market State Machine for Polymarket Latency-Arbitrage Bot
==========================================================

Manages state transitions for all tracked markets.

Implements Section 9 (Market State Machine) of PRD:
- Market discovery, watching, eligibility, execution, reconciliation
- Automatic state transitions based on time, price, and feed freshness
- Thread-safe with asyncio.Lock
- Comprehensive logging of state changes
"""

import asyncio
import logging
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Tuple


logger = logging.getLogger(__name__)


class MarketState(Enum):
    """Enumeration of all possible market states in the trading lifecycle"""

    DISCOVERED = "discovered"  # Market identified, basic info loaded
    WATCHING = "watching"  # Actively receiving WebSocket updates, not trading yet
    ELIGIBLE = "eligible"  # Criteria met, on-deck for execution
    EXECUTING = "executing"  # Orders live/placed
    RECONCILING = "reconciling"  # Post-resolution, calculating P&L
    DONE = "done"  # Completed, can drop from watchlist
    ON_HOLD = "on_hold"  # Stale feed or error, paused


@dataclass
class Market:
    """
    Represents a single market with all tracking data.

    Tracks prices, execution state, and metadata needed for
    state machine transitions and execution.
    """

    token_id: str
    condition_id: str
    question: str
    end_time: datetime  # Resolution time

    # State management
    state: MarketState = MarketState.DISCOVERED

    # Price data
    current_bid: Optional[float] = None
    current_ask: Optional[float] = None
    last_update: Optional[datetime] = None

    # Execution data
    allocated_capital: float = 0.0
    orders_placed: int = 0
    pnl: float = 0.0

    # Metadata
    is_neg_risk: bool = False
    failure_count: int = 0

    # Internal tracking
    created_at: datetime = field(default_factory=datetime.utcnow)
    transition_history: List[Tuple[datetime, MarketState, MarketState, str]] = field(
        default_factory=list
    )

    def time_to_expiry(self) -> timedelta:
        """
        Calculate time remaining until market resolution.

        Returns:
            timedelta: Time remaining (negative if already expired)
        """
        return self.end_time - datetime.utcnow()

    def is_stale(self, threshold_ms: int = 500) -> bool:
        """
        Check if market feed is stale (no updates in threshold_ms).

        Args:
            threshold_ms: Staleness threshold in milliseconds

        Returns:
            True if no update in threshold_ms, False if recent update or None
        """
        if self.last_update is None:
            return True

        threshold_seconds = threshold_ms / 1000.0
        time_since_update = (datetime.utcnow() - self.last_update).total_seconds()
        return time_since_update > threshold_seconds

    def record_transition(
        self, new_state: MarketState, reason: str = ""
    ) -> None:
        """
        Record a state transition in history.

        Args:
            new_state: New state being transitioned to
            reason: Human-readable reason for transition
        """
        self.transition_history.append(
            (datetime.utcnow(), self.state, new_state, reason)
        )

    def last_transition_time(self) -> Optional[datetime]:
        """Get timestamp of last state transition"""
        if self.transition_history:
            return self.transition_history[-1][0]
        return None


@dataclass
class SchedulerConfig:
    """
    Configuration for the market scheduler.

    Controls transition thresholds, pricing criteria, and cleanup.
    """

    # Time-based criteria
    time_to_eligibility_sec: int = 60  # Enter ELIGIBLE when < 60s to expiry
    stale_feed_threshold_ms: int = 500  # Feed stale if no update in 500ms
    max_failures_before_hold: int = 3  # ON_HOLD after 3+ failures

    # Price criteria
    max_buy_price: float = 0.99  # Don't trade above this price
    min_edge_pct: float = 0.01  # Minimum edge percentage (0.01 = 1%)

    # Cleanup
    max_hold_hours: int = 24  # Drop DONE markets after 24 hours


class MarketStateMachine:
    """
    Manages state transitions for all tracked markets.

    Thread-safe with asyncio.Lock. Handles:
    - Adding/removing markets
    - State transitions with validation
    - Automatic eligibility checks based on time/price
    - Price and stale-feed detection
    - Comprehensive state history
    """

    def __init__(self, config: SchedulerConfig):
        """
        Initialize state machine.

        Args:
            config: Scheduler configuration
        """
        self.markets: Dict[str, Market] = {}
        self.config = config
        self._lock = asyncio.Lock()

        logger.info(
            f"MarketStateMachine initialized with config: "
            f"eligibility_sec={config.time_to_eligibility_sec}, "
            f"stale_threshold_ms={config.stale_feed_threshold_ms}, "
            f"max_buy_price={config.max_buy_price}"
        )

    async def add_market(self, market: Market) -> None:
        """
        Add a new market to tracking.

        Args:
            market: Market instance to track

        Raises:
            ValueError: If market already exists
        """
        async with self._lock:
            if market.token_id in self.markets:
                raise ValueError(f"Market {market.token_id} already exists")

            self.markets[market.token_id] = market
            logger.info(
                f"Market added: {market.token_id} | Question: {market.question[:50]}... | "
                f"Expiry: {market.end_time}"
            )

    async def remove_market(self, token_id: str) -> None:
        """
        Remove market from tracking.

        Args:
            token_id: Market token ID

        Returns:
            True if removed, False if not found
        """
        async with self._lock:
            if token_id in self.markets:
                market = self.markets[token_id]
                del self.markets[token_id]
                logger.info(
                    f"Market removed: {token_id} | Final state: {market.state.value} | "
                    f"Transitions: {len(market.transition_history)}"
                )
            else:
                logger.warning(f"Attempted to remove non-existent market: {token_id}")

    async def transition(
        self, token_id: str, new_state: MarketState, reason: str = ""
    ) -> bool:
        """
        Perform explicit state transition.

        Validates transition is allowed and updates market state.

        Args:
            token_id: Market token ID
            new_state: Target state
            reason: Reason for transition

        Returns:
            True if transition succeeded, False otherwise
        """
        async with self._lock:
            market = self.markets.get(token_id)
            if not market:
                logger.warning(f"Cannot transition: market {token_id} not found")
                return False

            # Validate transition is allowed
            if not self._is_valid_transition(market.state, new_state):
                logger.warning(
                    f"Invalid transition: {token_id} {market.state.value} -> {new_state.value}"
                )
                return False

            old_state = market.state
            market.state = new_state
            market.record_transition(new_state, reason)

            logger.info(
                f"Market transitioned: {token_id} | "
                f"{old_state.value} -> {new_state.value} | {reason}"
            )
            return True

    async def get_markets_by_state(self, state: MarketState) -> List[Market]:
        """
        Get all markets in a specific state.

        Args:
            state: Target state

        Returns:
            List of markets in that state
        """
        async with self._lock:
            return [m for m in self.markets.values() if m.state == state]

    async def update_price(self, token_id: str, bid: float, ask: float) -> None:
        """
        Update market prices from WebSocket feed.

        Args:
            token_id: Market token ID
            bid: Current bid price (0-1)
            ask: Current ask price (0-1)
        """
        async with self._lock:
            market = self.markets.get(token_id)
            if not market:
                logger.debug(f"Price update for unknown market: {token_id}")
                return

            market.current_bid = bid
            market.current_ask = ask
            market.last_update = datetime.utcnow()

            # Reset failure count on successful update
            if market.failure_count > 0:
                logger.info(
                    f"Market {token_id} feed recovered. "
                    f"Failure count reset from {market.failure_count} to 0"
                )
                market.failure_count = 0

    async def check_transitions(
        self,
    ) -> List[Tuple[str, MarketState, MarketState]]:
        """
        Check all markets for automatic state transitions.

        Performs state transitions based on:
        - Time to expiry
        - Price thresholds
        - Feed staleness
        - Failure counts

        Returns:
            List of (token_id, old_state, new_state) tuples for all transitions
        """
        transitions = []

        async with self._lock:
            for token_id, market in list(self.markets.items()):
                old_state = market.state
                new_state = await self._check_market_transitions(market)

                # Perform transition if needed
                if new_state != old_state:
                    market.state = new_state
                    market.record_transition(new_state, "auto-transition")
                    transitions.append((token_id, old_state, new_state))

                    log_message = (
                        f"Auto-transition: {token_id} | "
                        f"{old_state.value} -> {new_state.value}"
                    )

                    if new_state == MarketState.ELIGIBLE:
                        time_to_expiry = market.time_to_expiry().total_seconds()
                        log_message += f" | {time_to_expiry:.1f}s to expiry"

                    logger.info(log_message)

        return transitions

    async def _check_market_transitions(self, market: Market) -> MarketState:
        """
        Determine what state a market should be in.

        Implements all transition rules:
        - DISCOVERED → WATCHING: WebSocket subscription
        - WATCHING → ELIGIBLE: time < 60s AND price < max_buy_price
        - ELIGIBLE → EXECUTING: orders placed
        - EXECUTING → RECONCILING: market resolved
        - RECONCILING → DONE: P&L calculated
        - ANY → ON_HOLD: stale feed OR failure_count > 3
        - ON_HOLD → WATCHING: feed resumes

        Args:
            market: Market to check

        Returns:
            Target state for this market
        """
        current_state = market.state

        # Rule 1: Any state → ON_HOLD if stale or too many failures
        if market.is_stale(self.config.stale_feed_threshold_ms):
            if current_state != MarketState.ON_HOLD:
                logger.warning(f"Market {market.token_id} feed is stale")
                return MarketState.ON_HOLD

        if market.failure_count > self.config.max_failures_before_hold:
            if current_state != MarketState.ON_HOLD:
                logger.warning(
                    f"Market {market.token_id} failure_count={market.failure_count} "
                    f"> max={self.config.max_failures_before_hold}"
                )
                return MarketState.ON_HOLD

        # Rule 2: ON_HOLD → WATCHING if feed recovers
        if current_state == MarketState.ON_HOLD:
            if not market.is_stale() and market.failure_count <= self.config.max_failures_before_hold:
                logger.info(f"Market {market.token_id} feed recovered from ON_HOLD")
                return MarketState.WATCHING

        # Rule 3: DISCOVERED → WATCHING when we get first price update
        if current_state == MarketState.DISCOVERED:
            if market.last_update is not None:
                return MarketState.WATCHING

        # Rule 4: WATCHING → ELIGIBLE when time < threshold AND price < max_buy_price
        if current_state == MarketState.WATCHING:
            time_to_expiry = market.time_to_expiry().total_seconds()

            if time_to_expiry <= self.config.time_to_eligibility_sec:
                # Check price criteria
                if market.current_ask is not None:
                    if market.current_ask < self.config.max_buy_price:
                        return MarketState.ELIGIBLE
                    else:
                        logger.debug(
                            f"Market {market.token_id} price too high: "
                            f"ask={market.current_ask:.3f} > max={self.config.max_buy_price}"
                        )
                else:
                    # No price data yet, stay in WATCHING
                    pass

        # Rule 5: ELIGIBLE → EXECUTING when orders placed
        if current_state == MarketState.ELIGIBLE:
            if market.orders_placed > 0:
                return MarketState.EXECUTING

        # Rule 6: EXECUTING → RECONCILING when market resolves
        if current_state == MarketState.EXECUTING:
            if market.time_to_expiry().total_seconds() <= 0:
                return MarketState.RECONCILING

        # Rule 7: RECONCILING → DONE when P&L calculated
        # (P&L calculation is external, but we mark DONE after some time)
        if current_state == MarketState.RECONCILING:
            # In real implementation, check if P&L has been calculated
            # For now, assume it's done immediately
            return MarketState.DONE

        # No transition needed
        return current_state

    def _is_valid_transition(
        self, from_state: MarketState, to_state: MarketState
    ) -> bool:
        """
        Check if a state transition is valid.

        Most transitions are allowed, but some are explicitly blocked.

        Args:
            from_state: Current state
            to_state: Target state

        Returns:
            True if transition is valid
        """
        # DONE is terminal - can only transition out via remove
        if from_state == MarketState.DONE:
            return False

        # All other transitions are allowed
        return True

    async def mark_execution_started(
        self, token_id: str, capital_allocated: float
    ) -> bool:
        """
        Mark that execution has started for a market.

        Transitions ELIGIBLE -> EXECUTING and records capital allocation.

        Args:
            token_id: Market token ID
            capital_allocated: Amount of capital being used

        Returns:
            True if successful
        """
        async with self._lock:
            market = self.markets.get(token_id)
            if not market:
                return False

            if market.state != MarketState.ELIGIBLE:
                logger.warning(
                    f"Cannot mark execution: {token_id} not in ELIGIBLE state "
                    f"(current: {market.state.value})"
                )
                return False

            market.allocated_capital = capital_allocated
            market.orders_placed += 1
            market.state = MarketState.EXECUTING
            market.record_transition(MarketState.EXECUTING, "execution-started")

            logger.info(
                f"Execution started: {token_id} | Capital: ${capital_allocated:.2f} | "
                f"Orders: {market.orders_placed}"
            )
            return True

    async def mark_resolution(self, token_id: str, pnl: float) -> bool:
        """
        Mark that market has resolved and calculate P&L.

        Transitions to RECONCILING and records P&L.

        Args:
            token_id: Market token ID
            pnl: Realized profit/loss

        Returns:
            True if successful
        """
        async with self._lock:
            market = self.markets.get(token_id)
            if not market:
                return False

            market.pnl = pnl
            market.state = MarketState.RECONCILING
            market.record_transition(MarketState.RECONCILING, "resolution-detected")

            logger.info(
                f"Market resolved: {token_id} | P&L: ${pnl:+.2f} | "
                f"Total capital: ${market.allocated_capital:.2f}"
            )
            return True

    async def mark_done(self, token_id: str) -> bool:
        """
        Mark market as complete.

        Transitions RECONCILING -> DONE.

        Args:
            token_id: Market token ID

        Returns:
            True if successful
        """
        async with self._lock:
            market = self.markets.get(token_id)
            if not market:
                return False

            if market.state != MarketState.RECONCILING:
                logger.warning(
                    f"Cannot mark done: {token_id} not in RECONCILING state "
                    f"(current: {market.state.value})"
                )
                return False

            market.state = MarketState.DONE
            market.record_transition(MarketState.DONE, "completed")

            logger.info(
                f"Market marked done: {token_id} | Final P&L: ${market.pnl:+.2f} | "
                f"Total transitions: {len(market.transition_history)}"
            )
            return True

    async def mark_failure(self, token_id: str, reason: str = "") -> bool:
        """
        Increment failure counter for a market.

        If failures exceed threshold, automatically moves to ON_HOLD.

        Args:
            token_id: Market token ID
            reason: Reason for failure

        Returns:
            True if successful
        """
        async with self._lock:
            market = self.markets.get(token_id)
            if not market:
                return False

            market.failure_count += 1
            logger.warning(
                f"Market failure recorded: {token_id} | "
                f"Count: {market.failure_count} | Reason: {reason}"
            )

            # Auto-hold if threshold exceeded
            if market.failure_count > self.config.max_failures_before_hold:
                if market.state != MarketState.ON_HOLD:
                    market.state = MarketState.ON_HOLD
                    market.record_transition(
                        MarketState.ON_HOLD, f"too-many-failures ({market.failure_count})"
                    )
                    logger.error(
                        f"Market moved to ON_HOLD due to failures: {token_id} "
                        f"({market.failure_count}/{self.config.max_failures_before_hold})"
                    )

            return True

    async def get_stats(self) -> Dict[str, int]:
        """
        Get statistics on all tracked markets.

        Returns:
            Dictionary with counts by state
        """
        async with self._lock:
            stats = {}
            for state in MarketState:
                count = sum(1 for m in self.markets.values() if m.state == state)
                stats[state.value] = count

            stats["total"] = len(self.markets)
            return stats

    async def cleanup_old_done_markets(self) -> int:
        """
        Remove DONE markets older than max_hold_hours.

        Returns:
            Number of markets removed
        """
        removed = 0
        max_age = timedelta(hours=self.config.max_hold_hours)
        cutoff = datetime.utcnow() - max_age

        async with self._lock:
            to_remove = []
            for token_id, market in self.markets.items():
                if (
                    market.state == MarketState.DONE
                    and market.last_transition_time() is not None
                    and market.last_transition_time() < cutoff
                ):
                    to_remove.append(token_id)

            for token_id in to_remove:
                del self.markets[token_id]
                removed += 1

        if removed > 0:
            logger.info(f"Cleaned up {removed} old DONE markets")

        return removed
