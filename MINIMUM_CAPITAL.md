# Minimum Viable Capital for Polymarket Bot

Based on research from January 2025, here's what you actually need to start live trading.

---

## TL;DR: The Numbers

| Item | Minimum | Recommended | Notes |
|------|---------|-------------|-------|
| **Trading Capital (USDC)** | $10 | $50-100 | No platform minimum |
| **Gas Fees (POL)** | $1 | $2-5 | For ~100+ transactions |
| **Funding Method Fees** | $0-5 | Varies | Depends on method |
| **VPS (optional)** | $0 | $6/mo | Run locally first |
| **TOTAL TO START** | **~$15-20** | **$60-110** | |

---

## Breaking It Down

### 1. Trading Capital (USDC)

**Polymarket has NO minimum trade size.** You can literally trade $1.

However, with your 0.5-1% position sizing strategy:
- At $50 capital: Each trade = $0.25-0.50
- At $100 capital: Each trade = $0.50-1.00
- At $500 capital: Each trade = $2.50-5.00

**Recommendation**: Start with **$50-100 USDC** to have meaningful position sizes while limiting risk.

### 2. Gas Fees (POL)

Polygon transactions cost **$0.001-0.01 each** (extremely cheap).

- Approval transaction (one-time): ~$0.01
- Each trade: ~$0.005-0.02
- 100 trades ≈ $0.50-2.00

**Recommendation**: Keep **$2-5 worth of POL** in your wallet. This covers hundreds of trades.

### 3. Funding Method Costs

| Method | Minimum | Fees | Speed | Best For |
|--------|---------|------|-------|----------|
| **Coinbase → Polygon** | ~$10 | 0.5-1% + $0.60 | Minutes | US users with bank account |
| **Binance → Polygon** | ~$10 | 0.1% + ~$0.50 | Minutes | Non-US, lowest fees |
| **MoonPay (on Polymarket)** | $20-30 | 3.5-4.5% | Instant | Convenience, first-timers |
| **Debit Card via Exchange** | $10-20 | 2-4% | Instant | Speed over cost |
| **Bridge from other chain** | $10 | 0.2-0.5% | 1-5 min | Already have crypto |

### Cheapest Path: Bank → Exchange → Polygon

1. **Coinbase/Binance** - Buy USDC with bank transfer (0.5-1% fee)
2. **Withdraw to Polygon** - Select "Polygon" network (~$0.60 fee)
3. **Done** - USDC appears in your MetaMask on Polygon

**Example with $50:**
- Buy $50 USDC on Coinbase: $50 × 1% = $0.50 fee
- Withdraw to Polygon: $0.60 fee
- **Total received: ~$48.90 USDC**

---

## Realistic Starting Scenarios

### Scenario A: Absolute Minimum ($20)
```
$15 USDC (trading capital)
$2 POL (gas for ~200 trades)
$3 fees (exchange + withdrawal)
= $20 total cost
```
- Position size at 1%: $0.15 per trade
- Very small but functional for testing

### Scenario B: Sensible Start ($50-60)
```
$45 USDC (trading capital)
$3 POL (gas)
$5-10 fees
= $55-60 total cost
```
- Position size at 1%: $0.45 per trade
- Enough to see real results

### Scenario C: Proper Test ($100-120)
```
$90 USDC (trading capital)
$5 POL (gas)
$8-12 fees
= $105-120 total cost
```
- Position size at 1%: $0.90 per trade
- Meaningful trades, real data

---

## Profitability Math

### The Expiration Sniping Edge

When buying YES shares at $0.98 that settle at $1.00:
- **Profit per trade**: 2% ($0.02 per $1)
- **Dynamic fee at 98% price**: ~0.1% (nearly zero)
- **Net profit**: ~1.9% per successful trade

### Break-Even Analysis

With $50 capital and 1% position sizing ($0.50 trades):
- Profit per winning trade: $0.50 × 1.9% = **$0.0095**
- Need ~100 winning trades to make $0.95

**This strategy is about volume, not individual trade size.**

The real money comes from:
1. Running 24/7 (catching more opportunities)
2. Scaling up capital once proven
3. Low fees at extreme prices

---

## How to Get POL for Gas

You need a tiny amount of POL (Polygon's native token) for gas fees.

### Option 1: Buy on Exchange
- Buy $2-5 of POL/MATIC on Coinbase/Binance
- Withdraw to Polygon

### Option 2: Swap USDC for POL
- Once you have USDC on Polygon
- Use [QuickSwap](https://quickswap.exchange) or [Uniswap](https://app.uniswap.org)
- Swap $2-3 USDC for POL

### Option 3: Gas-Free First Transaction
- Some wallets (like Coinbase Wallet) offer gas-free USDC transfers
- MoonPay deposits may include a tiny POL amount

---

## Step-by-Step: Cheapest Funding Path

### Using Coinbase (Recommended for US)

1. **Create Coinbase account** (if you don't have one)
   - Verify identity (required)

2. **Add money via bank transfer**
   - Link bank account
   - ACH transfer (free, takes 3-5 days)
   - Or instant with debit card (2.5% fee)

3. **Buy USDC**
   - Go to USDC page
   - Buy $50-100 worth
   - Fee: ~0.5-1%

4. **Withdraw to Polygon**
   - Click Send/Receive → Send
   - Select USDC
   - **IMPORTANT: Choose "Polygon" network** (not Ethereum!)
   - Enter your MetaMask wallet address
   - Confirm withdrawal
   - Fee: ~$0.60

5. **Buy small amount of POL**
   - Buy $3-5 of POL
   - Withdraw to same wallet on Polygon
   - Fee: ~$0.50

### Using Binance (Lower Fees, Non-US)

Same process but:
- Trading fee: 0.1%
- Withdrawal fee: ~$0.50
- May have lower minimums

---

## When NOT to Fund Yet

Wait if:
- ❌ Bot hasn't worked in DRY_RUN mode
- ❌ You don't understand the strategy
- ❌ You can't afford to lose the money
- ❌ API credentials aren't set up correctly

---

## Team Pooling Strategy

Since you mentioned working with Clyde and Phoenix:

### Option 1: One Shared Wallet
- Pool funds into one wallet
- Run one bot
- Split profits

### Option 2: Parallel Testing
- Each person funds $20-30
- Run identical bots
- Compare results, then consolidate

### Option 3: Staged Funding
- One person tests with $25 first
- If working, others add funds
- Scale up together

---

## Summary Checklist

Before funding:
- [ ] Bot runs successfully in DRY_RUN mode
- [ ] All API credentials working
- [ ] Scanner finds markets correctly
- [ ] WebSocket connects to price feed
- [ ] You understand the risks

When ready to fund:
- [ ] Create exchange account (Coinbase/Binance)
- [ ] Buy $50-100 USDC
- [ ] Withdraw to Polygon (select correct network!)
- [ ] Buy $3-5 POL for gas
- [ ] Withdraw POL to same wallet
- [ ] Run approve.py (one-time USDC approval)
- [ ] Set DRY_RUN=False
- [ ] Start with smallest position size (0.5%)

---

## Sources

- [Polymarket - No Trading Limits](https://docs.polymarket.com/polymarket-learn/trading/no-limits)
- [Polymarket Fees Explained](https://www.polytrackhq.app/blog/polymarket-fees-explained)
- [How to Get USDC on Polygon](https://www.usdc.com/learn/how-to-get-usdc-on-polygon)
- [Coinbase Fees](https://help.coinbase.com/en/coinbase/trading-and-funding/pricing-and-fees/fees)
- [Polygon Gas Tracker](https://polygonscan.com/gastracker)
- [Across Protocol - Polygon Bridge](https://across.to/blog/polygon-bridge-guide-2025)
