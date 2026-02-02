# Metrics Integration Guide

## Quick Start

### 1. Initialize in Your Bot

```python
from metrics import MetricsCollector, MetricsConfig, MetricsDashboard

# In bot __init__ or setup
config = MetricsConfig()  # Uses defaults
self.collector = MetricsCollector(config)
self.dashboard = MetricsDashboard(self.collector, config)

await self.dashboard.start()  # Optional: start live monitoring
```

### 2. Record Trades

At the point where you execute/complete trades:

```python
from datetime import datetime, timezone
from metrics import TradeMetrics
import time

# Before decision
decision_start = time.time()

# ... make decision ...

# After decision
decision_time = (time.time() - decision_start) * 1000  # Convert to ms

# Before order
order_start = time.time()

# ... execute order ...
result = await executor.execute_order(request)

# After order
order_time = (time.time() - order_start) * 1000

# Record metrics
metric = TradeMetrics(
    timestamp=datetime.now(timezone.utc),
    market_id=token_id,
    attempted=True,
    filled=result.success,
    fill_amount=size if result.success else 0.0,
    tick_to_decision_ms=decision_time,
    order_to_ack_ms=order_time,
    entry_price=price,
    expected_payout=1.0,
    edge_cents=(1.0 - price) * 100,
    actual_pnl=result.pnl if result.success else 0.0,
    outcome_reason="filled" if result.success else result.error_message,
)

await self.collector.record_trade(metric)
```

### 3. Query Metrics

In logging or status updates:

```python
# Quick one-liners
fill_rate = await self.collector.get_current_fill_rate()
pnl = await self.collector.get_current_pnl()
latency = await self.collector.get_latency_stats()

print(f"Fill: {fill_rate:.0%} | P&L: ${pnl:+.2f} | p95: {latency['p95_decision_ms']:.0f}ms")
```

---

## Integration Points

### With sniper.py

In the `SniperBot` class:

```python
from metrics import MetricsCollector, MetricsConfig, TradeMetrics
from datetime import datetime, timezone
import time

class SniperBot:
    def __init__(self, config, token_id):
        # ... existing code ...

        # Add metrics
        metrics_config = MetricsConfig()
        self.metrics = MetricsCollector(metrics_config)

    async def execute_trade(self, side: str, price: float, size: float) -> bool:
        """Execute a FOK market order with metrics tracking"""

        decision_start = time.time()
        logger.info(f"Executing {side}: ${size:.2f} @ ${price:.3f}")

        if self.config.dry_run:
            logger.info(f"[DRY RUN] WOULD BUY {side} at ${price:.3f}")
            self.signals_detected += 1

            # Record dry run
            metric = TradeMetrics(
                timestamp=datetime.now(timezone.utc),
                market_id=self.token_id,
                attempted=True,
                filled=True,
                tick_to_decision_ms=(time.time() - decision_start) * 1000,
                entry_price=price,
                edge_cents=(1.0 - price) * 100,
                outcome_reason="dry_run",
            )
            await self.metrics.record_trade(metric)
            return True

        try:
            order_start = time.time()

            order_args = MarketOrderArgs(token_id=self.token_id, amount=size, side=BUY)
            if self.is_neg_risk:
                options = PartialCreateOrderOptions(neg_risk=True)
                signed_order = self.client.create_market_order(order_args, options)
            else:
                signed_order = self.client.create_market_order(order_args)

            response = self.client.post_order(signed_order, OrderType.FOK)

            if response:
                self.trades_executed += 1
                expected_profit = (1.0 - price) * size
                self.total_profit += expected_profit

                # Record successful trade
                metric = TradeMetrics(
                    timestamp=datetime.now(timezone.utc),
                    market_id=self.token_id,
                    attempted=True,
                    filled=True,
                    fill_amount=size,
                    tick_to_decision_ms=(order_start - decision_start) * 1000,
                    order_to_ack_ms=(time.time() - order_start) * 1000,
                    entry_price=price,
                    edge_cents=(1.0 - price) * 100,
                    actual_pnl=expected_profit,
                    outcome_reason="filled",
                )
                await self.metrics.record_trade(metric)

                logger.info(f"Trade executed! Expected profit: ${expected_profit:.4f}")
                return True

        except Exception as e:
            decision_latency = (time.time() - decision_start) * 1000
            error_msg = str(e).lower()

            # Record failed trade
            reason = "unknown_error"
            if "insufficient liquidity" in error_msg:
                reason = "no_liquidity"
            elif "invalid signature" in error_msg:
                reason = "invalid_signature"
            elif "not enough balance" in error_msg:
                reason = "insufficient_balance"

            metric = TradeMetrics(
                timestamp=datetime.now(timezone.utc),
                market_id=self.token_id,
                attempted=True,
                filled=False,
                tick_to_decision_ms=decision_latency,
                entry_price=price,
                outcome_reason=reason,
            )
            await self.metrics.record_trade(metric)

            logger.error(f"Trade execution failed: {e}")

        return False
```

### With orchestrator.py

In the main orchestration loop:

```python
from metrics import print_quick_stats

async def run_session(self):
    """Main bot execution loop"""

    while self.running:
        try:
            # ... existing logic ...

            # Periodic metrics logging (every 5 minutes)
            if self.tick_count % 300 == 0:
                await print_quick_stats(self.metrics)

            self.tick_count += 1

        except Exception as e:
            logger.error(f"Execution error: {e}")
```

### With executor.py

Minimal integration - executor already tracks latency:

```python
from metrics import TradeMetrics
from datetime import datetime, timezone

class OrderExecutor:
    def __init__(self, config, position_store, metrics_collector=None):
        # ... existing code ...
        self.metrics = metrics_collector

    async def execute_order(self, request: OrderRequest) -> dict:
        """Execute a market order with metrics tracking"""

        start_time = time.time()
        order_key = self._get_order_key(request)

        # ... existing execution logic ...

        # Record metrics if collector provided
        if self.metrics:
            latency_ms = (time.time() - start_time) * 1000
            metric = TradeMetrics(
                timestamp=datetime.now(timezone.utc),
                market_id=request.token_id,
                attempted=True,
                filled=success,
                order_to_ack_ms=latency_ms,
                entry_price=request.price or 0.0,
                outcome_reason="filled" if success else "execution_failed",
            )
            await self.metrics.record_trade(metric)
```

---

## Configuration Examples

### Conservative Settings (Starting Out)

```python
MetricsConfig(
    aggregation_interval_seconds=30,      # Frequent aggregation
    dashboard_refresh_seconds=2,          # Fast updates
    history_hours=6,                      # Keep 6 hours
    fill_rate_warning_threshold=0.3,      # 30% threshold
    latency_warning_ms=100.0,             # 100ms threshold
)
```

### Production Settings (Scaling)

```python
MetricsConfig(
    aggregation_interval_seconds=60,      # Once per minute
    dashboard_refresh_seconds=5,          # Every 5 seconds
    history_hours=72,                     # Keep 3 days
    fill_rate_warning_threshold=0.5,      # 50% threshold
    latency_warning_ms=50.0,              # 50ms threshold
    target_p95_decision_latency_ms=30.0,  # 30ms decision
    target_p95_order_ack_latency_ms=150.0,  # 150ms ack
)
```

### High-Frequency Testing

```python
MetricsConfig(
    aggregation_interval_seconds=10,      # Every 10 seconds
    dashboard_refresh_seconds=1,          # Every second
    history_hours=2,                      # Keep 2 hours
    fill_rate_warning_threshold=0.4,
    latency_warning_ms=75.0,
)
```

---

## Dashboard Integration

### Standalone Usage

```python
from metrics import MetricsDashboard, DashboardView
import asyncio

async def monitor():
    config = MetricsConfig()
    collector = MetricsCollector(config)
    dashboard = MetricsDashboard(collector, config)

    await dashboard.start()

    # Let it run in background
    await asyncio.sleep(3600)  # 1 hour

    await dashboard.stop()
```

### Integrated with Bot

```python
class PolymarketBot:
    async def run(self):
        # Start dashboard
        await self.dashboard.start()

        try:
            # Main bot loop
            while self.running:
                # ... trading logic ...
                pass
        finally:
            # Stop dashboard
            await self.dashboard.stop()

            # Export final metrics
            await self.metrics.write_metrics_log()
```

### View Switching

During runtime, cycle through views:

```python
async def monitor_task(dashboard):
    views = [
        DashboardView.SUMMARY,
        DashboardView.LATENCY,
        DashboardView.MARKETS,
        DashboardView.ALERTS,
    ]

    current_view_idx = 0
    while True:
        dashboard.set_view(views[current_view_idx])
        await asyncio.sleep(30)  # Show each view for 30s
        current_view_idx = (current_view_idx + 1) % len(views)
```

---

## Alert Handling

### Automatic Alerts in Dashboard

Alerts appear automatically in the ALERTS view when thresholds are exceeded.

### Programmatic Alert Handling

```python
async def monitor_alerts(collector):
    """Monitor and respond to alerts"""

    while True:
        alerts = await collector.check_alerts()

        if alerts:
            logger.warning(f"Active alerts: {len(alerts)}")

            for alert in alerts:
                if "Fill rate" in alert:
                    # Possible network issues or market conditions
                    await pause_trading()

                elif "latency" in alert.lower():
                    # System under load
                    await reduce_trade_frequency()

        await asyncio.sleep(5)
```

---

## Data Export & RAG Integration

### Periodic Export

```python
async def export_loop(collector):
    """Export metrics every 30 minutes for RAG ingestion"""

    while True:
        await collector.write_metrics_log()
        # Creates: logs/metrics/metrics_2026-02-01.jsonl
        await asyncio.sleep(1800)  # 30 minutes

# Start in background
asyncio.create_task(export_loop(self.metrics))
```

### RAG Analysis

The JSONL export is compatible with existing RAG systems:

```bash
# Copy metrics to RAG directory
cp logs/metrics/metrics_*.jsonl rag/data/

# Will be ingested alongside trade logs for pattern analysis
```

---

## Monitoring Checklist

Run through these checks regularly:

### Daily
- [ ] Fill rate above warning threshold
- [ ] No active alerts in dashboard
- [ ] P&L trending positive
- [ ] Latency within targets

### Weekly
- [ ] Review aggregated metrics for patterns
- [ ] Check for systematic failures (kill switches, circuit breakers)
- [ ] Verify metrics export is running
- [ ] Analyze edge per fill trend

### Monthly
- [ ] Export and analyze full month of metrics
- [ ] Update latency targets if needed
- [ ] Tune alert thresholds based on data
- [ ] Archive old metrics logs

---

## Troubleshooting

### High Decision Latency

Check bot responsiveness:

```python
# Is the decision-making slow?
latency = await collector.get_latency_stats()
if latency['p95_decision_ms'] > 50:
    # Profile decision() function
    logger.warning("Decision function too slow")
```

### Low Fill Rate

Investigate execution:

```python
# Are orders being placed?
attempted, filled = await collector.get_current_trades()
fill_rate = filled / attempted if attempted > 0 else 0
if fill_rate < 0.5:
    # Check order executor logs
    # Verify order book liquidity
    # Review error reasons in metrics
```

### Memory Bloat

Prune old metrics:

```python
# Run periodically
removed = await collector.prune_old_metrics()
logger.info(f"Pruned {removed} old metrics")
```

---

## Related Documentation

- [Metrics Guide](./METRICS_GUIDE.md)
- [TradeLogger Guide](./TRADE_LOGGER.md)
- [Executor Documentation](../executor.py)
- [Sniper Bot Guide](../sniper.py)
