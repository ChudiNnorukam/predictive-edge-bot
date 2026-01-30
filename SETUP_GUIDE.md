# Polymarket Bot - Free Setup Guide (No Money Required)

This guide walks you through setting up everything for FREE. You can run the bot in DRY_RUN mode to test without spending anything.

---

## Overview: What You're Setting Up

| Credential | Source | Cost |
|------------|--------|------|
| `PRIVATE_KEY` | MetaMask wallet | Free |
| `WALLET_ADDRESS` | MetaMask wallet | Free |
| `POLYMARKET_API_KEY` | Polymarket | Free |
| `POLYMARKET_API_SECRET` | Polymarket | Free |
| `POLYMARKET_PASSPHRASE` | Polymarket | Free |
| `POLYGON_RPC_URL` | Alchemy | Free |
| `TELEGRAM_BOT_TOKEN` | Telegram | Free (optional) |

---

## Step 1: Create a Crypto Wallet (MetaMask)

**Time: 5 minutes**

### 1.1 Install MetaMask

1. Go to [metamask.io](https://metamask.io/download/)
2. Click "Download" for your browser (Chrome recommended)
3. Add the extension to Chrome

### 1.2 Create Your Wallet

1. Click the MetaMask extension icon
2. Click **"Create a new wallet"**
3. Create a password (write it down!)
4. **CRITICAL**: Write down your 12-word Secret Recovery Phrase on paper
   - Never share this with anyone
   - Never store it digitally (no screenshots, no notes app)
   - If you lose it, you lose your wallet forever

### 1.3 Get Your Wallet Address

1. Click MetaMask extension
2. Your address is at the top (starts with `0x...`)
3. Click to copy it
4. Save this as `WALLET_ADDRESS` in a text file for later

### 1.4 Export Your Private Key

1. Click the three dots (⋮) in MetaMask
2. Go to **Account Details** → **Show Private Key**
3. Enter your password
4. Copy the private key (starts with `0x` or is 64 characters)
5. Save this as `PRIVATE_KEY`

⚠️ **SECURITY WARNING**: Never share your private key. Anyone with it can steal your funds.

---

## Step 2: Add Polygon Network to MetaMask

**Time: 2 minutes**

MetaMask defaults to Ethereum. Polymarket runs on Polygon, so add it:

### 2.1 Add Polygon Automatically

1. Go to [chainlist.org](https://chainlist.org/)
2. Search for "Polygon Mainnet"
3. Click **"Add to MetaMask"**
4. Approve the network addition

### 2.2 Or Add Manually

1. In MetaMask, click the network dropdown (top)
2. Click **"Add Network"** → **"Add a network manually"**
3. Enter these details:

```
Network Name: Polygon Mainnet
New RPC URL: https://polygon-rpc.com
Chain ID: 137
Currency Symbol: POL
Block Explorer: https://polygonscan.com
```

4. Click **Save**

---

## Step 3: Create Polymarket Account

**Time: 5 minutes**

### 3.1 Sign Up

1. Go to [polymarket.com](https://polymarket.com)
2. Click **"Sign Up"** or **"Log In"**
3. Choose your login method:
   - **Email** (easiest) - they'll create a wallet for you
   - **Browser Wallet** - connects your MetaMask

### 3.2 Using Your Own MetaMask Wallet (Recommended for Bot)

1. On Polymarket, click **"Connect Wallet"**
2. Select **MetaMask**
3. Approve the connection in MetaMask popup
4. Sign the message to verify ownership

---

## Step 4: Get Polymarket API Credentials

**Time: 5 minutes**

### Method A: Via Website (Easiest)

1. Go to [polymarket.com/settings?tab=builder](https://polymarket.com/settings?tab=builder)
2. Log in if prompted
3. You'll see your API credentials:
   - **API Key** → save as `POLYMARKET_API_KEY`
   - **Secret** → save as `POLYMARKET_API_SECRET`
   - **Passphrase** → save as `POLYMARKET_PASSPHRASE`

### Method B: Via Python Script (Alternative)

If Method A doesn't work, run this script:

```python
# generate_api_keys.py
import os
from py_clob_client.client import ClobClient

# Your private key from MetaMask
PRIVATE_KEY = "your_private_key_here"

client = ClobClient(
    host="https://clob.polymarket.com",
    chain_id=137,
    key=PRIVATE_KEY
)

# This will create or retrieve your API credentials
creds = client.create_or_derive_api_creds()

print("=== YOUR POLYMARKET API CREDENTIALS ===")
print(f"API Key: {creds.api_key}")
print(f"Secret: {creds.api_secret}")
print(f"Passphrase: {creds.api_passphrase}")
print("========================================")
print("\nSave these in your .env file!")
```

Run it:
```bash
pip install py-clob-client --break-system-packages
python generate_api_keys.py
```

---

## Step 5: Get Free Polygon RPC (Alchemy)

**Time: 5 minutes**

Public RPCs can be slow/unreliable. Alchemy's free tier gives you 30 million compute units/month.

### 5.1 Create Alchemy Account

1. Go to [alchemy.com](https://www.alchemy.com/)
2. Click **"Get started for free"**
3. Sign up with email or Google

### 5.2 Create a Polygon App

1. In the dashboard, click **"Create new app"**
2. Enter:
   - **Name**: `polymarket-bot`
   - **Chain**: Polygon PoS
   - **Network**: Mainnet
3. Click **"Create app"**

### 5.3 Get Your RPC URL

1. Click on your new app
2. Click **"API Key"** or **"View Key"**
3. Copy the **HTTPS** URL (looks like: `https://polygon-mainnet.g.alchemy.com/v2/YOUR_KEY`)
4. Save this as `POLYGON_RPC_URL`

### Alternative: Free Public RPCs (Less Reliable)

If you don't want to create an Alchemy account:

```
https://polygon-rpc.com
https://rpc-mainnet.matic.quiknode.pro
https://polygon.llamarpc.com
```

---

## Step 6: Set Up Notifications (Optional)

### Telegram Bot (Recommended)

1. Open Telegram, search for `@BotFather`
2. Send `/newbot`
3. Follow prompts to name your bot
4. Copy the **bot token** (looks like: `123456789:ABCdefGHI...`)
5. Save as `TELEGRAM_BOT_TOKEN`

**Get your Chat ID:**
1. Start a chat with your new bot
2. Send any message to it
3. Go to: `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
4. Find `"chat":{"id":XXXXXXXX}` - that's your Chat ID
5. Save as `TELEGRAM_CHAT_ID`

### Discord Webhook (Alternative)

1. In your Discord server, go to **Server Settings** → **Integrations**
2. Click **"Create Webhook"**
3. Copy the webhook URL
4. Save as `DISCORD_WEBHOOK_URL`

---

## Step 7: Configure Your .env File

Now put it all together:

```bash
cd /path/to/polymarket-bot
cp .env.example .env
```

Edit `.env` with your credentials:

```env
# Wallet (from MetaMask)
PRIVATE_KEY=your_64_character_private_key_here
WALLET_ADDRESS=0xYourWalletAddressHere

# Polymarket API (from Step 4)
POLYMARKET_API_KEY=your-api-key-uuid
POLYMARKET_API_SECRET=your-api-secret
POLYMARKET_PASSPHRASE=your-passphrase

# Polygon RPC (from Alchemy)
POLYGON_RPC_URL=https://polygon-mainnet.g.alchemy.com/v2/YOUR_KEY

# Trading Settings
DRY_RUN=True                    # KEEP TRUE UNTIL YOU HAVE FUNDS!
POSITION_SIZE_PERCENT=0.5       # 0.5% of portfolio per trade

# Notifications (optional)
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

---

## Step 8: Test Your Setup (No Money Needed!)

### 8.1 Install Dependencies

```bash
cd /path/to/polymarket-bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 8.2 Test the Scanner

```bash
python scanner.py --asset BTC
```

Expected output:
```
Found 3 active BTC markets expiring in next 60 minutes:
- Will BTC be above $95,000 at 3:00 PM? (expires in 12 min)
- Will BTC be above $94,500 at 3:15 PM? (expires in 27 min)
...
```

### 8.3 Test the Sniper (Dry Run)

```bash
python sniper.py
```

With `DRY_RUN=True`, it will:
- Connect to Polymarket WebSocket ✓
- Monitor prices in real-time ✓
- Log what trades it *would* make ✓
- NOT execute any actual trades ✓

---

## Troubleshooting

### "Invalid API Key" Error
- Regenerate credentials at polymarket.com/settings?tab=builder
- Make sure you're using the correct wallet

### "Connection refused" / RPC Errors
- Try a different RPC URL
- Check your Alchemy dashboard for rate limits

### MetaMask Won't Connect
- Make sure you're on Polygon network
- Clear browser cache and try again
- Try a different browser

---

## What's Next?

Once your setup is working in DRY_RUN mode, you'll need:

1. **USDC on Polygon** - Minimum ~$25-30 to start
2. **Tiny amount of POL** - ~$1 for gas fees

See the next section for minimum capital requirements.

---

## Quick Reference: All Your Credentials

Save these somewhere secure (password manager recommended):

```
WALLET_ADDRESS=0x...
PRIVATE_KEY=...
POLYMARKET_API_KEY=...
POLYMARKET_API_SECRET=...
POLYMARKET_PASSPHRASE=...
POLYGON_RPC_URL=https://polygon-mainnet.g.alchemy.com/v2/...
TELEGRAM_BOT_TOKEN=... (optional)
TELEGRAM_CHAT_ID=... (optional)
```

---

## Sources

- [Polymarket Authentication Docs](https://docs.polymarket.com/developers/CLOB/authentication)
- [Polymarket Builder Settings](https://polymarket.com/settings?tab=builder)
- [Alchemy Polygon RPC](https://www.alchemy.com/polygon)
- [Polygon RPC Endpoints](https://docs.polygon.technology/pos/reference/rpc-endpoints/)
- [MetaMask Download](https://metamask.io/download/)
