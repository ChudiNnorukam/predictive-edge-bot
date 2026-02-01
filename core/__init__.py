"""
Core Market State Machine and Priority Queue
=============================================

Implements the market lifecycle and execution scheduling for the bot.

Exports:
- MarketState: Enum of market states
- Market: Market data class with state tracking
- SchedulerConfig: Configuration for scheduler
- MarketStateMachine: State transition manager
- MarketPriorityQueue: Time-based priority queue
"""

from core.market_state import (
    Market,
    MarketState,
    SchedulerConfig,
    MarketStateMachine,
)
from core.priority_queue import MarketPriorityQueue

__all__ = [
    "Market",
    "MarketState",
    "SchedulerConfig",
    "MarketStateMachine",
    "MarketPriorityQueue",
]
