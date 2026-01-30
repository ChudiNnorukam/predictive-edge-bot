#!/usr/bin/env python3
"""
Strategy Orchestrator
=====================

Main coordinator that runs multiple trading strategies concurrently.
Provides centralized execution, monitoring, and risk management.

Usage:
    python orchestrator.py --strategies sniper,copy_trader --dry-run
"""

import asyncio
import signal
import sys
import logging
import argparse
from typing import List, Dict, Any
from datetime import datetime

from config import load_config, LOG_FORMAT, LOG_DATE_FORMAT
from storage import PositionStore
from executor import OrderExecutor

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    datefmt=LOG_DATE_FORMAT,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/orchestrator.log"),
    ],
)
logger = logging.getLogger(__name__)


class StrategyOrchestrator:
    """Coordinates multiple trading strategies"""

    def __init__(
        self,
        config,
        strategies: List[str],
        redis_url: str = "redis://localhost:6379",
    ):
        """
        Initialize orchestrator

        Args:
            config: Bot configuration
            strategies: List of strategy names to run
            redis_url: Redis connection URL
        """
        self.config = config
        self.strategy_names = strategies
        self.running = False
        self.start_time: datetime = None

        # Initialize storage and executor
        self.position_store = PositionStore(
            db_path="data/positions.db",
            redis_url=redis_url if not config.dry_run else None,
        )
        self.executor = OrderExecutor(config, self.position_store)

        # Strategy instances will be loaded dynamically
        self.strategies: List[Any] = []
        self.strategy_tasks: List[asyncio.Task] = []

    async def load_strategies(self):
        """Dynamically load strategy classes"""
        logger.info(f"Loading strategies: {', '.join(self.strategy_names)}")

        for strategy_name in self.strategy_names:
            try:
                if strategy_name == "sniper":
                    from strategies.sniper import SniperStrategy

                    # TODO: Get token_id from config or market discovery
                    strategy = SniperStrategy(
                        config=self.config,
                        executor=self.executor,
                        position_store=self.position_store,
                    )
                    self.strategies.append(strategy)

                elif strategy_name == "copy_trader":
                    from strategies.copy_trader import CopyTraderStrategy

                    strategy = CopyTraderStrategy(
                        config=self.config,
                        executor=self.executor,
                        position_store=self.position_store,
                        target_address="distinct-baguette",  # TODO: Make configurable
                    )
                    self.strategies.append(strategy)

                else:
                    logger.warning(f"Unknown strategy: {strategy_name}")

            except ImportError as e:
                logger.error(f"Failed to load strategy {strategy_name}: {e}")

        logger.info(f"Loaded {len(self.strategies)} strategies")

    async def run(self):
        """Main orchestrator loop"""
        self.running = True
        self.start_time = datetime.utcnow()

        logger.info("=" * 80)
        logger.info("Polymarket Strategy Orchestrator Starting")
        logger.info("=" * 80)
        logger.info(f"Dry Run: {self.config.dry_run}")
        logger.info(f"Strategies: {', '.join(self.strategy_names)}")
        logger.info(f"Start Time: {self.start_time}")
        logger.info("=" * 80)

        # Load strategies
        await self.load_strategies()

        if not self.strategies:
            logger.error("No strategies loaded - exiting")
            return

        # Validate all strategies
        for strategy in self.strategies:
            if not await strategy.validate_config():
                logger.error(f"Strategy {strategy.name} config validation failed")
                return

        # Start all strategies concurrently
        logger.info("Starting all strategies...")
        for strategy in self.strategies:
            strategy.start()
            task = asyncio.create_task(self._run_strategy_with_monitoring(strategy))
            self.strategy_tasks.append(task)

        # Start monitoring loop
        monitor_task = asyncio.create_task(self._monitor_loop())

        # Wait for all tasks
        try:
            await asyncio.gather(*self.strategy_tasks, monitor_task)
        except asyncio.CancelledError:
            logger.info("Tasks cancelled")

        # Cleanup
        await self._cleanup()

        logger.info("=" * 80)
        logger.info("Orchestrator Stopped")
        await self._print_final_stats()
        logger.info("=" * 80)

    async def _run_strategy_with_monitoring(self, strategy):
        """Run strategy with error handling and monitoring"""
        try:
            logger.info(f"Strategy {strategy.name} started")
            await strategy.run()
        except Exception as e:
            logger.error(f"Strategy {strategy.name} crashed: {e}", exc_info=True)
        finally:
            await strategy.cleanup()
            logger.info(f"Strategy {strategy.name} stopped")

    async def _monitor_loop(self):
        """Monitor strategies and print periodic status"""
        iteration = 0
        while self.running:
            await asyncio.sleep(60)  # Check every minute
            iteration += 1

            if iteration % 5 == 0:  # Log every 5 minutes
                await self._print_status()

    async def _print_status(self):
        """Print current status"""
        runtime = (datetime.utcnow() - self.start_time).total_seconds()

        logger.info("=" * 80)
        logger.info(f"STATUS UPDATE | Runtime: {runtime / 3600:.1f}h")
        logger.info("-" * 80)

        # Strategy metrics
        for strategy in self.strategies:
            metrics = strategy.get_metrics()
            logger.info(
                f"{metrics['name']:20s} | "
                f"Signals: {metrics['signals_detected']:4d} | "
                f"Trades: {metrics['trades_executed']:4d} | "
                f"Profit: ${metrics['total_profit']:8.2f}"
            )

        # Executor metrics
        exec_metrics = self.executor.get_metrics()
        logger.info("-" * 80)
        logger.info(
            f"{'Executor':20s} | "
            f"Orders: {exec_metrics['total_orders']:4d} | "
            f"Success: {exec_metrics['success_rate']:.1%} | "
            f"Latency: {exec_metrics['avg_latency_seconds']:.3f}s"
        )

        # Database stats
        db_stats = await self.position_store.get_stats()
        logger.info(
            f"{'Database':20s} | "
            f"Trades: {db_stats.get('total_trades', 0):4d} | "
            f"Volume: ${db_stats.get('total_volume', 0):10.2f} | "
            f"Profit: ${db_stats.get('total_profit', 0):8.2f}"
        )

        logger.info("=" * 80)

    async def _print_final_stats(self):
        """Print final statistics"""
        runtime = (datetime.utcnow() - self.start_time).total_seconds()

        logger.info(f"Total Runtime: {runtime / 3600:.2f} hours")
        logger.info("")

        for strategy in self.strategies:
            metrics = strategy.get_metrics()
            logger.info(f"Strategy: {metrics['name']}")
            logger.info(f"  Signals Detected: {metrics['signals_detected']}")
            logger.info(f"  Trades Executed:  {metrics['trades_executed']}")
            logger.info(f"  Win Rate:         {metrics['win_rate']:.1%}")
            logger.info(f"  Total Profit:     ${metrics['total_profit']:.2f}")
            logger.info(f"  Total Invested:   ${metrics['total_invested']:.2f}")
            logger.info("")

        exec_metrics = self.executor.get_metrics()
        logger.info(f"Executor Performance:")
        logger.info(f"  Total Orders:     {exec_metrics['total_orders']}")
        logger.info(f"  Success Rate:     {exec_metrics['success_rate']:.1%}")
        logger.info(f"  Avg Latency:      {exec_metrics['avg_latency_seconds']:.3f}s")

    async def _cleanup(self):
        """Cleanup resources"""
        logger.info("Cleaning up...")

        # Stop all strategies
        for strategy in self.strategies:
            strategy.stop()

        # Cancel all tasks
        for task in self.strategy_tasks:
            if not task.done():
                task.cancel()

        # Wait for tasks to complete
        await asyncio.gather(*self.strategy_tasks, return_exceptions=True)

    def stop(self):
        """Stop orchestrator"""
        logger.info("Stop signal received")
        self.running = False


def main():
    parser = argparse.ArgumentParser(description="Polymarket Strategy Orchestrator")
    parser.add_argument(
        "--strategies",
        "-s",
        required=True,
        help="Comma-separated list of strategies (e.g., sniper,copy_trader)",
    )
    parser.add_argument(
        "--redis-url",
        default="redis://localhost:6379",
        help="Redis connection URL",
    )
    args = parser.parse_args()

    # Load config
    try:
        config = load_config()
    except ValueError as e:
        logger.error(f"Config error: {e}")
        sys.exit(1)

    # Parse strategies
    strategies = [s.strip() for s in args.strategies.split(",")]

    # Create orchestrator
    orchestrator = StrategyOrchestrator(
        config=config,
        strategies=strategies,
        redis_url=args.redis_url,
    )

    # Set up signal handlers
    def signal_handler(sig, frame):
        orchestrator.stop()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Run
    try:
        asyncio.run(orchestrator.run())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")


if __name__ == "__main__":
    main()
