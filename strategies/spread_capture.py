"""
Spread Capture Strategy
=======================

Implements a market-making style strategy inspired by distinct-baguette's approach:
- Buy at bid prices on both YES and NO sides
- Capture spread when combined cost < $1 (guaranteed profit on resolution)
- Alternative: Buy one side low, sell higher before resolution
- Exit all positions before market resolution

Key insight from distinct-baguette: $479k gains, only $809 losses across $45M volume.
This is achieved by NEVER holding directional risk to resolution - either:
1. Owning both sides (arbitrage), or
2. Exiting before resolution (spread trading)
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any, Tuple

import aiohttp

from strategies.base_strategy import BaseStrategy
from strategies.position_tracker import PositionTracker, Position
from strategies.order_manager import OrderManager, OrderStatus

logger = logging.getLogger(__name__)


@dataclass
class SpreadOpportunity:
    """Represents a spread capture opportunity."""

    token_id: str
    side: str  # YES or NO
    market_question: str
    market_expiry: Optional[datetime]

    # Prices
    bid: float
    ask: float
    mid: float
    spread_pct: float

    # Paired token (for arbitrage)
    paired_token_id: Optional[str] = None
    paired_bid: Optional[float] = None
    paired_ask: Optional[float] = None

    # Calculated opportunity
    entry_price: float = 0.0  # Price we can buy at (bid)
    exit_target: float = 0.0  # Price to exit at
    expected_profit_pct: float = 0.0

    # Arbitrage check
    combined_cost: Optional[float] = None  # Cost to buy both sides
    arbitrage_profit: Optional[float] = None  # If combined_cost < 1.0


@dataclass
class SpreadCaptureConfig:
    """Configuration for spread capture strategy."""

    # Entry criteria
    min_spread_pct: float = 2.0  # Minimum spread to consider (2%)
    max_spread_pct: float = 15.0  # Maximum spread (avoid illiquid)
    min_liquidity_usd: float = 100.0  # Minimum liquidity depth

    # Exit targets
    exit_target_pct: float = 2.0  # Target profit per trade (2%)
    stop_loss_pct: float = 5.0  # Max loss before exit (5%)
    max_hold_seconds: int = 600  # 10 minutes max hold

    # Pre-expiry safety
    exit_before_expiry_seconds: int = 60  # Exit 60s before market closes
    no_entry_before_expiry_seconds: int = 120  # No new entries in final 2 min

    # Position sizing (percentage-based for compounding)
    position_size_pct: float = 0.25  # 25% of bankroll per position
    max_exposure_pct: float = 0.75  # 75% max total exposure
    max_concurrent_positions: int = 5  # Max simultaneous positions
    # Safety caps (0 = no cap, uses percentage only)
    max_position_usd: float = 0  # Hard cap per position
    max_total_exposure_usd: float = 0  # Hard cap total exposure

    # Arbitrage mode
    enable_arbitrage: bool = True  # Try to buy both sides
    max_arbitrage_cost: float = 0.98  # Max combined cost for arbitrage

    # Scanning
    scan_interval_seconds: float = 5.0  # How often to scan markets
    market_types: List[str] = None  # Filter to specific market types

    def __post_init__(self):
        if self.market_types is None:
            self.market_types = ["crypto"]  # Focus on 15-min crypto markets


class SpreadCaptureStrategy(BaseStrategy):
    """
    Spread Capture Strategy - Market Making Style Trading.

    Inspired by distinct-baguette's approach:
    - Nearly zero losses by never holding to resolution with directional risk
    - Two modes:
      1. Arbitrage: Buy both YES and NO when combined < $1
      2. Spread Trading: Buy at bid, sell at ask before resolution

    Features:
    - Automatic spread detection across crypto markets
    - Position tracking with P&L
    - Time-based exit before market resolution
    - Integration with existing executor and risk management
    """

    def __init__(
        self,
        config,
        executor,
        position_tracker: PositionTracker,
        order_manager: OrderManager,
        spread_config: Optional[SpreadCaptureConfig] = None,
    ):
        """
        Initialize spread capture strategy.

        Args:
            config: Bot configuration (credentials, dry_run, etc.)
            executor: OrderExecutor for placing orders
            position_tracker: PositionTracker for managing positions
            order_manager: OrderManager for limit orders
            spread_config: Strategy-specific configuration
        """
        super().__init__(config, name="SpreadCapture")

        self.executor = executor
        self.position_tracker = position_tracker
        self.order_manager = order_manager
        self.spread_config = spread_config or SpreadCaptureConfig()

        # Market data cache
        self.market_cache: Dict[str, Dict] = {}  # token_id -> market data
        self.price_cache: Dict[str, Dict] = {}  # token_id -> {bid, ask, last_update}
        self.paired_tokens: Dict[str, str] = {}  # token_id -> paired_token_id

        # Scanning state
        self.last_scan_time = 0
        self.markets_scanned = 0

        # Statistics
        self.opportunities_found = 0
        self.arbitrage_opportunities = 0
        self.spread_trades_executed = 0

        # Bankroll tracking for percentage-based sizing
        self.starting_bankroll = config.starting_bankroll
        self.current_bankroll = config.starting_bankroll

        logger.info(
            f"SpreadCaptureStrategy initialized | "
            f"Min spread: {self.spread_config.min_spread_pct}% | "
            f"Exit target: {self.spread_config.exit_target_pct}% | "
            f"Position size: {self.spread_config.position_size_pct*100:.0f}% of bankroll | "
            f"Starting bankroll: ${self.starting_bankroll:.2f}"
        )

    def calculate_position_size(self) -> float:
        """
        Calculate position size based on current bankroll.

        Uses percentage-based sizing for compounding:
        - position_size_pct of current bankroll
        - Capped by max_position_usd if set (> 0)

        Returns:
            Position size in USDC
        """
        # Update current bankroll with realized P&L
        self.current_bankroll = self.starting_bankroll + self.position_tracker.total_realized_pnl

        # Calculate percentage-based size
        size = self.current_bankroll * self.spread_config.position_size_pct

        # Apply hard cap if set
        if self.spread_config.max_position_usd > 0:
            size = min(size, self.spread_config.max_position_usd)

        # Minimum viable size
        size = max(size, 1.0)

        return size

    def calculate_max_exposure(self) -> float:
        """
        Calculate maximum total exposure based on current bankroll.

        Returns:
            Max exposure in USDC
        """
        self.current_bankroll = self.starting_bankroll + self.position_tracker.total_realized_pnl

        exposure = self.current_bankroll * self.spread_config.max_exposure_pct

        # Apply hard cap if set
        if self.spread_config.max_total_exposure_usd > 0:
            exposure = min(exposure, self.spread_config.max_total_exposure_usd)

        return exposure

    async def run(self):
        """Main strategy loop."""
        self.start()

        logger.info("=" * 60)
        logger.info("Spread Capture Strategy Starting")
        logger.info("=" * 60)
        logger.info(f"Mode: {'Arbitrage + Spread' if self.spread_config.enable_arbitrage else 'Spread Only'}")
        logger.info(f"Dry Run: {self.config.dry_run}")
        logger.info("=" * 60)

        try:
            # Start background tasks
            tasks = [
                asyncio.create_task(self._market_scan_loop()),
                asyncio.create_task(self._position_management_loop()),
                asyncio.create_task(self._order_sync_loop()),
            ]

            # Wait for all tasks
            await asyncio.gather(*tasks)

        except asyncio.CancelledError:
            logger.info("Strategy cancelled")
        except Exception as e:
            logger.error(f"Strategy error: {e}", exc_info=True)
        finally:
            await self.cleanup()

    async def cleanup(self):
        """Cleanup resources on shutdown."""
        logger.info("Cleaning up spread capture strategy...")

        # Cancel all pending orders
        cancelled = await self.order_manager.cancel_all()
        logger.info(f"Cancelled {cancelled} pending orders")

        # Log final stats
        metrics = self.get_strategy_metrics()
        logger.info(f"Final metrics: {metrics}")

    # =========================================================================
    # Market Scanning
    # =========================================================================

    async def _market_scan_loop(self):
        """Background loop to scan for spread opportunities."""
        while self.running:
            try:
                await self._scan_markets()
                await asyncio.sleep(self.spread_config.scan_interval_seconds)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Scan loop error: {e}")
                await asyncio.sleep(5)

    async def _scan_markets(self):
        """Scan markets for spread opportunities."""
        now = time.time()

        # Fetch eligible markets
        markets = await self._fetch_eligible_markets()
        self.markets_scanned = len(markets)

        for market in markets:
            token_id = market.get("token_id")
            if not token_id:
                continue

            # Check if we already have a position
            existing = await self.position_tracker.get_position(token_id)
            if existing:
                continue

            # Check capacity (percentage-based)
            current_exposure = await self.position_tracker.get_total_exposure()
            max_exposure = self.calculate_max_exposure()
            if current_exposure >= max_exposure:
                continue

            positions = await self.position_tracker.get_all_positions()
            if len(positions) >= self.spread_config.max_concurrent_positions:
                continue

            # Analyze opportunity
            opportunity = await self._analyze_opportunity(market)
            if opportunity:
                await self._execute_opportunity(opportunity)

        self.last_scan_time = now

    async def _fetch_eligible_markets(self) -> List[Dict]:
        """Fetch markets eligible for spread capture."""
        from config import GAMMA_API

        now = datetime.now(timezone.utc)
        min_expiry = now + timedelta(seconds=self.spread_config.no_entry_before_expiry_seconds)
        max_expiry = now + timedelta(hours=2)

        async with aiohttp.ClientSession() as session:
            url = f"{GAMMA_API}/markets"
            params = {
                "active": "true",
                "closed": "false",
                "limit": 100,
            }

            try:
                async with session.get(url, params=params) as response:
                    if response.status != 200:
                        return []

                    markets = await response.json()
                    eligible = []

                    for market in markets:
                        # Parse expiry
                        end_date_str = market.get("endDate") or market.get("end_date_iso")
                        if not end_date_str:
                            continue

                        try:
                            end_date = datetime.fromisoformat(
                                end_date_str.replace("Z", "+00:00")
                            )
                        except ValueError:
                            continue

                        # Filter by expiry window
                        if not (min_expiry <= end_date <= max_expiry):
                            continue

                        # Filter by market type (crypto 15-min)
                        question = market.get("question", "").lower()
                        is_crypto = any(kw in question for kw in ["bitcoin", "btc", "eth", "ethereum", "solana", "crypto"])

                        if "crypto" in self.spread_config.market_types and not is_crypto:
                            continue

                        # Extract tokens
                        tokens = market.get("tokens", [])
                        for token in tokens:
                            token_id = token.get("token_id")
                            outcome = token.get("outcome", "")

                            if token_id:
                                eligible.append({
                                    "token_id": token_id,
                                    "condition_id": market.get("conditionId", ""),
                                    "question": market.get("question", ""),
                                    "end_date": end_date,
                                    "outcome": outcome,
                                    "neg_risk": market.get("negRisk", False),
                                    "tokens": tokens,
                                })

                                # Cache market and pair mapping
                                self.market_cache[token_id] = market
                                for other in tokens:
                                    if other.get("token_id") != token_id:
                                        self.paired_tokens[token_id] = other.get("token_id")

                    return eligible

            except Exception as e:
                logger.error(f"Failed to fetch markets: {e}")
                return []

    async def _analyze_opportunity(self, market: Dict) -> Optional[SpreadOpportunity]:
        """
        Analyze a market for spread capture opportunity.

        Args:
            market: Market data dictionary

        Returns:
            SpreadOpportunity if opportunity found, None otherwise
        """
        token_id = market["token_id"]
        end_date = market.get("end_date")

        # Fetch current orderbook
        book = await self._fetch_orderbook(token_id)
        if not book:
            return None

        bid = book.get("bid", 0)
        ask = book.get("ask", 0)

        if bid <= 0 or ask <= 0:
            return None

        # Calculate spread
        spread = ask - bid
        spread_pct = (spread / bid) * 100 if bid > 0 else 0

        # Check spread criteria
        if spread_pct < self.spread_config.min_spread_pct:
            return None
        if spread_pct > self.spread_config.max_spread_pct:
            return None

        # Create opportunity
        opportunity = SpreadOpportunity(
            token_id=token_id,
            side=market.get("outcome", "YES"),
            market_question=market.get("question", ""),
            market_expiry=end_date,
            bid=bid,
            ask=ask,
            mid=(bid + ask) / 2,
            spread_pct=spread_pct,
            entry_price=bid,  # Buy at bid
            exit_target=bid * (1 + self.spread_config.exit_target_pct / 100),
        )

        # Check for arbitrage opportunity (buy both sides)
        if self.spread_config.enable_arbitrage:
            paired_token = self.paired_tokens.get(token_id)
            if paired_token:
                paired_book = await self._fetch_orderbook(paired_token)
                if paired_book:
                    paired_ask = paired_book.get("ask", 0)
                    if paired_ask > 0:
                        # Combined cost to own both sides
                        combined = ask + paired_ask
                        if combined < self.spread_config.max_arbitrage_cost:
                            opportunity.paired_token_id = paired_token
                            opportunity.paired_ask = paired_ask
                            opportunity.combined_cost = combined
                            opportunity.arbitrage_profit = 1.0 - combined
                            self.arbitrage_opportunities += 1
                            logger.info(
                                f"[ARBITRAGE] {token_id[:16]}... | "
                                f"YES: ${ask:.3f} + NO: ${paired_ask:.3f} = ${combined:.3f} | "
                                f"Profit: ${opportunity.arbitrage_profit:.4f}"
                            )

        # Calculate expected profit for spread trade
        opportunity.expected_profit_pct = self.spread_config.exit_target_pct

        self.opportunities_found += 1
        return opportunity

    async def _fetch_orderbook(self, token_id: str) -> Optional[Dict]:
        """Fetch current orderbook for a token."""
        from config import CLOB_HOST

        # Check cache (< 1 second old)
        cached = self.price_cache.get(token_id)
        if cached and time.time() - cached.get("last_update", 0) < 1.0:
            return cached

        async with aiohttp.ClientSession() as session:
            url = f"{CLOB_HOST}/book"
            params = {"token_id": token_id}

            try:
                async with session.get(url, params=params) as response:
                    if response.status != 200:
                        return None

                    data = await response.json()

                    bids = data.get("bids", [])
                    asks = data.get("asks", [])

                    bid = float(bids[0].get("price", 0)) if bids else 0
                    ask = float(asks[0].get("price", 0)) if asks else 0

                    result = {
                        "bid": bid,
                        "ask": ask,
                        "last_update": time.time(),
                        "bid_size": float(bids[0].get("size", 0)) if bids else 0,
                        "ask_size": float(asks[0].get("size", 0)) if asks else 0,
                    }

                    self.price_cache[token_id] = result
                    return result

            except Exception as e:
                logger.debug(f"Failed to fetch orderbook for {token_id[:16]}: {e}")
                return None

    # =========================================================================
    # Opportunity Execution
    # =========================================================================

    async def _execute_opportunity(self, opp: SpreadOpportunity):
        """Execute a spread capture opportunity."""
        logger.info(
            f"[SpreadCapture] Executing opportunity: {opp.token_id[:16]}... | "
            f"Bid: ${opp.bid:.3f} | Ask: ${opp.ask:.3f} | Spread: {opp.spread_pct:.1f}%"
        )

        # Determine position size (percentage-based for compounding)
        position_size = self.calculate_position_size()
        current_exposure = await self.position_tracker.get_total_exposure()
        max_exposure = self.calculate_max_exposure()
        available_exposure = max_exposure - current_exposure

        size = min(position_size, available_exposure)

        if size < 1:  # Minimum viable position
            logger.debug(f"Position size too small: ${size:.2f}")
            return

        logger.info(
            f"[SpreadCapture] Position sizing: ${size:.2f} "
            f"({self.spread_config.position_size_pct*100:.0f}% of ${self.current_bankroll:.2f} bankroll)"
        )

        # Place limit buy at bid price
        order_id = await self.order_manager.place_buy(
            token_id=opp.token_id,
            side=opp.side,
            price=opp.bid,
            size=size,
            metadata={
                "opportunity_type": "arbitrage" if opp.arbitrage_profit else "spread",
                "spread_pct": opp.spread_pct,
                "exit_target": opp.exit_target,
            },
        )

        if not order_id:
            logger.warning(f"Failed to place buy order for {opp.token_id[:16]}...")
            return

        # Track position (pending fill)
        await self.position_tracker.add_position(
            token_id=opp.token_id,
            side=opp.side,
            entry_price=opp.bid,
            size=size,
            take_profit_price=opp.exit_target,
            stop_loss_price=opp.bid * (1 - self.spread_config.stop_loss_pct / 100),
            max_hold_seconds=self.spread_config.max_hold_seconds,
            order_id=order_id,
            market_question=opp.market_question,
            market_expiry=opp.market_expiry,
        )

        self.spread_trades_executed += 1

        # If arbitrage opportunity, also buy the paired side
        if opp.arbitrage_profit and opp.paired_token_id:
            paired_order = await self.order_manager.place_buy(
                token_id=opp.paired_token_id,
                side="NO" if opp.side == "YES" else "YES",
                price=opp.paired_ask,
                size=size,
                metadata={"opportunity_type": "arbitrage_pair"},
            )

            if paired_order:
                await self.position_tracker.add_position(
                    token_id=opp.paired_token_id,
                    side="NO" if opp.side == "YES" else "YES",
                    entry_price=opp.paired_ask,
                    size=size,
                    max_hold_seconds=self.spread_config.max_hold_seconds,
                    order_id=paired_order,
                    market_question=opp.market_question,
                    market_expiry=opp.market_expiry,
                )

        logger.info(
            f"[SpreadCapture] Order placed: {order_id} | "
            f"{opp.side} @ ${opp.bid:.3f} x ${size:.2f}"
        )

    # =========================================================================
    # Position Management
    # =========================================================================

    async def _position_management_loop(self):
        """Background loop to manage open positions."""
        while self.running:
            try:
                await self._manage_positions()
                await asyncio.sleep(1)  # Check every second
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Position management error: {e}")
                await asyncio.sleep(1)

    async def _manage_positions(self):
        """Check positions for exit conditions."""
        # Get positions needing exit
        needs_exit = await self.position_tracker.get_positions_needing_exit(
            exit_before_expiry_seconds=self.spread_config.exit_before_expiry_seconds
        )

        for position, reason in needs_exit:
            await self._exit_position(position, reason)

        # Update prices for all positions
        positions = await self.position_tracker.get_all_positions()
        for pos in positions:
            book = await self._fetch_orderbook(pos.token_id)
            if book:
                # For selling, we look at bid (what we can sell at)
                current_price = book.get("bid", 0)
                await self.position_tracker.update_position(
                    pos.token_id,
                    current_price=current_price,
                )

    async def _exit_position(self, position: Position, reason: str):
        """Exit a position."""
        logger.info(
            f"[SpreadCapture] Exiting position: {position.token_id[:16]}... | "
            f"Reason: {reason} | P&L: ${position.unrealized_pnl:.2f}"
        )

        # Cancel any pending orders for this market
        await self.order_manager.cancel_all_for_market(position.token_id)

        # Place market sell order
        if position.current_price > 0:
            order_id = await self.order_manager.place_sell(
                token_id=position.token_id,
                side=position.side,
                price=position.current_price * 0.99,  # Slightly below bid for quick fill
                size=position.size,
                metadata={"exit_reason": reason},
            )

            if order_id:
                # Close position in tracker
                closed = await self.position_tracker.close_position(
                    position.token_id,
                    exit_price=position.current_price,
                    reason=reason,
                )

                if closed:
                    self.total_profit += closed.unrealized_pnl
                    logger.info(
                        f"[SpreadCapture] Position closed: {position.token_id[:16]}... | "
                        f"Realized P&L: ${closed.unrealized_pnl:.2f}"
                    )

    # =========================================================================
    # Order Sync
    # =========================================================================

    async def _order_sync_loop(self):
        """Background loop to sync orders with exchange."""
        while self.running:
            try:
                # Sync order states
                changes = await self.order_manager.sync_with_exchange()
                if changes > 0:
                    logger.debug(f"Order sync: {changes} changes detected")

                # Cancel stale orders
                stale = await self.order_manager.cancel_stale_orders()
                if stale > 0:
                    logger.info(f"Cancelled {stale} stale orders")

                # Cleanup old completed orders
                await self.order_manager.cleanup_completed()

                await asyncio.sleep(5)  # Sync every 5 seconds

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Order sync error: {e}")
                await asyncio.sleep(5)

    # =========================================================================
    # Metrics
    # =========================================================================

    def get_strategy_metrics(self) -> Dict[str, Any]:
        """Get strategy-specific metrics."""
        base_metrics = self.get_metrics()

        return {
            **base_metrics,
            "opportunities_found": self.opportunities_found,
            "arbitrage_opportunities": self.arbitrage_opportunities,
            "spread_trades_executed": self.spread_trades_executed,
            "markets_scanned": self.markets_scanned,
            "position_metrics": self.position_tracker.get_metrics(),
            "order_metrics": self.order_manager.get_metrics(),
        }
