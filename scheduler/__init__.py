"""
Multi-Market Scheduler Package
==============================

Core components:
- MultiMarketScheduler: Main orchestrator
- ExecutionWindow: Manages T-60 → T-0 phases
- SchedulerConfig: Configuration dataclass
"""

from scheduler.scheduler import MultiMarketScheduler, SchedulerConfig
from scheduler.execution_window import ExecutionWindow

__all__ = [
    "MultiMarketScheduler",
    "ExecutionWindow",
    "SchedulerConfig",
]
