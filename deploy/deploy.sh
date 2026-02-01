#!/bin/bash
# =============================================================================
# Quick Deploy Script - Pull latest and restart
# =============================================================================
# Run on VPS: ./deploy/deploy.sh
# =============================================================================

set -e

BOT_DIR="/home/ubuntu/polymarket-bot"
cd "$BOT_DIR"

echo "🔄 Pulling latest changes..."
git pull origin main

echo "📦 Updating dependencies..."
source venv/bin/activate
pip install -r requirements.txt --quiet

echo "🔁 Restarting service..."
sudo systemctl restart polymarket-bot

echo "✅ Deployment complete!"
echo ""
echo "Check status: sudo systemctl status polymarket-bot"
echo "View logs:    tail -f /var/log/polymarket-bot/bot.log"
