#!/bin/bash
# =============================================================================
# RAG Infrastructure Deployment Script for Oracle Cloud VPS
# =============================================================================
#
# This script deploys the RAG (Retrieval-Augmented Generation) architecture
# alongside the Polymarket trading bot on Oracle Cloud Always Free tier.
#
# Prerequisites:
# - Oracle Cloud VM.Standard.E2.1.Micro instance (Always Free)
# - SSH access configured (~/.ssh/oracle_polymarket)
# - Python 3.11+ on VPS
# - Git repository cloned to /home/polybot/polymarket-bot
#
# Usage:
#   ./scripts/deploy_rag_vps.sh [--install-chromadb]
#
# Options:
#   --install-chromadb   Install ChromaDB (optional, uses ~200MB RAM)
#                        If not installed, falls back to JSON storage
#
# =============================================================================

set -e

# Configuration
VPS_HOST="${VPS_HOST:-84.235.168.150}"
VPS_USER="${VPS_USER:-polybot}"
SSH_KEY="${SSH_KEY:-~/.ssh/oracle_polymarket}"
REMOTE_DIR="/home/polybot/polymarket-bot"
RAG_DATA_DIR="$REMOTE_DIR/data/rag"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Parse arguments
INSTALL_CHROMADB=false
while [[ $# -gt 0 ]]; do
    case $1 in
        --install-chromadb)
            INSTALL_CHROMADB=true
            shift
            ;;
        *)
            log_error "Unknown option: $1"
            exit 1
            ;;
    esac
done

# SSH wrapper
ssh_cmd() {
    ssh -i "$SSH_KEY" "$VPS_USER@$VPS_HOST" "$@"
}

scp_cmd() {
    scp -i "$SSH_KEY" "$@" "$VPS_USER@$VPS_HOST:$REMOTE_DIR/"
}

log_info "=== RAG Infrastructure Deployment ==="
log_info "VPS: $VPS_USER@$VPS_HOST"
log_info "Remote directory: $REMOTE_DIR"

# Step 1: Check VPS connectivity
log_info "Checking VPS connectivity..."
if ! ssh_cmd "echo 'Connected'" > /dev/null 2>&1; then
    log_error "Cannot connect to VPS. Check SSH key and VPS status."
    exit 1
fi
log_info "VPS connection successful"

# Step 2: Push latest code
log_info "Pushing latest code to GitHub..."
git add .
git commit -m "Deploy RAG infrastructure to VPS" 2>/dev/null || echo "No changes to commit"
git push origin main 2>/dev/null || echo "Already up to date"

# Step 3: Pull code on VPS
log_info "Pulling latest code on VPS..."
ssh_cmd "cd $REMOTE_DIR && git pull origin main"

# Step 4: Create RAG data directory
log_info "Creating RAG data directory..."
ssh_cmd "mkdir -p $RAG_DATA_DIR"

# Step 5: Install dependencies
log_info "Installing RAG dependencies..."
if [ "$INSTALL_CHROMADB" = true ]; then
    log_info "Installing ChromaDB (this may take a few minutes)..."
    ssh_cmd "cd $REMOTE_DIR && source venv/bin/activate && pip install chromadb"
else
    log_warn "Skipping ChromaDB installation (using JSON fallback)"
    log_warn "To install ChromaDB later, run: ./deploy_rag_vps.sh --install-chromadb"
fi

# Step 6: Update requirements.txt on VPS
log_info "Updating requirements..."
ssh_cmd "cd $REMOTE_DIR && source venv/bin/activate && pip install -r requirements.txt"

# Step 7: Verify RAG module loads
log_info "Verifying RAG module..."
ssh_cmd "cd $REMOTE_DIR && source venv/bin/activate && python -c 'from rag import KnowledgeStore, LearningCapture; print(\"RAG module loaded successfully\")'"

# Step 8: Run RAG self-test
log_info "Running RAG self-test..."
ssh_cmd "cd $REMOTE_DIR && source venv/bin/activate && python -c '
import asyncio
from rag import KnowledgeStore

async def test():
    ks = KnowledgeStore(persist_directory=\"data/rag_test\")
    await ks.initialize()

    # Add test learning
    learning_id = await ks.add_learning(
        learning_type=\"test\",
        content=\"This is a test learning from VPS deployment\",
        tags=[\"test\", \"deployment\"]
    )
    print(f\"Added test learning: {learning_id}\")

    # Search for it
    results = await ks.search_learnings(\"test deployment\")
    print(f\"Search found {len(results)} results\")

    # Get stats
    stats = await ks.get_stats()
    print(f\"Backend: {stats[\"backend\"]}, Total learnings: {stats[\"total_learnings\"]}\")

    await ks.close()
    print(\"RAG self-test passed!\")

asyncio.run(test())
'"

# Step 9: Create PM2 ecosystem update for RAG
log_info "Updating PM2 ecosystem config..."
ssh_cmd "cat > $REMOTE_DIR/ecosystem.config.js << 'EOF'
module.exports = {
  apps: [
    {
      name: 'polymarket-orchestrator',
      script: 'orchestrator.py',
      interpreter: './venv/bin/python',
      cwd: '/home/polybot/polymarket-bot',
      args: '--strategies sniper,copy_trader,exit_manager --dry-run',
      env: {
        PYTHONUNBUFFERED: '1',
        RAG_ENABLED: 'true',
        RAG_DATA_DIR: '/home/polybot/polymarket-bot/data/rag',
      },
      log_file: 'logs/orchestrator.log',
      error_file: 'logs/orchestrator-error.log',
      merge_logs: true,
      autorestart: true,
      max_restarts: 10,
      restart_delay: 5000,
    }
  ]
};
EOF"

# Step 10: Restart PM2 (if running)
log_info "Restarting PM2 processes..."
ssh_cmd "cd $REMOTE_DIR && pm2 restart ecosystem.config.js 2>/dev/null || pm2 start ecosystem.config.js"

# Step 11: Show status
log_info "=== Deployment Complete ==="
ssh_cmd "pm2 status"

log_info ""
log_info "RAG infrastructure deployed successfully!"
log_info ""
log_info "To check RAG status:"
log_info "  ssh -i $SSH_KEY $VPS_USER@$VPS_HOST 'cd $REMOTE_DIR && source venv/bin/activate && python -c \"from rag import KnowledgeStore; import asyncio; ks = KnowledgeStore(); asyncio.run(ks.initialize()); print(asyncio.run(ks.get_stats()))\"'"
log_info ""
log_info "To view logs:"
log_info "  ssh -i $SSH_KEY $VPS_USER@$VPS_HOST 'pm2 logs orchestrator'"
