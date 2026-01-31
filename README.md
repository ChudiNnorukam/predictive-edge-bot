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

# Scan for markets
python scanner.py --direct

# Run sniper (dry run)
python sniper.py --token-id <TOKEN_ID>
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    PredictiveEdge                        │
├─────────────────────────────────────────────────────────┤
│                                                          │
│  scanner.py          sniper.py           trade_logger   │
│  ┌──────────┐       ┌──────────┐        ┌──────────┐   │
│  │ Find     │──────▶│ Monitor  │───────▶│ Log JSON │   │
│  │ Markets  │       │ & Execute│        │ for RAG  │   │
│  └──────────┘       └──────────┘        └──────────┘   │
│       │                  │                    │         │
│       ▼                  ▼                    ▼         │
│  Gamma API          CLOB WebSocket      logs/trades/   │
│                                                          │
└─────────────────────────────────────────────────────────┘
```

---

## Project Structure

```
predictive-edge-bot/
├── scanner.py              # Market discovery
├── sniper.py               # Core trading bot
├── config.py               # Configuration loader
├── approve.py              # USDC approval for trading
├── utils/
│   ├── trade_logger.py     # RAG-ready JSON logging
│   └── notifications.py    # Alert system
├── strategies/             # Strategy implementations
├── storage/                # Database layer
├── docs/
│   ├── PARTNER_BRIEF.md    # Non-technical overview
│   ├── AUTO_SNIPER_SETUP.md# Deployment guide
│   └── TEXT_TO_PARTNERS.md # Communication templates
├── logs/
│   └── trades/             # JSON trade logs (gitignored)
├── requirements.txt
├── .env.example
└── README.md
```

---

## Configuration

| Variable | Description | Required |
|----------|-------------|----------|
| `PRIVATE_KEY` | Wallet private key | Yes |
| `WALLET_ADDRESS` | Wallet address (0x...) | Yes |
| `CLOB_API_KEY` | Polymarket API key | Yes |
| `CLOB_SECRET` | Polymarket API secret | Yes |
| `CLOB_PASSPHRASE` | Polymarket API passphrase | Yes |
| `SIGNATURE_TYPE` | 0=EOA, 1=Email, 2=Browser | Yes |
| `DRY_RUN` | True=simulate, False=live | Yes |
| `MAX_BUY_PRICE` | Max price to pay (default: 0.99) | No |

---

## Strategies

### Sniper (Active)
- Monitors 15-minute crypto markets (BTC, ETH, SOL)
- Executes at T-minus 1 second when outcome is clear
- Buys when price < $0.99 and probability > 95%
- FOK orders for guaranteed fill or cancel

### Copy Trader (Planned)
- Mirrors successful Polymarket wallets
- Configurable delay and position sizing

### RAG-Powered Optimization (Planned)
- Machine learning on trade history
- Automatic strategy parameter tuning

---

## Deployment

### Local Development
```bash
python scanner.py --direct
python sniper.py --token-id <TOKEN>
```

### Production (VPS)
```bash
# PM2 for process management
pm2 start sniper.py --name "sniper" --interpreter ./venv/bin/python -- --token-id <TOKEN>

# Cron for auto-scheduling (Mon-Fri, market hours)
*/15 6-20 * * 1-5 /opt/polymarket-bot/auto_sniper.sh >> logs/auto.log 2>&1
```

See [docs/AUTO_SNIPER_SETUP.md](docs/AUTO_SNIPER_SETUP.md) for full deployment guide.

---

## Logging & Analytics

Trade logs are stored as JSON Lines for RAG/ML analysis:

```json
{"timestamp":"2026-02-03T14:15:32Z","event_type":"EXECUTION","token_id":"123...","side":"YES","price":0.98,"success":true}
```

Log location: `logs/trades/trades_YYYY-MM-DD.jsonl`

---

## Roadmap

- [x] Phase 1: Core sniper strategy
- [x] Phase 2: Production deployment (VPS + PM2 + Cron)
- [x] Phase 3: Enhanced logging for RAG
- [ ] Phase 4: RAG-powered strategy optimization
- [ ] Phase 5: Copy trader integration
- [ ] Phase 6: Multi-strategy orchestration
- [ ] Phase 7: Real-time dashboard

---

## Risk Disclosure

This software is for educational and research purposes. Trading prediction markets involves risk of loss. Past performance does not guarantee future results. Only trade with funds you can afford to lose.

---

## Team

Built by **PredictiveEdge** | January 2026

---

## License

Private repository. All rights reserved.
