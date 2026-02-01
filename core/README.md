# Market State Machine

Core module implementing the market lifecycle and execution scheduling for the Polymarket latency-arbitrage bot.

## Overview

The Market State Machine manages the complete lifecycle of markets from discovery through execution to resolution. It handles:

- **State Transitions**: Automatic transitions based on time, price, and feed quality
- **Priority Queuing**: Markets prioritized by time to expiry
- **Failure Handling**: Automatic hold on stale feeds or repeated failures
- **Execution Tracking**: Capital allocation, order placement, and P&L calculation

## Architecture

### Market States

```
DISCOVERED
    ↓ (price update)
WATCHING
    ↓ (time < threshold & price < max)
ELIGIBLE
    ↓ (orders placed)
EXECUTING
    ↓ (market resolves)
RECONCILING
    ↓ (P&L calculated)
DONE
    ↓ (cleanup after max age)
    (removed)

ANY STATE
    ↓ (stale feed or failures)
ON_HOLD
    ↓ (feed resumes)
    (back to WATCHING)
```

## Components

### 1. MarketState Enum

All possible states a market can be in:

```python
from core.market_state import MarketState

assert MarketState.DISCOVERED.value == "discovered"
assert MarketState.WATCHING.value == "watching"
assert MarketState.ELIGIBLE.value == "eligible"
# etc.
```

### 2. Market Dataclass

Represents a single market with all tracking data:

```python
from core.market_state import Market
from datetime import datetime, timedelta

market = Market(
    token_id="0xabc123...",
    condition_id="0xcond456...",
    question="Will Bitcoin reach $100k by Dec 31?",
    end_time=datetime.utcnow() + timedelta(minutes=5)
)

# Track current prices
market.current_bid = 0.45
market.current_ask = 0.47
market.last_update = datetime.utcnow()

# Check staleness
if market.is_stale(threshold_ms=500):
    print("Feed is stale")

# Calculate time to expiry
time_left = market.time_to_expiry()
print(f"Expiring in {time_left.total_seconds():.1f}s")
```

### 3. MarketStateMachine

Thread-safe state manager for all markets:

```python
from core.market_state import SchedulerConfig, MarketStateMachine
import asyncio

async def main():
    config = SchedulerConfig(
        time_to_eligibility_sec=60,
        max_buy_price=0.95,
        stale_feed_threshold_ms=500,
        max_failures_before_hold=3
    )
    machine = MarketStateMachine(config)

    # Add market
    await machine.add_market(market)

    # Update prices from WebSocket
    await machine.update_price("token_id", bid=0.45, ask=0.47)

    # Check for automatic transitions
    transitions = await machine.check_transitions()

    # Get markets by state
    eligible = await machine.get_markets_by_state(MarketState.ELIGIBLE)

    # Mark execution started
    await machine.mark_execution_started("token_id", capital_allocated=100.0)

    # Record P&L
    await machine.mark_resolution("token_id", pnl=25.0)
    await machine.mark_done("token_id")

asyncio.run(main())
```

### 4. MarketPriorityQueue

Time-priority queue for efficient market scheduling:

```python
from core.priority_queue import MarketPriorityQueue

queue = MarketPriorityQueue()

# Add markets (automatically sorted by time to expiry)
queue.push(market1)  # Expires in 10s
queue.push(market2)  # Expires in 30s

# Get next market to process (shortest expiry first)
next_token_id = queue.pop()  # Returns market1

# Peek without removing
peek_id = queue.peek()

# Update priority when time changes
queue.update_priority(market2)

# Remove market
queue.remove(market2.token_id)

# Check state
print(f"Queue size: {len(queue)}")
print(f"Empty: {queue.is_empty()}")
```

## Transition Rules

### Automatic Transitions

The `check_transitions()` method applies these rules:

**ANY → ON_HOLD**
- Feed is stale (no update in `stale_feed_threshold_ms`)
- `failure_count > max_failures_before_hold`

**ON_HOLD → WATCHING**
- Feed resumes fresh update
- Failure count back within threshold

**DISCOVERED → WATCHING**
- First price update received

**WATCHING → ELIGIBLE**
- `time_to_expiry() < time_to_eligibility_sec`
- `current_ask < max_buy_price`

**ELIGIBLE → EXECUTING**
- At least one order placed (`mark_execution_started()`)

**EXECUTING → RECONCILING**
- Market expires (`time_to_expiry() <= 0`)

**RECONCILING → DONE**
- P&L calculated and recorded

### Manual Transitions

These methods handle explicit state changes:

```python
# Mark execution started
success = await machine.mark_execution_started(
    token_id="token_123",
    capital_allocated=100.0
)

# Record resolution and P&L
success = await machine.mark_resolution(
    token_id="token_123",
    pnl=25.0
)

# Mark as complete (removes from active tracking)
success = await machine.mark_done(token_id="token_123")

# Record failure (increments counter, auto-holds if threshold exceeded)
success = await machine.mark_failure(
    token_id="token_123",
    reason="Order execution timeout"
)
```

## Configuration

### SchedulerConfig

Controls all transition thresholds:

```python
from core.market_state import SchedulerConfig

config = SchedulerConfig(
    # Time-based
    time_to_eligibility_sec=60,      # Enter ELIGIBLE when < 60s to expiry
    stale_feed_threshold_ms=500,     # Feed is stale if no update in 500ms
    max_failures_before_hold=3,      # ON_HOLD after 3+ failures

    # Price-based
    max_buy_price=0.99,              # Don't trade above this price
    min_edge_pct=0.01,               # Minimum edge (1%)

    # Cleanup
    max_hold_hours=24                # Drop DONE markets after 24 hours
)
```

## Usage Examples

### Example 1: Complete Market Lifecycle

```python
import asyncio
from core.market_state import Market, MarketState, SchedulerConfig, MarketStateMachine
from datetime import datetime, timedelta

async def trade_market():
    config = SchedulerConfig(time_to_eligibility_sec=10)
    machine = MarketStateMachine(config)

    # Create market expiring in 30 seconds
    market = Market(
        token_id="0xabc...",
        condition_id="0xcond...",
        question="Will BTC > $60k?",
        end_time=datetime.utcnow() + timedelta(seconds=30)
    )

    # Start tracking
    await machine.add_market(market)

    # Receive WebSocket price update
    await machine.update_price("0xabc...", bid=0.45, ask=0.47)

    # Check for state transitions
    transitions = await machine.check_transitions()
    # Now in WATCHING state

    # After another check (when time < 10s)
    await machine.check_transitions()
    # Now in ELIGIBLE state

    # Execute
    await machine.mark_execution_started("0xabc...", capital_allocated=50.0)
    # Now in EXECUTING state

    # Wait for resolution...
    await machine.mark_resolution("0xabc...", pnl=10.0)
    # Now in RECONCILING state

    # Complete
    await machine.mark_done("0xabc...")
    # Now in DONE state

asyncio.run(trade_market())
```

### Example 2: Monitoring Multiple Markets

```python
async def monitor_markets(machine: MarketStateMachine):
    while True:
        # Check for automatic state transitions
        transitions = await machine.check_transitions()

        if transitions:
            print(f"Transitions: {len(transitions)}")
            for token_id, old_state, new_state in transitions:
                print(f"  {token_id}: {old_state.value} -> {new_state.value}")

        # Get eligible markets ready for execution
        eligible = await machine.get_markets_by_state(MarketState.ELIGIBLE)
        print(f"Eligible markets: {len(eligible)}")

        # Get metrics
        stats = await machine.get_stats()
        print(f"Market distribution: {stats}")

        await asyncio.sleep(0.1)
```

### Example 3: Priority-Based Execution

```python
from core.priority_queue import MarketPriorityQueue

async def execute_by_priority(machine, queue):
    # Add all eligible markets to priority queue
    eligible = await machine.get_markets_by_state(MarketState.ELIGIBLE)
    for market in eligible:
        queue.push(market)

    # Process in priority order (shortest expiry first)
    while not queue.is_empty():
        token_id = queue.pop()
        await machine.mark_execution_started(token_id, capital_allocated=100.0)
        print(f"Executed: {token_id}")
```

## State Machine Diagram

```
         ┌─────────────────────────┐
         │      DISCOVERED         │
         │  (initial state)        │
         └───────────┬─────────────┘
                     │
         (price update, feed online)
                     │
                     ▼
         ┌─────────────────────────┐      (stale or
         │       WATCHING          │───► ON_HOLD ───┐
         │ (receiving prices)      │                │
         └───────────┬─────────────┘    (recovered) │
                     │                              │
       (time < 60s & price < 0.99)                 │
                     │                              │
                     ▼                              │
         ┌─────────────────────────┐      ◄────────┘
         │       ELIGIBLE          │
         │ (ready to execute)      │
         └───────────┬─────────────┘
                     │
           (orders placed)
                     │
                     ▼
         ┌─────────────────────────┐
         │      EXECUTING          │
         │ (position open)         │
         └───────────┬─────────────┘
                     │
           (market expires)
                     │
                     ▼
         ┌─────────────────────────┐
         │     RECONCILING         │
         │ (computing P&L)         │
         └───────────┬─────────────┘
                     │
        (P&L calculated)
                     │
                     ▼
         ┌─────────────────────────┐
         │         DONE            │
         │ (can be cleaned up)     │
         └─────────────────────────┘
```

## Thread Safety

- All methods are async and thread-safe via `asyncio.Lock`
- Safe to call from multiple coroutines concurrently
- No blocking I/O or CPU-intensive operations
- Suitable for integration with WebSocket listeners and order executors

## Testing

Run the test suite:

```bash
# Install pytest and pytest-asyncio
pip install pytest pytest-asyncio

# Run tests
pytest tests/test_market_state_machine.py -v
```

Test coverage includes:
- Market creation and state tracking
- All state transitions
- Priority queue operations
- Stale feed detection
- Failure handling and recovery
- Concurrent operations

## Integration with Bot

The state machine integrates with other bot components:

1. **WebSocket Listener** → calls `machine.update_price()`
2. **Scheduler** → calls `machine.check_transitions()`
3. **Executor** → calls `machine.mark_execution_started()`
4. **Position Manager** → calls `machine.mark_resolution()`, `machine.mark_done()`
5. **Priority Queue** → feeds eligible markets to executor

See the main orchestrator for full integration pattern.
