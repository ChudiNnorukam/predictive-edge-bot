"""
Sniper Strategy
===============

Buys assets below $0.99 in final seconds before settlement
when outcome is determined.

Inherits from BaseStrategy and integrates with centralized executor.

Phase 2 Improvements (Opus 4.5):
- HIGH-2: Session reuse (single aiohttp session)
- HIGH-3: Exponential backoff for reconnection
- HIGH-6: API timeouts on all requests
- MED-2: UTC timezone handling (datetime.now(timezone.utc))
- WebSocket connection timeout and reconnection
"""

import asyncio
import json
import logging
import aiohttp
import websockets
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from strategies.base_strategy import BaseStrategy
from executor import OrderRequest
from config import GAMMA_API, CLOB_WS

logger = logging.getLogger(__name__)

# Default timeouts
DEFAULT_API_TIMEOUT = 30
WEBSOCKET_TIMEOUT = 60
WEBSOCKET_PING_INTERVAL = 10


class SniperStrategy(BaseStrategy):
    """
    Expiration sniping strategy.

    Phase 2 implementation with:
    - Session reuse for API calls
    - Exponential backoff on WebSocket errors
    - API timeouts on all requests
    - UTC timezone handling
    """

    def __init__(
        self,
        config,
        executor,
        position_store,
        token_ids: Optional[list] = None,
    ):
        """
        Initialize sniper strategy

        Args:
            config: Bot configuration
            executor: OrderExecutor instance
            position_store: PositionStore instance
            token_ids: Optional list of token IDs to monitor
        """
        super().__init__(config, name="Sniper")

        self.executor = executor
        self.position_store = position_store
        self.token_ids = token_ids or []

        # Market state
        self.monitored_markets: Dict[str, Dict] = {}
        self.current_prices: Dict[str, float] = {}
        self.market_end_times: Dict[str, datetime] = {}

        # Reusable HTTP session
        self._session: Optional[aiohttp.ClientSession] = None

        # Exponential backoff state
        self._consecutive_errors = 0
        self._max_backoff = 300  # 5 minutes max

        # Track executed orders to prevent duplicate trades
        self._executed_tokens: set = set()

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create reusable HTTP session"""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=DEFAULT_API_TIMEOUT)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    def _calculate_backoff(self) -> float:
        """Calculate exponential backoff delay"""
        if self._consecutive_errors == 0:
            return 0
        delay = min(2 ** self._consecutive_errors, self._max_backoff)
        return delay

    def _reset_backoff(self):
        """Reset backoff counter after successful operation"""
        self._consecutive_errors = 0

    def _increment_backoff(self):
        """Increment backoff counter after error"""
        self._consecutive_errors += 1

    async def run(self):
        """Main strategy loop with exponential backoff"""
        logger.info(f"{self.name} strategy running")

        # If no token IDs provided, discover markets
        if not self.token_ids:
            logger.info("No token IDs provided - would integrate with market discovery")
            # TODO: Integrate with market_discovery module
            return

        # Connect to WebSocket and monitor with reconnection
        while self.running:
            try:
                # Apply backoff if needed
                backoff_delay = self._calculate_backoff()
                if backoff_delay > 0:
                    logger.warning(
                        f"{self.name} Backing off for {backoff_delay:.1f}s "
                        f"(consecutive errors: {self._consecutive_errors})"
                    )
                    await asyncio.sleep(backoff_delay)

                await self._connect_websocket()

                # Reset backoff on successful connection cycle
                self._reset_backoff()

            except asyncio.CancelledError:
                logger.info(f"{self.name} cancelled")
                break
            except websockets.exceptions.ConnectionClosed as e:
                logger.warning(f"{self.name} WebSocket closed: {e}")
                self._increment_backoff()
            except asyncio.TimeoutError:
                logger.error(f"{self.name} WebSocket timeout")
                self._increment_backoff()
            except Exception as e:
                logger.error(f"{self.name} WebSocket error: {e}")
                self._increment_backoff()

    async def cleanup(self):
        """Cleanup resources"""
        # Close HTTP session
        if self._session is not None and not self._session.closed:
            await self._session.close()
            self._session = None

        logger.info(f"{self.name} cleanup complete")

    async def _connect_websocket(self):
        """Connect to CLOB WebSocket and stream prices with timeout"""
        ws_url = f"{CLOB_WS}market"

        subscribe_msg = {
            "type": "subscribe",
            "channel": "market",
            "assets_ids": self.token_ids,
        }

        # Connect with timeout
        async with asyncio.timeout(WEBSOCKET_TIMEOUT):
            async with websockets.connect(
                ws_url,
                ping_interval=WEBSOCKET_PING_INTERVAL,
                ping_timeout=30,
            ) as ws:
                logger.info(f"{self.name} WebSocket connected to {ws_url}")
                await ws.send(json.dumps(subscribe_msg))

                # Heartbeat task
                async def heartbeat():
                    while self.running:
                        try:
                            await ws.ping()
                            await asyncio.sleep(WEBSOCKET_PING_INTERVAL)
                        except asyncio.CancelledError:
                            logger.debug(f"{self.name} heartbeat cancelled")
                            break
                        except Exception as e:
                            logger.error(f"{self.name} heartbeat failed: {e}")
                            break

                heartbeat_task = asyncio.create_task(heartbeat())

                try:
                    # Message loop
                    async for message in ws:
                        if not self.running:
                            break

                        try:
                            data = json.loads(message)
                            msg_type = data.get("type", "")

                            if msg_type in ("price_change", "book"):
                                await self._handle_price_update(data)

                        except json.JSONDecodeError as e:
                            logger.warning(f"{self.name} Invalid JSON: {e}")
                        except Exception as e:
                            logger.error(f"{self.name} message error: {e}")
                finally:
                    heartbeat_task.cancel()
                    try:
                        await heartbeat_task
                    except asyncio.CancelledError:
                        pass

    async def _handle_price_update(self, data: Dict[str, Any]):
        """Process price update and check for execution signal"""
        try:
            token_id = data.get("asset_id")
            if not token_id:
                return

            # Update current price
            if "asks" in data and data["asks"]:
                best_ask = float(data["asks"][0].get("price", 0))
                self.current_prices[token_id] = best_ask

                # Check if we should execute
                await self._check_execution_signal(token_id, best_ask)

        except (ValueError, TypeError) as e:
            logger.warning(f"{self.name} price update parse error: {e}")
        except Exception as e:
            logger.warning(f"{self.name} price update error: {e}")

    async def _check_execution_signal(self, token_id: str, best_ask: float):
        """Check if conditions met for sniping"""
        # Skip if already executed for this token
        if token_id in self._executed_tokens:
            return

        # Get market end time
        end_time = self.market_end_times.get(token_id)
        if not end_time:
            # Fetch market info
            await self._fetch_market_info(token_id)
            end_time = self.market_end_times.get(token_id)
            if not end_time:
                return

        # Calculate time remaining (using UTC)
        now = datetime.now(timezone.utc)
        # Ensure end_time is timezone-aware
        if end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=timezone.utc)
        time_remaining = (end_time - now).total_seconds()

        # Log countdown every 10 seconds
        if int(time_remaining) % 10 == 0 and time_remaining > 0:
            logger.info(
                f"{self.name} | {token_id[:8]}... | "
                f"Time: {time_remaining:.1f}s | Ask: ${best_ask:.3f}"
            )

        # Execution criteria
        if time_remaining > 1.0:
            return  # Too early

        if best_ask >= self.config.max_buy_price:
            return  # Price too high

        # Signal detected!
        self.signals_detected += 1

        logger.info("=" * 60)
        logger.info(f"{self.name} EXECUTION SIGNAL!")
        logger.info(f"Token: {token_id[:8]}... | Time: {time_remaining:.2f}s | Ask: ${best_ask:.3f}")
        logger.info("=" * 60)

        # Calculate position size
        size = self._calculate_position_size(best_ask)

        # Check if should execute
        if not self.should_execute_trade(size):
            logger.warning(f"{self.name} Trade blocked by risk limits")
            return

        # Create order request
        order_request = OrderRequest(
            token_id=token_id,
            side="YES",  # Assume YES based on price
            action="BUY",
            size=size,
            strategy=self.name,
            price=best_ask,
            metadata={
                "time_remaining": time_remaining,
                "signal_type": "expiration_snipe",
                "executed_at": datetime.now(timezone.utc).isoformat(),
            },
        )

        # Execute via centralized executor
        success = await self.executor.execute_order(order_request)

        if success:
            self.trades_executed += 1
            expected_profit = (1.0 - best_ask) * size
            self.total_profit += expected_profit
            self.total_invested += size

            # Mark as executed to prevent duplicate trades
            self._executed_tokens.add(token_id)

    async def _fetch_market_info(self, token_id: str):
        """Fetch market information for a token using reusable session"""
        session = await self._get_session()
        url = f"{GAMMA_API}/markets"
        params = {"clob_token_ids": token_id}

        try:
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    markets = await response.json()
                    if markets:
                        market = markets[0]
                        self.monitored_markets[token_id] = market

                        # Parse end time (handle multiple formats)
                        end_date = market.get("endDate") or market.get("end_date_iso")
                        if end_date:
                            # Parse ISO format, handle Z suffix
                            if end_date.endswith("Z"):
                                end_date = end_date[:-1] + "+00:00"
                            try:
                                end_time = datetime.fromisoformat(end_date)
                                # Ensure timezone-aware
                                if end_time.tzinfo is None:
                                    end_time = end_time.replace(tzinfo=timezone.utc)
                                self.market_end_times[token_id] = end_time
                                logger.info(
                                    f"{self.name} | Market '{market.get('question', 'Unknown')[:50]}...' "
                                    f"ends at {end_time.isoformat()}"
                                )
                            except ValueError as e:
                                logger.warning(f"{self.name} Failed to parse end date '{end_date}': {e}")
                else:
                    logger.warning(f"{self.name} API returned status {response.status}")

        except asyncio.TimeoutError:
            logger.error(f"{self.name} Timeout fetching market info for {token_id}")
        except aiohttp.ClientError as e:
            logger.error(f"{self.name} HTTP error fetching market info: {e}")
        except Exception as e:
            logger.error(f"{self.name} Failed to fetch market info: {e}")

    def _calculate_position_size(self, price: float) -> float:
        """Calculate position size based on config"""
        if self.config.dry_run:
            return 1.0  # $1 in dry run

        # Start small in live mode
        return min(self.config.position_size_pct * 10000, 10.0)  # Max $10 per trade initially
