# VPS Deployment Guide

Complete guide to deploying your Polymarket bot on a VPS for 24/7 operation.

## Recommended VPS Providers

| Provider | Plan | Price | Location |
|----------|------|-------|----------|
| **DigitalOcean** | Basic Droplet | $6/mo | Amsterdam (AMS3) |
| **Vultr** | Cloud Compute | $6/mo | Amsterdam |
| **Hetzner** | CX11 | €4/mo | Germany |

**Why Amsterdam/Europe?** Lower latency to Polygon nodes (~10-50ms vs 100-200ms from US).

---

## Step 1: Create VPS

### DigitalOcean (Recommended)

1. Go to [digitalocean.com](https://digitalocean.com)
2. Create Droplet:
   - **Image**: Ubuntu 22.04 LTS
   - **Plan**: Basic, $6/mo (1GB RAM, 1 CPU)
   - **Region**: Amsterdam (AMS3)
   - **Authentication**: SSH Key (recommended) or Password

3. Note your droplet's IP address

---

## Step 2: Initial Server Setup

### Connect to your VPS

```bash
ssh root@YOUR_VPS_IP
```

### Create a non-root user

```bash
# Create user
adduser polymarket

# Add to sudo group
usermod -aG sudo polymarket

# Switch to new user
su - polymarket
```

### Set up SSH key (if using password auth)

```bash
# On your LOCAL machine, copy your SSH key
ssh-copy-id polymarket@YOUR_VPS_IP
```

---

## Step 3: Upload Bot Files

### Option A: SCP (Simple)

From your **local machine**:

```bash
# Upload entire bot folder
scp -r ~/Projects/business/polymarket-bot polymarket@YOUR_VPS_IP:~/
```

### Option B: Git (Better for updates)

On the **VPS**:

```bash
# Install git
sudo apt install git

# Clone your repo (if you pushed to GitHub)
git clone https://github.com/YOUR_USERNAME/polymarket-bot.git
cd polymarket-bot
```

---

## Step 4: Run Setup Script

```bash
cd ~/polymarket-bot

# Make scripts executable
chmod +x scripts/*.sh

# Run setup (installs Python, Node, PM2, dependencies)
bash scripts/setup.sh
```

This will install:
- Python 3.10+
- Node.js 20
- PM2 (process manager)
- All Python dependencies

---

## Step 5: Configure Environment

```bash
# Copy example config
cp .env.example .env

# Edit with your credentials
nano .env
```

**Required fields:**
```env
PRIVATE_KEY=0x...your_private_key...
WALLET_ADDRESS=0x...your_wallet...
CLOB_API_KEY=...
CLOB_SECRET=...
CLOB_PASSPHRASE=...
DRY_RUN=True  # Keep True for testing!
```

---

## Step 6: Test Locally First

```bash
# Activate virtual environment
source venv/bin/activate

# Approve USDC (one-time)
python approve.py

# Test scanner
python scanner.py --asset BTC

# Test sniper (dry run)
python sniper.py --token-id YOUR_TOKEN_ID
# Press Ctrl+C to stop after verifying it works
```

---

## Step 7: Configure PM2

Edit `ecosystem.config.js`:

```bash
nano ecosystem.config.js
```

Update these values:
1. **Token ID** for sniper (line ~18)
2. **Working directory** paths (line ~21) - should be `/home/polymarket/polymarket-bot`

---

## Step 8: Start with PM2

```bash
# Start all bots
pm2 start ecosystem.config.js

# Save process list (survives reboot)
pm2 save

# Configure auto-start on boot
pm2 startup
# Run the command it outputs (starts with 'sudo env...')
```

---

## Step 9: Monitor

### Real-time monitoring
```bash
pm2 monit
```

### View logs
```bash
# All logs
pm2 logs

# Specific bot
pm2 logs sniper
pm2 logs copy-trader

# Last 100 lines
pm2 logs --lines 100
```

### Check status
```bash
pm2 status
```

### Restart bots
```bash
pm2 restart all
pm2 restart sniper
```

---

## PM2 Commands Reference

| Command | Description |
|---------|-------------|
| `pm2 start ecosystem.config.js` | Start all bots |
| `pm2 stop all` | Stop all bots |
| `pm2 restart all` | Restart all bots |
| `pm2 status` | Show status |
| `pm2 logs` | View logs |
| `pm2 monit` | Real-time monitor |
| `pm2 save` | Save process list |
| `pm2 resurrect` | Restore saved processes |

---

## Updating the Bot

```bash
# If using git
cd ~/polymarket-bot
git pull

# Reinstall dependencies
source venv/bin/activate
pip install -r requirements.txt

# Restart bots
pm2 restart all
```

---

## Troubleshooting

### Bot keeps restarting

```bash
# Check error logs
pm2 logs sniper --err --lines 50

# Common issues:
# - Missing .env file
# - Invalid API credentials
# - Insufficient MATIC for gas
```

### WebSocket disconnections

```bash
# Check if Polymarket API is up
curl https://clob.polymarket.com/

# Restart the specific bot
pm2 restart sniper
```

### Out of memory

```bash
# Check memory usage
free -h

# If needed, add swap
sudo fallocate -l 1G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
```

### Check disk space

```bash
df -h
```

---

## Security Best Practices

1. **Never commit .env file** - It contains your private key!

2. **Use SSH keys** instead of passwords

3. **Set up firewall**:
   ```bash
   sudo ufw allow OpenSSH
   sudo ufw enable
   ```

4. **Keep system updated**:
   ```bash
   sudo apt update && sudo apt upgrade -y
   ```

5. **Monitor for intrusions**:
   ```bash
   sudo apt install fail2ban
   sudo systemctl enable fail2ban
   ```

---

## Cost Estimate

| Item | Monthly Cost |
|------|-------------|
| VPS (DigitalOcean Basic) | $6 |
| Domain (optional) | $1 |
| **Total** | **~$7/month** |

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│                     VPS (Ubuntu)                     │
├─────────────────────────────────────────────────────┤
│                                                      │
│   ┌─────────────┐  ┌─────────────┐  ┌───────────┐  │
│   │   sniper    │  │ copy-trader │  │  scanner  │  │
│   │    .py      │  │    .py      │  │   .py     │  │
│   └──────┬──────┘  └──────┬──────┘  └─────┬─────┘  │
│          │                │               │         │
│          └────────┬───────┴───────┬───────┘         │
│                   │               │                 │
│            ┌──────▼──────┐  ┌─────▼─────┐          │
│            │     PM2     │  │   Logs    │          │
│            │  (manager)  │  │           │          │
│            └──────┬──────┘  └───────────┘          │
│                   │                                 │
└───────────────────┼─────────────────────────────────┘
                    │
                    ▼
        ┌───────────────────────┐
        │   Polymarket APIs     │
        │  (CLOB, Gamma, Data)  │
        └───────────────────────┘
```

---

## Next Steps After Deployment

1. **Monitor for 24-48 hours** with DRY_RUN=True
2. **Check logs regularly** for any errors
3. **Start with $1 trades** when switching to live
4. **Gradually increase** position sizes
5. **Set up alerts** (Telegram/Discord) for notifications

Good luck! 🚀
