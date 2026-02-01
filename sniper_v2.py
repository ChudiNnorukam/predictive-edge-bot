#!/usr/bin/env python3
"""
sniper_v2.py - Enhanced Multi-Market Expiration Sniping Bot
============================================================

Integrates with scaling modules:
- MarketStateMachine: Lifecycle tracking (DISCOVERED → WATCHING → ELIGIBLE → EXECUTING → DONE)
- RiskManager: Kill switches, circuit breakers, exposure limits
- CapitalAllocator: Position sizing with per-market and total limits
- MetricsCollector: Execution latency, fill rates, P&L tracking

Strategy: Buy assets trading below $0.99 in the final seconds before
settlement when the outcome is already determined.

Usage:
    # Single market mode (legacy)
    python sniper_v2.py --token-id <TOKEN_ID>

    # Multi-market mode (new)
    python sniper_v2.py --multi --max-markets 5
"""

import asyncio
import json
import signal
import sys
import logging
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
import argparse

import aiohttp
import websockets
from websockets.client import WebSocketClientProtocol
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import MarketOrderArgs, OrderType, ApiCreds, PartialCreateOrderOptions
from py_clob_client.order_builder.constants import BUY

# Core config
from config import load_config, CLOB_HOST, CLOB_WS, GAMMA_API, LOG_FORMAT, LOG_DATE_FORMAT

# Scaling modules
from core import Market, MarketState, MarketStateMachine, SchedulerConfig
from risk import (
    KillSwitchManager,
    KillSwitchConfig,
    CircuitBreakerRegistry,
    CircuitBreakerConfig,
    ExposureManager,
    ExposureConfig,
    RiskManager,
)
from capital import CapitalAllocator, CapitalConfig, AllocationResult
from metrics import MetricsCollector, MetricsConfig, TradeMetrics

# Utilities
from utils.trade_logger import get_trade_logger

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    datefmt=LOG_DATE_FORMAT,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/sniper_v2.log"),
    ],
)
logger = logging.getLogger(__name__)


@dataclass
class SniperConfig:
    """Configuration for enhanced sniper"""

    # Execution parameters
    max_buy_price: float = 0.99
    min_price_threshold: float = 0.50  # Don't trade if price below this
    execution_window_seconds: float = 1.0  # Execute within this window of expiry

    # Multi-market mode
    multi_market_mode: bool = False
    max_concurrent_markets: int = 5
    scan_interval_seconds: int = 60

    # Risk settings
    risk_config: KillSwitchConfig = None
    circuit_breaker_config: CircuitBreakerConfig = None
    exposure_config: ExposureConfig = None

    # Capital settings
    capital_config: CapitalConfig = None

    # Metrics
    metrics_config: MetricsConfig = None

    def __post_init__(self):
        self.risk_config = self.risk_config or KillSwitchConfig()
        self.circuit_breaker_config = self.circuit_breaker_config or CircuitBreakerConfig()
        self.exposure_config = self.exposure_config or ExposureConfig()
        self.capital_config = self.capital_config or CapitalConfig()
        self.metrics_config = self.metrics_config or MetricsConfig()


class EnhancedSniperBot:
    """
    Enhanced Polymarket Expiration Sniping Bot with scaling module integration.

    Features:
    - Market state machine tracking
    - Risk management (kill switches, circuit breakers, exposure limits)
    - Capital allocation with per-market and total limits
    - Metrics collection for observability
    - Multi-market concurrent execution support
    """

    def __init__(
        self,
        bot_config,
        sniper_config: SniperConfig,
        token_id: Optional[str] = None,
    ):
        """
        Initialize enhanced sniper.

        Args:
            bot_config: Base bot configuration (credentials, network)
            sniper_config: Sniper-specific configuration
            token_id: Single token ID (legacy mode) or None for multi-market
        """
        self.bot_config = bot_config
        self.sniper_config = sniper_config
        self.single_token_id = token_id

        self.running = False
        self.start_time: Optional[datetime] = None

        # Initialize scaling modules
        self._init_scaling_modules()

        # CLOB client
        self.client = self._init_client()

        # WebSocket management
        self.ws: Optional[WebSocketClientProtocol] = None
        self.subscribed_markets: Dict[str, float] = {}  # token_id -> last_update

        # Per-market state (price data)
        self.market_prices: Dict[str, Dict[str, float]] = {}  # token_id -> {bid, ask, last}

        # Trade logging
        self.trade_logger = get_trade_logger()

        # Stats
        self.trades_executed = 0
        self.trades_blocked_by_risk = 0
        self.trades_blocked_by_capital = 0
        self.total_profit = 0.0

        logger.info("EnhancedSniperBot initialized")

    def _init_scaling_modules(self):
        """Initialize all scaling modules"""
        config = self.sniper_config

        # Market state machine with scheduler config
        scheduler_config = SchedulerConfig(
            time_to_eligibility_sec=int(config.execution_window_seconds),
            stale_feed_threshold_ms=config.risk_config.stale_feed_threshold_ms if config.risk_config else 500,
            max_buy_price=config.max_buy_price,
        )
        self.state_machine = MarketStateMachine(scheduler_config)

        # Risk management stack
        self.kill_switches = KillSwitchManager(config.risk_config)
        self.circuit_breakers = CircuitBreakerRegistry(config.circuit_breaker_config)
        self.exposure_manager = ExposureManager(
            config.exposure_config,
            initial_bankroll=self.bot_config.starting_bankroll,
        )
        self.risk_manager = RiskManager(
            self.kill_switches,
            self.circuit_breakers,
            self.exposure_manager,
        )

        # Capital allocation
        self.capital_allocator = CapitalAllocator(
            config.capital_config,
            initial_bankroll=self.bot_config.starting_bankroll,
        )

        # Metrics collection
        self.metrics_collector = MetricsCollector(config.metrics_config)

        logger.info("Scaling modules initialized")

    def _init_client(self) -> ClobClient:
        """Initialize the Polymarket CLOB client"""
        sig_type = int(os.getenv("SIGNATURE_TYPE", "2"))

        if sig_type == 0:
            client = ClobClient(
                host=CLOB_HOST,
                key=self.bot_config.private_key,
                chain_id=self.bot_config.chain_id,
                signature_type=0,
            )
        else:
            client = ClobClient(
                host=CLOB_HOST,
                key=self.bot_config.private_key,
                chain_id=self.bot_config.chain_id,
                signature_type=sig_type,
                funder=self.bot_config.wallet_address,
            )

        if self.bot_config.clob_api_key and self.bot_config.clob_secret:
            creds = ApiCreds(
                api_key=self.bot_config.clob_api_key,
                api_secret=self.bot_config.clob_secret,
                api_passphrase=self.bot_config.clob_passphrase,
            )
            client.set_api_creds(creds)
        else:
            creds = client.create_or_derive_api_creds()
            client.set_api_creds(creds)

        return client

    # =========================================================================
    # Market Discovery and State Management
    # =========================================================================

    async def discover_markets(self) -> List[Dict[str, Any]]:
        """
        Discover markets expiring soon from Gamma API.

        Returns:
            List of market dictionaries matching criteria
        """
        now = datetime.now(timezone.utc)
        min_expiry = now + timedelta(seconds=60)
        max_expiry = now + timedelta(hours=1)

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
                        logger.warning(f"Gamma API returned {response.status}")
                        return []

                    markets = await response.json()
                    eligible = []

                    for market in markets:
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
                        if min_expiry <= end_date <= max_expiry:
                            # Get token IDs from tokens array
                            tokens = market.get("tokens", [])
                            for token in tokens:
                                token_id = token.get("token_id")
                                if token_id:
                                    eligible.append({
                                        "token_id": token_id,
                                        "question": market.get("question", ""),
                                        "end_date": end_date,
                                        "neg_risk": market.get("negRisk", False),
                                        "outcome": token.get("outcome", ""),
                                    })

                    logger.info(f"Discovered {len(eligible)} markets expiring soon")
                    return eligible

            except Exception as e:
                logger.error(f"Market discovery failed: {e}")
                return []

    async def add_market_to_state_machine(self, market_data: Dict[str, Any]) -> bool:
        """
        Add discovered market to state machine.

        Args:
            market_data: Market data from discovery

        Returns:
            True if market was added, False if already tracked
        """
        token_id = market_data["token_id"]

        # Check if already tracked (access internal dict)
        if token_id in self.state_machine.markets:
            return False

        # Create Market object
        market = Market(
            token_id=token_id,
            condition_id=market_data.get("condition_id", ""),
            question=market_data.get("question", ""),
            end_time=market_data.get("end_date"),  # API field -> internal field
            is_neg_risk=market_data.get("neg_risk", False),
        )

        # Add to state machine (starts in DISCOVERED state)
        await self.state_machine.add_market(market)
        logger.info(f"Added market to state machine: {token_id[:16]}...")

        return True

    # =========================================================================
    # Price Updates and WebSocket
    # =========================================================================

    async def subscribe_to_market(self, token_id: str):
        """Subscribe to price updates for a market"""
        if token_id in self.subscribed_markets:
            return

        self.subscribed_markets[token_id] = time.time()
        self.market_prices[token_id] = {"bid": 0.0, "ask": 0.0, "last": 0.0}

        # Send subscribe message if connected
        if self.ws:
            subscribe_msg = {
                "type": "subscribe",
                "channel": "market",
                "assets_ids": [token_id],
            }
            await self.ws.send(json.dumps(subscribe_msg))
            logger.debug(f"Subscribed to {token_id[:16]}...")

    async def handle_price_update(self, data: Dict[str, Any]):
        """Handle incoming price update from WebSocket"""
        asset_id = data.get("asset_id")
        if not asset_id or asset_id not in self.subscribed_markets:
            return

        prices = self.market_prices.get(asset_id, {})

        try:
            if "bids" in data and data["bids"]:
                prices["bid"] = float(data["bids"][0].get("price", 0))
            if "asks" in data and data["asks"]:
                prices["ask"] = float(data["asks"][0].get("price", 0))
            if "price" in data:
                prices["last"] = float(data["price"])
            elif prices.get("bid") and prices.get("ask"):
                prices["last"] = (prices["bid"] + prices["ask"]) / 2

            self.market_prices[asset_id] = prices
            self.subscribed_markets[asset_id] = time.time()

            # Update state machine with new price
            await self.state_machine.update_price(
                asset_id,
                prices.get("bid", 0),
                prices.get("ask", 0),
            )

        except Exception as e:
            logger.warning(f"Error parsing price update: {e}")

    async def connect_websocket(self):
        """Connect to WebSocket and stream prices"""
        ws_url = f"{CLOB_WS}market"
        logger.info(f"Connecting to WebSocket: {ws_url}")

        try:
            async with websockets.connect(ws_url) as ws:
                self.ws = ws
                logger.info("WebSocket connected")

                # Subscribe to all tracked markets
                for token_id in self.subscribed_markets:
                    subscribe_msg = {
                        "type": "subscribe",
                        "channel": "market",
                        "assets_ids": [token_id],
                    }
                    await ws.send(json.dumps(subscribe_msg))

                # Heartbeat task
                async def heartbeat():
                    while self.running:
                        try:
                            await ws.ping()
                            await asyncio.sleep(10)
                        except:
                            break

                heartbeat_task = asyncio.create_task(heartbeat())

                # Message processing loop
                async for message in ws:
                    if not self.running:
                        break

                    try:
                        data = json.loads(message)
                        msg_type = data.get("type", "")

                        if msg_type in ("price_change", "book"):
                            await self.handle_price_update(data)

                    except Exception as e:
                        logger.error(f"Error processing message: {e}")

                heartbeat_task.cancel()

        except Exception as e:
            logger.error(f"WebSocket error: {e}")
            raise
        finally:
            self.ws = None

    # =========================================================================
    # Execution Logic with Risk and Capital Integration
    # =========================================================================

    def should_execute(self, market: Market, prices: Dict[str, float]) -> bool:
        """
        Determine if trade should be executed based on timing and price.

        Args:
            market: Market object with end_time
            prices: Current bid/ask/last prices

        Returns:
            True if execution criteria met
        """
        if not market.end_time:
            return False

        now = datetime.now(timezone.utc)
        time_remaining = (market.end_time - now).total_seconds()

        # Must be within execution window
        if time_remaining > self.sniper_config.execution_window_seconds:
            return False

        # Check price criteria
        ask = prices.get("ask", 0)
        last = prices.get("last", 0)

        if ask >= self.sniper_config.max_buy_price:
            return False

        if last <= self.sniper_config.min_price_threshold:
            return False

        return True

    async def pre_execution_checks(
        self,
        token_id: str,
        amount: float,
    ) -> tuple[bool, str]:
        """
        Run all pre-execution checks (risk + capital).

        Args:
            token_id: Market token ID
            amount: Requested trade amount

        Returns:
            (can_execute, reason) tuple
        """
        # 1. Risk manager checks (kill switches, circuit breakers, exposure)
        feed_time = datetime.now(timezone.utc)
        can_trade, reason = await self.risk_manager.pre_execution_check(
            market_id=token_id,
            amount=amount,
            feed_last_update=feed_time,
        )

        if not can_trade:
            self.trades_blocked_by_risk += 1
            return False, f"Risk check failed: {reason}"

        # 2. Capital allocation
        result, allocated = await self.capital_allocator.request_allocation(
            token_id,
            amount,
        )

        if result != AllocationResult.SUCCESS:
            self.trades_blocked_by_capital += 1
            return False, f"Capital allocation failed: {result.value}"

        return True, f"Approved: ${allocated:.2f}"

    async def execute_trade(
        self,
        market: Market,
        prices: Dict[str, float],
    ) -> bool:
        """
        Execute trade with full risk/capital integration.

        Args:
            market: Market to trade
            prices: Current prices

        Returns:
            True if trade executed successfully
        """
        token_id = market.token_id
        ask = prices.get("ask", 0)
        last = prices.get("last", 0)

        # Determine side and size
        side = "YES" if last > 0.50 else "NO"
        base_size = 1.0  # Start small

        # Pre-execution checks
        can_execute, reason = await self.pre_execution_checks(token_id, base_size)

        if not can_execute:
            logger.warning(f"[{token_id[:16]}] Execution blocked: {reason}")

            # Record circuit breaker failure
            await self.circuit_breakers.record_failure(token_id)

            # Transition to ON_HOLD
            await self.state_machine.transition(
                token_id,
                MarketState.ON_HOLD,
                reason,
            )
            return False

        # Transition to EXECUTING
        await self.state_machine.mark_execution_started(token_id)

        # Record execution start time for metrics
        exec_start = time.time()

        logger.info("=" * 50)
        logger.info(f"EXECUTING TRADE: {token_id[:16]}...")
        logger.info(f"Side: {side} | Ask: ${ask:.3f} | Size: ${base_size:.2f}")
        logger.info("=" * 50)

        # Log opportunity
        self.trade_logger.log_opportunity(
            token_id=token_id,
            market_question=market.question,
            current_price=last,
            time_remaining_seconds=0,
            bid=prices.get("bid", 0),
            ask=ask,
            spread=ask - prices.get("bid", 0),
            is_neg_risk=market.neg_risk,
        )

        # Execute (dry run or live)
        success = False
        error_msg = None

        if self.bot_config.dry_run:
            logger.info(f"[DRY RUN] Would buy {side} at ${ask:.3f}")
            success = True
        else:
            try:
                order_args = MarketOrderArgs(
                    token_id=token_id,
                    amount=base_size,
                    side=BUY,
                )

                if market.neg_risk:
                    options = PartialCreateOrderOptions(neg_risk=True)
                    signed_order = self.client.create_market_order(order_args, options)
                else:
                    signed_order = self.client.create_market_order(order_args)

                response = self.client.post_order(signed_order, OrderType.FOK)

                if response:
                    success = True
                    expected_profit = (1.0 - ask) * base_size
                    self.total_profit += expected_profit
                    logger.info(f"Trade executed! Expected profit: ${expected_profit:.4f}")

            except Exception as e:
                error_msg = str(e)
                logger.error(f"Trade execution failed: {e}")

        # Record execution time
        exec_time_ms = (time.time() - exec_start) * 1000

        # Update metrics
        trade_metrics = TradeMetrics(
            timestamp=datetime.now(timezone.utc),
            market_id=token_id,
            attempted=True,
            filled=success,
            fill_amount=base_size if success else 0.0,
            total_latency_ms=exec_time_ms,
            entry_price=ask,
            actual_pnl=(1.0 - ask) * base_size if success else 0,
        )
        await self.metrics_collector.record_trade(trade_metrics)

        # Log execution result
        self.trade_logger.log_execution(
            token_id=token_id,
            side=side,
            size=base_size,
            price=ask,
            order_type="FOK",
            success=success,
            execution_time_ms=exec_time_ms,
            error_message=error_msg,
        )

        # Update state machine
        if success:
            self.trades_executed += 1
            await self.circuit_breakers.record_success(token_id)
            # Mark resolution with P&L
            await self.state_machine.mark_resolution(
                token_id,
                pnl=(1.0 - ask) * base_size,
            )
        else:
            await self.circuit_breakers.record_failure(token_id)
            # Mark failure (may transition to ON_HOLD if too many failures)
            await self.state_machine.mark_failure(
                token_id,
                reason=error_msg or "Trade execution failed",
            )

        # Release capital allocation
        await self.capital_allocator.release_allocation(token_id)

        return success

    # =========================================================================
    # Main Loop
    # =========================================================================

    async def process_markets(self):
        """
        Process all markets in state machine, checking for execution opportunities.
        """
        # Check state transitions
        transitions = await self.state_machine.check_transitions()
        for market_id, old_state, new_state in transitions:
            logger.debug(f"[{market_id[:16]}] {old_state.value} → {new_state.value}")

        # Get eligible markets
        eligible_markets = self.state_machine.get_eligible_markets()

        for market in eligible_markets:
            prices = self.market_prices.get(market.token_id, {})

            if self.should_execute(market, prices):
                await self.execute_trade(market, prices)

    async def market_discovery_loop(self):
        """Background task for discovering new markets (multi-market mode)"""
        while self.running:
            try:
                markets = await self.discover_markets()

                # Limit concurrent markets
                current_count = len(self.state_machine.get_all_markets())
                available_slots = self.sniper_config.max_concurrent_markets - current_count

                for market_data in markets[:available_slots]:
                    added = await self.add_market_to_state_machine(market_data)
                    if added:
                        await self.subscribe_to_market(market_data["token_id"])

            except Exception as e:
                logger.error(f"Discovery loop error: {e}")

            await asyncio.sleep(self.sniper_config.scan_interval_seconds)

    async def execution_loop(self):
        """Main execution loop checking for trade opportunities"""
        while self.running:
            try:
                await self.process_markets()
            except Exception as e:
                logger.error(f"Execution loop error: {e}")

            await asyncio.sleep(0.01)  # 10ms tick

    async def run(self):
        """Main bot entry point"""
        self.running = True
        self.start_time = datetime.now(timezone.utc)

        logger.info("=" * 60)
        logger.info("Enhanced Sniper Bot Starting")
        logger.info("=" * 60)
        logger.info(f"Mode: {'Multi-Market' if self.sniper_config.multi_market_mode else 'Single Market'}")
        logger.info(f"Dry Run: {self.bot_config.dry_run}")
        logger.info(f"Max Buy Price: ${self.sniper_config.max_buy_price}")
        logger.info("=" * 60)

        # Log session start
        self.trade_logger.log_session_start({
            "mode": "multi" if self.sniper_config.multi_market_mode else "single",
            "dry_run": self.bot_config.dry_run,
            "max_buy_price": self.sniper_config.max_buy_price,
        })

        try:
            # Single market mode (legacy)
            if self.single_token_id:
                market_info = await self._fetch_single_market_info(self.single_token_id)
                if market_info:
                    await self.add_market_to_state_machine(market_info)
                    await self.subscribe_to_market(self.single_token_id)

            # Start background tasks
            tasks = [
                asyncio.create_task(self.execution_loop()),
            ]

            if self.sniper_config.multi_market_mode:
                tasks.append(asyncio.create_task(self.market_discovery_loop()))

            # WebSocket connection loop with reconnection
            while self.running:
                try:
                    await self.connect_websocket()
                except Exception as e:
                    logger.error(f"WebSocket connection lost: {e}")
                    if self.running:
                        logger.info("Reconnecting in 5s...")
                        await asyncio.sleep(5)

            # Cancel background tasks
            for task in tasks:
                task.cancel()

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Fatal error: {e}", exc_info=True)
        finally:
            await self._print_final_stats()

    async def _fetch_single_market_info(self, token_id: str) -> Optional[Dict[str, Any]]:
        """Fetch info for a single market"""
        async with aiohttp.ClientSession() as session:
            url = f"{GAMMA_API}/markets"
            params = {"clob_token_ids": token_id}

            async with session.get(url, params=params) as response:
                if response.status == 200:
                    markets = await response.json()
                    if markets:
                        market = markets[0]
                        end_date_str = market.get("endDate") or market.get("end_date_iso")
                        end_date = None
                        if end_date_str:
                            try:
                                end_date = datetime.fromisoformat(
                                    end_date_str.replace("Z", "+00:00")
                                )
                            except ValueError:
                                pass

                        return {
                            "token_id": token_id,
                            "question": market.get("question", ""),
                            "end_date": end_date,
                            "neg_risk": market.get("negRisk", False),
                        }
        return None

    async def _print_final_stats(self):
        """Print final statistics"""
        runtime = 0
        if self.start_time:
            runtime = (datetime.now(timezone.utc) - self.start_time).total_seconds()

        logger.info("=" * 60)
        logger.info("Enhanced Sniper Bot Stopped")
        logger.info("=" * 60)
        logger.info(f"Runtime: {runtime / 60:.1f} minutes")
        logger.info(f"Trades Executed: {self.trades_executed}")
        logger.info(f"Blocked by Risk: {self.trades_blocked_by_risk}")
        logger.info(f"Blocked by Capital: {self.trades_blocked_by_capital}")
        logger.info(f"Total Profit: ${self.total_profit:.4f}")
        logger.info("=" * 60)

        # Metrics summary
        metrics = await self.metrics_collector.get_summary()
        if metrics:
            logger.info("Metrics Summary:")
            logger.info(f"  Fill Rate: {metrics.get('fill_rate', 0):.1%}")
            logger.info(f"  Avg Latency: {metrics.get('avg_latency_ms', 0):.1f}ms")
            logger.info(f"  P95 Latency: {metrics.get('p95_latency_ms', 0):.1f}ms")

        # Log session end
        self.trade_logger.log_session_end({
            "trades_executed": self.trades_executed,
            "blocked_by_risk": self.trades_blocked_by_risk,
            "blocked_by_capital": self.trades_blocked_by_capital,
            "total_profit": self.total_profit,
        })

    def stop(self):
        """Stop the bot gracefully"""
        logger.info("Stopping Enhanced Sniper...")
        self.running = False


async def main():
    parser = argparse.ArgumentParser(
        description="Enhanced Polymarket Expiration Sniping Bot"
    )
    parser.add_argument(
        "--token-id", "-t",
        help="Token ID to monitor (single market mode)",
    )
    parser.add_argument(
        "--multi", "-m",
        action="store_true",
        help="Enable multi-market mode",
    )
    parser.add_argument(
        "--max-markets",
        type=int,
        default=5,
        help="Max concurrent markets in multi-market mode",
    )
    parser.add_argument(
        "--max-buy-price",
        type=float,
        default=0.99,
        help="Maximum buy price threshold",
    )

    args = parser.parse_args()

    # Validate args
    if not args.token_id and not args.multi:
        parser.error("Either --token-id or --multi is required")

    # Load config
    try:
        bot_config = load_config()
    except ValueError as e:
        logger.error(f"Config error: {e}")
        sys.exit(1)

    # Create sniper config
    sniper_config = SniperConfig(
        max_buy_price=args.max_buy_price,
        multi_market_mode=args.multi,
        max_concurrent_markets=args.max_markets,
    )

    # Create bot
    bot = EnhancedSniperBot(
        bot_config=bot_config,
        sniper_config=sniper_config,
        token_id=args.token_id,
    )

    # Signal handlers
    def signal_handler(sig, frame):
        bot.stop()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Run
    try:
        await bot.run()
    except KeyboardInterrupt:
        bot.stop()


if __name__ == "__main__":
    asyncio.run(main())
