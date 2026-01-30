#!/bin/bash
# ===========================================
# Stop all bots
# ===========================================

echo "Stopping Polymarket bots..."

pm2 stop all

echo "All bots stopped."
echo "To restart: pm2 start all"
