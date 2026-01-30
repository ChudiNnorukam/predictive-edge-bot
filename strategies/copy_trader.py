"""
Copy Trader Strategy
====================

Mirrors positions from a target wallet (e.g., distinct-baguette).

Inherits from BaseStrategy and integrates with centralized executor.

Phase 2 Improvements (Opus 4.5):
- CRITICAL-3: Query actual wallet balance via web3
- HIGH-2: Session reuse (single aiohttp session)
- HIGH-3: Exponential backoff for reconnection
- HIGH-6: API timeouts on all requests
- MED-2: UTC timezone handling
"""

import asyncio
import aiohttp
import logging
import time
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

from web3 import Web3
from web3.exceptions import Web3Exception

from strategies.base_strategy import BaseStrategy
from executor import OrderRequest
from config import DATA_API

logger = logging.getLogger(__name__)

# Polygon USDC contract
POLYGON_USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
POLYGON_USDC_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function",
    }
]

# Polymarket WebSocket for real-time events
CLOB_WS = "wss://ws-subscriptions-clob.polymarket.com/ws/"

KNOWN_WALLETS = {
    "distinct-baguette": "0xe00740bce98a594e26861838885ab310ec3b548c",
}

# Default API timeout in seconds
DEFAULT_API_TIMEOUT = 30


class CopyTraderStrategy(BaseStrategy):
    """
    Copy trading strategy - mirrors target wallet positions.

    Phase 2 implementation with:
    - Real wallet balance queries
    - Session reuse for API calls
    - Exponential backoff on errors
    - UTC timezone handling
    """

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

        # Web3 client for balance queries
        self.w3: Optional[Web3] = None
        self.usdc_contract = None

        # Reusable HTTP session
        self._session: Optional[aiohttp.ClientSession] = None

        # Exponential backoff state
        self._consecutive_errors = 0
        self._max_backoff = 300  # 5 minutes max

        # Balance cache (query at most every 60 seconds)
        self._cached_balance: Optional[float] = None
        self._balance_cached_at: float = 0
        self._balance_cache_ttl: int = 60

    def _resolve_target(self, target: str) -> str:
        """Resolve target name to address"""
        if target.lower() in KNOWN_WALLETS:
            return KNOWN_WALLETS[target.lower()].lower()
        if target.startswith("0x") and len(target) == 42:
            return target.lower()
        raise ValueError(f"Unknown target: {target}")

    async def _init_web3(self):
        """Initialize Web3 connection for balance queries"""
        if self.w3 is None:
            try:
                self.w3 = Web3(Web3.HTTPProvider(
                    self.config.rpc_url,
                    request_kwargs={"timeout": DEFAULT_API_TIMEOUT}
                ))
                self.usdc_contract = self.w3.eth.contract(
                    address=Web3.to_checksum_address(POLYGON_USDC_ADDRESS),
                    abi=POLYGON_USDC_ABI
                )
                logger.info(f"{self.name} Web3 initialized: {self.config.rpc_url}")
            except Exception as e:
                logger.error(f"{self.name} Web3 initialization failed: {e}")
                self.w3 = None
                self.usdc_contract = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create reusable HTTP session"""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=DEFAULT_API_TIMEOUT)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def _get_wallet_balance(self) -> float:
        """
        Get actual USDC balance from wallet.

        Caches result for balance_cache_ttl seconds to avoid excessive RPC calls.
        Falls back to 0 if query fails (conservative - won't trade without balance).

        Returns:
            USDC balance as float
        """
        now = time.time()

        # Return cached value if still valid
        if (
            self._cached_balance is not None
            and (now - self._balance_cached_at) < self._balance_cache_ttl
        ):
            return self._cached_balance

        # Initialize Web3 if needed
        await self._init_web3()

        if self.w3 is None or self.usdc_contract is None:
            logger.warning(f"{self.name} Web3 not available, using fallback balance 0")
            return 0.0

        try:
            # Query USDC balance (6 decimals)
            wallet_address = Web3.to_checksum_address(self.config.wallet_address)

            # Use run_in_executor to avoid blocking event loop
            loop = asyncio.get_event_loop()
            raw_balance = await loop.run_in_executor(
                None,
                self.usdc_contract.functions.balanceOf(wallet_address).call
            )

            # Convert from 6 decimals to float
            balance = raw_balance / 1_000_000

            # Cache result
            self._cached_balance = balance
            self._balance_cached_at = now

            logger.debug(f"{self.name} Wallet balance: ${balance:.2f} USDC")
            return balance

        except Web3Exception as e:
            logger.error(f"{self.name} Web3 balance query failed: {e}")
            return self._cached_balance if self._cached_balance is not None else 0.0
        except Exception as e:
            logger.error(f"{self.name} Unexpected error querying balance: {e}")
            return self._cached_balance if self._cached_balance is not None else 0.0

    def _calculate_backoff(self) -> float:
        """
        Calculate exponential backoff delay.

        Returns:
            Delay in seconds (capped at max_backoff)
        """
        if self._consecutive_errors == 0:
            return 0

        # Exponential backoff: 2^n seconds, capped at max_backoff
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
        logger.info(f"Target wallet: {self.target_address}")

        # Initialize Web3 at startup
        await self._init_web3()

        # Log initial balance
        initial_balance = await self._get_wallet_balance()
        logger.info(f"{self.name} Initial wallet balance: ${initial_balance:.2f} USDC")

        iteration = 0
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

                await self._sync_positions()
                iteration += 1

                # Reset backoff on success
                self._reset_backoff()

                if iteration % 10 == 0:
                    current_balance = await self._get_wallet_balance()
                    logger.info(
                        f"{self.name} | Iteration {iteration} | "
                        f"Trades: {self.trades_executed} | "
                        f"Invested: ${self.total_invested:.2f} | "
                        f"Balance: ${current_balance:.2f}"
                    )

            except asyncio.CancelledError:
                logger.info(f"{self.name} cancelled")
                break
            except Exception as e:
                logger.error(f"{self.name} sync error: {e}")
                self._increment_backoff()

            await asyncio.sleep(self.poll_interval)

    async def cleanup(self):
        """Cleanup resources"""
        # Close HTTP session
        if self._session is not None and not self._session.closed:
            await self._session.close()
            self._session = None

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

        # Get actual wallet balance
        my_balance = await self._get_wallet_balance()

        # Don't trade if balance is 0 (failed to query or actually empty)
        if my_balance <= 0:
            logger.warning(f"{self.name} Wallet balance is ${my_balance:.2f}, skipping trades")
            return

        trades = self._calculate_trades(self.target_positions, self.my_positions, my_balance)

        if trades:
            logger.info(f"{self.name} found {len(trades)} trades to execute")
            for trade in trades:
                await self._execute_trade(trade)
                await asyncio.sleep(1)  # Rate limiting

    async def _fetch_wallet_positions(self, address: str) -> Dict[str, Dict]:
        """
        Fetch positions for a wallet from Polymarket API.

        Uses reusable session with timeout.
        """
        positions = {}
        session = await self._get_session()

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
                                "avg_price": float(
                                    pos.get("avgPrice", 0) or pos.get("average_price", 0)
                                ),
                                "side": pos.get("outcome", "").upper(),
                                "fetched_at": datetime.now(timezone.utc).isoformat(),
                            }
                else:
                    logger.warning(
                        f"{self.name} API returned status {response.status} for {url}"
                    )
        except asyncio.TimeoutError:
            logger.error(f"{self.name} Timeout fetching positions from {url}")
            raise
        except aiohttp.ClientError as e:
            logger.error(f"{self.name} HTTP error fetching positions: {e}")
            raise
        except Exception as e:
            logger.error(f"{self.name} Unexpected error fetching positions: {e}")
            raise

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
                    "calculated_at": datetime.now(timezone.utc).isoformat(),
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
                "calculated_at": trade.get("calculated_at"),
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
