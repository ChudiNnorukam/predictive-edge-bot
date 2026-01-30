#!/usr/bin/env python3
"""
copy_trader.py - Whale Following / Copy Trading Bot
====================================================

Monitors a target wallet (like distinct-baguette) and mirrors positions.

Usage:
    python copy_trader.py --target distinct-baguette
    python copy_trader.py --target 0xe00740bce98a594e26861838885ab310ec3b548c
"""

import asyncio
import aiohttp
import signal
import sys
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional
import argparse

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import MarketOrderArgs, OrderType, ApiCreds
from py_clob_client.order_builder.constants import BUY, SELL

from config import load_config, CLOB_HOST, DATA_API, LOG_FORMAT, LOG_DATE_FORMAT

logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    datefmt=LOG_DATE_FORMAT,
    handlers=[logging.StreamHandler(), logging.FileHandler("logs/copy_trader.log")]
)
logger = logging.getLogger(__name__)

KNOWN_WALLETS = {
    "distinct-baguette": "0xe00740bce98a594e26861838885ab310ec3b548c",
}


class CopyTrader:
    """Copy Trading Bot - mirrors positions from target wallet"""

    def __init__(self, config, target_address: str, poll_interval: int = 4, max_position_pct: float = 0.20):
        self.config = config
        self.target_address = target_address.lower()
        self.poll_interval = poll_interval
        self.max_position_pct = max_position_pct

        self.running = False
        self.target_positions: Dict[str, Dict] = {}
        self.my_positions: Dict[str, Dict] = {}
        self.trades_executed = 0
        self.total_invested = 0.0
        self.start_time = None

        self.client = self._init_client()

    def _init_client(self) -> ClobClient:
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

    async def fetch_wallet_positions(self, address: str) -> Dict[str, Dict]:
        """Fetch positions for a wallet"""
        positions = {}
        async with aiohttp.ClientSession() as session:
            url = f"{DATA_API}/positions"
            params = {"user": address}
            try:
                async with session.get(url, params=params) as response:
                    if response.status == 200:
                        data = await response.json()
                        for pos in data:
                            token_id = pos.get("asset") or pos.get("token_id")
                            if token_id:
                                positions[token_id] = {
                                    "token_id": token_id,
                                    "size": float(pos.get("size", 0)),
                                    "avg_price": float(pos.get("avgPrice", 0) or pos.get("average_price", 0)),
                                    "side": pos.get("outcome", "").upper(),
                                }
            except Exception as e:
                logger.error(f"Error fetching positions: {e}")
        return positions

    def calculate_trades(self, target_positions: Dict, my_positions: Dict, my_balance: float) -> List[Dict]:
        """Calculate trades to match target allocation"""
        trades = []
        target_total = sum(pos.get("size", 0) * pos.get("avg_price", 0.5) for pos in target_positions.values())
        if target_total == 0:
            return trades

        target_allocations = {}
        for token_id, pos in target_positions.items():
            value = pos.get("size", 0) * pos.get("avg_price", 0.5)
            target_allocations[token_id] = value / target_total

        my_total = my_balance + sum(pos.get("size", 0) * pos.get("avg_price", 0.5) for pos in my_positions.values())

        for token_id, target_pct in target_allocations.items():
            target_value = min(my_total * target_pct * self.config.position_size_pct, my_total * self.max_position_pct)
            my_pos = my_positions.get(token_id, {})
            my_value = my_pos.get("size", 0) * my_pos.get("avg_price", 0.5)
            diff = target_value - my_value

            if abs(diff) >= 1.0:
                trades.append({
                    "action": "BUY" if diff > 0 else "SELL",
                    "token_id": token_id,
                    "size": abs(diff),
                    "side": target_positions[token_id].get("side", "YES"),
                })
        return trades

    async def execute_trade(self, trade: Dict) -> bool:
        """Execute a trade"""
        action, token_id, size = trade["action"], trade["token_id"], trade["size"]
        logger.info(f"{'[DRY RUN] ' if self.config.dry_run else ''}Executing {action}: ${size:.2f}")

        if self.config.dry_run:
            self.trades_executed += 1
            return True

        try:
            order_args = MarketOrderArgs(token_id=token_id, amount=size, side=BUY if action == "BUY" else SELL)
            signed_order = self.client.create_market_order(order_args)
            response = self.client.post_order(signed_order, OrderType.FOK)
            if response:
                self.trades_executed += 1
                self.total_invested += size if action == "BUY" else -size
                return True
        except Exception as e:
            logger.error(f"Trade failed: {e}")
        return False

    async def sync_positions(self):
        """Compare and sync positions"""
        self.target_positions = await self.fetch_wallet_positions(self.target_address)
        self.my_positions = await self.fetch_wallet_positions(self.config.wallet_address)
        my_balance = 1000.0  # Placeholder

        trades = self.calculate_trades(self.target_positions, self.my_positions, my_balance)
        if trades:
            logger.info(f"Found {len(trades)} trades to execute")
            for trade in trades:
                await self.execute_trade(trade)
                await asyncio.sleep(1)

    async def run(self):
        """Main loop"""
        self.running = True
        self.start_time = datetime.now()

        logger.info("=" * 60)
        logger.info("Copy Trader Starting")
        logger.info(f"Target: {self.target_address}")
        logger.info(f"Dry Run: {self.config.dry_run}")
        logger.info("=" * 60)

        iteration = 0
        while self.running:
            try:
                await self.sync_positions()
                iteration += 1
                if iteration % 10 == 0:
                    logger.info(f"Status: {self.trades_executed} trades | ${self.total_invested:.2f} invested")
            except Exception as e:
                logger.error(f"Sync error: {e}")
                await asyncio.sleep(10)
            await asyncio.sleep(self.poll_interval)

        logger.info(f"Stopped. Trades: {self.trades_executed}, Invested: ${self.total_invested:.2f}")

    def stop(self):
        self.running = False


def resolve_target(target: str) -> str:
    if target.lower() in KNOWN_WALLETS:
        return KNOWN_WALLETS[target.lower()]
    if target.startswith("0x") and len(target) == 42:
        return target
    raise ValueError(f"Unknown target: {target}")


async def main():
    parser = argparse.ArgumentParser(description="Polymarket Copy Trading Bot")
    parser.add_argument("--target", "-t", required=True, help="Target wallet or username")
    parser.add_argument("--interval", "-i", type=int, default=4, help="Poll interval (seconds)")
    args = parser.parse_args()

    try:
        config = load_config()
        target_address = resolve_target(args.target)
    except ValueError as e:
        logger.error(str(e))
        sys.exit(1)

    bot = CopyTrader(config=config, target_address=target_address, poll_interval=args.interval)

    signal.signal(signal.SIGINT, lambda s, f: bot.stop())
    signal.signal(signal.SIGTERM, lambda s, f: bot.stop())

    await bot.run()


if __name__ == "__main__":
    asyncio.run(main())
