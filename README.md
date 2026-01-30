# Polymarket Arbitrage Bot

Trading bot for Polymarket prediction markets implementing two strategies:

1. **Expiration Sniping** - Buy guaranteed winners in final seconds
2. **Copy Trading** - Mirror successful whale traders

## Quick Start

```bash
# 1. Setup
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt

# 2. Configure
cp .env.example .env
# Edit .env with your credentials

# 3. Approve USDC (one-time)
python approve.py

# 4a. Run Sniper
python scanner.py --asset BTC
python sniper.py --token-id <TOKEN_ID>

# 4b. Or Run Copy Trader
python copy_trader.py --target distinct-baguette
```

## Project Structure

```
polymarket-bot/
├── .env.example      # Config template
├── config.py         # Settings
├── approve.py        # USDC approval (run once)
├── scanner.py        # Find markets
├── sniper.py         # Expiration sniping bot
├── copy_trader.py    # Whale following bot
├── utils/            # Helpers
├── logs/             # Bot logs
└── data/             # Saved data
```

## Configuration

Edit `.env`:

```env
PRIVATE_KEY=0x...
WALLET_ADDRESS=0x...
CLOB_API_KEY=...
CLOB_SECRET=...
CLOB_PASSPHRASE=...
DRY_RUN=True  # Set False when ready
```

## Target Wallets

| Trader | Address | Monthly PnL |
|--------|---------|-------------|
| distinct-baguette | `0xe00740bce98a594e26861838885ab310ec3b548c` | $150K-$170K |

## Safety

- `DRY_RUN=True` by default
- 0.5-1% position sizes
- FOK orders (no partial fills)

---
Created for Chudi, Clyde & Phoenix | January 2026
