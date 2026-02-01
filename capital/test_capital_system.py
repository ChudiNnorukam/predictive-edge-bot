"""
Test suite for Capital Allocation System
=========================================

Comprehensive tests for:
- CapitalAllocator: allocation logic, limits, state tracking
- CapitalRecycler: recycling, delays, callbacks
- Integration: allocator + recycler working together
"""

import asyncio
import pytest
from datetime import datetime, timedelta

from capital.allocator import (
    CapitalConfig,
    CapitalAllocator,
    AllocationResult,
)
from capital.recycler import (
    CapitalRecycler,
    RecycleEvent,
)


class TestCapitalConfig:
    """Test configuration validation"""

    def test_default_config(self):
        """Test default configuration values"""
        config = CapitalConfig()
        assert config.max_exposure_per_market_percent == 5.0
        assert config.max_exposure_per_market_absolute == 50.0
        assert config.max_total_exposure_percent == 30.0
        assert config.min_order_size == 1.0
        assert config.order_split_threshold == 20.0
        assert config.order_split_count == 3
        assert config.recycle_delay_seconds == 5.0

    def test_custom_config(self):
        """Test custom configuration"""
        config = CapitalConfig(
            max_exposure_per_market_percent=10.0,
            max_total_exposure_percent=50.0,
        )
        assert config.max_exposure_per_market_percent == 10.0
        assert config.max_total_exposure_percent == 50.0


class TestCapitalAllocator:
    """Test CapitalAllocator functionality"""

    @pytest.fixture
    def allocator(self):
        """Create a test allocator with $200 bankroll"""
        config = CapitalConfig(
            max_exposure_per_market_percent=5.0,  # $10 per market
            max_exposure_per_market_absolute=50.0,
            max_total_exposure_percent=30.0,  # $60 total
            order_split_threshold=20.0,
            order_split_count=3,
        )
        return CapitalAllocator(config, initial_bankroll=200.0)

    @pytest.mark.asyncio
    async def test_initialization(self, allocator):
        """Test allocator initialization"""
        assert allocator.bankroll == 200.0
        assert len(allocator._allocations) == 0
        assert allocator.config.max_exposure_per_market_percent == 5.0

    @pytest.mark.asyncio
    async def test_invalid_bankroll(self):
        """Test that invalid bankroll raises error"""
        with pytest.raises(ValueError):
            CapitalAllocator(CapitalConfig(), initial_bankroll=0)

        with pytest.raises(ValueError):
            CapitalAllocator(CapitalConfig(), initial_bankroll=-100)

    @pytest.mark.asyncio
    async def test_single_allocation_success(self, allocator):
        """Test successful allocation to single market"""
        result, amount = await allocator.request_allocation("market1", 10.0)

        assert result == AllocationResult.SUCCESS
        assert amount == 10.0
        assert await allocator.get_total_allocated() == 10.0

    @pytest.mark.asyncio
    async def test_allocation_respects_market_percent_limit(self, allocator):
        """Test that per-market percent limit is enforced"""
        # Request $15, but limit is 5% of $200 = $10
        result, amount = await allocator.request_allocation("market1", 15.0)

        assert result == AllocationResult.SUCCESS
        assert amount == 10.0  # Capped at limit

    @pytest.mark.asyncio
    async def test_allocation_respects_absolute_limit(self, allocator):
        """Test that absolute per-market limit is enforced"""
        # Change config to have lower absolute limit
        allocator.config.max_exposure_per_market_absolute = 5.0

        result, amount = await allocator.request_allocation("market1", 10.0)

        assert result == AllocationResult.SUCCESS
        assert amount == 5.0  # Capped at absolute limit

    @pytest.mark.asyncio
    async def test_allocation_respects_total_limit(self, allocator):
        """Test that total exposure limit is enforced"""
        # Allocate to 6 markets, each gets $10 (total $60 = 30% of $200)
        for i in range(6):
            result, amount = await allocator.request_allocation(f"market{i}", 10.0)
            assert result == AllocationResult.SUCCESS
            assert amount == 10.0

        # Market 7 should fail - total limit exceeded
        result, amount = await allocator.request_allocation("market7", 10.0)
        assert result == AllocationResult.TOTAL_LIMIT_EXCEEDED
        assert amount == 0.0

    @pytest.mark.asyncio
    async def test_allocation_already_allocated(self, allocator):
        """Test that double allocation fails"""
        result1, amount1 = await allocator.request_allocation("market1", 10.0)
        assert result1 == AllocationResult.SUCCESS

        result2, amount2 = await allocator.request_allocation("market1", 10.0)
        assert result2 == AllocationResult.ALREADY_ALLOCATED
        assert amount2 == 0.0

    @pytest.mark.asyncio
    async def test_allocation_invalid_amount(self, allocator):
        """Test that invalid amounts are rejected"""
        result, amount = await allocator.request_allocation("market1", 0.0)
        assert result == AllocationResult.INVALID_AMOUNT
        assert amount == 0.0

        result, amount = await allocator.request_allocation("market2", -10.0)
        assert result == AllocationResult.INVALID_AMOUNT
        assert amount == 0.0

    @pytest.mark.asyncio
    async def test_order_splitting_not_triggered(self, allocator):
        """Test that small orders are not split"""
        result, amount = await allocator.request_allocation("market1", 10.0)
        assert result == AllocationResult.SUCCESS

        allocation = await allocator.get_allocation("market1")
        assert allocation.orders == []  # No splitting for small orders
        assert allocation.get_order_sizes() == [10.0]  # Single order

    @pytest.mark.asyncio
    async def test_order_splitting_triggered(self):
        """Test that large orders are split correctly"""
        # Create allocator with high limits to allow order splitting
        config = CapitalConfig(
            max_exposure_per_market_percent=50.0,  # $100 per market
            max_exposure_per_market_absolute=100.0,
            max_total_exposure_percent=100.0,
            order_split_threshold=20.0,
            order_split_count=3,
        )
        allocator = CapitalAllocator(config, initial_bankroll=200.0)

        # Request $30, which exceeds threshold of $20
        # Should split into 3 orders of $10 each
        result, amount = await allocator.request_allocation("market1", 30.0)
        assert result == AllocationResult.SUCCESS
        assert amount == 30.0

        allocation = await allocator.get_allocation("market1")
        assert len(allocation.orders) == 3
        assert sum(allocation.orders) == 30.0
        assert allocation.get_order_sizes() == allocation.orders

    @pytest.mark.asyncio
    async def test_release_allocation_with_profit(self, allocator):
        """Test releasing allocation with P&L"""
        await allocator.request_allocation("market1", 10.0)
        assert allocator.bankroll == 200.0

        released = await allocator.release_allocation("market1", pnl=0.50)
        assert released == 10.0
        assert allocator.bankroll == 200.50  # Updated with P&L
        assert await allocator.get_total_allocated() == 0.0

    @pytest.mark.asyncio
    async def test_release_allocation_with_loss(self, allocator):
        """Test releasing allocation with loss"""
        await allocator.request_allocation("market1", 10.0)

        released = await allocator.release_allocation("market1", pnl=-2.0)
        assert released == 10.0
        assert allocator.bankroll == 198.0  # Updated with loss

    @pytest.mark.asyncio
    async def test_release_nonexistent_allocation(self, allocator):
        """Test releasing non-existent allocation"""
        released = await allocator.release_allocation("nonexistent")
        assert released == 0.0

    @pytest.mark.asyncio
    async def test_update_bankroll(self, allocator):
        """Test updating bankroll"""
        await allocator.update_bankroll(250.0)
        assert allocator.bankroll == 250.0

    @pytest.mark.asyncio
    async def test_update_bankroll_invalid(self, allocator):
        """Test that invalid bankroll update fails"""
        with pytest.raises(ValueError):
            await allocator.update_bankroll(0)

    @pytest.mark.asyncio
    async def test_get_available_capital(self, allocator):
        """Test available capital calculation"""
        # Initially all capital is available
        available = await allocator.get_available_capital()
        assert available == 200.0

        # Allocate $10 (max per market with this config)
        await allocator.request_allocation("market1", 60.0)  # Requested 60, gets 10
        available = await allocator.get_available_capital()
        assert available == 190.0

    @pytest.mark.asyncio
    async def test_get_market_headroom(self, allocator):
        """Test market-specific headroom calculation"""
        # Max per market is 5% of $200 = $10
        headroom = await allocator.get_market_headroom("market1")
        assert headroom == 10.0

        # After allocating, headroom should be 0
        await allocator.request_allocation("market1", 10.0)
        headroom = await allocator.get_market_headroom("market1")
        assert headroom == 0.0

        # New market still has headroom
        headroom = await allocator.get_market_headroom("market2")
        assert headroom == 10.0

    @pytest.mark.asyncio
    async def test_get_total_headroom(self, allocator):
        """Test total portfolio headroom"""
        # Max total is 30% of $200 = $60
        headroom = await allocator.get_total_headroom()
        assert headroom == 60.0

        # After allocating $10 (max per market with this config)
        await allocator.request_allocation("market1", 40.0)  # Gets capped at $10
        headroom = await allocator.get_total_headroom()
        assert headroom == 50.0

    @pytest.mark.asyncio
    async def test_allocation_report(self, allocator):
        """Test allocation report generation"""
        await allocator.request_allocation("market1", 10.0, strategy="sniper")
        await allocator.request_allocation("market2", 20.0, strategy="copy_trader")

        report = allocator.get_allocation_report()

        assert report["bankroll"] == 200.0
        assert report["total_allocated"] == 20.0  # Each market gets $10 (5% limit)
        assert report["available"] == 180.0
        assert abs(report["utilization_percent"] - 10.0) < 0.01
        assert len(report["allocations"]) == 2
        assert report["num_allocated_markets"] == 2

    @pytest.mark.asyncio
    async def test_concurrent_allocations(self, allocator):
        """Test concurrent allocation requests"""
        # Create 7 concurrent requests, each for $10
        # With 5% per-market limit ($10 each) and 30% total limit ($60 total),
        # only 6 can succeed
        tasks = [
            allocator.request_allocation(f"market{i}", 10.0)
            for i in range(7)
        ]

        results = await asyncio.gather(*tasks)

        # First 6 should succeed (total $60 = 30% limit)
        # Last 1 should fail
        success_count = sum(1 for result, amount in results if result == AllocationResult.SUCCESS)
        assert success_count == 6


class TestCapitalRecycler:
    """Test CapitalRecycler functionality"""

    @pytest.fixture
    def setup(self):
        """Create allocator and recycler for testing"""
        config = CapitalConfig(recycle_delay_seconds=0.1)  # Short delay for tests
        allocator = CapitalAllocator(config, initial_bankroll=200.0)
        recycler = CapitalRecycler(config, allocator)

        return allocator, recycler

    @pytest.mark.asyncio
    async def test_recycler_initialization(self, setup):
        """Test recycler initialization"""
        allocator, recycler = setup
        assert recycler.config.recycle_delay_seconds == 0.1
        assert len(recycler._pending_recycles) == 0
        assert len(recycler._recycle_history) == 0

    @pytest.mark.asyncio
    async def test_queue_recycle(self, setup):
        """Test queueing a market for recycling"""
        allocator, recycler = setup

        await allocator.request_allocation("market1", 10.0)
        await recycler.queue_recycle("market1", pnl=0.50)

        pending = await recycler.get_pending_recycles()
        assert len(pending) == 1
        assert pending[0].market_id == "market1"
        assert pending[0].pnl == 0.50
        assert pending[0].is_pending()

    @pytest.mark.asyncio
    async def test_force_recycle(self, setup):
        """Test immediate recycling"""
        allocator, recycler = setup

        await allocator.request_allocation("market1", 10.0)
        await recycler.queue_recycle("market1", pnl=0.50)

        # Force immediate recycle
        released = await recycler.force_recycle("market1")

        assert released == 10.0
        assert allocator.bankroll == 200.50
        assert await allocator.get_total_allocated() == 0.0

        # Should be in history now
        history = await recycler.get_recycle_history()
        assert len(history) == 1
        assert history[0].market_id == "market1"
        assert not history[0].is_pending()

    @pytest.mark.asyncio
    async def test_recycler_auto_process(self, setup):
        """Test automatic recycling after delay"""
        allocator, recycler = setup

        # Start recycler
        await recycler.start()

        try:
            # Allocate and queue
            await allocator.request_allocation("market1", 10.0)
            await recycler.queue_recycle("market1", pnl=0.25)

            # Wait for recycler to process
            await asyncio.sleep(0.3)

            # Should be recycled now
            assert await allocator.get_total_allocated() == 0.0
            assert allocator.bankroll == 200.25

            history = await recycler.get_recycle_history()
            assert len(history) == 1

        finally:
            await recycler.stop()

    @pytest.mark.asyncio
    async def test_recycler_callback(self, setup):
        """Test callback notification on capital freed"""
        allocator, recycler = setup

        freed_amounts = []

        async def on_freed(amount):
            freed_amounts.append(amount)

        recycler.on_capital_freed = on_freed

        await recycler.start()

        try:
            await allocator.request_allocation("market1", 10.0)
            await recycler.queue_recycle("market1")

            await asyncio.sleep(0.3)

            assert len(freed_amounts) == 1
            assert freed_amounts[0] == 10.0

        finally:
            await recycler.stop()

    @pytest.mark.asyncio
    async def test_get_pending_amount(self, setup):
        """Test pending recycle amount calculation"""
        allocator, recycler = setup

        await allocator.request_allocation("market1", 10.0)
        await allocator.request_allocation("market2", 15.0)

        await recycler.queue_recycle("market1")
        await recycler.queue_recycle("market2")

        pending_amount = await recycler.get_pending_amount()
        # With 5% per-market limit on $200, each gets $10 max
        assert pending_amount == 20.0

    @pytest.mark.asyncio
    async def test_recycle_history_filtering(self, setup):
        """Test recycle history with filtering"""
        allocator, recycler = setup

        # Force recycle multiple times
        for i in range(3):
            await allocator.request_allocation(f"market{i}", 10.0)
            await recycler.force_recycle(f"market{i}")

        history = await recycler.get_recycle_history(limit=2)
        assert len(history) == 2

    @pytest.mark.asyncio
    async def test_daily_stats(self, setup):
        """Test daily statistics"""
        allocator, recycler = setup

        # Recycle a market
        await allocator.request_allocation("market1", 10.0)
        await recycler.force_recycle("market1")

        stats = recycler.get_daily_stats()
        assert stats["recycles_today"] == 1
        assert stats["capital_recycled_today"] == 10.0
        assert stats["total_pnl_today"] == 0.0

    @pytest.mark.asyncio
    async def test_recycler_start_stop(self, setup):
        """Test recycler lifecycle"""
        allocator, recycler = setup

        await recycler.start()
        assert recycler._running

        await recycler.stop()
        assert not recycler._running

    @pytest.mark.asyncio
    async def test_recycler_idempotent_start(self, setup):
        """Test that starting recycler multiple times is safe"""
        allocator, recycler = setup

        await recycler.start()
        await recycler.start()  # Should not cause error

        await recycler.stop()


class TestIntegration:
    """Integration tests for allocator + recycler"""

    @pytest.mark.asyncio
    async def test_allocate_and_recycle_cycle(self):
        """Test complete allocation and recycling cycle"""
        config = CapitalConfig(
            max_exposure_per_market_percent=25.0,  # Allow $50 per market
            max_total_exposure_percent=50.0,  # Allow $100 total
            recycle_delay_seconds=0.1,
        )
        allocator = CapitalAllocator(config, initial_bankroll=200.0)
        recycler = CapitalRecycler(config, allocator)

        await recycler.start()

        try:
            # Allocate to market 1
            result1, amount1 = await allocator.request_allocation("market1", 50.0)
            assert result1 == AllocationResult.SUCCESS
            assert amount1 == 50.0

            # Allocate to market 2
            result2, amount2 = await allocator.request_allocation("market2", 50.0)
            assert result2 == AllocationResult.SUCCESS
            assert amount2 == 50.0

            # Total allocated should be $100
            assert await allocator.get_total_allocated() == 100.0

            # Queue recycle for market 1
            await recycler.queue_recycle("market1", pnl=2.0)

            # Wait for recycling
            await asyncio.sleep(0.3)

            # Market 1 should be freed, bankroll updated
            assert await allocator.get_total_allocated() == 50.0
            assert allocator.bankroll == 202.0

            # Should be able to allocate to market 3 now
            result3, amount3 = await allocator.request_allocation("market3", 50.0)
            assert result3 == AllocationResult.SUCCESS

        finally:
            await recycler.stop()

    @pytest.mark.asyncio
    async def test_multi_market_workflow(self):
        """Test realistic multi-market workflow"""
        config = CapitalConfig(
            max_exposure_per_market_percent=10.0,
            max_total_exposure_percent=50.0,
            recycle_delay_seconds=0.05,
        )
        allocator = CapitalAllocator(config, initial_bankroll=500.0)
        recycler = CapitalRecycler(config, allocator)

        await recycler.start()

        try:
            # Allocate to 5 markets
            for i in range(5):
                result, amount = await allocator.request_allocation(f"market{i}", 50.0)
                assert result == AllocationResult.SUCCESS

            # Check portfolio state
            assert await allocator.get_total_allocated() == 250.0

            # Markets resolve and get recycled
            for i in range(5):
                await recycler.queue_recycle(f"market{i}", pnl=i * 0.50)

            # Wait for recycling
            await asyncio.sleep(0.2)

            # All should be freed
            assert await allocator.get_total_allocated() == 0.0

            # Check final bankroll (profit of 0+0.5+1.0+1.5+2.0 = 5.0)
            assert allocator.bankroll == 505.0

            # Check stats
            stats = recycler.get_daily_stats()
            assert stats["recycles_today"] == 5
            assert abs(stats["capital_recycled_today"] - 250.0) < 0.01

        finally:
            await recycler.stop()


if __name__ == "__main__":
    # Run tests with pytest
    pytest.main([__file__, "-v"])
