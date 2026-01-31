# Changelog

All notable changes to PredictiveEdge are documented here.

---

## [0.3.0] - 2026-01-31

### Added
- **Enhanced Trade Logging** - RAG-ready JSON logging system
  - `utils/trade_logger.py` - Structured event logging
  - Daily log rotation (`trades_YYYY-MM-DD.jsonl`)
  - Events: SCAN, OPPORTUNITY, EXECUTION, SETTLEMENT, SKIP, ERROR
- **Documentation Overhaul**
  - Professional README with architecture diagrams
  - `docs/INVESTOR_OVERVIEW.md` - Non-technical partner document
  - `docs/AUTO_SNIPER_SETUP.md` - VPS deployment guide
- **Git Repository**
  - Pushed to GitHub (private): `ChudiNnorukam/predictive-edge-bot`
  - Branch structure: `main` (production), `feature/arag-prototype` (R&D)

### Changed
- `sniper.py` - Integrated trade logger throughout execution flow

---

## [0.2.0] - 2026-01-30

### Added
- **Production Deployment**
  - DigitalOcean VPS (Amsterdam) configuration
  - PM2 process management
  - Cron-based auto-scheduling (Mon-Fri, market hours)
  - `auto_sniper.sh` - Automated market discovery and execution
- **Partner Documentation**
  - `docs/PARTNER_BRIEF.md` - Transparency document
  - `docs/TEXT_TO_PARTNERS.md` - Communication templates

### Changed
- `scanner.py` - Added direct slug lookup for reliable market discovery
- `sniper.py` - Configurable signature_type via environment variable
- `requirements.txt` - Pinned web3<7.0.0 for compatibility

### Fixed
- Negative risk market detection and handling
- WebSocket silent disconnect detection (60s timeout)
- Specific error handling for known failure patterns

---

## [0.1.0] - 2026-01-29

### Added
- **Core Trading Bot**
  - `sniper.py` - Expiration sniping strategy
  - `scanner.py` - Market discovery for 15-min crypto markets
  - `copy_trader.py` - Whale following strategy (prototype)
  - `config.py` - Configuration management
  - `approve.py` - USDC approval script

### Features
- WebSocket real-time price streaming
- FOK (Fill-or-Kill) order execution
- Dry run mode for safe testing
- Support for BTC, ETH, SOL, XRP markets

---

## Version History

| Version | Date | Milestone |
|---------|------|-----------|
| 0.3.0 | 2026-01-31 | Enhanced logging + documentation |
| 0.2.0 | 2026-01-30 | Production deployment |
| 0.1.0 | 2026-01-29 | Initial prototype |

---

## Upcoming

### [0.4.0] - Planned
- [ ] RAG integration for strategy optimization
- [ ] Copy trader improvements
- [ ] Real-time dashboard

### [0.5.0] - Planned
- [ ] Multi-strategy orchestration
- [ ] Compounding position sizing
- [ ] Performance analytics
