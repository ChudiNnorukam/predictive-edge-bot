"""
Base Strategy Class
===================

Abstract base class that all trading strategies must inherit from.
Provides common functionality for strategy orchestration.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class BaseStrategy(ABC):
    """Abstract base class for all trading strategies"""

    def __init__(self, config, name: str):
        """
        Initialize base strategy

        Args:
            config: Bot configuration object
            name: Strategy name for logging and identification
        """
        self.config = config
        self.name = name
        self.running = False
        self.start_time: Optional[datetime] = None

        # Performance metrics
        self.trades_executed = 0
        self.signals_detected = 0
        self.total_profit = 0.0
        self.total_invested = 0.0

        logger.info(f"Strategy {self.name} initialized")

    @abstractmethod
    async def run(self):
        """
        Main strategy loop - must be implemented by subclasses

        This method should contain the core strategy logic and run continuously
        until self.running is set to False.
        """
        pass

    @abstractmethod
    async def cleanup(self):
        """
        Cleanup resources when strategy stops

        This method should close connections, save state, etc.
        """
        pass

    def start(self):
        """Mark strategy as running"""
        self.running = True
        self.start_time = datetime.utcnow()
        logger.info(f"Strategy {self.name} started")

    def stop(self):
        """Mark strategy as stopped"""
        self.running = False
        logger.info(f"Strategy {self.name} stopped")

    def get_metrics(self) -> Dict[str, Any]:
        """
        Get strategy performance metrics

        Returns:
            Dictionary with performance metrics
        """
        runtime = (datetime.utcnow() - self.start_time).total_seconds() if self.start_time else 0

        return {
            "name": self.name,
            "running": self.running,
            "runtime_seconds": runtime,
            "trades_executed": self.trades_executed,
            "signals_detected": self.signals_detected,
            "total_profit": self.total_profit,
            "total_invested": self.total_invested,
            "win_rate": self.trades_executed / max(self.signals_detected, 1),
        }

    async def validate_config(self) -> bool:
        """
        Validate configuration before running strategy

        Returns:
            True if configuration is valid
        """
        if not self.config.private_key or not self.config.wallet_address:
            logger.error(f"{self.name}: Missing wallet configuration")
            return False

        if not self.config.dry_run:
            logger.warning(f"{self.name}: Running in LIVE mode - real funds at risk!")

        return True

    def should_execute_trade(self, estimated_size: float) -> bool:
        """
        Check if trade should be executed based on risk limits

        Args:
            estimated_size: Estimated trade size in USDC

        Returns:
            True if trade is within risk limits
        """
        # Check position size limits
        if estimated_size > self.config.max_position_pct * 10000:  # Assuming $10k balance
            logger.warning(f"{self.name}: Trade size ${estimated_size:.2f} exceeds max position limit")
            return False

        # Check daily loss limit
        if self.total_profit < -self.config.daily_loss_limit_pct * 10000:
            logger.warning(f"{self.name}: Daily loss limit reached: ${self.total_profit:.2f}")
            return False

        return True

    def log_trade(self, action: str, token_id: str, size: float, price: float, success: bool):
        """
        Log trade execution for monitoring

        Args:
            action: BUY or SELL
            token_id: Token ID traded
            size: Trade size in USDC
            price: Execution price
            success: Whether trade succeeded
        """
        status = "SUCCESS" if success else "FAILED"
        logger.info(
            f"{self.name} | {action} | Token: {token_id[:8]}... | "
            f"Size: ${size:.2f} | Price: ${price:.3f} | {status}"
        )
