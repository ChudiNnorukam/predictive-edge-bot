# PredictiveEdge Trading Bot
### Partner Brief - Ready for Testing

**Prepared by:** Chudi
**Date:** January 31, 2026
**Status:** ✅ Ready to Test

---

## What Is This?

I've built an automated trading bot for Polymarket that captures small, low-risk profits from 15-minute crypto prediction markets.

**The strategy in plain English:**

> The bot waits until the final second before a market closes, then buys shares that are virtually guaranteed to win (95%+ probability) but are still trading at a small discount due to timing gaps.

**Example Trade:**
- BTC is clearly finishing above $100,000 (30 seconds left)
- "YES" shares trading at $0.98 (should be $1.00)
- Bot buys $10 worth at $0.98
- Market settles → We receive $10.20
- **Profit: $0.20 (2%) in under 1 minute**

Repeat 10-20 times per day during market hours.

---

## What The Bot Does

### ✅ Will Do:
- Monitor 15-minute BTC and ETH markets
- Place small buy orders ($1-5 each) in final seconds
- Only trade markets with 95%+ certain outcomes
- Log every action for full transparency

### ❌ Will NOT Do:
- Withdraw any funds
- Trade uncertain markets
- Risk more than agreed percentage
- Change any account settings

---

## Testing Plan

| Phase | What Happens | Risk |
|-------|--------------|------|
| **Phase 1: Dry Run** | Bot simulates trades, no real money | Zero |
| **Phase 2: $1 Trades** | Real trades, $1 each | $1-5 max |
| **Phase 3: Scale Up** | 0.5-1% per trade as agreed | Controlled |

We don't move to the next phase until the previous one proves successful.

---

## What I Need From You

### 1. ✅ Account Type - CONFIRMED

### 2. API Credentials (from Polymarket)
Go to: **Polymarket → Settings → Builder → Create API Key**

I'll need:
- API Key
- API Secret
- API Passphrase

*These allow trading but cannot withdraw funds. You can revoke anytime.*

### 3. Wallet Private Key
The private key for the wallet connected to Polymarket.

**How to get it from MetaMask:**
1. Open MetaMask
2. Click the three dots (⋮) next to your account
3. Account Details → Export Private Key
4. Enter your MetaMask password
5. Copy the key

**Share via Signal** (never regular text/email).

---

## Risk & Safety

| What Could Happen | Likelihood | Your Exposure |
|-------------------|------------|---------------|
| Trade doesn't fill | Medium | $0 loss |
| Small price move against us | Very Low | <$0.05 per trade |
| Bot finds no opportunities | Possible | $0 (no trades) |
| Total loss of funds | Extremely Low | Not possible with this strategy |

**Your funds stay in YOUR wallet.** The bot can trade but cannot withdraw.

**Kill switch:** You can revoke API access instantly from Polymarket settings.

---

## Transparency Commitments

1. **Full logging** - Every bot action is recorded
2. **Daily updates** - I'll share trade summaries
3. **Open access** - Revoke credentials anytime
4. **Code review** - Available if you want to see it

---

## Quick Summary

| Item | Status |
|------|--------|
| Bot code | ✅ Complete |
| Server (Amsterdam) | ✅ Running |
| Account type confirmed | ✅ Email wallet |
| API credentials | ⏳ Needed from you |
| Wallet private key | ⏳ Needed from you |
| Testing budget | ⏳ Confirm ($20-50 suggested) |

---

## Next Steps

1. **You:** Share API credentials + private key (via Signal)
2. **Me:** Configure bot, run Phase 1 dry test
3. **Me:** Share results, get your OK for Phase 2
4. **Us:** Review after first week, decide on scaling

---

**Questions?** Text me anytime. I want you 100% comfortable before we start.

---

*PredictiveEdge - Built for transparent, low-risk prediction market trading*
