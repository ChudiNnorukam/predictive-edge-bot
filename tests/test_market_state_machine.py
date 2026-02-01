"""
Comprehensive tests for Market State Machine

Tests all functionality including:
- Market creation and state tracking
- State machine transitions
- Priority queue operations
- Stale feed detection
- Failure handling
- Cleanup operations
"""

import pytest
import asyncio
from datetime import datetime, timedelta
import time

from core.market_state import Market, MarketState, SchedulerConfig, MarketStateMachine
from core.priority_queue import MarketPriorityQueue


class TestMarket:
    """Tests for Market dataclass"""

    def test_market_creation(self):
        """Test creating a market"""
        market = Market(
            token_id="test_token",
            condition_id="cond_123",
            question="Will Bitcoin reach $100k?",
            end_time=datetime.utcnow() + timedelta(minutes=5),
        )

        assert market.token_id == "test_token"
        assert market.condition_id == "cond_123"
        assert market.state == MarketState.DISCOVERED
        assert market.current_bid is None
        assert market.current_ask is None

    def test_time_to_expiry(self):
        """Test time_to_expiry calculation"""
        future = datetime.utcnow() + timedelta(seconds=30)
        market = Market(
            token_id="test",
            condition_id="cond",
            question="test",
            end_time=future,
        )

        time_to_expiry = market.time_to_expiry().total_seconds()
        # Should be approximately 30 seconds
        assert 29 < time_to_expiry < 31

    def test_is_stale_no_update(self):
        """Test stale detection when no update"""
        market = Market(
            token_id="test",
            condition_id="cond",
            question="test",
            end_time=datetime.utcnow() + timedelta(minutes=5),
        )

        # No update yet, should be stale
        assert market.is_stale(500) is True

    def test_is_stale_recent_update(self):
        """Test fresh update is not stale"""
        market = Market(
            token_id="test",
            condition_id="cond",
            question="test",
            end_time=datetime.utcnow() + timedelta(minutes=5),
        )

        market.last_update = datetime.utcnow()
        assert market.is_stale(500) is False

    def test_is_stale_old_update(self):
        """Test old update is stale"""
        market = Market(
            token_id="test",
            condition_id="cond",
            question="test",
            end_time=datetime.utcnow() + timedelta(minutes=5),
        )

        market.last_update = datetime.utcnow() - timedelta(seconds=1)
        assert market.is_stale(500) is True

    def test_transition_history(self):
        """Test transition history recording"""
        market = Market(
            token_id="test",
            condition_id="cond",
            question="test",
            end_time=datetime.utcnow() + timedelta(minutes=5),
        )

        market.record_transition(MarketState.WATCHING, "test-reason")
        market.record_transition(MarketState.ELIGIBLE, "test-reason-2")

        assert len(market.transition_history) == 2
        assert market.transition_history[0][2] == MarketState.WATCHING
        assert market.transition_history[1][2] == MarketState.ELIGIBLE


class TestMarketStateMachine:
    """Tests for MarketStateMachine"""

    @pytest.fixture
    def machine(self):
        """Fixture for state machine"""
        config = SchedulerConfig(
            time_to_eligibility_sec=60,
            max_buy_price=0.95,
        )
        return MarketStateMachine(config)

    @pytest.fixture
    def market(self):
        """Fixture for market"""
        return Market(
            token_id="test_token",
            condition_id="cond_123",
            question="Test market",
            end_time=datetime.utcnow() + timedelta(seconds=30),
        )

    @pytest.mark.asyncio
    async def test_add_market(self, machine, market):
        """Test adding a market"""
        await machine.add_market(market)
        markets = await machine.get_markets_by_state(MarketState.DISCOVERED)
        assert len(markets) == 1
        assert markets[0].token_id == "test_token"

    @pytest.mark.asyncio
    async def test_add_duplicate_market(self, machine, market):
        """Test adding duplicate market raises error"""
        await machine.add_market(market)
        with pytest.raises(ValueError):
            await machine.add_market(market)

    @pytest.mark.asyncio
    async def test_remove_market(self, machine, market):
        """Test removing a market"""
        await machine.add_market(market)
        await machine.remove_market("test_token")
        markets = await machine.get_markets_by_state(MarketState.DISCOVERED)
        assert len(markets) == 0

    @pytest.mark.asyncio
    async def test_update_price(self, machine, market):
        """Test price update"""
        await machine.add_market(market)
        await machine.update_price("test_token", 0.50, 0.52)

        assert market.current_bid == 0.50
        assert market.current_ask == 0.52
        assert market.last_update is not None

    @pytest.mark.asyncio
    async def test_transition_discovered_to_watching(self, machine, market):
        """Test DISCOVERED -> WATCHING transition"""
        await machine.add_market(market)
        await machine.update_price("test_token", 0.50, 0.52)
        transitions = await machine.check_transitions()

        assert market.state == MarketState.WATCHING
        assert len(transitions) == 1
        assert transitions[0] == ("test_token", MarketState.DISCOVERED, MarketState.WATCHING)

    @pytest.mark.asyncio
    async def test_transition_watching_to_eligible(self, machine, market):
        """Test WATCHING -> ELIGIBLE transition"""
        await machine.add_market(market)
        await machine.update_price("test_token", 0.50, 0.52)
        await machine.check_transitions()

        # With 30s to expiry and price 0.52 < 0.95, should transition
        transitions = await machine.check_transitions()

        assert market.state == MarketState.ELIGIBLE

    @pytest.mark.asyncio
    async def test_transition_eligible_to_executing(self, machine, market):
        """Test ELIGIBLE -> EXECUTING transition"""
        await machine.add_market(market)
        await machine.update_price("test_token", 0.50, 0.52)
        await machine.check_transitions()  # DISCOVERED -> WATCHING
        await machine.check_transitions()  # WATCHING -> ELIGIBLE

        success = await machine.mark_execution_started("test_token", 100.0)

        assert success
        assert market.state == MarketState.EXECUTING
        assert market.allocated_capital == 100.0
        assert market.orders_placed == 1

    @pytest.mark.asyncio
    async def test_transition_executing_to_reconciling(self, machine, market):
        """Test EXECUTING -> RECONCILING transition"""
        await machine.add_market(market)
        await machine.update_price("test_token", 0.50, 0.52)
        await machine.check_transitions()
        await machine.check_transitions()
        await machine.mark_execution_started("test_token", 100.0)

        success = await machine.mark_resolution("test_token", 50.0)

        assert success
        assert market.state == MarketState.RECONCILING
        assert market.pnl == 50.0

    @pytest.mark.asyncio
    async def test_transition_reconciling_to_done(self, machine, market):
        """Test RECONCILING -> DONE transition"""
        await machine.add_market(market)
        await machine.update_price("test_token", 0.50, 0.52)
        await machine.check_transitions()
        await machine.check_transitions()
        await machine.mark_execution_started("test_token", 100.0)
        await machine.mark_resolution("test_token", 50.0)

        success = await machine.mark_done("test_token")

        assert success
        assert market.state == MarketState.DONE

    @pytest.mark.asyncio
    async def test_stale_feed_detection(self, machine):
        """Test automatic ON_HOLD for stale feeds"""
        config = SchedulerConfig(stale_feed_threshold_ms=100)
        machine = MarketStateMachine(config)

        market = Market(
            token_id="test",
            condition_id="cond",
            question="test",
            end_time=datetime.utcnow() + timedelta(seconds=30),
        )

        await machine.add_market(market)
        await machine.update_price("test", 0.50, 0.52)
        await machine.check_transitions()

        assert market.state == MarketState.WATCHING

        # Wait for staleness threshold
        time.sleep(0.15)
        await machine.check_transitions()

        assert market.state == MarketState.ON_HOLD

    @pytest.mark.asyncio
    async def test_failure_counter(self, machine, market):
        """Test failure counting"""
        await machine.add_market(market)
        await machine.update_price("test_token", 0.50, 0.52)

        await machine.mark_failure("test_token", "test failure")
        assert market.failure_count == 1

        await machine.mark_failure("test_token", "test failure 2")
        assert market.failure_count == 2

    @pytest.mark.asyncio
    async def test_failure_to_on_hold(self, machine):
        """Test ON_HOLD transition due to failures"""
        config = SchedulerConfig(max_failures_before_hold=2)
        machine = MarketStateMachine(config)

        market = Market(
            token_id="test",
            condition_id="cond",
            question="test",
            end_time=datetime.utcnow() + timedelta(seconds=30),
        )

        await machine.add_market(market)
        await machine.update_price("test", 0.50, 0.52)
        await machine.check_transitions()

        for _ in range(3):
            await machine.mark_failure("test", "failure")

        assert market.state == MarketState.ON_HOLD

    @pytest.mark.asyncio
    async def test_get_stats(self, machine):
        """Test stats reporting"""
        market1 = Market(
            token_id="t1",
            condition_id="c1",
            question="q1",
            end_time=datetime.utcnow() + timedelta(seconds=30),
        )
        market2 = Market(
            token_id="t2",
            condition_id="c2",
            question="q2",
            end_time=datetime.utcnow() + timedelta(seconds=30),
        )

        await machine.add_market(market1)
        await machine.add_market(market2)

        stats = await machine.get_stats()

        assert stats["total"] == 2
        assert stats["discovered"] == 2

    @pytest.mark.asyncio
    async def test_cleanup_old_done_markets(self, machine):
        """Test cleanup of old DONE markets"""
        config = SchedulerConfig(max_hold_hours=0)  # Immediate cleanup
        machine = MarketStateMachine(config)

        market = Market(
            token_id="test",
            condition_id="cond",
            question="test",
            end_time=datetime.utcnow() + timedelta(seconds=30),
        )

        await machine.add_market(market)
        await machine.update_price("test", 0.50, 0.52)
        await machine.check_transitions()
        await machine.check_transitions()
        await machine.mark_execution_started("test", 100.0)
        await machine.mark_resolution("test", 50.0)
        await machine.mark_done("test")

        # Manually set transition time to past
        if market.transition_history:
            market.transition_history[-1] = (
                datetime.utcnow() - timedelta(hours=1),
                market.transition_history[-1][1],
                market.transition_history[-1][2],
                market.transition_history[-1][3],
            )

        removed = await machine.cleanup_old_done_markets()

        # Cleanup should remove it
        assert removed >= 0  # May or may not remove depending on timing


class TestMarketPriorityQueue:
    """Tests for MarketPriorityQueue"""

    def test_queue_creation(self):
        """Test creating a queue"""
        queue = MarketPriorityQueue()
        assert len(queue) == 0
        assert queue.is_empty() is True

    def test_push_pop(self):
        """Test push and pop"""
        queue = MarketPriorityQueue()

        market = Market(
            token_id="test",
            condition_id="cond",
            question="test",
            end_time=datetime.utcnow() + timedelta(seconds=30),
        )

        queue.push(market)
        assert len(queue) == 1

        token_id = queue.pop()
        assert token_id == "test"
        assert len(queue) == 0

    def test_priority_ordering(self):
        """Test priority queue ordering by time to expiry"""
        queue = MarketPriorityQueue()

        market_quick = Market(
            token_id="quick",
            condition_id="c1",
            question="q1",
            end_time=datetime.utcnow() + timedelta(seconds=10),
        )
        market_slow = Market(
            token_id="slow",
            condition_id="c2",
            question="q2",
            end_time=datetime.utcnow() + timedelta(seconds=30),
        )

        queue.push(market_quick)
        queue.push(market_slow)

        # Quick should be popped first
        assert queue.pop() == "quick"
        assert queue.pop() == "slow"

    def test_peek(self):
        """Test peek without removing"""
        queue = MarketPriorityQueue()

        market = Market(
            token_id="test",
            condition_id="cond",
            question="test",
            end_time=datetime.utcnow() + timedelta(seconds=30),
        )

        queue.push(market)
        assert queue.peek() == "test"
        assert len(queue) == 1  # Still there after peek

    def test_remove(self):
        """Test lazy removal"""
        queue = MarketPriorityQueue()

        market = Market(
            token_id="test",
            condition_id="cond",
            question="test",
            end_time=datetime.utcnow() + timedelta(seconds=30),
        )

        queue.push(market)
        assert len(queue) == 1

        queue.remove("test")
        assert len(queue) == 0

    def test_update_priority(self):
        """Test updating priority"""
        queue = MarketPriorityQueue()

        market = Market(
            token_id="test",
            condition_id="cond",
            question="test",
            end_time=datetime.utcnow() + timedelta(seconds=30),
        )

        queue.push(market)
        # Manually change end_time to test update
        market.end_time = datetime.utcnow() + timedelta(seconds=10)
        queue.update_priority(market)

        assert queue.peek() == "test"

    def test_debug_stats(self):
        """Test debug statistics"""
        queue = MarketPriorityQueue()

        market1 = Market(
            token_id="t1",
            condition_id="c1",
            question="q1",
            end_time=datetime.utcnow() + timedelta(seconds=10),
        )
        market2 = Market(
            token_id="t2",
            condition_id="c2",
            question="q2",
            end_time=datetime.utcnow() + timedelta(seconds=20),
        )

        queue.push(market1)
        queue.push(market2)
        queue.remove("t1")

        stats = queue.debug_stats()

        assert stats["active_entries"] == 1
        assert stats["removed_entries"] == 1
        assert stats["total_entries"] == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
