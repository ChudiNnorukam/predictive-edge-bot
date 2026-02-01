"""
Multi-Market Scheduler
======================

Orchestrates monitoring and execution across many markets concurrently.
Enforces selection rules, timing constraints, and risk limits.

Key responsibilities:
- Monitor up to 50 markets in watchlist (Layer A)
- Execute up to 5 markets concurrently (Layer B)
- Manage T-60s → T-0 execution windows
- Enforce concurrency and capital limits
- Handle state transitions and market lifecycle

Phase transitions:
WATCHING → ELIGIBLE (< 60s to expiry) → EXECUTING → DONE
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Dict, Set, List

from executor import OrderRequest, OrderExecutor
from scheduler.execution_window import ExecutionWindow

logger = logging.getLogger(__name__)


@dataclass
class SchedulerConfig:
    """Configuration for multi-market scheduler"""

    # Concurrency
    max_watchlist_size: int = 50        # Layer A: markets to monitor
    max_active_executions: int = 5      # Layer B: concurrent executions

    # Timing thresholds
    eligible_window_seconds: int = 60   # Move WATCHING→ELIGIBLE at < 60s
    execution_window_seconds: int = 3   # Final execution window (T-3 to T-0)
    priming_window_seconds: int = 15    # Preparation window (T-15 to T-3)

    # Selection criteria
    max_spread_percent: float = 5.0     # Max bid-ask spread %
    min_liquidity_usd: float = 100.0    # Min order book depth
    max_price_threshold: float = 0.99   # Won't pay more than this
    min_probability: float = 0.95       # Outcome probability threshold

    # Risk
    stale_feed_threshold_ms: int = 500  # Mark ON_HOLD if feed older
    max_failure_count: int = 3          # Circuit breaker threshold

    # Performance
    tick_interval_ms: int = 10          # Main loop interval


class MultiMarketScheduler:
    """
    Orchestrates monitoring and execution across many markets concurrently.

    Layer A (Watchlist): Up to 50 markets in WATCHING or ELIGIBLE states
    Layer B (Execution): Up to 5 markets in concurrent execution (EXECUTING)

    State machine:
    - WATCHING: Market added to watchlist, monitoring price/expiry
    - ELIGIBLE: Time to expiry < 60s, ready for execution
    - EXECUTING: Order in flight (T-60 to T-0)
    - DONE: Market resolved, reconciled
    - ON_HOLD: Temporary pause (stale feed, circuit breaker)
    """

    class MarketState(str):
        """Market state labels"""
        WATCHING = "watching"
        ELIGIBLE = "eligible"
        EXECUTING = "executing"
        DONE = "done"
        ON_HOLD = "on_hold"

    def __init__(
        self,
        config: SchedulerConfig,
        executor: OrderExecutor,
    ):
        """
        Initialize the scheduler.

        Args:
            config: SchedulerConfig instance
            executor: OrderExecutor for trade execution
        """
        self.config = config
        self.executor = executor

        # Market tracking (Layer A + B)
        self.watchlist: Dict[str, dict] = {}  # token_id -> market_data
        self.market_states: Dict[str, str] = {}  # token_id -> MarketState
        self.execution_windows: Dict[str, ExecutionWindow] = {}  # token_id -> window
        self.market_failures: Dict[str, int] = {}  # token_id -> failure_count

        # Concurrency control
        self._running = False
        self._tasks: List[asyncio.Task] = []
        self._execution_semaphore = asyncio.Semaphore(config.max_active_executions)

        # Metrics
        self.metrics = {
            "total_added": 0,
            "total_removed": 0,
            "total_executed": 0,
            "total_failed": 0,
            "total_resolved": 0,
        }

    async def start(self) -> None:
        """
        Start the scheduler main loop.

        Runs continuously, processing markets until stopped.
        """
        if self._running:
            logger.warning("Scheduler already running")
            return

        self._running = True
        logger.info("Scheduler starting")

        try:
            # Start main loop task
            main_loop = asyncio.create_task(self._main_loop())
            self._tasks.append(main_loop)

            # Wait for cancellation
            await asyncio.gather(*self._tasks)
        except asyncio.CancelledError:
            logger.info("Scheduler main loop cancelled")
        except Exception as e:
            logger.error(f"Scheduler error: {e}", exc_info=True)
        finally:
            self._running = False

    async def stop(self) -> None:
        """
        Graceful shutdown of the scheduler.

        Cancels all tasks and waits for completion.
        """
        logger.info("Scheduler stopping")
        self._running = False

        # Cancel all tasks
        for task in self._tasks:
            if not task.done():
                task.cancel()

        # Wait for cancellation
        await asyncio.gather(*self._tasks, return_exceptions=True)
        logger.info("Scheduler stopped")

    async def add_market_to_watchlist(self, market: dict) -> bool:
        """
        Add market to watchlist if criteria met.

        Args:
            market: Market data dict with token_id, expiry_timestamp, etc.

        Returns:
            True if added, False if rejected
        """
        # Check capacity
        if len(self.watchlist) >= self.config.max_watchlist_size:
            logger.warning(
                f"Watchlist full ({self.config.max_watchlist_size}), "
                f"cannot add {market['token_id']}"
            )
            return False

        # Check criteria
        if not self.meets_watchlist_criteria(market):
            logger.debug(f"Market {market['token_id']} does not meet watchlist criteria")
            return False

        token_id = market["token_id"]
        self.watchlist[token_id] = market
        self.market_states[token_id] = self.MarketState.WATCHING
        self.market_failures[token_id] = 0
        self.execution_windows[token_id] = ExecutionWindow(
            token_id, market["expiry_timestamp"]
        )
        self.metrics["total_added"] += 1

        logger.info(f"Added {token_id} to watchlist")
        return True

    async def remove_market(self, token_id: str) -> None:
        """
        Remove market from all tracking structures.

        Args:
            token_id: Market token ID to remove
        """
        if token_id not in self.watchlist:
            return

        del self.watchlist[token_id]
        del self.market_states[token_id]
        del self.execution_windows[token_id]
        del self.market_failures[token_id]
        self.metrics["total_removed"] += 1

        logger.info(f"Removed {token_id} from tracking")

    async def _main_loop(self) -> None:
        """
        Core scheduling loop runs continuously.

        Each iteration:
        1. Check state transitions (WATCHING → ELIGIBLE)
        2. Process eligible markets (ELIGIBLE → EXECUTING)
        3. Manage execution windows (T-60 → T-0)
        4. Reconcile resolved markets (EXECUTING → DONE)
        """
        tick_interval = self.config.tick_interval_ms / 1000.0

        while self._running:
            try:
                start_time = time.time()

                # Process all markets
                await self._check_state_transitions()
                await self._process_eligible_markets()
                await self._handle_active_executions()
                await self._reconcile_resolved_markets()

                # Maintain tick rate
                elapsed = time.time() - start_time
                sleep_time = max(0, tick_interval - elapsed)
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Main loop error: {e}", exc_info=True)
                await asyncio.sleep(0.1)

    async def _check_state_transitions(self) -> None:
        """
        Check for transitions: WATCHING → ELIGIBLE.

        When time to expiry drops below eligible_window_seconds,
        move market to ELIGIBLE state.
        """
        now = datetime.now(timezone.utc).timestamp()

        for token_id in list(self.watchlist.keys()):
            if self.market_states[token_id] != self.MarketState.WATCHING:
                continue

            market = self.watchlist[token_id]
            tte = market["expiry_timestamp"] - now

            if tte < self.config.eligible_window_seconds:
                self.market_states[token_id] = self.MarketState.ELIGIBLE
                logger.info(f"Market {token_id} ELIGIBLE (TTE: {tte:.2f}s)")

    async def _process_eligible_markets(self) -> None:
        """
        Move eligible markets to execution if capacity allows.

        Respects max_active_executions concurrency limit.
        """
        eligible = [
            token_id
            for token_id, state in self.market_states.items()
            if state == self.MarketState.ELIGIBLE
        ]

        for token_id in eligible:
            # Check if we have capacity
            active_count = sum(
                1 for s in self.market_states.values()
                if s == self.MarketState.EXECUTING
            )
            if active_count >= self.config.max_active_executions:
                break

            # Transition to EXECUTING
            self.market_states[token_id] = self.MarketState.EXECUTING
            logger.info(f"Market {token_id} EXECUTING")

    async def _handle_active_executions(self) -> None:
        """
        Manage execution windows for markets in EXECUTING state.

        Orchestrates order preparation, priming, and execution.
        """
        executing = [
            token_id
            for token_id, state in self.market_states.items()
            if state == self.MarketState.EXECUTING
        ]

        # Process each executing market
        tasks = [
            self._execute_trade_lifecycle(token_id)
            for token_id in executing
        ]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _execute_trade_lifecycle(self, token_id: str) -> None:
        """
        Execute the full trade lifecycle for a market.

        Manages phases: PREPARATION → PRIMING → EXECUTION → POST_RESOLUTION
        """
        window = self.execution_windows[token_id]
        market = self.watchlist[token_id]

        try:
            # Check phase and take appropriate action
            phase = window.current_phase()

            if window.should_prepare_order():
                # Phase: PREPARATION (T-60 to T-15)
                order_data = await self._prepare_order(token_id, market)
                if order_data:
                    window.mark_order_prepared(order_data)
                else:
                    self._record_failure(token_id)

            elif window.should_prime():
                # Phase: PRIMING (T-15 to T-3)
                # Pre-validate order, ensure wallet is unlocked
                # For now, this is a no-op (order already prepared)
                pass

            elif window.should_execute():
                # Phase: EXECUTION (T-3 to T-0)
                success = await self._execute_trade(token_id, market, window)
                window.mark_order_sent()
                if success:
                    self.metrics["total_executed"] += 1
                else:
                    self._record_failure(token_id)
                    self.metrics["total_failed"] += 1

        except Exception as e:
            logger.error(f"Error in trade lifecycle for {token_id}: {e}", exc_info=True)
            self._record_failure(token_id)

    async def _prepare_order(self, token_id: str, market: dict) -> Optional[dict]:
        """
        Prepare order (calculate size, validate price).

        Args:
            token_id: Market token ID
            market: Market data

        Returns:
            Order data dict or None if preparation failed
        """
        # Validate market meets execution criteria
        if not self.meets_execution_criteria(market):
            logger.warning(f"Market {token_id} does not meet execution criteria")
            return None

        # Calculate order size and price
        # This is a placeholder - would integrate with capital allocator
        order_size = market.get("size", 100)
        order_price = market.get("price", 0.95)

        return {
            "token_id": token_id,
            "side": market.get("side", "YES"),
            "action": market.get("action", "BUY"),
            "size": order_size,
            "price": order_price,
        }

    async def _execute_trade(
        self,
        token_id: str,
        market: dict,
        window: ExecutionWindow
    ) -> bool:
        """
        Send FOK order via executor.

        Args:
            token_id: Market token ID
            market: Market data
            window: ExecutionWindow instance

        Returns:
            True if order sent successfully
        """
        if not window.order_prepared:
            logger.warning(f"Market {token_id} has no prepared order")
            return False

        order_data = window.order_prepared
        request = OrderRequest(
            token_id=token_id,
            side=order_data["side"],
            action=order_data["action"],
            size=order_data["size"],
            price=order_data.get("price"),
            strategy="multi_market_scheduler",
            metadata={"market": token_id, "window": window.get_debug_info()},
        )

        try:
            success = await self.executor.execute_order(request)
            if success:
                logger.info(f"Order executed for {token_id}")
            return success
        except Exception as e:
            logger.error(f"Order execution failed for {token_id}: {e}", exc_info=True)
            return False

    async def _reconcile_resolved_markets(self) -> None:
        """
        Post-resolution: cancel unfilled orders, reconcile, mark DONE.

        Moves markets from EXECUTING to DONE after resolution window passes.
        """
        executing = [
            token_id
            for token_id, state in self.market_states.items()
            if state == self.MarketState.EXECUTING
        ]

        for token_id in executing:
            window = self.execution_windows[token_id]
            if window.is_resolved():
                await self._reconcile_market(token_id)
                self.market_states[token_id] = self.MarketState.DONE
                self.metrics["total_resolved"] += 1
                logger.info(f"Market {token_id} DONE")

    async def _reconcile_market(self, token_id: str) -> None:
        """
        Reconcile market after resolution.

        Args:
            token_id: Market token ID
        """
        # Placeholder for reconciliation logic
        # Would:
        # 1. Check order fill status
        # 2. Cancel unfilled orders
        # 3. Calculate P&L
        # 4. Update position tracking
        logger.debug(f"Reconciling market {token_id}")

    def meets_watchlist_criteria(self, market: dict) -> bool:
        """
        Check if market meets watchlist criteria.

        Args:
            market: Market data dict

        Returns:
            True if market can be added to watchlist
        """
        # Check time to expiry (must have at least 2 minutes)
        now = datetime.now(timezone.utc).timestamp()
        tte = market.get("expiry_timestamp", 0) - now
        if tte < 120:
            return False

        # Check liquidity
        liquidity = market.get("liquidity_usd", 0)
        if liquidity < self.config.min_liquidity_usd:
            return False

        # Check spread
        spread = market.get("spread_percent", 0)
        if spread > self.config.max_spread_percent:
            return False

        return True

    def meets_execution_criteria(self, market: dict) -> bool:
        """
        Check if market meets execution criteria.

        Args:
            market: Market data dict

        Returns:
            True if market can be executed
        """
        # Check outcome probability
        probability = market.get("probability", 0)
        if probability < self.config.min_probability:
            return False

        # Check price is within threshold
        price = market.get("price", 0)
        if price > self.config.max_price_threshold:
            return False

        # Check feed freshness
        last_update_ms = market.get("last_update_ms", 0)
        age_ms = int(datetime.now(timezone.utc).timestamp() * 1000) - last_update_ms
        if age_ms > self.config.stale_feed_threshold_ms:
            return False

        return True

    def _record_failure(self, token_id: str) -> None:
        """
        Record a failure for a market.

        After max_failure_count failures, circuit breaker triggers.

        Args:
            token_id: Market token ID
        """
        self.market_failures[token_id] += 1
        failure_count = self.market_failures[token_id]

        if failure_count >= self.config.max_failure_count:
            self.market_states[token_id] = self.MarketState.ON_HOLD
            logger.warning(
                f"Market {token_id} circuit breaker triggered "
                f"({failure_count}/{self.config.max_failure_count} failures)"
            )

    def get_metrics(self) -> Dict:
        """
        Get scheduler metrics.

        Returns:
            Dictionary with performance metrics
        """
        return {
            **self.metrics,
            "watchlist_size": len(self.watchlist),
            "executing_count": sum(
                1 for s in self.market_states.values()
                if s == self.MarketState.EXECUTING
            ),
            "eligible_count": sum(
                1 for s in self.market_states.values()
                if s == self.MarketState.ELIGIBLE
            ),
            "on_hold_count": sum(
                1 for s in self.market_states.values()
                if s == self.MarketState.ON_HOLD
            ),
        }

    def get_market_state(self, token_id: str) -> Optional[str]:
        """
        Get current state of a market.

        Args:
            token_id: Market token ID

        Returns:
            MarketState or None if market not tracked
        """
        return self.market_states.get(token_id)

    def get_debug_info(self) -> dict:
        """Get detailed debugging information"""
        return {
            "running": self._running,
            "metrics": self.get_metrics(),
            "markets": {
                token_id: {
                    "state": self.market_states.get(token_id),
                    "failures": self.market_failures.get(token_id, 0),
                    "window": self.execution_windows[token_id].get_debug_info(),
                }
                for token_id in self.watchlist.keys()
            },
        }
