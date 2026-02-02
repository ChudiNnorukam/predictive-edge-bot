# Metrics & Observability Guide

## Overview

The metrics module provides comprehensive performance tracking for the Polymarket latency-arbitrage bot. It collects detailed metrics on every trade, aggregates them into time periods, and provides real-time dashboards for monitoring system health.

**Key metrics tracked (PRD Section 7):**
1. **Fill Rate** - `filled_trades / attempted_trades` (target: >50% at scale)
2. **Average Edge per Fill** - Expected profit per filled trade in cents
3. **Latency Percentiles** - p50, p95, p99 for tick→decision and order→ack
4. **P&L** - Session total and per-trade
5. **System Health** - Missed trades, kill switches, circuit breaker trips

---

## Architecture

### Three Core Components

```
┌─────────────────────────────────────────────────────────┐
│ MetricsCollector                                        │
│ - Records individual trades (TradeMetrics)              │
│ - Aggregates into time periods (AggregatedMetrics)      │
│ - Provides query APIs for current stats                 │
│ - Exports to JSONL for RAG ingestion                    │
└──────────────────────────────────────────────────────────┘
              ↓
┌──────────────────────────────────────────────────────────┐
│ MetricsDashboard                                        │
│ - Terminal-based live monitoring                        │
│ - Multiple views: Summary, Latency, Markets, Alerts     │
│ - Real-time updates (configurable refresh)              │
│ - No external dependencies (pure ASCII)                 │
└──────────────────────────────────────────────────────────┘
              ↓
┌──────────────────────────────────────────────────────────┐
│ Alert System                                            │
│ - Check metrics against thresholds                      │
│ - Return list of alert messages                         │
│ - Integrated into dashboard views                       │
└──────────────────────────────────────────────────────────┘
```

---

## Usage

### 1. Initialize Metrics Collector

```python
from metrics import MetricsCollector, MetricsConfig

# Use defaults or customize
config = MetricsConfig(
    aggregation_interval_seconds=60,
    dashboard_refresh_seconds=5,
    history_hours=24,
    fill_rate_warning_threshold=0.5,  # Warn if < 50%
    latency_warning_ms=50.0,           # Warn if p95 > 50ms
)

collector = MetricsCollector(config)
```

### 2. Record Trades

```python
from datetime import datetime, timezone
from metrics import TradeMetrics

# Create metric record
trade_metric = TradeMetrics(
    timestamp=datetime.now(timezone.utc),
    market_id="token123",
    attempted=True,
    filled=True,
    fill_amount=10.0,  # USD

    # Timing in milliseconds
    tick_to_decision_ms=15.5,    # Price update → decision
    decision_to_order_ms=8.2,     # Decision → order sent
    order_to_ack_ms=95.3,         # Order sent → ack
    total_latency_ms=119.0,

    # P&L
    entry_price=0.85,
    expected_payout=1.0,
    edge_cents=15.0,              # (1.0 - 0.85) * 100
    actual_pnl=1.50,              # Realized profit
    outcome_reason="filled",
)

# Record it
await collector.record_trade(trade_metric)
```

### 3. Query Real-Time Stats

```python
# Fill rate
fill_rate = await collector.get_current_fill_rate()  # float 0-1

# P&L
pnl = await collector.get_current_pnl()  # float

# Latency stats
latency_stats = await collector.get_latency_stats()
# {
#     "p50_decision_ms": 12.5,
#     "p95_decision_ms": 28.0,
#     "p99_decision_ms": 45.0,
#     "max_decision_ms": 120.0,
#     "p95_order_ack_ms": 120.0,
#     "samples": 150,
# }

# Session stats
stats = await collector.get_session_stats()
# {
#     "session_start": "2026-02-01T12:00:00+00:00",
#     "elapsed_seconds": 3600,
#     "trades_attempted": 50,
#     "trades_filled": 35,
#     "fill_rate": 0.70,
#     "total_pnl": 5.25,
#     "missed_trades": 3,
#     "kill_switches": 0,
#     "circuit_breakers": 0,
# }
```

### 4. Use Dashboard

```python
from metrics import MetricsDashboard, DashboardView

dashboard = MetricsDashboard(collector, config)

# Start automatic refresh
await dashboard.start()

# Switch views
dashboard.set_view(DashboardView.SUMMARY)
dashboard.set_view(DashboardView.LATENCY)
dashboard.set_view(DashboardView.MARKETS)
dashboard.set_view(DashboardView.ALERTS)

# Stop when done
await dashboard.stop()
```

### 5. Missed Trades & System Events

```python
# Record missed trade
await collector.record_missed_trade(
    market_id="token456",
    reason="no_liquidity"
)

# Record kill switch
await collector.record_kill_switch_trigger()

# Record circuit breaker
await collector.record_circuit_breaker_trip()
```

### 6. Export for RAG

```python
# Get JSONL formatted metrics
jsonl_data = collector.to_jsonl()

# Write to file
await collector.write_metrics_log()
# Creates: logs/metrics/metrics_2026-02-01.jsonl
```

---

## Data Models

### TradeMetrics

Individual trade metric record:

```python
@dataclass
class TradeMetrics:
    timestamp: datetime
    market_id: str

    # Outcome
    attempted: bool = True
    filled: bool = False
    fill_amount: float = 0.0

    # Timing (milliseconds)
    tick_to_decision_ms: float = 0.0
    decision_to_order_ms: float = 0.0
    order_to_ack_ms: float = 0.0
    total_latency_ms: float = 0.0

    # P&L
    entry_price: float = 0.0
    expected_payout: float = 1.0
    edge_cents: float = 0.0
    actual_pnl: float = 0.0

    outcome_reason: str = ""
```

### AggregatedMetrics

Time-period aggregation:

```python
@dataclass
class AggregatedMetrics:
    period_start: datetime
    period_end: datetime

    # Fill rate
    trades_attempted: int = 0
    trades_filled: int = 0
    fill_rate: float = 0.0

    # P&L
    total_pnl: float = 0.0
    avg_edge_per_fill_cents: float = 0.0
    win_rate: float = 0.0

    # Latency percentiles (milliseconds)
    p50_decision_latency_ms: float = 0.0
    p95_decision_latency_ms: float = 0.0
    p99_decision_latency_ms: float = 0.0
    max_decision_latency_ms: float = 0.0

    p50_order_ack_ms: float = 0.0
    p95_order_ack_ms: float = 0.0
    p99_order_ack_ms: float = 0.0
    max_order_ack_ms: float = 0.0

    # Volume
    markets_traded: int = 0
    total_volume_usd: float = 0.0

    # Health
    missed_trades: int = 0
    kill_switch_triggers: int = 0
    circuit_breaker_trips: int = 0

    # Samples
    decision_latency_samples: int = 0
    order_ack_samples: int = 0
```

---

## Dashboard Views

### 1. Summary View (Default)

```
╔══════════════════════════════════════════════════════╗
║     POLYMARKET SNIPER - LIVE DASHBOARD            ║
╠══════════════════════════════════════════════════════╣
║ Session: 2h 15m          Trades: 45               ║
║ Fill Rate: 100%  P&L: $+4.25                  ║
╠══════════════════════════════════════════════════════╣
║ KEY METRICS                                      ║
║                                                  ║
║ Fill Rate:  ████████████████████░░░░░░░░░░  100%   ║
║ Session P&L: +$4.25                           ║
║ Avg Edge:    +1.41¢ per trade                 ║
║                                                  ║
╠══════════════════════════════════════════════════════╣
║ LATENCY (milliseconds)                          ║
║                                                  ║
║ Tick→Decision:    25ms ✓  Order→Ack:   110ms ✓    ║
║ Targets:          30ms               150ms        ║
║                                                  ║
╠══════════════════════════════════════════════════════╣
║ HEALTH                                          ║
║                                                  ║
║ Missed Trades:   0     Kill Switches: 0        ║
║ Circuit Breaks:  0                             ║
║                                                  ║
╠══════════════════════════════════════════════════════╣
║ ALERTS                                          ║
║ None                                               ║
╚══════════════════════════════════════════════════════╝
```

### 2. Latency View

Detailed breakdown of latency percentiles with sample counts.

### 3. Markets View

Per-market performance summary.

### 4. Alerts View

Active alerts with health metrics.

---

## Alert System

Alerts are triggered when metrics exceed thresholds:

### Default Thresholds

| Metric | Threshold | Description |
|--------|-----------|-------------|
| Fill Rate | < 50% | Too many orders failing to fill |
| p95 Decision Latency | > 50ms | Tick→Decision taking too long |
| p95 Order Ack Latency | > 150ms | Order acknowledgment too slow |

### Customizing Alerts

```python
config = MetricsConfig(
    fill_rate_warning_threshold=0.4,  # Warn at 40%
    latency_warning_ms=100.0,         # Warn at 100ms
    target_p95_decision_latency_ms=40.0,
    target_p95_order_ack_latency_ms=200.0,
)

alerts = await collector.check_alerts()
```

---

## Performance Targets (PRD Section 7)

| Metric | Target | Rationale |
|--------|--------|-----------|
| Fill Rate | > 50% at scale | Even with competition, should fill majority of orders |
| p95 Decision Latency | ≤ 30ms | Must respond fast to market signals |
| p95 Order Ack Latency | ≤ 150ms | Standard order book acknowledgment |
| Avg Edge per Fill | 1-3 cents | Typical sniping edge before slippage |

---

## Integration with Existing Systems

### With TradeLogger (utils/trade_logger.py)

The metrics system is **complementary** to the existing trade logger:

```
Trade occurs
    ↓
TradeLogger records: OPPORTUNITY, EXECUTION, SETTLEMENT events (RAG ingestion)
    ↓
MetricsCollector records: TradeMetrics with latency and P&L data
    ↓
Dashboard displays: Real-time summary and detailed metrics
```

Both log to JSONL format for RAG analysis.

### With OrderExecutor (executor.py)

Integrate metrics recording into execution pipeline:

```python
from metrics import TradeMetrics, MetricsCollector

collector = MetricsCollector(config)

async def execute_and_record(order_request, latency_ms):
    start = time.time()
    result = await executor.execute_order(order_request)
    elapsed = (time.time() - start) * 1000

    metric = TradeMetrics(
        timestamp=datetime.now(timezone.utc),
        market_id=order_request.token_id,
        attempted=True,
        filled=result.success,
        tick_to_decision_ms=latency_ms,
        order_to_ack_ms=elapsed,
        entry_price=order_request.price,
        actual_pnl=result.pnl if result.success else 0,
    )
    await collector.record_trade(metric)
```

---

## Maintenance

### Pruning Old Metrics

```python
# Automatically remove metrics older than history_hours
removed = await collector.prune_old_metrics()
print(f"Removed {removed} old metrics")
```

Run periodically in background:

```python
async def prune_loop(collector):
    while True:
        await collector.prune_old_metrics()
        await asyncio.sleep(3600)  # Every hour

asyncio.create_task(prune_loop(collector))
```

### Logging

The metrics module uses standard Python logging:

```python
import logging

# Enable debug output
logging.getLogger("metrics.collector").setLevel(logging.DEBUG)
logging.getLogger("metrics.dashboard").setLevel(logging.DEBUG)
```

---

## Example: Complete Session

```python
import asyncio
from datetime import datetime, timezone
from metrics import (
    MetricsCollector, MetricsConfig,
    MetricsDashboard, DashboardView,
    TradeMetrics,
)

async def run_bot_with_metrics():
    # Setup
    config = MetricsConfig()
    collector = MetricsCollector(config)
    dashboard = MetricsDashboard(collector, config)

    await dashboard.start()

    try:
        # Main bot loop
        for market_id in markets:
            tick = await get_price(market_id)

            # Timing measurements
            decision_start = time.time()
            decision = decide_trade(tick)
            decision_latency = (time.time() - decision_start) * 1000

            if decision:
                order_start = time.time()
                result = await execute_order(market_id)
                order_latency = (time.time() - order_start) * 1000

                # Record metrics
                metric = TradeMetrics(
                    timestamp=datetime.now(timezone.utc),
                    market_id=market_id,
                    attempted=True,
                    filled=result.success,
                    fill_amount=result.fill_amount,
                    tick_to_decision_ms=decision_latency,
                    order_to_ack_ms=order_latency,
                    entry_price=tick.ask,
                    actual_pnl=result.pnl,
                    outcome_reason="filled" if result.success else result.reason,
                )
                await collector.record_trade(metric)

                # Check for alerts
                alerts = await collector.check_alerts()
                if alerts:
                    dashboard.set_view(DashboardView.ALERTS)

    finally:
        await dashboard.stop()
        await collector.write_metrics_log()
```

---

## Troubleshooting

### Latency Metrics Are All Zero

**Cause:** Not populating latency fields when recording metrics.

**Fix:** Measure time between decision points:

```python
import time

tick_start = time.time()
decision = make_decision()
decision_latency = (time.time() - tick_start) * 1000  # in ms

metric.tick_to_decision_ms = decision_latency
```

### Fill Rate Appears Incorrect

**Cause:** Not setting `filled=True` for completed trades.

**Fix:** Verify trade outcome before recording:

```python
metric.filled = order_result.status == "filled"
metric.outcome_reason = "filled" if metric.filled else "rejected"
```

### Dashboard Not Updating

**Cause:** Dashboard refresh task not running.

**Fix:** Ensure `await dashboard.start()` was called and bot is still running.

### Missing Metrics in JSONL Export

**Cause:** Old metrics pruned before export.

**Fix:** Increase `history_hours` or export more frequently:

```python
config = MetricsConfig(history_hours=72)  # 3 days
await collector.write_metrics_log()  # Every hour
```

---

## Related Documentation

- [PRD Section 7: Metrics & Observability](../docs/)
- [TradeLogger Guide](./TRADE_LOGGER.md)
- [System Architecture](./ARCHITECTURE.md)
