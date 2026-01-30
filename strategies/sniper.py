"""
Sniper Strategy
===============

Buys assets below $0.99 in final seconds before settlement
when outcome is determined.

Inherits from BaseStrategy and integrates with centralized executor.
"""

import asyncio
import json
import logging
import aiohttp
import websockets
from datetime import datetime
from typing import Optional, Dict, Any

from strategies.base_strategy import BaseStrategy
from executor import OrderRequest
from config import GAMMA_API, CLOB_WS

logger = logging.getLogger(__name__)


class SniperStrategy(BaseStrategy):
    """Expiration sniping strategy"""

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

    async def run(self):
        """Main strategy loop"""
        logger.info(f"{self.name} strategy running")

        # If no token IDs provided, discover markets
        if not self.token_ids:
            logger.info("No token IDs provided - would integrate with market discovery")
            # TODO: Integrate with market_discovery module
            return

        # Connect to WebSocket and monitor
        while self.running:
            try:
                await self._connect_websocket()
            except Exception as e:
                logger.error(f"{self.name} WebSocket error: {e}")
                if self.running:
                    await asyncio.sleep(5)

    async def cleanup(self):
        """Cleanup resources"""
        logger.info(f"{self.name} cleanup complete")

    async def _connect_websocket(self):
        """Connect to CLOB WebSocket and stream prices"""
        ws_url = f"{CLOB_WS}market"

        subscribe_msg = {
            "type": "subscribe",
            "channel": "market",
            "assets_ids": self.token_ids,
        }

        async with websockets.connect(ws_url) as ws:
            logger.info(f"{self.name} WebSocket connected")
            await ws.send(json.dumps(subscribe_msg))

            # Heartbeat task
            async def heartbeat():
                while self.running:
                    try:
                        await ws.ping()
                        await asyncio.sleep(10)
                    except asyncio.CancelledError:
                        logger.debug(f"{self.name} heartbeat cancelled")
                        break
                    except Exception as e:
                        logger.error(f"{self.name} heartbeat failed: {e}")
                        break

            heartbeat_task = asyncio.create_task(heartbeat())

            # Message loop
            async for message in ws:
                if not self.running:
                    break

                try:
                    data = json.loads(message)
                    msg_type = data.get("type", "")

                    if msg_type in ("price_change", "book"):
                        await self._handle_price_update(data)

                except Exception as e:
                    logger.error(f"{self.name} message error: {e}")

            heartbeat_task.cancel()

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

        except Exception as e:
            logger.warning(f"{self.name} price update error: {e}")

    async def _check_execution_signal(self, token_id: str, best_ask: float):
        """Check if conditions met for sniping"""
        # Get market end time
        end_time = self.market_end_times.get(token_id)
        if not end_time:
            # Fetch market info
            await self._fetch_market_info(token_id)
            end_time = self.market_end_times.get(token_id)
            if not end_time:
                return

        # Calculate time remaining
        now = datetime.utcnow()
        time_remaining = (end_time - now).total_seconds()

        # Log countdown every 10 seconds
        if int(time_remaining) % 10 == 0:
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
            },
        )

        # Execute via centralized executor
        success = await self.executor.execute_order(order_request)

        if success:
            self.trades_executed += 1
            expected_profit = (1.0 - best_ask) * size
            self.total_profit += expected_profit
            self.total_invested += size

    async def _fetch_market_info(self, token_id: str):
        """Fetch market information for a token"""
        async with aiohttp.ClientSession() as session:
            url = f"{GAMMA_API}/markets"
            params = {"clob_token_ids": token_id}

            try:
                async with session.get(url, params=params) as response:
                    if response.status == 200:
                        markets = await response.json()
                        if markets:
                            market = markets[0]
                            self.monitored_markets[token_id] = market

                            # Parse end time
                            end_date = market.get("endDate") or market.get("end_date_iso")
                            if end_date:
                                end_time = datetime.fromisoformat(
                                    end_date.replace("Z", "+00:00")
                                ).replace(tzinfo=None)
                                self.market_end_times[token_id] = end_time
                                logger.info(
                                    f"{self.name} | Market {market.get('question', 'Unknown')} "
                                    f"ends at {end_time}"
                                )

            except Exception as e:
                logger.error(f"{self.name} failed to fetch market info: {e}")

    def _calculate_position_size(self, price: float) -> float:
        """Calculate position size based on config"""
        if self.config.dry_run:
            return 1.0  # $1 in dry run

        # Start small in live mode
        return min(self.config.position_size_pct * 10000, 10.0)  # Max $10 per trade initially
