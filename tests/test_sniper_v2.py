"""
Tests for Enhanced Sniper Bot (sniper_v2.py)
=============================================

Verifies integration with scaling modules:
- MarketStateMachine
- RiskManager
- CapitalAllocator
- MetricsCollector
"""

import pytest
import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from sniper_v2 import EnhancedSniperBot, SniperConfig
from core import Market, MarketState, MarketStateMachine, SchedulerConfig
from risk import KillSwitchConfig, CircuitBreakerConfig, ExposureConfig, KillSwitchType
from capital import CapitalConfig, AllocationResult
from metrics import TradeMetrics


@pytest.fixture
def mock_bot_config():
    """Mock bot configuration"""
    config = MagicMock()
    config.dry_run = True
    config.private_key = "0x" + "a" * 64
    config.wallet_address = "0x" + "b" * 40
    config.chain_id = 137
    config.clob_api_key = ""
    config.clob_secret = ""
    config.clob_passphrase = ""
    config.starting_bankroll = 10000.0
    config.max_buy_price = 0.99
    return config


@pytest.fixture
def sniper_config():
    """Sniper configuration for tests"""
    return SniperConfig(
        max_buy_price=0.99,
        min_price_threshold=0.50,
        execution_window_seconds=1.0,
        multi_market_mode=False,
        capital_config=CapitalConfig(
            max_exposure_per_market_percent=5.0,
            max_exposure_per_market_absolute=50.0,
        ),
        risk_config=KillSwitchConfig(
            stale_feed_threshold_ms=500,
        ),
    )


@pytest.fixture
def mock_clob_client():
    """Mock CLOB client"""
    with patch("sniper_v2.ClobClient") as mock:
        client = MagicMock()
        client.create_or_derive_api_creds.return_value = MagicMock()
        mock.return_value = client
        yield client


@pytest.fixture
def enhanced_sniper(mock_bot_config, sniper_config, mock_clob_client):
    """Create enhanced sniper instance with mocked dependencies"""
    with patch("sniper_v2.get_trade_logger") as mock_logger:
        mock_logger.return_value = MagicMock()
        bot = EnhancedSniperBot(
            bot_config=mock_bot_config,
            sniper_config=sniper_config,
            token_id="test_token_123",
        )
        yield bot


class TestEnhancedSniperInitialization:
    """Test sniper initialization and module setup"""

    def test_scaling_modules_initialized(self, enhanced_sniper):
        """Verify all scaling modules are initialized"""
        assert enhanced_sniper.state_machine is not None
        assert enhanced_sniper.risk_manager is not None
        assert enhanced_sniper.capital_allocator is not None
        assert enhanced_sniper.metrics_collector is not None
        assert enhanced_sniper.kill_switches is not None
        assert enhanced_sniper.circuit_breakers is not None
        assert enhanced_sniper.exposure_manager is not None

    def test_initial_state(self, enhanced_sniper):
        """Verify initial bot state"""
        assert enhanced_sniper.running is False
        assert enhanced_sniper.trades_executed == 0
        assert enhanced_sniper.trades_blocked_by_risk == 0
        assert enhanced_sniper.trades_blocked_by_capital == 0
        assert enhanced_sniper.total_profit == 0.0


class TestMarketStateIntegration:
    """Test integration with MarketStateMachine"""

    @pytest.mark.asyncio
    async def test_add_market_to_state_machine(self, enhanced_sniper):
        """Test adding market to state machine"""
        market_data = {
            "token_id": "test_market_001",
            "condition_id": "cond_001",
            "question": "Will BTC be above $50k?",
            "end_date": datetime.now(timezone.utc) + timedelta(minutes=5),
            "neg_risk": False,
        }

        added = await enhanced_sniper.add_market_to_state_machine(market_data)
        assert added is True

        # Verify market is tracked via internal dict
        assert "test_market_001" in enhanced_sniper.state_machine.markets
        market = enhanced_sniper.state_machine.markets["test_market_001"]
        assert market.state == MarketState.DISCOVERED

    @pytest.mark.asyncio
    async def test_duplicate_market_not_added(self, enhanced_sniper):
        """Test that duplicate markets are not added twice"""
        market_data = {
            "token_id": "test_market_002",
            "condition_id": "cond_002",
            "question": "Test market",
            "end_date": datetime.now(timezone.utc) + timedelta(minutes=5),
            "neg_risk": False,
        }

        # Add first time
        added1 = await enhanced_sniper.add_market_to_state_machine(market_data)
        assert added1 is True

        # Try to add again
        added2 = await enhanced_sniper.add_market_to_state_machine(market_data)
        assert added2 is False


class TestExecutionCriteria:
    """Test trade execution criteria"""

    def test_should_execute_within_window(self, enhanced_sniper):
        """Test execution within time window"""
        market = Market(
            token_id="test_001",
            condition_id="cond_001",
            question="Test",
            end_time=datetime.now(timezone.utc) + timedelta(seconds=0.5),
        )
        prices = {"ask": 0.95, "last": 0.96}

        assert enhanced_sniper.should_execute(market, prices) is True

    def test_should_not_execute_outside_window(self, enhanced_sniper):
        """Test no execution outside time window"""
        market = Market(
            token_id="test_002",
            condition_id="cond_002",
            question="Test",
            end_time=datetime.now(timezone.utc) + timedelta(seconds=10),
        )
        prices = {"ask": 0.95, "last": 0.96}

        assert enhanced_sniper.should_execute(market, prices) is False

    def test_should_not_execute_price_too_high(self, enhanced_sniper):
        """Test no execution when price above max"""
        market = Market(
            token_id="test_003",
            condition_id="cond_003",
            question="Test",
            end_time=datetime.now(timezone.utc) + timedelta(seconds=0.5),
        )
        prices = {"ask": 0.995, "last": 0.99}

        assert enhanced_sniper.should_execute(market, prices) is False

    def test_should_not_execute_price_too_low(self, enhanced_sniper):
        """Test no execution when price below threshold"""
        market = Market(
            token_id="test_004",
            condition_id="cond_004",
            question="Test",
            end_time=datetime.now(timezone.utc) + timedelta(seconds=0.5),
        )
        prices = {"ask": 0.95, "last": 0.40}  # Last price below 0.50 threshold

        assert enhanced_sniper.should_execute(market, prices) is False


class TestRiskIntegration:
    """Test integration with RiskManager"""

    @pytest.mark.asyncio
    async def test_pre_execution_risk_check_passes(self, enhanced_sniper):
        """Test successful pre-execution risk check"""
        can_execute, reason = await enhanced_sniper.pre_execution_checks(
            "test_market",
            amount=10.0,
        )

        # Should pass with default config
        assert can_execute is True
        assert "Approved" in reason

    @pytest.mark.asyncio
    async def test_kill_switch_blocks_execution(self, enhanced_sniper):
        """Test that active kill switch blocks execution"""
        # Activate kill switch using correct API
        await enhanced_sniper.kill_switches.activate(
            KillSwitchType.MANUAL,
            "Test halt",
        )

        can_execute, reason = await enhanced_sniper.pre_execution_checks(
            "test_market",
            amount=10.0,
        )

        assert can_execute is False
        assert "Risk check failed" in reason
        assert enhanced_sniper.trades_blocked_by_risk == 1


class TestCapitalIntegration:
    """Test integration with CapitalAllocator"""

    @pytest.mark.asyncio
    async def test_capital_allocation_success(self, enhanced_sniper):
        """Test successful capital allocation"""
        result, allocated = await enhanced_sniper.capital_allocator.request_allocation(
            "market_001",
            25.0,  # requested_amount positional
        )

        assert result == AllocationResult.SUCCESS
        assert allocated == 25.0  # Within limits

    @pytest.mark.asyncio
    async def test_capital_allocation_capped(self, enhanced_sniper):
        """Test capital allocation capped at per-market limit"""
        result, allocated = await enhanced_sniper.capital_allocator.request_allocation(
            "market_002",
            100.0,  # Request more than limit
        )

        assert result == AllocationResult.SUCCESS
        assert allocated == 50.0  # Capped at $50 absolute limit


class TestMetricsIntegration:
    """Test integration with MetricsCollector"""

    @pytest.mark.asyncio
    async def test_trade_metrics_recorded(self, enhanced_sniper):
        """Test that trade metrics are recorded"""
        trade_metrics = TradeMetrics(
            timestamp=datetime.now(timezone.utc),
            market_id="test_market",
            attempted=True,
            filled=True,
            fill_amount=10.0,
            total_latency_ms=25.0,
            entry_price=0.95,
            actual_pnl=0.50,
        )
        await enhanced_sniper.metrics_collector.record_trade(trade_metrics)

        # Verify metrics recorded
        stats = await enhanced_sniper.metrics_collector.get_session_stats()
        assert stats["trades_attempted"] >= 1


class TestDryRunExecution:
    """Test dry run execution mode"""

    @pytest.mark.asyncio
    async def test_dry_run_execution(self, enhanced_sniper):
        """Test execution in dry run mode"""
        market = Market(
            token_id="dry_run_test",
            condition_id="cond_dry",
            question="Test market",
            end_time=datetime.now(timezone.utc) + timedelta(seconds=0.5),
            is_neg_risk=False,
        )

        # Add market to state machine
        await enhanced_sniper.state_machine.add_market(market)

        # Set prices
        enhanced_sniper.market_prices["dry_run_test"] = {
            "bid": 0.94,
            "ask": 0.95,
            "last": 0.96,
        }

        # Transition to eligible state
        await enhanced_sniper.state_machine.update_price("dry_run_test", 0.94, 0.95)
        await enhanced_sniper.state_machine.check_transitions()

        # Manually transition to ELIGIBLE for test (may need time condition)
        market = enhanced_sniper.state_machine.markets["dry_run_test"]

        # Check if trade would execute (depends on state)
        prices = enhanced_sniper.market_prices["dry_run_test"]
        should_exec = enhanced_sniper.should_execute(market, prices)

        # Should be True since we're within the execution window
        assert should_exec is True


class TestPriceUpdates:
    """Test price update handling"""

    @pytest.mark.asyncio
    async def test_handle_price_update(self, enhanced_sniper):
        """Test price update processing"""
        # Subscribe to market
        await enhanced_sniper.subscribe_to_market("price_test_001")

        # Simulate price update
        data = {
            "asset_id": "price_test_001",
            "bids": [{"price": "0.94"}],
            "asks": [{"price": "0.96"}],
        }

        await enhanced_sniper.handle_price_update(data)

        # Verify prices updated
        prices = enhanced_sniper.market_prices.get("price_test_001", {})
        assert prices["bid"] == 0.94
        assert prices["ask"] == 0.96


class TestMultiMarketMode:
    """Test multi-market mode functionality"""

    @pytest.mark.asyncio
    async def test_multi_market_subscription(self, enhanced_sniper):
        """Test subscribing to multiple markets"""
        enhanced_sniper.sniper_config.multi_market_mode = True

        # Subscribe to multiple markets
        await enhanced_sniper.subscribe_to_market("multi_001")
        await enhanced_sniper.subscribe_to_market("multi_002")
        await enhanced_sniper.subscribe_to_market("multi_003")

        assert len(enhanced_sniper.subscribed_markets) == 3
        assert "multi_001" in enhanced_sniper.subscribed_markets
        assert "multi_002" in enhanced_sniper.subscribed_markets
        assert "multi_003" in enhanced_sniper.subscribed_markets
