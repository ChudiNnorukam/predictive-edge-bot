"""
Capital Management System
=========================

Manages capital allocation and recycling across markets.

Export:
    CapitalConfig: Configuration for allocation limits
    CapitalAllocator: Manages capital allocation with exposure controls
    CapitalRecycler: Manages capital recycling as markets complete
    AllocationResult: Enum of allocation outcomes
    Allocation: Data class for allocation state
    RecycleEvent: Data class for recycle events
"""

from capital.allocator import (
    CapitalConfig,
    CapitalAllocator,
    AllocationResult,
    Allocation,
)
from capital.recycler import (
    CapitalRecycler,
    RecycleEvent,
)

__all__ = [
    "CapitalConfig",
    "CapitalAllocator",
    "CapitalRecycler",
    "AllocationResult",
    "Allocation",
    "RecycleEvent",
]
