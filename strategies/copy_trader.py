"""
Copy Trader Strategy
====================

Mirrors positions from a target wallet (e.g., distinct-baguette).

Inherits from BaseStrategy and integrates with centralized executor.
"""

import asyncio
import aiohttp
import logging
from typing import Dict, Any, List

from strategies.base_strategy import BaseStrategy
from executor import OrderRequest

logger = logging.getLogger(__name__)

KNOWN_WALLETS = {
    "distinct-baguette": "0xe00740bce98a594e26861838885ab310ec3b548c",
}


class CopyTraderStrategy(BaseStrategy):
    """Copy trading strategy - mirrors target wallet positions"""

    def __init__(
        self,
        config,
        executor,
        position_store,
        target_address: str,
        poll_interval: int = 4,
        max_position_pct: float = 0.20,
    ):
        """
        Initialize copy trader strategy

        Args:
            config: Bot configuration
            executor: OrderExecutor instance
            position_store: PositionStore instance
            target_address: Target wallet address or known name
            poll_interval: Seconds between position checks
            max_position_pct: Max position size as % of balance
        """
        super().__init__(config, name=f"CopyTrader-{target_address[:8]}")

        self.executor = executor
        self.position_store = position_store
        self.target_address = self._resolve_target(target_address)
        self.poll_interval = poll_interval
        self.max_position_pct = max_position_pct

        # Position tracking
        self.target_positions: Dict[str, Dict] = {}
        self.my_positions: Dict[str, Dict] = {}

    def _resolve_target(self, target: str) -> str:
        """Resolve target name to address"""
        if target.lower() in KNOWN_WALLETS:
            return KNOWN_WALLETS[target.lower()].lower()
        if target.startswith("0x") and len(target) == 42:
            return target.lower()
        raise ValueError(f"Unknown target: {target}")

    async def run(self):
        """Main strategy loop"""
        logger.info(f"{self.name} strategy running")
        logger.info(f"Target wallet: {self.target_address}")

        iteration = 0
        while self.running:
            try:
                await self._sync_positions()
                iteration += 1

                if iteration % 10 == 0:
                    logger.info(
                        f"{self.name} | Iteration {iteration} | "
                        f"Trades: {self.trades_executed} | "
                        f"Invested: ${self.total_invested:.2f}"
                    )

            except Exception as e:
                logger.error(f"{self.name} sync error: {e}")
                await asyncio.sleep(10)

            await asyncio.sleep(self.poll_interval)

    async def cleanup(self):
        """Cleanup resources"""
        logger.info(f"{self.name} cleanup complete")

    async def _sync_positions(self):
        """Compare and sync positions with target"""
        # Fetch target positions
        self.target_positions = await self._fetch_wallet_positions(self.target_address)

        # Fetch my positions from database
        my_open_positions = await self.position_store.get_open_positions(strategy=self.name)
        self.my_positions = {
            pos["token_id"]: pos for pos in my_open_positions
        }

        # Calculate trades needed
        my_balance = 1000.0  # TODO: Get actual balance from wallet
        trades = self._calculate_trades(self.target_positions, self.my_positions, my_balance)

        if trades:
            logger.info(f"{self.name} found {len(trades)} trades to execute")
            for trade in trades:
                await self._execute_trade(trade)
                await asyncio.sleep(1)  # Rate limiting

    async def _fetch_wallet_positions(self, address: str) -> Dict[str, Dict]:
        """Fetch positions for a wallet from Polymarket API"""
        positions = {}
        async with aiohttp.ClientSession() as session:
            url = "https://data-api.polymarket.com/positions"
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
                                    "avg_price": float(
                                        pos.get("avgPrice", 0) or pos.get("average_price", 0)
                                    ),
                                    "side": pos.get("outcome", "").upper(),
                                }
            except Exception as e:
                logger.error(f"{self.name} error fetching positions: {e}")

        return positions

    def _calculate_trades(
        self,
        target_positions: Dict,
        my_positions: Dict,
        my_balance: float,
    ) -> List[Dict]:
        """Calculate trades needed to match target allocation"""
        trades = []

        # Calculate target allocations as % of total
        target_total = sum(
            pos.get("size", 0) * pos.get("avg_price", 0.5)
            for pos in target_positions.values()
        )

        if target_total == 0:
            return trades

        target_allocations = {}
        for token_id, pos in target_positions.items():
            value = pos.get("size", 0) * pos.get("avg_price", 0.5)
            target_allocations[token_id] = value / target_total

        # Calculate my total value
        my_total = my_balance + sum(
            pos.get("size", 0) * pos.get("avg_price", 0.5)
            for pos in my_positions.values()
        )

        # Generate trades to match allocations
        for token_id, target_pct in target_allocations.items():
            # Target value for this position
            target_value = min(
                my_total * target_pct * self.config.position_size_pct,
                my_total * self.max_position_pct,
            )

            # My current value in this position
            my_pos = my_positions.get(token_id, {})
            my_value = my_pos.get("size", 0) * my_pos.get("avg_price", 0.5)

            # Difference
            diff = target_value - my_value

            # Only trade if difference > $1
            if abs(diff) >= 1.0:
                trades.append({
                    "action": "BUY" if diff > 0 else "SELL",
                    "token_id": token_id,
                    "size": abs(diff),
                    "side": target_positions[token_id].get("side", "YES"),
                })

        return trades

    async def _execute_trade(self, trade: Dict):
        """Execute a trade via centralized executor"""
        self.signals_detected += 1

        # Check risk limits
        if not self.should_execute_trade(trade["size"]):
            logger.warning(f"{self.name} Trade blocked by risk limits")
            return

        # Create order request
        order_request = OrderRequest(
            token_id=trade["token_id"],
            side=trade["side"],
            action=trade["action"],
            size=trade["size"],
            strategy=self.name,
            metadata={
                "signal_type": "copy_trade",
                "target_wallet": self.target_address,
            },
        )

        # Execute
        success = await self.executor.execute_order(order_request)

        if success:
            self.trades_executed += 1
            if trade["action"] == "BUY":
                self.total_invested += trade["size"]
            else:
                self.total_invested -= trade["size"]
