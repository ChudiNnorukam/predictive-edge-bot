"""
Risk Controls System Tests
===========================

Comprehensive tests for kill switches, circuit breakers, and exposure management.
Tests both individual components and integrated RiskManager.
"""

import asyncio
import pytest
from datetime import datetime, timedelta, timezone
from typing import Tuple

# Import risk components
from risk import (
    KillSwitchManager,
    KillSwitchConfig,
    KillSwitchType,
    CircuitBreakerRegistry,
    CircuitBreakerConfig,
    CircuitState,
    ExposureManager,
    ExposureConfig,
    RiskManager,
)


# ============================================================================
# KILL SWITCH TESTS
# ============================================================================

@pytest.mark.asyncio
async def test_kill_switch_stale_feed():
    """Test stale feed detection."""
    config = KillSwitchConfig(stale_feed_threshold_ms=500)
    manager = KillSwitchManager(config)

    # Fresh feed - should not trigger
    now = datetime.now(timezone.utc)
    assert not await manager.check_stale_feed(now)
    assert not manager.is_trading_halted()

    # Stale feed - should trigger
    stale_time = now - timedelta(milliseconds=600)
    assert await manager.check_stale_feed(stale_time)
    assert manager.is_trading_halted()

    # Feed recovered - should clear
    assert not await manager.check_stale_feed(datetime.now(timezone.utc))
    assert not manager.is_trading_halted()


@pytest.mark.asyncio
async def test_kill_switch_rpc_lag():
    """Test RPC lag detection."""
    config = KillSwitchConfig(rpc_lag_threshold_ms=300)
    manager = KillSwitchManager(config)

    # Low latency - should not trigger
    assert not await manager.check_rpc_lag(100.0)
    assert not manager.is_trading_halted()

    # High latency - should trigger
    assert await manager.check_rpc_lag(400.0)
    assert manager.is_trading_halted()

    # Recovered - should clear
    assert not await manager.check_rpc_lag(100.0)
    assert not manager.is_trading_halted()


@pytest.mark.asyncio
async def test_kill_switch_order_limit():
    """Test outstanding order limit."""
    config = KillSwitchConfig(max_outstanding_orders=10)
    manager = KillSwitchManager(config)

    # Below limit - should not trigger
    assert not await manager.check_order_limit(5)
    assert not manager.is_trading_halted()

    # At limit - should trigger
    assert await manager.check_order_limit(10)
    assert manager.is_trading_halted()

    # Below limit - should clear
    assert not await manager.check_order_limit(5)
    assert not manager.is_trading_halted()


@pytest.mark.asyncio
async def test_kill_switch_daily_loss():
    """Test daily loss limit."""
    config = KillSwitchConfig(daily_loss_limit_percent=5.0)
    manager = KillSwitchManager(config)
    bankroll = 10000.0
    max_loss = bankroll * 0.05  # $500

    # Small loss - should not trigger
    await manager.update_daily_pnl(-100.0)
    assert not await manager.check_daily_loss(bankroll - 100)
    assert not manager.is_trading_halted()

    # Exceeds loss limit - should trigger
    await manager.update_daily_pnl(-450.0)  # Total -$550
    assert await manager.check_daily_loss(bankroll - 550)
    assert manager.is_trading_halted()

    # Reset and recover
    await manager.reset_daily()
    assert not manager.is_trading_halted()


@pytest.mark.asyncio
async def test_kill_switch_manual_activation():
    """Test manual kill switch activation/deactivation."""
    config = KillSwitchConfig()
    manager = KillSwitchManager(config)

    # Manually activate
    await manager.activate(KillSwitchType.MANUAL, "Operator intervention")
    assert manager.is_trading_halted()
    assert KillSwitchType.MANUAL in manager.get_active_switches()

    # Deactivate
    await manager.deactivate(KillSwitchType.MANUAL)
    assert not manager.is_trading_halted()


@pytest.mark.asyncio
async def test_kill_switch_multiple_active():
    """Test multiple simultaneous kill switches."""
    config = KillSwitchConfig(rpc_lag_threshold_ms=300, max_outstanding_orders=5)
    manager = KillSwitchManager(config)

    # Trigger two switches
    await manager.check_rpc_lag(400.0)
    await manager.check_order_limit(10)

    assert manager.is_trading_halted()
    switches = manager.get_active_switches()
    assert len(switches) == 2
    assert KillSwitchType.RPC_LAG in switches
    assert KillSwitchType.MAX_ORDERS in switches


# ============================================================================
# CIRCUIT BREAKER TESTS
# ============================================================================

@pytest.mark.asyncio
async def test_circuit_breaker_closed_to_open():
    """Test circuit breaker transitions CLOSED -> OPEN."""
    config = CircuitBreakerConfig(failure_threshold=3, recovery_timeout_seconds=60)
    registry = CircuitBreakerRegistry(config)

    market_id = "market_123"

    # All requests allowed in CLOSED state
    assert await registry.can_execute(market_id)

    # Record failures
    await registry.record_failure(market_id, "error 1")
    assert await registry.can_execute(market_id)  # Still CLOSED

    await registry.record_failure(market_id, "error 2")
    assert await registry.can_execute(market_id)  # Still CLOSED

    # Third failure trips to OPEN
    await registry.record_failure(market_id, "error 3")
    assert not await registry.can_execute(market_id)  # OPEN

    open_breakers = await registry.get_open_breakers()
    assert market_id in open_breakers


@pytest.mark.asyncio
async def test_circuit_breaker_recovery():
    """Test circuit breaker transitions OPEN -> HALF_OPEN -> CLOSED."""
    config = CircuitBreakerConfig(failure_threshold=3, recovery_timeout_seconds=1)
    registry = CircuitBreakerRegistry(config)

    market_id = "market_123"
    breaker = await registry.get_or_create(market_id)

    # Trip to OPEN
    for _ in range(3):
        await registry.record_failure(market_id, "error")

    assert not await registry.can_execute(market_id)
    assert breaker.state == CircuitState.OPEN

    # Wait for recovery timeout
    await asyncio.sleep(1.1)

    # Manually check recovery (normally called during can_execute)
    breaker._check_recovery()
    assert breaker.state == CircuitState.HALF_OPEN

    # Success in HALF_OPEN -> CLOSED
    await registry.record_success(market_id)
    assert breaker.state == CircuitState.CLOSED
    assert await registry.can_execute(market_id)


@pytest.mark.asyncio
async def test_circuit_breaker_per_market_isolation():
    """Test circuit breaker isolation across markets."""
    config = CircuitBreakerConfig(failure_threshold=2)
    registry = CircuitBreakerRegistry(config)

    market_1 = "market_1"
    market_2 = "market_2"

    # Trip market_1
    await registry.record_failure(market_1, "error")
    await registry.record_failure(market_1, "error")

    # market_1 should be OPEN, market_2 should be CLOSED
    assert not await registry.can_execute(market_1)
    assert await registry.can_execute(market_2)


# ============================================================================
# EXPOSURE MANAGER TESTS
# ============================================================================

def test_exposure_manager_initialization():
    """Test exposure manager initialization."""
    config = ExposureConfig(max_exposure_per_market_percent=5.0)
    manager = ExposureManager(config, initial_bankroll=10000.0)

    assert manager.bankroll == 10000.0
    assert manager.config.max_exposure_per_market_percent == 5.0


@pytest.mark.asyncio
async def test_exposure_allocation():
    """Test exposure allocation and limits."""
    config = ExposureConfig(
        max_exposure_per_market_percent=5.0,
        max_exposure_per_market_absolute=50.0,
        max_total_exposure_percent=30.0,
    )
    manager = ExposureManager(config, initial_bankroll=10000.0)

    market_id = "market_123"

    # Small allocation should succeed
    can_allocate, reason = await manager.can_allocate(market_id, 100.0)
    assert can_allocate, reason

    await manager.allocate(market_id, 100.0)
    exposure = await manager.get_market_exposure(market_id)
    assert exposure == 100.0


@pytest.mark.asyncio
async def test_exposure_per_market_limit():
    """Test per-market percentage limit."""
    config = ExposureConfig(max_exposure_per_market_percent=5.0)
    manager = ExposureManager(config, initial_bankroll=10000.0)

    # Max per market = 5% of $10k = $500
    market_id = "market_123"

    # At limit
    can_allocate, reason = await manager.can_allocate(market_id, 500.0)
    assert can_allocate

    await manager.allocate(market_id, 500.0)

    # Exceed limit
    can_allocate, reason = await manager.can_allocate(market_id, 1.0)
    assert not can_allocate
    assert "$500" in reason or "5.0%" in reason


@pytest.mark.asyncio
async def test_exposure_absolute_limit():
    """Test per-market absolute limit."""
    config = ExposureConfig(
        max_exposure_per_market_percent=50.0,  # High percent
        max_exposure_per_market_absolute=50.0,  # Low absolute
    )
    manager = ExposureManager(config, initial_bankroll=10000.0)

    market_id = "market_123"

    # At absolute limit
    can_allocate, reason = await manager.can_allocate(market_id, 50.0)
    assert can_allocate

    await manager.allocate(market_id, 50.0)

    # Exceed absolute limit (even though % limit allows it)
    can_allocate, reason = await manager.can_allocate(market_id, 1.0)
    assert not can_allocate


@pytest.mark.asyncio
async def test_exposure_total_limit():
    """Test total portfolio exposure limit."""
    config = ExposureConfig(
        max_exposure_per_market_percent=100.0,  # No per-market limit
        max_total_exposure_percent=30.0,
    )
    manager = ExposureManager(config, initial_bankroll=10000.0)

    # Max total = 30% of $10k = $3k
    market_1 = "market_1"
    market_2 = "market_2"

    # Allocate to market 1
    await manager.allocate(market_1, 1500.0)
    await manager.allocate(market_2, 1500.0)

    # At limit
    can_allocate, reason = await manager.can_allocate("market_3", 0.01)
    assert not can_allocate


@pytest.mark.asyncio
async def test_exposure_release():
    """Test releasing exposure."""
    config = ExposureConfig(max_exposure_per_market_percent=5.0)
    manager = ExposureManager(config, initial_bankroll=10000.0)

    market_id = "market_123"

    # Allocate
    await manager.allocate(market_id, 100.0)
    assert await manager.get_market_exposure(market_id) == 100.0

    # Partial release
    released = await manager.release(market_id, 30.0)
    assert released == 30.0
    assert await manager.get_market_exposure(market_id) == 70.0

    # Release all
    released = await manager.release(market_id)
    assert released == 70.0
    assert await manager.get_market_exposure(market_id) == 0.0


@pytest.mark.asyncio
async def test_exposure_pnl_tracking():
    """Test P&L recording and bankroll updates."""
    config = ExposureConfig()
    manager = ExposureManager(config, initial_bankroll=10000.0)

    market_id = "market_123"

    # Record loss
    await manager.record_pnl(market_id, -100.0)
    assert manager.bankroll == 9900.0

    # Record gain
    await manager.record_pnl(market_id, 500.0)
    assert manager.bankroll == 10400.0


@pytest.mark.asyncio
async def test_exposure_available_capital():
    """Test available capital calculation."""
    config = ExposureConfig()
    manager = ExposureManager(config, initial_bankroll=10000.0)

    assert await manager.get_available_capital() == 10000.0

    # Allocate $2k
    await manager.allocate("market_1", 2000.0)
    assert await manager.get_available_capital() == 8000.0

    # Allocate $3k
    await manager.allocate("market_2", 3000.0)
    assert await manager.get_available_capital() == 5000.0


# ============================================================================
# UNIFIED RISK MANAGER TESTS
# ============================================================================

@pytest.mark.asyncio
async def test_risk_manager_global_halt():
    """Test RiskManager global trading halt."""
    kill_switches = KillSwitchManager(KillSwitchConfig())
    circuit_breakers = CircuitBreakerRegistry(CircuitBreakerConfig())
    exposure = ExposureManager(ExposureConfig(), initial_bankroll=10000.0)
    risk = RiskManager(kill_switches, circuit_breakers, exposure)

    # Should allow trading
    can_trade, reason = await risk.can_trade()
    assert can_trade

    # Activate kill switch
    await kill_switches.activate(KillSwitchType.MANUAL, "Test halt")

    # Should block trading
    can_trade, reason = await risk.can_trade()
    assert not can_trade
    assert "MANUAL" in reason


@pytest.mark.asyncio
async def test_risk_manager_pre_execution():
    """Test comprehensive pre-execution check."""
    kill_switches = KillSwitchManager(
        KillSwitchConfig(stale_feed_threshold_ms=500)
    )
    circuit_breakers = CircuitBreakerRegistry(CircuitBreakerConfig())
    exposure = ExposureManager(
        ExposureConfig(max_exposure_per_market_percent=5.0),
        initial_bankroll=10000.0,
    )
    risk = RiskManager(kill_switches, circuit_breakers, exposure)

    market_id = "market_123"
    feed_time = datetime.now(timezone.utc)

    # Should pass all checks
    can_execute, reason = await risk.pre_execution_check(
        market_id=market_id,
        amount=100.0,
        feed_last_update=feed_time,
    )
    assert can_execute, reason

    # Stale feed should fail
    stale_time = feed_time - timedelta(milliseconds=600)
    can_execute, reason = await risk.pre_execution_check(
        market_id=market_id,
        amount=100.0,
        feed_last_update=stale_time,
    )
    assert not can_execute
    assert "stale" in reason.lower() or "feed" in reason.lower()


@pytest.mark.asyncio
async def test_risk_manager_post_execution():
    """Test post-execution recording."""
    kill_switches = KillSwitchManager(KillSwitchConfig())
    circuit_breakers = CircuitBreakerRegistry(CircuitBreakerConfig(failure_threshold=3))
    exposure = ExposureManager(ExposureConfig(), initial_bankroll=10000.0)
    risk = RiskManager(kill_switches, circuit_breakers, exposure)

    market_id = "market_123"

    # Record success with P&L
    await risk.post_execution_record(
        market_id=market_id,
        success=True,
        pnl=50.0,
        latency_ms=100.0,
    )

    # Bankroll should be updated
    assert exposure.bankroll == 10050.0

    # Record failures
    await risk.post_execution_record(market_id=market_id, success=False)
    await risk.post_execution_record(market_id=market_id, success=False)

    # Circuit breaker should still allow execution (not at threshold yet)
    assert await circuit_breakers.can_execute(market_id)

    # Third failure should trip
    await risk.post_execution_record(market_id=market_id, success=False)
    assert not await circuit_breakers.can_execute(market_id)


# ============================================================================
# INTEGRATION TEST
# ============================================================================

@pytest.mark.asyncio
async def test_full_risk_integration():
    """Integration test: realistic trading scenario."""
    # Setup
    kill_switches = KillSwitchManager(
        KillSwitchConfig(
            stale_feed_threshold_ms=500,
            rpc_lag_threshold_ms=300,
            max_outstanding_orders=10,
            daily_loss_limit_percent=5.0,
        )
    )
    circuit_breakers = CircuitBreakerRegistry(
        CircuitBreakerConfig(failure_threshold=3, recovery_timeout_seconds=60)
    )
    exposure = ExposureManager(
        ExposureConfig(
            max_exposure_per_market_percent=5.0,
            max_total_exposure_percent=30.0,
            max_exposure_per_market_absolute=50.0,
        ),
        initial_bankroll=10000.0,
    )
    risk = RiskManager(kill_switches, circuit_breakers, exposure)

    feed_time = datetime.now(timezone.utc)

    # Trade 1: Should succeed
    can_execute, _ = await risk.pre_execution_check("market_1", 100.0, feed_time)
    assert can_execute

    await risk.post_execution_record("market_1", success=True, pnl=25.0)
    assert exposure.bankroll == 10025.0

    # Trade 2: Should succeed (different market)
    can_execute, _ = await risk.pre_execution_check("market_2", 150.0, feed_time)
    assert can_execute

    await risk.post_execution_record("market_2", success=True, pnl=50.0)
    assert exposure.bankroll == 10075.0

    # Trade 3: Stale feed should block
    stale_time = feed_time - timedelta(milliseconds=600)
    can_execute, reason = await risk.pre_execution_check("market_3", 100.0, stale_time)
    assert not can_execute

    # Trade 4: High RPC lag should activate kill switch
    await kill_switches.check_rpc_lag(400.0)
    can_execute, _ = await risk.can_trade()
    assert not can_execute


# ============================================================================
# MAIN TEST EXECUTION
# ============================================================================

if __name__ == "__main__":
    # Run tests
    pytest.main([__file__, "-v", "--tb=short"])
