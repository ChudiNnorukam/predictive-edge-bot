#!/usr/bin/env python3
"""
Scaling Orchestrator v2
=======================

Production-ready orchestrator integrating all scaling modules:
- Market State Machine (core/)
- Multi-Market Scheduler (scheduler/)
- Risk Management System (risk/)
- Capital Allocation & Recycling (capital/)
- Metrics & Observability (metrics/)

Main trading loop:
  1. Check risk (kill switches, circuit breakers)
  2. Get eligible markets from state machine
  3. Request capital allocation
  4. Execute via scheduler
  5. Record metrics
  6. Recycle capital on resolution

Usage:
    python orchestrator_v2.py --dry-run --log-level INFO
"""

import asyncio
import signal
import sys
import logging
import argparse
from typing import Optional, Dict, Any
from datetime import datetime, timezone
from dataclasses import dataclass

# Core modules
from core import (
    MarketState,
    Market,
    SchedulerConfig as CoreSchedulerConfig,
    MarketStateMachine,
    MarketPriorityQueue,
)

# Scheduler modules
from scheduler import (
    MultiMarketScheduler,
    ExecutionWindow,
    SchedulerConfig,
)

# Risk modules
from risk import (
    KillSwitchManager,
    KillSwitchConfig,
    KillSwitchType,
    CircuitBreakerRegistry,
    CircuitBreakerConfig,
    ExposureManager,
    ExposureConfig,
    RiskManager,
)

# Capital modules
from capital import (
    CapitalConfig,
    CapitalAllocator,
    CapitalRecycler,
    AllocationResult,
)

# Metrics modules
from metrics import (
    MetricsCollector,
    MetricsConfig,
    MetricsDashboard,
    DashboardView,
)

# Utilities
from config import load_config
from executor import OrderRequest, OrderExecutor
from storage import PositionStore

# Configure logging
LOG_FORMAT = "%(asctime)s | %(name)s | %(levelname)s | %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

logger = logging.getLogger(__name__)


@dataclass
class OrchestratorConfig:
    """Configuration for ScalingOrchestrator"""

    # Subsystem configs
    scheduler_config: SchedulerConfig
    kill_switch_config: KillSwitchConfig
    circuit_breaker_config: CircuitBreakerConfig
    exposure_config: ExposureConfig
    capital_config: CapitalConfig
    metrics_config: MetricsConfig

    # Runtime
    dry_run: bool = False
    log_level: str = "INFO"
    tick_interval_ms: int = 10
    metrics_refresh_ms: int = 1000

    # Limits
    max_iterations: Optional[int] = None  # None = run forever

    @classmethod
    def create_default(cls) -> "OrchestratorConfig":
        """Create default configuration"""
        return cls(
            scheduler_config=SchedulerConfig(),
            kill_switch_config=KillSwitchConfig(),
            circuit_breaker_config=CircuitBreakerConfig(),
            exposure_config=ExposureConfig(),
            capital_config=CapitalConfig(),
            metrics_config=MetricsConfig(),
            dry_run=False,
            log_level="INFO",
        )


class ScalingOrchestrator:
    """
    Production-ready orchestrator integrating all scaling modules.

    Manages:
    - Market discovery and state transitions
    - Risk controls (kill switches, circuit breakers, exposure limits)
    - Capital allocation and recycling
    - Multi-market concurrent execution
    - Comprehensive metrics and observability

    Thread-safe async implementation with graceful shutdown.
    """

    def __init__(
        self,
        config: OrchestratorConfig,
        position_store: Optional[PositionStore] = None,
        order_executor: Optional[OrderExecutor] = None,
    ):
        """
        Initialize ScalingOrchestrator

        Args:
            config: OrchestratorConfig instance
            position_store: Optional PositionStore (created if not provided)
            order_executor: Optional OrderExecutor (created if not provided)
        """
        self.config = config
        self.running = False
        self.start_time: Optional[datetime] = None
        self.iteration = 0

        # Set logging
        logging.basicConfig(
            level=getattr(logging, config.log_level),
            format=LOG_FORMAT,
            datefmt=LOG_DATE_FORMAT,
            handlers=[
                logging.StreamHandler(),
                logging.FileHandler("logs/orchestrator_v2.log"),
            ],
        )

        # Initialize storage and executor
        self.position_store = position_store or PositionStore(
            db_path="data/positions.db",
            redis_url=None if config.dry_run else "redis://localhost:6379",
        )

        # Load bot config for executor
        bot_config = load_config()
        self.order_executor = order_executor or OrderExecutor(
            bot_config,
            self.position_store,
        )

        # Initialize core subsystems
        self.state_machine = MarketStateMachine()
        self.priority_queue = MarketPriorityQueue()

        # Initialize risk subsystems
        self.kill_switches = KillSwitchManager(config.kill_switch_config)
        self.circuit_breakers = CircuitBreakerRegistry(
            config.circuit_breaker_config
        )
        self.exposure_manager = ExposureManager(
            config.exposure_config,
            initial_bankroll=bot_config.starting_bankroll,
        )
        self.risk_manager = RiskManager(
            self.kill_switches,
            self.circuit_breakers,
            self.exposure_manager,
        )

        # Initialize scheduler
        self.scheduler = MultiMarketScheduler(
            config.scheduler_config,
            self.order_executor,
        )

        # Initialize capital management
        self.capital_allocator = CapitalAllocator(config.capital_config)
        self.capital_recycler = CapitalRecycler(
            initial_capital=bot_config.starting_bankroll
        )

        # Initialize metrics
        self.metrics_collector = MetricsCollector(config.metrics_config)
        self.metrics_dashboard = MetricsDashboard(
            self.metrics_collector,
            refresh_interval_ms=config.metrics_refresh_ms,
        )

        # Task management
        self._background_tasks: list[asyncio.Task] = []
        self._subsystem_lock = asyncio.Lock()

        logger.info("ScalingOrchestrator initialized successfully")

    async def start(self) -> None:
        """
        Start all subsystems and main trading loop.

        Subsystems started in order:
        1. Storage & Executor
        2. State Machine
        3. Risk Management
        4. Capital Management
        5. Scheduler
        6. Metrics Dashboard

        Raises:
            RuntimeError: If any subsystem fails to start
        """
        if self.running:
            logger.warning("Orchestrator already running")
            return

        self.running = True
        self.start_time = datetime.now(timezone.utc)

        logger.info("=" * 80)
        logger.info("ScalingOrchestrator Starting")
        logger.info("=" * 80)
        logger.info(f"Dry Run: {self.config.dry_run}")
        logger.info(f"Log Level: {self.config.log_level}")
        logger.info(f"Start Time: {self.start_time.isoformat()}")
        logger.info("=" * 80)

        try:
            # Start subsystems in order
            async with self._subsystem_lock:
                logger.info("Starting subsystems...")

                # 1. Storage (already initialized)
                logger.info("✓ Position store ready")

                # 2. Executor (already initialized)
                logger.info("✓ Order executor ready")

                # 3. State machine (stateless, ready)
                logger.info("✓ Market state machine ready")

                # 4. Risk management (no async init needed currently)
                logger.info("✓ Kill switches ready")
                logger.info("✓ Circuit breakers ready")
                logger.info("✓ Exposure manager ready")

                # 5. Capital management (no async init needed currently)
                logger.info("✓ Capital allocator ready")
                logger.info("✓ Capital recycler ready")

                # 6. Scheduler (starts monitoring)
                await self.scheduler.start()
                logger.info("✓ Multi-market scheduler started")

                # 7. Metrics (starts dashboard if enabled)
                await self.metrics_dashboard.start()
                logger.info("✓ Metrics dashboard started")

            logger.info("=" * 80)
            logger.info("All subsystems started successfully")
            logger.info("=" * 80)

            # Start main trading loop
            await self._trading_loop()

        except Exception as e:
            logger.error(f"Orchestrator startup failed: {e}", exc_info=True)
            self.running = False
            raise
        finally:
            await self.stop()

    async def _trading_loop(self) -> None:
        """
        Main trading loop orchestrating all subsystems.

        Iteration flow:
        1. Check risk (kill switches, circuit breakers)
        2. Get eligible markets from state machine
        3. Request capital allocation
        4. Submit to scheduler
        5. Record metrics
        6. Process capital recycling
        7. Log status periodically

        Runs until stop() called or max_iterations reached.
        """
        logger.info("Trading loop started")

        try:
            while self.running:
                self.iteration += 1
                iteration_start = datetime.now(timezone.utc)

                try:
                    # 1. CHECK RISK CONTROLS
                    # Kill switch checks are passive (check state, don't modify)
                    if self.kill_switches.is_trading_halted():
                        active_switches = self.kill_switches.get_active_switches()
                        logger.warning(
                            f"Trading halted by kill switches: {active_switches}"
                        )
                        await asyncio.sleep(
                            self.config.tick_interval_ms / 1000.0
                        )
                        continue

                    # 2. GET ELIGIBLE MARKETS
                    # State machine tracks all markets and their states
                    eligible_markets = self.state_machine.get_eligible_markets()

                    if eligible_markets:
                        logger.debug(
                            f"Iteration {self.iteration}: Found {len(eligible_markets)} "
                            f"eligible markets"
                        )

                    # 3. PROCESS EACH ELIGIBLE MARKET
                    for market in eligible_markets:
                        # Pre-execution risk check
                        can_trade, reason = await self.risk_manager.pre_execution_check(
                            market_id=market.token_id,
                            amount=market.current_ask or 0.5,  # Estimate
                            feed_last_update=market.last_update or iteration_start,
                        )

                        if not can_trade:
                            logger.info(
                                f"Market {market.token_id} blocked by risk: {reason}"
                            )
                            # Transition to ON_HOLD
                            await self.state_machine.transition(
                                market.token_id,
                                MarketState.ON_HOLD,
                                f"Risk check failed: {reason}",
                            )
                            continue

                        # Request capital allocation
                        allocation = await self.capital_allocator.request_allocation(
                            market_id=market.token_id,
                            amount=market.current_ask or 0.5,
                            priority=self.priority_queue.get_priority(market.token_id),
                        )

                        if allocation.result == AllocationResult.APPROVED:
                            logger.info(
                                f"Capital allocated to {market.token_id}: "
                                f"${allocation.amount_approved:.2f}"
                            )

                            # Update market with allocated capital
                            market.allocated_capital = allocation.amount_approved

                            # Submit to scheduler
                            submitted = await self.scheduler.submit_market(
                                market,
                                allocated_capital=allocation.amount_approved,
                            )

                            if submitted:
                                # Transition to EXECUTING
                                await self.state_machine.transition(
                                    market.token_id,
                                    MarketState.EXECUTING,
                                    "Submitted to scheduler",
                                )

                                logger.info(
                                    f"Market {market.token_id} submitted to scheduler"
                                )
                        else:
                            logger.debug(
                                f"Capital allocation rejected for {market.token_id}: "
                                f"{allocation.reason}"
                            )

                    # 4. RECORD METRICS
                    # Collect from all subsystems
                    scheduler_metrics = self.scheduler.get_metrics()
                    risk_metrics = {
                        "kill_switches_active": len(
                            self.kill_switches.get_active_switches()
                        ),
                        "circuit_breakers_open": len(
                            self.circuit_breakers.get_open_breakers()
                        ),
                        "exposure_used_percent": (
                            self.exposure_manager.get_current_exposure() /
                            self.exposure_manager.bankroll * 100
                        ),
                    }
                    executor_metrics = self.order_executor.get_metrics()
                    capital_metrics = {
                        "total_capital": self.capital_allocator.get_total_capital(),
                        "available_capital": (
                            self.capital_allocator.get_total_capital() -
                            self.capital_allocator.get_allocated_capital()
                        ),
                        "pending_recycles": len(
                            self.capital_recycler.get_pending_recycles()
                        ),
                    }

                    await self.metrics_collector.record_iteration(
                        iteration=self.iteration,
                        num_markets_watching=(
                            len(self.state_machine.get_markets_by_state(
                                MarketState.WATCHING
                            ))
                        ),
                        num_markets_eligible=(
                            len(self.state_machine.get_markets_by_state(
                                MarketState.ELIGIBLE
                            ))
                        ),
                        num_markets_executing=(
                            len(self.state_machine.get_markets_by_state(
                                MarketState.EXECUTING
                            ))
                        ),
                        metrics={
                            "scheduler": scheduler_metrics,
                            "risk": risk_metrics,
                            "executor": executor_metrics,
                            "capital": capital_metrics,
                        },
                    )

                    # 5. PROCESS CAPITAL RECYCLING
                    # Check for completed markets and recycle capital
                    done_markets = self.state_machine.get_markets_by_state(
                        MarketState.DONE
                    )
                    for market in done_markets:
                        # Queue capital recycle
                        await self.capital_recycler.queue_recycle(
                            market_id=market.token_id,
                            amount=market.allocated_capital,
                            pnl=market.pnl,
                        )

                    # 6. CHECK ITERATION LIMIT
                    if (
                        self.config.max_iterations
                        and self.iteration >= self.config.max_iterations
                    ):
                        logger.info(
                            f"Max iterations ({self.config.max_iterations}) reached"
                        )
                        self.running = False
                        break

                    # 7. PERIODIC STATUS LOGGING
                    if self.iteration % 100 == 0:
                        await self._log_status()

                    # Sleep before next iteration
                    await asyncio.sleep(
                        self.config.tick_interval_ms / 1000.0
                    )

                except Exception as e:
                    logger.error(
                        f"Iteration {self.iteration} error: {e}",
                        exc_info=True,
                    )
                    await asyncio.sleep(1)  # Brief backoff on error

        except asyncio.CancelledError:
            logger.info("Trading loop cancelled")
            raise

        finally:
            logger.info("Trading loop ended")

    async def _log_status(self) -> None:
        """Log periodic status update with all subsystem metrics"""
        runtime = (datetime.now(timezone.utc) - self.start_time).total_seconds()

        logger.info("=" * 80)
        logger.info(f"STATUS UPDATE | Iteration: {self.iteration} | Runtime: {runtime / 3600:.1f}h")
        logger.info("-" * 80)

        # Market states
        watching = self.state_machine.get_markets_by_state(MarketState.WATCHING)
        eligible = self.state_machine.get_markets_by_state(MarketState.ELIGIBLE)
        executing = self.state_machine.get_markets_by_state(MarketState.EXECUTING)
        done = self.state_machine.get_markets_by_state(MarketState.DONE)

        logger.info(
            f"Markets: WATCHING={len(watching)} ELIGIBLE={len(eligible)} "
            f"EXECUTING={len(executing)} DONE={len(done)}"
        )

        # Risk status
        kill_switches = self.kill_switches.get_active_switches()
        breakers = self.circuit_breakers.get_open_breakers()
        exposure = (
            self.exposure_manager.get_current_exposure() /
            self.exposure_manager.bankroll * 100
        )

        logger.info(
            f"Risk: KillSwitches={len(kill_switches)} "
            f"CircuitBreakers={len(breakers)} Exposure={exposure:.1f}%"
        )

        # Execution metrics
        exec_metrics = self.order_executor.get_metrics()
        logger.info(
            f"Executor: Orders={exec_metrics['total_orders']} "
            f"Success={exec_metrics['success_rate']:.1%} "
            f"AvgLatency={exec_metrics['avg_latency_seconds']:.3f}s"
        )

        # Capital status
        total_capital = self.capital_allocator.get_total_capital()
        allocated = self.capital_allocator.get_allocated_capital()
        available = total_capital - allocated

        logger.info(
            f"Capital: Total=${total_capital:.2f} "
            f"Allocated=${allocated:.2f} Available=${available:.2f}"
        )

        # Scheduler metrics
        scheduler_metrics = self.scheduler.get_metrics()
        logger.info(
            f"Scheduler: ActiveExecutions={scheduler_metrics.get('active_executions', 0)} "
            f"CompletedExecutions={scheduler_metrics.get('completed_executions', 0)}"
        )

        logger.info("=" * 80)

    async def stop(self) -> None:
        """
        Gracefully stop all subsystems.

        Shutdown order:
        1. Stop trading loop (running = False)
        2. Stop scheduler
        3. Stop metrics dashboard
        4. Cancel background tasks
        5. Cleanup resources
        """
        if not self.running:
            logger.warning("Orchestrator not running")
            return

        logger.info("=" * 80)
        logger.info("ScalingOrchestrator Stopping")
        logger.info("=" * 80)

        self.running = False

        try:
            async with self._subsystem_lock:
                # Stop scheduler
                await self.scheduler.stop()
                logger.info("✓ Scheduler stopped")

                # Stop metrics dashboard
                await self.metrics_dashboard.stop()
                logger.info("✓ Metrics dashboard stopped")

                # Cancel background tasks
                for task in self._background_tasks:
                    if not task.done():
                        task.cancel()

                if self._background_tasks:
                    await asyncio.gather(
                        *self._background_tasks,
                        return_exceptions=True,
                    )
                    logger.info(
                        f"✓ {len(self._background_tasks)} background tasks cancelled"
                    )

        except Exception as e:
            logger.error(f"Error during shutdown: {e}", exc_info=True)

        # Print final statistics
        await self._print_final_stats()

        logger.info("=" * 80)
        logger.info("ScalingOrchestrator Stopped")
        logger.info("=" * 80)

    async def _print_final_stats(self) -> None:
        """Print comprehensive final statistics"""
        if not self.start_time:
            return

        runtime = (datetime.now(timezone.utc) - self.start_time).total_seconds()

        logger.info("")
        logger.info("FINAL STATISTICS")
        logger.info("-" * 80)
        logger.info(f"Total Runtime: {runtime / 3600:.2f} hours")
        logger.info(f"Total Iterations: {self.iteration}")
        logger.info(f"Avg Iteration Time: {runtime / max(self.iteration, 1) * 1000:.2f}ms")
        logger.info("")

        # Market lifecycle stats
        all_markets = self.state_machine.get_all_markets()
        logger.info(f"Markets Tracked: {len(all_markets)}")
        for state in MarketState:
            markets = self.state_machine.get_markets_by_state(state)
            logger.info(f"  {state.value}: {len(markets)}")
        logger.info("")

        # Executor stats
        exec_metrics = self.order_executor.get_metrics()
        logger.info("Executor Performance:")
        logger.info(f"  Total Orders: {exec_metrics['total_orders']}")
        logger.info(f"  Successful: {exec_metrics['successful_orders']}")
        logger.info(f"  Failed: {exec_metrics['failed_orders']}")
        logger.info(f"  Success Rate: {exec_metrics['success_rate']:.1%}")
        logger.info(f"  Avg Latency: {exec_metrics['avg_latency_seconds']:.3f}s")
        logger.info("")

        # Risk stats
        logger.info("Risk Management:")
        logger.info(f"  Current Exposure: {self.exposure_manager.get_current_exposure():.2f}")
        logger.info(f"  Exposure Limit: {self.exposure_manager.limit:.2f}")
        logger.info(f"  Kill Switches Triggered: {self.kill_switches.total_triggers}")
        logger.info("")

        # Capital stats
        total_capital = self.capital_allocator.get_total_capital()
        allocated = self.capital_allocator.get_allocated_capital()
        logger.info("Capital Management:")
        logger.info(f"  Total Capital: ${total_capital:.2f}")
        logger.info(f"  Allocated: ${allocated:.2f}")
        logger.info(f"  Available: ${total_capital - allocated:.2f}")
        logger.info(f"  Pending Recycles: {len(self.capital_recycler.get_pending_recycles())}")
        logger.info("")


def main():
    """Entry point for ScalingOrchestrator"""
    parser = argparse.ArgumentParser(
        description="Polymarket Scaling Orchestrator v2"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run in dry-run mode (no real trades)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        help="Max iterations before stopping (for testing)",
    )

    args = parser.parse_args()

    # Create configuration
    config = OrchestratorConfig.create_default()
    config.dry_run = args.dry_run
    config.log_level = args.log_level
    config.max_iterations = args.max_iterations

    # Create orchestrator
    orchestrator = ScalingOrchestrator(config)

    # Setup signal handlers
    def signal_handler(sig, frame):
        logger.info(f"Received signal {sig}")
        orchestrator.running = False

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Run orchestrator
    try:
        asyncio.run(orchestrator.start())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
