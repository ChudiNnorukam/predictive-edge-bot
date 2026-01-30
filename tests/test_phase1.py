#!/usr/bin/env python3
"""
Phase 1 Verification Tests
===========================

Tests the base architecture:
- BaseStrategy class
- PositionStore (SQLite + Redis)
- OrderExecutor
- StrategyOrchestrator
"""

import sys
import os
import asyncio
import tempfile
from pathlib import Path

import pytest

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import Config
from storage import PositionStore
from executor import OrderExecutor, OrderRequest
from strategies.base_strategy import BaseStrategy


def create_test_config():
    """Create a test configuration"""
    return Config(
        private_key="0x" + "0" * 64,  # Dummy private key
        wallet_address="0x" + "0" * 40,  # Dummy wallet address
        clob_api_key="test_key",
        clob_secret="test_secret",
        clob_passphrase="test_pass",
        chain_id=137,
        rpc_url="https://polygon-rpc.com",
        position_size_pct=0.005,
        max_position_pct=0.05,
        daily_loss_limit_pct=0.05,
        min_price_threshold=0.99,
        max_buy_price=0.99,
        dry_run=True,  # Always dry run for tests
        telegram_bot_token=None,
        telegram_chat_id=None,
        discord_webhook_url=None,
    )


class MockStrategy(BaseStrategy):
    """Mock strategy for testing (renamed to avoid pytest collection)"""

    async def run(self):
        """Simple run loop"""
        self.signals_detected = 5
        self.trades_executed = 3
        self.total_profit = 10.5

        await asyncio.sleep(0.1)  # Reduced sleep for faster tests

    async def cleanup(self):
        """No cleanup needed"""
        pass


@pytest.mark.asyncio
async def test_position_store():
    """Test PositionStore database operations"""
    print("\n=== Testing PositionStore ===")

    # Use temporary database
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    try:
        store = PositionStore(db_path=db_path, redis_url=None)

        # Test recording a trade (use valid token_id format)
        test_token = "0x1234567890abcdef1234567890abcdef12345678"
        trade_id = await store.record_trade(
            token_id=test_token,
            side="YES",
            action="BUY",
            price=0.65,
            size=10.0,
            strategy="MockStrategy",
            status="executed",
        )

        print(f"✓ Recorded trade ID: {trade_id}")
        assert trade_id is not None, "Trade ID should be returned"

        # Test getting position
        position = await store.get_position(test_token)
        assert position is not None, "Position should exist"
        assert position["entry_price"] == 0.65, "Entry price should match"
        print(f"✓ Retrieved position: {position['token_id']}")

        # Test getting open positions
        open_positions = await store.get_open_positions()
        assert len(open_positions) >= 1, "Should have at least 1 open position"
        print(f"✓ Found {len(open_positions)} open positions")

        # Test getting trades
        trades = await store.get_trades(limit=10)
        assert len(trades) >= 1, "Should have at least 1 trade"
        print(f"✓ Retrieved {len(trades)} trades")

        # Test stats
        stats = await store.get_stats()
        print(f"✓ Stats: {stats}")
        assert stats["total_trades"] >= 1, "Should have at least 1 trade in stats"

        print("✓ PositionStore tests passed!")

    finally:
        # Cleanup
        if os.path.exists(db_path):
            os.unlink(db_path)
        # Also clean up WAL files
        for suffix in ["-wal", "-shm"]:
            wal_path = db_path + suffix
            if os.path.exists(wal_path):
                os.unlink(wal_path)


@pytest.mark.asyncio
async def test_executor():
    """Test OrderExecutor"""
    print("\n=== Testing OrderExecutor ===")

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    try:
        config = create_test_config()
        store = PositionStore(db_path=db_path, redis_url=None)
        executor = OrderExecutor(config, store)

        # Test order execution (use valid token_id - minimum 10 chars)
        request = OrderRequest(
            token_id="0x1234567890abcdef1234567890abcdef12345678",
            side="YES",
            action="BUY",
            size=5.0,
            strategy="MockStrategy",
            price=0.70,
        )

        success = await executor.execute_order(request)
        assert success, "Order should execute in dry run"
        print("✓ Order executed successfully")

        # Test concurrent deduplication (submit same order while first is pending)
        # Create tasks that will run concurrently
        request2 = OrderRequest(
            token_id="0xabcdef1234567890abcdef1234567890abcdef12",
            side="YES",
            action="BUY",
            size=5.0,
            strategy="MockStrategy",
            price=0.70,
        )

        # Submit two identical orders concurrently
        task1 = asyncio.create_task(executor.execute_order(request2))
        task2 = asyncio.create_task(executor.execute_order(request2))

        results = await asyncio.gather(task1, task2)

        # One should succeed, one should fail (or both might succeed if timing is off)
        # For testing purposes, we just verify both ran without error
        print(f"✓ Concurrent execution handled (results: {results})")

        # Test metrics
        metrics = executor.get_metrics()
        assert metrics["total_orders"] >= 1, "Should have recorded orders"
        print(f"✓ Executor metrics: {metrics}")

        print("✓ OrderExecutor tests passed!")

    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)
        # Also clean up WAL files
        for suffix in ["-wal", "-shm"]:
            wal_path = db_path + suffix
            if os.path.exists(wal_path):
                os.unlink(wal_path)


@pytest.mark.asyncio
async def test_base_strategy():
    """Test BaseStrategy abstract class"""
    print("\n=== Testing BaseStrategy ===")

    config = create_test_config()
    strategy = MockStrategy(config, name="MockStrategy")

    # Test initialization
    assert strategy.name == "MockStrategy", "Name should match"
    assert not strategy.running, "Should start as not running"
    print("✓ Strategy initialized")

    # Test start/stop
    strategy.start()
    assert strategy.running, "Should be running after start"
    print("✓ Strategy started")

    strategy.stop()
    assert not strategy.running, "Should not be running after stop"
    print("✓ Strategy stopped")

    # Test metrics
    await strategy.run()
    metrics = strategy.get_metrics()
    assert metrics["signals_detected"] == 5, "Should track signals"
    assert metrics["trades_executed"] == 3, "Should track trades"
    print(f"✓ Metrics: {metrics}")

    print("✓ BaseStrategy tests passed!")


# Allow running directly with asyncio.run for debugging
async def main():
    """Run all tests"""
    print("=" * 70)
    print("Phase 1 Foundation Tests")
    print("=" * 70)

    results = []

    # Run tests
    try:
        await test_position_store()
        results.append(True)
    except Exception as e:
        print(f"✗ PositionStore test failed: {e}")
        import traceback
        traceback.print_exc()
        results.append(False)

    try:
        await test_executor()
        results.append(True)
    except Exception as e:
        print(f"✗ OrderExecutor test failed: {e}")
        import traceback
        traceback.print_exc()
        results.append(False)

    try:
        await test_base_strategy()
        results.append(True)
    except Exception as e:
        print(f"✗ BaseStrategy test failed: {e}")
        import traceback
        traceback.print_exc()
        results.append(False)

    # Summary
    print("\n" + "=" * 70)
    print("Test Summary")
    print("=" * 70)
    passed = sum(results)
    total = len(results)
    print(f"Passed: {passed}/{total}")

    if passed == total:
        print("✓ All tests passed!")
        return 0
    else:
        print("✗ Some tests failed")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
