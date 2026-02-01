#!/bin/bash
# =============================================================================
# Polymarket Bot VPS Setup Script
# =============================================================================
# Run this on your VPS after cloning the repo:
#   curl -sSL https://raw.githubusercontent.com/ChudiNnorukam/predictive-edge-bot/main/deploy/setup-vps.sh | bash
# Or:
#   chmod +x deploy/setup-vps.sh && ./deploy/setup-vps.sh
# =============================================================================

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  Polymarket Bot VPS Setup${NC}"
echo -e "${GREEN}========================================${NC}"

# Configuration
BOT_DIR="/home/ubuntu/polymarket-bot"
VENV_DIR="$BOT_DIR/venv"
LOG_DIR="/var/log/polymarket-bot"

# Check if running as root for some operations
if [ "$EUID" -eq 0 ]; then
    echo -e "${YELLOW}Running as root - will set up system services${NC}"
    IS_ROOT=true
else
    echo -e "${YELLOW}Running as user - will need sudo for some operations${NC}"
    IS_ROOT=false
fi

# Step 1: Install system dependencies
echo -e "\n${GREEN}[1/6] Installing system dependencies...${NC}"
sudo apt-get update
sudo apt-get install -y python3 python3-pip python3-venv git

# Step 2: Clone or update repo
echo -e "\n${GREEN}[2/6] Setting up repository...${NC}"
if [ -d "$BOT_DIR" ]; then
    echo "Repository exists, pulling latest..."
    cd "$BOT_DIR"
    git pull origin main
else
    echo "Cloning repository..."
    git clone https://github.com/ChudiNnorukam/predictive-edge-bot.git "$BOT_DIR"
    cd "$BOT_DIR"
fi

# Step 3: Create virtual environment
echo -e "\n${GREEN}[3/6] Setting up Python environment...${NC}"
if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
fi
source "$VENV_DIR/bin/activate"
pip install --upgrade pip
pip install -r requirements.txt

# Step 4: Create directories
echo -e "\n${GREEN}[4/6] Creating directories...${NC}"
mkdir -p "$BOT_DIR/data"
mkdir -p "$BOT_DIR/logs"
sudo mkdir -p "$LOG_DIR"
sudo chown ubuntu:ubuntu "$LOG_DIR"

# Step 5: Setup .env file
echo -e "\n${GREEN}[5/6] Checking .env file...${NC}"
if [ ! -f "$BOT_DIR/.env" ]; then
    echo -e "${YELLOW}Creating .env template - YOU MUST EDIT THIS${NC}"
    cat > "$BOT_DIR/.env" << 'ENVEOF'
# =============================================================================
# Polymarket Bot Configuration
# =============================================================================
# REQUIRED: Fill in your wallet credentials
PRIVATE_KEY=your_private_key_here
WALLET_ADDRESS=your_wallet_address_here

# Optional: CLOB API credentials (will be auto-derived if not set)
CLOB_API_KEY=
CLOB_SECRET=
CLOB_PASSPHRASE=

# Network
POLYGON_CHAIN_ID=137
POLYGON_RPC_URL=https://polygon-rpc.com

# Safety - START WITH DRY_RUN=True
DRY_RUN=True

# Trading parameters
STARTING_BANKROLL=100.0
POSITION_SIZE_PCT=0.01
MAX_POSITION_PCT=0.05

# Spread Capture Strategy
SPREAD_MIN_SPREAD_PCT=2.0
SPREAD_EXIT_TARGET_PCT=2.0
SPREAD_MAX_POSITION_USD=50.0
SPREAD_MAX_CONCURRENT_POSITIONS=5
SPREAD_ENABLE_ARBITRAGE=True

# Optional: Notifications
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
DISCORD_WEBHOOK_URL=
ENVEOF
    echo -e "${RED}⚠️  IMPORTANT: Edit $BOT_DIR/.env with your credentials!${NC}"
else
    echo ".env file exists"
fi

# Step 6: Install systemd service
echo -e "\n${GREEN}[6/6] Installing systemd service...${NC}"
sudo cp "$BOT_DIR/deploy/polymarket-bot.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable polymarket-bot

echo -e "\n${GREEN}========================================${NC}"
echo -e "${GREEN}  Setup Complete!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo -e "Next steps:"
echo -e "  1. ${YELLOW}Edit your .env file:${NC}"
echo -e "     nano $BOT_DIR/.env"
echo ""
echo -e "  2. ${YELLOW}Test the bot (dry run):${NC}"
echo -e "     cd $BOT_DIR && source venv/bin/activate"
echo -e "     python sniper_v2.py --strategy spread-capture --max-markets 3"
echo ""
echo -e "  3. ${YELLOW}Start the service:${NC}"
echo -e "     sudo systemctl start polymarket-bot"
echo ""
echo -e "  4. ${YELLOW}Check status:${NC}"
echo -e "     sudo systemctl status polymarket-bot"
echo -e "     tail -f /var/log/polymarket-bot/bot.log"
echo ""
echo -e "  5. ${YELLOW}When ready for live trading:${NC}"
echo -e "     Edit .env and set DRY_RUN=False"
echo -e "     sudo systemctl restart polymarket-bot"
echo ""
