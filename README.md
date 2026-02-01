# PredictiveEdge

**Automated prediction market trading on Polymarket**

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: Private](https://img.shields.io/badge/license-private-red.svg)]()

---

## What It Does

PredictiveEdge captures small, consistent profits from 15-minute crypto prediction markets by executing trades in the final seconds when outcomes are virtually certain.

**The Edge:** When BTC has clearly moved up with 30 seconds left, "YES" shares still trade at $0.97-0.99 instead of $1.00. We buy that gap.

---

## Quick Start

```bash
# Clone
git clone https://github.com/ChudiNnorukam/predictive-edge-bot.git
cd predictive-edge-bot

# Setup
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env with your Polymarket credentials

# Approve USDC (one-time)
python approve.py

# Run enhanced sniper (multi-market mode)
python sniper_v2.py --multi --max-markets 5
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         PredictiveEdge v2                               │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐                │
│  │   Scanner   │───▶│ State       │───▶│  Sniper     │                │
│  │   (Gamma)   │    │ Machine     │    │  Executor   │                │
│  └─────────────┘    └─────────────┘    └─────────────┘                │
│         │                 │                   │                        │
│         ▼                 ▼                   ▼                        │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐                │
│  │  WebSocket  │    │    Risk     │    │   Capital   │                │
│  │  Feed Mgr   │    │   Manager   │    │  Allocator  │                │
│  └─────────────┘    └─────────────┘    └─────────────┘                │
│         │                 │                   │                        │
│         └────────────────┼───────────────────┘                        │
│                          ▼                                             │
│                   ┌─────────────┐                                      │
│                   │   Metrics   │                                      │
│                   │  Collector  │                                      │
│                   └─────────────┘                                      │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

### Market State Machine

```
DISCOVERED → WATCHING → ELIGIBLE → EXECUTING → RECONCILING → DONE
                ↓           ↓           ↓
            STALE_FEED   ON_HOLD    FAILED
```

---

## Project Structure

```
predictive-edge-bot/
├── sniper_v2.py            # Enhanced multi-market sniper bot
├── sniper.py               # Legacy single-market bot
├── scanner.py              # Legacy market scanner
│
├── core/                   # Core state management
│   ├── market_state.py     # MarketStateMachine + Market model
│   └── priority_queue.py   # Priority-based market scheduling
│
├── risk/                   # Risk management engine
│   ├── kill_switches.py    # Emergency halt system
│   ├── circuit_breakers.py # Per-market failure isolation
│   └── exposure_manager.py # Position limits & exposure tracking
│
├── capital/                # Capital allocation
│   ├── allocator.py        # Per-market & total exposure limits
│   └── recycler.py         # Capital recycling after trades
│
├── metrics/                # Observability
│   ├── collector.py        # Trade metrics collection
│   └── dashboard.py        # Real-time stats
│
├── scheduler/              # Multi-market scheduling
│   ├── scheduler.py        # Execution window management
│   └── execution_window.py # Time-based trade triggering
│
├── config.py               # Configuration loader
├── config_v2.py            # Unified config with presets
├── orchestrator_v2.py      # Full system orchestrator
├── feed_manager.py         # WebSocket price feed manager
│
├── utils/
│   ├── trade_logger.py     # RAG-ready JSON logging
│   └── notifications.py    # Alert system
│
├── tests/
│   ├── test_sniper_v2.py           # Sniper integration tests
│   ├── test_full_integration.py    # End-to-end tests
│   ├── test_market_state_machine.py
│   └── test_risk_controls.py
│
├── docs/
│   ├── PARTNER_BRIEF.md
│   └── AUTO_SNIPER_SETUP.md
│
├── logs/
│   └── trades/             # JSON trade logs (gitignored)
│
├── requirements.txt
├── .env.example
└── README.md
```

---

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `PRIVATE_KEY` | Wallet private key | Required |
| `WALLET_ADDRESS` | Wallet address (0x...) | Required |
| `CLOB_API_KEY` | Polymarket API key | "" |
| `CLOB_SECRET` | Polymarket API secret | "" |
| `CLOB_PASSPHRASE` | Polymarket API passphrase | "" |
| `DRY_RUN` | True=simulate, False=live | True |
| `MAX_BUY_PRICE` | Max price to pay | 0.99 |
| `STARTING_BANKROLL` | Initial capital | 1000.0 |

### Config Presets (config_v2.py)

```python
# Conservative: Lower risk, smaller positions
CONSERVATIVE_PROFILE

# Aggressive: Higher exposure, more markets
AGGRESSIVE_PROFILE

# Paper Trading: Safe testing mode
PAPER_TRADING_PROFILE
```

---

## Risk Management

### Kill Switches
- **Stale Feed**: Halts if price feed > 500ms old
- **RPC Lag**: Halts if blockchain RPC unresponsive
- **Manual**: Emergency stop via API/signal

### Circuit Breakers
- Per-market failure isolation
- Auto-reset after cooldown
- Prevents cascade failures

### Exposure Limits
- Max 5% per market
- Max 30% total exposure
- Per-market absolute caps ($50 default)

---

## Usage

### Multi-Market Mode (Recommended)
```bash
python sniper_v2.py --multi --max-markets 5
```

### Single Token Mode
```bash
python sniper_v2.py --token-id <TOKEN_ID>
```

### CLI Options
```
--multi, -m           Enable multi-market mode
--token-id, -t        Single token to monitor
--max-markets         Max concurrent markets (default: 5)
--max-buy-price       Max price threshold (default: 0.99)
```

---

## Deployment

### Production (VPS + PM2)

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Configure
cp .env.example .env
nano .env  # Add credentials

# Create logs directory
mkdir -p logs

# Start with PM2
pm2 start "source venv/bin/activate && python sniper_v2.py --multi --max-markets 5" \
  --name sniper --cwd /root/polymarket-bot

# Auto-start on reboot
pm2 save
pm2 startup
```

### PM2 Commands
```bash
pm2 status          # Check status
pm2 logs sniper     # View logs
pm2 restart sniper  # Restart
pm2 stop sniper     # Stop
pm2 monit           # Real-time dashboard
```

---

## Testing

```bash
# Run all tests
pytest tests/ -v

# Run specific test suite
pytest tests/test_sniper_v2.py -v
pytest tests/test_full_integration.py -v

# Test coverage
pytest tests/ --cov=. --cov-report=html
```

**Test Coverage:**
- 26 tests passing
- State machine lifecycle
- Risk controls (kill switches, circuit breakers)
- Capital allocation limits
- Metrics collection
- Multi-market execution

---

## Roadmap

- [x] Phase 1: Core sniper strategy
- [x] Phase 2: Production deployment (VPS + PM2)
- [x] Phase 3: Enhanced logging for RAG
- [x] Phase 4: **Multi-market scaling system**
  - [x] Market State Machine
  - [x] Risk Controls Engine
  - [x] Capital Allocation System
  - [x] Metrics & Observability
- [ ] Phase 5: RAG-powered strategy optimization
- [ ] Phase 6: Copy trader integration
- [ ] Phase 7: Real-time dashboard

---

## Risk Disclosure

This software is for educational and research purposes. Trading prediction markets involves risk of loss. Past performance does not guarantee future results. Only trade with funds you can afford to lose.

---

## Team

Built by **PredictiveEdge** | February 2026

---

## License

Private repository. All rights reserved.
