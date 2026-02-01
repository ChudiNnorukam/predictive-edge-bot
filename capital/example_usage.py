"""
Capital Allocation System - Example Usage
==========================================

Demonstrates how to use the capital allocator and recycler
in a realistic Polymarket trading scenario.
"""

import asyncio
import logging
from datetime import datetime

from capital.allocator import CapitalConfig, CapitalAllocator, AllocationResult
from capital.recycler import CapitalRecycler

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


async def example_basic_allocation():
    """
    Example 1: Basic allocation and release
    ========================================

    Shows how to:
    - Initialize allocator with configuration
    - Request allocations for multiple markets
    - Release allocations with P&L
    """
    logger.info("=== Example 1: Basic Allocation ===")

    # Create config with conservative limits
    config = CapitalConfig(
        max_exposure_per_market_percent=5.0,   # 5% per market
        max_exposure_per_market_absolute=50.0, # $50 hard cap
        max_total_exposure_percent=30.0,       # 30% total
        min_order_size=1.0,
        order_split_threshold=20.0,
        order_split_count=3,
    )

    # Initialize allocator with $200 bankroll
    allocator = CapitalAllocator(config, initial_bankroll=200.0)

    # Request allocation for market 1
    result, amount = await allocator.request_allocation("market1", 15.0, strategy="sniper")
    logger.info(f"Market 1: {result.value}, allocated ${amount:.2f}")

    # Request allocation for market 2
    result, amount = await allocator.request_allocation("market2", 10.0, strategy="copy_trader")
    logger.info(f"Market 2: {result.value}, allocated ${amount:.2f}")

    # Check portfolio state
    report = allocator.get_allocation_report()
    logger.info(f"Portfolio: ${report['total_allocated']:.2f} allocated, "
                f"{report['utilization_percent']:.1f}% utilized")

    # Market 1 resolves with profit
    released = await allocator.release_allocation("market1", pnl=0.75)
    logger.info(f"Market 1 resolved: released ${released:.2f}, bankroll now ${allocator.bankroll:.2f}")


async def example_order_splitting():
    """
    Example 2: Order splitting for large allocations
    ==================================================

    Shows how large orders are automatically split
    to reduce slippage and manage execution risk.
    """
    logger.info("\n=== Example 2: Order Splitting ===")

    config = CapitalConfig(
        max_exposure_per_market_percent=50.0,  # Allow larger allocations
        max_total_exposure_percent=100.0,
        order_split_threshold=20.0,            # Split orders > $20
        order_split_count=3,                   # Into 3 orders
    )

    allocator = CapitalAllocator(config, initial_bankroll=500.0)

    # Allocate $30 - should be split into 3 orders
    result, amount = await allocator.request_allocation("market1", 30.0)
    logger.info(f"Requested $30, allocated ${amount:.2f}")

    allocation = await allocator.get_allocation("market1")
    logger.info(f"Order sizes: {allocation.get_order_sizes()}")
    # Expected: [10.0, 10.0, 10.0]


async def example_with_recycler():
    """
    Example 3: Complete allocation and recycling workflow
    ======================================================

    Shows how to:
    - Allocate capital across multiple markets
    - Queue markets for recycling as they resolve
    - Automatically free capital for new opportunities
    - Use callbacks when capital is freed
    """
    logger.info("\n=== Example 3: Allocation + Recycling ===")

    config = CapitalConfig(
        max_exposure_per_market_percent=20.0,
        max_total_exposure_percent=60.0,
        recycle_delay_seconds=1.0,  # Wait 1 second after resolution
    )

    allocator = CapitalAllocator(config, initial_bankroll=500.0)
    recycler = CapitalRecycler(
        config,
        allocator,
        on_capital_freed=log_capital_freed,  # Callback when capital freed
    )

    # Start the recycler background task
    await recycler.start()

    try:
        # Allocate to 3 markets
        for i in range(3):
            result, amount = await allocator.request_allocation(f"market{i}", 50.0)
            logger.info(f"Allocated to market{i}: ${amount:.2f}")

        # Check portfolio
        report = allocator.get_allocation_report()
        logger.info(f"Total allocated: ${report['total_allocated']:.2f}")

        # Markets resolve and get recycled
        await asyncio.sleep(0.5)
        for i in range(3):
            pnl = (i + 1) * 0.50  # Varying profits
            await recycler.queue_recycle(f"market{i}", pnl=pnl)
            logger.info(f"Queued market{i} for recycling with ${pnl:.2f} profit")

        # Wait for recycling to complete
        await asyncio.sleep(2.0)

        # Check final state
        report = allocator.get_allocation_report()
        logger.info(f"Final bankroll: ${allocator.bankroll:.2f}")
        logger.info(f"Remaining allocated: ${report['total_allocated']:.2f}")

        # Get recycling stats
        stats = recycler.get_daily_stats()
        logger.info(f"Daily stats: {stats['recycles_today']} recycles, "
                    f"${stats['capital_recycled_today']:.2f} recycled, "
                    f"${stats['total_pnl_today']:+.2f} P&L")

    finally:
        await recycler.stop()


async def example_allocation_limits():
    """
    Example 4: Respecting allocation limits
    ========================================

    Shows how allocator enforces limits and handles failures.
    """
    logger.info("\n=== Example 4: Allocation Limits ===")

    config = CapitalConfig(
        max_exposure_per_market_percent=5.0,   # $10 per market
        max_total_exposure_percent=30.0,       # $60 total
    )

    allocator = CapitalAllocator(config, initial_bankroll=200.0)

    # Allocate to 6 markets (hits 30% total limit)
    for i in range(6):
        result, amount = await allocator.request_allocation(f"market{i}", 15.0)
        if result == AllocationResult.SUCCESS:
            logger.info(f"market{i}: allocated ${amount:.2f}")
        else:
            logger.info(f"market{i}: {result.value}")

    # Try to allocate to market 7 - should fail
    result, amount = await allocator.request_allocation("market7", 10.0)
    logger.info(f"market7: {result.value} (market limit exceeded)")

    # Show portfolio state
    report = allocator.get_allocation_report()
    logger.info(f"Portfolio utilization: {report['utilization_percent']:.1f}% "
                f"(max {config.max_total_exposure_percent}%)")
    logger.info(f"Markets allocated: {report['num_allocated_markets']}")


async def log_capital_freed(amount: float):
    """Callback when capital is freed by recycler"""
    logger.info(f"Capital freed callback: ${amount:.2f} returned to pool")


async def example_stress_test():
    """
    Example 5: Stress test - many concurrent allocations
    ======================================================

    Shows how the system handles concurrent allocation requests.
    """
    logger.info("\n=== Example 5: Stress Test ===")

    config = CapitalConfig(
        max_exposure_per_market_percent=10.0,  # $20 per market
        max_total_exposure_percent=80.0,       # $160 total
    )

    allocator = CapitalAllocator(config, initial_bankroll=200.0)

    # Create 20 concurrent allocation requests
    tasks = [
        allocator.request_allocation(f"market{i}", 20.0)
        for i in range(20)
    ]

    results = await asyncio.gather(*tasks)

    # Analyze results
    successes = sum(1 for result, _ in results if result == AllocationResult.SUCCESS)
    failures = len(results) - successes

    logger.info(f"Concurrent allocations: {successes} succeeded, {failures} failed")
    report = allocator.get_allocation_report()
    logger.info(f"Final allocation: ${report['total_allocated']:.2f} "
                f"({report['utilization_percent']:.1f}%)")


async def main():
    """Run all examples"""
    await example_basic_allocation()
    await example_order_splitting()
    await example_with_recycler()
    await example_allocation_limits()
    await example_stress_test()

    logger.info("\n=== All examples completed ===")


if __name__ == "__main__":
    asyncio.run(main())
