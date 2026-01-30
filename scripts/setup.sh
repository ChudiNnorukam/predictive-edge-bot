#!/bin/bash
# ===========================================
# Polymarket Bot - Initial Setup Script
# ===========================================
# Run this ONCE on a fresh VPS to set up everything
#
# Usage: bash scripts/setup.sh
# ===========================================

set -e  # Exit on error

echo "============================================"
echo "Polymarket Bot Setup"
echo "============================================"
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if running as root
if [ "$EUID" -eq 0 ]; then
    echo -e "${RED}Please don't run as root. Run as your user account.${NC}"
    exit 1
fi

# Get the directory where the script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BOT_DIR="$(dirname "$SCRIPT_DIR")"

echo "Bot directory: $BOT_DIR"
cd "$BOT_DIR"

# ===========================================
# 1. System Updates
# ===========================================
echo ""
echo -e "${YELLOW}[1/6] Updating system packages...${NC}"
sudo apt update && sudo apt upgrade -y

# ===========================================
# 2. Install Python 3.10+
# ===========================================
echo ""
echo -e "${YELLOW}[2/6] Installing Python...${NC}"

if ! command -v python3.10 &> /dev/null; then
    sudo apt install -y software-properties-common
    sudo add-apt-repository -y ppa:deadsnakes/ppa
    sudo apt update
    sudo apt install -y python3.10 python3.10-venv python3.10-dev python3-pip
fi

python3 --version

# ===========================================
# 3. Install Node.js & PM2
# ===========================================
echo ""
echo -e "${YELLOW}[3/6] Installing Node.js and PM2...${NC}"

if ! command -v node &> /dev/null; then
    curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
    sudo apt install -y nodejs
fi

if ! command -v pm2 &> /dev/null; then
    sudo npm install -g pm2
fi

node --version
pm2 --version

# ===========================================
# 4. Create Python Virtual Environment
# ===========================================
echo ""
echo -e "${YELLOW}[4/6] Creating Python virtual environment...${NC}"

if [ ! -d "venv" ]; then
    python3 -m venv venv
fi

source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# ===========================================
# 5. Create Required Directories
# ===========================================
echo ""
echo -e "${YELLOW}[5/6] Creating directories...${NC}"

mkdir -p logs data

# ===========================================
# 6. Setup PM2 Startup
# ===========================================
echo ""
echo -e "${YELLOW}[6/6] Configuring PM2 startup...${NC}"

# Save PM2 process list and configure startup
pm2 startup systemd -u $USER --hp $HOME

echo ""
echo "============================================"
echo -e "${GREEN}Setup Complete!${NC}"
echo "============================================"
echo ""
echo "Next steps:"
echo ""
echo "1. Copy your .env file:"
echo "   cp .env.example .env"
echo "   nano .env  # Edit with your credentials"
echo ""
echo "2. Run USDC approval:"
echo "   source venv/bin/activate"
echo "   python approve.py"
echo ""
echo "3. Test the sniper (dry run):"
echo "   python scanner.py --asset BTC"
echo "   python sniper.py --token-id <TOKEN_ID>"
echo ""
echo "4. Start with PM2 (24/7):"
echo "   pm2 start ecosystem.config.js"
echo "   pm2 save"
echo ""
echo "5. Monitor:"
echo "   pm2 monit"
echo "   pm2 logs"
echo ""
