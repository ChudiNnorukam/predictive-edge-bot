"""
Exit Manager Strategy
=====================

Monitors open positions and triggers exits based on:
- Profit targets (take_profit_price)
- Stop loss (stop_loss_price)
- Time-based exits (max_hold_seconds)

Phase 3 Implementation:
- Matches distinct-baguette's 99.9% position turnover
- Active position management for consistent profitability
"""

import asyncio
import aiohttp
import logging
import time
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

from strategies.base_strategy import BaseStrategy
from executor import OrderRequest
from config import DATA_API

logger = logging.getLogger(__name__)

# Default exit parameters
DEFAULT_TAKE_PROFIT_PCT = 0.01  # 1% profit target
DEFAULT_STOP_LOSS_PCT = 0.005  # 0.5% stop loss
DEFAULT_MAX_HOLD_SECONDS = 3600  # 1 hour max hold
DEFAULT_API_TIMEOUT = 30
CHECK_INTERVAL = 5  # Check positions every 5 seconds


class ExitManagerStrategy(BaseStrategy):
    """
    Active position exit management.

    Monitors all open positions and executes exits when:
    1. Price reaches take_profit_price (profit target)
    2. Price drops to stop_loss_price (stop loss)
    3. Position held for max_hold_seconds (time exit)
    """

    def __init__(
        self,
        config,
        executor,
        position_store,
        take_profit_pct: float = DEFAULT_TAKE_PROFIT_PCT,
        stop_loss_pct: float = DEFAULT_STOP_LOSS_PCT,
        max_hold_seconds: int = DEFAULT_MAX_HOLD_SECONDS,
    ):
        """
        Initialize exit manager.

        Args:
            config: Bot configuration
            executor: OrderExecutor instance
            position_store: PositionStore instance
            take_profit_pct: Default take profit percentage (0.01 = 1%)
            stop_loss_pct: Default stop loss percentage (0.005 = 0.5%)
            max_hold_seconds: Default max hold time in seconds
        """
        super().__init__(config, name="ExitManager")

        self.executor = executor
        self.position_store = position_store
        self.take_profit_pct = take_profit_pct
        self.stop_loss_pct = stop_loss_pct
        self.max_hold_seconds = max_hold_seconds

        # Price cache - reduced TTL for fast-moving markets
        self.current_prices: Dict[str, float] = {}
        self._price_cache_ttl = 3  # Refresh prices every 3 seconds (research-fill fix)
        self._last_price_fetch: float = 0
        self._price_fetch_failures: Dict[str, int] = {}  # Track failures per token
        self._max_price_failures = 5  # Force exit after N consecutive failures

        # Reusable HTTP session
        self._session: Optional[aiohttp.ClientSession] = None

        # Track exits
        self.exits_triggered = {
            "profit_target": 0,
            "stop_loss": 0,
            "time_exit": 0,
        }

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create reusable HTTP session"""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=DEFAULT_API_TIMEOUT)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def run(self):
        """Main strategy loop - monitor positions and trigger exits"""
        logger.info(f"{self.name} strategy running")
        logger.info(
            f"Exit params: take_profit={self.take_profit_pct:.1%}, "
            f"stop_loss={self.stop_loss_pct:.1%}, max_hold={self.max_hold_seconds}s"
        )

        iteration = 0
        while self.running:
            try:
                await self._check_positions()
                iteration += 1

                if iteration % 12 == 0:  # Log every minute
                    logger.info(
                        f"{self.name} | Iteration {iteration} | "
                        f"Exits: profit={self.exits_triggered['profit_target']}, "
                        f"stop={self.exits_triggered['stop_loss']}, "
                        f"time={self.exits_triggered['time_exit']}"
                    )

            except asyncio.CancelledError:
                logger.info(f"{self.name} cancelled")
                break
            except Exception as e:
                logger.error(f"{self.name} error: {e}")

            await asyncio.sleep(CHECK_INTERVAL)

    async def cleanup(self):
        """Cleanup resources"""
        if self._session is not None and not self._session.closed:
            await self._session.close()
            self._session = None
        logger.info(f"{self.name} cleanup complete")

    async def _check_positions(self):
        """Check all open positions for exit conditions"""
        # Get all open positions
        open_positions = await self.position_store.get_open_positions()

        if not open_positions:
            return

        # Refresh prices if needed
        await self._refresh_prices(open_positions)

        now = int(time.time())

        for position in open_positions:
            token_id = position["token_id"]
            entry_price = position["entry_price"]
            entry_time = position["entry_time"]
            size = position["size"]
            side = position["side"]

            # Get exit parameters (use position-specific or defaults)
            take_profit_price = position.get("take_profit_price")
            stop_loss_price = position.get("stop_loss_price")
            max_hold = position.get("max_hold_seconds") or self.max_hold_seconds

            # Calculate default exit prices if not set
            if take_profit_price is None:
                take_profit_price = entry_price * (1 + self.take_profit_pct)
            if stop_loss_price is None:
                stop_loss_price = entry_price * (1 - self.stop_loss_pct)

            # Get current price (research-fill: handle missing prices properly)
            current_price = self.current_prices.get(token_id)
            if current_price is None:
                # Track failure count
                failures = self._price_fetch_failures.get(token_id, 0) + 1
                self._price_fetch_failures[token_id] = failures

                if failures >= self._max_price_failures:
                    # Force time-based exit if we can't get prices
                    logger.warning(
                        f"{self.name} | FORCED EXIT | {token_id[:8]}... | "
                        f"Price fetch failed {failures} times, forcing exit"
                    )
                    exit_reason = "price_failure"
                    self.exits_triggered["time_exit"] += 1
                    await self._execute_exit(
                        token_id=token_id,
                        side=side,
                        size=size,
                        exit_price=entry_price,  # Use entry price as fallback
                        exit_reason=exit_reason,
                        entry_price=entry_price,
                    )
                continue

            # Reset failure count on successful price fetch
            self._price_fetch_failures[token_id] = 0

            # Calculate holding time
            hold_time = now - entry_time

            # Check exit conditions
            exit_reason = None
            exit_price = current_price

            # 1. Take profit
            if current_price >= take_profit_price:
                exit_reason = "profit_target"
                self.exits_triggered["profit_target"] += 1
                logger.info(
                    f"{self.name} | PROFIT TARGET | {token_id[:8]}... | "
                    f"Entry: ${entry_price:.3f} → Current: ${current_price:.3f} "
                    f"(+{(current_price/entry_price - 1)*100:.2f}%)"
                )

            # 2. Stop loss
            elif current_price <= stop_loss_price:
                exit_reason = "stop_loss"
                self.exits_triggered["stop_loss"] += 1
                logger.info(
                    f"{self.name} | STOP LOSS | {token_id[:8]}... | "
                    f"Entry: ${entry_price:.3f} → Current: ${current_price:.3f} "
                    f"({(current_price/entry_price - 1)*100:.2f}%)"
                )

            # 3. Time exit
            elif hold_time >= max_hold:
                exit_reason = "time_exit"
                self.exits_triggered["time_exit"] += 1
                logger.info(
                    f"{self.name} | TIME EXIT | {token_id[:8]}... | "
                    f"Held for {hold_time}s (max: {max_hold}s)"
                )

            # Execute exit if condition triggered
            if exit_reason:
                await self._execute_exit(
                    token_id=token_id,
                    side=side,
                    size=size,
                    exit_price=exit_price,
                    exit_reason=exit_reason,
                    entry_price=entry_price,
                )

    async def _refresh_prices(self, positions: List[Dict]):
        """Fetch current prices for all positions"""
        now = time.time()

        # Skip if recently fetched
        if (now - self._last_price_fetch) < self._price_cache_ttl:
            return

        session = await self._get_session()

        for position in positions:
            token_id = position["token_id"]

            try:
                # Fetch current market price from Polymarket API
                url = f"{DATA_API}/prices"
                params = {"token_id": token_id}

                async with session.get(url, params=params) as response:
                    if response.status == 200:
                        data = await response.json()
                        # Extract mid price or last price
                        price = data.get("price") or data.get("mid") or data.get("last")
                        if price:
                            self.current_prices[token_id] = float(price)
                    else:
                        # Fallback: try to get from orderbook
                        await self._fetch_price_from_orderbook(token_id)

            except asyncio.TimeoutError:
                logger.warning(f"{self.name} Timeout fetching price for {token_id[:8]}...")
            except Exception as e:
                logger.warning(f"{self.name} Error fetching price for {token_id[:8]}...: {e}")

        self._last_price_fetch = now

    async def _fetch_price_from_orderbook(self, token_id: str):
        """Fallback: fetch price from orderbook endpoint"""
        session = await self._get_session()

        try:
            url = f"{DATA_API}/book"
            params = {"token_id": token_id}

            async with session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    bids = data.get("bids", [])
                    asks = data.get("asks", [])

                    # Calculate mid price
                    best_bid = float(bids[0]["price"]) if bids else 0
                    best_ask = float(asks[0]["price"]) if asks else 1

                    if best_bid and best_ask:
                        mid_price = (best_bid + best_ask) / 2
                        self.current_prices[token_id] = mid_price

        except Exception as e:
            logger.warning(f"{self.name} Orderbook fallback failed for {token_id[:8]}...: {e}")

    async def _execute_exit(
        self,
        token_id: str,
        side: str,
        size: float,
        exit_price: float,
        exit_reason: str,
        entry_price: float,
    ):
        """Execute position exit via centralized executor"""
        self.signals_detected += 1

        # Calculate profit/loss
        if side == "YES":
            profit = (exit_price - entry_price) * size
        else:
            profit = (entry_price - exit_price) * size

        # Create sell order
        order_request = OrderRequest(
            token_id=token_id,
            side=side,
            action="SELL",
            size=size,
            strategy=self.name,
            price=exit_price,
            metadata={
                "exit_reason": exit_reason,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "profit": profit,
                "executed_at": datetime.now(timezone.utc).isoformat(),
            },
        )

        # Execute
        success = await self.executor.execute_order(order_request)

        if success:
            self.trades_executed += 1
            self.total_profit += profit
            self.total_invested -= size

            # Update position with exit reason
            await self.position_store.update_position(
                token_id=token_id,
                status="closed",
                metadata={"exit_reason": exit_reason, "exit_price": exit_price},
            )

            logger.info(
                f"{self.name} | EXIT EXECUTED | {token_id[:8]}... | "
                f"Reason: {exit_reason} | P/L: ${profit:+.2f}"
            )

    def get_metrics(self) -> Dict[str, Any]:
        """Get exit manager metrics"""
        base_metrics = super().get_metrics()
        base_metrics.update({
            "exits_profit_target": self.exits_triggered["profit_target"],
            "exits_stop_loss": self.exits_triggered["stop_loss"],
            "exits_time": self.exits_triggered["time_exit"],
            "total_exits": sum(self.exits_triggered.values()),
            "cached_prices": len(self.current_prices),
        })
        return base_metrics
