"""
Centralized Order Executor
===========================

Handles all trade execution with:
- Order deduplication
- Rate limiting
- Input validation
- Error handling
- Performance tracking

Rebuilt with Opus 4.5 audit fixes:
- CRITICAL-1: Async CLOB calls via run_in_executor
- CRITICAL-4: Input validation on OrderRequest
- HIGH-1: Rate limit lock for thread safety
"""

import asyncio
import time
import logging
import re
from typing import Dict, Any, Optional, Set
from dataclasses import dataclass, field
from functools import partial

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import MarketOrderArgs, OrderType, ApiCreds
from py_clob_client.order_builder.constants import BUY, SELL

from config import CLOB_HOST
from storage import PositionStore

logger = logging.getLogger(__name__)


class OrderValidationError(Exception):
    """Raised when order validation fails"""
    pass


@dataclass
class OrderRequest:
    """
    Order execution request with validation.

    All fields are validated in __post_init__ to catch invalid orders
    before they reach the execution layer.
    """

    token_id: str
    side: str  # YES or NO
    action: str  # BUY or SELL
    size: float
    strategy: str
    price: Optional[float] = None
    metadata: Optional[Dict] = field(default_factory=dict)

    def __post_init__(self):
        """Validate all fields after initialization"""
        # Validate token_id
        if not self.token_id or not isinstance(self.token_id, str):
            raise OrderValidationError("token_id must be a non-empty string")
        if len(self.token_id) < 10:
            raise OrderValidationError(f"token_id too short: {self.token_id}")

        # Validate side
        if self.side not in ("YES", "NO"):
            raise OrderValidationError(f"side must be 'YES' or 'NO', got: {self.side}")

        # Validate action
        if self.action not in ("BUY", "SELL"):
            raise OrderValidationError(f"action must be 'BUY' or 'SELL', got: {self.action}")

        # Validate size
        if not isinstance(self.size, (int, float)):
            raise OrderValidationError(f"size must be numeric, got: {type(self.size)}")
        if self.size <= 0:
            raise OrderValidationError(f"size must be positive, got: {self.size}")
        if self.size > 100000:  # Max $100k per order - safety limit
            raise OrderValidationError(f"size exceeds maximum (100000): {self.size}")

        # Validate price if provided
        if self.price is not None:
            if not isinstance(self.price, (int, float)):
                raise OrderValidationError(f"price must be numeric, got: {type(self.price)}")
            if not (0 < self.price < 1):
                raise OrderValidationError(f"price must be between 0 and 1 exclusive, got: {self.price}")

        # Validate strategy
        if not self.strategy or not isinstance(self.strategy, str):
            raise OrderValidationError("strategy must be a non-empty string")

        # Ensure metadata is a dict
        if self.metadata is None:
            self.metadata = {}


class OrderExecutor:
    """
    Centralized order executor with deduplication and rate limiting.

    Thread-safe and async-compatible. All blocking CLOB API calls are
    executed in a thread pool to avoid blocking the event loop.
    """

    def __init__(self, config, position_store: PositionStore):
        """
        Initialize executor

        Args:
            config: Bot configuration
            position_store: Position storage instance
        """
        self.config = config
        self.position_store = position_store
        self.client = self._init_client()

        # Deduplication tracking
        self.pending_orders: Set[str] = set()
        self.order_lock = asyncio.Lock()

        # Rate limiting with thread-safe lock
        self.order_timestamps: list = []
        self.rate_limit_lock = asyncio.Lock()
        self.max_orders_per_minute = 50  # Buffer below 60 limit

        # Performance tracking
        self.total_orders = 0
        self.successful_orders = 0
        self.failed_orders = 0
        self.total_latency = 0.0

        # Thread pool for blocking calls
        self._executor = None  # Use default ThreadPoolExecutor

    def _init_client(self) -> ClobClient:
        """Initialize CLOB client"""
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

    def _get_order_key(self, request: OrderRequest) -> str:
        """Generate unique key for order deduplication"""
        return f"{request.strategy}:{request.token_id}:{request.action}:{request.size:.2f}"

    async def _check_rate_limit(self) -> bool:
        """
        Check if we're within rate limits.

        Thread-safe implementation using asyncio lock.

        Returns:
            True if can proceed, False if rate limited
        """
        async with self.rate_limit_lock:
            now = time.time()
            # Remove timestamps older than 60 seconds
            self.order_timestamps = [ts for ts in self.order_timestamps if now - ts < 60]

            if len(self.order_timestamps) >= self.max_orders_per_minute:
                logger.warning(
                    f"Rate limit reached: {len(self.order_timestamps)}/{self.max_orders_per_minute} "
                    f"orders in last 60s"
                )
                return False

            self.order_timestamps.append(now)
            return True

    async def execute_order(self, request: OrderRequest) -> bool:
        """
        Execute a market order with deduplication and rate limiting.

        Args:
            request: OrderRequest object (validated on construction)

        Returns:
            True if order executed successfully
        """
        start_time = time.time()
        order_key = self._get_order_key(request)

        # Check deduplication
        async with self.order_lock:
            if order_key in self.pending_orders:
                logger.warning(f"Duplicate order detected: {order_key}")
                return False
            self.pending_orders.add(order_key)

        try:
            # Check rate limit with retry
            if not await self._check_rate_limit():
                await asyncio.sleep(1)  # Brief delay
                if not await self._check_rate_limit():
                    logger.error("Rate limit exceeded after retry - order dropped")
                    return False

            # Execute order
            success = await self._execute_market_order(request)

            # Record execution time
            latency = time.time() - start_time
            self.total_latency += latency
            self.total_orders += 1

            if success:
                self.successful_orders += 1
            else:
                self.failed_orders += 1

            logger.info(
                f"Order execution: {order_key} | Success: {success} | Latency: {latency:.3f}s"
            )
            return success

        finally:
            # Remove from pending
            async with self.order_lock:
                self.pending_orders.discard(order_key)

    async def _execute_market_order(self, request: OrderRequest) -> bool:
        """
        Execute market order via CLOB.

        Uses run_in_executor to avoid blocking the event loop during
        order signing and submission.

        Args:
            request: OrderRequest object

        Returns:
            True if successful
        """
        logger.info(
            f"{'[DRY RUN] ' if self.config.dry_run else ''}Executing {request.action}: "
            f"{request.strategy} | ${request.size:.2f}"
        )

        # Dry run mode
        if self.config.dry_run:
            logger.info(
                f"[DRY RUN] Would {request.action} {request.side} at "
                f"${request.price or 0:.3f}"
            )
            # Record to database even in dry run
            await self.position_store.record_trade(
                token_id=request.token_id,
                side=request.side,
                action=request.action,
                price=request.price or 0.0,
                size=request.size,
                strategy=request.strategy,
                status="dry_run",
                metadata=request.metadata,
            )
            return True

        # Live execution
        try:
            # Determine side for py_clob_client
            clob_side = BUY if request.action == "BUY" else SELL

            # Create market order args
            order_args = MarketOrderArgs(
                token_id=request.token_id,
                amount=request.size,
                side=clob_side,
            )

            # Get event loop for running blocking calls
            loop = asyncio.get_event_loop()

            # Sign order in thread pool (blocking call)
            signed_order = await loop.run_in_executor(
                self._executor,
                partial(self.client.create_market_order, order_args)
            )

            # Submit order in thread pool (blocking call)
            response = await loop.run_in_executor(
                self._executor,
                partial(self.client.post_order, signed_order, OrderType.FOK)
            )

            if response:
                # Get execution price from response
                execution_price = float(response.get("price", request.price or 0))
                fee = float(response.get("fee", 0))

                # Record to database
                await self.position_store.record_trade(
                    token_id=request.token_id,
                    side=request.side,
                    action=request.action,
                    price=execution_price,
                    size=request.size,
                    strategy=request.strategy,
                    status="executed",
                    fee=fee,
                    metadata=request.metadata,
                )

                logger.info(
                    f"Trade executed: {request.strategy} | {request.action} {request.side} | "
                    f"${request.size:.2f} @ ${execution_price:.3f} (fee: ${fee:.4f})"
                )
                return True
            else:
                logger.warning(f"Order returned empty response: {order_args}")

        except OrderValidationError as e:
            logger.error(f"Order validation failed: {e}")
        except Exception as e:
            logger.error(f"Order execution failed: {e}", exc_info=True)
            # Record failed trade
            await self.position_store.record_trade(
                token_id=request.token_id,
                side=request.side,
                action=request.action,
                price=request.price or 0.0,
                size=request.size,
                strategy=request.strategy,
                status="failed",
                metadata={"error": str(e), **(request.metadata or {})},
            )

        return False

    def get_metrics(self) -> Dict[str, Any]:
        """
        Get executor performance metrics

        Returns:
            Dictionary with metrics
        """
        avg_latency = self.total_latency / max(self.total_orders, 1)

        return {
            "total_orders": self.total_orders,
            "successful_orders": self.successful_orders,
            "failed_orders": self.failed_orders,
            "success_rate": self.successful_orders / max(self.total_orders, 1),
            "avg_latency_seconds": avg_latency,
            "pending_orders": len(self.pending_orders),
            "rate_limit_window": len(self.order_timestamps),
        }
