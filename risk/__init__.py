"""
Risk Management System
======================

Comprehensive risk control framework for Polymarket bot.

Components:
- KillSwitchManager: Global trading halts on critical thresholds
- CircuitBreakerRegistry: Per-market failure isolation
- ExposureManager: Capital allocation and limit enforcement
- RiskManager: Unified facade combining all risk systems

Usage:
    from risk import KillSwitchManager, CircuitBreakerRegistry, ExposureManager, RiskManager
    from risk.kill_switches import KillSwitchConfig, KillSwitchType
    from risk.circuit_breakers import CircuitBreakerConfig
    from risk.exposure_manager import ExposureConfig

    # Initialize components
    kill_switches = KillSwitchManager(KillSwitchConfig())
    circuit_breakers = CircuitBreakerRegistry(CircuitBreakerConfig())
    exposure = ExposureManager(ExposureConfig(), initial_bankroll=10000.0)

    # Create unified manager
    risk = RiskManager(kill_switches, circuit_breakers, exposure)

    # Pre-execution check
    can_trade, reason = await risk.pre_execution_check(
        market_id="market_123",
        amount=100.0,
        feed_last_update=datetime.now(timezone.utc)
    )
"""

from .kill_switches import (
    KillSwitchManager,
    KillSwitchConfig,
    KillSwitchType,
    RiskManager,
)
from .circuit_breakers import (
    CircuitBreaker,
    CircuitBreakerRegistry,
    CircuitBreakerConfig,
    CircuitState,
)
from .exposure_manager import (
    ExposureManager,
    ExposureConfig,
)

__all__ = [
    # Kill Switches
    "KillSwitchManager",
    "KillSwitchConfig",
    "KillSwitchType",
    # Circuit Breakers
    "CircuitBreaker",
    "CircuitBreakerRegistry",
    "CircuitBreakerConfig",
    "CircuitState",
    # Exposure
    "ExposureManager",
    "ExposureConfig",
    # Unified
    "RiskManager",
]
