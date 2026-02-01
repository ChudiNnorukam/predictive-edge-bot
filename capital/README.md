# Capital Allocation System

Comprehensive capital management for the Polymarket latency arbitrage bot.

## Overview

The Capital Allocation System provides:
- **Multi-market exposure limits** - Per-market and total portfolio constraints
- **Automatic order splitting** - Reduce slippage on large allocations
- **Capital recycling** - Free up capital as markets resolve
- **Real-time reporting** - Portfolio state and utilization metrics
- **Thread-safe async design** - Safe concurrent allocations

## Components

### CapitalAllocator

Manages capital allocation with strict exposure controls.

**Key Methods:**
- `request_allocation(market_id, amount, strategy)` - Request capital for a market
- `release_allocation(market_id, pnl)` - Release capital after market completes
- `get_allocation_report()` - Get portfolio state snapshot
- `update_bankroll(amount)` - Update bankroll from deposits/withdrawals

**Configuration:**
```python
config = CapitalConfig(
    max_exposure_per_market_percent=5.0,    # 5% of bankroll per market
    max_exposure_per_market_absolute=50.0,  # Hard cap $50/market
    max_total_exposure_percent=30.0,        # 30% total exposure
    order_split_threshold=20.0,             # Split orders > $20
    order_split_count=3,                    # Into 3 orders
)
```

**Example Usage:**
```python
allocator = CapitalAllocator(config, initial_bankroll=200.0)

# Request allocation
result, amount = await allocator.request_allocation("market1", 15.0)
if result == AllocationResult.SUCCESS:
    print(f"Allocated ${amount:.2f}")

# Get portfolio state
report = allocator.get_allocation_report()
print(f"Utilization: {report['utilization_percent']:.1f}%")

# Release with P&L
released = await allocator.release_allocation("market1", pnl=0.50)
```

### CapitalRecycler

Manages capital recycling as markets resolve.

**Key Methods:**
- `start()` - Start background recycler task
- `stop()` - Stop recycler
- `queue_recycle(market_id, pnl)` - Queue market for recycling
- `force_recycle(market_id)` - Immediately recycle
- `get_recycle_history()` - Get completed recycles
- `get_daily_stats()` - Daily recycling statistics

**Example Usage:**
```python
async def on_capital_freed(amount):
    print(f"${amount:.2f} freed, available for new allocations")

recycler = CapitalRecycler(
    config,
    allocator,
    on_capital_freed=on_capital_freed
)

await recycler.start()

# Queue market for recycling after resolution
await recycler.queue_recycle("market1", pnl=0.75)

# Later, get statistics
stats = recycler.get_daily_stats()
print(f"Recycled: {stats['recycles_today']} times, "
      f"${stats['capital_recycled_today']:.2f}, "
      f"${stats['total_pnl_today']:+.2f} P&L")

await recycler.stop()
```

## Allocation Logic

### Request Flow

1. **Validate input** - Check amount > 0, market not already allocated
2. **Calculate max per market** - Apply percent and absolute limits
3. **Calculate total headroom** - Remaining under portfolio limit
4. **Calculate available capital** - Bankroll minus allocations
5. **Determine actual allocation** - Min of requested and max allowed
6. **Calculate order splits** - If amount > split_threshold
7. **Record allocation** - Store and return result

### Limit Enforcement

Three layers of constraints:

1. **Per-market percent**: `max = bankroll * (max_percent / 100)`
2. **Per-market absolute**: `max = max_absolute_cap`
3. **Per-market effective**: `max = min(percent_limit, absolute_limit)`
4. **Total exposure**: `max_total = bankroll * (max_total_percent / 100)`
5. **Available capital**: `max_available = bankroll - total_allocated`

Final allocation: `min(requested, max_per_market, headroom, available)`

### Order Splitting

Large orders are split to reduce execution risk and slippage:

```python
if amount > order_split_threshold:
    split_size = amount / order_split_count
    orders = [split_size] * order_split_count
    # Handle rounding on last order
else:
    orders = []  # Single order of full amount
```

Example: $30 allocation with threshold=$20, count=3
- Split into: [$10.00, $10.00, $10.00]

## Recycling Process

### Timeline

1. **Market resolves** - Outcome determined
2. **Queue recycle** - Call `queue_recycle(market_id, pnl)`
3. **Wait for delay** - Default 5 seconds (configurable)
4. **Release allocation** - Capital freed, P&L applied to bankroll
5. **Callback notification** - If `on_capital_freed` configured
6. **History recorded** - For stats and audit

### Manual Control

Force immediate recycling without delay:
```python
released = await recycler.force_recycle("market1")
```

## State Tracking

### Allocations

Each allocation tracks:
- Market ID
- Amount allocated
- Strategy name
- Timestamp
- Order split sizes (if split)

### Recycle Events

Each recycle event records:
- Market ID
- Amount recycled
- P&L
- Resolution timestamp
- Recycle timestamp (None if pending)

## Thread Safety

All operations are async-safe using `asyncio.Lock()`:
- Concurrent allocation requests are serialized
- No race conditions on bankroll updates
- History is append-only (no conflicting writes)

## Reporting

### Allocation Report

```python
report = allocator.get_allocation_report()
# Returns:
# {
#     "bankroll": 200.0,
#     "total_allocated": 60.0,
#     "available": 140.0,
#     "utilization_percent": 30.0,
#     "max_total_allowed": 60.0,
#     "headroom": 0.0,
#     "num_allocated_markets": 6,
#     "allocations": [...]
# }
```

### Daily Stats

```python
stats = recycler.get_daily_stats()
# Returns:
# {
#     "recycles_today": 15,
#     "capital_recycled_today": 150.0,
#     "total_pnl_today": 4.50,
#     "avg_recycle_time_seconds": 5.2,
#     "pending_recycles": 2
# }
```

## Error Handling

Allocation failures return specific result codes:

| Result | Meaning |
|--------|---------|
| `SUCCESS` | Allocation succeeded |
| `INSUFFICIENT_CAPITAL` | Not enough total capital |
| `MARKET_LIMIT_EXCEEDED` | Market-specific limit hit |
| `TOTAL_LIMIT_EXCEEDED` | Portfolio limit exceeded |
| `ALREADY_ALLOCATED` | Market already has allocation |
| `INVALID_AMOUNT` | Amount <= 0 |

Always check the result:
```python
result, amount = await allocator.request_allocation("market1", 15.0)
if result != AllocationResult.SUCCESS:
    logger.warning(f"Allocation failed: {result.value}")
```

## Integration with Bot

### Typical Workflow

```python
# 1. Initialize at startup
config = CapitalConfig()
allocator = CapitalAllocator(config, initial_bankroll=balance)
recycler = CapitalRecycler(config, allocator)
await recycler.start()

# 2. When sniper detects opportunity
result, amount = await allocator.request_allocation(
    market_id=market["id"],
    requested_amount=edge_size,
    strategy="sniper"
)
if result == AllocationResult.SUCCESS:
    # Place orders with allocated amount
    execute_trade(market, amount)

# 3. When market resolves
await recycler.queue_recycle(market_id, pnl=profit_loss)

# 4. At shutdown
await recycler.stop()
```

## Testing

Run full test suite:
```bash
pytest capital/test_capital_system.py -v
```

Run specific test class:
```bash
pytest capital/test_capital_system.py::TestCapitalAllocator -v
```

All 34 tests pass, covering:
- Configuration validation
- Allocation logic and limits
- Order splitting
- Concurrent requests
- Recycling workflow
- Integration scenarios

## Performance Characteristics

- **Allocation time**: < 1ms per request (O(1) operations)
- **Concurrent requests**: Serialized by lock (thread-safe)
- **Memory**: O(n) where n = number of allocated markets
- **Recycling**: Background task, doesn't block allocations
- **Reporting**: O(n) snapshot generation, safe to call frequently

## Future Enhancements

- Multi-tier allocation (critical/high/normal priority)
- Dynamic limit adjustment based on volatility
- Market-specific risk profiles
- P&L tracking and analytics dashboard
- Risk aggregation (correlated markets)
