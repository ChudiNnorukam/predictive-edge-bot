"""
Polymarket Bot Strategies Module
================================

Available strategies:
- BaseStrategy: Abstract base class for all strategies
- SpreadCaptureStrategy: Market-making style trading (buy at bid, sell at ask)
- PositionTracker: Track open positions for spread capture
- OrderManager: Manage limit order lifecycle
"""

from strategies.base_strategy import BaseStrategy
from strategies.spread_capture import SpreadCaptureStrategy, SpreadCaptureConfig
from strategies.position_tracker import PositionTracker, Position
from strategies.order_manager import OrderManager, Order, OrderStatus

__all__ = [
    "BaseStrategy",
    "SpreadCaptureStrategy",
    "SpreadCaptureConfig",
    "PositionTracker",
    "Position",
    "OrderManager",
    "Order",
    "OrderStatus",
]
