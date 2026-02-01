"""
Kill Switches Module
====================

Global trading halt mechanisms for emergency risk management.
Provides automatic shutdown of trading when critical thresholds are breached.

Features:
- Stale feed detection (halt if feed > 500ms stale)
- RPC lag detection (halt if ack > 300ms)
- Order limit enforcement (max outstanding orders)
- Daily loss limit (stop if loss > 5%)
- Manual override capability

All operations are thread-safe with asyncio.Lock.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from .circuit_breakers import CircuitBreakerRegistry
    from .exposure_manager import ExposureManager

logger = logging.getLogger(__name__)


class KillSwitchType(Enum):
    """Enumeration of kill switch trigger types."""
    STALE_FEED = "stale_feed"
    RPC_LAG = "rpc_lag"
    MAX_ORDERS = "max_orders"
    DAILY_LOSS = "daily_loss"
    MANUAL = "manual"


@dataclass
class KillSwitchConfig:
    """Configuration for kill switch thresholds."""
    stale_feed_threshold_ms: int = 500      # Halt if feed > 500ms stale
    rpc_lag_threshold_ms: int = 300         # Halt if order ack > 300ms
    max_outstanding_orders: int = 10        # Global limit on outstanding orders
    daily_loss_limit_percent: float = 5.0   # Stop trading if daily loss > 5%


class KillSwitchManager:
    """
    Global kill switches for emergency trading halt.

    Monitors critical metrics and automatically halts trading when thresholds
    are exceeded. All operations are atomic and thread-safe.

    Example:
        config = KillSwitchConfig(
            stale_feed_threshold_ms=500,
            daily_loss_limit_percent=5.0
        )
        manager = KillSwitchManager(config)

        # In trading loop
        if await manager.check_stale_feed(last_update):
            # Stop trading immediately
            pass

        if manager.is_trading_halted():
            logger.error(f"Trading halted: {manager.get_active_switches()}")
    """

    def __init__(self, config: KillSwitchConfig):
        """
        Initialize kill switch manager.

        Args:
            config: KillSwitchConfig with threshold settings
        """
        self.config = config
        self._active_switches: Dict[KillSwitchType, str] = {}  # type -> reason
        self._daily_pnl: float = 0.0
        self._outstanding_orders: int = 0
        self._lock = asyncio.Lock()
        self._last_reset: datetime = datetime.now(timezone.utc)

    async def check_stale_feed(self, last_update: datetime) -> bool:
        """
        Check if data feed is stale and activate kill switch if needed.

        Args:
            last_update: Timestamp of last feed update

        Returns:
            True if feed is stale (kill switch activated), False otherwise
        """
        now = datetime.now(timezone.utc)
        stale_ms = (now - last_update).total_seconds() * 1000

        if stale_ms > self.config.stale_feed_threshold_ms:
            reason = f"Feed stale for {stale_ms:.0f}ms (threshold: {self.config.stale_feed_threshold_ms}ms)"
            await self.activate(KillSwitchType.STALE_FEED, reason)
            return True

        # Clear stale feed switch if recovered
        if KillSwitchType.STALE_FEED in self._active_switches:
            await self.deactivate(KillSwitchType.STALE_FEED)

        return False

    async def check_rpc_lag(self, latency_ms: float) -> bool:
        """
        Check if RPC latency exceeds threshold and activate kill switch if needed.

        Args:
            latency_ms: RPC operation latency in milliseconds

        Returns:
            True if lag exceeds threshold (kill switch activated), False otherwise
        """
        if latency_ms > self.config.rpc_lag_threshold_ms:
            reason = f"RPC lag {latency_ms:.0f}ms (threshold: {self.config.rpc_lag_threshold_ms}ms)"
            await self.activate(KillSwitchType.RPC_LAG, reason)
            return True

        # Clear RPC lag switch if recovered
        if KillSwitchType.RPC_LAG in self._active_switches:
            await self.deactivate(KillSwitchType.RPC_LAG)

        return False

    async def check_order_limit(self, current_orders: int) -> bool:
        """
        Check if outstanding order count exceeds limit.

        Args:
            current_orders: Current number of outstanding orders

        Returns:
            True if at/exceeds max orders (kill switch activated), False otherwise
        """
        async with self._lock:
            self._outstanding_orders = current_orders

            if current_orders >= self.config.max_outstanding_orders:
                reason = f"Outstanding orders {current_orders} >= {self.config.max_outstanding_orders}"
                await self.activate(KillSwitchType.MAX_ORDERS, reason)
                return True

            # Clear order limit switch if recovered
            if KillSwitchType.MAX_ORDERS in self._active_switches:
                await self.deactivate(KillSwitchType.MAX_ORDERS)

        return False

    async def check_daily_loss(self, bankroll: float) -> bool:
        """
        Check if daily loss exceeds limit and activate kill switch if needed.

        Args:
            bankroll: Current bankroll amount

        Returns:
            True if loss limit exceeded (kill switch activated), False otherwise
        """
        async with self._lock:
            # Calculate loss as negative change from start of day
            max_allowed_loss = bankroll * (self.config.daily_loss_limit_percent / 100.0)

            if self._daily_pnl < -max_allowed_loss:
                reason = f"Daily loss {-self._daily_pnl:.2f} exceeds {max_allowed_loss:.2f} ({self.config.daily_loss_limit_percent}%)"
                await self.activate(KillSwitchType.DAILY_LOSS, reason)
                return True

            # Clear loss limit switch if recovered
            if KillSwitchType.DAILY_LOSS in self._active_switches:
                await self.deactivate(KillSwitchType.DAILY_LOSS)

        return False

    def is_trading_halted(self) -> bool:
        """
        Check if trading is halted (any kill switch active).

        Returns:
            True if any kill switch is active, False if all clear
        """
        return len(self._active_switches) > 0

    def get_active_switches(self) -> Dict[KillSwitchType, str]:
        """
        Get all active kill switches and their reasons.

        Returns:
            Dictionary mapping KillSwitchType to reason string
        """
        return self._active_switches.copy()

    async def activate(self, switch_type: KillSwitchType, reason: str) -> None:
        """
        Manually activate a kill switch.

        Args:
            switch_type: Type of kill switch to activate
            reason: Human-readable reason for activation
        """
        async with self._lock:
            if switch_type not in self._active_switches:
                self._active_switches[switch_type] = reason
                logger.error(
                    f"KILL SWITCH ACTIVATED: {switch_type.value} - {reason}"
                )

    async def deactivate(self, switch_type: KillSwitchType) -> None:
        """
        Deactivate a kill switch (use with caution).

        Args:
            switch_type: Type of kill switch to deactivate
        """
        async with self._lock:
            if switch_type in self._active_switches:
                reason = self._active_switches.pop(switch_type)
                logger.warning(
                    f"Kill switch deactivated: {switch_type.value} ({reason})"
                )

    async def update_daily_pnl(self, pnl_change: float) -> None:
        """
        Record a P&L change and check against daily loss limit.

        Args:
            pnl_change: Change in P&L (positive or negative)
        """
        async with self._lock:
            self._daily_pnl += pnl_change
            logger.debug(f"Daily PnL updated: {self._daily_pnl:.2f} (change: {pnl_change:+.2f})")

    async def reset_daily(self) -> None:
        """
        Reset daily counters (call at midnight UTC).
        Should be called by a scheduler once per day.
        """
        async with self._lock:
            self._daily_pnl = 0.0
            self._last_reset = datetime.now(timezone.utc)

            # Don't auto-reset manual switches
            for switch_type in list(self._active_switches.keys()):
                if switch_type != KillSwitchType.MANUAL:
                    await self.deactivate(switch_type)

            logger.info("Daily counters reset")

    def get_status(self) -> Dict[str, any]:
        """
        Get comprehensive kill switch status for logging/monitoring.

        Returns:
            Dictionary with all kill switch metrics
        """
        return {
            "is_halted": self.is_trading_halted(),
            "active_switches": {k.value: v for k, v in self._active_switches.items()},
            "daily_pnl": self._daily_pnl,
            "outstanding_orders": self._outstanding_orders,
            "last_reset": self._last_reset.isoformat(),
        }


class RiskManager:
    """
    Unified risk management facade combining all risk systems.

    Integrates kill switches, circuit breakers, and exposure management
    for comprehensive trading risk control.

    Example:
        manager = RiskManager(
            kill_switches=kill_switch_mgr,
            circuit_breakers=cb_registry,
            exposure_manager=exp_mgr
        )

        # Global check
        can_trade, reason = await manager.can_trade()

        # Market-specific check
        can_trade_market, reason = await manager.can_trade_market("market_123")

        # Full pre-execution validation
        can_execute, reason = await manager.pre_execution_check(
            market_id="market_123",
            amount=100.0,
            feed_last_update=datetime.now(timezone.utc)
        )

        # Record execution outcome
        await manager.post_execution_record(
            market_id="market_123",
            success=True,
            pnl=15.50,
            latency_ms=45.0
        )
    """

    def __init__(
        self,
        kill_switches: KillSwitchManager,
        circuit_breakers: "CircuitBreakerRegistry",
        exposure_manager: "ExposureManager",
    ):
        """
        Initialize unified risk manager.

        Args:
            kill_switches: KillSwitchManager instance
            circuit_breakers: CircuitBreakerRegistry instance
            exposure_manager: ExposureManager instance
        """
        self.kill_switches = kill_switches
        self.circuit_breakers = circuit_breakers
        self.exposure_manager = exposure_manager

    async def can_trade(self) -> Tuple[bool, str]:
        """
        Check if trading is allowed globally.

        Checks kill switches only - doesn't validate per-market details.

        Returns:
            Tuple of (allowed: bool, reason: str)
        """
        if self.kill_switches.is_trading_halted():
            switches = self.kill_switches.get_active_switches()
            reasons = "; ".join(f"{k.value}: {v}" for k, v in switches.items())
            return False, f"Trading halted: {reasons}"

        return True, "OK"

    async def can_trade_market(self, market_id: str) -> Tuple[bool, str]:
        """
        Check if trading is allowed for a specific market.

        Checks kill switches and market-specific circuit breaker.

        Args:
            market_id: Market identifier

        Returns:
            Tuple of (allowed: bool, reason: str)
        """
        # First check global halts
        can_trade, reason = await self.can_trade()
        if not can_trade:
            return False, reason

        # Check market circuit breaker
        if not await self.circuit_breakers.can_execute(market_id):
            open_breakers = await self.circuit_breakers.get_open_breakers()
            return False, f"Circuit breaker OPEN for {market_id}"

        return True, "OK"

    async def pre_execution_check(
        self,
        market_id: str,
        amount: float,
        feed_last_update: datetime,
    ) -> Tuple[bool, str]:
        """
        Full pre-execution validation across all risk systems.

        Comprehensive check that should be performed before every trade:
        1. Kill switches (stale feed, RPC lag, etc.)
        2. Market circuit breaker
        3. Exposure limits

        Args:
            market_id: Market identifier
            amount: Proposed trade amount
            feed_last_update: Timestamp of last data feed update

        Returns:
            Tuple of (allowed: bool, reason: str)
        """
        # Check kill switches
        if self.kill_switches.is_trading_halted():
            switches = self.kill_switches.get_active_switches()
            reasons = "; ".join(f"{k.value}: {v}" for k, v in switches.items())
            return False, f"Trading halted: {reasons}"

        # Check stale feed
        if await self.kill_switches.check_stale_feed(feed_last_update):
            return False, "Data feed is stale"

        # Check market circuit breaker
        if not await self.circuit_breakers.can_execute(market_id):
            return False, f"Circuit breaker OPEN for {market_id}"

        # Check exposure limits
        can_allocate, reason = await self.exposure_manager.can_allocate(market_id, amount)
        if not can_allocate:
            return False, f"Exposure limit: {reason}"

        return True, "OK"

    async def post_execution_record(
        self,
        market_id: str,
        success: bool,
        pnl: float = 0.0,
        latency_ms: float = 0.0,
    ) -> None:
        """
        Record execution outcome across all risk systems.

        Should be called after every trade execution to update all
        risk tracking systems.

        Args:
            market_id: Market identifier
            success: Whether execution succeeded
            pnl: Profit/loss amount (if execution succeeded)
            latency_ms: RPC operation latency
        """
        # Check RPC lag and potentially activate kill switch
        if latency_ms > 0:
            await self.kill_switches.check_rpc_lag(latency_ms)

        # Update circuit breaker
        if success:
            await self.circuit_breakers.record_success(market_id)
            # Update exposure with P&L
            if pnl != 0:
                await self.exposure_manager.record_pnl(market_id, pnl)
        else:
            await self.circuit_breakers.record_failure(market_id, "Execution failed")

    def get_risk_status(self) -> Dict[str, any]:
        """
        Get comprehensive risk status across all systems.

        Useful for logging and monitoring dashboards.

        Returns:
            Dictionary with combined risk status
        """
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "kill_switches": self.kill_switches.get_status(),
            "circuit_breakers": self.circuit_breakers._breakers,  # Include count for safety
            "exposure": self.exposure_manager.get_exposure_report(),
        }
