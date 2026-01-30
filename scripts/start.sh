#!/bin/bash
# ===========================================
# Start all bots with PM2
# ===========================================

cd "$(dirname "$0")/.."

echo "Starting Polymarket bots..."

# Activate virtual environment
source venv/bin/activate

# Start PM2
pm2 start ecosystem.config.js

# Save process list
pm2 save

echo ""
echo "Bots started! Commands:"
echo "  pm2 monit     - Monitor processes"
echo "  pm2 logs      - View logs"
echo "  pm2 status    - Check status"
