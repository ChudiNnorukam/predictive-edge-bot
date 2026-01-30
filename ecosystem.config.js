/**
 * PM2 Ecosystem Configuration
 *
 * This file configures PM2 to run your bots 24/7 with:
 * - Auto-restart on crash
 * - Memory limits
 * - Log rotation
 * - Cluster mode (if needed)
 *
 * Usage:
 *   pm2 start ecosystem.config.js
 *   pm2 start ecosystem.config.js --only sniper
 *   pm2 start ecosystem.config.js --only copy-trader
 */

module.exports = {
  apps: [
    // ===========================================
    // SNIPER BOT
    // ===========================================
    {
      name: 'sniper',
      script: 'sniper.py',
      interpreter: './venv/bin/python',

      // Arguments - UPDATE TOKEN_ID before running!
      args: '--token-id YOUR_TOKEN_ID_HERE',

      // Working directory
      cwd: '/home/polymarket/polymarket-bot',

      // Auto-restart settings
      autorestart: true,
      watch: false,
      max_restarts: 50,
      restart_delay: 5000,  // 5 seconds between restarts

      // Memory management
      max_memory_restart: '500M',

      // Logging
      log_file: './logs/sniper-combined.log',
      out_file: './logs/sniper-out.log',
      error_file: './logs/sniper-error.log',
      log_date_format: 'YYYY-MM-DD HH:mm:ss Z',
      merge_logs: true,

      // Environment variables
      env: {
        NODE_ENV: 'production',
        PYTHONUNBUFFERED: '1',  // Real-time logging
      },

      // Graceful shutdown
      kill_timeout: 5000,
      listen_timeout: 10000,
    },

    // ===========================================
    // COPY TRADER BOT
    // ===========================================
    {
      name: 'copy-trader',
      script: 'copy_trader.py',
      interpreter: './venv/bin/python',

      // Arguments
      args: '--target distinct-baguette --interval 4',

      // Working directory
      cwd: '/home/polymarket/polymarket-bot',

      // Auto-restart settings
      autorestart: true,
      watch: false,
      max_restarts: 50,
      restart_delay: 5000,

      // Memory management
      max_memory_restart: '500M',

      // Logging
      log_file: './logs/copy-trader-combined.log',
      out_file: './logs/copy-trader-out.log',
      error_file: './logs/copy-trader-error.log',
      log_date_format: 'YYYY-MM-DD HH:mm:ss Z',
      merge_logs: true,

      // Environment
      env: {
        NODE_ENV: 'production',
        PYTHONUNBUFFERED: '1',
      },

      kill_timeout: 5000,
      listen_timeout: 10000,
    },

    // ===========================================
    // MARKET SCANNER (runs every 15 minutes)
    // ===========================================
    {
      name: 'scanner',
      script: 'scanner.py',
      interpreter: './venv/bin/python',
      args: '--asset BTC --json',
      cwd: '/home/polymarket/polymarket-bot',

      // Cron mode - runs every 15 minutes
      cron_restart: '*/15 * * * *',
      autorestart: false,  // Don't auto-restart, cron handles it

      // Logging
      log_file: './logs/scanner.log',
      out_file: './logs/scanner-out.log',
      error_file: './logs/scanner-error.log',
      log_date_format: 'YYYY-MM-DD HH:mm:ss Z',

      env: {
        PYTHONUNBUFFERED: '1',
      },
    },

    // ===========================================
    // HEALTH CHECK MONITOR
    // ===========================================
    {
      name: 'health-check',
      script: 'health_check.py',
      interpreter: './venv/bin/python',
      cwd: '/home/polymarket/polymarket-bot',

      // Run every 5 minutes
      cron_restart: '*/5 * * * *',
      autorestart: false,

      log_file: './logs/health-check.log',

      env: {
        PYTHONUNBUFFERED: '1',
      },
    },
  ],

  // ===========================================
  // DEPLOYMENT CONFIGURATION
  // ===========================================
  deploy: {
    production: {
      // SSH user
      user: 'polymarket',

      // Target server (update with your VPS IP)
      host: ['YOUR_VPS_IP_HERE'],

      // SSH key path
      key: '~/.ssh/id_rsa',

      // Git repository (if using git deploy)
      ref: 'origin/main',
      repo: 'git@github.com:YOUR_USERNAME/polymarket-bot.git',

      // Remote path
      path: '/home/polymarket/polymarket-bot',

      // Pre-deploy commands
      'pre-deploy': 'git fetch --all',

      // Post-deploy commands
      'post-deploy': 'source venv/bin/activate && pip install -r requirements.txt && pm2 reload ecosystem.config.js --env production',

      // Environment
      env: {
        NODE_ENV: 'production',
      },
    },
  },
};
