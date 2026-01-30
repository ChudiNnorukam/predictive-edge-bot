#!/usr/bin/env python3
"""
sniper.py - Polymarket Expiration Sniping Bot
==============================================

Strategy: Buy assets trading below $0.99 in the final seconds before
settlement when the outcome is already determined.

Usage:
    python sniper.py --token-id <TOKEN_ID>
"""

import asyncio
import json
import signal
import sys
import logging
from datetime import datetime
from typing import Optional, Dict, Any
import argparse

import aiohttp
import websockets
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import MarketOrderArgs, OrderType, ApiCreds
from py_clob_client.order_builder.constants import BUY

from config import load_config, CLOB_HOST, CLOB_WS, GAMMA_API, LOG_FORMAT, LOG_DATE_FORMAT

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    datefmt=LOG_DATE_FORMAT,
    handlers=[logging.StreamHandler(), logging.FileHandler("logs/sniper.log")]
)
logger = logging.getLogger(__name__)


class SniperBot:
    """Polymarket Expiration Sniping Bot"""

    def __init__(self, config, token_id: str, condition_id: Optional[str] = None):
        self.config = config
        self.token_id = token_id
        self.condition_id = condition_id

        self.running = False
        self.current_bid = 0.0
        self.current_ask = 0.0
        self.last_price = 0.0
        self.market_end_time: Optional[datetime] = None

        self.trades_executed = 0
        self.total_profit = 0.0
        self.signals_detected = 0

        self.client = self._init_client()

    def _init_client(self) -> ClobClient:
        """Initialize the Polymarket CLOB client"""
        client = ClobClient(
            host=CLOB_HOST,
            key=self.config.private_key,
            chain_id=self.config.chain_id,
            signature_type=1,
            funder=self.config.wallet_address,
        )

        if self.config.clob_api_key and self.config.clob_secret:
            creds = ApiCreds(
                api_key=self.config.clob_api_key,
                api_secret=self.config.clob_secret,
                api_passphrase=self.config.clob_passphrase,
            )
            client.set_api_creds(creds)
        else:
            creds = client.create_or_derive_api_creds()
            client.set_api_creds(creds)

        return client

    async def get_market_info(self) -> Dict[str, Any]:
        """Fetch market information from Gamma API"""
        async with aiohttp.ClientSession() as session:
            url = f"{GAMMA_API}/markets"
            params = {"clob_token_ids": self.token_id}
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    markets = await response.json()
                    if markets:
                        return markets[0]
        return {}

    def calculate_position_size(self, price: float) -> float:
        """Calculate position size"""
        if self.config.dry_run:
            return 1.0  # $1 in dry run
        return 1.0  # Start small

    def should_execute(self, time_remaining_seconds: float, best_ask: float) -> bool:
        """Determine if trade should be executed"""
        if time_remaining_seconds > 1.0:
            return False
        if best_ask >= self.config.max_buy_price:
            return False
        if self.last_price <= 0.50:
            return False
        return True

    async def execute_trade(self, side: str, price: float, size: float) -> bool:
        """Execute a FOK market order"""
        logger.info(f"{'[DRY RUN] ' if self.config.dry_run else ''}Executing {side}: ${size:.2f} @ ${price:.3f}")

        if self.config.dry_run:
            logger.info(f"[DRY RUN] WOULD BUY {side} at ${price:.3f}")
            self.signals_detected += 1
            return True

        try:
            order_args = MarketOrderArgs(token_id=self.token_id, amount=size, side=BUY)
            signed_order = self.client.create_market_order(order_args)
            response = self.client.post_order(signed_order, OrderType.FOK)

            if response:
                self.trades_executed += 1
                expected_profit = (1.0 - price) * size
                self.total_profit += expected_profit
                logger.info(f"Trade executed! Expected profit: ${expected_profit:.4f}")
                return True
        except Exception as e:
            logger.error(f"Trade execution failed: {e}")
        return False

    async def handle_price_update(self, data: Dict[str, Any]):
        """Handle incoming price updates"""
        try:
            if "bids" in data and data["bids"]:
                self.current_bid = float(data["bids"][0].get("price", 0))
            if "asks" in data and data["asks"]:
                self.current_ask = float(data["asks"][0].get("price", 0))
            if "price" in data:
                self.last_price = float(data["price"])
            elif self.current_bid and self.current_ask:
                self.last_price = (self.current_bid + self.current_ask) / 2
        except Exception as e:
            logger.warning(f"Error parsing price update: {e}")

    async def check_and_execute(self):
        """Check conditions and execute trade if appropriate"""
        if not self.market_end_time:
            return

        now = datetime.utcnow()
        time_remaining = (self.market_end_time - now).total_seconds()

        if int(time_remaining) % 10 == 0:
            logger.info(f"Time: {time_remaining:.1f}s | Price: ${self.last_price:.3f} | Ask: ${self.current_ask:.3f}")

        if self.should_execute(time_remaining, self.current_ask):
            logger.info("=" * 50)
            logger.info("EXECUTION SIGNAL!")
            logger.info(f"Time: {time_remaining:.2f}s | Ask: ${self.current_ask:.3f}")
            logger.info("=" * 50)

            size = self.calculate_position_size(self.current_ask)
            side = "YES" if self.last_price > 0.50 else "NO"
            await self.execute_trade(side, self.current_ask, size)

    async def connect_websocket(self):
        """Connect to WebSocket and stream prices"""
        ws_url = f"{CLOB_WS}market"
        logger.info(f"Connecting to WebSocket: {ws_url}")

        subscribe_msg = {"type": "subscribe", "channel": "market", "assets_ids": [self.token_id]}

        try:
            async with websockets.connect(ws_url) as ws:
                logger.info("WebSocket connected")
                await ws.send(json.dumps(subscribe_msg))

                async def heartbeat():
                    while self.running:
                        try:
                            await ws.ping()
                            await asyncio.sleep(10)
                        except:
                            break

                heartbeat_task = asyncio.create_task(heartbeat())

                async for message in ws:
                    if not self.running:
                        break
                    try:
                        data = json.loads(message)
                        msg_type = data.get("type", "")
                        if msg_type in ("price_change", "book"):
                            await self.handle_price_update(data)
                            await self.check_and_execute()
                    except Exception as e:
                        logger.error(f"Error: {e}")

                heartbeat_task.cancel()
        except Exception as e:
            logger.error(f"WebSocket error: {e}")
            raise

    async def run(self):
        """Main bot loop"""
        self.running = True

        logger.info("=" * 60)
        logger.info("Polymarket Sniper Bot Starting")
        logger.info("=" * 60)
        logger.info(f"Token ID: {self.token_id}")
        logger.info(f"Dry Run: {self.config.dry_run}")
        logger.info(f"Max Buy Price: ${self.config.max_buy_price}")
        logger.info("=" * 60)

        market_info = await self.get_market_info()
        if market_info:
            logger.info(f"Market: {market_info.get('question', 'Unknown')}")
            end_date = market_info.get("endDate") or market_info.get("end_date_iso")
            if end_date:
                try:
                    self.market_end_time = datetime.fromisoformat(end_date.replace("Z", "+00:00")).replace(tzinfo=None)
                    logger.info(f"Market ends: {self.market_end_time}")
                except:
                    pass

        while self.running:
            try:
                await self.connect_websocket()
            except Exception as e:
                logger.error(f"Connection lost: {e}")
                if self.running:
                    logger.info("Reconnecting in 5s...")
                    await asyncio.sleep(5)

        logger.info("=" * 60)
        logger.info("Bot Stopped")
        logger.info(f"Signals: {self.signals_detected} | Trades: {self.trades_executed} | Profit: ${self.total_profit:.4f}")
        logger.info("=" * 60)

    def stop(self):
        logger.info("Stopping...")
        self.running = False


async def main():
    parser = argparse.ArgumentParser(description="Polymarket Expiration Sniping Bot")
    parser.add_argument("--token-id", "-t", required=True, help="Token ID to monitor")
    parser.add_argument("--condition-id", "-c", help="Condition ID (optional)")
    args = parser.parse_args()

    try:
        config = load_config()
    except ValueError as e:
        logger.error(f"Config error: {e}")
        sys.exit(1)

    bot = SniperBot(config=config, token_id=args.token_id, condition_id=args.condition_id)

    def signal_handler(sig, frame):
        bot.stop()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        await bot.run()
    except KeyboardInterrupt:
        bot.stop()


if __name__ == "__main__":
    asyncio.run(main())
